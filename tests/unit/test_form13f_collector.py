"""Tests for Form13FCollector – infotable parsing."""

from datetime import date
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from trading_signals.collectors.form13f_collector import (
    TOP_FILERS,
    Form13FCollector,
    aggregate_holdings,
    parse_13f_infotable,
    value_multiplier,
)


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class TestTopFilers:
    """Test the top filers configuration."""

    def test_contains_berkshire(self):
        assert "0001067983" in TOP_FILERS
        name = TOP_FILERS["0001067983"]
        assert "Berkshire" in name or "Buffett" in name

    def test_contains_renaissance(self):
        assert "0001037389" in TOP_FILERS

    def test_at_least_20_filers(self):
        assert len(TOP_FILERS) >= 20

    def test_all_ciks_are_10_digits(self):
        for cik in TOP_FILERS:
            assert len(cik) == 10
            assert cik.isdigit()


class TestValueMultiplier:
    """C1: $ thousands before 2023-01-03, whole dollars afterwards."""

    def test_old_filing_in_thousands(self):
        assert value_multiplier(date(2022, 11, 14), date(2022, 9, 30)) == 1000

    def test_new_filing_in_dollars(self):
        assert value_multiplier(date(2023, 2, 14), date(2022, 12, 31)) == 1

    def test_late_amendment_of_old_period_in_dollars(self):
        assert value_multiplier(date(2023, 5, 1), date(2022, 6, 30)) == 1

    def test_period_fallback(self):
        assert value_multiplier(None, date(2022, 9, 30)) == 1000
        assert value_multiplier(None, date(2022, 12, 31)) == 1

    def test_no_dates_defaults_to_dollars(self):
        assert value_multiplier(None, None) == 1


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

    def test_market_value_in_dollars_for_new_filings(self):
        """Since 2023 13F values are whole dollars – no scaling."""
        holdings = parse_13f_infotable(
            self.SAMPLE_XML,
            filing_date=date(2026, 2, 14),
            report_period=date(2025, 12, 31),
        )
        assert holdings[0]["market_value"] == 50_000
        assert holdings[1]["market_value"] == 75_000

    def test_market_value_scaled_for_old_filings(self):
        """Pre-2023 filings report thousands → converted to dollars."""
        holdings = parse_13f_infotable(
            self.SAMPLE_XML,
            filing_date=date(2022, 8, 12),
            report_period=date(2022, 6, 30),
        )
        assert holdings[0]["market_value"] == 50_000_000  # 50000 * 1000
        assert holdings[1]["market_value"] == 75_000_000

    def test_put_call_captured(self):
        """Put/Call indicator captured; plain share positions are 'SH'."""
        holdings = parse_13f_infotable(self.SAMPLE_XML)
        assert holdings[0]["put_call"] == "SH"
        assert holdings[1]["put_call"] == "PUT"

    def test_filer_metadata_passed_through(self):
        holdings = parse_13f_infotable(
            self.SAMPLE_XML,
            filer_name="Berkshire Hathaway",
            filer_cik="0001067983",
            filing_date=date(2026, 2, 14),
            report_period=date(2025, 12, 31),
            accession_number="0000950123-26-000001",
            form_type="13F-HR",
        )
        assert holdings[0]["filer_name"] == "Berkshire Hathaway"
        assert holdings[0]["filer_cik"] == "0001067983"
        assert holdings[0]["filing_date"] == date(2026, 2, 14)
        assert holdings[0]["report_period"] == date(2025, 12, 31)
        assert holdings[0]["accession_number"] == "0000950123-26-000001"
        assert holdings[0]["form_type"] == "13F-HR"
        assert holdings[0]["amendment_type"] is None

    def test_invalid_xml_returns_empty(self):
        holdings = parse_13f_infotable("<broken>xml")
        assert holdings == []

    def test_empty_infotable_returns_empty(self):
        xml = """<?xml version="1.0"?>
        <informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
        </informationTable>"""
        holdings = parse_13f_infotable(xml)
        assert holdings == []


class TestAggregation:
    """C2: lines with same (filer, period, cusip, put_call) are summed."""

    def _h(self, cusip="037833100", put_call="SH", shares=10.0, value=100.0):
        return {
            "filer_cik": "0001", "report_period": date(2025, 12, 31),
            "cusip": cusip, "put_call": put_call, "shares": shares,
            "market_value": value, "accession_number": "acc",
        }

    def test_discretion_lines_summed(self):
        rows = aggregate_holdings([self._h(), self._h(shares=5.0, value=None)])
        assert len(rows) == 1
        assert rows[0]["shares"] == 15.0
        assert rows[0]["market_value"] == 100.0

    def test_put_call_kept_separate(self):
        rows = aggregate_holdings(
            [self._h(), self._h(put_call="PUT"), self._h(put_call="CALL")]
        )
        assert sorted(r["put_call"] for r in rows) == ["CALL", "PUT", "SH"]


