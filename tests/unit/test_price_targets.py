"""Analyst price targets: parsing helpers and the yfinance mapping (no DB, no HTTP)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest

from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.utils.price_targets import (
    parse_price_target,
    parse_target_action,
    price_target_fields,
)

# ── parsing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (275.0, 275.0),
        (275, 275.0),
        ("275.0", 275.0),
        (" 98.5 ", 98.5),
        ("1,234.5", 1234.5),
        (Decimal("120.5000"), 120.5),
        (0.01, 0.01),
    ],
)
def test_a_usable_price_target_is_a_float(value, expected):
    assert parse_price_target(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        float("nan"),
        float("inf"),
        "nan",
        "NaN",
        "None",
        "null",
        "",
        "  ",
        "n/a",
        "-",
        "abc",
        0,
        0.0,
        "0.0",
        -5.0,
        "-3",
        1e9,  # a mis-scaled value must not reach a table
        True,
        [],
    ],
)
def test_no_price_target_is_none_never_zero(value):
    assert parse_price_target(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Raises", "Raises"),
        (" Lowers ", "Lowers"),
        ("Maintains", "Maintains"),
        (None, None),
        (float("nan"), None),
        ("nan", None),
        ("None", None),
        ("", None),
        (0.0, None),
        ("0.0", None),
        (True, None),
    ],
)
def test_target_action_text(value, expected):
    assert parse_target_action(value) == expected


def test_fields_of_a_stored_raw_data_dict_and_of_a_yahoo_series():
    raw = {  # raw_data keeps every value as text
        "Firm": "Big Bank",
        "currentPriceTarget": "275.0",
        "priorPriceTarget": "250.0",
        "priceTargetAction": "Raises",
    }
    assert price_target_fields(raw) == (275.0, 250.0, "Raises")
    series = pd.Series(
        {
            "Firm": "Big Bank",
            "currentPriceTarget": 140.0,
            "priorPriceTarget": float("nan"),
            "priceTargetAction": "Announces",
        }
    )
    assert price_target_fields(series) == (140.0, None, "Announces")
    assert price_target_fields({}) == (None, None, None)
    assert price_target_fields(None) == (None, None, None)
    only_nan = {"currentPriceTarget": "nan", "priorPriceTarget": "0.0"}
    assert price_target_fields(only_nan) == (None, None, None)


# ── yfinance mapping ──────────────────────────────────────────────────


def _client():
    return YFinanceClient(
        batch_size=10, delay_between_tickers=0, delay_between_batches=0
    )


@patch("trading_signals.collectors.yfinance_client.yf.Ticker")
def test_new_rows_fill_the_price_target_columns(mock_ticker_cls):
    recent = [
        (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d") for d in (5, 4, 3)
    ]
    frame = pd.DataFrame(
        {
            "Firm": ["Goldman Sachs", "Morgan Stanley", "No Target Inc"],
            "ToGrade": ["Buy", "Overweight", "Hold"],
            "FromGrade": ["Hold", "Equal-Weight", "Hold"],
            "Action": ["up", "up", "main"],
            "priceTargetAction": ["Raises", "Announces", "Maintains"],
            "currentPriceTarget": [275.0, 140.0, float("nan")],
            "priorPriceTarget": [250.0, 0.0, float("nan")],
        },
        index=pd.to_datetime(recent),
    )
    mock_ticker = MagicMock()
    type(mock_ticker).upgrades_downgrades = PropertyMock(return_value=frame)
    mock_ticker_cls.return_value = mock_ticker

    rows = _client().fetch_analyst_ratings(["AAPL"], lookback_days=30)

    assert len(rows) == 3
    first, second, third = rows
    assert (first["price_target_new"], first["price_target_old"]) == (275.0, 250.0)
    # a prior target of 0 is Yahoo's "none": NULL, not 0
    assert (second["price_target_new"], second["price_target_old"]) == (140.0, None)
    assert (third["price_target_new"], third["price_target_old"]) == (None, None)
    # the target action has no column: it stays in raw_data (as text)
    assert first["raw_data"]["priceTargetAction"] == "Raises"
    assert first["raw_data"]["currentPriceTarget"] == "275.0"
    # the existing mapping is unchanged
    assert first["firm"] == "Goldman Sachs" and first["action"] == "up"
    assert first["rating_new"] == "Buy" and first["rating_old"] == "Hold"


@patch("trading_signals.collectors.yfinance_client.yf.Ticker")
def test_an_older_yfinance_without_target_columns_still_works(mock_ticker_cls):
    recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {"Firm": ["A"], "ToGrade": ["Buy"], "FromGrade": ["Hold"], "Action": ["up"]},
        index=pd.to_datetime([recent]),
    )
    mock_ticker = MagicMock()
    type(mock_ticker).upgrades_downgrades = PropertyMock(return_value=frame)
    mock_ticker_cls.return_value = mock_ticker
    (row,) = _client().fetch_analyst_ratings(["AAPL"], lookback_days=30)
    assert row["price_target_new"] is None and row["price_target_old"] is None
