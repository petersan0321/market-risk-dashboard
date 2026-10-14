from datetime import date

import numpy as np
import pandas as pd
import pytest

from dashboard.config import Rule
from dashboard.signals import apply_transform, known_daily, light, score_indicator, trend

# --- no look-ahead (spec §13: critical) ---------------------------------------------

@pytest.mark.parametrize("transform", [("level",), ("yoy_pct",), ("change_6m",), ("ma_30d",),
                                       ("pct_from_ma200",), ("drawdown_252d",)])
@pytest.mark.parametrize("risk_when", ["high", "low", "extreme"])
def test_past_scores_do_not_change_when_future_data_arrives(make_indicator, random_walk, transform, risk_when):
    ind = make_indicator(transform=transform, risk_when=risk_when, release_lag_days=3,
                         rules=(Rule(min_score=90, label="hi", when="value > 55"),))
    cutoff = pd.Timestamp("2019-06-28")
    past_only = score_indicator(ind, random_walk[:cutoff], cutoff.date())
    with_future = score_indicator(ind, random_walk, date(2021, 12, 31))
    pd.testing.assert_frame_equal(past_only, with_future.loc[past_only.index])


def test_monthly_series_no_look_ahead(make_indicator):
    idx = pd.date_range("2000-01-01", "2020-12-01", freq="MS")
    raw = pd.Series(np.linspace(100, 200, len(idx)) + np.sin(np.arange(len(idx))), index=idx)
    ind = make_indicator(frequency="monthly", transform=("yoy_pct",), release_lag_days=45)
    cutoff = pd.Timestamp("2015-03-01")
    # April value is released 2015-05-16, so "today" must be before that for a fair comparison
    past_only = score_indicator(ind, raw[:cutoff], date(2015, 5, 15))
    with_future = score_indicator(ind, raw, date(2020, 12, 31))
    pd.testing.assert_frame_equal(past_only, with_future.loc[past_only.index])


# --- release lag --------------------------------------------------------------------

def test_monthly_value_becomes_known_on_release_date(make_indicator):
    raw = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-01", "2024-02-01"]))
    ind = make_indicator(frequency="monthly", release_lag_days=45)
    known = known_daily(ind, raw, date(2024, 3, 29))
    # Jan value released 2024-02-15 (Thu); Feb value released 2024-03-17 (Sun) -> visible Mon 18th
    assert known.index[0] == pd.Timestamp("2024-02-15")
    assert known[pd.Timestamp("2024-03-15")] == 1.0
    assert known[pd.Timestamp("2024-03-18")] == 2.0


# --- transforms ---------------------------------------------------------------------

def test_yoy_pct_on_monthly():
    idx = pd.date_range("2023-01-01", "2024-01-01", freq="MS")
    s = pd.Series(np.arange(100, 113, dtype=float), index=idx)
    assert apply_transform(s, "yoy_pct").iloc[-1] == pytest.approx(12.0)
    assert apply_transform(s, "yoy_pct").iloc[:12].isna().all()


def test_change_6m_is_absolute_difference():
    idx = pd.date_range("2024-01-01", "2024-07-01", freq="MS")
    s = pd.Series([3.0, 3, 3, 3, 3, 3, 4.5], index=idx)
    assert apply_transform(s, "change_6m").iloc[-1] == pytest.approx(1.5)


def test_ann_6m_annualizes():
    idx = pd.date_range("2024-01-01", "2024-07-01", freq="MS")
    s = pd.Series([100.0, 0, 0, 0, 0, 0, 101], index=idx)
    assert apply_transform(s, "ann_6m").iloc[-1] == pytest.approx((1.01 ** 2 - 1) * 100)


def test_drawdown_from_running_high():
    s = pd.Series([100.0, 120, 90, 108], index=pd.bdate_range("2024-01-01", periods=4))
    assert apply_transform(s, "drawdown_252d").tolist() == pytest.approx([0, 0, 25, 10])


