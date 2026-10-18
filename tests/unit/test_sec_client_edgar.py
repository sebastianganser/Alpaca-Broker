"""Tests for the EDGAR additions of SECClient (no network; HTTP is mocked)."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from trading_signals.collectors.sec_client import (
    SECClient,
    SgmlDocument,
    iter_sgml_documents,
    parse_header_parties,
    parse_subject_cik,
    parse_subject_ciks,
    submission_acceptance_utc,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sec_8k_submission_sample.txt"
NY = ZoneInfo("America/New_York")


class TestSubmissionAcceptance:
    """The submissions JSON is Eastern wall clock + 2 x the UTC offset ('Z')."""

    @pytest.mark.parametrize(
        ("raw", "eastern"),
        [
            # (JSON value, "Accepted" time EDGAR shows on the filing index page)
            ("2026-07-31T00:30:28.000Z", datetime(2026, 7, 30, 16, 30, 28)),
            ("2026-07-31T14:01:02.000Z", datetime(2026, 7, 31, 6, 1, 2)),
            ("2026-10-17T00:29:07.000Z", datetime(2026, 10, 16, 16, 29, 7)),
            ("2026-04-21T01:29:51.000Z", datetime(2026, 4, 20, 17, 29, 51)),
            ("2025-10-01T02:30:22.000Z", datetime(2025, 9, 30, 18, 30, 22)),
            ("2022-09-28T05:48:06.000Z", datetime(2022, 9, 27, 21, 48, 6)),
            # winter time: +10 h
            ("2026-01-09T02:31:36.000Z", datetime(2026, 1, 8, 16, 31, 36)),
            ("2017-12-28T02:31:05.000Z", datetime(2017, 12, 27, 16, 31, 5)),
            ("2016-02-04T04:35:20.000Z", datetime(2016, 2, 3, 18, 35, 20)),
        ],
    )
    def test_matches_the_time_shown_on_the_edgar_index_page(self, raw, eastern):
        result = submission_acceptance_utc(raw)
        assert result.tzinfo is not None and result.utcoffset().total_seconds() == 0
        assert result.astimezone(NY).replace(tzinfo=None) == eastern

    def test_the_result_is_the_real_instant_in_utc(self):
        # 16:30:28 EDT = 20:30:28 UTC (the JSON says 00:30:28)
        assert submission_acceptance_utc("2026-07-31T00:30:28.000Z") == datetime(
            2026, 7, 30, 20, 30, 28, tzinfo=UTC
        )
        # 16:31:36 EST = 21:31:36 UTC
        assert submission_acceptance_utc("2026-01-09T02:31:36.000Z") == datetime(
            2026, 1, 8, 21, 31, 36, tzinfo=UTC
        )

    def test_a_filing_is_never_dated_after_its_acceptance_day(self):
        # 8-K accepted 20:10 ET gets the filing date of that day (rule: until 22:00 ET)
        accepted = submission_acceptance_utc("2026-08-11T04:10:48.000Z")
        assert accepted.astimezone(NY).date().isoformat() == "2026-08-10"

    @pytest.mark.parametrize(
        "raw", [None, "", "   ", "not a date", "2026-13-45T00:00:00Z"]
    )
    def test_missing_or_garbage_input_gives_none(self, raw):
        assert submission_acceptance_utc(raw) is None

    def test_values_without_the_z_suffix_are_read_the_same_way(self):
        assert submission_acceptance_utc("2026-07-31T00:30:28") == datetime(
            2026, 7, 30, 20, 30, 28, tzinfo=UTC
        )


def _chunks(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i : i + size]


class TestSgmlDocuments:
    DATA = FIXTURE.read_bytes()

    def test_documents_are_yielded_in_file_order_without_the_header(self):
        docs = list(iter_sgml_documents([self.DATA]))
        assert [d.type for d in docs] == [
            "8-K",
            "EX-99.2",
            "EX-99.1",
            "GRAPHIC",
            "EX-101.SCH",
        ]
        assert [d.sequence for d in docs] == [1, 3, 2, 4, 5]
        assert docs[0].filename == "exmpl-20260730.htm"
        assert "SEC-HEADER" not in docs[0].text()
        assert docs[2].text().lstrip().startswith("<html>")
        assert "&mdash;" in docs[2].text()

    @pytest.mark.parametrize("size", [1, 7, 64, 1000, 10**6])
    def test_chunk_boundaries_do_not_matter(self, size):
        docs = list(iter_sgml_documents(_chunks(self.DATA, size)))
        assert [d.type for d in docs] == [
            "8-K",
            "EX-99.2",
            "EX-99.1",
            "GRAPHIC",
            "EX-101.SCH",
        ]
        assert docs[1].text().strip().startswith("<html><body><p>Supplemental")

    def test_the_consumer_can_stop_early(self):
        consumed = []

        def chunks():
            for chunk in _chunks(self.DATA, 100):
                consumed.append(len(chunk))
                yield chunk

        gen = iter_sgml_documents(chunks())
        first = next(gen)
        gen.close()
        assert first.type == "8-K"
        assert sum(consumed) < len(self.DATA)  # the rest was never read

    def test_reading_stops_at_the_byte_limit_and_drops_the_cut_document(self):
        first_end = self.DATA.index(b"</DOCUMENT>") + len(b"</DOCUMENT>")
        docs = list(
            iter_sgml_documents(_chunks(self.DATA, 100), max_bytes=first_end + 50)
        )
        assert [d.type for d in docs] == ["8-K"]  # EX-99.2 was cut off by the limit
        assert list(iter_sgml_documents(_chunks(self.DATA, 100), max_bytes=500)) == []

    def test_empty_and_header_only_input_yield_nothing(self):
        assert list(iter_sgml_documents([])) == []
        assert list(iter_sgml_documents([b"", b"<SEC-HEADER>x</SEC-HEADER>"])) == []

    def test_text_decodes_utf8_and_falls_back_to_windows_1252(self):
        assert SgmlDocument("EX-99.1", 1, "a.htm", "café".encode()).text() == "café"
        assert SgmlDocument("EX-99.1", 1, "a.htm", b"caf\xe9").text() == "café"


class TestClientEndpoints:
    def _client(self):
        return SECClient(user_agent="TestAgent/1.0 (test@example.com)")

    def test_company_facts_url_and_payload(self):
        client = self._client()
        with patch.object(client, "_get_json", return_value={"facts": {}}) as get:
            assert client.get_company_facts("320193") == {"facts": {}}
        get.assert_called_once_with(
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
        )

    def test_company_facts_without_xbrl_is_none_other_errors_are_raised(self):
        client = self._client()
        not_found = requests.exceptions.HTTPError(response=MagicMock(status_code=404))
        with patch.object(client, "_get_json", side_effect=not_found):
            assert client.get_company_facts("0000000001") is None
        server_error = requests.exceptions.HTTPError(
            response=MagicMock(status_code=503)
        )
        with patch.object(client, "_get_json", side_effect=server_error):
            with pytest.raises(requests.exceptions.HTTPError):
                client.get_company_facts("0000000001")

    def test_fetch_submissions_does_not_fill_the_cache(self):
        client = self._client()
        with patch.object(client, "_get_json", return_value={"filings": {}}) as get:
            client.fetch_submissions("320193")
            client.fetch_submissions("320193")
        assert get.call_count == 2  # nothing cached
        assert client._submissions_cache == {}
        get.assert_called_with("https://data.sec.gov/submissions/CIK0000320193.json")

    def test_older_submission_pages_use_the_submissions_base(self):
        client = self._client()
        with patch.object(client, "_get_json", return_value={}) as get:
            client.get_submissions_page("CIK0000320193-submissions-001.json")
        get.assert_called_once_with(
            "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"
        )

    def test_filing_documents_stream_the_full_submission_and_close(self):
        client = self._client()
        response = MagicMock()
        response.iter_content.return_value = _chunks(FIXTURE.read_bytes(), 500)
        with patch.object(client, "_open_stream", return_value=response) as opened:
            gen = client.iter_filing_documents("320193", "0000320193-26-000018")
            first = next(gen)
            gen.close()
        opened.assert_called_once_with(
            "https://www.sec.gov/Archives/edgar/data/0000320193/"
            "000032019326000018/0000320193-26-000018.txt"
        )
        assert first.type == "8-K"
        response.close.assert_called_once()

    def test_open_stream_streams_with_a_text_accept_header(self):
        client = self._client()
        response = MagicMock()
        with (
            patch.object(client, "_rate_limit") as limit,
            patch.object(client._session, "get", return_value=response) as get,
        ):
            assert client._open_stream("https://example.test/x.txt") is response
        limit.assert_called_once()
        assert get.call_args.kwargs["stream"] is True
        assert "text/plain" in get.call_args.kwargs["headers"]["Accept"]

    def test_open_stream_closes_the_response_on_http_errors_and_does_not_retry_404(
        self,
    ):
        client = self._client()
        response = MagicMock(status_code=404)
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=response
        )
        with (
            patch.object(client, "_rate_limit"),
            patch.object(client._session, "get", return_value=response) as get,
        ):
            with pytest.raises(requests.exceptions.HTTPError):
                client._open_stream("https://example.test/missing.txt")
        assert get.call_count == 1
        response.close.assert_called_once()


FIXTURES = Path(__file__).parent.parent / "fixtures"


def _hdr(name: str) -> str:
    return (FIXTURES / name).read_text()


#: The header at the top of ``<accession>.txt`` (Apple as subject of a 13G/A).
TEXT_HEADER = """<SEC-HEADER>0001193125-24-036431.hdr.sgml : 20240214
<ACCEPTANCE-DATETIME>20240214164705
ACCESSION NUMBER:\t\t0001193125-24-036431
CONFORMED SUBMISSION TYPE:\tSC 13G/A
PUBLIC DOCUMENT COUNT:\t\t3
FILED AS OF DATE:\t\t20240214
GROUP MEMBERS:\t\tGEICO CORP

