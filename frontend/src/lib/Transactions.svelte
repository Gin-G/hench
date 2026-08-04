<script>
  import { api, prettyCategory, fmtMoney } from "./api.js";

  let { month, reloadKey = 0 } = $props();

  let rows = $state([]);
  let total = $state(0);
  let categories = $state([]);
  let search = $state("");
  let editing = $state(null); // transaction_id currently being edited
  let error = $state(null);

  async function load() {
    error = null;
    try {
      const [list, cats] = await Promise.all([
        api.transactions({ month, search, limit: 200 }),
        categories.length ? Promise.resolve(categories) : api.categories(),
      ]);
      rows = list.transactions;
      total = list.total;
      if (!categories.length) categories = cats;
    } catch (e) {
      error = String(e);
    }
  }

  async function applyOverride(txn, value) {
    editing = null;
    try {
      if (value === "__clear__") {
        await api.clearCategory(txn.transaction_id);
      } else {
        await api.setCategory(txn.transaction_id, value);
      }
      await load();
    } catch (e) {
      error = String(e);
    }
  }

  let searchTimer;
  function onSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, 300);
  }

  $effect(() => {
    month; // re-run when month changes
    reloadKey; // ...and when the parent forces a reload after a sync or link
    load();
  });
</script>

<div class="tx">
  <div class="bar">
    <input
      type="search"
      placeholder="Search name or merchant…"
      bind:value={search}
      oninput={onSearch}
    />
    <span class="count">{total} transactions</span>
  </div>

  {#if error}<p style="color: var(--danger)">{error}</p>{/if}

  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Name</th>
        <th class="num">Amount</th>
        <th>Category</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as t (t.transaction_id)}
        <tr class:pending={t.pending}>
          <td class="date">{t.date}</td>
          <td>
            {t.merchant_name || t.name}
            {#if t.pending}<span class="tag">pending</span>{/if}
          </td>
          <td class="num" class:income={t.amount < 0}>
            {fmtMoney(-t.amount)}
          </td>
          <td class="cat">
            {#if editing === t.transaction_id}
              <select
                onchange={(e) => applyOverride(t, e.target.value)}
                value={t.category_primary || ""}
              >
                {#each categories as c}
                  <option value={c}>{prettyCategory(c)}</option>
                {/each}
                {#if t.is_overridden}
                  <option value="__clear__">↺ Reset to Plaid</option>
                {/if}
              </select>
            {:else}
              <button class="chip" onclick={() => (editing = t.transaction_id)}>
                {prettyCategory(t.category_primary)}
                {#if t.is_overridden}<span class="dot" title="overridden">•</span>{/if}
              </button>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .tx {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 0.75rem;
  }
  .bar input {
    flex: 1;
    max-width: 360px;
  }
  .count {
    color: var(--muted);
    font-size: 0.9rem;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
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
  td.date {
    color: var(--muted);
    white-space: nowrap;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .income {
    color: var(--accent);
  }
  tr.pending {
    opacity: 0.6;
  }
  .tag {
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
    margin-left: 0.4rem;
  }
  .chip {
    padding: 0.2rem 0.55rem;
    border-radius: 999px;
    font-size: 0.82rem;
  }
  .dot {
    color: var(--accent);
    margin-left: 0.2rem;
  }
</style>
