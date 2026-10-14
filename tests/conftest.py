from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from dashboard.config import Indicator


@pytest.fixture
def make_indicator():
    base = Indicator(
        id="test", name="Test", category="credit", source="fred", series="X",
        frequency="daily", release_lag_days=0, transform=("level",), risk_when="high",
        revised=False, unit="%", plausible=(-1000.0, 1000.0), rules=(), explain="Test indicator.",
    )
    return lambda **kw: replace(base, **kw)


@pytest.fixture
def random_walk():
    """Deterministic daily business-day series, about 12 years long."""
    idx = pd.bdate_range("2010-01-01", "2021-12-31")
    rng = np.random.default_rng(42)
    return pd.Series(50 + rng.normal(0, 1, len(idx)).cumsum(), index=idx)
