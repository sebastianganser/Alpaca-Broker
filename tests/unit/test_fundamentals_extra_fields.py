"""The extra fundamentals fields of the yfinance ``info`` call (migration 040)."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from trading_signals.collectors.fundamentals_collector import (
    _UPSERT_COLUMNS,
    FundamentalsCollectorYF,
)
from trading_signals.collectors.yfinance_client import (
    FUNDAMENTALS_KEYS,
    YFinanceClient,
    _recommendation_key,
)
from trading_signals.db.models.fundamentals import FundamentalsSnapshot

NEW_COLUMNS = (
    "shares_outstanding",
    "float_shares",
    "held_percent_institutions",
    "held_percent_insiders",
    "short_percent_of_float",
    "free_cashflow",
    "total_cash",
    "total_debt",
    "gross_margins",
    "recommendation_mean",
    "recommendation_key",
    "number_of_analyst_opinions",
)

#: What Yahoo returned for AAPL on 2026-10-18 (ratios as decimal fractions).
AAPL_INFO = {
    "regularMarketPrice": 343.46,
    "sharesOutstanding": 14594180000,
    "floatShares": 14569078010,
    "heldPercentInstitutions": 0.6635,
    "heldPercentInsiders": 0.01648,
    "shortPercentOfFloat": 0.0083,
    "freeCashflow": 107721875456,
    "totalCash": 62399000576,
    "totalDebt": 84343996416,
    "grossMargins": 0.48653,
    "recommendationMean": 2.20455,
    "recommendationKey": "buy",
    "numberOfAnalystOpinions": 39,
}


def _fetch(info: dict) -> dict | None:
    with patch("trading_signals.collectors.yfinance_client.yf.Ticker") as ticker_cls:
        ticker = MagicMock()
        ticker.info = info
        ticker.get_earnings_estimate.return_value = None
        ticker_cls.return_value = ticker
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_fundamentals(["AAPL"])
    return results[0] if results else None


class TestExtraction:
    def test_all_twelve_fields_are_taken_from_the_info_dict(self):
        record = _fetch(AAPL_INFO)
        assert record["shares_outstanding"] == 14594180000
        assert record["float_shares"] == 14569078010
        assert record["held_percent_institutions"] == 0.6635
        assert record["held_percent_insiders"] == 0.01648
        assert record["short_percent_of_float"] == 0.0083
        assert record["free_cashflow"] == 107721875456
        assert record["total_cash"] == 62399000576
        assert record["total_debt"] == 84343996416
        assert record["gross_margins"] == 0.48653
        assert record["recommendation_mean"] == 2.20455
        assert record["recommendation_key"] == "buy"
        assert record["number_of_analyst_opinions"] == 39
        assert isinstance(record["number_of_analyst_opinions"], int)

    def test_the_new_keys_are_exactly_the_documented_yahoo_names(self):
        assert {
            FUNDAMENTALS_KEYS[k]
            for k in (
                "sharesOutstanding",
                "floatShares",
                "heldPercentInstitutions",
                "heldPercentInsiders",
                "shortPercentOfFloat",
                "freeCashflow",
                "totalCash",
                "totalDebt",
                "grossMargins",
                "recommendationMean",
                "numberOfAnalystOpinions",
            )
        } | {"recommendation_key"} == set(NEW_COLUMNS)

    def test_a_stock_without_analyst_coverage_gets_nulls_not_the_literal_none(self):
        record = _fetch(
            {
                **AAPL_INFO,
                "recommendationKey": "none",
                "recommendationMean": None,
                "numberOfAnalystOpinions": None,
            }
        )
        assert record["recommendation_key"] is None
        assert record["recommendation_mean"] is None
        assert record["number_of_analyst_opinions"] is None
        assert record["shares_outstanding"] == 14594180000  # the rest is unaffected

    def test_missing_keys_are_null(self):
        record = _fetch({"regularMarketPrice": 100.0})
        assert all(record[c] is None for c in NEW_COLUMNS)

    def test_implausible_ratios_are_nulled_and_amounts_are_kept(self):
        record = _fetch(
            {
                **AAPL_INFO,
                "heldPercentInstitutions": 66.35,  # percent instead of fraction
                "shortPercentOfFloat": 8.3,
                "grossMargins": 48.65,
                "recommendationMean": 0.0,
                "heldPercentInsiders": 1.7,
                "sharesOutstanding": -5,
                # ADR in local currency: no plausibility rule for amounts
                "totalDebt": 3.2e13,
                "freeCashflow": -4.0e12,
            }
        )
        for column in (
            "held_percent_institutions",
            "short_percent_of_float",
            "gross_margins",
            "recommendation_mean",
            "held_percent_insiders",
            "shares_outstanding",
        ):
            assert record[column] is None, column
        assert record["total_debt"] == 3.2e13 and record["free_cashflow"] == -4.0e12
        assert record["float_shares"] == 14569078010

    def test_institutional_ownership_above_100_percent_is_a_valid_yahoo_value(self):
        record = _fetch({**AAPL_INFO, "heldPercentInstitutions": 1.0345})
        assert record["held_percent_institutions"] == 1.0345

    def test_existing_fields_still_come_through(self):
        record = _fetch(
            {**AAPL_INFO, "marketCap": 2.5e12, "trailingPE": 28.5, "beta": 1.24}
        )
        assert (record["market_cap"], record["pe_ratio"], record["beta"]) == (
            2.5e12,
            28.5,
            1.24,
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("buy", "buy"),
            ("Strong_Buy", "strong_buy"),
            (" hold ", "hold"),
            ("none", None),
            ("NONE", None),
            ("", None),
            (None, None),
            (3, None),
        ],
    )
    def test_recommendation_key(self, raw, expected):
        assert _recommendation_key(raw) == expected


class TestCollectorAndModel:
    def test_every_extracted_field_is_upserted(self):
        extracted = set(FUNDAMENTALS_KEYS.values()) | {
            "recommendation_key",
            "most_recent_quarter",
            "eps_growth_yoy",
            "target_price_low",
            "target_price_mean",
            "target_price_median",
            "target_price_high",
        }
        assert extracted <= set(_UPSERT_COLUMNS)
        assert set(NEW_COLUMNS) <= set(_UPSERT_COLUMNS)

    def test_the_model_has_a_column_for_every_upserted_field(self):
        columns = {c.name for c in FundamentalsSnapshot.__table__.columns}
        assert set(_UPSERT_COLUMNS) <= columns
        for name in NEW_COLUMNS:
            assert FundamentalsSnapshot.__table__.c[name].nullable is True

    def test_store_refreshes_the_new_columns_on_conflict(self):
        collector = FundamentalsCollectorYF()
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        record = {"ticker": "AAPL", **{c: None for c in _UPSERT_COLUMNS}}
        record.update(recommendation_key="buy", short_percent_of_float=0.0083)
        collector.store(session, [record])
        sql = str(session.execute.call_args[0][0].compile(dialect=postgresql.dialect()))
        for name in NEW_COLUMNS:
            assert f"{name} = excluded.{name}" in sql, name
        assert "ON CONFLICT (ticker, snapshot_date) DO UPDATE" in sql
