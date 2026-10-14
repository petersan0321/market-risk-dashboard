import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from dashboard.fetch import fetch_all
from dashboard.fetch.base import is_stale, read_raw, with_retry, write_raw
from dashboard.fetch.fred import fetch_fred, parse_api_json, parse_csv
from dashboard.fetch.yahoo import closes_from_history

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_fred_api_json_skips_missing():
    s = parse_api_json(json.loads((FIXTURES / "fred_api.json").read_text()))
    assert s.index.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")]
    assert s.tolist() == [3.5, 3.7]


def test_parse_fred_csv_skips_missing():
    s = parse_csv((FIXTURES / "fred_graph.csv").read_text())
    assert s.tolist() == [3.5, 3.7]
    assert s.index[0] == pd.Timestamp("2024-01-02")


class FakeResponse:
    def __init__(self, text="", payload=None):
        self.text, self._payload = text, payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        return self.response


def test_fred_uses_api_when_key_given():
    session = FakeSession(FakeResponse(payload=json.loads((FIXTURES / "fred_api.json").read_text())))
    fetch_fred("T10Y3M", api_key="k", session=session)
    url, params = session.calls[0]
    assert "api.stlouisfed.org" in url and params["api_key"] == "k"


def test_fred_falls_back_to_csv_without_key():
    session = FakeSession(FakeResponse(text=(FIXTURES / "fred_graph.csv").read_text()))
    fetch_fred("T10Y3M", api_key="", session=session)
    assert "fredgraph.csv" in session.calls[0][0]


def test_yahoo_closes_drop_timezone():
    idx = pd.DatetimeIndex(["2024-01-02 00:00", "2024-01-03 00:00"]).tz_localize("America/New_York")
    s = closes_from_history(pd.DataFrame({"Close": [13.2, 14.1]}, index=idx))
    assert s.index.tz is None and s.index[0] == pd.Timestamp("2024-01-02")


def test_yahoo_empty_history_raises():
    with pytest.raises(ValueError):
        closes_from_history(pd.DataFrame({"Close": []}))


def test_retry_backs_off_then_succeeds():
    attempts, waits = [], []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("down")
        return "ok"

    assert with_retry(flaky, sleep=waits.append) == "ok"
    assert waits == [2, 4]


def test_retry_gives_up_after_four_attempts():
    waits = []
    with pytest.raises(ConnectionError):
        with_retry(lambda: (_ for _ in ()).throw(ConnectionError("down")), sleep=waits.append)
    assert waits == [2, 4, 8]


def test_raw_roundtrip(tmp_path):
    s = pd.Series([1.0, None, 3.0], index=pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02"]))
    write_raw(tmp_path / "x.csv", s)
    back = read_raw(tmp_path / "x.csv")
    assert back.index.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert back.tolist() == [3.0, 1.0]


@pytest.mark.parametrize("frequency, lag, age_days, stale", [
    ("daily", 1, 7, False), ("daily", 1, 8, True),
    ("monthly", 45, 107, False), ("monthly", 45, 108, True),
])
def test_staleness(make_indicator, frequency, lag, age_days, stale):
    ind = make_indicator(frequency=frequency, release_lag_days=lag)
    today = date(2024, 6, 30)
    assert is_stale(ind, today - pd.Timedelta(days=age_days), today) is stale


def test_fetch_all_keeps_old_data_on_failure_and_drops_implausible(make_indicator, tmp_path):
    good = make_indicator(id="good", plausible=(0.0, 10.0))
    write_raw(tmp_path / "bad.csv", pd.Series([1.0], index=pd.to_datetime(["2020-01-01"])))
    series = pd.Series([1.0, 999.0], index=pd.to_datetime(["2024-01-01", "2024-01-02"]))

    bad = make_indicator(id="bad", series="BROKEN")

    def fetcher(series_id):
        if series_id == "BROKEN":
            raise ConnectionError("down")
        return series

    status = fetch_all([good, bad], tmp_path, fetchers={"fred": fetcher}, sleep=lambda _: None, markets={})

    assert status["good"] == "ok" and status["bad"].startswith("failed")
    assert read_raw(tmp_path / "good.csv").tolist() == [1.0]  # 999 dropped as implausible
    assert read_raw(tmp_path / "bad.csv").tolist() == [1.0]  # previous file untouched
