"""Step: render the static site from data/scores into site/."""

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from dashboard.composite import BANDS
from dashboard.config import (CATEGORIES, Event, GlossaryTerm, Indicator, Untracked, load_events, load_glossary,
                              load_indicators, load_untracked, load_weights)
from dashboard.paths import SCORES_DIR, SITE_DIR
from dashboard.regime import REGIMES
from dashboard.signals import AMBER, RED, read_tables
from dashboard.timemachine import export as export_time_machine

HERE = Path(__file__).parent
LIGHT_LABELS = {"green": "Green", "amber": "Amber", "red": "Red", "none": "No score"}
LIGHT_ICONS = {"green": "●", "amber": "▲", "red": "■", "none": "○"}
TREND_LABELS = {"up": "↑ Risk rising", "down": "↓ Risk easing", "flat": "→ Steady"}
PLOTLY_URL = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.31.0/plotly-basic.min.js"
OVERVIEW_YEARS = 5
INDICATOR_CHART_YEARS = 10


# --- formatting -----------------------------------------------------------------------

def format_value(value: float | None, unit: str) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    if unit == "k":
        return f"{value:,.0f}k"
    text = f"{value:,.2f}"
    return {"%": f"{text}%", "pp": f"{text} pp", "x": f"{text}×", "": text}.get(unit, f"{text} {unit}")


def format_pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%" if signed else f"{value * 100:.0f}%"


def spectrum_zones(ind: Indicator, spectrum: dict) -> dict:
    """Positions (0-100% of the bar) of the green/amber/red zones and the markers."""
    lo, hi = spectrum["min"], spectrum["max"]
    span = (hi - lo) or 1.0

    def pos(v: float) -> float:
        return max(0.0, min(100.0, (v - lo) / span * 100))

    amber, red = spectrum["amber"], spectrum["red"]
    if ind.risk_when == "high":
        cuts = [(0, pos(amber[0]), "green"), (pos(amber[0]), pos(red[0]), "amber"), (pos(red[0]), 100, "red")]
    elif ind.risk_when == "low":
        cuts = [(0, pos(red[0]), "red"), (pos(red[0]), pos(amber[0]), "amber"), (pos(amber[0]), 100, "green")]
    else:
        cuts = [(0, pos(red[0]), "red"), (pos(red[0]), pos(amber[0]), "amber"), (pos(amber[0]), pos(amber[1]), "green"),
                (pos(amber[1]), pos(red[1]), "amber"), (pos(red[1]), 100, "red")]
    return {"zones": [{"left": a, "width": max(b - a, 0), "light": l} for a, b, l in cuts], "pos": pos}


# --- chart data ---------------------------------------------------------------------

def _clean(values) -> list:
    return [None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4) for v in values]


def score_chart(series: pd.Series, years: int | None, weekly: bool = False, events: list[dict] = (),
                label_events: bool = True) -> dict:
    """Score line with band shading and shaded event windows; empty x/y when there is no data."""
    s = series.dropna()
    if years and not s.empty:
        s = s[s.index >= s.index[-1] - pd.DateOffset(years=years)]
    if weekly and not s.empty:
        s = s.resample("W-FRI").last().dropna()
    shown = [] if s.empty else [
        {"name": e["name"], "start": e["start"], "end": e["end"], "label": label_events}
        for e in events if e["kind"] == "crash" and e["end"] >= s.index[0].strftime("%Y-%m-%d")]
    return {"x": [d.strftime("%Y-%m-%d") for d in s.index], "y": _clean(s.values), "events": shown,
            "bands": [{"name": name, "from": floor, "to": ([100] + [f for f, _ in BANDS])[i]}
                      for i, (floor, name) in enumerate(BANDS)]}


def value_chart(values: pd.Series, detail: dict, ind: Indicator) -> dict:
    s = values.dropna()
    s = s[s.index >= s.index[-1] - pd.DateOffset(years=INDICATOR_CHART_YEARS)]
    if len(s) > 1500:
        s = s.resample("W-FRI").last().dropna()
    spec = detail["spectrum"]
    lines = [{"y": v, "light": "amber"} for v in spec["amber"]] + [{"y": v, "light": "red"} for v in spec["red"]]
    return {"x": [d.strftime("%Y-%m-%d") for d in s.index], "y": _clean(s.values), "unit": ind.unit,
            "name": ind.name, "lines": lines}


# --- pages ----------------------------------------------------------------------------

def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
    env.filters["fmt"] = format_value
    env.filters["pct"] = format_pct
    env.filters["signed"] = lambda v: "—" if v is None else f"{v:+.1f}"
    env.globals.update(LIGHT_LABELS=LIGHT_LABELS, LIGHT_ICONS=LIGHT_ICONS, TREND_LABELS=TREND_LABELS,
                       AMBER=AMBER, RED=RED, CATEGORIES=CATEGORIES, PLOTLY_URL=PLOTLY_URL, BANDS=BANDS)
    return env


