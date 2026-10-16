<script>
  import * as echarts from "echarts";
  import { api, fmtMoney, fmtDate } from "./api.js";

  let { reloadKey = 0 } = $props();

  let data = $state(null);
  let error = $state(null);
  let el = $state();
  let chart;

  // Names as services/networth.py classes them.
  const CLASSES = [
    { key: "debt", label: "Can get rid of", note: "debt: ends once it's paid off" },
    { key: "essential", label: "Can't get rid of", note: "essentials that stay" },
    { key: "optional", label: "Everything else", note: "could go if needed" },
  ];
  const CATEGORIES = {
    asset: [
      ["home", "Home"],
      ["vehicle", "Vehicle"],
      ["retirement", "Retirement"],
      ["investment", "Investments"],
      ["cash", "Cash"],
      ["other", "Other"],
    ],
    debt: [
      ["mortgage", "Mortgage"],
      ["auto_loan", "Car loan"],
      ["student_loan", "Student loan"],
      ["personal_loan", "Personal loan"],
      ["other", "Other"],
    ],
  };
  const LINKED_CATEGORY = {
    checking: "Checking",
    savings: "Savings",
    "credit card": "Credit card",
  };

  function categoryLabel(line) {
    const pair = (CATEGORIES[line.kind] || []).find(([k]) => k === line.category);
    if (pair) return pair[1];
    return LINKED_CATEGORY[line.category] || line.category;
  }

  async function load() {
    error = null;
    try {
      data = await api.networth();
    } catch (e) {
      error = String(e);
    }
  }

  $effect(() => {
    reloadKey; // bumped by the parent after a sync or a bank link
    load();
  });

  // --- Summary -------------------------------------------------------------
  const assets = $derived(data ? data.lines.filter((l) => l.kind === "asset") : []);
  const owed = $derived(data ? data.lines.filter((l) => l.kind === "debt") : []);
  const manualAssets = $derived(assets.filter((l) => l.source === "manual"));
  const lastMonth = $derived(data && data.history.length > 1 ? data.history.at(-2) : null);
  const change = $derived(lastMonth ? Number(data.net_worth) - Number(lastMonth.net_worth) : null);
  const byClass = $derived(
    Object.fromEntries(
      CLASSES.map((c) => [c.key, data ? data.commitments.filter((p) => p.commitment === c.key) : []])
    )
  );
  const classTotal = (key) => byClass[key].reduce((s, p) => s + Number(p.monthly), 0);

  // Full months only: the month in progress would drag every average down.
  const full = $derived(data ? data.months.filter((m) => !m.partial) : []);
  const avg = $derived.by(() => {
    if (!full.length) return null;
    const mean = (k) => full.reduce((s, m) => s + Number(m[k]), 0) / full.length;
    return {
      income: mean("income"),
      essential: mean("essential"),
      debt: mean("debt"),
      other: mean("other"),
      card: mean("card_change"),
    };
  });

  // Where a month's gap came from, or where its surplus went.
  function verdict(m) {
    const net = Number(m.net);
    const cash = Number(m.cash_change);
    const card = Number(m.card_change);
    const parts = [];
    if (cash < -0.5) parts.push(`${fmtMoney(-cash)} out of the bank`);
    if (cash > 0.5) parts.push(`bank up ${fmtMoney(cash)}`);
    if (card > 0.5) parts.push(`${fmtMoney(card)} more onto cards`);
    if (card < -0.5) parts.push(`cards paid down ${fmtMoney(-card)}`);
    const head = net < 0 ? `Short ${fmtMoney(-net)}` : `Up ${fmtMoney(net)}`;
    return parts.length ? `${head}: ${parts.join(", ")}` : head;
  }

  // --- Chart ---------------------------------------------------------------
  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function render() {
    if (!chart || !data) return;
    const months = data.history.map((h) => h.month);
    const series = [
      {
        name: "Net worth",
        type: "line",
        data: data.history.map((h) => Number(h.net_worth)),
        symbolSize: 7,
        lineStyle: { width: 3, color: token("--gold") },
        itemStyle: { color: token("--gold") },
      },
    ];
    // Only worth a second line once there is something besides the linked
    // accounts.
    if (data.lines.some((l) => l.source === "manual")) {
      series.push({
        name: "Bank less cards",
        type: "line",
        data: data.history.map((h) => Number(h.liquid)),
        symbolSize: 5,
        lineStyle: { width: 2, type: "dashed", color: token("--accent") },
        itemStyle: { color: token("--accent") },
      });
    }
    chart.setOption(
      {
        backgroundColor: "transparent",
        grid: { left: 80, right: 20, top: 30, bottom: 30 },
        legend: { top: 0, textStyle: { color: token("--muted") } },
        tooltip: {
          trigger: "axis",
          backgroundColor: token("--panel-2"),
          borderColor: token("--border"),
          textStyle: { color: token("--text") },
          valueFormatter: (v) => fmtMoney(v),
        },
        xAxis: {
          type: "category",
          data: months,
          axisLine: { lineStyle: { color: token("--border") } },
          axisLabel: { color: token("--muted") },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: token("--muted"), formatter: (v) => fmtMoney(v).replace(".00", "") },
          splitLine: { lineStyle: { color: token("--panel-2") } },
        },
        series,
      },
      true
    );
  }

  $effect(() => {
    if (el && !chart) {
      chart = echarts.init(el, null, { renderer: "canvas" });
      new ResizeObserver(() => chart.resize()).observe(el);
    }
    data;
    render();
  });

  // --- Regular payments ----------------------------------------------------
  async function setClass(p, value) {
    try {
      await api.updateStream(p.ref_id, { commitment: value || null });
      await load();
    } catch (e) {
      error = String(e);
    }
  }

  // --- Holdings ------------------------------------------------------------
  const today = () => new Date().toLocaleDateString("en-CA");
  let form = $state(null); // null, or the holding being added or edited
  let deleting = $state(null);

  function startAdd(kind) {
    form = {
      id: null,
      name: "",
      kind,
      category: CATEGORIES[kind][0][0],
      value: "",
      as_of: today(),
      secured_by_id: "",
      notes: "",
    };
  }

  function startEdit(h) {
    form = {
      ...h,
      value: String(h.value),
      was: String(h.value),
      as_of: today(),
      secured_by_id: h.secured_by_id ?? "",
      notes: h.notes ?? "",
    };
  }

  async function save(e) {
    e.preventDefault();
    const body = {
      name: form.name,
      category: form.category,
      value: form.value,
      as_of: form.as_of || null,
      secured_by_id: form.kind === "debt" && form.secured_by_id !== "" ? Number(form.secured_by_id) : null,
      notes: form.notes || null,
    };
    // A rename alone records no new value.
    if (form.id != null && String(form.value) === form.was) {
      delete body.value;
      delete body.as_of;
    }
    try {
      if (form.id == null) await api.createHolding({ ...body, kind: form.kind });
      else await api.updateHolding(form.id, body);
      form = null;
      await load();
    } catch (err) {
      error = String(err);
    }
  }

  async function remove(id) {
    deleting = null;
    try {
      await api.deleteHolding(id);
      await load();
    } catch (e) {
      error = String(e);
    }
  }
