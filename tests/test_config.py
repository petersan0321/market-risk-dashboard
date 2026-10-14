import pytest
import yaml

from dashboard.config import ConfigError, load_indicators

VALID = {
    "id": "x", "name": "X", "category": "credit", "source": "fred", "series": "S",
    "frequency": "daily", "release_lag_days": 1, "transform": "level", "risk_when": "high",
    "revised": False, "unit": "%", "plausible": [0, 10], "explain": "Why it matters.",
}


def write(tmp_path, entries):
    path = tmp_path / "indicators.yaml"
    path.write_text(yaml.safe_dump(entries))
    return path


def test_real_config_is_valid():
    indicators = load_indicators()
    assert {i.id for i in indicators} == {"baa_spread", "curve_10y3m", "vix", "cpi_yoy", "sahm_rule"}


def test_valid_entry_loads(tmp_path):
    [ind] = load_indicators(write(tmp_path, [VALID]))
    assert ind.transform == ("level",)
    assert ind.plausible == (0.0, 10.0)


def test_transform_list_is_kept_in_order(tmp_path):
    [ind] = load_indicators(write(tmp_path, [{**VALID, "transform": ["ma_4w", "pct_change_6m"]}]))
    assert ind.transform == ("ma_4w", "pct_change_6m")


@pytest.mark.parametrize("change, message", [
    ({"category": "nope"}, "unknown category"),
    ({"source": "nope"}, "unknown source"),
    ({"frequency": "hourly"}, "unknown frequency"),
    ({"risk_when": "sometimes"}, "unknown risk_when"),
    ({"transform": ["level", "nope"]}, "unknown transform"),
    ({"plausible": [10, 0]}, "plausible range"),
    ({"explain": "  "}, "explain"),
    ({"rules": [{"when": "value >> 3", "min_score": 86, "label": "x"}]}, "'when' must look like"),
    ({"rules": [{"min_score": 86, "label": "x"}]}, "exactly one of"),
    ({"rules": [{"when": "value > 3", "label": "x"}]}, "min_score"),
])
def test_invalid_entries_fail_fast(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_indicators(write(tmp_path, [{**VALID, **change}]))


def test_missing_field_fails(tmp_path):
    entry = {k: v for k, v in VALID.items() if k != "explain"}
    with pytest.raises(ConfigError, match="missing fields"):
        load_indicators(write(tmp_path, [entry]))


def test_duplicate_ids_fail(tmp_path):
    with pytest.raises(ConfigError, match="duplicate"):
        load_indicators(write(tmp_path, [VALID, VALID]))
