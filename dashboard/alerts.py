"""Step 9: alerts (spec §11). Compare today's state with the last run's, then email a digest of
what changed, plus a weekly summary every Monday. Without email secrets, or with --dry-run,
the email is written to out/ instead of being sent."""

import json
import logging
import os
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from dashboard.composite import lights_with_hysteresis, with_hysteresis
from dashboard.config import CATEGORIES, CalendarType, Indicator, NewsTopic

log = logging.getLogger(__name__)

JUMP_POINTS = 10
JUMP_DAYS = 5
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587
TEMPLATES = Path(__file__).parent / "site" / "templates"
PRIORITY = {"band": 1, "category": 2, "indicator": 3, "rule": 4, "regime": 5, "jump": 6}
BAND_ORDER = ["Calm", "Normal", "Elevated", "High"]
LIGHT_ORDER = ["green", "amber", "red"]


# --- state -------------------------------------------------------------------------------

def current_state(composite: pd.DataFrame, tables: dict[str, pd.DataFrame], regimes: pd.Series,
                  indicators: list[Indicator]) -> dict:
    """Today's hysteresis-adjusted band and lights, rule labels, and regime."""
    valid = composite.dropna(subset=["overall"])
    last = tables["score"].index[-1]
    indicator_lights = {}
    for ind in indicators:
        if ind.id not in tables["score"]:
            continue
        stale = bool(tables["stale"].at[last, ind.id])
        lights = lights_with_hysteresis(tables["score"][ind.id])
        indicator_lights[ind.id] = None if stale or lights.empty else lights.iloc[-1]
    return {
        "as_of": last.date().isoformat(),
        "overall": {"score": float(valid["overall"].iloc[-1]),
                    "band": with_hysteresis(valid["overall"]).iloc[-1]},
        "categories": {c: lights_with_hysteresis(composite[f"cat_{c}"]).iloc[-1] for c in CATEGORIES},
        "indicators": indicator_lights,
        "rules": {i.id: [r for r in tables["rule_label"].at[last, i.id].split("; ") if r]
                  for i in indicators if i.rules and i.id in tables["rule_label"]},
        "regime": regimes.dropna().iloc[-1] if regimes.notna().any() else None,
    }


