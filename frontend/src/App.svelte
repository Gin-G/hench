<script>
  import { api } from "./lib/api.js";
  import Link from "./lib/Link.svelte";
  import Sankey from "./lib/Sankey.svelte";
  import Transactions from "./lib/Transactions.svelte";

  let view = $state("sankey");
  let months = $state([]);
  let month = $state(new Date().toISOString().slice(0, 7));
  let items = $state([]);
  let syncing = $state(false);
  // Bumped to force Sankey and Transactions to re-fetch. They reload on a
  // month change alone, so after a sync or a fresh link — where month does not
  // change — they would otherwise keep showing whatever they fetched on mount,
  // making a successful sync look like it pulled nothing.
  let reloadKey = $state(0);

  async function loadMeta() {
    const [m, it] = await Promise.all([api.months(), api.items()]);
    months = m.length ? m : [month];
    if (!m.includes(month) && m.length) month = m[0];
    items = it;
  }

  // Refresh the metadata *and* the child views. Only for explicit actions that
  // change server-side data — the mount path calls loadMeta directly, so the
  // children are not made to fetch twice on first load.
  async function reload() {
    await loadMeta();
    reloadKey += 1;
  }

  async function sync() {
    syncing = true;
    try {
      await api.syncNow();
      await reload();
    } finally {
      syncing = false;
    }
  }

  function refresh() {
    reload();
  }

  $effect(() => {
    loadMeta();
  });
</script>

<header>
  <div class="brand">
    <span class="logo">⊟</span> hench
  </div>
  <div class="connections">
    {#each items as it}
      <span class="item" class:warn={it.status === "login_required"}>
        {it.institution_name || it.item_id.slice(0, 8)}
        {#if it.status === "login_required"}
          <Link itemId={it.item_id} label="Re-auth" onDone={refresh} />
        {/if}
      </span>
    {/each}
    <Link onDone={refresh} />
    <button onclick={sync} disabled={syncing}>{syncing ? "Syncing…" : "Sync now"}</button>
  </div>
</header>

<main>
  <div class="controls">
    <div class="tabs">
      <button class:active={view === "sankey"} onclick={() => (view = "sankey")}>
        Cash flow
      </button>
      <button
        class:active={view === "transactions"}
        onclick={() => (view = "transactions")}
      >
        Transactions
      </button>
    </div>
    <label>
      Month
      <select bind:value={month}>
        {#each months as m}<option value={m}>{m}</option>{/each}
      </select>
    </label>
  </div>

  {#if view === "sankey"}
    <Sankey {month} />
  {:else}
    <Transactions {month} />
  {/if}
</main>

<style>
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.9rem 1.5rem;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .brand {
    font-size: 1.2rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }
  .logo {
    color: var(--accent);
    margin-right: 0.3rem;
  }
  .connections {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
  .item {
    font-size: 0.85rem;
    color: var(--muted);
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  .item.warn {
    color: var(--danger);
  }
  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 1.5rem;
  }
  .controls {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
  }
  .tabs button {
    border-radius: 0;
    border-right: none;
  }
  .tabs button:first-child {
    border-radius: 7px 0 0 7px;
  }
  .tabs button:last-child {
    border-radius: 0 7px 7px 0;
    border-right: 1px solid var(--border);
  }
  .tabs button.active {
    background: var(--accent-dim);
    color: var(--accent);
    border-color: var(--accent);
  }
  label {
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
</style>
