"""Step 2: turn each raw series into a point-in-time 0-100 risk score (spec §5.1).

Every calculation here only looks backwards from each date, so a past day's score
never changes when newer data arrives (tested in test_signals.py).
"""

import json
import logging
import operator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from dashboard.config import COMPARISON, FREQUENCY_INTERVAL_DAYS, Indicator, load_indicators
from dashboard.fetch.base import read_raw
from dashboard.paths import RAW_DIR, SCORES_DIR

log = logging.getLogger(__name__)

MIN_HISTORY = pd.DateOffset(years=5)
TREND_LOOKBACK_DAYS = 63  # trading days, about 3 months
AMBER, RED = 60, 85
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


# --- transforms (applied at the series' native frequency) ---------------------------

def _value_ago(s: pd.Series, offset: pd.DateOffset) -> pd.Series:
    past = s.asof(s.index - offset)
    return pd.Series(past.values, index=s.index)


def apply_transform(s: pd.Series, name: str) -> pd.Series:
    match name:
        case "level":
            return s
        case "yoy_pct":
            return (s / _value_ago(s, pd.DateOffset(years=1)) - 1) * 100
        case "change_3m" | "change_6m" | "change_12m":
            months = int(name.split("_")[1].rstrip("m"))
            return s - _value_ago(s, pd.DateOffset(months=months))
        case "pct_change_6m":
            return (s / _value_ago(s, pd.DateOffset(months=6)) - 1) * 100
        case "ann_6m":
            return ((s / _value_ago(s, pd.DateOffset(months=6))) ** 2 - 1) * 100
        case "ma_4w":
            return s.rolling(4).mean()
        case "ma_30d":
            return s.rolling("30D").mean()
        case "pct_from_ma200":
            return (s / s.rolling(200).mean() - 1) * 100
        case "drawdown_252d":
            return (1 - s / s.rolling(252, min_periods=1).max()) * 100
        case "sum_12m":
            return s.rolling(12).sum()
    raise ValueError(f"unknown transform {name}")


# --- scoring ------------------------------------------------------------------------

def _transformed(ind: Indicator, raw: pd.Series) -> pd.Series:
    value = raw.sort_index()
    for name in ind.transform:
        value = apply_transform(value, name)
    return value.dropna()


def _to_known_days(observed: pd.Series, ind: Indicator, today: date) -> pd.Series:
    """Re-index observations to the business days on which they were known (release lag applied)."""
    known = observed.copy()
    known.index = known.index + pd.Timedelta(days=ind.release_lag_days)
    days = pd.bdate_range(known.index[0], pd.Timestamp(today))
    return known.reindex(known.index.union(days)).ffill().reindex(days)


def known_daily(ind: Indicator, raw: pd.Series, today: date) -> pd.Series:
    """Transformed values as they were known on each business day."""
    value = _transformed(ind, raw)
    return value if value.empty else _to_known_days(value, ind, today)


def _rule_mask(rule, value: pd.Series) -> pd.Series:
    if rule.when:
        op, threshold = COMPARISON.match(rule.when).groups()
        return OPS[op](value, float(threshold))
    window = f"{rule.crossed_above_zero_within_days}D"
    return (value > 0) & (value.rolling(window).min() < 0)


def score_indicator(ind: Indicator, raw: pd.Series, today: date) -> pd.DataFrame:
    """Columns: value, score (0-100, NaN before 5 years of history), rule_label, and stale
    (True on days when the latest known observation was older than expected)."""
    transformed = _transformed(ind, raw)
    if transformed.empty:
        return pd.DataFrame(columns=["value", "score", "rule_label", "stale"])
    value = _to_known_days(transformed, ind, today)
    observed_on = _to_known_days(pd.Series(transformed.index, index=transformed.index), ind, today)
    allowed = pd.Timedelta(days=2 * FREQUENCY_INTERVAL_DAYS[ind.frequency] + ind.release_lag_days)
    stale = (value.index - pd.DatetimeIndex(observed_on)) > allowed
    pct = value.expanding().rank(pct=True) * 100
    pct[value.index < value.index[0] + MIN_HISTORY] = float("nan")
    if ind.risk_when == "high":
        score = pct
    elif ind.risk_when == "low":
        score = 100 - pct
    else:
        score = 2 * (pct - 50).abs()

    labels = pd.Series("", index=value.index)
    for rule in ind.rules:
        hit = _rule_mask(rule, value) & score.notna()
        score = score.where(~hit, score.clip(lower=rule.min_score))
        labels = labels.where(~hit, labels.where(labels == "", labels + "; ") + rule.label)
    return pd.DataFrame({"value": value, "score": score.round(1), "rule_label": labels, "stale": stale})


