"""Load and validate the YAML config. Invalid config fails fast with a clear message."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from dashboard.paths import CONFIG_DIR

CATEGORIES = {
    "rates": "Rates & Fed",
    "inflation": "Inflation",
    "growth": "Growth",
    "labour": "Labour market",
    "credit": "Credit & financial stress",
    "valuation": "Valuation",
    "volatility": "Volatility, sentiment & breadth",
    "global": "Dollar, commodities & global",
    "policy": "Policy & geopolitics",
}
SOURCES = {"fred", "yahoo"}
TRANSFORMS = {
    "level", "yoy_pct", "change_3m", "change_6m", "change_12m", "pct_change_6m",
    "ann_6m", "ma_4w", "ma_30d", "pct_from_ma200", "drawdown_252d", "sum_12m",
}
# Normal gap between observations, in calendar days (daily allows for weekends).
FREQUENCY_INTERVAL_DAYS = {"daily": 3, "weekly": 7, "monthly": 31, "quarterly": 92}
RISK_WHEN = {"high", "low", "extreme"}

COMPARISON = re.compile(r"^value\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    min_score: float
    label: str
    when: str | None = None
    crossed_above_zero_within_days: int | None = None


@dataclass(frozen=True)
class Indicator:
    id: str
    name: str
    category: str
    source: str
    series: str
    frequency: str
    release_lag_days: int
    transform: tuple[str, ...]
    risk_when: str
    revised: bool
    unit: str
    plausible: tuple[float, float]
    rules: tuple[Rule, ...]
    explain: str


def _parse_rule(ind_id: str, raw: dict) -> Rule:
    keys = set(raw) - {"min_score", "label"}
    if keys not in ({"when"}, {"crossed_above_zero_within_days"}):
        raise ConfigError(f"{ind_id}: a rule needs exactly one of 'when' or 'crossed_above_zero_within_days'")
    if "min_score" not in raw or "label" not in raw:
        raise ConfigError(f"{ind_id}: a rule needs 'min_score' and 'label'")
    if not 0 <= raw["min_score"] <= 100:
        raise ConfigError(f"{ind_id}: rule min_score must be 0-100")
    if "when" in raw and not COMPARISON.match(raw["when"]):
        raise ConfigError(f"{ind_id}: rule 'when' must look like 'value > 8', got {raw['when']!r}")
    return Rule(
        min_score=float(raw["min_score"]),
        label=raw["label"],
        when=raw.get("when"),
        crossed_above_zero_within_days=raw.get("crossed_above_zero_within_days"),
    )


def _parse_indicator(raw: dict) -> Indicator:
    ind_id = raw.get("id", "<missing id>")
    required = ["id", "name", "category", "source", "series", "frequency", "release_lag_days",
                "transform", "risk_when", "revised", "unit", "plausible", "explain"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ConfigError(f"{ind_id}: missing fields {missing}")
    if raw["category"] not in CATEGORIES:
        raise ConfigError(f"{ind_id}: unknown category {raw['category']!r}")
    if raw["source"] not in SOURCES:
        raise ConfigError(f"{ind_id}: unknown source {raw['source']!r}")
    if raw["frequency"] not in FREQUENCY_INTERVAL_DAYS:
        raise ConfigError(f"{ind_id}: unknown frequency {raw['frequency']!r}")
    if raw["risk_when"] not in RISK_WHEN:
        raise ConfigError(f"{ind_id}: unknown risk_when {raw['risk_when']!r}")
    transform = raw["transform"]
    transform = (transform,) if isinstance(transform, str) else tuple(transform)
    unknown = [t for t in transform if t not in TRANSFORMS]
    if unknown:
        raise ConfigError(f"{ind_id}: unknown transform {unknown}")
    lo, hi = raw["plausible"]
    if lo >= hi:
        raise ConfigError(f"{ind_id}: plausible range must be [low, high]")
    if not str(raw["explain"]).strip():
        raise ConfigError(f"{ind_id}: explain must not be empty")
    return Indicator(
        id=raw["id"],
        name=raw["name"],
        category=raw["category"],
        source=raw["source"],
        series=str(raw["series"]),
        frequency=raw["frequency"],
        release_lag_days=int(raw["release_lag_days"]),
        transform=transform,
        risk_when=raw["risk_when"],
        revised=bool(raw["revised"]),
        unit=raw["unit"],
        plausible=(float(lo), float(hi)),
        rules=tuple(_parse_rule(ind_id, r) for r in raw.get("rules", [])),
        explain=" ".join(str(raw["explain"]).split()),
    )


def load_indicators(path: Path = CONFIG_DIR / "indicators.yaml") -> list[Indicator]:
    entries = yaml.safe_load(path.read_text()) or []
    indicators = [_parse_indicator(e) for e in entries]
    ids = [i.id for i in indicators]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConfigError(f"duplicate indicator ids: {sorted(dupes)}")
    return indicators
