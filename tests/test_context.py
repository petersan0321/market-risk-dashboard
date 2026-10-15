"""Calendar, news, and presidential-cycle context (spec §10)."""

import json
from datetime import date, timedelta

import pandas as pd
import pytest
import yaml

from dashboard.config import CalendarDate, CalendarType, ConfigError, NewsTopic, load_calendar, load_news_topics
from dashboard.context import (build_calendar, matches, parse_fred_release_dates, parse_rss, parse_treasury_upcoming,
                               presidential_cycle, recent_news, save_news, select_headlines, tag)

TODAY = date(2026, 10, 15)


def ctype(type_, source="manual", recurring=False):
    return CalendarType(type_, type_.upper(), source, "why", "watch", "reaction", (), recurring,
                        10 if source == "fred" else None)


TOPICS = [NewsTopic("fed_rates", "Fed", ("federal reserve", "fed", "rate cut"), "why", ()),
          NewsTopic("inflation", "Inflation", ("inflation", "cpi"), "why", ())]


def test_real_calendar_and_topics_load():
    types, dates = load_calendar()
    assert "fomc" in types and any(d.type == "fomc" for d in dates)
    feeds, topics = load_news_topics()
    assert feeds and all(f["url"].startswith("https://") for f in feeds) and len(topics) == 10


def test_calendar_rejects_unknown_indicator(tmp_path):
    raw = yaml.safe_load(open("config/calendar.yaml"))
    raw["types"][0]["related_indicators"] = ["nope"]
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="related_indicators"):
        load_calendar(path)


def test_build_calendar_merges_sources_and_flags_gaps():
    types = {"fomc": ctype("fomc", recurring=True), "cpi": ctype("cpi", "fred"),
             "treasury_auction": ctype("treasury_auction", "treasury")}
    manual = [CalendarDate("fomc", date(2026, 10, 28)), CalendarDate("fomc", date(2026, 9, 1))]
    cal = build_calendar(types, manual, TODAY, fred_dates=None,
                         auctions=[{"date": date(2026, 10, 20), "note": "10-year auction"}])
    assert [i["date"] for i in cal["items"]] == ["2026-10-20", "2026-10-28"]  # past dates dropped
    assert cal["unavailable"] == ["cpi"]
    assert len(cal["warnings"]) == 1  # FOMC dates run out within 60 days
    cal = build_calendar(types, manual + [CalendarDate("fomc", date(2027, 3, 1))], TODAY,
                         fred_dates={"cpi": [date(2026, 11, 12)]}, auctions=[])
    assert cal["unavailable"] == [] and cal["warnings"] == []
    assert any(i["type"] == "cpi" for i in cal["items"])


def test_parsers():
    assert parse_fred_release_dates({"release_dates": [{"release_id": 10, "date": "2026-11-12"}]}) == [date(2026, 11, 12)]
    auctions = parse_treasury_upcoming([
        {"securityType": "Note", "securityTerm": "10-Year", "auctionDate": "2026-11-10T00:00:00", "reopening": "No"},
        {"securityType": "Bond", "securityTerm": "29-Year 11-Month", "auctionDate": "2026-11-12T00:00:00", "reopening": "Yes"},
        {"securityType": "Bill", "securityTerm": "4-Week", "auctionDate": "2026-10-20T00:00:00"},
    ])
    assert [a["note"] for a in auctions] == ["10-year auction", "30-year auction (reopening)"]


def test_parse_rss_handles_byte_order_mark():
    xml = ("﻿<?xml version='1.0' encoding='utf-8'?><rss><channel>"
           "<item><title>Fed holds rates</title><link>https://x/1</link><pubDate>Thu, 15 Oct 2026 14:43:21 GMT</pubDate></item>"
           "<item><title>No date</title><link>https://x/2</link></item></channel></rss>").encode("utf-8")
    items = parse_rss(xml, "Test")
    assert items == [{"title": "Fed holds rates", "url": "https://x/1", "source": "Test", "date": "2026-10-15"}]


def test_keyword_matching_uses_whole_words():
    assert matches("Fed signals a rate cut", ("fed",))
    assert not matches("Fedex shares fall", ("fed",))
    assert tag("Inflation surprises as the Fed waits", TOPICS) == "fed_rates"  # first matching topic wins
    assert tag("Sports news", TOPICS) is None


def test_select_headlines_dedupes_and_caps():
    def h(title, url, day="2026-10-15"):
        return {"title": title, "url": url, "source": "S", "date": day}
    cands = [h("Fed cuts rates", "u1"), h("Fed cuts rates!", "u2"), h("Unrelated story", "u3")]
    cands += [h(f"Inflation story {k}", f"i{k}") for k in range(8)]
    out = select_headlines(cands, TOPICS)
    assert [x["url"] for x in out if x["topic"] == "fed_rates"] == ["u1"]
    assert sum(x["topic"] == "inflation" for x in out) == 5


def test_save_news_merges_caps_and_expires(tmp_path):
    old_day = (TODAY - timedelta(days=100)).isoformat()
    (tmp_path / f"{old_day}.json").write_text("[]")
    first = [{"title": f"Inflation {k}", "url": f"a{k}", "source": "S", "date": "2026-10-15", "topic": "inflation"} for k in range(3)]
    second = [{"title": f"Inflation {k}", "url": f"b{k}", "source": "S", "date": "2026-10-15", "topic": "inflation"} for k in range(2, 8)]
    save_news(first, tmp_path, TODAY)
    save_news(second, tmp_path, TODAY)
    saved = json.loads((tmp_path / "2026-10-15.json").read_text())
    assert len(saved) == 5 and saved[0]["url"] == "a0"  # earlier picks kept; same title not repeated
    assert not (tmp_path / f"{old_day}.json").exists()
    assert len(recent_news(tmp_path, TODAY)) == 5


def test_presidential_cycle_groups_years():
    years = pd.date_range("1949-12-30", "2025-12-31", freq="YE")
    prices = pd.Series([100 * 1.1 ** k if y.year % 4 != 2 else 100 * 1.1 ** (k - 1) * 0.9
                        for k, y in enumerate(years)], index=years)
    rows = {r["phase"]: r for r in presidential_cycle(prices, prices, TODAY)}
    assert rows[2]["name"] == "Midterm year" and rows[2]["current"]  # 2026 is a midterm year
    assert rows[1]["years"] == 19 and rows[0]["years"] == 19
    assert rows[3]["stocks_median"] > rows[2]["stocks_median"]
