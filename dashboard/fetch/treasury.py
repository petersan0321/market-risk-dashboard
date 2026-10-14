"""TreasuryDirect auction results. The API sometimes ignores its term filter, so all notes and
bonds are requested and filtered here by maturity."""

import pandas as pd
import requests

URL = "https://www.treasurydirect.gov/TA_WS/securities/search"
TIMEOUT = 60
# Original issues and reopenings (e.g. "9-Year 10-Month" is a reopened 10-year note).
FAMILIES = {"Note": ("10-Year", "9-Year 1"), "Bond": ("30-Year", "29-Year")}
WINDOW = 6  # last 6 auctions, about 3 of each


def bid_to_cover(auctions: list[dict]) -> pd.Series:
    rows = [
        (a["auctionDate"][:10], float(a["bidToCoverRatio"]))
        for a in auctions
        if a.get("bidToCoverRatio") and a["securityTerm"].startswith(FAMILIES[a["type"]])
    ]
    s = pd.Series(dict(rows)).sort_index()
    s.index = pd.to_datetime(s.index)
    return s.rolling(WINDOW).mean().dropna()


def fetch_treasury(series: str, session=requests) -> pd.Series:
    if series != "bid_to_cover_10y_30y":
        raise ValueError(f"unknown treasury series {series}")
    auctions = []
    for security_type in FAMILIES:
        resp = session.get(URL, params={"type": security_type, "format": "json",
                                        "dateFieldName": "auctionDate", "startDate": "01/01/1980"},
                           timeout=TIMEOUT)
        resp.raise_for_status()
        auctions += resp.json()
    return bid_to_cover(auctions)
