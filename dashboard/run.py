"""Run every pipeline step in order: fetch → signals → site."""

import logging
from datetime import date, datetime, timezone

from dashboard import signals
from dashboard.config import load_indicators
from dashboard.fetch import fetch_all
from dashboard.paths import RAW_DIR, SCORES_DIR, SITE_DIR
from dashboard.site.build import build_site


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    indicators = load_indicators()
    status = fetch_all(indicators, RAW_DIR)
    failed = [k for k, v in status.items() if v != "ok"]
    if failed:
        logging.warning("fetch failed for %s; continuing with previous data", failed)
    signals.run(indicators, RAW_DIR, SCORES_DIR, date.today())
    build_site(indicators, SCORES_DIR, SITE_DIR, datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
