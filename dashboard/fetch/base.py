"""Shared fetch helpers: retry with backoff, raw CSV storage, sanity filtering, staleness."""

import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd

from dashboard.config import FREQUENCY_INTERVAL_DAYS, Indicator

log = logging.getLogger(__name__)

RETRY_DELAYS = (2, 4, 8)


def with_retry[T](fn: Callable[[], T], delays=RETRY_DELAYS, sleep=time.sleep) -> T:
    """Call fn; on failure wait each delay in turn and retry. Re-raises the last error."""
    for delay in delays:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - any source failure is retryable
            log.warning("attempt failed (%s); retrying in %ss", exc, delay)
            sleep(delay)
    return fn()


def drop_implausible(series: pd.Series, ind: Indicator) -> pd.Series:
    lo, hi = ind.plausible
    ok = series.between(lo, hi)
    if (~ok).any():
        log.warning("%s: dropped %d implausible values", ind.id, int((~ok).sum()))
    return series[ok]


def read_raw(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date")["value"].sort_index()


def write_raw(path: Path, series: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = series.dropna().sort_index().round(4).rename("value")
    out.index = pd.DatetimeIndex(out.index).normalize().rename("date")
    out = out[~out.index.duplicated(keep="last")]
    out.to_csv(path, date_format="%Y-%m-%d")


def is_stale(ind: Indicator, last_observation: date, today: date) -> bool:
    """Stale if older than 2x the normal interval plus the release lag."""
    allowed = 2 * FREQUENCY_INTERVAL_DAYS[ind.frequency] + ind.release_lag_days
    return (today - last_observation).days > allowed