class TestForm13FCollectorUnit:
    """Test Form13FCollector logic with mocked dependencies."""

    def _holding(self, **kw):
        h = {
            "filer_name": "Berkshire Hathaway",
            "filer_cik": "0001067983",
            "report_period": date(2025, 12, 31),
            "filing_date": date(2026, 2, 14),
            "ticker": None,
            "cusip": "037833100",
            "shares": 250000.0,
            "market_value": 50_000_000,
            "put_call": "SH",
            "accession_number": "acc1",
            "form_type": "13F-HR",
            "amendment_type": None,
            "source_url": "https://example.com",
        }
        h.update(kw)
        return h

    def test_store_writes_holdings(self):
        collector = Form13FCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        fetched, written = collector.store(session, [self._holding()])

        assert fetched == 1
        assert written == 1
        session.flush.assert_called()
        sql = _sql(session.execute.call_args_list[0][0][0])
        assert (
            "ON CONFLICT (filer_cik, report_period, cusip, put_call) DO NOTHING"
            in sql
        )

    def test_store_aggregates_and_bulk_inserts(self):
        collector = Form13FCollector()
        session = MagicMock()
        session.execute.return_value.rowcount = 2
        data = [
            self._holding(),
            self._holding(shares=50_000.0, market_value=10_000_000),
            self._holding(put_call="CALL"),
        ]
        fetched, written = collector.store(session, data)
        assert (fetched, written) == (3, 2)
        assert session.execute.call_count == 1
        stmt = session.execute.call_args_list[0][0][0]
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert params["shares_m0"] == 300_000.0
        assert params["put_call_m1"] == "CALL"

    def test_store_replaces_flagged_periods(self):
        collector = Form13FCollector()
        collector._replace_keys = {("0001067983", date(2025, 12, 31))}
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        collector.store(session, [self._holding()])

        assert session.execute.call_count == 2
        delete_sql = _sql(session.execute.call_args_list[0][0][0])
        assert delete_sql.startswith("DELETE FROM signals.form13f_holdings")
        assert "filer_cik = '0001067983'" in delete_sql
        assert "report_period = '2025-12-31'" in delete_sql
        assert collector._replace_keys == set()


class TestForm13FPeriodLogic:
    """C2: all filings in the window, restatements replace, new holdings add."""

    INFOTABLE = """<informationTable><infoTable>
        <cusip>037833100</cusip><value>1</value>
        <shrsOrPrnAmt><sshPrnamt>1</sshPrnamt></shrsOrPrnAmt>
    </infoTable></informationTable>"""

    def _filing(self, acc, form="13F-HR", filed="2026-02-14"):
        return {
            "accession_number": acc, "filing_date": filed,
            "primary_document": "primary_doc.xml",
            "report_period": "2025-12-31", "form_type": form,
            "acceptance_datetime": None,
        }

    def _collector(self, amendment_types=None):
        collector = Form13FCollector()
        client = MagicMock()
        client.find_infotable_document.return_value = "infotable.xml"
        client.download_filing_document.return_value = self.INFOTABLE
        client.pad_cik.side_effect = lambda c: str(c).zfill(10)
        types = amendment_types or {}
        client.get_13f_amendment_type.side_effect = lambda cik, acc: types[acc]
        collector.sec_client = client
        return collector, client

    def _downloaded(self, client):
        return [c[0][1] for c in client.download_filing_document.call_args_list]

    def test_new_original_replaces_period(self):
        collector, client = self._collector()
        rows, replace, errors = collector._process_period(
            "0001", "X", [self._filing("orig")], stored=set()
        )
        assert replace is True and errors == 0
        assert [r["accession_number"] for r in rows] == ["orig"]

    def test_restatement_is_base(self):
        collector, client = self._collector({"amd": "RESTATEMENT"})
        filings = [
            self._filing("amd", "13F-HR/A", "2026-03-01"),
            self._filing("orig", "13F-HR", "2026-02-14"),
        ]
        rows, replace, _ = collector._process_period("0001", "X", filings, set())
        assert replace is True
        assert self._downloaded(client) == ["amd"]  # original superseded
        assert rows[0]["amendment_type"] == "RESTATEMENT"

    def test_new_holdings_amendment_after_stored_base_adds(self):
        collector, client = self._collector({"amd": "NEW HOLDINGS"})
        filings = [
            self._filing("orig"),
            self._filing("amd", "13F-HR/A", "2026-03-01"),
        ]
        rows, replace, _ = collector._process_period(
            "0001", "X", filings, stored={"orig"}
        )
        assert replace is False
        assert self._downloaded(client) == ["amd"]

    def test_new_original_with_new_holdings_amendment(self):
        collector, client = self._collector({"amd": "NEW HOLDINGS"})
        filings = [
            self._filing("amd", "13F-HR/A", "2026-03-01"),
            self._filing("orig"),
        ]
        rows, replace, _ = collector._process_period("0001", "X", filings, set())
        assert replace is True
        assert self._downloaded(client) == ["orig", "amd"]  # oldest first

    def test_unknown_amendment_type_treated_as_new_holdings(self):
        collector, client = self._collector()
        client.get_13f_amendment_type.side_effect = RuntimeError("404")
        rows, replace, errors = collector._process_period(
            "0001", "X", [self._filing("amd", "13F-HR/A")], set()
        )
        assert replace is False
        assert errors == 1
        assert self._downloaded(client) == ["amd"]

    def test_failed_base_stores_nothing(self):
        collector, client = self._collector({"amd": "NEW HOLDINGS"})
        client.download_filing_document.side_effect = RuntimeError("503")
        filings = [self._filing("orig"), self._filing("amd", "13F-HR/A", "2026-03-01")]
        rows, replace, errors = collector._process_period("0001", "X", filings, set())
        assert (rows, replace, errors) == ([], False, 1)

    def test_fetch_tracks_status_and_flags_replacement(self):
        collector, client = self._collector()

        def filings(cik, since_date=None):
            if cik == "0001067983":
                return [self._filing("orig")]
            if cik == "0001649339":
                raise RuntimeError("HTTP 500")
            return []

        client.get_recent_13f_filings.side_effect = filings
        session = MagicMock()
        rows = collector.fetch(session)

        assert len(rows) == 1
        assert rows[0]["filer_cik"] == "0001067983"
        assert collector._replace_keys == {("0001067983", date(2025, 12, 31))}
        # 20 submissions requests (1 failed) + 1 filing
        assert collector._attempts == len(TOP_FILERS) + 1
        assert collector._errors == 1
        client.clear_cache.assert_called_once()
        session.commit.assert_called()  # read txn released before HTTP
