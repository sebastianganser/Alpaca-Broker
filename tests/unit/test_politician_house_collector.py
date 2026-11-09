"""Tests for the House part of the PoliticianTradesCollector.

No network, no database: the House client is a fake, ``extract_pdf_text`` is
patched to return the real fixture texts of ``tests/fixtures/house_ptr``,
sessions are mocks and ``NewTickerOnboarder`` is patched.
"""

import io
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors import politician_trades_collector as ptc
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.politician_trades_collector import (
    HOUSE_WINDOW_DAYS,
    PoliticianTradesCollector,
)
from trading_signals.utils.retention import data_start_date

FIX = Path(__file__).parent.parent / "fixtures" / "house_ptr"
EXTRACT = "trading_signals.collectors.politician_trades_collector.extract_pdf_text"
ONBOARDER = "trading_signals.universe.onboarder.NewTickerOnboarder"

HEADER = (
    "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
)


def _index(year: int, filings: list[tuple]) -> bytes:
    """Index ZIP; filings = (last, first, type, state_dst, filing_date, doc_id)."""
    lines = [HEADER]
    for last, first, ftype, sd, fdate, doc_id in filings:
        lines.append(
            f"\t{last}\t{first}\t\t{ftype}\t{sd}\t{fdate.year}\t"
            f"{fdate.month}/{fdate.day}/{fdate.year}\t{doc_id}\r\n"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{year}FD.txt", b"\xef\xbb\xbf" + "".join(lines).encode())
    return buf.getvalue()


class FakeHouseClient:
    def __init__(self, indexes: dict[int, bytes], failing: set[str] = frozenset()):
        self.indexes = indexes
        self.failing = failing
        self.pdf_calls: list[tuple[int, str]] = []
        self.index_calls: list[int] = []

    def fetch_index(self, year: int) -> bytes:
        self.index_calls.append(year)
        if year not in self.indexes:
            raise RuntimeError(f"no index {year}")
        return self.indexes[year]

    def fetch_ptr_pdf(self, year: int, doc_id: str) -> bytes:
        self.pdf_calls.append((year, doc_id))
        if doc_id in self.failing:
            raise RuntimeError("boom")
        return f"doc:{doc_id}".encode()


def _fixture_extract(pdf: bytes) -> str:
    doc_id = pdf.decode().split(":")[1]
    path = FIX / f"{doc_id}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _collector(client, house_since=None):
    c = PoliticianTradesCollector(
        house_since=house_since, senate_enabled=False, house_client=client
    )
    c._reset_run_state()
    return c


def _session(stored_urls=()):
    s = MagicMock()
    s.execute.return_value.all.return_value = [(u,) for u in stored_urls]
    return s


TODAY = date.today()
RECENT = TODAY - timedelta(days=5)
SINCE = TODAY - timedelta(days=30)


def _fetch(client, stored=(), house_since=SINCE, years=None):
    c = _collector(client, house_since)
    with patch(EXTRACT, side_effect=_fixture_extract):
        trades = c.fetch_house(_session(stored), years=years)
    return c, trades


class TestHouseFetch:
    def test_maps_rows(self):
        idx = _index(
            RECENT.year,
            [("Lowenthal", "Alan S.", "P", "CA47", RECENT, "20022162")],
        )
        client = FakeHouseClient({RECENT.year: idx})
        c, trades = _fetch(client)

        assert [t["ticker"] for t in trades] == ["ATVI", "GTLS", "CRM"]
        t = trades[0]
        assert t["chamber"] == "House"
        assert t["politician_name"] == "Alan S. Lowenthal"
        assert t["party"] is None
        assert t["state"] == "CA"
        assert t["transaction_type"] == "Purchase"
        assert trades[2]["transaction_type"] == "Sale"
        assert t["owner"] == "Joint"
        assert t["amount_range"] == "$1,001 - $15,000"
        assert t["transaction_date"] == date(2022, 12, 12)
        assert t["disclosure_date"] == RECENT
        assert t["asset_description"] == "Activision Blizzard, Inc (ATVI) [ST]"
        assert t["comment"] == ""
        assert t["source_url"] == (
            f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/"
            f"{RECENT.year}/20022162.pdf"
        )
        assert t["raw_data"] == {
            "asset_type": "ST",
            "doc_id": "20022162",
            "state_dst": "CA47",
            "notification_date": "2022-12-19",
            "raw_transaction_type": "P",
            "subholding": "Neuberger Berman - Malumed Lowenthal Rev Trust #2",
        }
        assert c._errors == 0

    def test_owner_and_type_mapping(self):
        idx = _index(
            RECENT.year,
            [
                ("Pelosi", "Nancy", "P", "CA12", RECENT, "20020208"),
                ("Burgess", "Michael C.", "P", "TX26", RECENT, "20022087"),
                ("Moskowitz", "Jared", "P", "FL23", RECENT, "20033421"),
            ],
        )
        _, trades = _fetch(FakeHouseClient({RECENT.year: idx}))
        pelosi = [t for t in trades if t["politician_name"] == "Nancy Pelosi"]
        assert {t["owner"] for t in pelosi} == {"Spouse"}
        assert {t["transaction_type"] for t in pelosi} == {"Sale"}  # S (partial)
        assert pelosi[0]["comment"].startswith("Contribution of 3,000 shares")
        burgess = [t for t in trades if t["politician_name"] == "Michael C. Burgess"]
        assert {t["owner"] for t in burgess} == {"Self"}
        mosk = [t for t in trades if t["politician_name"] == "Jared Moskowitz"]
        assert {t["owner"] for t in mosk} == {"Child", "Self"}

    def test_exchange_and_unknown_types(self):
        c = _collector(FakeHouseClient({}))
        filing = MagicMock(doc_id="1", url="u", state="CA", state_dst="CA1")
        filing.name = "X Y"
        filing.filing_date = RECENT
        row = ptc.PtrRow(
            owner_code=None, asset_text="A (AA) [ST]", asset_code="ST",
            ticker="AA", raw_type="E", transaction_date=RECENT,
            notification_date=RECENT, amount="$1,001 - $15,000",
        )
        assert c._house_trade(filing, row)["transaction_type"] == "Exchange"
        row.raw_type = "Z"
        assert c._house_trade(filing, row) is None

    def test_skips_stored_paper_non_ptr_and_old_filings(self):
        old = TODAY - timedelta(days=200)
        idx = _index(
            RECENT.year,
            [
                ("A", "Stored", "P", "CA01", RECENT, "20022162"),
                ("B", "Paper", "P", "CA02", RECENT, "9116361"),
                ("C", "Annual", "A", "CA03", RECENT, "10063333"),
                ("D", "Old", "P", "CA04", old, "20022087"),
                ("E", "New", "P", "CA05", RECENT, "20035727"),
            ],
        )
        client = FakeHouseClient({RECENT.year: idx, old.year: idx})
        stored = ["https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/"
                  f"{RECENT.year}/20022162.pdf"]
        c, trades = _fetch(client, stored=stored, years=[RECENT.year])

        assert client.pdf_calls == [(RECENT.year, "20035727")]
        s = c.house_stats
        assert (s.filings_in_window, s.already_stored, s.new) == (3, 1, 2)
        assert s.paper_skipped == 1
        assert s.parsed == 1
        assert s.stock_rows == 8 and len(trades) == 8
        assert s.non_stock_rows == 0
        assert c._errors == 0

    def test_counts_non_stock_and_unreadable(self):
        idx = _index(
            RECENT.year,
            [
                ("A", "Bonds", "P", "CA01", RECENT, "20035689"),
                ("B", "Llc", "P", "CA02", RECENT, "20035553"),
                ("C", "Scan", "P", "CA03", RECENT, "20099999"),  # no fixture: no text
                ("D", "Greene", "P", "GA14", RECENT, "20026473"),
            ],
        )
        c, trades = _fetch(FakeHouseClient({RECENT.year: idx}))
        s = c.house_stats
        assert s.unreadable == 1
        assert s.parsed == 3
        assert s.non_stock_rows == 2 + 1 + 1
        assert s.stock_rows == 14
        assert len(trades) == 14
        assert c._errors == 0
        assert c._partial_reasons == ()

    def test_download_errors_are_counted_and_do_not_abort(self):
        idx = _index(
            RECENT.year,
            [
                ("A", "Bad", "P", "CA01", RECENT, "20022162"),
                ("B", "Good", "P", "CA02", RECENT, "20022087"),
            ],
        )
        client = FakeHouseClient({RECENT.year: idx}, failing={"20022162"})
        c, trades = _fetch(client)
        assert len(trades) == 4
        assert c.house_stats.errors == 1
        assert c._errors == 1 and c._attempts == 3  # index + 2 filings

    def test_index_error_is_an_error_and_other_years_continue(self):
        last_year = TODAY.year - 1
        since = date(last_year, 1, 1)
        idx = _index(TODAY.year, [("A", "New", "P", "CA01", RECENT, "20022162")])
        client = FakeHouseClient({TODAY.year: idx})  # last_year index missing
        c, trades = _fetch(client, house_since=since)
        assert client.index_calls == [last_year, TODAY.year]
        assert len(trades) == 3
        assert c.house_stats.errors == 1

    def test_parse_mismatch_is_reported_once_via_mark_partial(self):
        idx = _index(
            RECENT.year,
            [
                ("A", "One", "P", "CA01", RECENT, "20022162"),
                ("B", "Two", "P", "CA02", RECENT, "20022087"),
            ],
        )
        c = _collector(FakeHouseClient({RECENT.year: idx}), SINCE)

        def broken(pdf: bytes) -> str:
            return _fixture_extract(pdf).replace(
                "* For the complete list",
                "  Orphan Corp (ORPH) [ST]\n* For the complete list",
            )

        with patch(EXTRACT, side_effect=broken):
            trades = c.fetch_house(_session())
        assert len(c._partial_reasons) == 1
        assert "20022162" in c._partial_reasons[0]
        assert "20022087" in c._partial_reasons[0]
        assert c.house_stats.mismatches == ["20022162", "20022087"]
        # All or nothing per filing: a stored source_url marks a filing as done,
        # so an incomplete filing stores no row and is fetched again later.
        assert trades == []
        assert c.house_stats.stock_rows == 0
        status, _ = c._compute_status(len(trades))
        assert status == "partial"

    def test_unmappable_stock_row_makes_the_filing_incomplete(self):
        idx = _index(RECENT.year, [("A", "One", "P", "CA01", RECENT, "20022162")])
        c = _collector(FakeHouseClient({RECENT.year: idx}), SINCE)

        def bad_date(pdf: bytes) -> str:  # month 13: the row parses, the date not
            return _fixture_extract(pdf).replace("12/12/2022", "13/12/2022", 1)

        with patch(EXTRACT, side_effect=bad_date):
            trades = c.fetch_house(_session())
        assert trades == []
        assert c.house_stats.rows_invalid == 1
        assert c.house_stats.mismatches == ["20022162"]
        assert "20022162" in c._partial_reasons[0]

    def test_default_since_is_window_bounded_by_retention(self):
        c = PoliticianTradesCollector(senate_enabled=False)
        today = date(2026, 11, 9)
        assert c._resolve_house_since(today) == max(
            today - timedelta(days=HOUSE_WINDOW_DAYS), data_start_date()
        )
        assert c._resolve_house_since(today) == date(2026, 9, 10)

    def test_explicit_since_wins_inside_the_retention_window(self):
        c = PoliticianTradesCollector(house_since=date(2024, 1, 1))
        assert c._resolve_house_since(date(2026, 11, 9)) == date(2024, 1, 1)

    def test_explicit_since_is_clamped_to_the_retention_window(self):
        c = PoliticianTradesCollector(house_since=date(2015, 1, 1))
        assert c._resolve_house_since(date(2026, 11, 9)) == data_start_date()

    def test_reads_house_urls_before_http(self):
        client = FakeHouseClient({})
        c = _collector(client, SINCE)
        session = _session()
        with patch(EXTRACT, side_effect=_fixture_extract):
            c.fetch_house(session, years=[])
        stmt = session.execute.call_args[0][0]
        assert "'House'" in str(stmt.compile(compile_kwargs={"literal_binds": True}))
        session.commit.assert_called()  # read transaction released


class TestFetchDispatch:
    def test_senate_off_by_default_logs_and_never_touches_client(self):
        c = PoliticianTradesCollector(
            house_since=SINCE, house_client=FakeHouseClient({})
        )
        c.client = MagicMock()
        settings = MagicMock(POLITICIAN_SENATE_ENABLED=False)
        with patch("trading_signals.config.get_settings", return_value=settings), \
                patch.object(c, "fetch_house", return_value=[{"x": 1}]) as house:
            assert c.fetch(MagicMock()) == [{"x": 1}]
        house.assert_called_once()
        assert not c.client.method_calls

    def test_senate_runs_when_enabled(self):
        c = PoliticianTradesCollector(senate_enabled=True)
        senate = [{"chamber": "Senate"}]
        with patch.object(c, "_fetch_senate", return_value=senate) as s, \
                patch.object(c, "fetch_house", return_value=[{"chamber": "House"}]):
            result = c.fetch(MagicMock())
        s.assert_called_once()
        assert [t["chamber"] for t in result] == ["Senate", "House"]

    def test_setting_default_is_false(self):
        from trading_signals.config import Settings

        assert Settings.model_fields["POLITICIAN_SENATE_ENABLED"].default is False

    def test_no_senate_client_is_built_while_disabled(self):
        with patch.object(ptc, "DisclosureClient") as cls:
            PoliticianTradesCollector(senate_enabled=False)
        cls.assert_not_called()

    def test_is_a_base_collector_with_default_status_rules(self):
        assert issubclass(PoliticianTradesCollector, BaseCollector)


def _row(chamber, ticker):
    return {
        "politician_name": "X", "chamber": chamber, "ticker": ticker,
        "transaction_date": date(2026, 1, 2), "transaction_type": "Purchase",
        "amount_range": "$1,001 - $15,000", "owner": "Self",
        "source_url": f"https://example.com/{chamber}",
    }


class TestStoreOnboarding:
    def test_only_senate_tickers_are_onboarded(self):
        c = PoliticianTradesCollector(senate_enabled=False)
        session = MagicMock()
        session.execute.return_value.rowcount = 2
        data = [_row("House", "NVDA"), _row("Senate", "AAPL")]
        with patch(ONBOARDER) as onboarder_cls:
            c.store(session, data)
        onboarder_cls.return_value.onboard.assert_called_once()
        kwargs = onboarder_cls.return_value.onboard.call_args.kwargs
        assert kwargs["tickers"] == {"AAPL"}

    def test_house_only_never_creates_an_onboarder(self):
        c = PoliticianTradesCollector(senate_enabled=False)
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        with patch(ONBOARDER) as onboarder_cls:
            fetched, written = c.store(session, [_row("House", "NVDA")])
        onboarder_cls.assert_not_called()
        assert (fetched, written) == (1, 1)


@pytest.mark.parametrize("raw", ["P", "S", "S (partial)", "E"])
def test_known_house_types_are_mapped(raw):
    assert raw in ptc._HOUSE_TYPES
