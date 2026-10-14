import logging

from dashboard.config import load_indicators
from dashboard.fetch import fetch_all
from dashboard.paths import RAW_DIR


def main() -> None:
    status = fetch_all(load_indicators(), RAW_DIR)
    for ind_id, result in status.items():
        print(f"{ind_id}: {result}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
