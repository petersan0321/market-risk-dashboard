"""Step 4: growth and inflation direction, and the four regimes (spec §5.4)."""

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from dashboard.config import Indicator
from dashboard.signals import MIN_HISTORY, known_daily

GROWTH_INPUTS = {"cfnai_ma3": 1, "claims_4w": -1, "permits_yoy": 1}  # sign: +1 = higher is stronger growth
CHANGE_WINDOW = 126  # trading days, about 6 months
CONFIRM_DAYS = 42  # a new regime must hold this many trading days (about 2 months) before it counts
REGIMES = {
    (True, False): ("goldilocks", "Goldilocks", "Growth rising, inflation easing"),
    (True, True): ("reflation", "Reflation", "Growth rising, inflation rising"),
    (False, False): ("slowdown", "Slowdown", "Growth slowing, inflation easing"),
    (False, True): ("stagflation", "Stagflation", "Growth slowing, inflation rising"),
}


def point_in_time_z(values: pd.Series) -> pd.Series:
    """How unusual each value was versus its own history up to that day."""
    mean = values.expanding().mean()
    std = values.expanding().std()
    z = (values - mean) / std
    first = values.first_valid_index()
    return z.where(values.index >= first + MIN_HISTORY) if first is not None else z


def growth_index(indicator_values: pd.DataFrame) -> pd.Series:
    parts = [sign * point_in_time_z(indicator_values[i_id].dropna())
             for i_id, sign in GROWTH_INPUTS.items() if i_id in indicator_values]
    return pd.concat(parts, axis=1, sort=True).reindex(indicator_values.index).ffill().mean(axis=1)


def inflation_gap(core_pce: Indicator, raw: pd.Series, today: date) -> pd.Series:
    """Core PCE 6-month annualized minus 12-month: positive means inflation is speeding up."""
    six = known_daily(replace(core_pce, transform=("ann_6m",)), raw, today)
    twelve = known_daily(replace(core_pce, transform=("yoy_pct",)), raw, today)
    return six - twelve


def confirm(raw: pd.Series, days: int = CONFIRM_DAYS) -> pd.Series:
    """Switch to a new regime only after it has been signalled for `days` days in a row.
    Uses only past values, so it never looks ahead."""
    out, current, candidate, count = [], None, None, 0
    for value in raw:
        if value is None or value != value:
            pass
        elif current is None:
            current = value
        elif value == current:
            candidate, count = None, 0
        else:
            count = count + 1 if value == candidate else 1
            candidate = value
            if count >= days:
                current, candidate, count = value, None, 0
        out.append(current)
    return pd.Series(out, index=raw.index, dtype=object)


def classify(growth: pd.Series, inflation: pd.Series, index: pd.DatetimeIndex) -> pd.DataFrame:
    growth = growth.reindex(index)
    growth_change = growth - growth.shift(CHANGE_WINDOW)
    inflation = inflation.reindex(index).ffill()
    ok = growth_change.notna() & inflation.notna()
    signalled = pd.Series([REGIMES[(g > 0, i > 0)][0] if k else None
                           for g, i, k in zip(growth_change, inflation, ok)], index=index, dtype=object)
    return pd.DataFrame({"growth_change": growth_change, "inflation_gap": inflation,
                         "signalled": signalled, "regime": confirm(signalled)}, index=index)


def regime_returns(regimes: pd.Series, stocks: pd.Series, bonds: pd.Series) -> dict[str, dict]:
    """Average annualized stock and bond return while each regime was in place."""
    stock_r = np.log(stocks).diff()
    bond_r = np.log(bonds).diff()
    df = pd.DataFrame({"regime": regimes.shift(1), "stocks": stock_r, "bonds": bond_r}).dropna()
    out = {}
    for key, *_ in REGIMES.values():
        rows = df[df["regime"] == key]
        out[key] = {
            "days": int(len(rows)),
            "stocks": float(np.expm1(rows["stocks"].mean() * 252)) if len(rows) else None,
            "bonds": float(np.expm1(rows["bonds"].mean() * 252)) if len(rows) else None,
        }
    return out
