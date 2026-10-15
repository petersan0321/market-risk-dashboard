"""Step 8: context around the score (spec §10) — upcoming calendar, news headlines by topic, and
presidential-cycle statistics. None of this affects the score, and failures here never stop a run."""

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from dashboard.config import CalendarDate, CalendarType, NewsTopic

log = logging.getLogger(__name__)

TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (market-risk-dashboard)"}
FRED_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
TREASURY_UPCOMING_URL = "https://www.treasurydirect.gov/TA_WS/securities/upcoming"
CALENDAR_DAYS = 120
RECURRING_MIN_DAYS = 60
NEWS_RETENTION_DAYS = 90
PER_TOPIC = 5
AUCTION_TERMS = ("10-Year", "9-Year 1", "30-Year", "29-Year")


# --- calendar ---------------------------------------------------------------------------------

def parse_fred_release_dates(payload: dict) -> list[date]:
    return [date.fromisoformat(r["date"]) for r in payload.get("release_dates", [])]


def parse_treasury_upcoming(auctions: list[dict]) -> list[dict]:
    out = []
    for a in auctions:
        term = a.get("securityTerm", "")
        if a.get("securityType") in ("Note", "Bond") and term.startswith(AUCTION_TERMS):
            label = "10-year" if term.startswith(("10-Year", "9-Year")) else "30-year"
            reopen = " (reopening)" if a.get("reopening") == "Yes" else ""
            out.append({"date": date.fromisoformat(a["auctionDate"][:10]), "note": f"{label} auction{reopen}"})
    return out


