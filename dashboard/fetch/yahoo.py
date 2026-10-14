"""Yahoo Finance daily closes via yfinance."""

import pandas as pd
import yfinance as yf


def closes_from_history(history: pd.DataFrame, column: str = "Close") -> pd.Series:
    if history.empty:
        raise ValueError("no rows returned")
    closes = history[column].dropna()
    index = pd.DatetimeIndex(closes.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    return pd.Series(closes.values, index=index.normalize())


def fetch_yahoo(ticker: str) -> pd.Series:
    history = yf.Ticker(ticker).history(period="max", auto_adjust=False)
    return closes_from_history(history)


def fetch_yahoo_total_return(ticker: str) -> pd.Series:
    """Close adjusted for dividends and splits, i.e. a total-return price series."""
    history = yf.Ticker(ticker).history(period="max", auto_adjust=False)
    return closes_from_history(history, "Adj Close")
