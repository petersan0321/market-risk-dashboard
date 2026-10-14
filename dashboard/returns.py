"""Stock and bond return series and forward-looking outcomes (spec §5.5, §6.2)."""

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

BOND_DURATION = 8.0  # approximate modified duration of a 10-year Treasury
TRADING_DAYS = 252


def bond_index(ust10y: pd.Series, agg: pd.Series | None = None) -> pd.Series:
    """Bond total-return index (starts at 100). Before AGG exists, estimated from the 10-year
    yield: daily return ≈ yesterday's yield / 252 - duration × change in yield."""
    days = pd.bdate_range(ust10y.index.min(), ust10y.index.max())
    y = ust10y.reindex(ust10y.index.union(days)).ffill().reindex(days) / 100
    returns = y.shift(1) / TRADING_DAYS - BOND_DURATION * y.diff()
    if agg is not None and not agg.empty:
        agg_days = agg.reindex(agg.index.union(days)).ffill().reindex(days)
        agg_returns = agg_days.pct_change()
        start = agg.index.min()
        returns = returns.where(returns.index <= start, agg_returns)
    return (1 + returns.fillna(0)).cumprod() * 100


def is_estimated_bond(dates: pd.DatetimeIndex, agg: pd.Series | None) -> pd.Series:
    start = agg.index.min() if agg is not None and not agg.empty else pd.Timestamp.max
    return pd.Series(dates <= start, index=dates)


def on_days(prices: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Prices on the given days, carrying the last price over holidays."""
    return prices.reindex(prices.index.union(days)).ffill().reindex(days)


def forward_return(prices: pd.Series, periods: int) -> pd.Series:
    """Return from each day to `periods` rows later; NaN when that day is in the future."""
    return prices.shift(-periods) / prices - 1


def forward_max_drawdown(prices: pd.Series, periods: int) -> pd.Series:
    """Worst peak-to-trough fall within the next `periods` rows (0.2 = a 20% fall)."""
    values = prices.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) > periods:
        windows = sliding_window_view(values, periods + 1)
        running_max = np.maximum.accumulate(windows, axis=1)
        out[: len(windows)] = (1 - windows / running_max).max(axis=1)
    return pd.Series(out, index=prices.index)
