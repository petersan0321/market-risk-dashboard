"""Alerts: state, triggers, hysteresis, email composition, and sending (spec §11)."""

from datetime import date

import pandas as pd
import pytest

from dashboard.alerts import compose, related_headlines, run, send, this_week, triggers
from dashboard.config import CATEGORIES, CalendarType, NewsTopic

TODAY = date(2026, 10, 15)  # a Thursday
MONDAY = date(2026, 10, 19)


def state(band="Normal", score=55.0, cats=None, inds=None, rules=None, regime="reflation"):
    return {"as_of": "2026-10-15", "overall": {"score": score, "band": band},
            "categories": cats or {c: "green" for c in CATEGORIES},
            "indicators": inds or {"vix": "green", "baa_spread": "green"},
            "rules": rules or {"vix": []}, "regime": regime}


@pytest.fixture
def inds(make_indicator):
    return [make_indicator(id="vix", name="VIX", category="volatility", explain="Fear gauge."),
            make_indicator(id="baa_spread", name="Credit spread", category="credit", explain="Lender worry.")]


FLAT = pd.Series([55.0] * 10)


def test_first_run_never_alerts(inds):
    assert triggers(None, state(), FLAT, inds) == []


def test_all_trigger_kinds_in_priority_order(inds):
    prev = state()
    now = state(band="Elevated", score=62, cats={**{c: "green" for c in CATEGORIES}, "credit": "amber"},
                inds={"vix": "red", "baa_spread": "green"}, rules={"vix": ["VIX above 40"]}, regime="slowdown")
    kinds = [t["kind"] for t in triggers(prev, now, FLAT, inds)]
    assert kinds == ["band", "category", "indicator", "rule", "regime"]


def test_stale_indicator_does_not_trigger(inds):
    assert triggers(state(), state(inds={"vix": None, "baa_spread": "green"}), FLAT, inds) == []


def test_week_jump_without_band_change(inds):
    rising = pd.Series([50, 50, 50, 50, 50, 50, 52, 54, 57, 59, 61.0])
    t = triggers(state(score=61), state(score=61), rising, inds)
    assert [x["kind"] for x in t] == ["jump"]
    t = triggers(state(band="Normal"), state(band="Elevated", score=61), rising, inds)
    assert "jump" not in [x["kind"] for x in t]  # a band change already says it


def test_rule_cleared(inds):
    t = triggers(state(rules={"vix": ["VIX above 40"]}), state(rules={"vix": []}), FLAT, inds)
    assert t[0]["text"].startswith("Cleared") and t[0]["up"] is False


TOPICS = [NewsTopic("fed_rates", "Fed", ("fed",), "why", ("vix",)), NewsTopic("energy", "Energy", ("oil",), "why", ())]
NEWS = [{"title": "Fed moves", "url": "u1", "source": "S", "date": "2026-10-14", "topic": "fed_rates"},
        {"title": "Old Fed", "url": "u2", "source": "S", "date": "2026-09-01", "topic": "fed_rates"},
        {"title": "Oil up", "url": "u3", "source": "S", "date": "2026-10-14", "topic": "energy"}]


def test_related_headlines_follow_changed_indicators(inds):
    trig = [{"kind": "indicator", "id": "vix", "up": True, "text": "VIX"}]
    assert [h["url"] for h in related_headlines(trig, TOPICS, NEWS, TODAY, inds)] == ["u1"]
    cat_trig = [{"kind": "category", "id": "volatility", "up": True, "text": "Vol"}]
    assert [h["url"] for h in related_headlines(cat_trig, TOPICS, NEWS, TODAY, inds)] == ["u1"]


def test_this_week():
    types = {"fomc": CalendarType("fomc", "Fed decision", "manual", "why", "w", "r", ())}
    cal = {"items": [{"date": "2026-10-20", "type": "fomc", "note": ""}, {"date": "2026-11-20", "type": "fomc", "note": ""}]}
    assert [e["date"] for e in this_week(cal, types, MONDAY)] == ["2026-10-20"]


RESULT = {"overall": {"score": 62.0, "band": "Elevated"}, "outcomes": [{"band": "Elevated", "days": 10, "big_drop_rate": 0.5}],
          "changes": {"7d": {"from": 55.0, "to": 62.0, "change": 7.0}}, "thresholds": {"band_up": None},
          "contributions": [{"name": "Credit", "contribution": 4.0}], "confidence": {"fresh": 44, "total": 44},
          "model_confidence": {"auc": 0.68, "grade": "modest", "ci": [0.4, 0.6]},
          "looks_like": {"matches": [{"event": "gfc_2008", "name": "Global Financial Crisis", "offset": "3 months before"}],
                         "no_crash_rate": 0.7}}


def test_compose_digest_and_weekly(inds):
    trig = [{"kind": "band", "up": True, "text": "Risk score Normal → Elevated (62)"}]
    subject, html, text = compose(trig, state(band="Elevated", score=62), RESULT, {"news": [], "calendar": {}}, inds,
                                  {}, TOPICS, TODAY, "https://x.github.io/d/", weekly=False)
    assert subject == "[Market Watch] Risk score Normal → Elevated (62)"
    assert "Global Financial Crisis" in html and "Open the dashboard" in html and "Weekly summary" not in html
    assert "&amp;" not in text and "not financial advice" in text
    assert compose([], state(), RESULT, {}, inds, {}, TOPICS, TODAY, "u", weekly=False) is None
    subject, html, _ = compose([], state(), RESULT, {"calendar": {"items": [], "warnings": ["add dates"]}}, inds,
                               {}, TOPICS, MONDAY, "u", weekly=True)
    assert subject.startswith("[Market Watch] Weekly summary") and "Calendar needs updating" in html


def test_run_previews_without_secrets_and_saves_state(inds, tmp_path, monkeypatch):
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_TO"):
        monkeypatch.delenv(k, raising=False)
    now = state(band="Elevated", score=62)
    out = run(now, state(), RESULT, {"news": []}, FLAT, inds, {}, TOPICS, TODAY, tmp_path / "state.json", tmp_path / "out")
    assert out["status"].startswith("preview written") and (tmp_path / "out" / "email-preview.html").exists()
    assert (tmp_path / "state.json").exists()


def test_run_sends_when_configured(inds, tmp_path, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def starttls(self):
            sent.append("tls")
        def login(self, user, password):
            sent.append(("login", user))
        def send_message(self, msg):
            sent.append(msg["To"])

    send("S", "<p>h</p>", "t", "me@example.com", "pw", "you@example.com", smtp=FakeSMTP)
    assert sent == ["tls", ("login", "me@example.com"), "you@example.com"]
