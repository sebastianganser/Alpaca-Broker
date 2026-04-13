"""Tests for Form4Collector – XML parsing and transaction extraction."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.form4_collector import (
    Form4Collector,
    parse_form4_xml,
    _text,
    _safe_float,
    TRANSACTION_CODES,
)


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestXMLHelpers:
    """Test XML helper functions."""

    def test_safe_float_valid(self):
        assert _safe_float("175.50") == 175.50

    def test_safe_float_integer(self):
        assert _safe_float("5000") == 5000.0

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_invalid(self):
        assert _safe_float("not-a-number") is None

    def test_safe_float_empty(self):
        assert _safe_float("") is None


class TestTransactionCodes:
    """Test transaction code mapping."""

    def test_purchase_code(self):
        assert TRANSACTION_CODES["P"] == "Purchase"

    def test_sale_code(self):
        assert TRANSACTION_CODES["S"] == "Sale"

    def test_option_exercise(self):
        assert TRANSACTION_CODES["M"] == "Option Exercise"

    def test_gift_code(self):
        assert TRANSACTION_CODES["G"] == "Gift"


class TestForm4XMLParsing:
    """Test Form 4 XML parsing with fixture data."""

    def _load_fixture(self) -> str:
        with open(FIXTURES_DIR / "form4_sample.xml") as f:
            return f.read()

    def test_parses_all_transactions(self):
        """Should find all three transactions (2 non-derivative + 1 derivative)."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)
        assert len(txns) == 3

    def test_purchase_transaction(self):
        """First transaction should be a purchase."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)
        purchase = txns[0]

        assert purchase["transaction_type"] == "P"
        assert purchase["shares"] == 5000.0
        assert purchase["price_per_share"] == 175.50
        assert purchase["total_value"] == 877500.0  # 5000 * 175.50
        assert purchase["shares_owned_after"] == 3395725.0
        assert purchase["is_derivative"] is False

    def test_sale_transaction(self):
        """Second transaction should be a sale."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)
        sale = txns[1]

        assert sale["transaction_type"] == "S"
        assert sale["shares"] == 2000.0
        assert sale["price_per_share"] == 176.25
        assert sale["total_value"] == 352500.0
        assert sale["is_derivative"] is False

    def test_derivative_transaction(self):
        """Third transaction should be a derivative (RSU exercise)."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)
        derivative = txns[2]

        assert derivative["transaction_type"] == "M"
        assert derivative["shares"] == 100000.0
        assert derivative["is_derivative"] is True

    def test_issuer_info(self):
        """Should extract issuer information."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)

        # Without ticker override, uses the XML's issuer trading symbol
        assert txns[0]["company_name"] == "Apple Inc"
        assert txns[0]["cik"] == "0000320193"

    def test_owner_info(self):
        """Should extract reporting owner information."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)

        assert txns[0]["insider_name"] == "Cook Timothy D"
        assert txns[0]["insider_title"] == "Chief Executive Officer"

    def test_ticker_override(self):
        """Passing ticker= should override the XML's trading symbol."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml, ticker="AAPL")

        assert all(t["ticker"] == "AAPL" for t in txns)

    def test_filing_metadata(self):
        """Should include filing_date and form4_url when provided."""
        xml = self._load_fixture()
        txns = parse_form4_xml(
            xml,
            filing_date=date(2026, 4, 12),
            form4_url="https://www.sec.gov/example",
        )

        assert txns[0]["filing_date"] == date(2026, 4, 12)
        assert txns[0]["form4_url"] == "https://www.sec.gov/example"

    def test_transaction_dates(self):
        """Should correctly parse transaction dates."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)

        assert txns[0]["transaction_date"] == date(2026, 4, 10)
        assert txns[1]["transaction_date"] == date(2026, 4, 11)

    def test_raw_data_included(self):
        """Each transaction should have raw_data for audit."""
        xml = self._load_fixture()
        txns = parse_form4_xml(xml)

        assert txns[0]["raw_data"] is not None
        assert txns[0]["raw_data"]["transaction_code"] == "P"
        assert txns[0]["raw_data"]["transaction_code_description"] == "Purchase"


class TestForm4CollectorUnit:
    """Test Form4Collector logic with mocked dependencies."""

    def test_store_writes_transactions(self):
        """store() should insert transactions into session."""
        collector = Form4Collector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc",
                "cik": "0000320193",
                "insider_name": "Cook Timothy D",
                "insider_title": "CEO",
                "transaction_date": date(2026, 4, 10),
                "filing_date": date(2026, 4, 12),
                "transaction_type": "P",
                "shares": 5000.0,
                "price_per_share": 175.50,
                "total_value": 877500.0,
                "shares_owned_after": 3395725.0,
                "is_derivative": False,
                "form4_url": "https://example.com",
                "raw_data": {"transaction_code": "P"},
            }
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.flush.assert_called()

    def test_empty_xml_returns_empty(self):
        """Invalid XML should return empty list, not crash."""
        txns = parse_form4_xml("<invalid>xml</broken>")
        assert txns == []

    def test_no_transactions_returns_empty(self):
        """XML without transactions should return empty list."""
        xml = """<?xml version="1.0"?>
        <ownershipDocument>
            <issuer><issuerCik>123</issuerCik></issuer>
            <reportingOwner>
                <reportingOwnerId><rptOwnerName>Test</rptOwnerName></reportingOwnerId>
            </reportingOwner>
        </ownershipDocument>"""
        txns = parse_form4_xml(xml)
        assert txns == []
