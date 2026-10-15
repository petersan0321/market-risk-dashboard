"""Load and validate the YAML config. Invalid config fails fast with a clear message."""

import ast
import re
from dataclasses import dataclass
from datetime import date
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


# --- events and glossary (spec §7.1, §10.4) --------------------------------------------

EVENT_KINDS = {"crash", "rally", "event"}
EVENT_ASSETS = {"stocks", "bonds"}
EVENT_TAGS = {"fed", "rates", "inflation", "credit", "banking", "policy", "election", "geopolitics",
              "valuation", "global"}
EXPLAIN_FIELDS = ("background", "chain", "why_stocks", "why_bonds", "lesson")


@dataclass(frozen=True)
class GlossaryTerm:
    id: str
    term: str
    definition: str


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    kind: str
    assets: tuple[str, ...]
    start: date
    end: date
    tags: tuple[str, ...]
    summary: str
    background: str
    chain: tuple[str, ...]
    why_stocks: str
    why_bonds: str
    lesson: str
    terms: tuple[str, ...]
    search_days: int = 30  # how far from the anchors to look for the exact peak/trough


def _text(value) -> str:
    return " ".join(str(value).split())


def load_glossary(path: Path = CONFIG_DIR / "glossary.yaml") -> dict[str, GlossaryTerm]:
    entries = yaml.safe_load(path.read_text()) or {}
    out = {}
    for term_id, e in entries.items():
        if not str(e.get("term", "")).strip() or not str(e.get("definition", "")).strip():
            raise ConfigError(f"glossary {term_id!r} needs 'term' and 'definition'")
        out[term_id] = GlossaryTerm(term_id, _text(e["term"]), _text(e["definition"]))
    return out


def load_events(path: Path = CONFIG_DIR / "events.yaml",
                glossary: dict[str, GlossaryTerm] | None = None) -> list[Event]:
    glossary = load_glossary() if glossary is None else glossary
    events, seen = [], set()
    for e in yaml.safe_load(path.read_text()) or []:
        ev_id = e.get("id", "<missing id>")
        if ev_id in seen:
            raise ConfigError(f"duplicate event id {ev_id!r}")
        seen.add(ev_id)
        missing = [k for k in ("id", "name", "kind", "assets", "anchor", "tags", "summary", "explain", "terms")
                   if not e.get(k)]
        if missing:
            raise ConfigError(f"event {ev_id}: missing {missing}")
        explain = e["explain"]
        missing = [k for k in EXPLAIN_FIELDS if not explain.get(k)]
        if missing:
            raise ConfigError(f"event {ev_id}: explain is missing {missing}")
        if e["kind"] not in EVENT_KINDS:
            raise ConfigError(f"event {ev_id}: unknown kind {e['kind']!r}")
        if not set(e["assets"]) <= EVENT_ASSETS:
            raise ConfigError(f"event {ev_id}: unknown assets {e['assets']}")
        if not set(e["tags"]) <= EVENT_TAGS:
            raise ConfigError(f"event {ev_id}: unknown tags {sorted(set(e['tags']) - EVENT_TAGS)}")
        unknown_terms = [t for t in e["terms"] if t not in glossary]
        if unknown_terms:
            raise ConfigError(f"event {ev_id}: terms not in glossary {unknown_terms}")
        start, end = e["anchor"]["start"], e["anchor"]["end"]
        if not (isinstance(start, date) and isinstance(end, date) and start < end):
            raise ConfigError(f"event {ev_id}: anchor needs start < end dates")
        events.append(Event(
            id=ev_id, name=e["name"], kind=e["kind"], assets=tuple(e["assets"]), start=start, end=end,
            tags=tuple(e["tags"]), summary=_text(e["summary"]), background=_text(explain["background"]),
            chain=tuple(_text(c) for c in explain["chain"]), why_stocks=_text(explain["why_stocks"]),
            why_bonds=_text(explain["why_bonds"]), lesson=_text(explain["lesson"]), terms=tuple(e["terms"]),
            search_days=int(e.get("search_days", 30)),
        ))
    return events


