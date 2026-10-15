"""End-to-end: synthetic raw data → scores → analysis → site, then check the pages."""

import json
import re
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from dashboard import signals
from dashboard.config import CATEGORIES, Untracked, load_events, load_glossary
from dashboard.fetch.base import write_raw
from dashboard.run import analyze
from dashboard.site.build import build_site, format_value, spectrum_zones

TODAY = date(2024, 6, 28)


def test_format_value():
    assert format_value(3.353, "%") == "3.35%"
    assert format_value(-0.5, "pp") == "-0.50 pp"
    assert format_value(15.27, "") == "15.27"
    assert format_value(1.5, "x") == "1.50×"
    assert format_value(512.4, "k") == "512k"
    assert format_value(5.2, "% of GDP") == "5.20 % of GDP"
    assert format_value(None, "%") == "—"


def test_spectrum_zones_cover_the_bar(make_indicator):
    spec = {"min": 0, "max": 100, "amber": [60], "red": [85]}
    zones = spectrum_zones(make_indicator(risk_when="high"), spec)
    assert [z["light"] for z in zones["zones"]] == ["green", "amber", "red"]
    assert sum(z["width"] for z in zones["zones"]) == pytest.approx(100)
    low = spectrum_zones(make_indicator(risk_when="low"), {**spec, "amber": [40], "red": [15]})
    assert [z["light"] for z in low["zones"]] == ["red", "amber", "green"]


def make_indicators(make_indicator):
    return [
        make_indicator(id="core_pce_6m", name="Core PCE", category="inflation", frequency="monthly",
                       transform=("ann_6m",), release_lag_days=58, revised=True, unit="%"),
        make_indicator(id="cfnai_ma3", name="CFNAI", category="growth", frequency="monthly", release_lag_days=52),
        make_indicator(id="permits_yoy", name="Permits", category="growth", frequency="monthly",
                       transform=("yoy_pct",), release_lag_days=48, risk_when="low"),
        make_indicator(id="claims_4w", name="Claims", category="labour", release_lag_days=5),
        make_indicator(id="spread", name="Spread", category="credit", unit="pp"),
        make_indicator(id="vol", name="Vol", category="volatility", risk_when="extreme"),
        make_indicator(id="never", name="Never fetched", category="policy"),
    ]


def write_synthetic_raw(raw_dir, until=TODAY):
    """Deterministic fake history for every test indicator, cut off at `until`."""
    rng = np.random.default_rng(7)
    days = pd.bdate_range("1995-01-02", TODAY)
    months = pd.date_range("1995-01-01", "2024-05-01", freq="MS")
    def walk(n, start):
        return pd.Series(start + rng.normal(0, 1, n).cumsum())

    write_raw(raw_dir / "core_pce_6m.csv", pd.Series(np.linspace(60, 120, len(months)), index=months))
    write_raw(raw_dir / "cfnai_ma3.csv", pd.Series(rng.normal(0, 1, len(months)), index=months))
    write_raw(raw_dir / "permits_yoy.csv", pd.Series(1500 + rng.normal(0, 50, len(months)), index=months))
    for i_id in ("claims_4w", "spread", "vol"):
        write_raw(raw_dir / f"{i_id}.csv", walk(len(days), 100).set_axis(days))
    prices = pd.Series(np.exp(np.linspace(0, 1, len(days)) + rng.normal(0, 0.01, len(days))) * 100, index=days)
    write_raw(raw_dir / "market" / "spx.csv", prices)
    write_raw(raw_dir / "market" / "ust10y.csv", pd.Series(4.0, index=days))
    write_raw(raw_dir / "market" / "agg.csv", prices[prices.index >= "2004-01-01"])
    for path in raw_dir.rglob("*.csv"):
        df = pd.read_csv(path, parse_dates=["date"])
        df[df["date"] <= pd.Timestamp(until)].to_csv(path, index=False, date_format="%Y-%m-%d")


def run_pipeline(indicators, tmp_path, today):
    raw_dir = tmp_path / "raw"
    scores_dir = tmp_path / "scores"
    tables = signals.run(indicators, raw_dir, scores_dir, today)
    weights = {c: 1.0 for c in CATEGORIES}
    return analyze(indicators, weights, tables, raw_dir, scores_dir, today, load_events())


