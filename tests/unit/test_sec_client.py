"""Tests for SECClient – CIK mapping, rate limiting, and API helpers."""

import json
import time
from pathlib import Path
from unittest.mock import patch

from trading_signals.collectors.sec_client import (
    MIN_REQUEST_INTERVAL,
    SECClient,
    normalize_ticker,
    parse_acceptance_datetime,
)

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


class TestTickerNormalisation:
    """M7: share-class tickers use SEC notation (BRK-B)."""

    def test_normalize_ticker(self):
        assert normalize_ticker("brk.b") == "BRK-B"
        assert normalize_ticker(" BF/B ") == "BF-B"

    def test_get_cik_dot_ticker(self):
        client = SECClient(user_agent="TestAgent/1.0")
        client._ticker_to_cik = {"BRK-B": "0001067983"}
        client._cik_to_ticker = {"0001067983": "BRK-B"}
        assert client.get_cik("BRK.B") == "0001067983"
        assert client.get_cik("brk-b") == "0001067983"


class TestSubmissionsCache:
    """M7: submissions JSON fetched once per CIK per client."""

    def test_cached_per_cik(self):
        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "_get_json", return_value={"x": 1}) as mock:
            client.get_submissions("320193")
            client.get_submissions("0000320193")
            assert mock.call_count == 1
            client.get_submissions("789019")
            assert mock.call_count == 2
            client.clear_cache()
            client.get_submissions("320193")
            assert mock.call_count == 3


class TestFilingMetadata:
    """M1: accession number + acceptance datetime for every filing."""

    SUBMISSIONS = {
        "filings": {
            "recent": {
                "form": ["13F-HR", "4", "13F-HR/A"],
                "accessionNumber": ["acc1", "acc2", "acc3"],
                "filingDate": ["2026-02-14", "2026-02-15", "2026-03-01"],
                "reportDate": ["2025-12-31", "2026-02-13", "2025-12-31"],
                "acceptanceDateTime": [
                    "2026-02-14T16:05:23.000Z", "", "2026-03-01T09:00:00.000Z",
                ],
                "primaryDocument": ["primary_doc.xml", "x.xml", "primary_doc.xml"],
            }
        }
    }

    def test_13f_filings(self):
        from datetime import UTC, datetime

        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "get_submissions", return_value=self.SUBMISSIONS):
            filings = client.get_recent_13f_filings("0001067983")
        assert [f["accession_number"] for f in filings] == ["acc1", "acc3"]
        assert filings[0]["report_period"] == "2025-12-31"
        # EDGAR wall-clock is interpreted as US/Eastern (conservative PIT):
        # 16:05:23 EST (February) == 21:05:23 UTC
        assert filings[0]["acceptance_datetime"] == datetime(
            2026, 2, 14, 21, 5, 23, tzinfo=UTC
        )
        assert filings[1]["form_type"] == "13F-HR/A"

    def test_form4_missing_acceptance(self):
        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "get_submissions", return_value=self.SUBMISSIONS):
            filings = client.get_recent_form4_filings("0001067983")
        assert len(filings) == 1
        assert filings[0]["accession_number"] == "acc2"
        assert filings[0]["acceptance_datetime"] is None

    def test_parse_acceptance_datetime(self):
        assert parse_acceptance_datetime(None) is None
        assert parse_acceptance_datetime("garbage") is None
        dt = parse_acceptance_datetime("2026-02-14T16:05:23")
        assert dt is not None and dt.tzinfo is not None
        # Summer (EDT, UTC-4)
        from datetime import UTC, datetime

        assert parse_acceptance_datetime("2026-07-01T16:05:23.000Z") == datetime(
            2026, 7, 1, 20, 5, 23, tzinfo=UTC
        )


class TestAmendmentType:
    """13F-HR/A amendment type from primary_doc.xml."""

    def test_restatement(self):
        xml = """<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler">
          <formData><coverPage><isAmendment>true</isAmendment>
            <amendmentInfo><amendmentType>RESTATEMENT</amendmentType></amendmentInfo>
          </coverPage></formData></edgarSubmission>"""
        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "download_filing_document", return_value=xml):
            assert client.get_13f_amendment_type("1", "acc") == "RESTATEMENT"

    def test_new_holdings_whitespace(self):
        xml = "<a><amendmentType>New  Holdings</amendmentType></a>"
        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "download_filing_document", return_value=xml):
            assert client.get_13f_amendment_type("1", "acc") == "NEW HOLDINGS"

    def test_missing(self):
        client = SECClient(user_agent="TestAgent/1.0")
        with patch.object(client, "download_filing_document", return_value="<a/>"):
            assert client.get_13f_amendment_type("1", "acc") is None
