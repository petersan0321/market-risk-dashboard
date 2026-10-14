"""Step: render the static site from data/scores into site/."""

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from dashboard.composite import BANDS
from dashboard.config import CATEGORIES, Indicator, Untracked, load_indicators, load_untracked
from dashboard.paths import SCORES_DIR, SITE_DIR
from dashboard.regime import REGIMES
from dashboard.signals import AMBER, RED, read_tables

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


def score_chart(series: pd.Series, years: int | None, weekly: bool = False) -> dict:
    """Score line with band shading; empty x/y when there is no data."""
    s = series.dropna()
    if years and not s.empty:
        s = s[s.index >= s.index[-1] - pd.DateOffset(years=years)]
    if weekly and not s.empty:
        s = s.resample("W-FRI").last().dropna()
    return {"x": [d.strftime("%Y-%m-%d") for d in s.index], "y": _clean(s.values),
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
            card["chart"] = value_chart(tables["value"][ind.id], detail, ind)
        cards[ind.id] = card
    return cards


def build_site(indicators: list[Indicator], untracked: list[Untracked], scores_dir: Path, site_dir: Path,
               now: datetime) -> Path:
    rationale = json.loads((scores_dir / "rationale.json").read_text())
    composite = pd.read_csv(scores_dir / "composite.csv", index_col="date", parse_dates=["date"])
    tables = read_tables(scores_dir)
    cards = _indicator_cards(indicators, rationale, tables)
    env = _env()
    common = {
        "updated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "r": rationale,
        "names": {i.id: i.name for i in indicators},
        "units": {i.id: i.unit for i in indicators},
        "regimes": {key: {"label": label, "description": desc} for key, label, desc in REGIMES.values()},
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
          chart_data=score_chart(composite["overall"], OVERVIEW_YEARS))
    write("why.html", "why.html.j2", chart_data=score_chart(composite["overall"], None, weekly=True))
    write("learn.html", "learn.html.j2")
    for cat in CATEGORIES:
        members = [cards[i.id] for i in indicators if i.category == cat]
        write(f"category/{cat}.html", "category.html.j2", cat=cat, cards=members,
              chart_data=score_chart(composite[f"cat_{cat}"], INDICATOR_CHART_YEARS))
    return site_dir / "index.html"


def main() -> None:
    path = build_site(load_indicators(), load_untracked(), SCORES_DIR, SITE_DIR, datetime.now(timezone.utc))
    print(f"built {path}")


if __name__ == "__main__":
    main()
