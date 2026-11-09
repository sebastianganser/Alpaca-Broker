"""Tests for the House Clerk disclosure module (index, PDF text, PTR parser).

All inputs are real fixtures from ``tests/fixtures/house_ptr`` (layout text
of House PTR PDFs, extracted with pdfplumber). No network, no database.
"""

import io
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from trading_signals.collectors import house_disclosures as hd

FIX = Path(__file__).parent.parent / "fixtures" / "house_ptr"


def _text(doc_id: str) -> str:
    return (FIX / f"{doc_id}.txt").read_text(encoding="utf-8")


def _parse(doc_id: str) -> hd.ParsedPtr:
    return hd.parse_ptr_text(_text(doc_id))


def _stock(parsed: hd.ParsedPtr):
    return [r for r in parsed.rows if r.asset_code == "ST" and r.ticker]


# ============================================================================
# Index
# ============================================================================

INDEX_TXT = (
    "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
    "\tGreene\tMarjorie Taylor Mrs\t\tP\tGA14\t2024\t12/27/2024\t20026473\r\n"
    "Hon.\tPelosi\tNancy\t\tP\tCA11\t2026\t10/2/2026\t20035553\r\n"
    "\tBurgess\tMichael C.\t\tA\tTX26\t2026\t5/15/2026\t10063333\r\n"
    "\tKelly\tMike\t\tP\tPA16\t2026\t11/4/2026\t20035765\r\n"
    "\tSmith\tPaper\t\tP\tNJ04\t2026\t1/3/2026\t9116361\r\n"
    "\tBroken\tRow\r\n"
    "\tBadDate\tDoc\t\tP\tCA01\t2026\tnot-a-date\t20000001\r\n"
)


def _zip(year: int, txt: str = INDEX_TXT) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{year}FD.txt", b"\xef\xbb\xbf" + txt.encode("utf-8"))
        z.writestr(f"{year}FD.xml", "<xml/>")
    return buf.getvalue()