</script>

{#if error}<p class="error">{error}</p>{/if}

{#if data}
  <section class="tiles">
    <div class="tile">
      <span class="label">Net worth</span>
      <span class="value" class:neg={Number(data.net_worth) < 0}>{fmtMoney(data.net_worth)}</span>
      <span class="sub">
        {#if change != null}
          <span class:bad={change < 0} class:good={change > 0}>
            {change >= 0 ? "▲" : "▼"} {fmtMoney(Math.abs(change))}
          </span>
          since {fmtDate(lastMonth.on, { month: "short", day: "numeric" })}
        {/if}
      </span>
    </div>
    <div class="tile">
      <span class="label">You own</span>
      <span class="value">{fmtMoney(data.assets)}</span>
      <span class="sub">{assets.length} {assets.length === 1 ? "account" : "accounts and assets"}</span>
    </div>
    <div class="tile">
      <span class="label">You owe</span>
      <span class="value">{fmtMoney(data.debts)}</span>
      <span class="sub">{owed.length} {owed.length === 1 ? "debt" : "debts"}</span>
    </div>
    <div class="tile">
      <span class="label">Debt payments a month</span>
      <span class="value">{fmtMoney(classTotal("debt"))}</span>
      <span class="sub">yours back each month once paid off</span>
    </div>
  </section>

  {#if !manualAssets.length}
    <p class="hint">
      Only linked accounts so far. Add the home, the car and retirement accounts, and the mortgage and
      loans Plaid can't see, under <a href="#own">What you own and owe</a>.
    </p>
  {/if}

  <section class="panel">
    <div class="head"><h2>Net worth by month</h2></div>
    <div class="chart" bind:this={el}></div>
    <p class="note">
      Linked balances at each month's end are worked back from today's through the transactions since.
      Anything entered by hand counts at its value then, or its first value before you entered it.
    </p>
  </section>

  <section class="panel">
    <div class="head">
      <h2>Month by month</h2>
      <span class="muted">the same money in and out as Cash flow</span>
    </div>
    {#if avg}
      <p class="summary">
        Over the last {full.length} full {full.length === 1 ? "month" : "months"} you've averaged
        <b>{fmtMoney(avg.income)}</b> in, against <b>{fmtMoney(avg.essential)}</b> on essentials,
        <b>{fmtMoney(avg.debt)}</b> on debt payments and <b>{fmtMoney(avg.other)}</b> on everything else.
        Without the debt payments that would leave
        <b class:bad={avg.income - avg.essential - avg.other < 0} class:good={avg.income - avg.essential - avg.other >= 0}>
          {fmtMoney(avg.income - avg.essential - avg.other)}</b> a month.
        {#if avg.card > 0.5}
          Card balances have grown by <b class="bad">{fmtMoney(avg.card)}</b> a month on average, so new card
          debt is outpacing what's being paid down.
        {:else if avg.card < -0.5}
          Card balances have come down by <b class="good">{fmtMoney(-avg.card)}</b> a month on average.
        {/if}
      </p>
    {/if}
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>Month</th>
            <th class="num">Income</th>
            <th class="num">Essentials</th>
            <th class="num">Debt payments</th>
            <th class="num">Everything else</th>
            <th class="num">Up / short</th>
            <th class="num">Bank</th>
            <th class="num">Card debt</th>
            <th class="grow">Where it went</th>
          </tr>
        </thead>
        <tbody>
          {#each [...data.months].reverse() as m (m.month)}
            <tr class:partial={m.partial}>
              <td class="when">{m.month}{#if m.partial}<span class="tag">so far</span>{/if}</td>
              <td class="num">{fmtMoney(m.income)}</td>
              <td class="num">{fmtMoney(m.essential)}</td>
              <td class="num">{fmtMoney(m.debt)}</td>
              <td class="num">{fmtMoney(m.other)}</td>
              <td class="num strong" class:bad={Number(m.net) < 0} class:good={Number(m.net) > 0}>
                {fmtMoney(m.net)}
              </td>
              <td class="num" class:bad={Number(m.cash_change) < 0}>{fmtMoney(m.cash_change)}</td>
              <td class="num" class:bad={Number(m.card_change) > 0} class:good={Number(m.card_change) < 0}>
                {Number(m.card_change) > 0 ? "+" : ""}{fmtMoney(m.card_change)}
                <span class="sub">{fmtMoney(m.card_charged)} on, {fmtMoney(m.card_paid)} off</span>
              </td>
              <td class="why">{verdict(m)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="note">
      Debt payments here are what left for lenders that aren't linked (the mortgage, loans) plus card
      interest. Paying a linked card is a move between your own accounts, so it shows as card debt going
      down, not as spending.
    </p>
  </section>

  <section class="panel">
    <div class="head">
      <h2>Regular payments</h2>
      <span class="muted">a month each; pick which column a detected payment belongs in</span>
    </div>
    <div class="classes">
      {#each CLASSES as c (c.key)}
        <div class="class">
          <h3>
            {c.label}
            <span class="total">{fmtMoney(classTotal(c.key))}</span>
          </h3>
          <span class="muted small">{c.note}</span>
          <table>
            <tbody>
              {#each byClass[c.key] as p (p.source + p.ref_id)}
                <tr>
                  <td class="pname">
                    {p.name}
                    {#if p.frequency && p.frequency !== "MONTHLY"}
                      <span class="tag">{p.frequency.toLowerCase().replace("_", "-")}</span>
                    {/if}
                    {#if p.owed != null}<span class="sub">{fmtMoney(p.owed)} left</span>{/if}
                  </td>
                  <td class="num">{fmtMoney(p.monthly)}</td>
                  <td class="pick">
                    {#if p.source === "recurring"}
                      <select
                        value={p.chosen ? p.commitment : ""}
                        onchange={(e) => setClass(p, e.target.value)}
                        aria-label="Column for {p.name}"
                      >
                        <option value="">Auto</option>
                        {#each CLASSES as o}<option value={o.key}>{o.label}</option>{/each}
                      </select>
                    {/if}
                  </td>
                </tr>
              {:else}
                <tr><td class="muted">Nothing here.</td></tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/each}
    </div>
    <p class="note">
      Cards count at their planned payment. A payment charged to a card is in both that card's payment
      and its own line while the card carries a balance; once the card's paid off, only its own line is
      left.
    </p>
  </section>

  <section class="panel" id="own">
    <div class="head">
      <h2>What you own and owe</h2>
      <span class="actions">
        <button onclick={() => startAdd("asset")}>+ Something you own</button>
        <button onclick={() => startAdd("debt")}>+ Something you owe</button>
      </span>
    </div>

    {#if form}
      <form class="hform" onsubmit={save}>
        <label>
          Name
          <input bind:value={form.name} required placeholder={form.kind === "asset" ? "Home" : "Mortgage"} />
        </label>
        <label>
          Type
          <select bind:value={form.category}>
            {#each CATEGORIES[form.kind] as [k, label]}<option value={k}>{label}</option>{/each}
          </select>
        </label>
        <label>
          {form.kind === "asset" ? "Worth" : "Left to pay"}
          <input type="number" min="0" step="0.01" bind:value={form.value} required />
        </label>
        <label>
          As of
          <input type="date" bind:value={form.as_of} />
        </label>
        {#if form.kind === "debt"}
          <label>
            Secured on
            <select bind:value={form.secured_by_id}>
              <option value="">Nothing</option>
              {#each manualAssets as a}<option value={a.holding.id}>{a.name}</option>{/each}
            </select>
          </label>
        {/if}
        <label class="wide">
          Notes
          <input bind:value={form.notes} placeholder="Zillow estimate, 401(k) at…" />
        </label>
        <span class="buttons">
          <button type="submit" class="primary">{form.id == null ? "Add" : "Save"}</button>
          <button type="button" onclick={() => (form = null)}>Cancel</button>
        </span>
        {#if form.id != null}
          <span class="muted small">A new value is kept alongside the old ones, so the chart shows the change.</span>
        {/if}
      </form>
    {/if}

    <div class="sides">
      {#each [["asset", "Own", assets, data.assets], ["debt", "Owe", owed, data.debts]] as [kind, title, rows, total] (kind)}
        <div class="side">
          <h3>{title} <span class="total">{fmtMoney(total)}</span></h3>
          <table>
            <tbody>
              {#each rows as l (l.source + l.ref_id)}
                <tr>
                  <td class="pname">
                    {l.name}
                    <span class="sub">
                      {categoryLabel(l)}{l.detail ? ` · ${l.detail}` : ""}{l.source === "linked" ? " · linked" : ""}
                      {#if l.source === "manual"} · as of {fmtDate(l.as_of, { month: "short", day: "numeric", year: "numeric" })}{/if}
                    </span>
                  </td>
                  <td class="num">{fmtMoney(l.value)}</td>
                  <td class="pick">
                    {#if l.source === "manual"}
                      {#if deleting === l.holding.id}
                        <button class="small danger" onclick={() => remove(l.holding.id)}>Delete</button>
                        <button class="small" onclick={() => (deleting = null)}>Keep</button>
                      {:else}
                        <button class="small" title="Edit or update the value" onclick={() => startEdit(l.holding)}>✎</button>
                        <button class="small" title="Remove" onclick={() => (deleting = l.holding.id)}>×</button>
                      {/if}
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/each}
    </div>

    {#if data.equity.length}
      <h3 class="eq">Equity</h3>
      <table>
        <tbody>
          {#each data.equity as q (q.name)}
            <tr>
              <td class="pname">{q.name}<span class="sub">{fmtMoney(q.value)} less {fmtMoney(q.owed)} owed</span></td>
              <td class="num strong" class:bad={Number(q.equity) < 0}>{fmtMoney(q.equity)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
{:else if !error}
  <p class="muted">Loading…</p>
{/if}

<style>
  .error {
    color: var(--danger);
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
  .label {
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
  .tile .sub {
    color: var(--muted);
    font-size: 0.82rem;
  }
  .hint {
    margin: 0 0 1rem;
    padding: 0.55rem 0.8rem;
    border: 1px solid var(--accent);
    background: var(--accent-dim);
    border-radius: 8px;
    font-size: 0.88rem;
  }
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
  h3 {
    font-size: 0.92rem;
    margin: 0 0 0.2rem;
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }
  h3.eq {
    margin-top: 1rem;
  }
  .total {
    font-variant-numeric: tabular-nums;
  }
  .muted {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .small {
    font-size: 0.78rem;
  }
  .chart {
    width: 100%;
    height: 300px;
  }
  .note {
    color: var(--muted);
    font-size: 0.8rem;
    margin: 0.6rem 0 0;
  }
  .summary {
    margin: 0 0 0.8rem;
    font-size: 0.9rem;
    line-height: 1.5;
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
    vertical-align: top;
  }
  th.grow {
    width: 100%;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  th.num {
    text-align: right;
  }
  .strong {
    font-weight: 600;
  }
  .bad {
    color: var(--danger);
  }
  .good {
    color: var(--accent);
  }
  td .sub {
    display: block;
    color: var(--muted);
    font-size: 0.76rem;
  }
  td.when {
    white-space: nowrap;
  }
  td.why {
    font-size: 0.85rem;
    min-width: 16rem;
  }
  tr.partial td {
    color: var(--muted);
  }
  .tag {
    white-space: nowrap;
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 0.3rem;
    margin-left: 0.4rem;
  }
  .classes,
  .sides {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1rem 1.5rem;
  }
  .classes table,
  .sides table {
    margin-top: 0.4rem;
  }
  .pname {
    width: 100%;
    overflow-wrap: anywhere;
  }
  td.pick {
    white-space: nowrap;
    text-align: right;
    padding-right: 0;
  }
  td.pick select {
    font-size: 0.78rem;
    padding: 0.15rem 0.3rem;
    max-width: 9.5rem;
  }
  button.small {
    font-size: 0.78rem;
    padding: 0.15rem 0.45rem;
  }
  button.danger {
    color: var(--danger);
    border-color: var(--danger);
  }
  .actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .hform {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 0.6rem 0.8rem;
    padding: 0.8rem;
    margin-bottom: 1rem;
    background: var(--panel-2);
    border-radius: 8px;
  }
  .hform label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .hform label.wide {
    flex: 1;
    min-width: 12rem;
  }
  .hform input,
  .hform select {
    background: var(--panel);
  }
  .buttons {
    display: flex;
    gap: 0.5rem;
  }
</style>
