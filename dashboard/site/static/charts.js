// Renders every [data-chart] element with Plotly. Colors come from the page's CSS tokens,
// so charts follow light/dark mode.
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const BAND_FILL = { High: "--zone-red", Elevated: "--zone-amber", Normal: null, Calm: "--zone-green" };
  const LINE_COLOR = { amber: "--warning", red: "--critical" };

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

  function scoreChart(el, d) {
    const shapes = d.bands.filter((b) => BAND_FILL[b.name]).map((b) => ({
      type: "rect", xref: "paper", x0: 0, x1: 1, yref: "y", y0: b.from, y1: b.to,
      fillcolor: css(BAND_FILL[b.name]), opacity: 0.6, line: { width: 0 }, layer: "below",
    }));
    const annotations = d.bands.map((b) => ({
      xref: "paper", x: 1.01, xanchor: "left", yref: "y", y: (b.from + b.to) / 2, text: b.name,
      showarrow: false, font: { size: 11, color: css("--text-muted") },
    }));
    Plotly.newPlot(el, [{
      x: d.x, y: d.y, type: "scatter", mode: "lines", name: "Score",
      line: { color: css("--series-1"), width: 2 },
      hovertemplate: "%{x|%b %d, %Y}<br>Score <b>%{y:.0f}</b><extra></extra>",
    }], layout({ yaxis: { range: [0, 100], gridcolor: css("--grid"), zeroline: false, dtick: 20 }, shapes, annotations,
                 margin: { l: 40, r: 72, t: 8, b: 32 } }),
    { displayModeBar: false, responsive: true });
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
    }], layout({ yaxis: { gridcolor: css("--grid"), zeroline: false }, shapes, annotations }),
    { displayModeBar: false, responsive: true });
  }

  function renderAll() {
    if (!window.Plotly) return;
    document.querySelectorAll("[data-chart]").forEach((el) => {
      const data = JSON.parse(document.getElementById(el.dataset.source).textContent);
      (el.dataset.chart === "value" ? valueChart : scoreChart)(el, data);
    });
  }

  renderAll();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);
})();
