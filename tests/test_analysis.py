"""Composite, returns, regime, and rationale."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from dashboard.composite import band, category_scores, overall_from_latest, overall_scores
from dashboard.config import CATEGORIES
from dashboard.rationale import (change_attribution, confidence, nearest_thresholds, outcome_by_band,
                                 value_at_score)
from dashboard.regime import classify, confirm, point_in_time_z, regime_returns
from dashboard.returns import bond_index, forward_max_drawdown, forward_return

EQUAL = {c: 1.0 for c in CATEGORIES}
DAYS = pd.bdate_range("2024-01-01", periods=3)


@pytest.fixture
def five_cats(make_indicator):
    cats = ["rates", "inflation", "growth", "labour", "credit"]
    return [make_indicator(id=f"{c}_{n}", category=c) for c in cats for n in (1, 2)]


# --- composite ------------------------------------------------------------------------

def test_category_mean_skips_stale_and_missing(make_indicator):
    inds = [make_indicator(id="a", category="credit"), make_indicator(id="b", category="credit"),
            make_indicator(id="c", category="credit")]
    scores = pd.DataFrame({"a": [80.0], "b": [20.0], "c": [None]}, index=DAYS[:1])
    stale = pd.DataFrame({"a": [False], "b": [True], "c": [False]}, index=DAYS[:1])
    assert category_scores(scores, stale, inds)["credit"].iloc[0] == 80.0


def test_overall_needs_five_categories(five_cats):
    scores = pd.DataFrame({i.id: [60.0] for i in five_cats}, index=DAYS[:1])
    stale = scores.astype(bool) & False
    cats = category_scores(scores, stale, five_cats)
    assert overall_scores(cats, EQUAL)["overall"].iloc[0] == 60.0
    cats["credit"] = float("nan")
    assert pd.isna(overall_scores(cats, EQUAL)["overall"].iloc[0])


def test_weights_and_contributions_sum(five_cats):
    scores = pd.DataFrame({i.id: [s] for i, s in zip(five_cats, [90, 90, 50, 50, 50, 50, 20, 20, 50, 50])},
                          index=DAYS[:1], dtype=float)
    cats = category_scores(scores, scores.astype(bool) & False, five_cats)
    weights = {**EQUAL, "rates": 3.0}
    out = overall_scores(cats, weights).iloc[0]
    assert out["overall"] == pytest.approx((3 * 90 + 50 + 50 + 20 + 50) / 7, abs=0.05)
    contrib = sum(out[f"contrib_{c}"] for c in ["rates", "inflation", "growth", "labour", "credit"])
    assert contrib == pytest.approx(out["overall"] - 50, abs=0.05)


@pytest.mark.parametrize("score, expected", [(10, "Calm"), (40, "Normal"), (59.9, "Normal"), (60, "Elevated"),
                                             (75, "High"), (float("nan"), None)])
def test_bands(score, expected):
    assert band(score) == expected


def test_overall_from_latest(five_cats):
    assert overall_from_latest({i.id: 70.0 for i in five_cats}, five_cats, EQUAL) == 70.0


# --- returns --------------------------------------------------------------------------

def test_forward_return_and_drawdown():
    prices = pd.Series([100.0, 110, 88, 99, 120])
    assert forward_return(prices, 2).tolist()[:3] == pytest.approx([88 / 100 - 1, 99 / 110 - 1, 120 / 88 - 1])
    assert forward_return(prices, 2).iloc[-2:].isna().all()
    dd = forward_max_drawdown(prices, 2)
    assert dd.iloc[0] == pytest.approx(0.2)  # 110 -> 88
    assert dd.iloc[2] == pytest.approx(0.0)
    assert dd.iloc[-2:].isna().all()


def test_bond_index_estimate_and_splice():
    days = pd.bdate_range("2024-01-01", periods=4)
    yields = pd.Series([4.0, 4.0, 5.0, 5.0], index=days)
    est = bond_index(yields)
    assert est.iloc[1] / est.iloc[0] - 1 == pytest.approx(0.04 / 252)
    assert est.iloc[2] / est.iloc[1] - 1 == pytest.approx(0.04 / 252 - 8 * 0.01)
    agg = pd.Series([50.0, 51.0], index=days[2:])
    spliced = bond_index(yields, agg)
    assert spliced.iloc[3] / spliced.iloc[2] - 1 == pytest.approx(0.02)
    assert spliced.iloc[2] == pytest.approx(est.iloc[2])


# --- regime ---------------------------------------------------------------------------

def test_confirm_requires_persistence():
    raw = pd.Series(["a"] * 3 + ["b"] * 2 + ["a"] + ["b"] * 3)
    assert confirm(raw, days=3).tolist() == ["a"] * 8 + ["b"]


def test_point_in_time_z_has_no_look_ahead(random_walk):
    full = point_in_time_z(random_walk)
    part = point_in_time_z(random_walk[:"2018-12-31"])
    pd.testing.assert_series_equal(part, full[part.index])


def test_classify_quadrants():
    idx = pd.bdate_range("2020-01-01", periods=200)
    growth = pd.Series(np.linspace(0, 1, 200), index=idx)  # rising
    inflation = pd.Series(-1.0, index=idx)  # easing
    out = classify(growth, inflation, idx)
    assert out["regime"].dropna().iloc[-1] == "goldilocks"
    assert out["regime"].iloc[:126].isna().all()


def test_regime_returns_use_previous_day_regime():
    idx = pd.bdate_range("2024-01-01", periods=3)
    regimes = pd.Series(["goldilocks", "slowdown", "slowdown"], index=idx)
    stocks = pd.Series([100.0, 110.0, 110.0], index=idx)
    out = regime_returns(regimes, stocks, stocks)
    assert out["goldilocks"]["days"] == 1 and out["goldilocks"]["stocks"] > 0
    assert out["slowdown"]["stocks"] == pytest.approx(0.0)


# --- rationale ------------------------------------------------------------------------

def test_outcomes_exclude_unfinished_windows():
    idx = pd.bdate_range("2000-01-03", periods=600)
    comp = pd.DataFrame({"band": ["High"] * 300 + ["Calm"] * 300}, index=idx)
    stocks = pd.Series(np.linspace(100, 200, 600), index=idx)
    rows = {r["band"]: r for r in outcome_by_band(comp, stocks, stocks)}
    assert rows["High"]["days"] == 300
    assert rows["Calm"]["days"] == 600 - 252 - 300
    assert rows["Elevated"]["days"] == 0 and rows["Elevated"]["spx_12m"] is None
    assert rows["High"]["big_drop_rate"] == 0.0


def test_change_attribution_sums_to_change():
    idx = pd.bdate_range("2024-01-01", periods=6)
    comp = pd.DataFrame({"overall": [50, 51, 52, 53, 54, 60.0]}, index=idx)
    for c in CATEGORIES:
        comp[f"contrib_{c}"] = 0.0
    comp["contrib_credit"] = comp["overall"] - 50
    ch = change_attribution(comp, 5)
    assert ch["change"] == pytest.approx(10) and sum(p["change"] for p in ch["parts"]) == pytest.approx(10)


def test_value_at_score_respects_direction(make_indicator):
    history = pd.Series(np.arange(101, dtype=float))
    assert value_at_score(make_indicator(risk_when="high"), history, 85) == pytest.approx([85.0])
    assert value_at_score(make_indicator(risk_when="low"), history, 85) == pytest.approx([15.0])
    assert value_at_score(make_indicator(risk_when="extreme"), history, 60) == pytest.approx([20.0, 80.0])


def test_nearest_thresholds_counts_flips(five_cats):
    history = pd.Series(np.arange(101, dtype=float))
    latest = {i.id: {"score": 55.0, "stale": False, "history": history} for i in five_cats}
    out = nearest_thresholds(five_cats, latest, EQUAL, overall=55.0)
    assert out["band_up"]["band"] == "Elevated"
    # each flip to 86 adds 3.1 to the overall (31/2 per category / 5 categories); 2 flips -> 61.2
    assert len(out["band_up"]["indicators"]) == 2
    assert out["closest_up"][0]["to"] == "amber" and out["closest_up"][0]["at_value"] == [60.0]


def test_confidence_revised_share(make_indicator):
    inds = [make_indicator(id="a", category="credit", revised=True), make_indicator(id="b", category="credit"),
            make_indicator(id="c", category="rates")]
    latest = {i.id: {"score": 50.0, "stale": False} for i in inds}
    out = confidence(inds, latest, {"credit": 50.0, "rates": 50.0}, EQUAL)
    assert out["revised_share"] == pytest.approx(0.25)
    assert out["fresh"] == 3 and out["agreement"] == "categories broadly agree"
