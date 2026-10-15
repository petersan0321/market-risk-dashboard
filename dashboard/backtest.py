"""Step 7: model report card (spec §9) — how well has the overall score worked, how sure can we be,
is it robust, and does it beat simple alternatives?

`python -m dashboard.backtest --weights alt.yaml ...` compares an alternative setup (§9.8)."""

import math
from collections.abc import Callable

import numpy as np
import pandas as pd

from dashboard.composite import BAND_NAMES, FLOORS, band_for, overall_scores
from dashboard.config import CATEGORIES
from dashboard.history import warning
from dashboard.returns import forward_max_drawdown

HORIZON = 252
STOCK_DROP, BOND_DROP = 0.15, 0.05
BLOCK = 252
REPS = 1000
SEED = 42
Z90 = 1.645
EPISODE = 20
PERIODS = {"1990–2007": ("1990-01-01", "2007-12-31"), "2008–today": ("2008-01-01", None)}
HELD_OUT_START = "2015-01-01"
BASELINES = ["curve_10y3m", "vix", "baa_spread"]
GRADES = [(0.8, "strong"), (0.7, "useful"), (0.6, "modest"), (0.0, "weak")]


# --- outcome labels (§9.1) -------------------------------------------------------------------

def outcome_labels(stocks: pd.Series, bonds: pd.Series) -> pd.DataFrame:
    """1.0 if a big drop followed within 12 months, 0.0 if not, NaN if not known yet."""
    dd_s, dd_b = forward_max_drawdown(stocks, HORIZON), forward_max_drawdown(bonds, HORIZON)
    return pd.DataFrame({
        "stocks": (dd_s >= STOCK_DROP).astype(float).where(dd_s.notna()),
        "bonds": (dd_b >= BOND_DROP).astype(float).where(dd_b.notna()),
    })


# --- metrics (§9.2) ----------------------------------------------------------------------------

def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """Chance a random day followed by trouble scored higher than a random day that wasn't
    (ties count half). 0.5 = coin flip. Scores are binned to 0.1 points, which makes it fast."""
    ok = ~(np.isnan(scores) | np.isnan(labels))
    s, y = scores[ok], labels[ok]
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    bins = 1001
    p = np.bincount(np.clip(np.rint(pos * 10), 0, bins - 1).astype(int), minlength=bins)
    n = np.bincount(np.clip(np.rint(neg * 10), 0, bins - 1).astype(int), minlength=bins)
    below = np.cumsum(n) - n
    return float((p * (below + 0.5 * n)).sum() / (len(pos) * len(neg)))


def grade(value: float | None) -> str | None:
    return None if value is None else next(name for floor, name in GRADES if value >= floor)


def band_rates(bands: np.ndarray, labels: np.ndarray) -> dict[str, float | None]:
    out = {}
    for name in BAND_NAMES:
        y = labels[(bands == name) & ~np.isnan(labels)]
        out[name] = float(y.mean()) if len(y) else None
    return out


def rises(rates: dict) -> bool:
    values = [rates[b] for b in BAND_NAMES if rates[b] is not None]
    return len(values) >= 2 and all(a < b for a, b in zip(values, values[1:]))


def event_hits(overall: pd.Series, located: list[dict], threshold: float = 60) -> dict:
    rows = []
    for e in located:
        if e["event"].kind != "crash":
            continue
        w = warning(overall, e["start"], threshold)
        if w["warned"] is None:
            continue
        rows.append({"event": e["event"].id, "warned": bool(w["warned"]), "lead_days": w["lead_days"]})
    hits = [r for r in rows if r["warned"]]
    leads = [r["lead_days"] for r in hits if r["lead_days"] is not None]
    return {"rows": rows, "hits": len(hits), "events": len(rows),
            "rate": len(hits) / len(rows) if rows else None,
            "interval": wilson(len(hits), len(rows)),
            "median_lead": float(np.median(leads)) if leads else None}


