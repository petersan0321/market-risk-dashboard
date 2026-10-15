"""Step 6: history playbook (spec §7): exact event dates, factor heatmaps, whether the score
warned, which factors warned most reliably, and which past events today resembles."""

import numpy as np
import pandas as pd

from dashboard.config import CATEGORIES, Event, Indicator
from dashboard.signals import AMBER, RED

OFFSETS = [("12 months before", -252, "start"), ("6 months before", -126, "start"), ("3 months before", -63, "start"),
           ("Start", 0, "start"), ("End", 0, "end"), ("3 months after", 63, "end"), ("6 months after", 126, "end")]
WARNING_RUN = 10  # trading days the overall score must stay Elevated or higher to count as a warning
LOOKBACK = 252  # trading days before an event searched for a warning
RED_EPISODE = 20  # trading days a light must stay red to count as an episode
CHART_BEFORE = pd.DateOffset(months=24)
CHART_AFTER = pd.DateOffset(months=12)
FUTURE = pd.Timedelta(days=365)
MIN_DIMS = 6  # minimum fingerprint dimensions to compare two days


# --- exact dates and moves -----------------------------------------------------------------

def _extreme(series: pd.Series, around: pd.Timestamp, highest: bool, search: pd.Timedelta,
             after: pd.Timestamp | None = None):
    window = series[(series.index >= around - search) & (series.index <= around + search)].dropna()
    if after is not None:
        window = window[window.index > after]
    if window.empty:
        return None
    return window.idxmax() if highest else window.idxmin()


def locate(event: Event, stocks: pd.Series, bonds: pd.Series) -> dict | None:
    """Exact start/end: the peak and trough (crash) or trough and peak (rally) near the anchors."""
    series = bonds if event.assets == ("bonds",) else stocks
    a_start, a_end = pd.Timestamp(event.start), pd.Timestamp(event.end)
    if event.kind == "event":
        days = series.dropna().index
        pos_s, pos_e = days.searchsorted(a_start), days.searchsorted(a_end, side="right") - 1
        if pos_s >= len(days) or pos_e < 0:
            return None
        start, end = days[pos_s], days[pos_e]
    else:
        search = pd.Timedelta(days=event.search_days)
        start = _extreme(series, a_start, highest=event.kind == "crash", search=search)
        end = None if start is None else _extreme(series, a_end, highest=event.kind == "rally", search=search, after=start)
        if start is None or end is None:
            return None
    return {
        "start": start,
        "end": end,
        "stocks_move": _move(stocks, start, end),
        "bonds_move": _move(bonds, start, end),
    }


def _move(series: pd.Series, start, end) -> float | None:
    a, b = series.asof(start), series.asof(end)
    return None if pd.isna(a) or pd.isna(b) else float(b / a - 1)


def _offset_day(index: pd.DatetimeIndex, day: pd.Timestamp, n: int) -> pd.Timestamp | None:
    pos = index.searchsorted(day, side="right") - 1 + n
    return index[pos] if 0 <= pos < len(index) else None


# --- per-event analysis ---------------------------------------------------------------------

def heatmap(start, end, scores: pd.DataFrame, categories: pd.DataFrame, indicators: list[Indicator]) -> dict:
    days = []
    for label, n, anchor in OFFSETS:
        days.append((label, _offset_day(scores.index, start if anchor == "start" else end, n)))

    def cells(series: pd.Series) -> list:
        return [None if d is None or pd.isna(series.get(d)) else round(float(series[d]), 1) for _, d in days]

    rows = [{"id": i.id, "category": i.category, "cells": cells(scores[i.id])}
            for i in indicators if i.id in scores]
    cat_rows = [{"id": c, "cells": cells(categories[f"cat_{c}"])} for c in CATEGORIES]
    return {"columns": [label for label, _ in days],
            "dates": [None if d is None else d.date().isoformat() for _, d in days],
            "rows": rows, "categories": cat_rows}


