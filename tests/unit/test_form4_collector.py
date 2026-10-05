"""Tests for Form4Collector – XML parsing and transaction extraction."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from trading_signals.collectors.form4_collector import (
    TRANSACTION_CODES,
    Form4Collector,
    _text,
    parse_form4_xml,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load(name: str = "form4_sample.xml") -> str:
    with open(FIXTURES_DIR / name) as f:
        return f.read()


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class TestXMLHelpers:
    """Test XML helper functions."""

    def test_text_found(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring("<a><b> x </b></a>")
        assert _text(root, "b") == "x"

    def test_text_missing(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring("<a/>")
        assert _text(root, "b") is None


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
        return _load()

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
        assert txns[0]["insider_cik"] == "0001234567"
        assert txns[0]["owner_count"] == 1

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


class TestFilingKeyFields:
    """H1: accession/form type/row index/acceptance time on every row."""

    def test_dedup_key_fields(self):
        acc_dt = datetime(2026, 4, 12, 20, 1, 2, tzinfo=UTC)
        txns = parse_form4_xml(
            _load(),
            accession_number="0001-26-000001",
            form_type="4",
            acceptance_datetime=acc_dt,
        )
        assert [t["accession_number"] for t in txns] == ["0001-26-000001"] * 3
        assert all(t["form_type"] == "4" for t in txns)
        assert all(t["acceptance_datetime"] == acc_dt for t in txns)
        # row_index counted per table (non-derivative 0,1; derivative 0)
        assert [(t["is_derivative"], t["row_index"]) for t in txns] == [
            (False, 0), (False, 1), (True, 0),
        ]

    def test_identical_lines_get_distinct_keys(self):
        """Two lines with same date/type/shares and NULL price stay distinct."""
        txns = parse_form4_xml(
            _load("form4_amendment_joint.xml"), accession_number="acc"
        )
        assert len(txns) == 2
        assert txns[1]["price_per_share"] is None
        keys = {(t["accession_number"], t["is_derivative"], t["row_index"])
                for t in txns}
        assert len(keys) == 2

    def test_form_type_falls_back_to_document_type(self):
        txns = parse_form4_xml(_load("form4_amendment_joint.xml"))
        assert txns[0]["form_type"] == "4/A"
        assert txns[0]["raw_data"]["date_of_original_submission"] == "2026-04-12"

    def test_multiple_owners(self):
        txns = parse_form4_xml(_load("form4_amendment_joint.xml"))
        t = txns[0]
        assert t["insider_name"] == "Example Fund LP"
        assert t["insider_cik"] == "0001111111"
        assert t["insider_title"] == "10% Owner"
        assert t["owner_count"] == 2
        names = [o["name"] for o in t["raw_data"]["reporting_owners"]]
        assert names == ["Example Fund LP", "Example GP LLC"]


class TestIssuerHandling:
    """LOW: never store the owner CIK as issuer; skip other issuers' filings."""

    XML_NO_ISSUER_CIK = """<?xml version="1.0"?>
    <ownershipDocument>
        <issuer><issuerName>X Corp</issuerName></issuer>
        <reportingOwner>
            <reportingOwnerId>
                <rptOwnerCik>0009999999</rptOwnerCik>
                <rptOwnerName>Owner</rptOwnerName>
            </reportingOwnerId>
        </reportingOwner>
        <nonDerivativeTable><nonDerivativeTransaction>
            <transactionDate><value>2026-04-10</value></transactionDate>
            <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""

    def test_no_owner_cik_fallback(self):
        txns = parse_form4_xml(self.XML_NO_ISSUER_CIK)
        assert txns[0]["cik"] is None

    def test_expected_issuer_used_when_xml_has_none(self):
        txns = parse_form4_xml(self.XML_NO_ISSUER_CIK, issuer_cik="0000123456")
        assert txns[0]["cik"] == "0000123456"

    def test_filing_of_other_issuer_skipped(self):
        """Company listed only as reporting owner → filing is not ours."""
        txns = parse_form4_xml(_load(), ticker="BRK-B", issuer_cik="0001067983")
        assert txns == []

    def test_matching_issuer_ignores_leading_zeros(self):
        txns = parse_form4_xml(_load(), issuer_cik="320193")
        assert len(txns) == 3


class TestForm4CollectorUnit:
    """Test Form4Collector logic with mocked dependencies."""

    def _row(self, **kw) -> dict:
        row = {
            "ticker": "AAPL",
            "company_name": "Apple Inc",
            "cik": "0000320193",
            "insider_name": "Cook Timothy D",
            "insider_title": "CEO",
            "insider_cik": "0001234567",
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
            "accession_number": "0001-26-000001",
            "form_type": "4",
            "row_index": 0,
        }
        row.update(kw)
        return row

    def test_store_writes_transactions(self):
        """store() should insert transactions into session."""
        collector = Form4Collector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        fetched, written = collector.store(session, [self._row()])

        assert fetched == 1
        assert written == 1
        session.flush.assert_called()

    def test_store_uses_bulk_insert_on_filing_key(self):
        collector = Form4Collector()
        session = MagicMock()
        session.execute.return_value.rowcount = 2

        rows = [self._row(), self._row(row_index=1)]
        fetched, written = collector.store(session, rows)

        assert (fetched, written) == (2, 2)
        assert session.execute.call_count == 1  # one multi-row INSERT
        sql = str(
            session.execute.call_args_list[0][0][0].compile(
                dialect=postgresql.dialect()
            )
        )
        assert "ON CONFLICT (accession_number, is_derivative, row_index)" in sql
        assert "WHERE accession_number IS NOT NULL DO NOTHING" in sql

    def test_store_skips_rows_without_accession(self):
        collector = Form4Collector()
        session = MagicMock()
        session.execute.return_value.rowcount = 1

        fetched, written = collector.store(
            session, [self._row(), self._row(accession_number=None)]
        )
        assert fetched == 2
        assert written == 1

    def test_amendment_deletes_superseded_rows(self):
        collector = Form4Collector()
        session = MagicMock()
        session.execute.return_value.rowcount = 1

        amendment = self._row(
            accession_number="0001-26-000009",
            form_type="4/A",
            filing_date=date(2026, 4, 15),
            raw_data={"date_of_original_submission": "2026-04-12"},
        )
        collector.store(session, [amendment])

        # INSERT + one DELETE for the amended transaction
        assert session.execute.call_count == 2
        sql = _sql(session.execute.call_args_list[1][0][0])
        assert sql.startswith("DELETE FROM signals.insider_trades")
        assert "accession_number != '0001-26-000009'" in sql
        assert "insider_trades.filing_date >= '2026-04-12'" in sql
        assert "insider_trades.filing_date < '2026-04-15'" in sql
        assert "IS DISTINCT FROM '4/A'" in sql

    def test_original_filing_deletes_nothing(self):
        collector = Form4Collector()
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        collector.store(session, [self._row()])
        assert session.execute.call_count == 1

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


class TestForm4Fetch:
    """H5: run-status tracking, stored-accession skip, no repeated CIKs."""

    def _collector(self):
        collector = Form4Collector()
        client = MagicMock()
        ciks = {
            "AAPL": "0000320193",
            "GOOG": "0001652044",
            "GOOGL": "0001652044",
            "MSFT": "0000789019",
        }
        client.get_cik.side_effect = ciks.get

        def filings(cik, since_date=None):
            if cik == "0000320193":
                return [
                    {"accession_number": "acc-new", "filing_date": "2026-04-12",
                     "primary_document": "xslF345X05/doc.xml", "form_type": "4",
                     "acceptance_datetime": None},
                    {"accession_number": "acc-stored", "filing_date": "2026-04-11",
                     "primary_document": "doc.xml", "form_type": "4",
                     "acceptance_datetime": None},
                ]
            if cik == "0000789019":
                raise RuntimeError("HTTP 503")
            return []

        client.get_recent_form4_filings.side_effect = filings
        client.download_filing_document.return_value = _load()
        client.pad_cik.side_effect = lambda c: str(c).zfill(10)
        collector.sec_client = client
        return collector, client

    def test_fetch(self):
        collector, client = self._collector()
        session = MagicMock()
        session.execute.return_value.all.return_value = [("acc-stored",)]

        with patch.object(
            collector, "get_active_tickers",
            return_value=["AAPL", "GOOG", "GOOGL", "MSFT"],
        ):
            txns = collector.fetch(session)

        assert len(txns) == 3
        assert {t["accession_number"] for t in txns} == {"acc-new"}
        assert all(t["ticker"] == "AAPL" for t in txns)
        # stored accession not downloaded again; XSLT prefix stripped
        client.download_filing_document.assert_called_once_with(
            "0000320193", "acc-new", "doc.xml"
        )
        # GOOGL shares GOOG's CIK → submissions fetched once per CIK
        assert client.get_recent_form4_filings.call_count == 3
        client.clear_cache.assert_called_once()
        # 3 submissions requests (1 failed) + 1 filing
        assert collector._attempts == 4
        assert collector._errors == 1
        # read transaction released before HTTP work
        session.commit.assert_called()

    def test_filing_error_recorded(self):
        collector, client = self._collector()
        client.download_filing_document.side_effect = RuntimeError("boom")
        session = MagicMock()
        with patch.object(collector, "get_active_tickers", return_value=["AAPL"]):
            txns = collector.fetch(session)
        assert txns == []
        assert collector._errors == 2  # both AAPL filings failed
        assert collector._attempts == 3
