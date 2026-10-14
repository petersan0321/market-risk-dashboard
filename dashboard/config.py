"""Load and validate the YAML config. Invalid config fails fast with a clear message."""

import ast
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
SOURCES = {"fred", "yahoo", "shiller", "gpr", "treasury", "derived"}
INPUT_SOURCES = SOURCES - {"derived"}
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
class Input:
    name: str
    source: str
    series: str
    transform: tuple[str, ...] = ()


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
    inputs: tuple[Input, ...] = ()
    formula: str | None = None


@dataclass(frozen=True)
class Untracked:
    name: str
    why_it_matters: str
    why_not_tracked: str


_FORMULA_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Load,
                  ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)


def check_formula(ind_id: str, formula: str, names: set[str]) -> None:
    """Formulas may only use + - * /, numbers, and the indicator's input names."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ConfigError(f"{ind_id}: formula is not valid: {formula!r}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _FORMULA_NODES):
            raise ConfigError(f"{ind_id}: formula may only use + - * / and input names: {formula!r}")
        if isinstance(node, ast.Name) and node.id not in names:
            raise ConfigError(f"{ind_id}: formula uses unknown input {node.id!r}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ConfigError(f"{ind_id}: formula constants must be numbers")


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def _parse_inputs(ind_id: str, raw: dict) -> tuple[Input, ...]:
    inputs = []
    for name, spec in (raw or {}).items():
        if spec.get("source") not in INPUT_SOURCES or "series" not in spec:
            raise ConfigError(f"{ind_id}: input {name!r} needs a valid source and a series")
        transform = _as_tuple(spec.get("transform"))
        if any(t not in TRANSFORMS for t in transform):
            raise ConfigError(f"{ind_id}: input {name!r} has an unknown transform")
        inputs.append(Input(name=name, source=spec["source"], series=str(spec["series"]), transform=transform))
    return tuple(inputs)


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
    required = ["id", "name", "category", "source", "frequency", "release_lag_days",
                "transform", "risk_when", "revised", "unit", "plausible", "explain"]
    if raw.get("source") != "derived":
        required.append("series")
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
    transform = _as_tuple(raw["transform"])
    inputs, formula = (), None
    if raw["source"] == "derived":
        if not raw.get("inputs") or not raw.get("formula"):
            raise ConfigError(f"{ind_id}: a derived indicator needs 'inputs' and 'formula'")
        inputs, formula = _parse_inputs(ind_id, raw["inputs"]), str(raw["formula"])
        check_formula(ind_id, formula, {i.name for i in inputs})
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
        series=str(raw.get("series", "")),
        frequency=raw["frequency"],
        release_lag_days=int(raw["release_lag_days"]),
        transform=transform,
        risk_when=raw["risk_when"],
        revised=bool(raw["revised"]),
        unit=raw["unit"],
        plausible=(float(lo), float(hi)),
        rules=tuple(_parse_rule(ind_id, r) for r in raw.get("rules", [])),
        explain=" ".join(str(raw["explain"]).split()),
        inputs=inputs,
        formula=formula,
    )


def load_indicators(path: Path = CONFIG_DIR / "indicators.yaml") -> list[Indicator]:
    entries = yaml.safe_load(path.read_text()) or []
    indicators = [_parse_indicator(e) for e in entries]
    ids = [i.id for i in indicators]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConfigError(f"duplicate indicator ids: {sorted(dupes)}")
    return indicators


def load_weights(path: Path = CONFIG_DIR / "weights.yaml") -> dict[str, float]:
    weights = yaml.safe_load(path.read_text()) or {}
    if set(weights) != set(CATEGORIES):
        raise ConfigError(f"weights.yaml must list exactly these categories: {sorted(CATEGORIES)}")
    if any(not isinstance(w, (int, float)) or w < 0 for w in weights.values()):
        raise ConfigError("weights must be non-negative numbers")
    if sum(weights.values()) <= 0:
        raise ConfigError("at least one weight must be positive")
    return {k: float(v) for k, v in weights.items()}


def load_untracked(path: Path = CONFIG_DIR / "untracked.yaml") -> list[Untracked]:
    entries = yaml.safe_load(path.read_text()) or []
    out = []
    for e in entries:
        missing = [k for k in ("name", "why_it_matters", "why_not_tracked") if not str(e.get(k, "")).strip()]
        if missing:
            raise ConfigError(f"untracked entry {e.get('name')!r} missing {missing}")
        out.append(Untracked(**{k: " ".join(str(e[k]).split()) for k in ("name", "why_it_matters", "why_not_tracked")}))
    return out
