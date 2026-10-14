"""Robert Shiller's monthly S&P 500 dataset (CAPE, earnings). The download link changes,
so it is read from the shillerdata.com page each run."""

import io
import re
from functools import lru_cache

import pandas as pd
import requests

PAGE_URL = "https://shillerdata.com/"
LINK = re.compile(r"""(?:https?:)?//[^"' ]+/ie_data\.xls[^"' ]*""")
TIMEOUT = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (market-risk-dashboard)"}


def find_download_link(page_html: str) -> str:
    match = LINK.search(page_html)
    if not match:
        raise ValueError("ie_data.xls link not found on shillerdata.com")
    link = match.group(0)
    return "https:" + link if link.startswith("//") else link


def _month_index(decimal_dates: pd.Series) -> pd.DatetimeIndex:
    # Shiller dates look like 2023.01 (Jan) ... 2023.1 (Oct), 2023.12 (Dec).
    years = decimal_dates.astype(float).floordiv(1).astype(int)
    months = ((decimal_dates.astype(float) - years) * 100).round().astype(int)
    return pd.to_datetime({"year": years, "month": months, "day": 1})


def parse_workbook(data: bytes) -> pd.DataFrame:
    """Columns: cape, earnings_yield (%, trailing 12-month earnings / price)."""
    raw = pd.read_excel(io.BytesIO(data), sheet_name="Data", header=None, skiprows=8)
    raw = raw[pd.to_numeric(raw[0], errors="coerce").notna()]
    index = _month_index(pd.to_numeric(raw[0]))
    price = pd.to_numeric(raw[1], errors="coerce").values
    earnings = pd.to_numeric(raw[3], errors="coerce").values
    cape = pd.to_numeric(raw[12], errors="coerce").values
    return pd.DataFrame({"cape": cape, "earnings_yield": earnings / price * 100}, index=index)


@lru_cache(maxsize=1)
def _download() -> pd.DataFrame:
    page = requests.get(PAGE_URL, headers=HEADERS, timeout=TIMEOUT)
    page.raise_for_status()
    resp = requests.get(find_download_link(page.text), headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_workbook(resp.content)


def fetch_shiller(series: str) -> pd.Series:
    return _download()[series].dropna()
