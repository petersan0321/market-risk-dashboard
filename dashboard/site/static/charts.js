// Chart rendering with Plotly. Colors come from the page's CSS tokens, so charts follow
// light/dark mode. Every [data-chart] element is rendered on load; the time machine calls
// window.DashCharts directly.
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const BAND_FILL = { High: "--zone-red", Elevated: "--zone-amber", Normal: null, Calm: "--zone-green" };
  const LINE_COLOR = { amber: "--warning", red: "--critical" };
  const CONFIG = { displayModeBar: false, responsive: true };

  function layout(extra) {
    return Object.assign({
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "system-ui, -apple-system, sans-serif", color: css("--text-secondary"), size: 12 },
      margin: { l: 48, r: 16, t: 8, b: 32 },
      xaxis: { gridcolor: css("--grid"), linecolor: css("--border"), zeroline: false, showspikes: true,
               spikecolor: css("--text-muted"), spikethickness: 1, spikedash: "solid", spikemode: "across" },
      hovermode: "x",
      hoverlabel: { bgcolor: css("--surface-1"), bordercolor: css("--border"), font: { color: css("--text-primary") } },
      showlegend: false,
    }, extra);
  }

  // Grey bands for event windows (neutral, so they never read as a status color).
  function eventShapes(events) {
    return (events || []).map((e) => ({
      type: "rect", xref: "x", x0: e.start, x1: e.end, yref: "paper", y0: 0, y1: 1,
      fillcolor: css("--event-fill"), line: { width: 0 }, layer: "below",
    }));
  }

  function eventLabels(events) {
    return (events || []).filter((e) => e.label).map((e) => ({
      xref: "x", x: e.start, xanchor: "left", yref: "paper", y: 1, yanchor: "top", text: e.name,
      showarrow: false, font: { size: 10, color: css("--text-muted") },
    }));
  }

  function scoreChart(el, d) {
    const shapes = d.bands.filter((b) => BAND_FILL[b.name]).map((b) => ({
      type: "rect", xref: "paper", x0: 0, x1: 1, yref: "y", y0: b.from, y1: b.to,
      fillcolor: css(BAND_FILL[b.name]), opacity: 0.6, line: { width: 0 }, layer: "below",
    })).concat(eventShapes(d.events));
    const annotations = d.bands.map((b) => ({
      xref: "paper", x: 1.01, xanchor: "left", yref: "y", y: (b.from + b.to) / 2, text: b.name,
      showarrow: false, font: { size: 11, color: css("--text-muted") },
    })).concat(eventLabels(d.events));
    const traces = [{
      x: d.x, y: d.y, type: "scatter", mode: "lines", name: d.name || "Overall score",
      line: { color: css("--series-1"), width: 2 },
      hovertemplate: "%{x|%b %d, %Y}<br>" + (d.name || "Score") + " <b>%{y:.0f}</b><extra></extra>",
    }];
    if (d.overlay) {
      traces.push({
        x: d.x, y: d.overlay.y, type: "scatter", mode: "lines", name: d.overlay.name,
        line: { color: css("--series-2"), width: 2 },
        hovertemplate: d.overlay.name + " <b>%{y:.0f}</b><extra></extra>",
      });
    }
    Plotly.newPlot(el, traces, layout({
      yaxis: { range: [0, 100], gridcolor: css("--grid"), zeroline: false, dtick: 20 }, shapes, annotations,
      margin: { l: 40, r: 72, t: d.overlay ? 28 : 8, b: 32 },
      showlegend: !!d.overlay, legend: { orientation: "h", x: 0, y: 1.12, font: { color: css("--text-secondary") } },
    }), CONFIG);
  }

  function valueChart(el, d) {
    const fmt = d.unit === "%" ? ":.2f}%" : d.unit === "pp" ? ":.2f} pp" : d.unit === "k" ? ":,.0f}k" : ":,.2f}";
    const shapes = d.lines.map((l) => ({
      type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: l.y, y1: l.y,
      line: { color: css(LINE_COLOR[l.light]), width: 1.5, dash: "dash" },
    }));
    const annotations = d.lines.map((l) => ({
      xref: "paper", x: 1, xanchor: "right", yref: "y", y: l.y, yanchor: "bottom",
      text: l.light === "red" ? "■ red" : "▲ amber", showarrow: false, font: { size: 10, color: css("--text-muted") },
    }));
    Plotly.newPlot(el, [{
      x: d.x, y: d.y, type: "scatter", mode: "lines", name: d.name,
      line: { color: css("--series-1"), width: 2 },
      hovertemplate: "%{x|%b %d, %Y}<br><b>%{y" + fmt + "</b><extra></extra>",
    }], layout({ yaxis: { gridcolor: css("--grid"), zeroline: false }, shapes, annotations }), CONFIG);
  }

  // Stocks and bonds on one axis, both indexed to 100 at the start (never a dual axis).
  function priceChart(el, d) {
    const traces = [
      { name: "S&P 500", y: d.stocks, color: "--series-1" },
      { name: "Bonds", y: d.bonds, color: "--series-2" },
    ].filter((t) => t.y && t.y.length).map((t) => ({
      x: d.x, y: t.y, type: "scatter", mode: "lines", name: t.name,
      line: { color: css(t.color), width: 2 },
      hovertemplate: t.name + " <b>%{y:.1f}</b><extra></extra>",
    }));
    const shapes = d.start ? eventShapes([{ start: d.start, end: d.end }]) : eventShapes(d.events);
    Plotly.newPlot(el, traces, layout({
      yaxis: { gridcolor: css("--grid"), zeroline: false, title: { text: "Start = 100", font: { size: 11 } } },
      shapes, annotations: eventLabels(d.events),
      showlegend: true, legend: { orientation: "h", x: 0, y: 1.12, font: { color: css("--text-secondary") } },
      margin: { l: 56, r: 16, t: 28, b: 32 },
    }), CONFIG);
  }

  function eventScoreChart(el, d) {
    scoreChart(el, {
      x: d.x, y: d.overall, events: [{ start: d.start, end: d.end }],
      bands: [{ name: "High", from: 75, to: 100 }, { name: "Elevated", from: 60, to: 75 },
              { name: "Normal", from: 40, to: 60 }, { name: "Calm", from: 0, to: 40 }],
    });
  }

  const SIM_SERIES = [
    { key: "signal", name: "Signal-based", color: "--series-1" },
    { key: "hold", name: "Hold 60/40", color: "--series-2" },
    { key: "stocks", name: "100% stocks", color: "--series-3" },
  ];

  function simChart(el, d, kind) {
    const traces = SIM_SERIES.map((s) => ({
      x: d.x, y: kind === "growth" ? d[s.key] : d.drawdown[s.key].map((v) => -v * 100),
      type: "scatter", mode: "lines", name: s.name, line: { color: css(s.color), width: 2 },
      hovertemplate: s.name + (kind === "growth" ? " <b>$%{y:,.0f}</b>" : " <b>%{y:.1f}%</b>") + "<extra></extra>",
    }));
    Plotly.newPlot(el, traces, layout({
      yaxis: kind === "growth"
        ? { type: "log", gridcolor: css("--grid"), tickvals: [1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6],
            ticktext: ["$10k", "$20k", "$50k", "$100k", "$200k", "$500k", "$1M"] }
        : { gridcolor: css("--grid"), ticksuffix: "%", zeroline: false },
      showlegend: true, legend: { orientation: "h", x: 0, y: 1.12, font: { color: css("--text-secondary") } },
      margin: { l: 64, r: 16, t: 28, b: 32 },
    }), CONFIG);
  }

  function weightChart(el, d) {
    Plotly.newPlot(el, [{
      x: d.x, y: d.stock_weight.map((v) => v * 100), type: "scatter", mode: "lines", name: "Share in stocks",
      line: { color: css("--series-1"), width: 2, shape: "hv" },
      hovertemplate: "Stocks <b>%{y:.0f}%</b><extra></extra>",
    }], layout({ yaxis: { range: [0, 100], ticksuffix: "%", gridcolor: css("--grid"), zeroline: false } }), CONFIG);
  }

  const RENDERERS = {
    score: scoreChart, value: valueChart, eventprices: priceChart, eventscore: eventScoreChart,
    simgrowth: (el, d) => simChart(el, d, "growth"), simdrawdown: (el, d) => simChart(el, d, "drawdown"),
    simweight: weightChart,
  };

  function renderAll() {
    if (!window.Plotly) return;
    document.querySelectorAll("[data-chart]").forEach((el) => {
      const data = JSON.parse(document.getElementById(el.dataset.source).textContent);
      RENDERERS[el.dataset.chart](el, data);
    });
  }

  window.DashCharts = { scoreChart, priceChart, rerender: [] };
  renderAll();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    renderAll();
    window.DashCharts.rerender.forEach((fn) => fn());
  });
})();