def warning(overall: pd.Series, start: pd.Timestamp) -> dict:
    """When the overall score first entered Elevated or High (and stayed ≥ WARNING_RUN days)
    in the year before the event started."""
    history = overall.dropna()
    pos = history.index.searchsorted(start, side="right") - 1
    if pos < 0:
        return {"warned": None, "lead_days": None, "first_date": None, "already_elevated": False}
    window = history.iloc[max(0, pos - LOOKBACK): pos + 1]
    elevated = (window >= 60).to_numpy()
    run = 0
    for i, flag in enumerate(elevated):
        run = run + 1 if flag else 0
        if run >= WARNING_RUN:
            first = i - run + 1
            return {"warned": True, "lead_days": int(len(window) - 1 - first),
                    "first_date": window.index[first].date().isoformat(),
                    "already_elevated": first == 0}
    return {"warned": False, "lead_days": None, "first_date": None, "already_elevated": False}


def what_it_showed(hm: dict, indicators: list[Indicator]) -> dict:
    """Generated 'what the dashboard showed' summary: red and amber indicators before the start."""
    by_id = {i.id: i for i in indicators}
    out = {}
    for col, label in ((1, "6 months before"), (2, "3 months before")):
        red = [r["id"] for r in hm["rows"] if r["cells"][col] is not None and r["cells"][col] > RED]
        amber = [r["id"] for r in hm["rows"] if r["cells"][col] is not None and AMBER <= r["cells"][col] <= RED]
        out[label] = {"red": red, "amber": amber, "scored": sum(r["cells"][col] is not None for r in hm["rows"])}
    lit = out["3 months before"]["red"] + out["3 months before"]["amber"]
    out["revised_share"] = (sum(by_id[i].revised for i in lit) / len(lit)) if lit else None
    return out


def chart_window(start, end, stocks: pd.Series, bonds: pd.Series, overall: pd.Series) -> dict:
    lo, hi = start - CHART_BEFORE, end + CHART_AFTER
    frame = pd.DataFrame({"stocks": stocks, "bonds": bonds, "overall": overall})
    frame = frame[(frame.index >= lo) & (frame.index <= hi)].ffill()
    if frame.empty:
        return {"x": []}
    first = frame.iloc[0]
    return {
        "x": [d.date().isoformat() for d in frame.index],
        "stocks": _round(frame["stocks"] / first["stocks"] * 100) if pd.notna(first["stocks"]) else [],
        "bonds": _round(frame["bonds"] / first["bonds"] * 100) if pd.notna(first["bonds"]) else [],
        "overall": _round(frame["overall"]),
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
    }


def _round(series: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 2) for v in series]


# --- across events --------------------------------------------------------------------------

def _runs(mask: pd.Series, min_len: int) -> list[pd.Timestamp]:
    """Start dates of runs of True at least min_len long."""
    starts, run_start, length = [], None, 0
    for day, flag in mask.items():
        if flag:
            run_start = day if length == 0 else run_start
            length += 1
            if length == min_len:
                starts.append(run_start)
        else:
            length = 0
    return starts


def which_factors_warned(located: list[dict], scores: pd.DataFrame, indicators: list[Indicator],
                         today: pd.Timestamp) -> list[dict]:
    crashes = [e for e in located if e["event"].kind == "crash"]
    windows = [(e["start"], e["end"]) for e in crashes]
    rows = []
    for ind in indicators:
        if ind.id not in scores:
            continue
        s = scores[ind.id]
        with_data = hits = 0
        for e in crashes:
            before = [s.get(_offset_day(s.index, e["start"], n)) for n in (-126, -63)]
            before = [v for v in before if v is not None and pd.notna(v)]
            if not before:
                continue
            with_data += 1
            hits += any(v >= AMBER for v in before)
        episodes = [d for d in _runs(s > RED, RED_EPISODE) if d <= today - FUTURE]
        false = [d for d in episodes
                 if not any(st - FUTURE <= d <= en or d < st <= d + FUTURE for st, en in windows)]
        rows.append({
            "id": ind.id,
            "events": with_data,
            "hits": hits,
            "hit_rate": hits / with_data if with_data else None,
            "red_episodes": len(episodes),
            "false_alarms": len(false),
            "false_alarm_rate": len(false) / len(episodes) if episodes else None,
        })
    return sorted(rows, key=lambda r: (-(r["hit_rate"] or -1), r["false_alarm_rate"] or 0))


