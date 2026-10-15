"""Educational "what if" simulation (spec §9.6): would shifting between stocks and bonds by the
overall band have helped, and what would it have cost? Not a recommendation."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dashboard.backtest import REPS, SEED, block_indices
from dashboard.composite import BAND_NAMES
from dashboard.paths import CONFIG_DIR

DECADES = [(1990, 1999), (2000, 2009), (2010, 2019), (2020, 2029)]
HELD_OUT_START = "2015-01-01"


@dataclass(frozen=True)
class SimConfig:
    stocks_share: dict[str, float]
    baseline_stocks_share: float
    trading_cost: float
    start: date


def load_sim_config(path: Path = CONFIG_DIR / "simulation.yaml") -> SimConfig:
    raw = yaml.safe_load(path.read_text())
    shares = raw["stocks_share"]
    if set(shares) != set(BAND_NAMES) or not all(0 <= v <= 1 for v in shares.values()):
        raise ValueError(f"simulation.yaml stocks_share needs a 0-1 value for each of {BAND_NAMES}")
    return SimConfig(shares, float(raw["baseline_stocks_share"]), float(raw["trading_cost"]), raw["start"])


def run_portfolio(stock_ret: pd.Series, bond_ret: pd.Series, targets: pd.Series, cost: float) -> pd.DataFrame:
    """Daily value of a portfolio starting at 10,000. `targets` is the stock share decided at each
    month-end; it is applied from the next trading day (no same-day look-ahead). Between month-ends
    the holdings drift with prices. Trading costs `cost` × amount traded."""
    days = stock_ret.index
    month_end = pd.Series(days, index=days).groupby(days.to_period("M")).transform("max") == days
    value, stock_w = 10_000.0, None
    values, weights = [], []
    pending = None
    for i, day in enumerate(days):
        if pending is not None:  # rebalance at today's open to the target set yesterday
            if stock_w is not None and abs(pending - stock_w) > 1e-12:
                value *= 1 - cost * abs(pending - stock_w)
            stock_w, pending = pending, None
        if stock_w is None:
            stock_w = float(targets.iloc[i])
        r_s, r_b = stock_ret.iloc[i], bond_ret.iloc[i]
        growth = stock_w * (1 + r_s) + (1 - stock_w) * (1 + r_b)
        value *= growth
        stock_w = stock_w * (1 + r_s) / growth
        values.append(value)
        weights.append(stock_w)
        if month_end.iloc[i]:  # month-end: rebalance to the target from the next trading day
            pending = float(targets.iloc[i])
    out = pd.DataFrame({"value": values, "stock_weight": weights}, index=days)
    target_changes = targets[month_end.to_numpy()].diff().fillna(0).ne(0).sum()
    out.attrs["switches"] = int(target_changes)
    return out


def stats(values: pd.Series) -> dict:
    r = values.pct_change().dropna()
    years = len(values) / 252
    running_max = values.cummax()
    drawdown = 1 - values / running_max
    # longest stretch below a previous peak, in trading days
    underwater = (values < running_max).astype(int)
    longest = int(underwater.groupby((underwater == 0).cumsum()).sum().max()) if len(values) else 0
    return {
        "annual_return": float((values.iloc[-1] / values.iloc[0]) ** (1 / years) - 1) if years > 0 else None,
        "volatility": float(r.std() * np.sqrt(252)),
        "max_drawdown": float(drawdown.max()),
        "longest_recovery_days": longest,
        "final_value": float(values.iloc[-1] / values.iloc[0] * 10_000),
    }


def simulate(stocks_tr: pd.Series, bonds: pd.Series, bands: pd.Series, config: SimConfig,
             events: list[dict] = ()) -> dict | None:
    """Three portfolios: signal-based, hold 60/40, hold 100% stocks."""
    frame = pd.DataFrame({"s": stocks_tr, "b": bonds, "band": bands}).ffill()
    first_band = bands.first_valid_index()
    if first_band is None:
        return None
    start = max(pd.Timestamp(config.start), first_band)
    frame = frame[frame.index >= start].dropna()
    if len(frame) < 300:
        return None
    s_ret, b_ret = frame["s"].pct_change().fillna(0), frame["b"].pct_change().fillna(0)
    targets = {
        "signal": frame["band"].map(config.stocks_share).astype(float),
        "hold": pd.Series(config.baseline_stocks_share, index=frame.index),
        "stocks": pd.Series(1.0, index=frame.index),
    }
    runs = {k: run_portfolio(s_ret, b_ret, t, config.trading_cost) for k, t in targets.items()}
    values = pd.DataFrame({k: r["value"] for k, r in runs.items()})

    portfolios = {k: {**stats(values[k]), "switches": runs[k].attrs["switches"]} for k in runs}
    held = values[values.index >= HELD_OUT_START]
    held_out = {k: stats(held[k]) for k in runs} if len(held) > 252 else None

    decades = []
    for a, b in DECADES:
        part = values[(values.index.year >= a) & (values.index.year <= b)]
        if len(part) > 252:
            decades.append({"decade": f"{a}s", **{k: stats(part[k])["annual_return"] for k in runs}})

    event_rows = []
    for e in events:
        if e["event"].kind != "crash" or e["start"] < values.index[0]:
            continue
        window = values[(values.index >= e["start"]) & (values.index <= e["end"])]
        if len(window) > 1:
            event_rows.append({"event": e["event"].id, **{k: float(window[k].iloc[-1] / window[k].iloc[0] - 1) for k in runs}})

    weekly = values.resample("W-FRI").last().dropna()
    weights_weekly = runs["signal"]["stock_weight"].resample("W-FRI").last().dropna()
    return {
        "start": values.index[0].date().isoformat(),
        "end": values.index[-1].date().isoformat(),
        "config": {"stocks_share": config.stocks_share, "baseline": config.baseline_stocks_share,
                   "trading_cost": config.trading_cost},
        "portfolios": portfolios,
        "held_out": held_out,
        "decades": decades,
        "events": event_rows,
        "comparison": return_difference(values["signal"], values["hold"]),
        "chart": {
            "x": [d.date().isoformat() for d in weekly.index],
            **{k: [round(float(v), 1) for v in weekly[k]] for k in runs},
            "drawdown": {k: [round(float(v), 4) for v in (1 - weekly[k] / weekly[k].cummax())] for k in runs},
            "stock_weight": [round(float(v), 3) for v in weights_weekly],
        },
    }


def _annual(values: np.ndarray) -> float:
    return float(np.prod(1 + values) ** (252 / len(values)) - 1)


def return_difference(signal: pd.Series, hold: pd.Series, reps: int = REPS, seed: int = SEED) -> dict:
    """Annual return of signal minus hold, with a 90% block-bootstrap interval."""
    rs, rh = signal.pct_change().dropna().to_numpy(), hold.pct_change().dropna().to_numpy()
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(reps):
        idx = block_indices(len(rs), rng)
        diffs.append(_annual(rs[idx]) - _annual(rh[idx]))
    return {"difference": _annual(rs) - _annual(rh),
            "ci": [float(np.percentile(diffs, 5)), float(np.percentile(diffs, 95))]}
