<script>
  // The upcoming timeline as a ledger: every payment, paycheck and transfer
  // in, with the balance of each account it touches straight after it.
  import { api, fmtMoney, fmtDate, relative } from "./api.js";

  let { ledger, accounts = [], days = $bindable(30), horizons = [14, 30, 60, 90], onAct } = $props();

  let books = $derived(Object.fromEntries((ledger?.accounts ?? []).map((a) => [a.key, a])));

  // Where a payment can be drawn from, besides checking (the default): other
  // bank accounts, and for a bill, a credit card it is charged to.
  function payFromOptions(p) {
    return accounts.filter(
      (a) =>
        (a.type === "depository" && a.subtype !== "checking") ||
        (p.source === "bill" && a.type === "credit") ||
        a.account_id === p.pay_from_account_id
    );
  }

  function accountName(a) {
    const name = a.nickname || a.name || a.official_name || "Account";
    return a.nickname || !a.mask ? name : `${name} ••${a.mask}`;
  }

  function setPayFrom(p, value) {
    const id = value || null;
    onAct(() =>
      p.source === "bill"
        ? api.updateBill(p.ref_id, { pay_from_account_id: id })
        : api.setAccountPayFrom(p.ref_id, id)
    );
  }

  const isDebt = (key) => books[key]?.kind === "debt";
  const neg = (key, bal) => bal != null && !isDebt(key) && Number(bal) < 0;
</script>

