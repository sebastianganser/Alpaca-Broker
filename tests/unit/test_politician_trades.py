"""Tests for Politician Trades – disclosure client, collector, and ORM model."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.disclosure_client import (
    DisclosureClient,
    _normalize_ticker,
    _normalize_transaction_type,
    _parse_date,
)
from trading_signals.collectors.politician_trades_collector import (
    PoliticianTradesCollector,
)
from trading_signals.db.models.politicians import PoliticianTrade


# ============================================================================
# Fixtures – HTML samples from Senate eFD portal
# ============================================================================

SENATE_SEARCH_RESULTS_HTML = """
<html>
<body>
<table class="table">
<thead>
  <tr>
    <th>First</th><th>Last</th><th>Office</th>
    <th>Report Type</th><th>Date Filed</th>
  </tr>
</thead>
<tbody>
  <tr>
    <td>Tommy</td>
    <td>Tuberville</td>
    <td>Tuberville, Tommy (Senator)</td>
    <td><a href="/search/view/ptr/abc-123/">Periodic Transaction Report</a></td>
    <td>01/15/2026</td>
  </tr>
  <tr>
    <td>Nancy</td>
    <td>Pelosi</td>
    <td>Pelosi, Nancy (Senator)</td>
    <td><a href="/search/view/paper/def-456/">Periodic Transaction Report</a></td>
    <td>01/20/2026</td>
  </tr>
  <tr>
    <td>Mark</td>
    <td>Kelly</td>
    <td>Kelly, Mark (Senator)</td>
    <td><a href="/search/view/ptr/ghi-789/">Periodic Transaction Report</a></td>
    <td>02/01/2026</td>
  </tr>
</tbody>
</table>
</body>
</html>
"""

SENATE_PTR_DETAIL_HTML = """
<html>
<body>
<h2>Periodic Transaction Report</h2>
<table>
<thead>
  <tr>
    <th>#</th>
    <th>Transaction Date</th>
    <th>Owner</th>
    <th>Ticker</th>
    <th>Asset Name</th>
    <th>Asset Type</th>
    <th>Type</th>
    <th>Amount</th>
    <th>Comment</th>
  </tr>
</thead>
<tbody>
  <tr>
    <td>1</td>
    <td>12/15/2025</td>
    <td>Self</td>
    <td>AAPL</td>
    <td>Apple Inc. (Common Stock)</td>
    <td>Stock</td>
    <td>Purchase</td>
    <td>$1,001 - $15,000</td>
    <td></td>
  </tr>
  <tr>
    <td>2</td>
    <td>12/20/2025</td>
    <td>Spouse</td>
    <td>MSFT</td>
    <td>Microsoft Corporation</td>
    <td>Stock</td>
    <td>Sale (Full)</td>
    <td>$15,001 - $50,000</td>
    <td>Position closed</td>
  </tr>
  <tr>
    <td>3</td>
    <td>12/22/2025</td>
    <td>Self</td>
    <td>--</td>
    <td>ABC Municipal Bond Fund</td>
    <td>Municipal Security</td>
    <td>Purchase</td>
    <td>$1,001 - $15,000</td>
    <td></td>
  </tr>