def light(score: float) -> str:
    if pd.isna(score):
        return "none"
    return "red" if score > RED else "amber" if score >= AMBER else "green"


def trend(scores: pd.Series) -> str:
    scores = scores.dropna()
    if len(scores) <= TREND_LOOKBACK_DAYS:
        return "flat"
    change = scores.iloc[-1] - scores.iloc[-1 - TREND_LOOKBACK_DAYS]
    return "up" if change >= 10 else "down" if change <= -10 else "flat"


@dataclass
class Latest:
    id: str
    value: float | None
    score: float | None
    light: str
    trend: str
    rule_label: str
    last_observation: str | None
    history_since: str | None
    stale: bool


def summarize(ind: Indicator, raw: pd.Series, scored: pd.DataFrame, today: date) -> Latest:
    if scored.empty or raw.empty:
        return Latest(ind.id, None, None, "none", "flat", "", None, None, True)
    last = scored.iloc[-1]
    last_obs = raw.index.max().date()
    stale = bool(last["stale"])
    score = None if pd.isna(last["score"]) else float(last["score"])
    return Latest(
        id=ind.id,
        value=round(float(last["value"]), 3),
        score=score,
        light="none" if stale else light(score),
        trend=trend(scored["score"]),
        rule_label=last["rule_label"],
        last_observation=last_obs.isoformat(),
        history_since=raw.index.min().date().isoformat(),
        stale=stale,
    )


TABLES = ("value", "score", "stale", "rule_label")


def run(indicators: list[Indicator], raw_dir: Path, scores_dir: Path, today: date) -> dict[str, pd.DataFrame]:
    """Score every indicator. Writes one wide table per column in TABLES (date x indicator)
    plus latest.json, and returns the tables."""
    scores_dir.mkdir(parents=True, exist_ok=True)
    columns = {t: {} for t in TABLES}
    latest = []
    for ind in indicators:
        path = raw_dir / f"{ind.id}.csv"
        if not path.exists():
            log.error("%s: no raw data yet", ind.id)
            latest.append(Latest(ind.id, None, None, "none", "flat", "", None, None, True))
            continue
        raw = read_raw(path)
        scored = score_indicator(ind, raw, today)
        latest.append(summarize(ind, raw, scored, today))
        for t in TABLES:
            columns[t][ind.id] = scored[t]
    tables = {t: pd.DataFrame(columns[t]).rename_axis("date") for t in TABLES}
    tables["stale"] = tables["stale"].astype("boolean").fillna(True).astype(bool)
    tables["rule_label"] = tables["rule_label"].fillna("")
    for t, df in tables.items():
        df.to_csv(scores_dir / f"indicator_{t}.csv", date_format="%Y-%m-%d")
    (scores_dir / "latest.json").write_text(json.dumps(
        {"as_of": today.isoformat(), "indicators": [vars(l) for l in latest]}, indent=2))
    return tables


def read_tables(scores_dir: Path) -> dict[str, pd.DataFrame]:
    dtypes = {"value": float, "score": float, "stale": bool, "rule_label": str}
    return {t: pd.read_csv(scores_dir / f"indicator_{t}.csv", index_col="date", parse_dates=["date"],
                           dtype={c: dtypes[t] for c in pd.read_csv(scores_dir / f"indicator_{t}.csv", nrows=0).columns
                                  if c != "date"}, keep_default_na=t != "rule_label")
            for t in TABLES}


def main() -> None:
    run(load_indicators(), RAW_DIR, SCORES_DIR, date.today())
    for row in json.loads((SCORES_DIR / "latest.json").read_text())["indicators"]:
        print(f"{row['id']}: value={row['value']} score={row['score']} {row['light']} {row['trend']} {row['rule_label']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
