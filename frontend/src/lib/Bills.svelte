<script>
  import { api, fmtMoney } from "./api.js";
  import BillForm from "./BillForm.svelte";

  let { reloadKey = 0 } = $props();

  const HORIZONS = [14, 30, 60, 90];

  let days = $state(30);
  let upcoming = $state(null);
  let debts = $state(null);
  let plan = $state(null);
  let bills = $state([]);
  let error = $state(null);
  // null = closed, "new" = adding, or the bill being edited.
  let editing = $state(null);
  // Bill id awaiting a second click to delete.
  let confirmDelete = $state(null);
  // Detected streams the user hid from the timeline.
  let hidden = $state([]);
  let showHidden = $state(false);
  // account_id being renamed, and the draft name.
  let renaming = $state(null);
  let renameDraft = $state("");

  async function load() {
    error = null;
    try {
      [upcoming, debts, bills, hidden, plan] = await Promise.all([
        api.upcoming(days),
        api.debts(),
        api.bills(),
        api.hiddenStreams(),
        api.plan(),
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

  function startRename(d) {
    renaming = d.ref_id;
    // Start from the current label unless it is just the institution's
    // generic name, which is the thing being replaced.
    renameDraft = d.name.includes("••") ? "" : d.name;
  }

  function finishRename(save) {
    // Enter or Escape closes the input, which then fires blur: already done.
    if (renaming === null) return;
    const id = renaming;
    renaming = null;
    if (save) act(() => api.renameAccount(id, renameDraft));
  }

  function focus(node) {
    node.focus();
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

  // Payments in due-date order, with each payday in the window slotted in
  // ahead of the payments due that day or later.
  let rows = $derived.by(() => {
    if (!upcoming) return [];
    const pays = (plan?.paychecks ?? []).filter((c) => c.date <= upcoming.end);
    const out = [];
    let i = 0;
    for (const p of upcoming.payments) {
      while (i < pays.length && pays[i].date < p.due_date) {
        out.push({ key: `pay-${pays[i].name}-${pays[i].date}`, payday: pays[i++] });
      }
      out.push({ key: `${p.source}-${p.ref_id}-${p.due_date}`, payment: p });
    }
    while (i < pays.length) out.push({ key: `pay-${pays[i].name}-${pays[i].date}`, payday: pays[i++] });
    return out;
  });

  let dueCount = $derived(
    upcoming?.payments.filter((p) => !p.paid && p.pay_from === "cash").length ?? 0
  );
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

{#if plan}
  <section class="panel plan">
    <h2>
      Until {plan.next_paycheck ? "next paycheck" : "two weeks out"}
      <span class="rel">
        · {fmtDate(plan.until)}{#if plan.next_paycheck}, {plan.next_paycheck.name} ~{fmtMoney(plan.next_paycheck.amount)}{/if}
      </span>
    </h2>
    <div class="equation">
      <div class="term">
        <span class="label">In checking</span>
        <span class="value">{fmtMoney(plan.cash_total)}</span>
        <span class="sub">{plan.cash_accounts.map((c) => c.name).join(" + ")}</span>
      </div>
      <span class="op">−</span>
      <div class="term">
        <span class="label">Due by {fmtDate(plan.until)}</span>
        <span class="value">{fmtMoney(plan.due_total)}</span>
        <span class="sub">
          {plan.due_count} {plan.due_count === 1 ? "payment" : "payments"}{#if plan.unknown_amounts}, {plan.unknown_amounts} amount unknown{/if}
        </span>
      </div>
      <span class="op">=</span>
      <div class="term">
        <span class="label">{Number(plan.left_over) < 0 ? "Short by" : "Left over"}</span>
        <span class="value" class:neg={Number(plan.left_over) < 0}>{fmtMoney(Math.abs(Number(plan.left_over)))}</span>
        <span class="sub">until the paycheck lands</span>
      </div>
    </div>
    <div class="safe" class:short={Number(plan.low_point) < 0}>
      {#if Number(plan.low_point) < 0}
        <span class="label">Heads up</span>
        <span class="value">Short {fmtMoney(-Number(plan.low_point))} on {fmtDate(plan.low_point_date)}</span>
        <span class="sub">
          Counting every bill and paycheck through {fmtDate(plan.horizon)}, checking dips below zero. Nothing spare to send to debt yet.
        </span>
      {:else}
        <span class="label">Safe to send to debt now</span>
        <span class="value">{fmtMoney(plan.safe_extra)}</span>
        <span class="sub">
          The lowest checking gets through {fmtDate(plan.horizon)}, on {fmtDate(plan.low_point_date)}, counting every bill and paycheck.
          {#if plan.target_debt}
            Best spent on <strong>{plan.target_debt.name}</strong>, {Number(plan.target_debt.apr).toFixed(2)}% APR on {fmtMoney(plan.target_debt.balance)}.
          {/if}
        </span>
      {/if}
    </div>
  </section>
{/if}

<section class="tiles">
  <div class="tile">
    <span class="label">Due next {days} days</span>
    <span class="value">{upcoming ? fmtMoney(upcoming.total_due) : "—"}</span>
    <span class="sub">{dueCount} {dueCount === 1 ? "payment" : "payments"}</span>
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
    <div class="scroll">
      <table class="upcoming">
        <thead>
          <tr>
            <th>Merchant</th>
            <th class="num">Amount due</th>
            <th>Due date</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r (r.key)}
            {#if r.payday}
              <tr class="payday">
                <td colspan="4">
                  <span>💵 {r.payday.name} · ~{fmtMoney(r.payday.amount)}</span>
                  <span class="rel">{fmtDate(r.payday.date)}</span>
                </td>
              </tr>
            {:else}
              {@const p = r.payment}
              <tr class:overdue={p.overdue} class:paid={p.paid}>
                <td class="merchant">
                  <span class="name">{p.name}</span>
                  <span class="tags">
                    {#if p.synced_from}<span class="tag synced" title="Read from the biller's site">portal</span>{/if}
                    {#if p.source === "card"}<span class="tag">card</span>{/if}
                    {#if p.source === "recurring"}<span class="tag" title="Detected by Plaid from past payments">detected</span>{/if}
                    {#if p.pay_from === "card"}<span class="tag" title="Charged to a credit card, so paid through that card's payment">on card</span>{/if}
                    {#if p.autopay}<span class="tag">autopay</span>{/if}
                    {#if p.statement_balance != null && Number(p.statement_balance) > 0}
                      <span class="hint">statement {fmtMoney(p.statement_balance)}</span>
                    {/if}
                  </span>
                </td>
                <td class="num amt">
                  {p.amount != null ? fmtMoney(p.amount) : "varies"}
                  {#if p.amount_basis}<span class="basis">{p.amount_basis === "minimum" ? "min" : "avg"}</span>{/if}
                </td>
                <td class="when">
                  <span>{fmtDate(p.due_date)}{#if p.estimated}<span class="basis" title="Projected, not stated by the biller"> est.</span>{/if}</span>
                  <span class="rel">{p.paid ? "paid" : relative(p.due_date)}</span>
                </td>
                <td class="act">
                  {#if p.source === "bill" && !p.autopay && !p.synced_from}
                    <button class="small" onclick={() => act(() => api.markBillPaid(p.ref_id))}>
                      Paid
                    </button>
                  {:else if p.source === "recurring"}
                    <button
                      class="small ghost"
                      title="Hide this detected payment from the timeline"
                      onclick={() => act(() => api.setStreamHidden(p.ref_id, true))}
                    >
                      Hide
                    </button>
                  {/if}
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
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
            {#if renaming === d.ref_id}
              <input
                class="rename"
                bind:value={renameDraft}
                placeholder="Nickname, blank to reset"
                use:focus
                onkeydown={(e) => {
                  if (e.key === "Enter") finishRename(true);
                  if (e.key === "Escape") finishRename(false);
                }}
                onblur={() => finishRename(true)}
              />
            {:else}
              <span class="name">
                {d.name}
                {#if d.source === "plaid"}
                  <button class="icon" title="Rename" onclick={() => startRename(d)}>✎</button>
                {/if}
                {#if d.is_overdue}<span class="tag danger">overdue</span>{/if}
              </span>
            {/if}
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
                {#if b.source}<span class="tag synced">portal</span>{/if}
                {#if b.notes}<div class="note">{b.notes}</div>{/if}
                {#if b.source_error}
                  <div class="note err">Last update failed: {b.source_error}</div>
                {:else if b.source_synced_at}
                  <div class="note">Updated {new Date(b.source_synced_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</div>
                {/if}
              </td>
              <td class="num">{b.amount != null ? fmtMoney(b.amount) : "varies"}</td>
              <td>{b.frequency}</td>
              <td>{fmtDate(b.next_due_date)}{#if b.due_date_estimated}<span class="basis"> est.</span>{/if}</td>
              <td class="num">{b.balance != null ? fmtMoney(b.balance) : ""}</td>
              <td class="rowact">
                <!-- A synced bill is rewritten on every sync, so editing it would not stick. -->
                {#if !b.source}
                  <button class="small" onclick={() => { confirmDelete = null; editing = b; }}>Edit</button>
                {/if}
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

{#if hidden.length}
  <section class="panel hidden-panel">
    <button class="link" onclick={() => (showHidden = !showHidden)}>
      {showHidden ? "▾" : "▸"} Hidden detected payments ({hidden.length})
    </button>
    {#if showHidden}
      <ul class="hidden-list">
        {#each hidden as h (h.stream_id)}
          <li>
            <span>{h.merchant_name || h.description}</span>
            <span class="muted">
              {h.average_amount != null ? fmtMoney(h.average_amount) : ""}
              {h.frequency ? h.frequency.toLowerCase().replace("_", "-") : ""}
            </span>
            <button class="small" onclick={() => act(() => api.setStreamHidden(h.stream_id, false))}>
              Unhide
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .plan {
    margin-bottom: 1rem;
  }
  .plan h2 {
    margin-bottom: 0.8rem;
  }
  .plan h2 .rel {
    font-weight: 400;
    font-size: 0.85rem;
  }
  .equation {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem 1rem;
  }
  .term {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    min-width: 0;
  }
  .value.neg {
    color: var(--danger);
  }
  .safe {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    margin-top: 0.9rem;
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    border-radius: 10px;
    padding: 0.7rem 0.9rem;
  }
  .safe .value {
    color: var(--accent);
  }
  .safe .sub {
    white-space: normal;
    color: var(--text);
  }
  .safe.short {
    background: transparent;
    border-color: var(--danger);
  }
  .safe.short .value {
    color: var(--danger);
  }
  .op {
    font-size: 1.4rem;
    color: var(--muted);
  }
  /* Stacked on a phone, the terms read as a list; the operators just float. */
  @media (max-width: 560px) {
    .op {
      display: none;
    }
    .equation {
      flex-direction: column;
      align-items: flex-start;
    }
  }
  .scroll {
    overflow-x: auto;
  }
  table.upcoming td {
    vertical-align: middle;
  }
  table.upcoming .when span {
    white-space: nowrap;
  }
  table.upcoming tr.paid {
    opacity: 0.5;
  }
  table.upcoming tr.overdue .when,
  table.upcoming tr.overdue .rel {
    color: var(--danger);
  }
  tr.payday td {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--accent);
    font-size: 0.85rem;
  }
  tr.payday td span + span {
    margin-left: 0.6rem;
  }
  .merchant .tags {
    margin-left: 0.4rem;
  }
  .tag.synced {
    color: var(--accent);
    border-color: var(--accent-dim);
  }
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
  .when {
    display: flex;
    flex-direction: column;
    font-size: 0.88rem;
  }
  .rel {
    color: var(--muted);
    font-size: 0.76rem;
  }
  .name {
    overflow-wrap: break-word;
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
  button.ghost {
    background: transparent;
    color: var(--muted);
  }
  button.ghost:hover {
    color: var(--text);
  }
  button.icon {
    background: none;
    border: none;
    padding: 0 0.25rem;
    color: var(--muted);
    font-size: 0.85rem;
  }
  button.icon:hover {
    color: var(--accent);
  }
  input.rename {
    flex: 1;
    min-width: 0;
    padding: 0.2rem 0.45rem;
  }
  .hidden-panel {
    margin-top: 1rem;
  }
  button.link {
    background: none;
    border: none;
    padding: 0;
    color: var(--muted);
    font-size: 0.9rem;
  }
  button.link:hover {
    color: var(--text);
  }
  .hidden-list li {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.45rem 0;
    border-bottom: 1px solid var(--panel-2);
  }
  .hidden-list li span:first-child {
    flex: 1;
    min-width: 0;
    overflow-wrap: break-word;
  }
  .muted {
    color: var(--muted);
    font-size: 0.82rem;
    white-space: nowrap;
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
