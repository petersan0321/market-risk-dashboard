"""Step 1: fetch every indicator's raw series into data/raw/<id>.csv, and the market price
series used for outcome statistics into data/raw/market/<name>.csv."""

import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from dashboard.config import Indicator
from dashboard.derived import combine
from dashboard.fetch.base import drop_implausible, with_retry, write_raw
from dashboard.fetch.fred import fetch_fred
from dashboard.fetch.gpr import fetch_gpr
from dashboard.fetch.shiller import fetch_shiller
from dashboard.fetch.treasury import fetch_treasury
from dashboard.fetch.yahoo import fetch_yahoo, fetch_yahoo_total_return

log = logging.getLogger(__name__)

FETCHERS: dict[str, Callable[[str], pd.Series]] = {
    "fred": fetch_fred,
    "yahoo": fetch_yahoo,
    "yahoo_tr": fetch_yahoo_total_return,
    "shiller": fetch_shiller,
    "gpr": fetch_gpr,
    "treasury": fetch_treasury,
}

# Price series for outcome statistics and the bond return series (spec §5.5).
MARKETS = {
    "spx": ("yahoo", "^GSPC"),
    "spx_tr": ("yahoo", "^SP500TR"),
    "agg": ("yahoo_tr", "AGG"),
    "ust10y": ("fred", "DGS10"),
}


class CachedFetcher:
    """Fetches each (source, series) once per run, with retries."""

    def __init__(self, fetchers, sleep=None):
        self.fetchers, self.sleep, self.cache = fetchers, sleep, {}

    def __call__(self, source: str, series: str) -> pd.Series:
        key = (source, series)
        if key not in self.cache:
            kwargs = {} if self.sleep is None else {"sleep": self.sleep}
            try:
                self.cache[key] = with_retry(lambda: self.fetchers[source](series), **kwargs)
            except Exception as exc:  # noqa: BLE001 - remember failures so shared inputs aren't retried again
                self.cache[key] = exc
        if isinstance(self.cache[key], Exception):
            raise self.cache[key]
        return self.cache[key]


def _fetch_indicator(ind: Indicator, get: CachedFetcher) -> pd.Series:
    if ind.source == "derived":
        return combine(ind, {i.name: get(i.source, i.series) for i in ind.inputs})
    return get(ind.source, ind.series)


def fetch_all(indicators: list[Indicator], raw_dir: Path, fetchers=FETCHERS, sleep=None,
              markets=MARKETS) -> dict[str, str]:
    """Fetch everything. A failure keeps the previous file (it will show as stale) and the run continues."""
    get = CachedFetcher(fetchers, sleep)
    status = {}
    for ind in indicators:
        try:
            series = drop_implausible(_fetch_indicator(ind, get), ind)
            if series.empty:
                raise ValueError("no plausible values")
            write_raw(raw_dir / f"{ind.id}.csv", series)
            status[ind.id] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.error("%s: fetch failed, keeping previous data: %s", ind.id, exc)
            status[ind.id] = f"failed: {exc}"
    for name, (source, series_id) in markets.items():
        try:
            write_raw(raw_dir / "market" / f"{name}.csv", get(source, series_id))
            status[f"market/{name}"] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.error("market %s: fetch failed, keeping previous data: %s", name, exc)
            status[f"market/{name}"] = f"failed: {exc}"
    return status
