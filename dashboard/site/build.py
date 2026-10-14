"""Step: render the static site from data/scores into site/."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from dashboard.config import CATEGORIES, Indicator, load_indicators
from dashboard.paths import SCORES_DIR, SITE_DIR
from dashboard.signals import AMBER, RED

HERE = Path(__file__).parent
LIGHT_LABELS = {"green": "Green", "amber": "Amber", "red": "Red", "none": "No score"}
TREND_LABELS = {"up": "↑ Risk rising", "down": "↓ Risk easing", "flat": "→ Steady"}


def format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    text = f"{value:,.2f}"
    return {"%": f"{text}%", "pp": f"{text} pp"}.get(unit, text)


def build_cards(indicators: list[Indicator], latest: dict) -> list[dict]:
    by_id = {row["id"]: row for row in latest["indicators"]}
    cards = []
    for ind in indicators:
        row = by_id.get(ind.id, {})
        light = row.get("light", "none")
        cards.append({
            "ind": ind,
            "category": CATEGORIES[ind.category],
            "value": format_value(row.get("value"), ind.unit),
            "score": row.get("score"),
            "light": light,
            "light_label": LIGHT_LABELS[light],
            "trend": TREND_LABELS[row.get("trend", "flat")],
            "rule_label": row.get("rule_label", ""),
            "last_observation": row.get("last_observation"),
            "history_since": row.get("history_since"),
            "stale": row.get("stale", True),
        })
    return cards


def build_site(indicators: list[Indicator], scores_dir: Path, site_dir: Path, now: datetime) -> Path:
    latest = json.loads((scores_dir / "latest.json").read_text())
    cards = build_cards(indicators, latest)
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
    html = env.get_template("index.html.j2").render(
        cards=cards,
        fresh=sum(not c["stale"] for c in cards),
        total=len(cards),
        amber=AMBER,
        red=RED,
        updated=now.strftime("%Y-%m-%d %H:%M UTC"),
    )
    if site_dir.exists():
        shutil.rmtree(site_dir)
    shutil.copytree(HERE / "static", site_dir / "static")
    (site_dir / "index.html").write_text(html)
    (site_dir / ".nojekyll").write_text("")
    return site_dir / "index.html"


def main() -> None:
    path = build_site(load_indicators(), SCORES_DIR, SITE_DIR, datetime.now(timezone.utc))
    print(f"built {path}")


if __name__ == "__main__":
    main()
