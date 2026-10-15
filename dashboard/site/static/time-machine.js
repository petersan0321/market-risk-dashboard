// Time machine: look up the dashboard as it stood on any past day or over any period.
// Data: data/tm/index.json plus one <year>.json per year, loaded on demand.
(function () {
  const ROOT = window.TM_ROOT || "";
  const out = document.getElementById("tm-output");
  const cache = {};
  let INDEX = null;

  // --- formatting ------------------------------------------------------------------------
  const ICON = { green: "●", amber: "▲", red: "■", none: "○" };
  const LABEL = { green: "Green", amber: "Amber", red: "Red", none: "No score" };
  const BAND_LIGHT = { Calm: "green", Normal: "none", Elevated: "amber", High: "red" };
  const BAND_ICON = { Calm: "●", Normal: "◆", Elevated: "▲", High: "■" };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const band = (s) => (s == null ? null : s >= 75 ? "High" : s >= 60 ? "Elevated" : s >= 40 ? "Normal" : "Calm");
  const light = (s) => (s == null ? "none" : s > 85 ? "red" : s >= 60 ? "amber" : "green");
  const chip = (l) => `<span class="chip chip-${l}"><span class="icon icon-${l}" aria-hidden="true">${ICON[l]}</span> ${LABEL[l]}</span>`;
  const bandChip = (b) => (b ? `<span class="chip chip-${BAND_LIGHT[b]}"><span class="icon icon-${BAND_LIGHT[b]}" aria-hidden="true">${BAND_ICON[b]}</span> ${b}</span>` : chip("none"));
  const pct = (v, signed = true) => (v == null ? "—" : `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
  const num = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
  function value(v, unit) {
    if (v == null) return "—";
    if (unit === "k") return `${Math.round(v).toLocaleString()}k`;
    const t = Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return { "%": `${t}%`, pp: `${t} pp`, x: `${t}×`, "": t }[unit] ?? `${t} ${unit}`;
  }
  const trendText = (now, then) => {
    if (now == null || then == null) return "";
    const d = now - then;
    return d >= 10 ? "↑ rising" : d <= -10 ? "↓ easing" : "→ steady";
  };
  const addDays = (iso, n) => new Date(Date.parse(iso) + n * 86400000).toISOString().slice(0, 10);

  // --- data --------------------------------------------------------------------------------
  function loadYear(y) {
    if (!INDEX.years.includes(y)) return Promise.resolve(null);
    cache[y] ||= fetch(`${ROOT}data/tm/${y}.json`).then((r) => r.json());
    return cache[y];
  }

  async function loadSpan(y0, y1) {
    const parts = (await Promise.all(Array.from({ length: y1 - y0 + 1 }, (_, k) => loadYear(y0 + k)))).filter(Boolean);
    const m = { dates: [], overall: [], pct: [], regime: [], spx: [], bond: [], categories: {}, indicators: {}, stale: {}, rules: {} };
    for (const p of parts) {
      const n = p.dates.length;
      const blank = (v) => Array(n).fill(v);
      for (const k of ["dates", "overall", "pct", "regime", "spx", "bond"]) m[k].push(...p[k]);
      for (const c of Object.keys(INDEX.categories)) (m.categories[c] ||= []).push(...(p.categories[c] || blank(null)));
      for (const ind of INDEX.indicators) {
        const src = p.indicators[ind.id];
        const t = (m.indicators[ind.id] ||= { v: [], s: [] });
        t.v.push(...(src ? src.v : blank(null)));
        t.s.push(...(src ? src.s : blank(null)));
        (m.stale[ind.id] ||= []).push(...(p.stale[ind.id] || blank(0)));
        (m.rules[ind.id] ||= []).push(...(p.rules[ind.id] || blank(null)));
      }
    }
    return m;
  }

  function lastIndexOnOrBefore(dates, iso) {
    let lo = 0, hi = dates.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (dates[mid] <= iso) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }

  const clamp = (iso) => (iso < INDEX.first_date ? INDEX.first_date : iso > INDEX.last_date ? INDEX.last_date : iso);

  function contributions(m, i) {
    const w = INDEX.weights;
    const avail = Object.keys(INDEX.categories).filter((c) => m.categories[c][i] != null);
    const total = avail.reduce((s, c) => s + w[c], 0);
    return avail.map((c) => ({ c, v: (w[c] * (m.categories[c][i] - 50)) / total })).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  }

  function forward(arr, i, n) {
    return i + n < arr.length && arr[i] != null && arr[i + n] != null ? arr[i + n] / arr[i] - 1 : null;
  }

  function maxDrawdown(arr, i, n) {
    if (i + n >= arr.length) return null;
    let peak = arr[i], worst = 0;
    for (let k = i; k <= i + n; k++) {
      if (arr[k] == null) continue;
      peak = Math.max(peak, arr[k]);
      worst = Math.max(worst, 1 - arr[k] / peak);
    }
    return worst;
  }

  // --- day view ------------------------------------------------------------------------------
  async function showDay(requested) {
    const iso = clamp(requested);
    const y = Number(iso.slice(0, 4));
    const m = await loadSpan(y - 1, y + 1);
    const i = lastIndexOnOrBefore(m.dates, iso);
    if (i < 0) { out.innerHTML = `<p class="muted">No data for ${esc(iso)}.</p>`; return; }
    const day = m.dates[i];
    const o = m.overall[i];
    const j = i - 63;
    const note = day !== requested ? `<p class="small muted">Showing ${day}, the nearest earlier day with data (requested ${esc(requested)}).</p>` : "";
    const reg = m.regime[i] ? INDEX.regimes[m.regime[i]] : null;

    const cats = Object.entries(INDEX.categories).map(([c, name]) => {
      const s = m.categories[c][i];
      return `<a class="card card-link" href="category/${c}.html"><h3>${esc(name)}</h3>
        <div class="row"><p class="mid-number">${num(s)}</p>${chip(light(s))}</div>
        <p class="small">${trendText(s, j >= 0 ? m.categories[c][j] : null)} <span class="muted">vs 3 months earlier</span></p></a>`;
    }).join("");

    const contrib = contributions(m, i).map((x) => `<li>${esc(INDEX.categories[x.c])} ${x.v > 0 ? "+" : ""}${x.v.toFixed(1)}</li>`).join("");

    const rows = Object.entries(INDEX.categories).map(([c, name]) => {
      const inds = INDEX.indicators.filter((ind) => ind.category === c).map((ind) => {
        const s = m.indicators[ind.id].s[i];
        const stale = m.stale[ind.id][i] && s != null;
        const rule = m.rules[ind.id][i];
        const l = stale ? "none" : light(s);
        return `<tr><th scope="row" class="heat-ind">${esc(ind.name)}${ind.revised ? ' <abbr title="Revised data" class="rev">R</abbr>' : ""}${rule ? `<br><span class="small">⚠ ${esc(rule)}</span>` : ""}</th>
          <td>${value(m.indicators[ind.id].v[i], ind.unit)}</td><td>${num(s)}</td><td>${chip(l)}${stale ? ' <span class="badge badge-warn">Stale</span>' : ""}</td></tr>`;
      }).join("");
      return `<tr class="heat-cat"><th scope="row" colspan="4">${esc(name)}</th></tr>${inds}`;
    }).join("");

    const yearLater = addDays(day, 365);
    const evs = INDEX.events.filter((e) => (e.start <= day && day <= e.end) || (day < e.start && e.start <= yearLater));
    const events = evs.length ? `<ul class="plain">${evs.map((e) => `<li><a href="history/${e.id}.html">${esc(e.name)}</a>
        <span class="small muted">${e.start <= day ? "in progress" : "starts " + e.start}</span><br><span class="small muted">${esc(e.summary)}</span></li>`).join("")}</ul>`
      : '<p class="muted small">No playbook events in progress or starting within the next 12 months.</p>';

    const horizons = [["1 month", 21], ["3 months", 63], ["6 months", 126], ["12 months", 252]];
    const next = `<table><thead><tr><th>Over the next…</th>${horizons.map(([l]) => `<th>${l}</th>`).join("")}</tr></thead><tbody>
      <tr><th scope="row">S&amp;P 500</th>${horizons.map(([, n]) => `<td>${forward(m.spx, i, n) == null ? "not known yet" : pct(forward(m.spx, i, n))}</td>`).join("")}</tr>
      <tr><th scope="row">Bonds</th>${horizons.map(([, n]) => `<td>${forward(m.bond, i, n) == null ? "not known yet" : pct(forward(m.bond, i, n))}</td>`).join("")}</tr>
      </tbody></table>
      <p class="small">Worst S&amp;P 500 fall within the next 12 months: <strong>${maxDrawdown(m.spx, i, 252) == null ? "not known yet" : pct(maxDrawdown(m.spx, i, 252), false)}</strong></p>`;

    out.innerHTML = `${note}
      <section class="hero">
        <div class="hero-score"><p class="eyebrow">Overall risk on ${day}</p>
          <p class="big-number">${num(o)}<span class="of">/100</span></p>${bandChip(band(o))}
          <p class="small muted">${m.pct[i] != null ? `Riskier than ${m.pct[i]}% of all earlier days.` : ""}</p></div>
        <div class="hero-why"><h2>At the time</h2><ul class="plain">
          <li><strong>Economic regime:</strong> ${reg ? `${esc(reg.label)} <span class="muted">(${esc(reg.description)})</span>` : "not enough data"}</li>
          <li><strong>Biggest drivers:</strong><ul class="plain small">${contrib}</ul></li></ul></div>
      </section>
      <section><h2>Categories</h2><div class="grid grid-cats">${cats}</div></section>
      <section class="card"><h2>Playbook events</h2>${events}</section>
      <section class="card"><details><summary>Show what happened next</summary>
        <p class="small muted">Try forming your own view from the readings first. Past outcomes are not a guide to the future.</p>${next}</details></section>
      <section class="card"><h2>Every indicator on ${day}</h2><div class="table-wrap"><table class="heatmap tm-ind-table">
        <thead><tr><th>Indicator</th><th>Reading</th><th>Score</th><th>Light</th></tr></thead><tbody>${rows}</tbody></table></div>
        <p class="small muted"><abbr title="Revised data" class="rev">R</abbr> = revised data: today's revised figures, which may differ from what was known at the time.</p></section>`;
    history.replaceState(null, "", `?date=${day}`);
  }

  // --- range view ----------------------------------------------------------------------------
  async function showRange(fromReq, toReq) {
    let [from, to] = [clamp(fromReq), clamp(toReq)];
    if (from > to) [from, to] = [to, from];
    const m = await loadSpan(Number(from.slice(0, 4)), Number(to.slice(0, 4)));
    const a = Math.max(0, lastIndexOnOrBefore(m.dates, from) + (m.dates.includes(from) ? 0 : 1));
    const b = lastIndexOnOrBefore(m.dates, to);
    if (b < a) { out.innerHTML = '<p class="muted">No data in that period.</p>'; return; }
    const x = m.dates.slice(a, b + 1);
    const y = m.overall.slice(a, b + 1);
    const scored = y.map((v, k) => [v, x[k]]).filter(([v]) => v != null);
    if (!scored.length) { out.innerHTML = '<p class="muted">No overall score in that period.</p>'; return; }
    const min = scored.reduce((p, q) => (q[0] < p[0] ? q : p));
    const max = scored.reduce((p, q) => (q[0] > p[0] ? q : p));
    const avg = scored.reduce((s, q) => s + q[0], 0) / scored.length;
    const daysIn = { Calm: 0, Normal: 0, Elevated: 0, High: 0 };
    const changes = [];
    let prev = null;
    scored.forEach(([v, d]) => { const bnd = band(v); daysIn[bnd]++; if (prev && bnd !== prev) changes.push([d, prev, bnd]); prev = bnd; });
    const ret = (arr) => (arr[a] != null && arr[b] != null ? arr[b] / arr[a] - 1 : null);
    const evs = INDEX.events.filter((e) => e.end >= x[0] && e.start <= x[x.length - 1]);

    const changeList = changes.length
      ? `<ul class="plain small">${changes.slice(0, 20).map(([d, p, q]) => `<li>${d}: ${p} → ${q}</li>`).join("")}</ul>${changes.length > 20 ? `<p class="small muted">and ${changes.length - 20} more</p>` : ""}`
      : '<p class="small muted">The score stayed in one band.</p>';
    const options = Object.entries(INDEX.categories).map(([c, n]) => `<option value="${c}">${esc(n)}</option>`).join("");

    out.innerHTML = `
      <section class="card"><h2>${x[0]} to ${x[x.length - 1]}</h2>
        <div class="stat-row">
          <div class="stat"><p class="small muted">Start → end</p><p class="mid-number">${num(scored[0][0])} → ${num(scored[scored.length - 1][0])}</p></div>
          <div class="stat"><p class="small muted">Highest</p><p class="mid-number">${num(max[0])}</p><p class="small muted">${max[1]}</p></div>
          <div class="stat"><p class="small muted">Lowest</p><p class="mid-number">${num(min[0])}</p><p class="small muted">${min[1]}</p></div>
          <div class="stat"><p class="small muted">Average</p><p class="mid-number">${num(avg)}</p></div>
          <div class="stat"><p class="small muted">S&amp;P 500</p><p class="mid-number">${pct(ret(m.spx))}</p></div>
          <div class="stat"><p class="small muted">Bonds</p><p class="mid-number">${pct(ret(m.bond))}</p></div>
        </div></section>
      <section class="card"><div class="row"><h2>Overall score</h2>
          <label class="small">Compare with <select id="overlay"><option value="">no category</option>${options}</select></label></div>
        <div class="chart" id="range-score"></div>
        <h3 class="small-h">S&amp;P 500 and bonds (start = 100)</h3><div class="chart" id="range-prices"></div>
        <p class="small muted">Grey shading marks playbook events.</p></section>
      <section class="two-col">
        <div class="card"><h2>Days in each band</h2><ul class="plain">${Object.entries(daysIn).map(([k, v]) => `<li>${bandChip(k)} ${v.toLocaleString()} days</li>`).join("")}</ul></div>
        <div class="card"><h2>Band changes</h2>${changeList}</div>
      </section>
      <section class="card"><h2>Playbook events in this period</h2>${evs.length ? `<ul class="plain">${evs.map((e) => `<li><a href="history/${e.id}.html">${esc(e.name)}</a> <span class="small muted">${e.start} to ${e.end}</span></li>`).join("")}</ul>` : '<p class="muted small">None.</p>'}</section>`;

    const shading = evs.map((e) => ({ name: e.name, start: e.start, end: e.end, label: evs.length <= 4 }));
    const bands = INDEX.bands.map((bd, k) => ({ name: bd.name, from: bd.from, to: k === 0 ? 100 : INDEX.bands[k - 1].from }));
    const base = (arr) => { const s = arr.slice(a, b + 1); const f = s.find((v) => v != null); return s.map((v) => (v == null || f == null ? null : (v / f) * 100)); };
    const draw = () => {
      const c = document.getElementById("overlay").value;
      window.DashCharts.scoreChart(document.getElementById("range-score"), {
        x, y, bands, events: shading,
        overlay: c ? { name: INDEX.categories[c], y: m.categories[c].slice(a, b + 1) } : null,
      });
      window.DashCharts.priceChart(document.getElementById("range-prices"), { x, stocks: base(m.spx), bonds: base(m.bond), events: shading });
    };
    document.getElementById("overlay").addEventListener("change", draw);
    window.DashCharts.rerender = [draw];
    draw();
    history.replaceState(null, "", `?from=${x[0]}&to=${x[x.length - 1]}`);
  }

  // --- controls --------------------------------------------------------------------------------
  function setMode(mode) {
    document.getElementById("mode-day").setAttribute("aria-pressed", String(mode === "day"));
    document.getElementById("mode-range").setAttribute("aria-pressed", String(mode === "range"));
    document.getElementById("day-form").hidden = mode !== "day";
    document.getElementById("range-form").hidden = mode !== "range";
  }

  async function init() {
    try {
      INDEX = await (await fetch(`${ROOT}data/tm/index.json`)).json();
    } catch (e) {
      out.innerHTML = '<p class="muted">Could not load time machine data.</p>';
      return;
    }
    for (const id of ["day-input", "from-input", "to-input"]) {
      const el = document.getElementById(id);
      el.min = INDEX.first_date;
      el.max = INDEX.last_date;
    }
    document.getElementById("mode-day").addEventListener("click", () => setMode("day"));
    document.getElementById("mode-range").addEventListener("click", () => setMode("range"));
    document.getElementById("day-form").addEventListener("submit", (e) => { e.preventDefault(); showDay(document.getElementById("day-input").value); });
    document.getElementById("range-form").addEventListener("submit", (e) => {
      e.preventDefault();
      showRange(document.getElementById("from-input").value, document.getElementById("to-input").value);
    });
    const params = new URLSearchParams(location.search);
    if (params.get("from") && params.get("to")) {
      setMode("range");
      document.getElementById("from-input").value = params.get("from");
      document.getElementById("to-input").value = params.get("to");
      await showRange(params.get("from"), params.get("to"));
    } else {
      const day = params.get("date") || INDEX.last_date;
      document.getElementById("day-input").value = day;
      await showDay(day);
    }
  }

  init();
})();
