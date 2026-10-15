"""Run every pipeline step in order: fetch → signals → composite → regime → rationale → site.

`python -m dashboard.run --no-fetch` rebuilds everything from the data already in data/raw."""

import argparse
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from dashboard import backtest, composite, context, history, rationale, regime, signals, simulate
from dashboard.config import (load_calendar, load_events, load_glossary, load_indicators, load_news_topics,
                              load_untracked, load_weights)
from dashboard.fetch import MARKETS, fetch_all
from dashboard.fetch.base import read_raw
from dashboard.paths import CONFIG_DIR, CONTEXT_DIR, RAW_DIR, SCORES_DIR, SITE_DIR
from dashboard.returns import bond_index, on_days
from dashboard.site.build import build_site

log = logging.getLogger("dashboard")


def to_jsonable(obj):
    """Replace NaN/inf with None and numpy scalars with Python ones."""
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if math.isnan(obj) or math.isinf(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def analyze(indicators, weights, tables, raw_dir: Path, scores_dir: Path, today: date, events=(),
            sim_config_path: Path = CONFIG_DIR / "simulation.yaml") -> dict:
    comp = composite.run(tables, indicators, weights, scores_dir)
    market = {name: read_raw(raw_dir / "market" / f"{name}.csv") for name in MARKETS
              if (raw_dir / "market" / f"{name}.csv").exists()}
    stocks = on_days(market["spx"], comp.index)
    bonds = on_days(bond_index(market["ust10y"], market.get("agg")), comp.index)
    pd.DataFrame({"stocks": stocks, "bonds": bonds}).to_csv(scores_dir / "market.csv", date_format="%Y-%m-%d")

    core_pce = next(i for i in indicators if i.id == "core_pce_6m")
    regimes = regime.classify(regime.growth_index(tables["value"]),
                              regime.inflation_gap(core_pce, read_raw(raw_dir / "core_pce_6m.csv"), today),
                              comp.index)
    regimes.to_csv(scores_dir / "regime.csv", date_format="%Y-%m-%d")

    result = rationale.build(indicators, tables, comp, weights, stocks, bonds)
    labels = regimes["regime"].dropna()
    current = labels.iloc[-1]
    changed = labels[labels != current]
    result["regime"] = {
        "now": current,
        "signalled": regimes["signalled"].dropna().iloc[-1],
        "current_since": (labels[labels.index > changed.index[-1]].index[0] if len(changed) else labels.index[0]).date().isoformat(),
        "since": labels.index[0].date().isoformat(),
        "growth_change": float(regimes["growth_change"].dropna().iloc[-1]),
        "inflation_gap": float(regimes["inflation_gap"].dropna().iloc[-1]),
        "returns": regime.regime_returns(regimes["regime"], stocks, bonds),
    }
    hist, located = history.build(list(events), indicators, tables["score"], comp, stocks, bonds)
    for ind in indicators:
        detail = result["indicators"].get(ind.id, {})
        if detail.get("available"):
            detail["spectrum"]["events"] = history.event_markers(ind, tables["value"][ind.id], located)
    result["looks_like"] = hist["looks_like"]

    labels = backtest.outcome_labels(stocks, bonds)
    report = backtest.evaluate(comp, tables["score"], labels, located, weights)
    stocks_tr = on_days(market["spx_tr"], comp.index) if "spx_tr" in market else stocks
    sim = simulate.simulate(stocks_tr, bonds, composite.with_hysteresis(comp["overall"]),
                            simulate.load_sim_config(sim_config_path), located)
    report["simulation"] = sim
    report["summary"] = backtest.summary(report, sim, {i.id: i.name for i in indicators})
    (scores_dir / "backtest.json").write_text(json.dumps(to_jsonable(report), indent=1))
    result["model_confidence"] = backtest.today_confidence(report, result["overall"]["band"])
    (scores_dir / "history.json").write_text(json.dumps(to_jsonable(hist), indent=1))
    result = to_jsonable(result)
    (scores_dir / "rationale.json").write_text(json.dumps(result, indent=1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="skip downloading; use data/raw as is")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    indicators, weights, untracked = load_indicators(), load_weights(), load_untracked()
    glossary = load_glossary()
    events = load_events(glossary=glossary)
    today = date.today()
    if not args.no_fetch:
        status = fetch_all(indicators, RAW_DIR)
        failed = [k for k, v in status.items() if v != "ok"]
        if failed:
            log.warning("fetch failed for %s; continuing with previous data", failed)
    tables = signals.run(indicators, RAW_DIR, SCORES_DIR, today)
    result = analyze(indicators, weights, tables, RAW_DIR, SCORES_DIR, today, events)
    ids = {i.id for i in indicators}
    types, manual = load_calendar(indicator_ids=ids)
    feeds, topics = load_news_topics(indicator_ids=ids)
    spx = read_raw(RAW_DIR / "market" / "spx.csv")
    agg_path = RAW_DIR / "market" / "agg.csv"
    long_bonds = bond_index(read_raw(RAW_DIR / "market" / "ust10y.csv"), read_raw(agg_path) if agg_path.exists() else None)
    ctx = context.run(types, manual, feeds, topics, CONTEXT_DIR, today, spx, long_bonds, fetch=not args.no_fetch)
    log.info("context: %d calendar items, %d recent headlines", len(ctx["calendar"]["items"]), len(ctx["news"]))
    o = result["overall"]
    log.info("overall %.1f (%s), regime %s", o["score"], o["band"], result["regime"]["now"])
    build_site(indicators, untracked, SCORES_DIR, SITE_DIR, datetime.now(timezone.utc), events, glossary, weights)


if __name__ == "__main__":
    main()