def fingerprints(composite: pd.DataFrame) -> pd.DataFrame:
    cats = composite[[f"cat_{c}" for c in CATEGORIES]]
    return pd.concat([cats, (cats - cats.shift(63)).add_suffix("_chg")], axis=1)


def _distance(matrix: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    diff = matrix - target
    n = (~np.isnan(diff)).sum(axis=1)
    sq = np.nansum(diff ** 2, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = np.sqrt(sq / n)
    dist[n < MIN_DIMS] = np.nan
    return dist, n


def today_looks_like(composite: pd.DataFrame, located: list[dict], today: pd.Timestamp) -> dict:
    """Closest past pre-event readings to today (root-mean-square distance over the 9 category
    scores and their 3-month changes), and how often similar readings came with no crash."""
    fp = fingerprints(composite)
    valid = fp.dropna(how="all")
    target = valid.iloc[-1].to_numpy(dtype=float)
    candidates = []
    for e in located:
        if e["event"].kind not in ("crash", "rally"):
            continue
        for label, n in (("6 months before", -126), ("3 months before", -63)):
            day = _offset_day(fp.index, e["start"], n)
            if day is None:
                continue
            dist, dims = _distance(fp.loc[[day]].to_numpy(dtype=float), target)
            if np.isnan(dist[0]):
                continue
            candidates.append({"event": e["event"].id, "offset": label, "date": day.date().isoformat(),
                               "distance": float(dist[0]), "dims": int(dims[0])})
    best = {}  # closest offset per event, so the three matches are three different events
    for c in sorted(candidates, key=lambda c: c["distance"]):
        best.setdefault(c["event"], c)
    matches = list(best.values())[:3]
    if not matches:
        return {"matches": [], "similar_days": 0, "no_crash_rate": None}

    crash_starts = [e["start"] for e in located if e["event"].kind == "crash"]
    history = fp[fp.index <= today - FUTURE]
    dist, _ = _distance(history.to_numpy(dtype=float), target)
    similar = history.index[dist <= matches[-1]["distance"]]
    no_crash = [d for d in similar if not any(d < s <= d + FUTURE for s in crash_starts)]
    return {
        "matches": matches,
        "similar_days": int(len(similar)),
        "no_crash_rate": len(no_crash) / len(similar) if len(similar) else None,
    }


def event_markers(ind: Indicator, values: pd.Series, located: list[dict], limit: int = 3) -> list[dict]:
    """The indicator's most extreme reading (in its risky direction) during up to 3 crash events."""
    marks = []
    for e in located:
        if e["event"].kind != "crash":
            continue
        window = values[(values.index >= e["start"]) & (values.index <= e["end"])].dropna()
        if window.empty:
            continue
        if ind.risk_when == "high":
            value = window.max()
        elif ind.risk_when == "low":
            value = window.min()
        else:
            value = window.loc[(window - values.median()).abs().idxmax()]
        marks.append({"event": e["event"].id, "name": e["event"].name, "year": e["start"].year,
                      "value": float(value)})
    median = values.median()
    return sorted(marks, key=lambda m: -abs(m["value"] - median))[:limit]


# --- assemble -------------------------------------------------------------------------------

def build(events: list[Event], indicators: list[Indicator], scores: pd.DataFrame, composite: pd.DataFrame,
          stocks: pd.Series, bonds: pd.Series) -> tuple[dict, list[dict]]:
    """Returns (history.json content, located events for other steps)."""
    today = composite.index[-1]
    located, pages = [], []
    for ev in events:
        found = locate(ev, stocks, bonds)
        if found is None:
            continue
        found["event"] = ev
        located.append(found)
        hm = heatmap(found["start"], found["end"], scores, composite, indicators)
        pages.append({
            "id": ev.id,
            "start": found["start"].date().isoformat(),
            "end": found["end"].date().isoformat(),
            "stocks_move": found["stocks_move"],
            "bonds_move": found["bonds_move"],
            "heatmap": hm,
            "showed": what_it_showed(hm, indicators),
            "warning": warning(composite["overall"], found["start"]) if ev.kind == "crash" else None,
            "chart": chart_window(found["start"], found["end"], stocks, bonds, composite["overall"]),
        })
    return {
        "events": pages,
        "factors": which_factors_warned(located, scores, indicators, today),
        "looks_like": today_looks_like(composite, located, today),
    }, located
