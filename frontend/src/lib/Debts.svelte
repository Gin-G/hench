<script>
  // Debts ranked by the rate an extra dollar saves today, with the target
  // marked. Rates and promos on linked cards and loans are edited inline;
  // a hand-entered debt is edited through its bill.
  import { api, fmtMoney, fmtDate } from "./api.js";

  let { debts, onAct, onEditBill } = $props();

  // ref_id of the debt whose rates are open, and its draft.
  let editing = $state(null);
  let draft = $state({});
  let renaming = $state(null);
  let renameDraft = $state("");

  const pct = (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`);
  const orNull = (v) => (v === "" || v == null ? null : String(v));
  const isTarget = (d) =>
    debts?.target && debts.target.source === d.source && debts.target.ref_id === d.ref_id;

  function startEdit(d) {
    if (d.source === "manual") return onEditBill(Number(d.ref_id));
    editing = d.ref_id;
    draft = {
      apr_override: d.apr_overridden ? String(Number(d.base_apr)) : "",
      promo_apr: d.promo_apr != null ? String(Number(d.promo_apr)) : "",
      promo_ends_on: d.promo_ends_on ?? "",
      promo_balance: d.promo_balance ?? "",
      promo_deferred_interest: d.promo_deferred_interest,
    };
  }

  function saveEdit(e) {
    e.preventDefault();
    const id = editing;
    const body = {
      apr_override: orNull(draft.apr_override),
      promo_apr: orNull(draft.promo_apr),
      promo_ends_on: draft.promo_ends_on || null,
      promo_balance: orNull(draft.promo_balance),
      promo_deferred_interest: !!draft.promo_deferred_interest,
    };
    editing = null;
    onAct(() => api.updateAccount(id, body));
  }

  function clearPromo() {
    draft = { ...draft, promo_apr: "", promo_ends_on: "", promo_balance: "", promo_deferred_interest: false };
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
    if (save) onAct(() => api.renameAccount(id, renameDraft));
  }

  function focus(node) {
    node.focus();
  }

  function aprHint(a) {
    const type = String(a.apr_type ?? "").replace(/_apr$/, "").replace(/_/g, " ");
    const bal = a.balance_subject_to_apr != null ? ` on ${fmtMoney(a.balance_subject_to_apr)}` : "";
    return `${type} ${Number(a.apr_percentage).toFixed(2)}%${bal}`;
  }

  function utilization(d) {
    if (!d.credit_limit || Number(d.credit_limit) <= 0) return null;
    return Math.min(100, (Number(d.balance) / Number(d.credit_limit)) * 100);
  }
</script>

<section class="panel">
  <div class="head">
    <h2>Debt, highest rate first</h2>
    {#if debts}
      <span class="muted">{fmtMoney(debts.total_balance)} owed · {fmtMoney(debts.total_minimum)} in minimums</span>
    {/if}
  </div>
  {#if debts?.target}
    <p class="target">
      <span class="tag tgt">target</span>
      <strong>{debts.target.name}</strong> — {debts.target_reason}
    </p>
  {/if}
  {#if debts && !debts.debts.length}
    <p class="empty">No credit cards, loans or bills with a balance yet.</p>
  {/if}
  <div class="scroll">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Debt</th>
          <th class="num">Rate now</th>
          <th>Promo</th>
          <th class="num">Balance</th>
          <th class="num">Minimum</th>
          <th>Due</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each debts?.debts ?? [] as d, i (d.source + d.ref_id)}
          {@const util = utilization(d)}
          <tr class:target={isTarget(d)}>
            <td class="rank">{i + 1}</td>
            <td class="debt">
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
                  {#if isTarget(d)}<span class="tag tgt">target</span>{/if}
                  {#if d.is_overdue}<span class="tag danger">overdue</span>{/if}
                </span>
              {/if}
              <span class="sub">{d.institution ?? "manual"} · {d.kind}</span>
              {#if util != null}
                <div class="bar" title="{util.toFixed(0)}% of {fmtMoney(d.credit_limit)} limit">
                  <div class="fill" class:high={util > 30} style="width: {util}%"></div>
                </div>
              {/if}
            </td>
            <td class="num">
              <span class="rate">{pct(d.apr)}</span>
              {#if d.apr != null && d.base_apr != null && Number(d.apr) !== Number(d.base_apr)}
                <span class="sub">regular {pct(d.base_apr)}</span>
              {/if}
              {#if d.apr_overridden}<span class="sub" title="Your rate, not Plaid's">set by you</span>{/if}
            </td>
            <td class="promo">
              {#if d.promo_active}
                <span>
                  {pct(d.promo_apr)}{#if d.promo_balance != null}{" "}on {fmtMoney(d.promo_balance)}{/if}
                  until {fmtDate(d.promo_ends_on, { month: "short", day: "numeric", year: "numeric" })}
                </span>
                {#if d.promo_deferred_interest}<span class="tag warn" title="Interest waived so far is charged back if not cleared by the end date">deferred interest</span>{/if}
                <span class="sub" class:bad={d.promo_on_track === false}>
                  {fmtMoney(d.promo_monthly_needed)}/mo clears it{#if d.promo_on_track === false}; minimum won't{/if}
                </span>
                <span class="sub">then {pct(d.base_apr)}</span>
              {:else if d.promo_ends_on}
                <span class="sub">promo ended {fmtDate(d.promo_ends_on, { month: "short", day: "numeric", year: "numeric" })}</span>
              {/if}
            </td>
            <td class="num strong">{fmtMoney(d.balance)}</td>
            <td class="num">{d.minimum_payment != null ? fmtMoney(d.minimum_payment) : "—"}</td>
            <td class="due">{d.next_due_date ? fmtDate(d.next_due_date) : ""}</td>
            <td class="act">
              <button class="small" onclick={() => startEdit(d)}>{d.source === "manual" ? "Edit bill" : "Rates"}</button>
            </td>
          </tr>
          {#if editing === d.ref_id && d.source === "plaid"}
            <tr class="edit">
              <td colspan="8">
                <form onsubmit={saveEdit}>
                  <label>
                    Regular APR %
                    <input type="number" step="0.01" min="0" max="100" bind:value={draft.apr_override} placeholder={d.apr_overridden ? "" : `Plaid: ${pct(d.base_apr)}`} />
                  </label>
                  <label>
                    Promo APR %
                    <input type="number" step="0.01" min="0" max="100" bind:value={draft.promo_apr} placeholder="e.g. 0" />
                  </label>
                  <label>
                    Promo ends
                    <input type="date" bind:value={draft.promo_ends_on} required={draft.promo_apr !== ""} />
                  </label>
                  <label>
                    On balance of
                    <input type="number" step="0.01" min="0" bind:value={draft.promo_balance} placeholder="whole balance" />
                  </label>
                  <label class="check">
                    <input type="checkbox" bind:checked={draft.promo_deferred_interest} /> Deferred interest
                  </label>
                  <div class="actions">
                    <button type="button" class="small ghost" onclick={clearPromo}>Clear promo</button>
                    <button type="button" class="small" onclick={() => (editing = null)}>Cancel</button>
                    <button type="submit" class="small primary">Save</button>
                  </div>
                  {#if d.plaid_aprs?.length}
                    <p class="hint">Plaid reports: {d.plaid_aprs.map(aprHint).join(" · ")}</p>
                  {/if}
                </form>
              </td>
            </tr>
          {/if}
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
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.4rem 1rem;
    margin-bottom: 0.6rem;
  }
  h2 {
    font-size: 1rem;
    margin: 0;
  }
  .target {
    margin: 0 0 0.8rem;
    padding: 0.55rem 0.8rem;
    border: 1px solid var(--accent);
    background: var(--accent-dim);
    border-radius: 8px;
    font-size: 0.88rem;
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
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--panel-2);
    vertical-align: top;
  }
  tr.target td {
    background: color-mix(in srgb, var(--accent-dim) 45%, transparent);
  }
  .rank {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .debt {
    min-width: 12rem;
  }
  .name {
    overflow-wrap: break-word;
  }
  .sub {
    display: block;
    color: var(--muted);
    font-size: 0.76rem;
  }
  .sub.bad {
    color: var(--danger);
  }
  .rate {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .promo {
    font-size: 0.85rem;
    min-width: 11rem;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .strong {
    font-weight: 600;
  }
  .due {
    white-space: nowrap;
    font-size: 0.85rem;
  }
  .tag {
    white-space: nowrap;
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
    margin-left: 0.3rem;
  }
  .tag.tgt {
    color: var(--accent);
    border-color: var(--accent);
  }
  p.target .tag.tgt {
    margin: 0 0.3rem 0 0;
  }
  .tag.warn {
    color: var(--warn);
    border-color: var(--warn);
    margin-left: 0;
  }
  .tag.danger {
    color: var(--danger);
    border-color: var(--danger);
  }
  .bar {
    height: 4px;
    background: var(--panel-2);
    border-radius: 2px;
    margin-top: 0.35rem;
    overflow: hidden;
    max-width: 14rem;
  }
  .fill {
    height: 100%;
    background: var(--accent);
  }
  .fill.high {
    background: var(--warn);
  }
  .act {
    text-align: right;
    white-space: nowrap;
  }
  button.small {
    padding: 0.2rem 0.55rem;
    font-size: 0.8rem;
  }
  button.ghost {
    background: transparent;
    color: var(--muted);
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
    width: 100%;
    padding: 0.2rem 0.45rem;
  }
  tr.edit td {
    background: var(--panel-2);
  }
  form {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 0.6rem 0.9rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.78rem;
    color: var(--muted);
  }
  label input:not([type="checkbox"]) {
    width: 9.5rem;
    background: var(--panel);
  }
  label.check {
    flex-direction: row;
    align-items: center;
    gap: 0.4rem;
    color: var(--text);
    font-size: 0.85rem;
    padding-bottom: 0.4rem;
  }
  .actions {
    display: flex;
    gap: 0.4rem;
    margin-left: auto;
  }
  .hint {
    flex-basis: 100%;
    margin: 0;
    color: var(--muted);
    font-size: 0.78rem;
  }
  .muted {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .empty {
    color: var(--muted);
    font-size: 0.9rem;
  }
</style>
