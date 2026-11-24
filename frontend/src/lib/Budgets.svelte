<script>
  import { api, fmtMoney, fmtDate } from "./api.js";

  let { reloadKey = 0 } = $props();

  let data = $state(null);
  let error = $state("");
  let open = $state({});
  let editing = $state(null);
  let editAmount = $state("");
  // "group\u0000item"; item empty for the whole group.
  let pick = $state("");
  let amount = $state("");
  let adding = $state(false);
  let testState = $state("");

  async function load() {
    try {
      data = await api.budgets();
      error = "";
    } catch (e) {
      error = e.message;
    }
  }

  $effect(() => {
    reloadKey;
    load();
  });

  const taken = $derived(new Set((data?.budgets ?? []).map((b) => `${b.group}\u0000${b.item ?? ""}`)));

  function averageOf(key) {
    if (!data || !key) return null;
    const [g, i] = key.split("\u0000");
    const group = data.categories.find((c) => c.group === g);
    if (!group) return null;
    return i ? group.items.find((x) => x.item === i)?.average : group.average;
  }

  function choose(key) {
    pick = key;
    const avg = Number(averageOf(key) ?? 0);
    // A round figure a little under the average: the point is to spend less.
    amount = avg > 0 ? String(Math.max(10, Math.round((avg * 0.9) / 10) * 10)) : "";
  }

  async function add(e) {
    e.preventDefault();
    if (!pick || !(Number(amount) > 0)) return;
    const [group, item] = pick.split("\u0000");
    adding = true;
    try {
      await api.createBudget({ group, item: item || null, amount: Number(amount) });
      pick = "";
      amount = "";
      await load();
    } catch (err) {
      error = err.message;
    } finally {
      adding = false;
    }
  }

  function startEdit(b) {
    editing = b.id;
    editAmount = String(Number(b.amount));
  }

  async function saveEdit(b) {
    if (!(Number(editAmount) > 0)) return;
    await api.updateBudget(b.id, { amount: Number(editAmount) });
    editing = null;
    await load();
  }

  async function remove(b) {
    await api.deleteBudget(b.id);
    await load();
  }

  async function toggleAlerts() {
    data.alerts = await api.setBudgetAlerts(!data.alerts.enabled);
  }

  async function test() {
    testState = "sending";
    try {
      await api.testBudgetAlert();
      testState = "sent";
    } catch (e) {
      testState = e.message;
    }
  }

  function copy(text) {
    navigator.clipboard?.writeText(text);
  }

  function pct(b) {
    return Number(b.amount) > 0 ? (Number(b.spent) / Number(b.amount)) * 100 : 0;
  }

  const monthName = $derived(
    data ? new Date(`${data.month}-01T00:00:00`).toLocaleString("en-US", { month: "long" }) : ""
  );
  const totalLeft = $derived(data ? Number(data.total_amount) - Number(data.total_spent) : 0);
</script>

