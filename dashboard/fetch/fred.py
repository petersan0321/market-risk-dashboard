"""FRED: the official API when FRED_API_KEY is set, otherwise FRED's public CSV download."""

import io
import os

import pandas as pd
import requests

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
TIMEOUT = 30


def parse_api_json(payload: dict) -> pd.Series:
    obs = payload["observations"]
    df = pd.DataFrame(obs, columns=["date", "value"])
    values = pd.to_numeric(df["value"], errors="coerce")  # FRED uses "." for missing
    return pd.Series(values.values, index=pd.to_datetime(df["date"])).dropna()


def parse_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    dates = pd.to_datetime(df.iloc[:, 0])
    values = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    return pd.Series(values.values, index=dates).dropna()


def fetch_fred(series_id: str, api_key: str | None = None, session=requests) -> pd.Series:
    api_key = api_key if api_key is not None else os.environ.get("FRED_API_KEY")
    if api_key:
        resp = session.get(API_URL, params={"series_id": series_id, "api_key": api_key,
                                            "file_type": "json"}, timeout=TIMEOUT)
        resp.raise_for_status()
        return parse_api_json(resp.json())
    resp = session.get(CSV_URL, params={"id": series_id}, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_csv(resp.text)
