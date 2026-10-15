"""History playbook, events config, glossary, and time machine export."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest
import yaml

from dashboard.config import CATEGORIES, ConfigError, Event, load_events, load_glossary
from dashboard.history import (_runs, event_markers, locate, today_looks_like, warning,
                               which_factors_warned)

DAYS = pd.bdate_range("2000-01-03", "2010-12-31")


@pytest.fixture
def event():
    return Event(id="e", name="E", kind="crash", assets=("stocks",), start=date(2005, 3, 1), end=date(2005, 9, 1),
                 tags=("credit",), summary="S", background="B", chain=("a",), why_stocks="x", why_bonds="y",
                 lesson="l", terms=("leverage",))


# --- config ---------------------------------------------------------------------------

def test_real_events_and_glossary_load():
    glossary = load_glossary()
    events = load_events(glossary=glossary)
    assert len(events) == 25
    assert all(t in glossary for e in events for t in e.terms)
    assert all(e.chain and e.lesson and e.why_stocks and e.why_bonds for e in events)


def _write_events(tmp_path, change):
    raw = yaml.safe_load(open("config/events.yaml"))[0]
    for k, v in change.items():
        if k.startswith("explain."):
            raw["explain"][k.split(".")[1]] = v
        else:
            raw[k] = v
    path = tmp_path / "events.yaml"
    path.write_text(yaml.safe_dump([raw]))
    return path


@pytest.mark.parametrize("change, message", [
    ({"terms": ["not_a_term"]}, "not in glossary"),
    ({"kind": "boom"}, "unknown kind"),
    ({"tags": ["astrology"]}, "unknown tags"),
    ({"explain.lesson": ""}, "explain is missing"),
    ({"anchor": {"start": date(2020, 1, 2), "end": date(2020, 1, 1)}}, "start < end"),
])
def test_bad_events_rejected(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_events(_write_events(tmp_path, change))


# --- locating events --------------------------------------------------------------------

def test_locate_finds_peak_and_trough_near_anchors(event):
    prices = pd.Series(100.0, index=DAYS)
    prices["2005-03-10"] = 120  # peak within 30 days of the start anchor
    prices["2005-08-19"] = 60   # trough within 30 days of the end anchor
    prices["2004-01-05"] = 200  # far outside the search window
    found = locate(event, prices, prices)
    assert found["start"] == pd.Timestamp("2005-03-10") and found["end"] == pd.Timestamp("2005-08-19")
    assert found["stocks_move"] == pytest.approx(-0.5)


def test_locate_uses_bonds_for_bond_events_and_respects_search(event):
    stocks = pd.Series(100.0, index=DAYS)
    bonds = pd.Series(100.0, index=DAYS)
    bonds["2005-03-08"] = 130
    bonds["2005-02-01"] = 150  # 28 days before the anchor
    narrow = replace(event, assets=("bonds",), search_days=10)
    assert locate(narrow, stocks, bonds)["start"] == pd.Timestamp("2005-03-08")
    wide = replace(event, assets=("bonds",))
    assert locate(wide, stocks, bonds)["start"] == pd.Timestamp("2005-02-01")


# --- warnings and factors -------------------------------------------------------------------

def test_warning_lead_time():
    overall = pd.Series(50.0, index=DAYS)
    start = pd.Timestamp("2005-06-01")
    overall[start - pd.Timedelta(days=60):start] = 65
    w = warning(overall, start)
    assert w["warned"] and 35 <= w["lead_days"] <= 45 and not w["already_elevated"]


def test_short_blip_is_not_a_warning():
    overall = pd.Series(50.0, index=DAYS)
    overall["2005-05-02":"2005-05-06"] = 70  # only 5 days
    assert warning(overall, pd.Timestamp("2005-06-01"))["warned"] is False


def test_runs():
    mask = pd.Series([True, True, False, True, True, True], index=pd.bdate_range("2020-01-01", periods=6))
    assert _runs(mask, 3) == [pd.Timestamp("2020-01-06")]


def test_hit_rate_and_false_alarms(make_indicator, event):
    ind = make_indicator(id="x")
    scores = pd.DataFrame({"x": 50.0}, index=DAYS)
    located = [{"event": event, "start": pd.Timestamp("2005-03-01"), "end": pd.Timestamp("2005-09-01")}]
    scores.loc["2004-10-01":"2004-12-31", "x"] = 90  # red before the crash: a hit, not a false alarm
    scores.loc["2008-01-01":"2008-03-31", "x"] = 90  # red with no crash after: a false alarm
    [row] = which_factors_warned(located, scores, [ind], pd.Timestamp("2010-12-31"))
    assert (row["hits"], row["events"]) == (1, 1)
    assert (row["red_episodes"], row["false_alarms"]) == (2, 1)


def test_today_looks_like_and_base_rate(event):
    rng = np.random.default_rng(1)
    comp = pd.DataFrame({f"cat_{c}": 50 + rng.normal(0, 5, len(DAYS)) for c in CATEGORIES}, index=DAYS)
    comp.iloc[-1] = comp.loc["2004-11-30"]  # today identical to 3 months before the event
    located = [{"event": event, "start": pd.Timestamp("2005-03-01"), "end": pd.Timestamp("2005-09-01")}]
    out = today_looks_like(comp, located, DAYS[-1])
    assert out["matches"][0]["event"] == "e" and out["matches"][0]["offset"] == "3 months before"
    assert out["similar_days"] >= 1 and 0 <= out["no_crash_rate"] <= 1


def test_event_markers_pick_risky_extreme(make_indicator, event):
    values = pd.Series(1.0, index=DAYS)
    values["2005-05-02"] = 9.0
    located = [{"event": event, "start": pd.Timestamp("2005-03-01"), "end": pd.Timestamp("2005-09-01")}]
    assert event_markers(make_indicator(risk_when="high"), values, located)[0]["value"] == 9.0
    values["2005-05-03"] = -5.0
    assert event_markers(make_indicator(risk_when="low"), values, located)[0]["value"] == -5.0
