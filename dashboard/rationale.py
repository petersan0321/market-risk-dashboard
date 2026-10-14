"""Step 5: explanations behind every score (spec §6)."""

import numpy as np
import pandas as pd

from dashboard.composite import BANDS, band, band_floor, overall_from_latest
from dashboard.config import CATEGORIES, Indicator
from dashboard.returns import forward_max_drawdown, forward_return
from dashboard.signals import AMBER, RED, TREND_LOOKBACK_DAYS, light, trend

HORIZONS = {"3m": 63, "6m": 126, "12m": 252}
BIG_DROP = 0.15
RED_SCORE, GREEN_SCORE = 86.0, 59.0  # scores used for "what if this indicator turned red/green"


# --- outcomes: what the overall score has meant historically (§6.2) -------------------

def outcome_by_band(composite: pd.DataFrame, stocks: pd.Series, bonds: pd.Series) -> list[dict]:
    """For each band: how stocks and bonds did over the following months. Only days whose full
    12-month forward window has already happened are counted."""
    df = pd.DataFrame({"band": composite["band"]})
    for name, n in HORIZONS.items():
        df[f"spx_{name}"] = forward_return(stocks, n)
    df["drawdown_12m"] = forward_max_drawdown(stocks, HORIZONS["12m"])
    df["bond_12m"] = forward_return(bonds, HORIZONS["12m"])
    df = df.dropna(subset=["band", "spx_12m", "drawdown_12m", "bond_12m"])
    rows = []
    for _, name in reversed(BANDS):
        part = df[df["band"] == name]
        rows.append({
            "band": name,
            "days": int(len(part)),
            "since": part.index.min().date().isoformat() if len(part) else None,
            "spx_3m": _median(part["spx_3m"]),
            "spx_6m": _median(part["spx_6m"]),
            "spx_12m": _median(part["spx_12m"]),
            "big_drop_rate": float((part["drawdown_12m"] >= BIG_DROP).mean()) if len(part) else None,
            "bond_12m": _median(part["bond_12m"]),
        })
    return rows


def _median(s: pd.Series) -> float | None:
    return float(s.median()) if len(s) else None


# --- indicators (§6.1) -------------------------------------------------------------------

def value_at_score(ind: Indicator, history: pd.Series, score: float) -> list[float]:
    """The reading(s) at which an indicator would have this risk score today."""
    q = score / 100
    if ind.risk_when == "high":
        return [float(history.quantile(q))]
    if ind.risk_when == "low":
        return [float(history.quantile(1 - q))]
    return [float(history.quantile(0.5 - q / 2)), float(history.quantile(0.5 + q / 2))]


def describe_risk(ind: Indicator, score: float, since_year: int) -> str:
    if ind.risk_when == "extreme":
        return f"Further from normal than {score:.0f}% of days since {since_year}."
    return f"Riskier than {score:.0f}% of days since {since_year}."


def indicator_detail(ind: Indicator, values: pd.Series, scores: pd.Series, stale: bool,
                     rule_label: str, revised_note: bool) -> dict:
    history = values.dropna()
    scored = scores.dropna()
    if history.empty:
        return {"id": ind.id, "available": False}
    score = float(scored.iloc[-1]) if len(scored) else None
    past = scored.iloc[-1 - TREND_LOOKBACK_DAYS] if len(scored) > TREND_LOOKBACK_DAYS else None
    since_year = history.index[0].year
    return {
        "id": ind.id,
        "available": True,
        "value": float(history.iloc[-1]),
        "score": score,
        "score_3m_ago": None if past is None else float(past),
        "light": "none" if stale else light(score),
        "trend": trend(scored),
        "stale": stale,
        "rule_label": rule_label,
        "sentence": describe_risk(ind, score, since_year) if score is not None else
                    "Not enough history yet to score (needs 5 years).",
        "spectrum": {
            "min": float(history.min()),
            "max": float(history.max()),
            "median": float(history.median()),
            "amber": value_at_score(ind, history, AMBER),
            "red": value_at_score(ind, history, RED),
            "since": history.index[0].date().isoformat(),
        },
        "data_note": "Revised data" if revised_note else "Market or survey data, not revised",
    }


# --- drivers and thresholds (§6.3-6.4) -----------------------------------------------------

def contributions_now(composite: pd.DataFrame) -> list[dict]:
    row = composite.iloc[-1]
    items = [{"category": c, "name": CATEGORIES[c], "contribution": float(row[f"contrib_{c}"])}
             for c in CATEGORIES if pd.notna(row.get(f"contrib_{c}"))]
    return sorted(items, key=lambda x: -abs(x["contribution"]))


def change_attribution(composite: pd.DataFrame, periods: int) -> dict | None:
    valid = composite.dropna(subset=["overall"])
    if len(valid) <= periods:
        return None
    now, then = valid.iloc[-1], valid.iloc[-1 - periods]
    parts = [{"category": c, "name": CATEGORIES[c],
              "change": float(np.nan_to_num(now[f"contrib_{c}"]) - np.nan_to_num(then[f"contrib_{c}"]))}
             for c in CATEGORIES]
    parts = [p for p in parts if abs(p["change"]) >= 0.05]
    return {
        "from": float(then["overall"]),
        "to": float(now["overall"]),
        "change": float(now["overall"] - then["overall"]),
        "since": valid.index[-1 - periods].date().isoformat(),
        "parts": sorted(parts, key=lambda p: -abs(p["change"])),
    }


def _next_band_up(score: float) -> tuple[str, float] | None:
    above = [(floor, name) for floor, name in BANDS if floor > score]
    return (above[-1][1], above[-1][0]) if above else None