</tbody>
</table>
</body>
</html>
"""


# ============================================================================
# Tests: Date Parsing
# ============================================================================


class TestDateParsing:
    """Test the _parse_date helper."""

    def test_us_format(self):
        assert _parse_date("12/15/2025") == date(2025, 12, 15)

    def test_iso_format(self):
        assert _parse_date("2025-12-15") == date(2025, 12, 15)

    def test_short_year_format(self):
        assert _parse_date("12/15/25") == date(2025, 12, 15)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_none_input(self):
        assert _parse_date(None) is None

    def test_invalid_format(self):
        assert _parse_date("not-a-date") is None


# ============================================================================
# Tests: Ticker Normalization
# ============================================================================


class TestTickerNormalization:
    """Test ticker normalization logic."""

    def test_simple_ticker(self):
        assert _normalize_ticker("AAPL") == "AAPL"

    def test_lowercase(self):
        assert _normalize_ticker("aapl") == "AAPL"

    def test_whitespace(self):
        assert _normalize_ticker("  MSFT  ") == "MSFT"

    def test_parenthetical_removed(self):
        assert _normalize_ticker("AAPL (Common Stock)") == "AAPL"

    def test_trailing_dot_removed(self):
        assert _normalize_ticker("BRK.") == "BRK"

    def test_trailing_dash_removed(self):
        assert _normalize_ticker("META-") == "META"


# ============================================================================
# Tests: Transaction Type Normalization
# ============================================================================


class TestTransactionTypeNorm:
    """Test transaction type normalization."""

    def test_purchase(self):
        assert _normalize_transaction_type("Purchase") == "Purchase"

    def test_sale(self):
        assert _normalize_transaction_type("Sale") == "Sale"

    def test_sale_full(self):
        assert _normalize_transaction_type("Sale (Full)") == "Sale"

    def test_sale_partial(self):
        assert _normalize_transaction_type("Sale (Partial)") == "Sale"

    def test_exchange(self):
        assert _normalize_transaction_type("Exchange") == "Exchange"

    def test_unknown_passthrough(self):
        assert _normalize_transaction_type("Conversion") == "Conversion"


# ============================================================================
# Tests: Disclosure Client – Search Results Parsing
# ============================================================================


class TestSenateSearchParsing:
    """Test parsing of Senate eFD search results."""

    def test_parses_electronic_filings(self):
        client = DisclosureClient()
        filings = client._parse_senate_search_results(SENATE_SEARCH_RESULTS_HTML)
        # Should only include /ptr/ links, not /paper/ links
        assert len(filings) == 2

    def test_excludes_paper_filings(self):
        client = DisclosureClient()
        filings = client._parse_senate_search_results(SENATE_SEARCH_RESULTS_HTML)
        urls = [f["ptr_link"] for f in filings]
        assert all("/ptr/" in url for url in urls)
        assert not any("/paper/" in url for url in urls)

    def test_extracts_name(self):
        client = DisclosureClient()
        filings = client._parse_senate_search_results(SENATE_SEARCH_RESULTS_HTML)
        assert filings[0]["first_name"] == "Tommy"
        assert filings[0]["last_name"] == "Tuberville"

    def test_extracts_date(self):
        client = DisclosureClient()
        filings = client._parse_senate_search_results(SENATE_SEARCH_RESULTS_HTML)
        assert filings[0]["date_filed"] == "01/15/2026"

    def test_full_url_built(self):
        client = DisclosureClient()
        filings = client._parse_senate_search_results(SENATE_SEARCH_RESULTS_HTML)
        assert filings[0]["ptr_link"].startswith("https://efdsearch.senate.gov")

    def test_empty_table_returns_empty(self):
        client = DisclosureClient()
        html = "<html><body><p>No results</p></body></html>"
        assert client._parse_senate_search_results(html) == []


# ============================================================================
# Tests: Disclosure Client – PTR Detail Parsing
# ============================================================================


class TestSenatePtrParsing:
    """Test parsing of individual Senate PTR detail pages."""

    def test_parses_stock_transactions(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        # Should have 2 (AAPL + MSFT), not the municipal bond
        assert len(txns) == 2

    def test_filters_municipal_bonds(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        tickers = [t["ticker"] for t in txns]
        assert "--" not in tickers

    def test_extracts_ticker(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        assert txns[0]["ticker"] == "AAPL"
        assert txns[1]["ticker"] == "MSFT"

    def test_extracts_amount(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        assert txns[0]["amount"] == "$1,001 - $15,000"

    def test_sale_full_normalized(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        assert txns[1]["transaction_type"] == "Sale"

    def test_extracts_owner(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        assert txns[0]["owner"] == "Self"
        assert txns[1]["owner"] == "Spouse"

    def test_extracts_transaction_date(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        assert txns[0]["transaction_date"] == date(2025, 12, 15)

    def test_extracts_comment(self):
        client = DisclosureClient()
        txns = client._parse_senate_ptr_page(
            SENATE_PTR_DETAIL_HTML, "https://example.com/ptr/1"
        )
        assert txns[0]["comment"] == ""
        assert txns[1]["comment"] == "Position closed"


# ============================================================================
# Tests: PoliticianTrade ORM Model
# ============================================================================


class TestPoliticianTradeModel:
    """Test the PoliticianTrade SQLAlchemy model."""

    def test_create_instance(self):
        trade = PoliticianTrade(
            politician_name="Tommy Tuberville",
            chamber="Senate",
            ticker="AAPL",
            transaction_date=date(2025, 12, 15),
            transaction_type="Purchase",
            amount_range="$1,001 - $15,000",
        )
        assert trade.politician_name == "Tommy Tuberville"
        assert trade.chamber == "Senate"
        assert trade.ticker == "AAPL"

    def test_repr(self):
        trade = PoliticianTrade(
            politician_name="Mark Kelly",
            ticker="MSFT",
            transaction_type="Sale",
            transaction_date=date(2025, 12, 20),
            amount_range="$50,001 - $100,000",
        )
        repr_str = repr(trade)
        assert "Mark Kelly" in repr_str
        assert "MSFT" in repr_str
        assert "Sale" in repr_str

    def test_nullable_fields(self):
        trade = PoliticianTrade(
            politician_name="Test",
            ticker="AAPL",
        )
        assert trade.party is None
        assert trade.state is None
        assert trade.owner is None
        assert trade.comment is None

    def test_table_name(self):
        assert PoliticianTrade.__tablename__ == "politician_trades"

    def test_schema(self):
        assert PoliticianTrade.__table_args__[-1]["schema"] == "signals"


# ============================================================================
# Tests: PoliticianTradesCollector
# ============================================================================


class TestPoliticianTradesCollector:
    """Test PoliticianTradesCollector with mocked client."""

    def test_store_writes_trades(self):
        collector = PoliticianTradesCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "politician_name": "Tommy Tuberville",
                "chamber": "Senate",
                "party": None,
                "state": None,
                "ticker": "AAPL",
                "transaction_date": date(2025, 12, 15),
                "disclosure_date": date(2026, 1, 15),
                "transaction_type": "Purchase",
                "amount_range": "$1,001 - $15,000",
                "owner": "Self",
                "asset_description": "Apple Inc.",
                "comment": "",
                "source_url": "https://example.com/ptr/1",
                "raw_data": {},
            }
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.flush.assert_called()

    def test_store_dedup_counts(self):
        """Duplicate records should not increment written count."""
        collector = PoliticianTradesCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0  # duplicate – not written
        session.execute.return_value = mock_result

        data = [
            {
                "politician_name": "Tommy Tuberville",
                "chamber": "Senate",
                "party": None,
                "state": None,
                "ticker": "AAPL",
                "transaction_date": date(2025, 12, 15),
                "disclosure_date": date(2026, 1, 15),
                "transaction_type": "Purchase",
                "amount_range": "$1,001 - $15,000",
                "owner": "Self",
                "asset_description": "Apple Inc.",
                "comment": "",
                "source_url": "https://example.com/ptr/1",
                "raw_data": {},
            }
        ]

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 0

    def test_extract_state_with_parens(self):
        assert PoliticianTradesCollector._extract_state("Kelly, Mark (AZ)") == "AZ"

    def test_extract_state_no_match(self):
        result = PoliticianTradesCollector._extract_state(
            "Tuberville, Tommy (Senator)"
        )
        # "Senator" won't match 2-letter state
        assert result is None

    def test_extract_state_empty(self):
        assert PoliticianTradesCollector._extract_state("") is None
        assert PoliticianTradesCollector._extract_state(None) is None

    def test_parse_disclosure_date(self):
        assert PoliticianTradesCollector._parse_disclosure_date(
            "01/15/2026"
        ) == date(2026, 1, 15)

    def test_parse_disclosure_date_empty(self):
        assert PoliticianTradesCollector._parse_disclosure_date("") is None

    @patch.object(DisclosureClient, "fetch_senate_ptrs")
    @patch.object(DisclosureClient, "fetch_senate_ptr_transactions")
    @patch.object(DisclosureClient, "__init__", return_value=None)
    def test_fetch_filters_invalid_tickers(
        self, mock_init, mock_txns, mock_ptrs
    ):
        """Trades with empty tickers should be filtered out."""
        mock_ptrs.return_value = [
            {
                "first_name": "Test",
                "last_name": "Senator",
                "office": "Senator (CA)",
                "report_type": "PTR",
                "date_filed": "01/01/2026",
                "ptr_link": "https://example.com/ptr/1",
            }
        ]
        mock_txns.return_value = [
            {
                "transaction_date": date(2025, 12, 1),
                "owner": "Self",
                "ticker": "",
                "asset_name": "No Ticker Corp",
                "asset_type": "Stock",
                "transaction_type": "Purchase",
                "amount": "$1,001 - $15,000",
                "comment": "",
            },
            {
                "transaction_date": date(2025, 12, 1),
                "owner": "Self",
                "ticker": "AAPL",
                "asset_name": "Apple Inc.",
                "asset_type": "Stock",
                "transaction_type": "Purchase",
                "amount": "$15,001 - $50,000",
                "comment": "",
            },
        ]

        collector = PoliticianTradesCollector()
        collector.client = DisclosureClient.__new__(DisclosureClient)
        collector.client.fetch_senate_ptrs = mock_ptrs
        collector.client.fetch_senate_ptr_transactions = mock_txns

        session = MagicMock()
        trades = collector.fetch(session)

        # Only AAPL should be included (empty ticker filtered)
        assert len(trades) == 1
        assert trades[0]["ticker"] == "AAPL"

    def test_name_property(self):
        collector = PoliticianTradesCollector()
        assert collector.name == "politician_trades_collector"
