"""Time machine data export (spec §8.1): one JSON file per year plus an index, loaded by the
time-machine page on demand."""

import json
import math
from pathlib import Path

import pandas as pd

from dashboard.composite import BANDS
from dashboard.config import CATEGORIES, Event, Indicator
from dashboard.regime import REGIMES


def _sig3(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return None
    return float(f"{float(v):.3g}")


def _int(v) -> int | None:
    return None if pd.isna(v) else int(round(float(v)))


def export(out_dir: Path, indicators: list[Indicator], events: list[dict], weights: dict[str, float],
           tables: dict[str, pd.DataFrame], composite: pd.DataFrame, regimes: pd.Series,
           market: pd.DataFrame) -> list[int]:
    """Write <year>.json for every year with an overall score, and index.json. Returns the years."""
    out_dir.mkdir(parents=True, exist_ok=True)
    first = composite["overall"].first_valid_index()
    days = composite.index[composite.index >= first]
    ruled = [i.id for i in indicators if i.rules and i.id in tables["rule_label"]]
    years = sorted(set(days.year))
    for year in years:
        idx = days[days.year == year]
        payload = {
            "dates": [d.date().isoformat() for d in idx],
            "overall": [_sig3(v) for v in composite.loc[idx, "overall"]],
            "pct": [_int(v) for v in composite.loc[idx, "percentile"]],
            "categories": {c: [_int(v) for v in composite.loc[idx, f"cat_{c}"]] for c in CATEGORIES},
            "indicators": {
                i.id: {"v": [_sig3(v) for v in tables["value"][i.id].reindex(idx)],
                       "s": [_int(v) for v in tables["score"][i.id].reindex(idx)]}
                for i in indicators if i.id in tables["score"]
            },
            "stale": {i_id: [int(v) for v in tables["stale"][i_id].reindex(idx, fill_value=True)]
                      for i_id in tables["stale"] if tables["stale"][i_id].reindex(idx, fill_value=True).any()},
            "rules": {i_id: [v or None for v in tables["rule_label"][i_id].reindex(idx, fill_value="")]
                      for i_id in ruled if (tables["rule_label"][i_id].reindex(idx, fill_value="") != "").any()},
            "regime": [None if pd.isna(v) else v for v in regimes.reindex(idx)],
            "spx": [_sig3(v) for v in market["stocks"].reindex(idx)],
            "bond": [_sig3(v) for v in market["bonds"].reindex(idx)],
        }
        (out_dir / f"{year}.json").write_text(json.dumps(payload, separators=(",", ":")))

    index = {
        "years": years,
        "first_date": days[0].date().isoformat(),
        "last_date": days[-1].date().isoformat(),
        "weights": weights,
        "categories": CATEGORIES,
        "bands": [{"name": n, "from": f} for f, n in BANDS],
        "regimes": {key: {"label": label, "description": desc} for key, label, desc in REGIMES.values()},
        "indicators": [{"id": i.id, "name": i.name, "category": i.category, "unit": i.unit,
                        "revised": i.revised, "explain": i.explain} for i in indicators],
        "events": [{"id": e["event"].id, "name": e["event"].name, "kind": e["event"].kind,
                    "summary": e["event"].summary, "start": e["start"].date().isoformat(),
                    "end": e["end"].date().isoformat()} for e in events],
    }
    (out_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return years
