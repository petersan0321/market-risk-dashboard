"""New data sources, derived indicators, and config additions."""

from datetime import date

import pandas as pd
import pytest
import yaml

from dashboard.config import CATEGORIES, ConfigError, Input, load_indicators, load_untracked, load_weights
from dashboard.derived import combine
from dashboard.fetch import CachedFetcher
from dashboard.fetch.shiller import _month_index, find_download_link
from dashboard.fetch.treasury import bid_to_cover
from dashboard.signals import score_indicator

DERIVED = {
    "id": "d", "name": "D", "category": "credit", "source": "derived", "frequency": "daily",
    "release_lag_days": 1, "transform": "level", "risk_when": "high", "revised": False, "unit": "",
    "plausible": [0, 10], "explain": "Derived.",
    "inputs": {"a": {"source": "fred", "series": "A"}, "b": {"source": "fred", "series": "B"}},
    "formula": "a - b",
}


def load_one(tmp_path, entry):
    path = tmp_path / "i.yaml"
    path.write_text(yaml.safe_dump([entry]))
    return load_indicators(path)[0]


def test_real_catalog_has_44_indicators_in_all_9_categories():
    indicators = load_indicators()
    assert len(indicators) == 44
    assert {i.category for i in indicators} == set(CATEGORIES)


def test_derived_entry_loads(tmp_path):
    ind = load_one(tmp_path, DERIVED)
    assert ind.inputs == (Input("a", "fred", "A"), Input("b", "fred", "B")) and ind.formula == "a - b"


@pytest.mark.parametrize("formula, message", [
    ("a - c", "unknown input"),
    ("__import__('os')", "may only use"),
    ("a ** 2", "may only use"),
    ("a -", "not valid"),
])
def test_unsafe_or_wrong_formulas_rejected(tmp_path, formula, message):
    with pytest.raises(ConfigError, match=message):
        load_one(tmp_path, {**DERIVED, "formula": formula})


def test_derived_needs_inputs(tmp_path):
    with pytest.raises(ConfigError, match="needs 'inputs'"):
        load_one(tmp_path, {k: v for k, v in DERIVED.items() if k != "inputs"})


def test_weights_and_untracked_load():
    assert set(load_weights()) == set(CATEGORIES)
    assert all(u.why_not_tracked for u in load_untracked())


def test_weights_must_cover_all_categories(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text("rates: 1\n")
    with pytest.raises(ConfigError):
        load_weights(path)


def test_combine_aligns_slower_input_to_latest_known_value(make_indicator):
    from dashboard.config import Input
    ind = make_indicator(source="derived", inputs=(Input("m", "fred", "M", ("sum_12m",)), Input("g", "fred", "G")),
                         formula="-m / g * 100")
    months = pd.date_range("2023-01-01", "2023-12-01", freq="MS")
    m = pd.Series(-1.0, index=months)
    gdp = pd.Series([100.0, 200.0], index=pd.to_datetime(["2023-01-01", "2023-10-01"]))
    out = combine(ind, {"m": m, "g": gdp})
    assert out.index.tolist() == [pd.Timestamp("2023-12-01")]  # sum_12m needs 12 months
    assert out.iloc[0] == pytest.approx(12 / 200 * 100)


def test_combine_drops_division_by_zero(make_indicator):
    ind = make_indicator(source="derived", inputs=(Input("a", "fred", "A"), Input("b", "fred", "B")), formula="a / b")
    idx = pd.bdate_range("2024-01-01", periods=2)
    out = combine(ind, {"a": pd.Series([1.0, 1.0], index=idx), "b": pd.Series([0.0, 2.0], index=idx)})
    assert out.tolist() == [0.5]


def test_shiller_month_parsing():
    idx = _month_index(pd.Series([2023.01, 2023.1, 2023.12]))
    assert [d.month for d in idx] == [1, 10, 12]


def test_shiller_link_found():
    html = '<a href="//img1.wsimg.com/blobby/go/x/downloads/y/ie_data.xls?ver=123">Data</a>'
    assert find_download_link(html) == "https://img1.wsimg.com/blobby/go/x/downloads/y/ie_data.xls?ver=123"
    with pytest.raises(ValueError):
        find_download_link("<html></html>")


def test_treasury_bid_to_cover_filters_terms_and_averages():
    def auction(day, term, kind, btc):
        return {"auctionDate": f"2024-01-{day:02d}T00:00:00", "securityTerm": term, "type": kind,
                "bidToCoverRatio": btc}
    auctions = [auction(d, "10-Year", "Note", "2.0") for d in range(1, 4)]
    auctions += [auction(d, "29-Year 11-Month", "Bond", "3.0") for d in range(4, 7)]
    auctions += [auction(7, "2-Year", "Note", "9.0"), auction(8, "30-Year", "Bond", "")]
    s = bid_to_cover(auctions)
    assert len(s) == 1 and s.iloc[0] == pytest.approx(2.5)


def test_cached_fetcher_fetches_once_and_remembers_failures():
    calls = []

    def ok(series):
        calls.append(series)
        return pd.Series([1.0])

    def broken(series):
        calls.append(series)
        raise ConnectionError("down")

    get = CachedFetcher({"fred": ok, "yahoo": broken}, sleep=lambda _: None)
    get("fred", "A"), get("fred", "A")
    for _ in range(2):
        with pytest.raises(ConnectionError):
            get("yahoo", "B")
    assert calls.count("A") == 1 and calls.count("B") == 4  # 4 attempts on first use, none after


def test_staleness_is_point_in_time(make_indicator):
    idx = pd.bdate_range("2010-01-01", "2016-06-30")
    raw = pd.Series(range(len(idx)), index=idx, dtype=float).drop(pd.bdate_range("2015-03-02", "2015-03-31"))
    scored = score_indicator(make_indicator(release_lag_days=1), raw, date(2016, 6, 30))
    assert not scored.loc["2015-03-03", "stale"]  # gap of 2 days: fine
    assert scored.loc["2015-03-20", "stale"]  # 3 weeks without data: stale
    assert not scored.loc["2015-04-02", "stale"]  # data resumed