def fetch_fred_dates(release_id: int, today: date, api_key: str, session=requests) -> list[date]:
    resp = session.get(FRED_DATES_URL, params={
        "release_id": release_id, "api_key": api_key, "file_type": "json",
        "include_release_dates_with_no_data": "true", "realtime_start": today.isoformat(),
        "realtime_end": (today + timedelta(days=CALENDAR_DAYS)).isoformat(), "sort_order": "asc"}, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_fred_release_dates(resp.json())


def build_calendar(types: dict[str, CalendarType], manual: list[CalendarDate], today: date,
                   fred_dates=None, auctions=None) -> dict:
    """fred_dates: {type: [dates]} or None when unavailable; auctions: [{date, note}] or None."""
    horizon = today + timedelta(days=CALENDAR_DAYS)
    items = [{"date": d.date, "type": d.type, "note": d.note} for d in manual if today <= d.date <= horizon]
    unavailable = []
    for t in types.values():
        if t.source == "fred":
            if fred_dates is None or t.type not in fred_dates:
                unavailable.append(t.type)
            else:
                items += [{"date": d, "type": t.type, "note": ""} for d in fred_dates[t.type] if today <= d <= horizon]
        elif t.source == "treasury":
            if auctions is None:
                unavailable.append(t.type)
            else:
                items += [{"date": a["date"], "type": t.type, "note": a["note"]} for a in auctions if today <= a["date"] <= horizon]
    warnings = []
    for t in types.values():
        if t.recurring and t.source == "manual":
            last = max((d.date for d in manual if d.type == t.type), default=None)
            if last is None or (last - today).days < RECURRING_MIN_DAYS:
                warnings.append(f"{t.name}: dates are only listed until {last or 'nothing'}; add the next ones to config/calendar.yaml.")
    items.sort(key=lambda i: (i["date"], i["type"]))
    return {"items": [{**i, "date": i["date"].isoformat()} for i in items],
            "unavailable": unavailable, "warnings": warnings, "as_of": today.isoformat()}


# --- news ---------------------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def matches(title: str, keywords: tuple[str, ...]) -> bool:
    text = f" {normalize_title(title)} "
    return any(f" {k} " in text for k in keywords)


def tag(title: str, topics: list[NewsTopic]) -> str | None:
    """The first topic whose keywords appear in the headline."""
    return next((t.id for t in topics if matches(title, t.keywords)), None)


def parse_rss(content: bytes, source: str) -> list[dict]:
    """Items from an RSS feed (bytes, so the XML's own encoding declaration is respected)."""
    root = ET.fromstring(content)
    out = []
    for item in root.iter("item"):
        title, link, pub = (item.findtext(k) or "" for k in ("title", "link", "pubDate"))
        try:
            when = parsedate_to_datetime(pub.strip()).date().isoformat()
        except (TypeError, ValueError):
            continue
        if title.strip() and link.strip():
            out.append({"title": " ".join(title.split()), "url": link.strip(), "source": source, "date": when})
    return out


def select_headlines(candidates: list[dict], topics: list[NewsTopic]) -> list[dict]:
    """Tag by topic, drop untagged and duplicate headlines, and cap each topic at PER_TOPIC per day."""
    seen_urls, seen_titles, counts, out = set(), set(), {}, []
    for h in candidates:
        key = normalize_title(h["title"])
        if not key or h["url"] in seen_urls or key in seen_titles:
            continue
        topic = tag(h["title"], topics)
        if topic is None or counts.get((topic, h["date"]), 0) >= PER_TOPIC:
            continue
        seen_urls.add(h["url"])
        seen_titles.add(key)
        counts[(topic, h["date"])] = counts.get((topic, h["date"]), 0) + 1
        out.append({**h, "topic": topic})
    return out


def fetch_news(feeds: list[dict], session=requests) -> list[dict]:
    candidates = []
    for feed in feeds:
        try:
            resp = session.get(feed["url"], headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            excluded = [e.lower() for e in feed.get("exclude", [])]
            candidates += [h for h in parse_rss(resp.content, feed["name"])
                           if not any(e in h["title"].lower() for e in excluded)]
        except Exception as exc:  # noqa: BLE001
            log.warning("news feed %s unavailable: %s", feed["name"], exc)
    return candidates


def save_news(headlines: list[dict], news_dir: Path, today: date) -> None:
    """Store headlines by the day they were published, merging with what's already saved,
    and delete files older than the retention period."""
    news_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for h in headlines:
        by_day.setdefault(h["date"], []).append(h)
    for day, items in by_day.items():
        path = news_dir / f"{day}.json"
        merged = (json.loads(path.read_text()) if path.exists() else []) + items
        kept, urls, titles, counts = [], set(), set(), {}
        for h in merged:  # existing headlines first, so earlier picks are stable
            key = normalize_title(h["title"])
            if h["url"] in urls or key in titles or counts.get(h["topic"], 0) >= PER_TOPIC:
                continue
            urls.add(h["url"])
            titles.add(key)
            counts[h["topic"]] = counts.get(h["topic"], 0) + 1
            kept.append(h)
        path.write_text(json.dumps(kept, indent=1))
    cutoff = today - timedelta(days=NEWS_RETENTION_DAYS)
    for path in news_dir.glob("*.json"):
        if date.fromisoformat(path.stem) < cutoff:
            path.unlink()


def recent_news(news_dir: Path, today: date, days: int = 3) -> list[dict]:
    out = []
    for k in range(days):
        path = news_dir / f"{(today - timedelta(days=k)).isoformat()}.json"
        if path.exists():
            out += json.loads(path.read_text())
    return sorted(out, key=lambda h: h["date"], reverse=True)


# --- presidential cycle (§10.2) -------------------------------------------------------------------

CYCLE_NAMES = {1: "Year after the election", 2: "Midterm year", 3: "Pre-election year", 0: "Election year"}


def presidential_cycle(stocks: pd.Series, bonds: pd.Series, today: date, since: int = 1950) -> list[dict]:
    """Median calendar-year return by year of the 4-year presidential cycle (complete years only)."""
    def yearly(prices: pd.Series) -> pd.Series:
        ends = prices.dropna().groupby(prices.dropna().index.year).last()
        return (ends / ends.shift(1) - 1).dropna()

    s, b = yearly(stocks), yearly(bonds)
    rows = []
    for phase in (1, 2, 3, 0):
        years = [y for y in s.index if y >= since and y < today.year and y % 4 == phase]
        b_years = [y for y in years if y in b.index]
        rows.append({
            "phase": phase, "name": CYCLE_NAMES[phase], "years": len(years),
            "stocks_median": float(s.loc[years].median()) if years else None,
            "stocks_down_years": int((s.loc[years] < 0).sum()) if years else 0,
            "bonds_median": float(b.loc[b_years].median()) if b_years else None,
            "bond_years": len(b_years),
            "current": today.year % 4 == phase,
        })
    return rows


# --- run ----------------------------------------------------------------------------------------------

def run(types, manual, feeds, topics, context_dir: Path, today: date, stocks: pd.Series, bonds: pd.Series,
        fetch: bool = True) -> dict:
    context_dir.mkdir(parents=True, exist_ok=True)
    fred_dates, auctions = None, None
    if fetch:
        key = os.environ.get("FRED_API_KEY")
        if key:
            fred_dates = {}
            for t in types.values():
                if t.source == "fred":
                    try:
                        fred_dates[t.type] = fetch_fred_dates(t.fred_release_id, today, key)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("release dates for %s unavailable: %s", t.type, exc)
        try:
            resp = requests.get(TREASURY_UPCOMING_URL, params={"format": "json"}, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            auctions = parse_treasury_upcoming(resp.json())
        except Exception as exc:  # noqa: BLE001
            log.warning("upcoming auctions unavailable: %s", exc)
        try:
            save_news(select_headlines(fetch_news(feeds), topics), context_dir / "news", today)
        except Exception as exc:  # noqa: BLE001
            log.warning("news unavailable: %s", exc)
        calendar = build_calendar(types, manual, today, fred_dates, auctions)
        (context_dir / "calendar.json").write_text(json.dumps(calendar, indent=1))
    else:
        path = context_dir / "calendar.json"
        calendar = json.loads(path.read_text()) if path.exists() else build_calendar(types, manual, today)
    result = {
        "calendar": calendar,
        "news": recent_news(context_dir / "news", today),
        "cycle": presidential_cycle(stocks, bonds, today),
    }
    (context_dir / "context.json").write_text(json.dumps(result, indent=1))
    return result