# --- calendar and news (spec §10) -----------------------------------------------------------

CALENDAR_SOURCES = {"manual", "fred", "treasury"}


@dataclass(frozen=True)
class CalendarType:
    type: str
    name: str
    source: str
    why_it_matters: str
    what_to_watch: str
    typical_reaction: str
    related_indicators: tuple[str, ...]
    recurring: bool = False
    fred_release_id: int | None = None


@dataclass(frozen=True)
class CalendarDate:
    type: str
    date: date
    note: str = ""


@dataclass(frozen=True)
class NewsTopic:
    id: str
    name: str
    keywords: tuple[str, ...]
    why_it_matters: str
    related_indicators: tuple[str, ...]


def _check_indicators(owner: str, ids, known: set[str]) -> tuple[str, ...]:
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise ConfigError(f"{owner}: related_indicators not in the catalog {unknown}")
    return tuple(ids)


def load_calendar(path: Path = CONFIG_DIR / "calendar.yaml", indicator_ids: set[str] | None = None
                  ) -> tuple[dict[str, CalendarType], list[CalendarDate]]:
    raw = yaml.safe_load(path.read_text())
    known = indicator_ids if indicator_ids is not None else {i.id for i in load_indicators()}
    types = {}
    for t in raw.get("types", []):
        missing = [k for k in ("type", "name", "source", "why_it_matters", "what_to_watch", "typical_reaction")
                   if not str(t.get(k, "")).strip()]
        if missing:
            raise ConfigError(f"calendar type {t.get('type')!r} missing {missing}")
        if t["source"] not in CALENDAR_SOURCES:
            raise ConfigError(f"calendar type {t['type']!r}: unknown source {t['source']!r}")
        if t["source"] == "fred" and not t.get("fred_release_id"):
            raise ConfigError(f"calendar type {t['type']!r}: fred source needs fred_release_id")
        types[t["type"]] = CalendarType(
            type=t["type"], name=t["name"], source=t["source"], why_it_matters=_text(t["why_it_matters"]),
            what_to_watch=_text(t["what_to_watch"]), typical_reaction=_text(t["typical_reaction"]),
            related_indicators=_check_indicators(t["type"], t.get("related_indicators", []), known),
            recurring=bool(t.get("recurring", False)), fred_release_id=t.get("fred_release_id"))
    dates = []
    for d in raw.get("dates", []):
        if d.get("type") not in types or types[d["type"]].source != "manual":
            raise ConfigError(f"calendar date {d}: type must be a manual calendar type")
        if not isinstance(d.get("date"), date):
            raise ConfigError(f"calendar date {d}: needs a date")
        dates.append(CalendarDate(d["type"], d["date"], str(d.get("note", ""))))
    return types, sorted(dates, key=lambda d: d.date)


def load_news_topics(path: Path = CONFIG_DIR / "news_topics.yaml", indicator_ids: set[str] | None = None
                     ) -> tuple[list[dict], list[NewsTopic]]:
    """Returns (feeds [{name, url}], topics)."""
    raw = yaml.safe_load(path.read_text())
    known = indicator_ids if indicator_ids is not None else {i.id for i in load_indicators()}
    topics = []
    for t in raw.get("topics", []):
        missing = [k for k in ("id", "name", "keywords", "why_it_matters") if not t.get(k)]
        if missing:
            raise ConfigError(f"news topic {t.get('id')!r} missing {missing}")
        topics.append(NewsTopic(t["id"], t["name"], tuple(k.lower() for k in t["keywords"]),
                                _text(t["why_it_matters"]),
                                _check_indicators(t["id"], t.get("related_indicators", []), known)))
    feeds = raw.get("feeds") or []
    if not feeds or not all(f.get("name") and str(f.get("url", "")).startswith("https://") for f in feeds):
        raise ConfigError("news_topics.yaml needs a feeds list of {name, url} with https URLs")
    return feeds, topics
