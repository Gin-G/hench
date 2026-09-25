<script>
  import { api, fmtMoney } from "./api.js";
  import BillForm from "./BillForm.svelte";

  let { reloadKey = 0 } = $props();

  const HORIZONS = [14, 30, 60, 90];

  let days = $state(30);
  let upcoming = $state(null);
  let debts = $state(null);
  let bills = $state([]);
  let error = $state(null);
  // null = closed, "new" = adding, or the bill being edited.
  let editing = $state(null);
  // Bill id awaiting a second click to delete.
  let confirmDelete = $state(null);

  async function load() {
    error = null;
    try {
      [upcoming, debts, bills] = await Promise.all([
        api.upcoming(days),
        api.debts(),
        api.bills(),
      ]);
    } catch (e) {
      error = String(e);
    }
  }

  async function act(fn) {
    error = null;
    try {
      await fn();
      await load();
    } catch (e) {
      error = String(e);
    }
  }

  async function save(body) {
    if (editing === "new") await api.createBill(body);
    else await api.updateBill(editing.id, body);
    editing = null;
    await load();
  }

  function remove(bill) {
    if (confirmDelete !== bill.id) {
      confirmDelete = bill.id;
      return;
    }
    confirmDelete = null;
    act(() => api.deleteBill(bill.id));
  }

  // "YYYY-MM-DD" parsed as a local date; new Date(str) would read it as UTC
  // midnight and show the previous day west of Greenwich.
  function parseDate(s) {
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  function fmtDate(s) {
    return parseDate(s).toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  }

  function relative(s) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diff = Math.round((parseDate(s) - today) / 86400000);
    if (diff === 0) return "today";
    if (diff === 1) return "tomorrow";
    if (diff < 0) return `${-diff}d late`;
    return `in ${diff}d`;
  }

  function utilization(d) {
    if (!d.credit_limit || Number(d.credit_limit) <= 0) return null;
    return Math.min(100, (Number(d.balance) / Number(d.credit_limit)) * 100);
  }

  const SOURCE_LABEL = { card: "card", recurring: "detected" };

  let overdue = $derived(upcoming?.payments.filter((p) => p.overdue) ?? []);
  let nextUp = $derived(
    upcoming?.payments.find((p) => !p.overdue && !p.paid) ?? null
  );

  $effect(() => {
    days; // re-fetch when the horizon changes
    reloadKey; // ...and after a sync or a bank link
    load();
  });
</script>