def test_pct_from_ma200():
    s = pd.Series([100.0] * 199 + [110.0], index=pd.bdate_range("2024-01-01", periods=200))
    assert apply_transform(s, "pct_from_ma200").iloc[-1] == pytest.approx((110 / 100.05 - 1) * 100)


@pytest.mark.parametrize("name, expected", [("ma_4w", 2.5), ("sum_12m", None)])
def test_rolling_transforms(name, expected):
    s = pd.Series([1.0, 2, 3, 4], index=pd.date_range("2024-01-05", periods=4, freq="W-FRI"))
    out = apply_transform(s, name).iloc[-1]
    assert (pd.isna(out) if expected is None else out == pytest.approx(expected))


# --- scoring ------------------------------------------------------------------------

def test_score_is_point_in_time_percentile(make_indicator):
    idx = pd.bdate_range("2010-01-01", "2016-12-30")
    raw = pd.Series(np.arange(len(idx), dtype=float), index=idx)  # always at a new high
    scored = score_indicator(make_indicator(), raw, date(2016, 12, 30))
    assert scored["score"].dropna().eq(100).all()
    flipped = score_indicator(make_indicator(risk_when="low"), raw, date(2016, 12, 30))
    assert flipped["score"].dropna().eq(0).all()


def test_no_score_before_five_years_of_history(make_indicator, random_walk):
    scored = score_indicator(make_indicator(), random_walk, date(2021, 12, 31))
    first = scored["score"].first_valid_index()
    assert first >= pd.Timestamp("2015-01-01")
    assert scored.loc[:"2014-12-31", "score"].isna().all()


def test_extreme_scores_both_tails(make_indicator):
    idx = pd.bdate_range("2010-01-01", "2016-12-30")
    values = np.r_[np.linspace(-1, 1, len(idx) - 2), [-5, 5]]
    scored = score_indicator(make_indicator(risk_when="extreme"), pd.Series(values, index=idx), date(2016, 12, 30))
    assert scored["score"].iloc[-2] > 95 and scored["score"].iloc[-1] > 95


def test_rule_raises_score_and_records_label(make_indicator, random_walk):
    ind = make_indicator(risk_when="low", rules=(Rule(min_score=86, label="inverted", when="value < 1000"),))
    scored = score_indicator(ind, random_walk, date(2021, 12, 31)).dropna(subset=["score"])
    assert (scored["score"] >= 86).all()
    assert (scored["rule_label"] == "inverted").all()


def test_multiple_rule_labels_joined(make_indicator, random_walk):
    rules = (Rule(min_score=70, label="a", when="value > -1000"), Rule(min_score=80, label="b", when="value > -1000"))
    scored = score_indicator(make_indicator(rules=rules), random_walk, date(2021, 12, 31))
    assert scored["rule_label"].iloc[-1] == "a; b"


def test_uninversion_rule(make_indicator):
    idx = pd.bdate_range("2010-01-01", "2016-12-30")
    values = pd.Series(1.0, index=idx)
    values["2016-03-01":"2016-06-30"] = -0.5
    rule = Rule(min_score=86, label="un-inverting", crossed_above_zero_within_days=182)
    scored = score_indicator(make_indicator(risk_when="low", rules=(rule,)), values, date(2016, 12, 30))
    assert scored.loc["2016-07-05", "rule_label"] == "un-inverting"
    assert scored.loc["2016-05-02", "rule_label"] == ""  # still inverted, not un-inverting
    assert scored.loc["2016-02-01", "rule_label"] == ""  # never inverted yet


@pytest.mark.parametrize("score, expected", [(10, "green"), (59.9, "green"), (60, "amber"),
                                             (85, "amber"), (85.1, "red"), (float("nan"), "none")])
def test_light_thresholds(score, expected):
    assert light(score) == expected


def test_trend():
    flat = pd.Series([50.0] * 100)
    rising = pd.Series([40.0] * 50 + [55.0] * 50)
    falling = pd.Series([70.0] * 50 + [55.0] * 50)
    assert (trend(flat), trend(rising), trend(falling)) == ("flat", "up", "down")