def load_state(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


# --- triggers (§11.1) ----------------------------------------------------------------------

def triggers(prev: dict | None, now: dict, overall: pd.Series, indicators: list[Indicator]) -> list[dict]:
    """Changes since the previous run, most important first. No previous state: nothing to compare."""
    if not prev:
        return []
    names = {i.id: i.name for i in indicators}
    out = []
    b0, b1 = prev["overall"]["band"], now["overall"]["band"]
    if b0 and b1 and b0 != b1:
        up = BAND_ORDER.index(b1) > BAND_ORDER.index(b0)
        out.append({"kind": "band", "up": up, "text": f"Risk score {b0} → {b1} ({now['overall']['score']:.0f})"})
    for c, light in now["categories"].items():
        old = prev["categories"].get(c)
        if old and light and old != light:
            out.append({"kind": "category", "id": c, "up": LIGHT_ORDER.index(light) > LIGHT_ORDER.index(old),
                        "text": f"{CATEGORIES[c]}: {old} → {light}"})
    for i_id, light in now["indicators"].items():
        old = prev["indicators"].get(i_id)
        if old and light and old != light:  # stale or new indicators (None) never trigger
            out.append({"kind": "indicator", "id": i_id, "up": LIGHT_ORDER.index(light) > LIGHT_ORDER.index(old),
                        "text": f"{names.get(i_id, i_id)}: {old} → {light}"})
    for i_id, labels in now["rules"].items():
        before = set(prev.get("rules", {}).get(i_id, []))
        for label in sorted(set(labels) - before):
            out.append({"kind": "rule", "id": i_id, "up": True, "text": f"New: {label} ({names.get(i_id, i_id)})"})
        for label in sorted(before - set(labels)):
            out.append({"kind": "rule", "id": i_id, "up": False, "text": f"Cleared: {label} ({names.get(i_id, i_id)})"})
    if prev.get("regime") and now["regime"] and prev["regime"] != now["regime"]:
        out.append({"kind": "regime", "up": None, "text": f"Economic regime: {prev['regime']} → {now['regime']}"})
    history = overall.dropna()
    if len(history) > JUMP_DAYS and not any(t["kind"] == "band" for t in out):
        then, today = history.iloc[-1 - JUMP_DAYS], history.iloc[-1]
        if today - then >= JUMP_POINTS:
            out.append({"kind": "jump", "up": True,
                        "text": f"Risk score up {today - then:.0f} points in a week ({then:.0f} → {today:.0f})"})
    return sorted(out, key=lambda t: PRIORITY[t["kind"]])


# --- email --------------------------------------------------------------------------------------

def related_headlines(trigs: list[dict], topics: list[NewsTopic], news: list[dict], today: date,
                      indicators: list[Indicator], limit: int = 5) -> list[dict]:
    """Recent headlines from topics linked to the indicators (or categories) that changed."""
    moved = {t["id"] for t in trigs if t["kind"] in ("indicator", "rule")}
    changed_cats = {t["id"] for t in trigs if t["kind"] == "category"}
    moved |= {i.id for i in indicators if i.category in changed_cats}
    wanted = {tp.id for tp in topics if moved & set(tp.related_indicators)}
    week_ago = (today - timedelta(days=7)).isoformat()
    picked = [h for h in news if h["topic"] in wanted and h["date"] >= week_ago]
    return picked[:limit]


def this_week(calendar: dict, types: dict[str, CalendarType], today: date) -> list[dict]:
    end = (today + timedelta(days=7)).isoformat()
    return [{**i, "name": types[i["type"]].name, "why": types[i["type"]].why_it_matters}
            for i in calendar.get("items", []) if today.isoformat() <= i["date"] <= end and i["type"] in types]


def compose(trigs: list[dict], state: dict, result: dict, ctx: dict, indicators: list[Indicator],
            types: dict[str, CalendarType], topics: list[NewsTopic], today: date, url: str,
            weekly: bool) -> tuple[str, str, str] | None:
    """(subject, html, text), or None when there is nothing to send."""
    if not trigs and not weekly:
        return None
    by_id = {i.id: i for i in indicators}
    o = result["overall"]
    if trigs:
        subject = f"[Market Watch] {trigs[0]['text']}"
    else:
        subject = f"[Market Watch] Weekly summary: {o['score']:.0f} ({state['overall']['band']})"
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=lambda name: bool(name) and name.endswith(".html.j2"))
    env.filters["pct"] = lambda v, signed=False: "—" if v is None else (f"{v * 100:+.1f}%" if signed else f"{v * 100:.0f}%")
    band_row = next((r for r in result["outcomes"] if r["band"] == state["overall"]["band"]), None)
    week = result["changes"].get("7d")
    up = result["thresholds"].get("band_up")
    context = {
        "subject": subject, "trigs": trigs, "state": state, "r": result, "by_id": by_id, "url": url.rstrip("/"),
        "band_row": band_row, "week": week, "up": up, "weekly": weekly, "today": today.isoformat(),
        "categories": CATEGORIES,
        "headlines": related_headlines(trigs, topics, ctx.get("news", []), today, indicators),
        "this_week": this_week(ctx.get("calendar", {}), types, today) if weekly else [],
        "calendar_warnings": ctx.get("calendar", {}).get("warnings", []) if weekly else [],
        "looks": (result.get("looks_like") or {}),
    }
    html = env.get_template("email.html.j2").render(**context)
    text = env.get_template("email.txt.j2").render(**context)
    return subject, html, text


def send(subject: str, html: str, text: str, user: str, password: str, to: str, smtp=smtplib.SMTP) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtp(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)


def dashboard_url() -> str:
    if os.environ.get("DASHBOARD_URL"):
        return os.environ["DASHBOARD_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "site/index.html"


def run(state: dict, prev: dict | None, result: dict, ctx: dict, overall: pd.Series, indicators: list[Indicator],
        types, topics, today: date, state_path: Path, out_dir: Path, dry_run: bool = False) -> dict:
    """Detect changes, send (or preview) the email, then save the new state."""
    trigs = triggers(prev, state, overall, indicators)
    message = compose(trigs, state, result, ctx, indicators, types, topics, today, dashboard_url(),
                      weekly=today.weekday() == 0)
    status = "nothing to send"
    if message:
        subject, html, text = message
        user, password, to = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_TO"))
        if dry_run or not (user and password and to):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "email-preview.html").write_text(html)
            (out_dir / "email-preview.txt").write_text(f"Subject: {subject}\n\n{text}")
            status = "preview written" + ("" if dry_run else " (email secrets not set)")
        else:
            send(subject, html, text, user, password, to)
            status = "sent"
        log.info("email: %s — %s", status, subject)
    state_path.write_text(json.dumps(state, indent=1))
    return {"triggers": trigs, "status": status}
