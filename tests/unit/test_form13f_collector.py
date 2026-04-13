"""Tests for Form13FCollector – infotable parsing."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from trading_signals.collectors.form13f_collector import (
    Form13FCollector,
    parse_13f_infotable,
    _parse_date,
    TOP_FILERS,
)


class TestTopFilers:
    """Test the top filers configuration."""

    def test_contains_berkshire(self):
        assert "0001067983" in TOP_FILERS
        assert "Berkshire" in TOP_FILERS["0001067983"] or "Buffett" in TOP_FILERS["0001067983"]

    def test_contains_renaissance(self):
        assert "0001037389" in TOP_FILERS

    def test_at_least_20_filers(self):
        assert len(TOP_FILERS) >= 20

    def test_all_ciks_are_10_digits(self):
        for cik in TOP_FILERS:
            assert len(cik) == 10
            assert cik.isdigit()


class TestDateParsing:
    """Test date parsing helper."""

    def test_valid_date(self):
        assert _parse_date("2026-03-31") == date(2026, 3, 31)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_none(self):
        assert _parse_date(None) is None

    def test_invalid_format(self):
        assert _parse_date("not-a-date") is None


class TestInfotableParsing:
    """Test 13F infotable XML parsing."""

    SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
    <informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
        <infoTable>
            <nameOfIssuer>APPLE INC</nameOfIssuer>
            <titleOfClass>COM</titleOfClass>
            <cusip>037833100</cusip>
            <value>50000</value>
            <shrsOrPrnAmt>
                <sshPrnamt>250000</sshPrnamt>
                <sshPrnamtType>SH</sshPrnamtType>
            </shrsOrPrnAmt>
            <investmentDiscretion>SOLE</investmentDiscretion>
            <votingAuthority>
                <Sole>250000</Sole>
                <Shared>0</Shared>
                <None>0</None>
            </votingAuthority>
        </infoTable>
        <infoTable>
            <nameOfIssuer>MICROSOFT CORP</nameOfIssuer>
            <titleOfClass>COM</titleOfClass>
            <cusip>594918104</cusip>
            <value>75000</value>
            <shrsOrPrnAmt>
                <sshPrnamt>200000</sshPrnamt>
                <sshPrnamtType>SH</sshPrnamtType>
            </shrsOrPrnAmt>
            <putCall>PUT</putCall>
            <investmentDiscretion>SOLE</investmentDiscretion>
            <votingAuthority>
                <Sole>200000</Sole>
                <Shared>0</Shared>
                <None>0</None>
            </votingAuthority>
        </infoTable>
    </informationTable>"""

    def test_parses_two_holdings(self):
        holdings = parse_13f_infotable(self.SAMPLE_XML)
        assert len(holdings) == 2

    def test_cusip_extracted(self):
        holdings = parse_13f_infotable(self.SAMPLE_XML)
        assert holdings[0]["cusip"] == "037833100"
        assert holdings[1]["cusip"] == "594918104"

    def test_shares_extracted(self):
        holdings = parse_13f_infotable(self.SAMPLE_XML)
        assert holdings[0]["shares"] == 250000.0
        assert holdings[1]["shares"] == 200000.0

    def test_market_value_in_dollars(self):
        """13F values are reported in thousands, should be converted."""
        holdings = parse_13f_infotable(self.SAMPLE_XML)
        assert holdings[0]["market_value"] == 50_000_000  # 50000 * 1000
        assert holdings[1]["market_value"] == 75_000_000

    def test_put_call_captured(self):
        """Put/Call indicator should be captured when present."""
        holdings = parse_13f_infotable(self.SAMPLE_XML)
        assert holdings[0]["put_call"] is None
        assert holdings[1]["put_call"] == "PUT"

    def test_filer_metadata_passed_through(self):
        holdings = parse_13f_infotable(
            self.SAMPLE_XML,
            filer_name="Berkshire Hathaway",
            filer_cik="0001067983",
            filing_date=date(2026, 2, 14),
            report_period=date(2025, 12, 31),
        )
        assert holdings[0]["filer_name"] == "Berkshire Hathaway"
        assert holdings[0]["filer_cik"] == "0001067983"
        assert holdings[0]["filing_date"] == date(2026, 2, 14)
        assert holdings[0]["report_period"] == date(2025, 12, 31)

    def test_invalid_xml_returns_empty(self):
        holdings = parse_13f_infotable("<broken>xml")
        assert holdings == []

    def test_empty_infotable_returns_empty(self):
        xml = """<?xml version="1.0"?>
        <informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
        </informationTable>"""
        holdings = parse_13f_infotable(xml)
        assert holdings == []


class TestForm13FCollectorUnit:
    """Test Form13FCollector logic with mocked dependencies."""

    def test_store_writes_holdings(self):
        collector = Form13FCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "filer_name": "Berkshire Hathaway",
                "filer_cik": "0001067983",
                "report_period": date(2025, 12, 31),
                "filing_date": date(2026, 2, 14),
                "ticker": None,
                "cusip": "037833100",
                "shares": 250000.0,
                "market_value": 50_000_000,
                "put_call": None,
                "source_url": "https://example.com",
            }
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.flush.assert_called()
