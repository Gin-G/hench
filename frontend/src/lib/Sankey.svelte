<script>
  import * as echarts from "echarts";
  import { api, prettyCategory, fmtMoney } from "./api.js";

  let { month, reloadKey = 0 } = $props();

  let el;
  let chart;
  let data = $state(null);
  let error = $state(null);

  // Categorical palette in the logo's hues, validated for the dark panel
  // (lightness band, chroma, colour-blind and normal-vision separation of
  // neighbours, contrast) with the dataviz validator. Order matters: it is
  // what keeps the red-green pair apart.
  const PALETTE = ["#068fa7", "#d27830", "#7b62b8", "#ae8e38", "#c35775", "#4379bc", "#50a064", "#ab5637"];
  // Colour follows the category, not this month's ranking, so a category
  // keeps its colour from month to month. The commonest Plaid spending
  // categories come first and get a slot each; rarer ones share, which the
  // node labels (every node is named on the chart) make unambiguous.
  const CATEGORY_ORDER = [
    "FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "RENT_AND_UTILITIES", "LOAN_PAYMENTS",
    "TRANSPORTATION", "ENTERTAINMENT", "GENERAL_SERVICES", "MEDICAL",
    "PERSONAL_CARE", "HOME_IMPROVEMENT", "TRAVEL", "GOVERNMENT_AND_NON_PROFIT",
    "BANK_FEES", "UNCATEGORIZED",
  ];
  // Income and the savings remainder are not spending categories. Names as
  // sent by services/sankey.py.
  const NEUTRAL = "#6f818b";
  const NEUTRAL_NODES = new Set(["Income", "Savings / Unspent"]);

  function categoryColor(name) {
    let i = CATEGORY_ORDER.indexOf(name);
    if (i < 0) {
      // A custom override category: a stable slot from its name.
      i = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0);
    }
    return PALETTE[i % PALETTE.length];
  }

  function nodeColors(links) {
    // Level-1 targets (fed straight from Income) are categories; anything
    // they feed is a subcategory and wears its parent's colour.
    const parent = {};
    for (const l of links) parent[l.target] = l.source;
    const colors = {};
    for (const l of links) {
      const cat = parent[l.target] && parent[parent[l.target]] ? parent[l.target] : l.target;
      colors[l.target] = NEUTRAL_NODES.has(cat) ? NEUTRAL : categoryColor(cat);
    }
    return colors;
  }

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function render() {
    if (!chart || !data) return;
    const colors = nodeColors(data.links);
    const nodes = data.nodes.map((n) => ({
      name: prettyCategory(n.name),
      itemStyle: { color: colors[n.name] ?? NEUTRAL, borderWidth: 0 },
    }));
    const links = data.links.map((l) => ({
      source: prettyCategory(l.source),
      target: prettyCategory(l.target),
      value: l.value,
    }));
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        backgroundColor: token("--panel-2"),
        borderColor: token("--border"),
        textStyle: { color: token("--text") },
        triggerOn: "mousemove",
        formatter: (p) =>
          p.dataType === "edge"
            ? `${p.data.source} → ${p.data.target}<br/><b>${fmtMoney(p.data.value)}</b>`
            : p.name,
      },
      series: [
        {
          type: "sankey",
          left: 10,
          right: 230,
          top: 20,
          bottom: 20,
          nodeWidth: 16,
          nodeGap: 10,
          // Keep nodes in the (value-sorted) order we send them rather than
          // letting ECharts re-shuffle to minimise crossings — that reshuffle
          // is what made the links bow/overlap leaving the Income node.
          layoutIterations: 0,
          nodeAlign: "left",
          emphasis: { focus: "adjacency" },
          lineStyle: { color: "gradient", opacity: 0.45, curveness: 0.5 },
          label: {
            color: token("--text"),
            fontSize: 12,
            // params.value is the node's total flow (the $ in/out of that
            // category), so the spend per category shows on the chart itself.
            formatter: (p) => `${p.name}  {v|${fmtMoney(p.value)}}`,
            rich: {
              v: { color: token("--muted"), fontSize: 11 },
            },
          },
          data: nodes,
          links: links,
        },
      ],
    });
  }

  async function load() {
    error = null;
    try {
      data = await api.sankey(month);
      render();
    } catch (e) {
      error = String(e);
    }
  }

  // Re-init the chart once the element is bound, then load whenever month changes.
  $effect(() => {
    if (el && !chart) {
      chart = echarts.init(el, null, { renderer: "canvas" });
      const ro = new ResizeObserver(() => chart.resize());
      ro.observe(el);
    }
    // referencing these makes the effect re-run when either changes
    month;
    reloadKey; // bumped by the parent after a sync or a bank link
    load();
  });
</script>

<div class="sankey-wrap">
  {#if error}
    <p style="color: var(--danger)">{error}</p>
  {/if}
  {#if data}
    <div class="totals">
      <span>Income <b>{fmtMoney(data.total_income)}</b></span>
      <span>Spending <b>{fmtMoney(data.total_spending)}</b></span>
      <span>Net <b>{fmtMoney(data.total_income - data.total_spending)}</b></span>
    </div>
  {/if}
  <div class="chart" bind:this={el}></div>
  {#if data && data.links.length === 0}
    <p class="empty">No transactions for {month}. Connect a bank or run a sync.</p>
  {/if}
</div>

<style>
  .sankey-wrap {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
  }
  .chart {
    width: 100%;
    height: 560px;
  }
  .totals {
    display: flex;
    gap: 1.5rem;
    margin-bottom: 0.5rem;
    color: var(--muted);
  }
  .totals b {
    color: var(--text);
    margin-left: 0.3rem;
  }
  .empty {
    color: var(--muted);
    text-align: center;
  }
</style>