{#if error}<p class="error">{error}</p>{/if}

{#if data}
  <section class="tiles">
    <div class="tile">
      <span class="label">Budgeted for {monthName}</span>
      <span class="value">{fmtMoney(data.total_amount)}</span>
    </div>
    <div class="tile">
      <span class="label">Spent so far</span>
      <span class="value">{fmtMoney(data.total_spent)}</span>
    </div>
    <div class="tile">
      <span class="label">{totalLeft < 0 ? "Over" : "Left"}</span>
      <span class="value" class:neg={totalLeft < 0}>{fmtMoney(Math.abs(totalLeft))}</span>
    </div>
    <div class="tile">
      <span class="label">Day</span>
      <span class="value">{data.day} <span class="of">of {data.days_in_month}</span></span>
      <span class="sub">{data.days_in_month - data.day} days left</span>
    </div>
  </section>

  <section class="panel">
    <div class="head">
      <h2>{monthName}'s budgets</h2>
      <span class="muted">The line on each bar is where an even pace would be today.</span>
    </div>
    {#if !data.budgets.length}
      <p class="muted">No budgets yet. Add one below: the amount starts a little under what you've averaged.</p>
    {/if}
    {#each data.budgets as b (b.id)}
      {@const p = pct(b)}
      {@const over = Number(b.remaining) < 0}
      {@const ahead = Number(b.spent) > Number(b.pace)}
      <div class="budget">
        <div class="row">
          <button class="name ghost" onclick={() => (open[b.id] = !open[b.id])} aria-expanded={!!open[b.id]}>
            <span class="caret">{open[b.id] ? "▾" : "▸"}</span>
            {b.name}
            {#if b.item}<span class="muted small">{b.group}</span>{/if}
          </button>
          <span class="meta">
            <span class="figures">
              {fmtMoney(b.spent)} of
              {#if editing === b.id}
                <input
                  class="amt"
                  type="number"
                  min="1"
                  step="1"
                  bind:value={editAmount}
                  onkeydown={(e) => e.key === "Enter" && saveEdit(b)}
                />
                <button class="small primary" onclick={() => saveEdit(b)}>Save</button>
                <button class="small ghost" onclick={() => (editing = null)}>Cancel</button>
              {:else}
                <button class="link" title="Change the amount" onclick={() => startEdit(b)}>{fmtMoney(b.amount)}</button>
              {/if}
            </span>
            <span class="left" class:over class:ahead={!over && ahead}>
              {over ? `${fmtMoney(-b.remaining)} over` : `${fmtMoney(b.remaining)} left`}
            </span>
            <button class="small ghost" title="Delete this budget" onclick={() => remove(b)}>✕</button>
          </span>
        </div>
        <div class="bar" role="img" aria-label="{Math.round(p)}% spent">
          <div class="fill" class:high={p >= 80 && !over} class:over style="width: {Math.min(p, 100)}%"></div>
          <div class="pace" style="left: {Math.min((Number(b.pace) / Number(b.amount)) * 100, 100)}%"></div>
        </div>
        {#if open[b.id]}
          {#if b.charges.length}
            <table class="charges">
              <tbody>
                {#each b.charges as c (c.transaction_id)}
                  <tr>
                    <td class="muted">{fmtDate(c.date)}</td>
                    <td>{c.name}{#if c.pending} <span class="tag">pending</span>{/if}</td>
                    <td class="num">{fmtMoney(c.amount)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
            {#if b.charges.length === 8}<p class="muted small">The 8 largest this month.</p>{/if}
          {:else}
            <p class="muted small">Nothing spent here yet this month.</p>
          {/if}
        {/if}
      </div>
    {/each}

    <form class="add" onsubmit={add}>
      <select value={pick} onchange={(e) => choose(e.currentTarget.value)} aria-label="Category">
        <option value="">Add a budget for…</option>
        {#each data.categories as g}
          <optgroup label={g.group}>
            {#if !taken.has(`${g.group}\u0000`)}
              <option value={`${g.group}\u0000`}>All {g.group} (avg {fmtMoney(g.average)}/mo)</option>
            {/if}
            {#each g.items as it}
              {#if !taken.has(`${g.group}\u0000${it.item}`)}
                <option value={`${g.group}\u0000${it.item}`}>
                  {it.item}{Number(it.average) > 0 ? ` (avg ${fmtMoney(it.average)}/mo)` : ""}
                </option>
              {/if}
            {/each}
          </optgroup>
        {/each}
      </select>
      <label class="amount">
        $<input type="number" min="1" step="1" placeholder="Monthly" bind:value={amount} />
      </label>
      <button class="primary" disabled={adding || !pick || !(Number(amount) > 0)}>Add</button>
    </form>
    <p class="muted small">
      Averages are over the last three full months. A whole-group budget and one for an item in it
      both count that item's spending; the totals above count it once.
    </p>
  </section>

  <section class="panel">
    <div class="head">
      <h2>Phone alerts</h2>
      {#if data.alerts.available}
        <button class:primary={!data.alerts.enabled} onclick={toggleAlerts}>
          {data.alerts.enabled ? "Turn off" : "Turn on"}
        </button>
      {/if}
    </div>
    {#if !data.alerts.available}
      <p class="muted">Push alerts aren't set up on the server yet (ntfy, NTFY_URL).</p>
    {:else}
      <p>
        {#if data.alerts.enabled}
          After each sync you get a push for every new charge against a budget, with what's left of it.
        {:else}
          Alerts are off.
        {/if}
      </p>
      <ol class="steps">
        <li>Install the <strong>ntfy</strong> app (iOS or Android).</li>
        <li>
          Add a subscription, choose <em>Use another server</em>, and enter:
          <div class="kv">
            <span class="muted">Server</span>
            <code>{data.alerts.server}</code>
            <button class="small ghost" onclick={() => copy(data.alerts.server)}>Copy</button>
            <span class="muted">Topic</span>
            <code>{data.alerts.topic}</code>
            <button class="small ghost" onclick={() => copy(data.alerts.topic)}>Copy</button>
          </div>
        </li>
        <li>
          <button class="small" onclick={test} disabled={testState === "sending"}>Send a test</button>
          {#if testState === "sent"}<span class="ok">Sent. It should be on your phone now.</span>
          {:else if testState && testState !== "sending"}<span class="error">{testState}</span>{/if}
        </li>
      </ol>
      <p class="muted small">
        Keep the topic to yourself: anyone who has its name can read the alerts.
      </p>
    {/if}
  </section>
{/if}

<style>
  .error {
    color: var(--danger);
  }
  .ok {
    color: var(--accent);
    margin-left: 0.5rem;
  }
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
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
  }
  .label,
  .tile .sub {
    color: var(--muted);
    font-size: 0.82rem;
  }
  .value {
    font-size: 1.6rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .value.neg {
    color: var(--danger);
  }
  .of {
    font-size: 1rem;
    font-weight: 400;
    color: var(--muted);
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
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
  .muted {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .small {
    font-size: 0.78rem;
  }
  .budget {
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  .name {
    flex: 1 1 12rem;
    text-align: left;
    border: none;
    padding: 0;
    font-weight: 600;
    color: var(--text);
    display: flex;
    align-items: baseline;
    gap: 0.45rem;
    min-width: 0;
  }
  .caret {
    color: var(--muted);
    width: 0.8rem;
  }
  .figures {
    font-variant-numeric: tabular-nums;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-left: auto;
    white-space: nowrap;
  }
  .left {
    text-align: right;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .left.ahead {
    color: var(--warn);
  }
  .left.over {
    color: var(--danger);
  }
  .bar {
    position: relative;
    height: 8px;
    background: var(--panel-2);
    border-radius: 4px;
    margin-top: 0.45rem;
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent);
  }
  .fill.high {
    background: var(--warn);
  }
  .fill.over {
    background: var(--danger);
  }
  .pace {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--text);
    opacity: 0.6;
  }
  .charges {
    width: 100%;
    max-width: 34rem;
    margin: 0.5rem 0 0 1.2rem;
    border-collapse: collapse;
    font-size: 0.88rem;
  }
  .charges td {
    padding: 0.2rem 0.6rem 0.2rem 0;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .tag {
    font-size: 0.72rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
  }
  button.small {
    padding: 0.2rem 0.55rem;
    font-size: 0.8rem;
  }
  button.ghost {
    background: transparent;
  }
  button.link {
    border: none;
    background: none;
    padding: 0;
    color: var(--accent);
    text-decoration: underline dotted;
    font-variant-numeric: tabular-nums;
  }
  input.amt {
    width: 6rem;
  }
  .add {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    align-items: center;
    margin-top: 1rem;
  }
  .add select {
    flex: 1 1 18rem;
    min-width: 0;
  }
  .amount {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    color: var(--muted);
  }
  .amount input {
    width: 7rem;
  }
  .steps {
    padding-left: 1.2rem;
    line-height: 1.7;
  }
  .kv {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: 0.3rem 0.6rem;
    align-items: center;
    margin: 0.4rem 0;
    max-width: 40rem;
  }
  code {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 0.15rem 0.4rem;
    overflow-wrap: anywhere;
  }
</style>
