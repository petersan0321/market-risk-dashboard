"""Step 1: fetch every indicator's raw series into data/raw/<id>.csv."""

import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from dashboard.config import Indicator
from dashboard.fetch.base import drop_implausible, with_retry, write_raw
from dashboard.fetch.fred import fetch_fred
from dashboard.fetch.yahoo import fetch_yahoo

log = logging.getLogger(__name__)

FETCHERS: dict[str, Callable[[str], pd.Series]] = {"fred": fetch_fred, "yahoo": fetch_yahoo}


def fetch_all(indicators: list[Indicator], raw_dir: Path, fetchers=FETCHERS, sleep=None) -> dict[str, str]:
    """Fetch each indicator. A failure keeps the previous file (it will show as stale) and the run continues."""
    status = {}
    for ind in indicators:
        fetch = fetchers[ind.source]
        try:
            kwargs = {} if sleep is None else {"sleep": sleep}
            series = with_retry(lambda: fetch(ind.series), **kwargs)
            series = drop_implausible(series, ind)
            if series.empty:
                raise ValueError("no plausible values")
            write_raw(raw_dir / f"{ind.id}.csv", series)
            status[ind.id] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.error("%s: fetch failed, keeping previous data: %s", ind.id, exc)
            status[ind.id] = f"failed: {exc}"
    return status
