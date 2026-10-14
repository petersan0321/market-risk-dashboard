"""Step 3: combine indicator scores into category scores and the overall risk score (spec §5.2-5.3)."""

import pandas as pd

from dashboard.config import CATEGORIES, Indicator

MIN_CATEGORIES = 5
BANDS = [(75, "High"), (60, "Elevated"), (40, "Normal"), (0, "Calm")]


def band(score: float) -> str | None:
    if pd.isna(score):
        return None
    return next(name for floor, name in BANDS if score >= floor)


def band_floor(name: str) -> float:
    return next(floor for floor, n in BANDS if n == name)


def category_scores(scores: pd.DataFrame, stale: pd.DataFrame, indicators: list[Indicator]) -> pd.DataFrame:
    """Mean of each category's available, non-stale indicator scores (date x category)."""
    usable = scores.where(~stale.reindex_like(scores).fillna(True).astype(bool))
    out = {}
    for cat in CATEGORIES:
        members = [i.id for i in indicators if i.category == cat and i.id in usable.columns]
        out[cat] = usable[members].mean(axis=1) if members else pd.Series(float("nan"), index=usable.index)
    return pd.DataFrame(out)


def overall_scores(categories: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Weighted mean of available categories; needs at least MIN_CATEGORIES.
    Columns: overall, n_categories, band, and contrib_<category> (sums to overall - 50)."""
    w = pd.Series(weights)[categories.columns]
    available = categories.notna()
    weight_sum = available.mul(w, axis=1).sum(axis=1)
    overall = categories.mul(w, axis=1).sum(axis=1, min_count=1) / weight_sum
    n = available.sum(axis=1)
    overall = overall.where(n >= MIN_CATEGORIES)
    contrib = (categories - 50).mul(w, axis=1).div(weight_sum, axis=0).where(n >= MIN_CATEGORIES, axis=0)
    out = pd.DataFrame({"overall": overall.round(1), "n_categories": n, "band": overall.map(band)})
    return out.join(contrib.add_prefix("contrib_"))


def overall_from_latest(latest_scores: dict[str, float], indicators: list[Indicator],
                        weights: dict[str, float]) -> float:
    """Overall score for a single set of indicator scores (used for what-if threshold checks)."""
    row = pd.DataFrame([latest_scores])
    stale = pd.DataFrame(False, index=row.index, columns=row.columns)
    return float(overall_scores(category_scores(row, stale, indicators), weights)["overall"].iloc[0])


def run(tables: dict[str, pd.DataFrame], indicators: list[Indicator], weights: dict[str, float],
        scores_dir) -> pd.DataFrame:
    cats = category_scores(tables["score"], tables["stale"], indicators)
    result = cats.add_prefix("cat_").join(overall_scores(cats, weights))
    result.to_csv(scores_dir / "composite.csv", date_format="%Y-%m-%d")
    return result