def false_alarms(overall: pd.Series, bad_stock: pd.Series, floor: float = 75) -> dict:
    """High-band episodes (20+ days) not followed by a 15%+ stock drop within 12 months."""
    high = overall >= floor
    starts, run = [], 0
    for day, flag in high.items():
        run = run + 1 if flag else 0
        if run == EPISODE:
            starts.append(overall.index[overall.index.get_loc(day) - EPISODE + 1])
    known = [d for d in starts if pd.notna(bad_stock.get(d))]
    false = [d for d in known if bad_stock[d] == 0]
    span_years = (overall.dropna().index[-1] - overall.dropna().index[0]).days / 365.25 if overall.notna().any() else 0
    return {"episodes": len(known), "false": len(false),
            "per_decade": len(false) / span_years * 10 if span_years else None,
            "dates": [d.date().isoformat() for d in false]}


def wilson(k: int, n: int, z: float = Z90) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, centre - half), min(1.0, centre + half)]


# --- confidence (§9.3) ---------------------------------------------------------------------------

def block_indices(n: int, rng: np.random.Generator, block: int = BLOCK) -> np.ndarray:
    """Moving-block bootstrap: stitch random year-long stretches together, so the strong
    day-to-day dependence in the data is kept."""
    starts = rng.integers(0, max(1, n - block + 1), size=math.ceil(n / block))
    return np.concatenate([np.arange(s, min(s + block, n)) for s in starts])[:n]


