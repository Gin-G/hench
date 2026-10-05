<script>
  import { api, fmtMoney, fmtDate } from "./api.js";
  import BillForm from "./BillForm.svelte";
  import Debts from "./Debts.svelte";
  import Ledger from "./Ledger.svelte";

  let { reloadKey = 0, gmailResult = null } = $props();

  const GMAIL_MESSAGES = {
    connected: "Gmail connected. Bill emails are read on the next sync.",
    declined: "Gmail was not connected: access was declined.",
    failed: "Gmail could not be connected. Check that read-only Gmail access was allowed, then try again.",
  };

  const HORIZONS = [14, 30, 60, 90];

  let days = $state(30);
  let upcoming = $state(null);
  let ledger = $state(null);
  let debts = $state(null);
  let plan = $state(null);
  let gmail = $state(null);
  let confirmDisconnect = $state(false);
  let bills = $state([]);
  let accounts = $state([]);
  let error = $state(null);
  // null = closed, "new" = adding, or the bill being edited.
  let editing = $state(null);
  // Bill id awaiting a second click to delete.
  let confirmDelete = $state(null);
  // Detected streams the user hid from the timeline.
  let hidden = $state([]);
  let showHidden = $state(false);

  async function load() {
    error = null;
    try {
      [upcoming, ledger, debts, bills, hidden, plan, gmail, accounts] = await Promise.all([
        api.upcoming(days),
        api.ledger(days),
        api.debts(),
        api.bills(),
        api.hiddenStreams(),
        api.plan(),
        api.googleStatus(),
        api.accounts(),
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

  function disconnectGmail() {
    if (!confirmDisconnect) {
      confirmDisconnect = true;
      return;
    }
    confirmDisconnect = false;
    act(() => api.disconnectGoogle());
  }

  function remove(bill) {
    if (confirmDelete !== bill.id) {
      confirmDelete = bill.id;
      return;
    }
    confirmDelete = null;
    act(() => api.deleteBill(bill.id));
  }

  let dueCount = $derived(
    upcoming?.payments.filter((p) => !p.paid && p.pay_from === "cash").length ?? 0
  );
  let overdue = $derived(upcoming?.payments.filter((p) => p.overdue) ?? []);
  let nextUp = $derived(
    upcoming?.payments.find((p) => !p.overdue && !p.paid) ?? null
  );

  function editBill(id) {
    confirmDelete = null;
    editing = bills.find((b) => b.id === id) ?? null;
  }

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
          Counting every bill and paycheck through {fmtDate(plan.horizon)}, checking dips below zero.
          Move at least {fmtMoney(-Number(plan.low_point))} into checking before {fmtDate(plan.low_point_date)} to cover it. Nothing spare to send to debt yet.
        </span>
      {:else}
        <span class="label">Safe to send to debt now</span>
        <span class="value">{fmtMoney(plan.safe_extra)}</span>
        <span class="sub">
          The lowest checking gets through {fmtDate(plan.horizon)}, on {fmtDate(plan.low_point_date)}, counting every bill and paycheck.
          {#if Number(plan.safe_extra) < Number(plan.low_point)}
            Less {fmtMoney(Number(plan.low_point) - Number(plan.safe_extra))} to cover the shortfall below.
          {/if}
          {#if plan.target_debt}
            Best spent on <strong>{plan.target_debt.name}</strong> ({fmtMoney(plan.target_debt.balance)}): {plan.target_reason}
          {/if}
        </span>
      {/if}
    </div>
    {#each plan.funding_accounts as f (f.account_id)}
      {@const short = Number(f.low_point) < 0}
      <div class="funding" class:short>
        <span class="fname">{f.name}</span>
        <span>{fmtMoney(f.available)} now</span>
        <span>
          {fmtMoney(f.due_total)} due by {fmtDate(plan.horizon)}
          ({f.due_count} {f.due_count === 1 ? "payment" : "payments"})
        </span>
        {#if short}
          <strong>Short {fmtMoney(-Number(f.low_point))} on {fmtDate(f.low_point_date)}: move it over before then</strong>
        {:else}
          <span>lowest {fmtMoney(f.low_point)}, {fmtDate(f.low_point_date)}</span>
        {/if}
      </div>
    {/each}
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

<Ledger {ledger} {accounts} bind:days horizons={HORIZONS} onAct={act} />

<Debts {debts} onAct={act} onEditBill={editBill} />

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

  {#if gmailResult && GMAIL_MESSAGES[gmailResult]}
    <p class="notice" class:bad={gmailResult !== "connected"}>{GMAIL_MESSAGES[gmailResult]}</p>
  {/if}
  {#if gmail?.configured}
    <div class="gmail">
      {#if gmail.connected}
        <span>
          <span class="tag synced">Gmail</span>
          Reading bill emails from <strong>{gmail.account_email}</strong> (read-only)
        </span>
        <button class="small" class:danger={confirmDisconnect} onclick={disconnectGmail}>
          {confirmDisconnect ? "Confirm disconnect" : "Disconnect"}
        </button>
      {:else}
        <span>Connect Gmail (read-only) to read bill emails, such as Xcel's reminders.</span>
        <a class="button" href={api.googleConnectUrl}>Connect Gmail</a>
      {/if}
    </div>
  {/if}

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
              <td class="num">{b.amount != null ? fmtMoney(b.amount) : b.source && !b.active ? "—" : "varies"}</td>
              <td>{b.frequency}</td>
              <td>
                {#if b.source && !b.active}
                  <span class="muted">waiting for first read</span>
                {:else}
                  {fmtDate(b.next_due_date)}{#if b.due_date_estimated}<span class="basis"> est.</span>{/if}
                {/if}
              </td>
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
      {showHidden ? "▾" : "▸"} Hidden detected payments and transfers ({hidden.length})
    </button>
    {#if showHidden}
      <ul class="hidden-list">
        {#each hidden as h (h.stream_id)}
          <li>
            <span>{h.merchant_name || h.description}</span>
            <span class="muted">
              {h.average_amount != null ? (h.direction === "inflow" ? "+" : "") + fmtMoney(Math.abs(Number(h.average_amount))) : ""}
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
  .funding {
    display: flex;
    flex-wrap: wrap;
    gap: 0.2rem 1rem;
    margin-top: 0.6rem;
    padding: 0.5rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 10px;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .funding .fname {
    color: var(--text);
    font-weight: 600;
  }
  .funding.short {
    border-color: var(--danger);
  }
  .funding.short strong {
    color: var(--danger);
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
  .rel {
    color: var(--muted);
    font-size: 0.76rem;
  }
  .tag {
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
    margin-left: 0.3rem;
  }
  .hint {
    color: var(--muted);
    font-size: 0.78rem;
  }
  p.hint {
    margin: -0.2rem 0 0.8rem;
  }
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
  button.small {
    padding: 0.2rem 0.55rem;
    font-size: 0.8rem;
  }
  button.danger {
    color: var(--danger);
    border-color: var(--danger);
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
  .note.err {
    color: var(--danger);
  }
  .notice {
    font-size: 0.85rem;
    color: var(--accent);
  }
  .notice.bad {
    color: var(--danger);
  }
  .gmail {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem 1rem;
    padding: 0.6rem 0.8rem;
    margin-bottom: 0.8rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 0.88rem;
  }
  a.button {
    display: inline-block;
    padding: 0.35rem 0.8rem;
    border: 1px solid var(--accent);
    border-radius: 7px;
    background: var(--accent-dim);
    color: var(--accent);
    text-decoration: none;
    font-size: 0.85rem;
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