@pytest.fixture
def pipeline(make_indicator, tmp_path):
    indicators = make_indicators(make_indicator)
    write_synthetic_raw(tmp_path / "raw")
    result = run_pipeline(indicators, tmp_path, TODAY)
    scores_dir = tmp_path / "scores"
    untracked = [Untracked("Margin debt", "Borrowed money.", "No source.")]
    index = build_site(indicators, untracked, scores_dir, tmp_path / "site", datetime(2024, 6, 28, tzinfo=timezone.utc),
                       load_events(), load_glossary(), context_dir=tmp_path / "context")
    return result, index.parent


def test_analysis_outputs(pipeline):
    result, _ = pipeline
    assert result["overall"]["n_categories"] == 5
    assert result["overall"]["band"] in {"Calm", "Normal", "Elevated", "High"}
    assert sum(c["contribution"] for c in result["contributions"]) == pytest.approx(result["overall"]["score"] - 50, abs=0.2)
    assert result["regime"]["now"] in {"goldilocks", "reflation", "slowdown", "stagflation"}
    assert result["confidence"]["fresh"] == 6 and result["confidence"]["total"] == 7
    json.dumps(result)  # fully serializable, no NaN


def test_all_pages_built(pipeline):
    _, site = pipeline
    pages = {"index.html", "why.html", "learn.html", "time-machine.html", "history/index.html",
             "history/which-factors-warned.html", "history/gfc_2008.html"} | {f"category/{c}.html" for c in CATEGORIES}
    assert pages <= {str(p.relative_to(site)) for p in site.rglob("*.html")}
    index = (site / "index.html").read_text()
    assert "Overall market risk" in index and "Margin debt" in index
    credit = (site / "category" / "credit.html").read_text()
    assert "Spread" in credit and "spectrum-track" in credit
    assert "Revised data" in (site / "category" / "inflation.html").read_text()
    assert "No data yet" in (site / "category" / "policy.html").read_text()


def test_internal_links_resolve(pipeline):
    _, site = pipeline
    for page in site.rglob("*.html"):
        for href in re.findall(r'(?:href|src)="([^"#]+)"', page.read_text()):
            path = href.split("?")[0]
            if href.startswith(("http://", "https://")) or not path:
                continue  # external, or a query on the same page
            assert (page.parent / path).resolve().exists(), f"{page.name} links to missing {href}"


def test_composite_and_regime_have_no_look_ahead(make_indicator, tmp_path):
    """Spec §13: past overall, category, and regime values must not change when newer data arrives."""
    indicators = make_indicators(make_indicator)
    cutoff = date(2015, 12, 31)
    write_synthetic_raw(tmp_path / "past" / "raw", until=cutoff)
    write_synthetic_raw(tmp_path / "full" / "raw")
    run_pipeline(indicators, tmp_path / "past", cutoff)
    run_pipeline(indicators, tmp_path / "full", TODAY)

    def read(name, which):
        return pd.read_csv(tmp_path / which / "scores" / name, index_col="date", parse_dates=["date"])

    past, full = read("composite.csv", "past"), read("composite.csv", "full")
    assert past["overall"].notna().sum() > 1000
    pd.testing.assert_frame_equal(past, full.loc[past.index])
    past_regime, full_regime = read("regime.csv", "past"), read("regime.csv", "full")
    pd.testing.assert_frame_equal(past_regime, full_regime.loc[past_regime.index])


def test_event_page_has_story_and_heatmap(pipeline):
    _, site = pipeline
    page = (site / "history" / "gfc_2008.html").read_text()
    assert "What happened, step by step" in page and "Lehman Brothers" in page
    assert "Every indicator's risk score around the event" in page and "Mortgage-backed security" in page
    assert "time-machine.html?from=" in page


def test_time_machine_export(pipeline):
    _, site = pipeline
    tm = site / "data" / "tm"
    index = json.loads((tm / "index.json").read_text())
    assert set(index["categories"]) == set(CATEGORIES) and index["events"]
    assert index["first_date"] <= index["last_date"]
    for year in index["years"]:
        data = json.loads((tm / f"{year}.json").read_text())
        n = len(data["dates"])
        assert n and all(len(data[k]) == n for k in ("overall", "pct", "regime", "spx", "bond"))
        assert all(len(v["s"]) == n and len(v["v"]) == n for v in data["indicators"].values())
        assert all(len(v) == n for v in data["categories"].values())