def nearest_thresholds(indicators: list[Indicator], latest: dict[str, dict], weights: dict[str, float],
                       overall: float) -> dict:
    """Which indicators are closest to changing light, and how many would have to change
    to move the overall score into the next band up or down."""
    by_id = {i.id: i for i in indicators}
    usable = {k: v for k, v in latest.items() if v.get("score") is not None and not v["stale"]}
    scores = {k: v["score"] for k, v in usable.items()}

    def light_change_value(i_id: str, target: float) -> list[float]:
        return value_at_score(by_id[i_id], usable[i_id]["history"], target)

    ups, downs = [], []
    for i_id, s in scores.items():
        if s <= RED:
            target = AMBER if s < AMBER else RED
            ups.append({"id": i_id, "gap": (target if s < AMBER else RED + 0.1) - s,
                        "to": "amber" if s < AMBER else "red",
                        "at_value": light_change_value(i_id, target)})
        if s >= AMBER:
            target = RED if s > RED else AMBER
            downs.append({"id": i_id, "gap": s - target, "to": "amber" if s > RED else "green",
                          "at_value": light_change_value(i_id, target)})

    def flips(candidates: list[str], new_score: float, reached) -> list[str] | None:
        trial, changed = dict(scores), []
        for i_id in candidates:
            trial[i_id] = new_score
            changed.append(i_id)
            if reached(overall_from_latest(trial, indicators, weights)):
                return changed
        return None

    up_band = _next_band_up(overall)
    to_red = sorted((i for i, s in scores.items() if s <= RED), key=lambda i: RED_SCORE - scores[i])
    current_floor = band_floor(band(overall))
    to_green = sorted((i for i, s in scores.items() if s >= AMBER), key=lambda i: scores[i] - GREEN_SCORE)
    return {
        "closest_up": sorted(ups, key=lambda x: x["gap"])[:3],
        "closest_down": sorted(downs, key=lambda x: x["gap"])[:3],
        "band_up": None if up_band is None else {
            "band": up_band[0],
            "indicators": flips(to_red, RED_SCORE, lambda o: o >= up_band[1]),
        },
        "band_down": None if current_floor == 0 else {
            "band": band(current_floor - 0.1),
            "indicators": flips(to_green, GREEN_SCORE, lambda o: o < current_floor),
        },
    }


# --- confidence (§6.5) ----------------------------------------------------------------------

def confidence(indicators: list[Indicator], latest: dict[str, dict], category_now: dict[str, float],
               weights: dict[str, float]) -> dict:
    fresh = sum(1 for v in latest.values() if v.get("score") is not None and not v["stale"])
    cats = {c: s for c, s in category_now.items() if pd.notna(s)}
    spread = float(np.std(list(cats.values()))) if cats else None
    agreement = ("categories broadly agree" if spread < 12 else
                 "somewhat mixed" if spread <= 20 else "mixed signal") if spread is not None else None

    # Each usable indicator's share of the overall score = category weight / (total weight × members)
    usable = [i for i in indicators if latest.get(i.id, {}).get("score") is not None and not latest[i.id]["stale"]]
    members = {c: sum(1 for i in usable if i.category == c) for c in cats}
    total_w = sum(weights[c] for c in cats)
    revised_share = sum(weights[i.category] / (total_w * members[i.category])
                        for i in usable if i.revised and i.category in cats) if total_w else 0.0
    return {
        "fresh": fresh,
        "total": len(indicators),
        "spread": spread,
        "agreement": agreement,
        "highest": max(cats, key=cats.get) if cats else None,
        "lowest": min(cats, key=cats.get) if cats else None,
        "revised_share": revised_share,
    }


# --- assemble ------------------------------------------------------------------------------

def build(indicators: list[Indicator], tables: dict[str, pd.DataFrame], composite: pd.DataFrame,
          weights: dict[str, float], stocks: pd.Series, bonds: pd.Series) -> dict:
    last_day = tables["score"].index[-1]
    latest = {}
    details = {}
    for ind in indicators:
        if ind.id not in tables["score"]:
            latest[ind.id] = {"score": None, "stale": True}
            details[ind.id] = {"id": ind.id, "available": False}
            continue
        values = tables["value"][ind.id].dropna()
        stale = bool(tables["stale"].at[last_day, ind.id])
        details[ind.id] = indicator_detail(ind, values, tables["score"][ind.id], stale,
                                           tables["rule_label"].at[last_day, ind.id], ind.revised)
        score = details[ind.id].get("score")
        latest[ind.id] = {"score": score, "stale": stale, "history": values}

    valid = composite.dropna(subset=["overall"])
    now = valid.iloc[-1]
    overall = float(now["overall"])
    category_now = {c: now[f"cat_{c}"] for c in CATEGORIES}
    history = valid["overall"]
    categories = {}
    for c in CATEGORIES:
        series = composite[f"cat_{c}"].dropna()
        categories[c] = {
            "name": CATEGORIES[c],
            "score": None if pd.isna(category_now[c]) else float(category_now[c]),
            "light": light(category_now[c]),
            "trend": trend(series),
            "members": [i.id for i in indicators if i.category == c],
        }
    return {
        "as_of": last_day.date().isoformat(),
        "overall": {
            "score": overall,
            "band": now["band"],
            "percentile": float((history <= overall).mean() * 100),
            "since": history.index[0].date().isoformat(),
            "n_categories": int(now["n_categories"]),
        },
        "outcomes": outcome_by_band(composite, stocks, bonds),
        "contributions": contributions_now(composite),
        "changes": {"7d": change_attribution(composite, 5), "30d": change_attribution(composite, 21)},
        "thresholds": nearest_thresholds(indicators, latest, weights, overall),
        "confidence": confidence(indicators, latest, category_now, weights),
        "categories": categories,
        "indicators": details,
    }
