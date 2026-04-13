"""Tests for SECClient – CIK mapping, rate limiting, and API helpers."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.sec_client import SECClient, MIN_REQUEST_INTERVAL


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestCIKMapping:
    """Test CIK ↔ Ticker mapping."""

    def _make_client_with_fixture(self) -> SECClient:
        """Create a SECClient and load the sample mapping fixture."""
        with open(FIXTURES_DIR / "company_tickers_sample.json") as f:
            sample_data = json.load(f)

        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "_get_json", return_value=sample_data):
            client.load_cik_mapping()
        return client

    def test_ticker_to_cik(self):
        client = self._make_client_with_fixture()
        assert client.get_cik("AAPL") == "0000320193"

    def test_ticker_to_cik_case_insensitive(self):
        client = self._make_client_with_fixture()
        assert client.get_cik("aapl") == "0000320193"

    def test_cik_to_ticker(self):
        client = self._make_client_with_fixture()
        assert client.get_ticker("0000320193") == "AAPL"

    def test_cik_to_ticker_without_padding(self):
        """get_ticker should handle unpadded CIKs."""
        client = self._make_client_with_fixture()
        assert client.get_ticker("320193") == "AAPL"

    def test_unknown_ticker_returns_none(self):
        client = self._make_client_with_fixture()
        assert client.get_cik("UNKNOWN_TICKER") is None

    def test_unknown_cik_returns_none(self):
        client = self._make_client_with_fixture()
        assert client.get_ticker("9999999999") is None

    def test_mapping_size(self):
        client = self._make_client_with_fixture()
        assert client.get_cik("MSFT") == "0000789019"
        assert client.get_cik("TSLA") == "0001318605"
        assert client.get_cik("NVDA") == "0001045810"

    def test_lazy_loading(self):
        """CIK mapping should load on first access."""
        with open(FIXTURES_DIR / "company_tickers_sample.json") as f:
            sample_data = json.load(f)

        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "_get_json", return_value=sample_data) as mock:
            # Should not be loaded yet
            assert client._ticker_to_cik is None
            # First access triggers loading
            client.get_cik("AAPL")
            mock.assert_called_once()


class TestCIKPadding:
    """Test CIK zero-padding."""

    def test_pad_integer(self):
        assert SECClient.pad_cik(320193) == "0000320193"

    def test_pad_short_string(self):
        assert SECClient.pad_cik("320193") == "0000320193"

    def test_pad_already_padded(self):
        assert SECClient.pad_cik("0000320193") == "0000320193"

    def test_pad_single_digit(self):
        assert SECClient.pad_cik("1") == "0000000001"


class TestRateLimiting:
    """Test rate limiting behavior."""

    def test_rate_limit_enforces_delay(self):
        """Rate limiter should enforce minimum interval between requests."""
        client = SECClient(user_agent="TestAgent/1.0")

        # Simulate a recent request
        client._last_request_time = time.monotonic()

        start = time.monotonic()
        client._rate_limit()
        elapsed = time.monotonic() - start

        # Should have waited at least ~0.1s
        assert elapsed >= MIN_REQUEST_INTERVAL * 0.8  # Allow some tolerance

    def test_rate_limit_no_delay_if_enough_time_passed(self):
        """No delay if enough time has passed since last request."""
        client = SECClient(user_agent="TestAgent/1.0")

        # Simulate an old request
        client._last_request_time = time.monotonic() - 1.0

        start = time.monotonic()
        client._rate_limit()
        elapsed = time.monotonic() - start

        # Should not have waited
        assert elapsed < 0.05


class TestUserAgent:
    """Test User-Agent header configuration."""

    def test_custom_user_agent(self):
        client = SECClient(user_agent="CustomApp/2.0 (test@test.com)")
        assert client._session.headers["User-Agent"] == "CustomApp/2.0 (test@test.com)"

    def test_session_headers(self):
        client = SECClient(user_agent="TestApp/1.0")
        assert "User-Agent" in client._session.headers
        assert client._session.headers["Accept"] == "application/json"


class TestSubmissionsAPI:
    """Test Submissions API response parsing."""

    def test_get_recent_form4_filings(self):
        """Should filter submissions for Form 4 only."""
        client = SECClient(user_agent="TestAgent/1.0")

        mock_submissions = {
            "filings": {
                "recent": {
                    "form": ["10-K", "4", "8-K", "4", "4/A"],
                    "accessionNumber": [
                        "0001-24-000001",
                        "0001-24-000002",
                        "0001-24-000003",
                        "0001-24-000004",
                        "0001-24-000005",
                    ],
                    "filingDate": [
                        "2026-04-01",
                        "2026-04-05",
                        "2026-04-06",
                        "2026-04-10",
                        "2026-04-11",
                    ],
                    "primaryDocument": [
                        "annual.htm",
                        "doc1.xml",
                        "current.htm",
                        "doc2.xml",
                        "doc3.xml",
                    ],
                }
            }
        }

        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_recent_form4_filings("0000320193")

        assert len(filings) == 3  # Two Form 4 + one 4/A
        assert filings[0]["form_type"] == "4"
        assert filings[0]["accession_number"] == "0001-24-000002"
        assert filings[2]["form_type"] == "4/A"

    def test_get_recent_form4_with_date_filter(self):
        """Should filter by since_date."""
        from datetime import date

        client = SECClient(user_agent="TestAgent/1.0")

        mock_submissions = {
            "filings": {
                "recent": {
                    "form": ["4", "4", "4"],
                    "accessionNumber": ["acc1", "acc2", "acc3"],
                    "filingDate": ["2026-03-01", "2026-04-05", "2026-04-10"],
                    "primaryDocument": ["d1.xml", "d2.xml", "d3.xml"],
                }
            }
        }

        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_recent_form4_filings(
                "0000320193", since_date=date(2026, 4, 1)
            )

        assert len(filings) == 2  # Only April filings
        assert filings[0]["filing_date"] == "2026-04-05"