def _indicator_cards(indicators: list[Indicator], rationale: dict, tables: dict) -> dict[str, dict]:
    cards = {}
    for ind in indicators:
        detail = rationale["indicators"].get(ind.id, {"available": False})
        card = {"ind": ind, "detail": detail}
        if detail.get("available"):
            zones = spectrum_zones(ind, detail["spectrum"])
            card["zones"] = zones["zones"]
            card["today_pos"] = zones["pos"](detail["value"])
            card["markers"] = [{**m, "pos": zones["pos"](m["value"])} for m in detail["spectrum"].get("events", [])]
            card["chart"] = value_chart(tables["value"][ind.id], detail, ind)
        cards[ind.id] = card
    return cards


def _time_machine_link(start: str, end: str) -> str:
    lo = (pd.Timestamp(start) - pd.DateOffset(months=6)).date().isoformat()
    hi = (pd.Timestamp(end) + pd.DateOffset(months=3)).date().isoformat()
    return f"time-machine.html?from={lo}&to={hi}"


def build_site(indicators: list[Indicator], untracked: list[Untracked], scores_dir: Path, site_dir: Path,
               now: datetime, events: list[Event] = (), glossary: dict[str, GlossaryTerm] | None = None,
               weights: dict[str, float] | None = None) -> Path:
    rationale = json.loads((scores_dir / "rationale.json").read_text())
    hist = json.loads((scores_dir / "history.json").read_text())
    composite = pd.read_csv(scores_dir / "composite.csv", index_col="date", parse_dates=["date"])
    market = pd.read_csv(scores_dir / "market.csv", index_col="date", parse_dates=["date"])
    regimes = pd.read_csv(scores_dir / "regime.csv", index_col="date", parse_dates=["date"])["regime"]
    tables = read_tables(scores_dir)
    cards = _indicator_cards(indicators, rationale, tables)
    glossary = glossary or {}
    by_id = {e.id: e for e in events}
    pages = [{**p, "ev": by_id[p["id"]], "tm_link": _time_machine_link(p["start"], p["end"])}
             for p in hist["events"] if p["id"] in by_id]
    located = [{"id": p["id"], "name": p["ev"].name, "kind": p["ev"].kind, "start": p["start"], "end": p["end"]}
               for p in pages]
    env = _env()
    common = {
        "updated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "r": rationale,
        "names": {i.id: i.name for i in indicators},
        "units": {i.id: i.unit for i in indicators},
        "revised": {i.id: i.revised for i in indicators},
        "cat_of": {i.id: i.category for i in indicators},
        "regimes": {key: {"label": label, "description": desc} for key, label, desc in REGIMES.values()},
        "events_by_id": {p["id"]: p for p in pages},
        "glossary": glossary,
    }

    if site_dir.exists():
        shutil.rmtree(site_dir)
    shutil.copytree(HERE / "static", site_dir / "static")
    (site_dir / ".nojekyll").write_text("")

    def write(path: str, template: str, **ctx) -> None:
        out = site_dir / path
        out.parent.mkdir(parents=True, exist_ok=True)
        root = "../" * path.count("/")
        out.write_text(env.get_template(template).render(root=root, page=path, **common, **ctx))

    write("index.html", "index.html.j2", untracked=untracked,
          chart_data=score_chart(composite["overall"], OVERVIEW_YEARS, events=located))
    write("why.html", "why.html.j2",
          chart_data=score_chart(composite["overall"], None, weekly=True, events=located, label_events=False))
    write("learn.html", "learn.html.j2")
    for cat in CATEGORIES:
        members = [cards[i.id] for i in indicators if i.category == cat]
        write(f"category/{cat}.html", "category.html.j2", cat=cat, cards=members,
              chart_data=score_chart(composite[f"cat_{cat}"], INDICATOR_CHART_YEARS, events=located))
    tags = sorted({t for p in pages for t in p["ev"].tags})
    write("history/index.html", "history_index.html.j2", pages=pages, tags=tags)
    for p in pages:
        write(f"history/{p['id']}.html", "event.html.j2", p=p, indicators=indicators,
              chart_data={**p["chart"], "kind": "event"})
    write("history/which-factors-warned.html", "factors.html.j2", factors=hist["factors"],
          n_crashes=sum(p["ev"].kind == "crash" for p in pages))
    export_time_machine(site_dir / "data" / "tm", indicators,
                        [{"event": p["ev"], "start": pd.Timestamp(p["start"]), "end": pd.Timestamp(p["end"])}
                         for p in pages],
                        weights or {c: 1.0 for c in CATEGORIES}, tables, composite, regimes, market)
    write("time-machine.html", "time_machine.html.j2")
    return site_dir / "index.html"


def main() -> None:
    glossary = load_glossary()
    path = build_site(load_indicators(), load_untracked(), SCORES_DIR, SITE_DIR, datetime.now(timezone.utc),
                      load_events(glossary=glossary), glossary, load_weights())
    print(f"built {path}")


if __name__ == "__main__":
    main()
