<script>
  import * as echarts from "echarts";
  import { api, prettyCategory, fmtMoney } from "./api.js";

  let { month } = $props();

  let el;
  let chart;
  let data = $state(null);
  let error = $state(null);

  function render() {
    if (!chart || !data) return;
    const nodes = data.nodes.map((n) => ({ name: prettyCategory(n.name) }));
    const links = data.links.map((l) => ({
      source: prettyCategory(l.source),
      target: prettyCategory(l.target),
      value: l.value,
    }));
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
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
            color: "#e6edf3",
            fontSize: 12,
            // params.value is the node's total flow (the $ in/out of that
            // category), so the spend per category shows on the chart itself.
            formatter: (p) => `${p.name}  {v|${fmtMoney(p.value)}}`,
            rich: {
              v: { color: "#8b97a7", fontSize: 11 },
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
    // referencing month makes this effect re-run on month change
    month;
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
