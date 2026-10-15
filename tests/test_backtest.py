"""Model report card and simulation."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from dashboard.backtest import (auc, band_rates, block_indices, bootstrap, event_hits, false_alarms, grade,
                                outcome_labels, rises, summary, wilson)
from dashboard.composite import with_hysteresis
from dashboard.config import Event
from dashboard.simulate import SimConfig, run_portfolio, simulate, stats

DAYS = pd.bdate_range("1995-01-02", "2010-12-31")


def test_outcome_labels_unknown_at_the_end():
    prices = pd.Series(np.r_[np.full(300, 100.0), np.full(300, 80.0)], index=DAYS[:600])
    labels = outcome_labels(prices, prices)
    assert labels["stocks"].iloc[100] == 1.0  # a 20% fall within the next 252 days
    assert labels["stocks"].iloc[0] == 0.0    # the fall is 300 days away: outside the window
    assert labels["stocks"].iloc[-252:].isna().all()


def test_auc_known_values():
    y = np.array([0, 0, 1, 1], dtype=float)
    assert auc(np.array([1.0, 2, 3, 4]), y) == 1.0
    assert auc(np.array([4.0, 3, 2, 1]), y) == 0.0
    assert auc(np.array([5.0, 5, 5, 5]), y) == 0.5
    assert auc(np.array([1.0, 2, np.nan, 4]), np.array([0, 1, 1, np.nan])) == 1.0
    assert auc(np.array([1.0, 2]), np.array([0.0, 0.0])) is None


def test_auc_matches_brute_force():
    rng = np.random.default_rng(0)
    s = np.round(rng.uniform(0, 100, 400), 1)
    y = (rng.uniform(size=400) < s / 100).astype(float)
    pos, neg = s[y == 1], s[y == 0]
    brute = np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg])
    assert auc(s, y) == pytest.approx(brute)


@pytest.mark.parametrize("value, expected", [(0.85, "strong"), (0.72, "useful"), (0.65, "modest"), (0.5, "weak")])
def test_grade(value, expected):
    assert grade(value) == expected


def test_band_rates_and_rises():
    bands = np.array(["Calm", "Calm", "Normal", "High", "High"], dtype=object)
    labels = np.array([0, 0, 1, 1, np.nan])
    rates = band_rates(bands, labels)
    assert rates == {"Calm": 0.0, "Normal": 1.0, "Elevated": None, "High": 1.0}
    assert not rises(rates)
    assert rises({"Calm": 0.1, "Normal": 0.2, "Elevated": None, "High": 0.5})


def test_wilson_interval():
    lo, hi = wilson(2, 15)
    assert 0 < lo < 2 / 15 < hi < 0.5
    assert wilson(0, 0) is None


def test_block_bootstrap_is_seeded_and_keeps_blocks():
    rng = np.random.default_rng(1)
    idx = block_indices(1000, rng, block=100)
    assert len(idx) == 1000 and np.all(np.diff(idx[:100]) == 1)
    a = bootstrap(500, lambda i: {"m": float(i.mean())}, reps=50, seed=3)
    b = bootstrap(500, lambda i: {"m": float(i.mean())}, reps=50, seed=3)
    assert a == b and a["m"][0] < a["m"][1]


def _event(start, end):
    ev = Event(id="c", name="C", kind="crash", assets=("stocks",), start=start, end=end, tags=("credit",),
               summary="s", background="b", chain=("x",), why_stocks="w", why_bonds="w", lesson="l", terms=())
    return {"event": ev, "start": pd.Timestamp(start), "end": pd.Timestamp(end)}


def test_event_hits_and_threshold():
    overall = pd.Series(50.0, index=DAYS)
    overall["2004-11-01":"2005-02-28"] = 62
    located = [_event(date(2005, 3, 1), date(2005, 9, 1)), _event(date(2008, 3, 3), date(2008, 9, 1))]
    out = event_hits(overall, located)
    assert (out["hits"], out["events"]) == (1, 2)
    assert event_hits(overall, located, threshold=65)["hits"] == 0


def test_false_alarms():
    overall = pd.Series(50.0, index=DAYS)
    bad = pd.Series(0.0, index=DAYS)
    overall["2000-01-03":"2000-03-31"] = 80   # High episode with a drop after: not false
    bad["2000-01-03":"2000-03-31"] = 1.0
    overall["2004-01-01":"2004-03-31"] = 80   # High episode, no drop: false alarm
    out = false_alarms(overall, bad)
    assert (out["episodes"], out["false"]) == (2, 1)


def test_hysteresis_stops_flip_flopping():
    scores = pd.Series([50, 61, 58, 59, 61, 54, 30, 76, 72, 69])
    assert with_hysteresis(scores).tolist() == ["Normal", "Elevated", "Elevated", "Elevated", "Elevated",
                                                "Normal", "Calm", "High", "High", "Elevated"]


def test_hysteresis_has_no_look_ahead():
    rng = np.random.default_rng(5)
    s = pd.Series(rng.uniform(0, 100, 300))
    assert with_hysteresis(s[:150]).tolist() == with_hysteresis(s).tolist()[:150]


# --- simulation -------------------------------------------------------------------------------

def test_portfolio_rebalances_next_day_with_costs():
    days = pd.bdate_range("2024-01-29", periods=4)  # Mon 29 .. Thu Feb 1; Jan 31 is month-end
    s_ret = pd.Series([0.0, 0.0, 0.0, 0.10], index=days)
    b_ret = pd.Series(0.0, index=days)
    targets = pd.Series([1.0, 1.0, 0.0, 0.0], index=days)  # decided at the Jan 31 month-end
    out = run_portfolio(s_ret, b_ret, targets, cost=0.01)
    # Feb 1: moved fully to bonds (cost 1% of 100% traded), so the 10% stock gain is missed
    assert out["value"].iloc[-1] == pytest.approx(10_000 * 0.99)
    assert out["stock_weight"].iloc[-1] == pytest.approx(0.0)


def test_signal_cannot_see_the_future():
    days = pd.bdate_range("2024-01-02", periods=60)
    rng = np.random.default_rng(2)
    s_ret = pd.Series(rng.normal(0, 0.01, 60), index=days)
    b_ret = pd.Series(0.0, index=days)
    targets = pd.Series(np.where(rng.uniform(size=60) < 0.5, 1.0, 0.0), index=days)
    base = run_portfolio(s_ret, b_ret, targets, 0.0)["value"]
    later = s_ret.copy()
    later.iloc[40:] = rng.normal(0, 0.05, 20)  # change only the future
    changed = run_portfolio(later, b_ret, targets, 0.0)["value"]
    pd.testing.assert_series_equal(base.iloc[:40], changed.iloc[:40])


def test_stats_and_simulate():
    values = pd.Series([100.0, 110, 88, 99, 121], index=pd.bdate_range("2024-01-01", periods=5))
    st = stats(values)
    assert st["max_drawdown"] == pytest.approx(0.2) and st["longest_recovery_days"] == 2

    prices = pd.Series(np.exp(np.linspace(0, 1, len(DAYS))), index=DAYS)
    bands = pd.Series("Normal", index=DAYS, dtype=object)
    bands["2003-01-01":"2003-12-31"] = "High"
    config = SimConfig({"Calm": 0.6, "Normal": 0.6, "Elevated": 0.5, "High": 0.4}, 0.6, 0.001, date(1990, 1, 1))
    out = simulate(prices, prices, bands, config)
    assert set(out["portfolios"]) == {"signal", "hold", "stocks"}
    assert out["portfolios"]["signal"]["switches"] == 2
    assert out["portfolios"]["hold"]["switches"] == 0
    assert len(out["chart"]["x"]) == len(out["chart"]["signal"])


def test_summary_only_claims_wins_backed_by_ranges():
    report = {
        "all": {"band_check": True, "auc_stocks": 0.68, "grade": "modest",
                "baselines": [{"id": "vix", "diff": 0.04, "diff_ci": [-0.07, 0.15]},
                              {"id": "curve", "diff": 0.2, "diff_ci": [0.05, 0.3]}]},
        "events": {"hits": 2, "events": 15, "rate": 2 / 15},
        "false_alarms": {"episodes": 4, "false": 0},
        "held_out": {"auc_stocks": 0.48},
    }
    out = summary(report, None, {"vix": "VIX", "curve": "Yield curve"})
    assert any("clearly better than Yield curve" in g for g in out["good"])
    assert any("no clear evidence it beats VIX" in b for b in out["bad"])
    assert any("0.48" in b for b in out["bad"])