SUBJECT COMPANY:\t

\tCOMPANY DATA:\t
\t\tCOMPANY CONFORMED NAME:\t\t\tApple Inc.
\t\tCENTRAL INDEX KEY:\t\t\t0000320193
\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\tELECTRONIC COMPUTERS [3571]

\tFILING VALUES:
\t\tFORM TYPE:\t\tSC 13G/A
\t\tSEC FILE NUMBER:\t005-34104

\tBUSINESS ADDRESS:\t
\t\tSTREET 1:\t\tONE APPLE PARK WAY

FILED BY:\t\t

\tCOMPANY DATA:\t
\t\tCOMPANY CONFORMED NAME:\t\t\tBERKSHIRE HATHAWAY INC
\t\tCENTRAL INDEX KEY:\t\t\t0001067983

\tFILING VALUES:
\t\tFORM TYPE:\t\tSC 13G/A
</SEC-HEADER>"""


def _subject_block(cik: str) -> str:
    return (
        f"<SUBJECT-COMPANY>\n<COMPANY-DATA>\n<CIK>{cik}\n</COMPANY-DATA>\n"
        "</SUBJECT-COMPANY>\n"
    )


class TestHeaderParties:
    def test_a_13g_filed_by_a_bank_has_the_issuer_as_subject(self):
        header = _hdr("sec_hdr_13g_filed_by_jpm.sgml")
        assert parse_subject_cik(header) == "0001582961"  # DigitalOcean, not JPM
        parties = parse_header_parties(header)
        assert parties.subjects == ("0001582961",)
        assert parties.others == frozenset({"0000019617"})  # JPMorgan = filer
        assert parties.is_subject("0001582961") and not parties.is_subject("19617")

    def test_a_13d_filed_by_a_company_names_the_target_as_subject(self):
        header = _hdr("sec_hdr_13d_filed_by_walmart.sgml")
        assert parse_subject_ciks(header) == ["0001837240"]  # Symbotic
        assert not parse_header_parties(header).is_subject("0000104169")  # Walmart

    def test_a_form_144_has_the_issuer_as_subject_and_the_seller_as_owner(self):
        header = _hdr("sec_hdr_144_apple.sgml")
        parties = parse_header_parties(header)
        assert parties.subjects == ("0000320193",)
        assert parties.others == frozenset({"0002050912"})
        # an issuer that submits the 144 of its own insider is still the subject
        wmt = parse_header_parties(_hdr("sec_hdr_144_walmart_self_submitted.sgml"))
        assert wmt.is_subject("0000104169") and wmt.others == frozenset({"0001335782"})

    def test_the_text_rendering_of_the_header_is_understood_too(self):
        parties = parse_header_parties(TEXT_HEADER)
        assert parties.subjects == ("0000320193",)
        assert parties.others == frozenset({"0001067983"})
        assert parse_subject_cik(TEXT_HEADER) == "0000320193"

    def test_ciks_are_zero_padded_to_ten_digits(self):
        assert parse_subject_cik(_subject_block("320193")) == "0000320193"

    def test_several_subject_blocks_are_all_returned_the_first_is_the_subject(self):
        header = _subject_block("0000000001") + _subject_block("0000000002")
        assert parse_subject_ciks(header) == ["0000000001", "0000000002"]
        assert parse_subject_cik(header) == "0000000001"

    def test_without_a_subject_block_the_subject_is_found_by_elimination(self):
        header = (
            "<FILED-BY>\n<COMPANY-DATA>\n<CIK>0000000009\n</COMPANY-DATA>\n</FILED-BY>\n"
        )
        assert parse_subject_cik(header) is None
        parties = parse_header_parties(header)
        assert not parties.is_subject("0000000009")  # a named filer
        assert parties.is_subject("0000000005")  # not named: only the issuer role left

    @pytest.mark.parametrize(
        "text",
        ["", "<html>Rate Threshold Exceeded</html>", "<SEC-HEADER>x\n</SEC-HEADER>"],
    )
    def test_garbage_gives_no_party(self, text):
        parties = parse_header_parties(text)
        assert parties.empty and parse_subject_cik(text) is None
        assert not parties.is_subject("0000000001")  # nobody named -> no verdict

    def test_a_sec_header_wrapper_is_not_taken_for_a_section(self):
        header = "<SEC-HEADER>\n<CIK>0000000001\n</SEC-HEADER>\n"
        assert parse_subject_cik(header) is None


class TestFetchFilingHeader:
    def _client(self):
        return SECClient(user_agent="TestAgent/1.0 (test@example.com)")

    def test_the_tagged_header_file_is_read_with_one_request(self):
        client = self._client()
        with patch.object(client, "_get_text", return_value="HDR") as get:
            header = client.fetch_filing_header("0000019617", "0000019617-26-000392")
        assert header == "HDR"
        get.assert_called_once_with(
            "https://www.sec.gov/Archives/edgar/data/19617/000001961726000392/"
            "0000019617-26-000392.hdr.sgml"
        )

    def test_a_404_falls_back_to_the_head_of_the_full_submission(self):
        client = self._client()
        not_found = requests.exceptions.HTTPError(response=MagicMock(status_code=404))
        response = MagicMock()
        body = TEXT_HEADER.encode() + b"\n<DOCUMENT>\n<TYPE>SC 13G/A\n" + b"x" * 50_000
        response.iter_content.return_value = _chunks(body, 8192)
        with (
            patch.object(client, "_get_text", side_effect=not_found),
            patch.object(client, "_open_stream", return_value=response) as opened,
        ):
            header = client.fetch_filing_header("320193", "0001193125-24-036431")
        assert header.endswith("</SEC-HEADER>") and "<DOCUMENT>" not in header
        assert parse_subject_cik(header) == "0000320193"
        opened.assert_called_once_with(
            "https://www.sec.gov/Archives/edgar/data/320193/000119312524036431/"
            "0001193125-24-036431.txt"
        )
        response.close.assert_called_once()

    def test_other_http_errors_are_raised_not_hidden_by_the_fallback(self):
        client = self._client()
        error = requests.exceptions.HTTPError(response=MagicMock(status_code=503))
        with (
            patch.object(client, "_get_text", side_effect=error),
            patch.object(client, "_open_stream") as opened,
        ):
            with pytest.raises(requests.exceptions.HTTPError):
                client.fetch_filing_header("1", "0000000001-26-000001")
        opened.assert_not_called()

    def test_a_text_without_header_end_is_an_error_and_the_stream_is_closed(self):
        client = self._client()
        not_found = requests.exceptions.HTTPError(response=MagicMock(status_code=404))
        response = MagicMock()
        response.iter_content.return_value = _chunks(b"y" * 300_000, 8192)
        with (
            patch.object(client, "_get_text", side_effect=not_found),
            patch.object(client, "_open_stream", return_value=response),
        ):
            with pytest.raises(ValueError, match="no SEC header"):
                client.fetch_filing_header("1", "0000000001-26-000001")
        response.close.assert_called_once()
