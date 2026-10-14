"""Caldara & Iacoviello daily Geopolitical Risk index."""

import io
from functools import lru_cache

import pandas as pd
import requests

URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
TIMEOUT = 60


def parse_workbook(data: bytes, column: str) -> pd.Series:
    df = pd.read_excel(io.BytesIO(data))
    dates = pd.to_datetime(df["DAY"].astype(int).astype(str), format="%Y%m%d")
    return pd.Series(pd.to_numeric(df[column], errors="coerce").values, index=dates).dropna()


@lru_cache(maxsize=1)
def _download() -> bytes:
    resp = requests.get(URL, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content


def fetch_gpr(column: str) -> pd.Series:
    return parse_workbook(_download(), column)
