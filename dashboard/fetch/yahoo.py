"""Yahoo Finance daily closes via yfinance."""

import pandas as pd
import yfinance as yf


def closes_from_history(history: pd.DataFrame) -> pd.Series:
    if history.empty:
        raise ValueError("no rows returned")
    closes = history["Close"].dropna()
    index = pd.DatetimeIndex(closes.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    return pd.Series(closes.values, index=index.normalize())


def fetch_yahoo(ticker: str) -> pd.Series:
    history = yf.Ticker(ticker).history(period="max", auto_adjust=False)
    return closes_from_history(history)