def bootstrap(n: int, stats: Callable[[np.ndarray], dict], reps: int = REPS, seed: int = SEED) -> dict:
    """90% intervals for every statistic returned by `stats(indices)`."""
    rng = np.random.default_rng(seed)
    samples: dict[str, list] = {}
    for _ in range(reps):
        for key, value in stats(block_indices(n, rng)).items():
            if value is not None:
                samples.setdefault(key, []).append(value)
    return {k: [float(np.percentile(v, 5)), float(np.percentile(v, 95))] for k, v in samples.items() if len(v) >= reps // 2}


# --- evaluation ------------------------------------------------------------------------------------

def _known(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.dropna(subset=["overall"]).loc[lambda f: f["stocks"].notna()]


def day_metrics(frame: pd.DataFrame, floors=FLOORS, baselines: list[str] = (), with_ci: bool = True) -> dict:
    """AUC (stocks, bonds), band rates, and baseline comparisons, with bootstrap intervals."""
    f = _known(frame)
    score = f["overall"].to_numpy(float)
    bands = np.array([band_for(s, floors) for s in score], dtype=object)
    y_s, y_b = f["stocks"].to_numpy(float), f["bonds"].to_numpy(float)
    base = {b: f[f"base_{b}"].to_numpy(float) for b in baselines}

    def stats(idx: np.ndarray) -> dict:
        out = {"auc_stocks": auc(score[idx], y_s[idx]), "auc_bonds": auc(score[idx], y_b[idx])}
        for name, rate in band_rates(bands[idx], y_s[idx]).items():
            out[f"rate_{name}"] = rate
        for b, s in base.items():
            a_model, a_base = auc(score[idx], y_s[idx]), auc(s[idx], y_s[idx])
            out[f"diff_{b}"] = None if a_model is None or a_base is None else a_model - a_base
            out[f"auc_{b}"] = a_base
        return out

    point = stats(np.arange(len(f)))
    ci = bootstrap(len(f), stats) if with_ci and len(f) > BLOCK else {}
    rates = {b: point[f"rate_{b}"] for b in BAND_NAMES}
    days = {b: int((bands == b).sum()) for b in BAND_NAMES}
    return {
        "days": int(len(f)),
        "from": f.index[0].date().isoformat() if len(f) else None,
        "to": f.index[-1].date().isoformat() if len(f) else None,
        "auc_stocks": point["auc_stocks"], "auc_stocks_ci": ci.get("auc_stocks"), "grade": grade(point["auc_stocks"]),
        "auc_bonds": point["auc_bonds"], "auc_bonds_ci": ci.get("auc_bonds"), "grade_bonds": grade(point["auc_bonds"]),
        "bands": [{"band": b, "days": days[b], "rate": rates[b], "ci": ci.get(f"rate_{b}")} for b in BAND_NAMES],
        "band_check": rises(rates),
        "baselines": [{"id": b, "auc": point[f"auc_{b}"], "diff": point[f"diff_{b}"], "diff_ci": ci.get(f"diff_{b}")}
                      for b in baselines],
    }


def evaluation_frame(composite: pd.DataFrame, scores: pd.DataFrame, labels: pd.DataFrame,
                     baselines: list[str] = BASELINES) -> pd.DataFrame:
    frame = pd.DataFrame({"overall": composite["overall"]}).join(labels)
    for b in baselines:
        if b in scores:
            frame[f"base_{b}"] = scores[b].reindex(frame.index)
    return frame


def _slice(frame: pd.DataFrame, start: str, end: str | None) -> pd.DataFrame:
    return frame[(frame.index >= start) & ((frame.index <= end) if end else True)]


def evaluate(composite: pd.DataFrame, scores: pd.DataFrame, labels: pd.DataFrame, located: list[dict],
             weights: dict[str, float], floors=FLOORS, with_robustness: bool = True) -> dict:
    baselines = [b for b in BASELINES if b in scores]
    frame = evaluation_frame(composite, scores, labels, baselines)
    overall = composite["overall"]
    result = {
        "definitions": {"stock_drop": STOCK_DROP, "bond_drop": BOND_DROP, "horizon_days": HORIZON,
                        "block": BLOCK, "reps": REPS},
        "all": day_metrics(frame, floors, baselines),
        "events": event_hits(overall, located, floors[1]),
        "false_alarms": false_alarms(overall, frame["stocks"], floors[2]),
        "held_out": day_metrics(_slice(frame, HELD_OUT_START, None), floors, with_ci=False),
    }
    if not with_robustness:
        return result
    result["periods"] = {name: day_metrics(_slice(frame, a, b), floors, with_ci=False) for name, (a, b) in PERIODS.items()}

    cats = composite[[f"cat_{c}" for c in CATEGORIES]].rename(columns=lambda c: c[4:])
    loco = []
    for c in CATEGORIES:
        rest = cats.drop(columns=c)
        alt = overall_scores(rest, {k: v for k, v in weights.items() if k != c})["overall"]
        f = frame.assign(overall=alt)
        known = _known(f)
        loco.append({"category": c,
                     "auc": auc(known["overall"].to_numpy(float), known["stocks"].to_numpy(float)),
                     "hits": event_hits(alt, located, floors[1])["hits"]})
    result["leave_one_out"] = loco

    result["sensitivity"] = []
    for shift in (-5, 5):
        shifted = tuple(x + shift for x in floors)
        m = day_metrics(frame, shifted, with_ci=False)
        result["sensitivity"].append({"shift": shift, "band_check": m["band_check"], "bands": m["bands"],
                                      "hits": event_hits(overall, located, shifted[1])["hits"],
                                      "false_alarms": false_alarms(overall, frame["stocks"], shifted[2])["false"]})
    return result


def today_confidence(report: dict, band_now: str) -> dict:
    row = next(b for b in report["all"]["bands"] if b["band"] == band_now)
    return {"band": band_now, "rate": row["rate"], "ci": row["ci"],
            "auc": report["all"]["auc_stocks"], "grade": report["all"]["grade"]}


def summary(report: dict, sim: dict | None, names: dict[str, str] | None = None) -> dict:
    """Plain-English strengths and weaknesses, generated from the numbers."""
    names = names or {}
    good, bad = [], []
    a = report["all"]
    if a["band_check"]:
        good.append("Higher bands have been followed by big stock drops more often, so the levels mean something.")
    else:
        bad.append("Big stock drops did not become steadily more common from Calm to High, so the bands are unreliable.")
    if a["auc_stocks"] is not None:
        line = f"Its accuracy at separating risky from calm periods is {a['auc_stocks']:.2f} ({a['grade']}; 0.5 is a coin flip)."
        (good if a["auc_stocks"] >= 0.6 else bad).append(line)
    e = report["events"]
    if e["events"]:
        line = f"It warned (Elevated or higher for 10+ days) before {e['hits']} of {e['events']} crashes."
        (good if (e["rate"] or 0) >= 0.5 else bad).append(line)
    fa = report["false_alarms"]
    if fa["episodes"]:
        if fa["false"] == 0:
            good.append(f"All {fa['episodes']} times it stayed High for 20+ days, a 15%+ stock drop followed within 12 months (no false alarms).")
        else:
            line = f"{fa['false']} of {fa['episodes']} times it stayed High for 20+ days, no 15%+ drop followed (false alarms)."
            (bad if fa["false"] > fa["episodes"] / 2 else good).append(line)
    label = lambda i: names.get(i, i)  # noqa: E731
    clearly_better = [label(b["id"]) for b in a["baselines"] if b["diff_ci"] and b["diff_ci"][0] > 0]
    clearly_worse = [label(b["id"]) for b in a["baselines"] if b["diff_ci"] and b["diff_ci"][1] < 0]
    unclear = [label(b["id"]) for b in a["baselines"] if label(b["id"]) not in clearly_better + clearly_worse]
    if clearly_worse:
        bad.append(f"A single indicator ({', '.join(clearly_worse)}) separated risky periods clearly better than the combined score.")
    if clearly_better:
        good.append(f"It separated risky periods clearly better than {', '.join(clearly_better)} alone.")
    if unclear:
        bad.append(f"There is no clear evidence it beats {', '.join(unclear)} alone; the likely ranges overlap.")
    recent = report.get("held_out", {})
    if recent.get("auc_stocks") is not None and recent["auc_stocks"] < 0.6:
        bad.append(f"Since {HELD_OUT_START[:4]} its accuracy has been {recent['auc_stocks']:.2f}, "
                   "little or no better than a coin flip, so it may be working less well than in the past.")
    if sim:
        d = sim["comparison"]["difference"]
        p = sim["portfolios"]
        dd = f"a worst fall of {p['signal']['max_drawdown']:.0%} vs {p['hold']['max_drawdown']:.0%}"
        if abs(d) < 0.002:
            line = f"The signal-based portfolio earned about the same as holding 60/40 ({d * 100:+.2f} points a year), with {dd}."
            (good if p["signal"]["max_drawdown"] < p["hold"]["max_drawdown"] else bad).append(line)
        else:
            line = f"The signal-based portfolio returned {d * 100:+.1f} points a year versus holding 60/40, with {dd}."
            (good if d > 0 else bad).append(line)
    return {"good": good, "bad": bad}


# --- comparing an alternative setup (§9.8) ------------------------------------------------------

COMPARE_LIMIT = 3  # comparisons per day before the over-tuning warning


def held_out_view(report: dict, located: list[dict], sim: dict | None) -> dict:
    since = pd.Timestamp(HELD_OUT_START)
    recent_ids = {e["event"].id for e in located if e["start"] >= since and e["event"].kind == "crash"}
    rows = [r for r in report["events"]["rows"] if r["event"] in recent_ids]
    h = report["held_out"]
    return {"auc": h["auc_stocks"], "band_check": h["band_check"],
            "hits": sum(r["warned"] for r in rows), "events": len(rows),
            "sim": None if not sim or not sim["held_out"] else
                   {k: v["annual_return"] for k, v in sim["held_out"].items()}}


def _setup(name: str, indicators, weights, floors, sim_config, tables, raw_dir, work_dir, stocks, bonds,
           stocks_tr, located, today):
    from dashboard import composite as comp_mod, signals, simulate

    if tables is None:
        tables = signals.run(indicators, raw_dir, work_dir, today)
    comp = comp_mod.category_scores(tables["score"], tables["stale"], indicators).add_prefix("cat_").join(
        comp_mod.overall_scores(comp_mod.category_scores(tables["score"], tables["stale"], indicators), weights))
    comp = comp.reindex(stocks.index)
    labels = outcome_labels(stocks, bonds)
    report = evaluate(comp, tables["score"].reindex(stocks.index), labels, located, weights, floors)
    sim = simulate.simulate(stocks_tr, bonds, comp_mod.with_hysteresis(comp["overall"], floors), sim_config, located)
    return {"name": name, "report": report, "sim": sim, "held_out": held_out_view(report, located, sim),
            "floors": floors, "weights": weights, "n_indicators": len(indicators)}


def compare_main(argv=None) -> None:
    import argparse
    import json
    import tempfile
    from datetime import date, datetime
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from dashboard import history, signals, simulate
    from dashboard.config import load_events, load_indicators, load_weights
    from dashboard.fetch.base import read_raw
    from dashboard.paths import CONFIG_DIR, RAW_DIR, ROOT, SCORES_DIR
    from dashboard.returns import on_days

    parser = argparse.ArgumentParser(description="Compare an alternative setup with the current model (spec §9.8).")
    parser.add_argument("--weights", type=Path, help="alternative weights.yaml")
    parser.add_argument("--indicators", type=Path, help="alternative indicators.yaml (indicators need data in data/raw)")
    parser.add_argument("--bands", help="alternative band floors, e.g. 40,60,75")
    parser.add_argument("--simulation", type=Path, help="alternative simulation.yaml")
    args = parser.parse_args(argv)

    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    log_path = out_dir / "compare-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    today_iso = date.today().isoformat()
    log.append({"at": datetime.now().isoformat(timespec="seconds"), "args": {k: str(v) for k, v in vars(args).items() if v}})
    log_path.write_text(json.dumps(log, indent=1))
    runs_today = sum(e["at"].startswith(today_iso) for e in log)

    market = pd.read_csv(SCORES_DIR / "market.csv", index_col="date", parse_dates=["date"])
    stocks, bonds = market["stocks"], market["bonds"]
    tr_path = RAW_DIR / "market" / "spx_tr.csv"
    stocks_tr = on_days(read_raw(tr_path), stocks.index) if tr_path.exists() else stocks
    located = [dict(found, event=ev) for ev in load_events()
               if (found := history.locate(ev, stocks, bonds)) is not None]
    today = date.today()
    base_ind, base_w = load_indicators(), load_weights()
    base_sim = simulate.load_sim_config()

    current = _setup("Current", base_ind, base_w, FLOORS, base_sim, signals.read_tables(SCORES_DIR), RAW_DIR,
                     None, stocks, bonds, stocks_tr, located, today)
    alt_ind = load_indicators(args.indicators) if args.indicators else base_ind
    alt_w = load_weights(args.weights) if args.weights else base_w
    alt_floors = tuple(float(x) for x in args.bands.split(",")) if args.bands else FLOORS
    if len(alt_floors) != 3 or list(alt_floors) != sorted(alt_floors):
        raise SystemExit("--bands needs three increasing numbers, e.g. 40,60,75")
    alt_sim = simulate.load_sim_config(args.simulation) if args.simulation else base_sim
    with tempfile.TemporaryDirectory() as work:
        alt_tables = None if args.indicators else signals.read_tables(SCORES_DIR)
        alternative = _setup("Alternative", alt_ind, alt_w, alt_floors, alt_sim, alt_tables, RAW_DIR, Path(work),
                             stocks, bonds, stocks_tr, located, today)

    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "site" / "templates"), autoescape=select_autoescape())
    env.filters["pct"] = lambda v, signed=False: "—" if v is None else (f"{v * 100:+.1f}%" if signed else f"{v * 100:.0f}%")
    html = env.get_template("compare.html.j2").render(
        setups=[current, alternative], args={k: str(v) for k, v in vars(args).items() if v},
        css=(Path(__file__).parent / "site" / "static" / "style.css").read_text(),
        runs_today=runs_today, limit=COMPARE_LIMIT, held_out_start=HELD_OUT_START[:4])
    (out_dir / "compare.html").write_text(html)
    print(f"Wrote {out_dir / 'compare.html'}")
    h_cur, h_alt = current["held_out"], alternative["held_out"]
    fmt = lambda v: "—" if v is None else f"{v:.2f}"  # noqa: E731
    print(f"Since {HELD_OUT_START[:4]} (headline): AUC {fmt(h_cur['auc'])} → {fmt(h_alt['auc'])}, "
          f"crashes warned {h_cur['hits']}/{h_cur['events']} → {h_alt['hits']}/{h_alt['events']}")
    if runs_today > COMPARE_LIMIT:
        print(f"WARNING: {runs_today} comparisons today. Trying many variants until one looks good is how models get "
              "tuned to the past and then fail. Prefer changes you can justify before seeing the results.")


if __name__ == "__main__":
    compare_main()
