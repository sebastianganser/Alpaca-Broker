"""Hand-checkable forecast / return fixtures shared by the forecast tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

D1, D2, D3 = pd.to_datetime(["2024-03-01", "2024-03-04", "2024-03-05"])


def universe() -> pd.DataFrame:
    """3 sessions x 4 tickers of realised 20d returns (60d still open)."""
    rows = []
    table = {
        D1: {"A": 0.10, "B": 0.02, "C": -0.02, "D": -0.10},  # mean 0, 50 % up
        D2: {"A": 0.08, "B": 0.06, "C": 0.04, "D": 0.02},    # mean .05, all up
        D3: {"A": 0.02, "B": 0.00, "C": -0.02, "D": 0.00},   # mean 0, 25 % up
    }
    for d, rets in table.items():
        for t, r in rets.items():
            rows.append({"snapshot_date": d, "ticker": t, "return_20d": r,
                         "return_60d": np.nan})
    return pd.DataFrame(rows)


def forecasts() -> pd.DataFrame:
    def row(d, t, direction, conf, conv):
        return {"session_date": d, "ticker": t, "horizon_days": 20,
                "direction": direction, "confidence": conf, "conviction": conv}

    return pd.DataFrame([
        row(D1, "A", "up", 0.60, 4), row(D1, "D", "down", 0.70, 5),
        row(D1, "B", "down", 0.55, 2), row(D1, "C", "neutral", 0.50, 3),
        row(D2, "A", "up", 0.80, 5), row(D2, "D", "up", 0.65, 3),
        row(D2, "C", "down", 0.60, 2),
        row(D3, "A", "up", 0.90, 5),
    ])


SPY = pd.DataFrame({"return_20d": [0.01, 0.03, 0.0]}, index=[D1, D2, D3])