class TestIndex:
    def test_parses_filings_and_skips_broken_rows(self):
        filings = hd.parse_house_index(_zip(2026), 2026)
        assert [f.doc_id for f in filings] == [
            "20026473", "20035553", "10063333", "20035765", "9116361",
        ]

    def test_fields(self):
        f = hd.parse_house_index(_zip(2026), 2026)[0]
        assert f.filing_type == "P"
        assert f.state == "GA"
        assert f.state_dst == "GA14"
        assert f.filing_date == date(2024, 12, 27)
        assert f.year == 2024
        assert f.name == "Marjorie Taylor Greene"

    def test_is_ptr(self):
        by_id = {f.doc_id: f for f in hd.parse_house_index(_zip(2026), 2026)}
        assert by_id["20035553"].is_ptr
        assert not by_id["10063333"].is_ptr

    def test_electronic_vs_paper(self):
        by_id = {f.doc_id: f for f in hd.parse_house_index(_zip(2026), 2026)}
        assert by_id["20035553"].is_electronic
        assert not by_id["9116361"].is_electronic

    def test_missing_txt_member_raises(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("other.txt", "x")
        with pytest.raises(ValueError):
            hd.parse_house_index(buf.getvalue(), 2026)

    def test_url(self):
        f = hd.parse_house_index(_zip(2026), 2026)[1]
        assert hd.ptr_url(f.year, f.doc_id) == (
            "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035553.pdf"
        )


class TestPoliticianName:
    @pytest.mark.parametrize(
        ("first", "last", "expected"),
        [
            ("Nancy", "Pelosi", "Nancy Pelosi"),
            ("Marjorie Taylor Mrs", "Greene", "Marjorie Taylor Greene"),
            ("Mark Dr", "Green", "Mark Green"),
            ("Dr. Mark", "Green", "Mark Green"),
            ("Mr", "Smith", "Mr Smith"),  # never leave the first name empty
            ("Mike  ", "  Kelly", "Mike Kelly"),
            ("Hon. Jane", "Doe Ms.", "Jane Doe"),
        ],
    )
    def test_names(self, first, last, expected):
        assert hd.house_politician_name(first, last) == expected


# ============================================================================
# Ticker helper
# ============================================================================


class TestTickerFromAsset:
    @pytest.mark.parametrize(
        ("asset", "expected"),
        [
            ("Adobe Inc. - Common Stock (ADBE) [ST]", "ADBE"),
            ("Apple Inc. (AAPl) [ST]", "AAPL"),
            ("Berkshire Hathaway Class B (BRK.B) [ST]", "BRK.B"),
            ("Some Company [ST]", None),
            ("Company (formerly Foo Bar) [ST]", None),
            ("Mobility Global Inc. Common Stock (MBGL) [ST]", "MBGL"),
        ],
    )
    def test_ticker(self, asset, expected):
        assert hd.ticker_from_asset(asset) == expected


# ============================================================================
# PTR parser on real fixtures
# ============================================================================


class TestParseElectronic:
    def test_pelosi_2021_old_format(self):
        p = _parse("20020208")
        assert len(p.rows) == 2
        assert p.codes_found == 2 and not p.mismatch
        for r in p.rows:
            assert (r.owner_code, r.ticker, r.raw_type) == ("SP", "AAPL", "S (partial)")
            assert r.transaction_date == date(2021, 12, 30)
            assert r.notification_date == date(2021, 12, 30)
            assert r.amount == "$500,001 - $1,000,000"
        assert "eorgetown" in p.rows[0].description
        assert "Trinity College" in p.rows[1].description

    def test_burgess_four_self_rows(self):
        p = _parse("20022087")
        assert [(r.ticker, r.raw_type, r.owner_code) for r in p.rows] == [
            ("MS", "S", None),
            ("SYK", "S (partial)", None),
            ("DIS", "P", None),
            ("DIS", "S (partial)", None),
        ]
        assert p.rows[0].transaction_date == date(2022, 11, 11)
        assert p.rows[0].amount == "$15,001 - $50,000"
        assert p.rows[1].amount == "$1,001 - $15,000"
        assert p.rows[1].description.startswith("The terms of the call")
        assert not p.mismatch

    def test_lowenthal_joint_rows(self):
        p = _parse("20022162")
        assert [(r.ticker, r.raw_type, r.transaction_date) for r in p.rows] == [
            ("ATVI", "P", date(2022, 12, 12)),
            ("GTLS", "P", date(2022, 12, 9)),
            ("CRM", "S", date(2022, 12, 8)),
        ]
        assert {r.owner_code for r in p.rows} == {"JT"}
        assert {r.amount for r in p.rows} == {"$1,001 - $15,000"}
        assert p.rows[0].subholding == (
            "Neuberger Berman - Malumed Lowenthal Rev Trust #2"
        )
        assert not p.mismatch

    def test_greene_page_break_and_treasury(self):
        p = _parse("20026473")
        stocks = _stock(p)
        assert len(stocks) == 14
        assert [r.ticker for r in stocks] == [
            "ADBE", "AMZN", "AMAT", "CAT", "DUK", "LLY", "GILD", "JNJ",
            "META", "MSFT", "NSC", "NVDA", "NOW", "HSY",
        ]
        assert {r.transaction_date for r in stocks} == {date(2024, 12, 24)}
        assert {r.raw_type for r in stocks} == {"P"}
        gs = [r for r in p.rows if r.asset_code == "GS"]
        assert len(gs) == 1 and gs[0].ticker is None
        assert gs[0].amount == "$50,001 - $100,000"
        assert len(p.rows) == 15
        assert not p.mismatch

    def test_moskowitz_five_pages(self):
        p = _parse("20033421")
        assert len(_stock(p)) == 30
        assert len(p.rows) == 30
        assert {r.owner_code for r in p.rows} == {"DC", None}
        assert p.rows[0].subholding == "Morgan Stanley Active Assets (6)"
        assert p.rows[2].amount == "$15,001 - $50,000"
        assert not p.mismatch

    def test_morrison_ticker_only_from_asset_text(self):
        p = _parse("20035244")
        stocks = _stock(p)
        assert len(stocks) == 7
        assert [r.ticker for r in stocks] == [
            "ACN", "ACN", "ACN", "MBGL", "MBGL", "MBGL", "SOLS",
        ]
        assert "SPGI" not in {r.ticker for r in p.rows}
        mbgl = stocks[3]
        assert "SPGI" in mbgl.description
        assert mbgl.owner_code == "SP"
        assert stocks[5].owner_code is None  # row across the page break
        assert stocks[5].description.startswith("Asset acquired through")
        assert stocks[2].amount == "$15,001 - $50,000"
        assert not p.mismatch

    def test_cohen_treasury_bills_only(self):
        p = _parse("20035350")
        assert len(p.rows) == 2
        assert {r.asset_code for r in p.rows} == {"GS"}
        assert _stock(p) == []
        assert p.rows[0].amount == "$250,001 - $500,000"
        assert "2/18/2027" in p.rows[0].asset_text
        assert not p.mismatch

    def test_delbene_bonds(self):
        p = _parse("20035689")
        assert len(p.rows) == 2
        assert {(r.asset_code, r.owner_code) for r in p.rows} == {("GS", "JT")}
        assert _stock(p) == []
        assert "Ser. 2025 F" in p.rows[0].asset_text
        assert not p.mismatch

    def test_pelosi_2026_llc(self):
        p = _parse("20035553")
        assert len(p.rows) == 1
        r = p.rows[0]
        assert (r.asset_code, r.owner_code, r.ticker) == ("AB", "SP", None)
        assert r.amount == "$500,001 - $1,000,000"
        assert "225 Bush Street in San Francisco, CA." in r.description
        assert not p.mismatch

    def test_spouse_dc_over_amount_split_over_two_lines(self):
        # "Spouse/DC Over" on the transaction line, "$1,000,000" below it
        p = _parse("20019787")
        assert len(p.rows) == 6 and p.codes_found == 6
        assert not p.mismatch
        assert [r.amount for r in p.rows] == [
            "Spouse/DC Over $1,000,000",
            "$250,001 - $500,000",
            "$50,001 - $100,000",
            "Spouse/DC Over $1,000,000",
            "$250,001 - $500,000",
            "$500,001 - $1,000,000",
        ]
        assert p.rows[0].asset_text == "Bay Area Toll Rev [gS]"
        assert p.rows[0].transaction_date == date(2021, 10, 14)
        assert {r.asset_code for r in p.rows} == {"GS"}

    def test_spouse_dc_over_amount_on_the_last_row(self):
        p = _parse("20019621")
        assert len(p.rows) == 3 and not p.mismatch
        assert p.rows[2].amount == "Spouse/DC Over $1,000,000"
        assert p.rows[2].raw_type == "S"

    def test_over_amount_split_keeps_the_stock_rows_of_the_filing(self):
        # "Over" on the line, "$50,000,000" below: before the fix the treasury
        # row was lost and the whole filing (3 stock rows) was dropped
        p = _parse("20022032")
        assert not p.mismatch
        assert [r.ticker for r in _stock(p)] == ["BX", "TJX", "TMUS"]
        assert p.rows[3].asset_code == "CS"
        assert p.rows[3].amount == "Over $50,000,000"
        assert p.rows[3].description.startswith("Maturity date is 05/04/2023")

    def test_repeated_code_in_the_asset_name_counts_once(self):
        # "... 10/01/2038 [GS] [GS]": the filer typed the code into the name
        p = _parse("20024205")
        assert len(p.rows) == 1 and not p.mismatch
        assert p.rows[0].asset_code == "GS"
        assert p.rows[0].amount == "$15,001 - $50,000"

    def test_codes_quoted_in_a_wrapped_description_are_not_rows(self):
        # "D: Tobacco Settlement ... TAXABLE" / "SENIOR B/E CPN [CS] [CS]"
        p = _parse("20023094")
        assert not p.mismatch
        assert len(p.rows) == p.codes_found
        assert p.rows[0].asset_code == "CS"
        assert p.rows[0].description.endswith("SENIOR B/E CPN [CS] [CS]")
        assert {r.asset_code for r in p.rows[1:]} == {"GS"}

    def test_row_missed_after_a_label_is_still_a_mismatch(self):
        # an unknown amount format makes the row pattern miss the DIS buy; the
        # line follows a label of the previous row and must not vanish
        text = _text("20022087").replace(
            "P 11/16/2022 11/18/2022 $15,001 -", "P 11/16/2022 11/18/2022 ~15k -"
        )
        p = hd.parse_ptr_text(text)
        assert len(p.rows) == 3
        assert p.mismatch

    def test_row_with_wrapped_dates_after_a_description_is_a_mismatch(self):
        # Codex review: the DIS buy follows the SYK description and its two
        # dates are split over two lines - neither the row pattern nor the
        # date pair matches, its [ST] code must still count
        text = _text("20022087").replace(
            "P 11/16/2022 11/18/2022 $15,001 -",
            "P 11/16/2022\n              11/18/2022 $15,001 -",
        )
        p = hd.parse_ptr_text(text)
        assert len(p.rows) == 3
        assert p.mismatch

    def test_other_samples_have_no_mismatch(self):
        for doc_id, n in [
            ("20033616", None), ("20035710", 3), ("20035727", 8),
            ("20035765", 2), ("20035770", 3),
        ]:
            p = _parse(doc_id)
            assert not p.mismatch, doc_id
            assert p.rows, doc_id
            if n is not None:
                assert len(p.rows) == n, doc_id

    def test_type_followed_by_two_spaces(self):
        # "Arm Holdings ... S  12/11/2025": two spaces after the type
        p = _parse("20033616")
        arm = next(r for r in p.rows if r.ticker == "ARM")
        assert arm.raw_type == "S"
        assert arm.amount == "$50,001 - $100,000"

    def test_one_and_two_letter_tickers(self):
        p = _parse("20035727")
        assert [r.ticker for r in p.rows if r.ticker in ("T", "V")] == ["T", "V"]

    def test_gs_with_s_in_name(self):
        # "North Carolina Hsg Fin Agy Hom S P 10/01/2026": the type is P
        p = _parse("20035765")
        assert p.rows[1].raw_type == "P"
        assert p.rows[1].asset_code == "GS"


class TestParserEdgeCases:
    def test_empty_text(self):
        p = hd.parse_ptr_text("")
        assert p.rows == [] and p.codes_found == 0 and not p.mismatch

    def test_mismatch_when_a_code_has_no_row(self):
        text = _text("20022162").replace(
            "* For the complete list",
            "              Orphan Corp (ORPH) [ST]\n* For the complete list",
        )
        p = hd.parse_ptr_text(text)
        assert len(p.rows) == 3
        assert p.codes_found == 4
        assert p.mismatch

    def test_unrecognised_first_row_still_counts_its_code(self):
        # The first transaction line no longer matches the row pattern: its
        # asset code must still be counted so the lost row shows as a mismatch.
        text = _text("20022162").replace("12/12/2022", "12/12/20XX", 1)
        p = hd.parse_ptr_text(text)
        assert len(p.rows) == 2
        assert p.codes_found == 3
        assert p.mismatch

    def test_incomplete_amount_is_flagged_and_dropped(self):
        text = _text("20022087").replace("$50,000", "       ")
        p = hd.parse_ptr_text(text)
        assert p.incomplete_amounts >= 1
        assert p.mismatch
        assert p.rows[0].amount is None

    def test_nul_characters_are_ignored(self):
        text = _text("20022162").replace("Salesforce", "Sales\x00force")
        p = hd.parse_ptr_text(text)
        assert p.rows[2].asset_text.startswith("Salesforce")

    def test_over_amount(self):
        text = _text("20022162").replace("$1,001 - $15,000", "Over $1,000,000", 1)
        assert hd.parse_ptr_text(text).rows[0].amount == "Over $1,000,000"

    def test_old_font_lowercase_asset_code(self):
        # The 2021/22 font extracts some capitals in lower case (real case:
        # DocID 20020134, "Due 6/1/2027 [gS]"; tickers like "(AAPl)").
        text = _text("20020208").replace("[ST]", "[gS]", 1)
        p = hd.parse_ptr_text(text)
        assert [r.asset_code for r in p.rows] == ["GS", "ST"]
        assert p.codes_found == 2 and not p.mismatch
        assert p.rows[0].ticker is None and p.rows[1].ticker == "AAPL"

    def test_old_font_lowercase_stock_code(self):
        text = _text("20020208").replace("[ST]", "[sT]")
        p = hd.parse_ptr_text(text)
        assert [(r.asset_code, r.ticker) for r in p.rows] == [("ST", "AAPL")] * 2
        assert not p.mismatch


# ============================================================================
# PDF text extraction (real PDFs)
# ============================================================================


class TestExtractPdfText:
    def test_electronic_pdf_is_parseable(self):
        text = hd.extract_pdf_text((FIX / "20022162.pdf").read_bytes())
        assert "\x00" not in text
        p = hd.parse_ptr_text(text)
        assert [r.ticker for r in p.rows] == ["ATVI", "GTLS", "CRM"]
        assert not p.mismatch

    def test_paper_pdf_has_no_text(self):
        assert hd.extract_pdf_text((FIX / "9116361.pdf").read_bytes()) == ""

    def test_garbage_raises(self):
        with pytest.raises(Exception):  # noqa: B017,PT011
            hd.extract_pdf_text(b"not a pdf")


# ============================================================================
# HTTP client (mocked session)
# ============================================================================


def _resp(status=200, content=b"x"):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.headers = {}
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(response=r)
    else:
        r.raise_for_status.return_value = None
    return r


class TestClient:
    def _client(self, responses):
        c = hd.HouseDisclosureClient()
        c.session = MagicMock()
        c.session.get.side_effect = responses
        return c

    def test_user_agent_is_honest(self):
        c = hd.HouseDisclosureClient()
        assert c.session.headers["User-Agent"].startswith("alpaca-broker-research/1.0")

    def test_index_url(self):
        c = self._client([_resp(content=b"zip")])
        with patch("time.sleep"):
            assert c.fetch_index(2026) == b"zip"
        assert c.session.get.call_args[0][0] == (
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.zip"
        )
        assert c.session.get.call_args[1]["timeout"]

    def test_fetch_pdf(self):
        c = self._client([_resp(content=b"%PDF")])
        with patch("time.sleep"):
            assert c.fetch_ptr_pdf(2026, "20035553") == b"%PDF"
        assert c.session.get.call_args[0][0].endswith("/ptr-pdfs/2026/20035553.pdf")

    def test_retries_5xx_then_succeeds(self):
        c = self._client([_resp(503), _resp(502), _resp(content=b"ok")])
        with patch("time.sleep"):
            assert c.fetch_ptr_pdf(2026, "20035553") == b"ok"
        assert c.session.get.call_count == 3

    def test_no_retry_on_404(self):
        c = self._client([_resp(404), _resp(content=b"ok")])
        with patch("time.sleep"), pytest.raises(requests.exceptions.HTTPError):
            c.fetch_ptr_pdf(2026, "20035553")
        assert c.session.get.call_count == 1

    def test_retries_timeouts_and_gives_up(self):
        c = self._client([requests.exceptions.Timeout()] * 3)
        with patch("time.sleep"), pytest.raises(requests.exceptions.Timeout):
            c.fetch_index(2026)
        assert c.session.get.call_count == 3

    def test_rate_limited(self):
        c = self._client([_resp(), _resp()])
        with patch("time.sleep") as sleep:
            c.fetch_index(2026)
            c.fetch_index(2025)
        assert sleep.called
        assert sleep.call_args[0][0] <= hd.RATE_LIMIT_DELAY
