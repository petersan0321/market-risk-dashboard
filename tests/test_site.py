from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from dashboard import signals
from dashboard.fetch.base import write_raw
from dashboard.site.build import build_site, format_value


def test_format_value():
    assert format_value(3.353, "%") == "3.35%"
    assert format_value(-0.5, "pp") == "-0.50 pp"
    assert format_value(15.27, "") == "15.27"
    assert format_value(None, "%") == "—"


def test_site_builds_from_scored_data(make_indicator, tmp_path):
    fresh = make_indicator(id="fresh", name="Fresh one", revised=True)
    missing = make_indicator(id="missing", name="Never fetched")
    idx = pd.bdate_range("2010-01-01", "2024-06-28")
    write_raw(tmp_path / "raw" / "fresh.csv", pd.Series(np.linspace(1, 2, len(idx)), index=idx))

    signals.run([fresh, missing], tmp_path / "raw", tmp_path / "scores", date(2024, 6, 28))
    page = build_site([fresh, missing], tmp_path / "scores", tmp_path / "site",
                      datetime(2024, 6, 28, tzinfo=timezone.utc)).read_text()

    assert "Fresh one" in page and "Never fetched" in page
    assert "■ Red" in page  # a series at its all-time high scores 100
    assert "Revised data" in page and "Stale" in page
    assert "Data health: 1/2 indicators fresh" in page
    assert (tmp_path / "site" / "static" / "style.css").exists()