<section class="panel">
  <div class="head">
    <h2>Upcoming</h2>
    <select bind:value={days} aria-label="Horizon">
      {#each horizons as h}<option value={h}>Next {h} days</option>{/each}
    </select>
  </div>

  {#if ledger}
    <div class="books">
      {#each ledger.accounts.filter((a) => a.kind === "cash") as a (a.key)}
        <div class="book" class:short={Number(a.low_point) < 0}>
          <span class="bname">{a.name}</span>
          <span>{fmtMoney(a.start_balance)} → <strong>{fmtMoney(a.end_balance)}</strong></span>
          <span class="muted">
            low {fmtMoney(a.low_point)}{#if a.low_point_date !== ledger.start}, {fmtDate(a.low_point_date)}{/if}
          </span>
        </div>
      {/each}
    </div>
  {/if}

  {#if ledger && !ledger.entries.length}
    <p class="empty">Nothing due in the next {days} days.</p>
  {/if}
  <div class="scroll">
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Item</th>
          <th class="num">Amount</th>
          <th>From · balance after</th>
          <th>Paid to · balance after</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each ledger?.entries ?? [] as e, i (`${e.kind}-${e.payment?.source ?? ""}-${e.payment?.ref_id ?? e.inflow?.stream_id ?? e.name}-${e.date}-${i}`)}
          {@const p = e.payment}
          <tr class:payday={e.kind === "paycheck"} class:inflow={e.kind !== "payment"} class:overdue={p?.overdue} class:paid={p?.paid}>
            <td class="when">
              <span>{fmtDate(e.date)}</span>
              <span class="rel">{p?.paid ? "paid" : relative(e.date)}</span>
            </td>
            <td class="item">
              <span class="name">{e.kind === "paycheck" ? "💵 " : e.kind === "transfer" ? "↘ " : ""}{e.name}</span>
              <span class="tags">
                {#if e.kind === "paycheck"}<span class="tag">paycheck</span>{/if}
                {#if e.kind === "transfer"}<span class="tag" title="Detected by Plaid from past transfers">transfer in</span>{/if}
                {#if p?.synced_from}<span class="tag synced" title="Read from the biller's site">portal</span>{/if}
                {#if p?.source === "card"}<span class="tag">card</span>{/if}
                {#if p?.source === "recurring"}<span class="tag" title="Detected by Plaid from past payments">detected</span>{/if}
                {#if p?.autopay}<span class="tag">autopay</span>{/if}
                {#if p && p.source !== "recurring" && accounts.length}
                  <select
                    class="payfrom"
                    class:set={p.pay_from_account_id}
                    title="Which account this is paid from"
                    value={p.pay_from_account_id ?? ""}
                    onchange={(ev) => setPayFrom(p, ev.currentTarget.value)}
                  >
                    <option value="">from checking</option>
                    {#each payFromOptions(p) as a (a.account_id)}
                      <option value={a.account_id}>{a.type === "credit" ? "on" : "from"} {accountName(a)}</option>
                    {/each}
                  </select>
                {/if}
                {#if p?.statement_balance != null && Number(p.statement_balance) > 0}
                  <span class="hint">statement {fmtMoney(p.statement_balance)}</span>
                {/if}
              </span>
            </td>
            <td class="num amt" class:in={e.kind !== "payment"}>
              {#if e.amount != null}{e.kind !== "payment" ? "+" : ""}{fmtMoney(e.amount)}{:else}varies{/if}
              {#if p?.amount_basis}<span class="basis">{p.amount_basis === "minimum" ? "min" : "avg"}</span>{/if}
              {#if e.kind !== "payment" || p?.estimated}<span class="basis" title="Projected, not stated">est.</span>{/if}
            </td>
            <td class="acct">
              {#if e.from_key}
                <span class="aname">{e.from_name}{#if isDebt(e.from_key)} <span class="basis">charged</span>{/if}</span>
                {#if e.from_balance != null}
                  <span class="bal" class:neg={neg(e.from_key, e.from_balance)}>
                    {isDebt(e.from_key) ? "owe " : ""}{fmtMoney(e.from_balance)}
                  </span>
                {/if}
              {/if}
            </td>
            <td class="acct">
              {#if e.to_key}
                <span class="aname">{e.to_name}</span>
                {#if e.to_balance != null}
                  <span class="bal">{isDebt(e.to_key) ? "owe " : ""}{fmtMoney(e.to_balance)}</span>
                {/if}
              {/if}
            </td>
            <td class="act">
              {#if p?.source === "bill" && !p.autopay && !p.synced_from}
                <button class="small" onclick={() => onAct(() => api.markBillPaid(p.ref_id))}>Paid</button>
              {:else if p?.source === "recurring" || e.kind === "transfer"}
                <button
                  class="small ghost"
                  title="Stop counting this detected {e.kind === 'transfer' ? 'transfer' : 'payment'}"
                  onclick={() => onAct(() => api.setStreamHidden(p?.ref_id ?? e.inflow.stream_id, true))}
                >
                  Hide
                </button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<style>
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
    min-width: 0;
    margin-bottom: 1rem;
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
  .books {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.8rem;
  }
  .book {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    padding: 0.45rem 0.7rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 0.82rem;
    font-variant-numeric: tabular-nums;
  }
  .book.short {
    border-color: var(--danger);
  }
  .book.short .muted {
    color: var(--danger);
  }
  .bname {
    font-weight: 600;
  }
  .scroll {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }
  th {
    text-align: left;
    color: var(--muted);
    font-weight: 600;
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td {
    padding: 0.45rem 0.6rem;
    border-bottom: 1px solid var(--panel-2);
    vertical-align: middle;
  }
  tr.paid {
    opacity: 0.5;
  }
  tr.overdue .when,
  tr.overdue .rel {
    color: var(--danger);
  }
  tr.payday td {
    background: var(--gold-dim);
  }
  tr.payday .name {
    color: var(--gold);
  }
  .amt.in {
    color: var(--accent);
  }
  .when > span,
  .acct > span {
    display: block;
    white-space: nowrap;
  }
  .rel,
  .aname {
    color: var(--muted);
    font-size: 0.76rem;
  }
  .bal {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .bal.neg {
    color: var(--danger);
  }
  .name {
    overflow-wrap: break-word;
  }
  .tags {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin-left: 0.4rem;
  }
  .tag {
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
  }
  .tag.synced {
    color: var(--accent);
    border-color: var(--accent-dim);
  }
  .hint {
    color: var(--muted);
    font-size: 0.78rem;
  }
  select.payfrom {
    font-size: 0.7rem;
    color: var(--muted);
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.2rem;
    max-width: 12rem;
  }
  select.payfrom.set {
    color: var(--text);
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
  .act {
    text-align: right;
  }
  button.small {
    padding: 0.2rem 0.55rem;
    font-size: 0.8rem;
  }
  button.ghost {
    background: transparent;
    color: var(--muted);
  }
  button.ghost:hover {
    color: var(--text);
  }
  .muted {
    color: var(--muted);
  }
  .empty {
    color: var(--muted);
    font-size: 0.9rem;
  }
</style>