{#if error}<p class="err">{error}</p>{/if}

<section class="tiles">
  <div class="tile">
    <span class="label">Due next {days} days</span>
    <span class="value">{upcoming ? fmtMoney(upcoming.total_due) : "—"}</span>
    <span class="sub">{upcoming?.payments.filter((p) => !p.paid).length ?? 0} payments</span>
  </div>
  <div class="tile">
    <span class="label">Total debt</span>
    <span class="value">{debts ? fmtMoney(debts.total_balance) : "—"}</span>
    <span class="sub">{debts ? `${fmtMoney(debts.total_minimum)} in minimums` : ""}</span>
  </div>
  <div class="tile" class:alert={overdue.length}>
    {#if overdue.length}
      <span class="label">Overdue</span>
      <span class="value">{overdue.length}</span>
      <span class="sub">{overdue.map((p) => p.name).join(", ")}</span>
    {:else}
      <span class="label">Next up</span>
      <span class="value small">{nextUp ? nextUp.name : "Nothing due"}</span>
      <span class="sub">
        {nextUp ? `${fmtDate(nextUp.due_date)} · ${nextUp.amount != null ? fmtMoney(nextUp.amount) : "amount varies"}` : ""}
      </span>
    {/if}
  </div>
</section>

<div class="columns">
  <section class="panel">
    <div class="head">
      <h2>Upcoming</h2>
      <select bind:value={days} aria-label="Horizon">
        {#each HORIZONS as h}<option value={h}>Next {h} days</option>{/each}
      </select>
    </div>
    {#if upcoming && !upcoming.payments.length}
      <p class="empty">Nothing due in the next {days} days.</p>
    {/if}
    <ul class="timeline">
      {#each upcoming?.payments ?? [] as p (p.source + p.ref_id + p.due_date)}
        <li class:overdue={p.overdue} class:paid={p.paid}>
          <div class="when">
            <span>{fmtDate(p.due_date)}</span>
            <span class="rel">{p.paid ? "paid" : relative(p.due_date)}</span>
          </div>
          <div class="what">
            <span class="name">{p.name}</span>
            <span class="tags">
              {#if SOURCE_LABEL[p.source]}<span class="tag">{SOURCE_LABEL[p.source]}</span>{/if}
              {#if p.autopay}<span class="tag">autopay</span>{/if}
              {#if p.statement_balance != null && Number(p.statement_balance) > 0}
                <span class="hint">statement {fmtMoney(p.statement_balance)}</span>
              {/if}
            </span>
          </div>
          <div class="amt">
            {p.amount != null ? fmtMoney(p.amount) : "varies"}
            {#if p.amount_basis}<span class="basis">{p.amount_basis === "minimum" ? "min" : "avg"}</span>{/if}
          </div>
          <div class="act">
            {#if p.source === "bill" && !p.autopay}
              <button class="small" onclick={() => act(() => api.markBillPaid(p.ref_id))}>
                Paid
              </button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  </section>

  <section class="panel">
    <div class="head"><h2>Debt</h2></div>
    {#if debts && !debts.debts.length}
      <p class="empty">No credit cards, loans or bills with a balance yet.</p>
    {/if}
    <ul class="debts">
      {#each debts?.debts ?? [] as d (d.source + d.ref_id)}
        {@const util = utilization(d)}
        <li>
          <div class="row">
            <span class="name">
              {d.name}
              {#if d.is_overdue}<span class="tag danger">overdue</span>{/if}
            </span>
            <span class="bal">{fmtMoney(d.balance)}</span>
          </div>
          <div class="meta">
            <span>{d.institution ?? "manual"}</span>
            {#if d.apr != null}<span>{Number(d.apr).toFixed(2)}% APR</span>{/if}
            {#if d.minimum_payment != null}<span>{fmtMoney(d.minimum_payment)} min</span>{/if}
            {#if d.next_due_date}<span>due {fmtDate(d.next_due_date)}</span>{/if}
          </div>
          {#if util != null}
            <div class="bar" title="{util.toFixed(0)}% of {fmtMoney(d.credit_limit)} limit">
              <div class="fill" class:high={util > 30} style="width: {util}%"></div>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  </section>
</div>

<section class="panel">
  <div class="head">
    <h2>Your bills</h2>
    {#if editing === null}
      <button class="primary" onclick={() => (editing = "new")}>+ Add bill</button>
    {/if}
  </div>
  <p class="hint">
    Anything Plaid can't see: rent, utilities, loans at banks you haven't linked.
    Linked cards show up above on their own.
  </p>

  {#if editing === "new"}
    <BillForm onSave={save} onCancel={() => (editing = null)} />
  {/if}

  {#if bills.length}
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th class="num">Amount</th>
          <th>Repeats</th>
          <th>Next due</th>
          <th class="num">Balance</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each bills as b (b.id)}
          {#if editing?.id === b.id}
            <tr><td colspan="6"><BillForm bill={b} onSave={save} onCancel={() => (editing = null)} /></td></tr>
          {:else}
            <tr>
              <td>
                {b.name}
                {#if b.autopay}<span class="tag">autopay</span>{/if}
                {#if b.notes}<div class="note">{b.notes}</div>{/if}
              </td>
              <td class="num">{b.amount != null ? fmtMoney(b.amount) : "varies"}</td>
              <td>{b.frequency}</td>
              <td>{fmtDate(b.next_due_date)}</td>
              <td class="num">{b.balance != null ? fmtMoney(b.balance) : ""}</td>
              <td class="rowact">
                <button class="small" onclick={() => { confirmDelete = null; editing = b; }}>Edit</button>
                <button class="small" class:danger={confirmDelete === b.id} onclick={() => remove(b)}>
                  {confirmDelete === b.id ? "Confirm" : "Delete"}
                </button>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {:else if editing !== "new"}
    <p class="empty">No bills added yet.</p>
  {/if}
</section>

<style>
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
    margin-bottom: 1rem;
  }
  .tile {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.9rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    min-width: 0;
  }
  .tile.alert {
    border-color: var(--danger);
  }
  .tile.alert .value {
    color: var(--danger);
  }
  .label {
    color: var(--muted);
    font-size: 0.82rem;
  }
  .value {
    font-size: 1.6rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .value.small {
    font-size: 1.15rem;
    padding-block: 0.3rem;
  }
  .sub {
    color: var(--muted);
    font-size: 0.82rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .columns {
    display: grid;
    grid-template-columns: 3fr 2fr;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  @media (max-width: 820px) {
    .columns {
      grid-template-columns: 1fr;
    }
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
    min-width: 0;
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.6rem;
  }
  h2 {
    font-size: 1rem;
    margin: 0;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .timeline li {
    display: grid;
    grid-template-columns: 6.5rem minmax(0, 1fr) auto 3.8rem;
    grid-template-areas: "when what amt act";
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--panel-2);
  }
  .timeline li.paid {
    opacity: 0.5;
  }
  .timeline li.overdue .when,
  .timeline li.overdue .rel {
    color: var(--danger);
  }
  .when {
    grid-area: when;
    display: flex;
    flex-direction: column;
    font-size: 0.88rem;
  }
  .rel {
    color: var(--muted);
    font-size: 0.76rem;
  }
  .what {
    grid-area: what;
    min-width: 0;
  }
  .name {
    overflow-wrap: break-word;
  }
  .amt {
    grid-area: amt;
  }
  .act {
    grid-area: act;
  }
  /* Phone width: the name gets the whole row, amount and action drop below. */
  @media (max-width: 560px) {
    .timeline li {
      grid-template-columns: 5.5rem minmax(0, 1fr) auto;
      grid-template-areas:
        "when what what"
        "when amt act";
      row-gap: 0.25rem;
    }
    .amt {
      text-align: left;
    }
  }
  .tags {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin-left: 0.3rem;
  }
  .tag {
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
    margin-left: 0.3rem;
  }
  .tags .tag {
    margin-left: 0;
  }
  .tag.danger {
    color: var(--danger);
    border-color: var(--danger);
  }
  .hint {
    color: var(--muted);
    font-size: 0.78rem;
  }
  p.hint {
    margin: -0.2rem 0 0.8rem;
  }
  .amt,
  .bal,
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .basis {
    color: var(--muted);
    font-size: 0.72rem;
    margin-left: 0.2rem;
  }
  .act {
    text-align: right;
  }
  button.small {
    padding: 0.2rem 0.55rem;
    font-size: 0.8rem;
  }
  button.danger {
    color: var(--danger);
    border-color: var(--danger);
  }
  .debts li {
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--panel-2);
  }
  .row {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .bal {
    font-weight: 600;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.8rem;
    color: var(--muted);
    font-size: 0.78rem;
    margin-top: 0.2rem;
  }
  .bar {
    height: 4px;
    background: var(--panel-2);
    border-radius: 2px;
    margin-top: 0.45rem;
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent);
  }
  .fill.high {
    background: #fbbf24;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
  }
  .panel:has(table) {
    overflow-x: auto;
  }
  th {
    text-align: left;
    color: var(--muted);
    font-weight: 600;
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 0.45rem 0.6rem;
    border-bottom: 1px solid var(--panel-2);
  }
  .note {
    color: var(--muted);
    font-size: 0.78rem;
  }
  .rowact {
    text-align: right;
    white-space: nowrap;
  }
  .empty {
    color: var(--muted);
    font-size: 0.9rem;
  }
  .err {
    color: var(--danger);
  }
</style>
