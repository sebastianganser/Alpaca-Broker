"""Tests for the SEC filings collector (no DB session, no network)."""

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from trading_signals.collectors import sec_filings_collector as mod
from trading_signals.collectors.sec_client import SgmlDocument, iter_sgml_documents
from trading_signals.collectors.sec_filings_collector import (
    BODY_ITEMS,
    FORMS,
    PRESS_RELEASE_ITEMS,
    SecFilingsCollector,
    build_texts,
    extract_filing_rows,
    filing_url,
    html_to_text,
    make_excerpt,
    parse_items,
    select_press_release,
    strip_cover_page,
    text_plan,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
SUBMISSIONS = json.loads((FIXTURES / "sec_submissions_sample.json").read_text())
SGML = (FIXTURES / "sec_8k_submission_sample.txt").read_bytes()

AAPL = "0000320193"
ACC_8K_RESULTS = "0000320193-26-000018"  # 8-K 2.02,9.01 of 2026-07-30
ACC_10Q = "0000320193-26-000020"


class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 18)


def _documents() -> list[SgmlDocument]:
    return list(iter_sgml_documents([SGML]))


def _recent() -> dict:
    return SUBMISSIONS["filings"]["recent"]


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestFormWhitelist:
    def test_whitelist_covers_the_documented_forms_and_nothing_else(self):
        assert {"8-K", "8-K/A", "10-Q", "10-K", "144", "DEF 14A", "S-3ASR"} <= FORMS
        assert {"SC 13D", "SC 13G/A", "SCHEDULE 13D", "SCHEDULE 13G/A"} <= FORMS
        assert {"6-K", "NT 10-K", "NT 10-Q", "DEFM14A", "S-1", "S-3"} <= FORMS
        # insider forms and prospectus supplements are other feeds / noise
        assert not FORMS & {
            "3",
            "4",
            "5",
            "424B2",
            "424B5",
            "13F-HR",
            "UPLOAD",
            "CORRESP",
        }

    def test_text_items_are_disjoint_and_cover_results_and_events(self):
        assert PRESS_RELEASE_ITEMS == {"2.02", "7.01", "8.01"}
        assert not PRESS_RELEASE_ITEMS & BODY_ITEMS
        assert {"5.02", "4.02", "1.05", "2.01"} <= BODY_ITEMS


class TestParsing:
    def test_parse_items(self):
        assert parse_items("2.02,9.01") == ["2.02", "9.01"]
        assert parse_items(" 5.02 , ") == ["5.02"]
        assert parse_items("") is None
        assert parse_items(None) is None
        assert parse_items(["2.02"]) is None

    def test_filing_url(self):
        assert filing_url(AAPL, ACC_10Q, "aapl-20260627.htm") == (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/"
            "aapl-20260627.htm"
        )
        assert filing_url(AAPL, ACC_10Q, None).endswith("/000032019326000020/")

    def test_extract_rows_keeps_whitelisted_forms_since_the_date(self):
        rows = extract_filing_rows(_recent(), date(2026, 1, 1))
        assert [(r["form"], str(r["filing_date"])) for r in rows] == [
            ("144", "2026-10-16"),
            ("8-K/A", "2026-09-01"),
            ("10-Q", "2026-07-31"),
            ("8-K", "2026-07-30"),
            ("8-K", "2026-04-20"),
            ("SCHEDULE 13G/A", "2026-03-26"),
            ("8-K", "2026-02-24"),
            ("DEF 14A", "2026-01-08"),
        ]
        assert not {"4", "UPLOAD", "CORRESP", "424B2"} & {r["form"] for r in rows}

    def test_extract_rows_respects_the_since_date(self):
        rows = extract_filing_rows(_recent(), date(2024, 1, 1))
        forms = [r["form"] for r in rows]
        assert "S-3ASR" in forms and "SC 13G/A" in forms and "10-K" in forms
        assert all(r["filing_date"] >= date(2024, 1, 1) for r in rows)
        assert extract_filing_rows(_recent(), date(2026, 10, 17)) == []

    def test_an_8k_row(self):
        rows = {
            r["accession_number"]: r
            for r in extract_filing_rows(_recent(), date(2026, 1, 1))
        }
        r = rows[ACC_8K_RESULTS]
        assert r["form"] == "8-K" and r["is_amendment"] is False
        assert r["items"] == ["2.02", "9.01"]
        assert r["filing_date"] == date(2026, 7, 30)
        assert r["report_date"] == date(2026, 7, 30)
        # EDGAR index page: accepted 2026-07-30 16:30:28 Eastern
        assert r["acceptance_datetime"] == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
        assert r["acceptance_raw"] == "2026-07-31T00:30:28.000Z"
        assert r["primary_document"] == "aapl-20260730.htm"

    def test_a_form_144_and_an_amendment(self):
        rows = extract_filing_rows(_recent(), date(2026, 1, 1))
        form144 = next(r for r in rows if r["form"] == "144")
        assert form144["items"] is None and form144["report_date"] is None
        assert form144["primary_document"] == "xsl144X01/primary_doc.xml"
        amendment = next(r for r in rows if r["form"] == "8-K/A")
        assert amendment["is_amendment"] is True and amendment["items"] == ["5.02"]

    def test_malformed_columns_are_tolerated(self):
        recent = copy.deepcopy(_recent())
        recent["accessionNumber"][0] = "garbage"  # first row (144) is skipped
        recent["filingDate"][1] = None  # the Form 4 would not match anyway
        recent["acceptanceDateTime"] = recent["acceptanceDateTime"][:3]  # short column
        del recent["items"]
        rows = extract_filing_rows(recent, date(2026, 1, 1))
        assert "garbage" not in {r["accession_number"] for r in rows}
        assert all(r["items"] is None for r in rows)
        assert extract_filing_rows({}, date(2026, 1, 1)) == []
        assert extract_filing_rows(None, date(2026, 1, 1)) == []  # type: ignore[arg-type]


class TestTextPlan:
    @pytest.mark.parametrize(
        ("form", "items", "expected"),
        [
            ("8-K", ["2.02", "9.01"], (True, False)),
            ("8-K", ["7.01"], (True, False)),
            ("8-K", ["8.01", "9.01"], (True, False)),
            ("8-K", ["5.02"], (False, True)),
            ("8-K", ["2.02", "5.02"], (True, True)),
            ("8-K", ["5.07", "9.01"], (False, False)),  # votes: nothing to read
            ("8-K", None, (False, False)),
            ("8-K/A", ["2.02"], (False, False)),
            ("10-Q", ["2.02"], (False, False)),
            ("144", None, (False, False)),
        ],
    )
    def test_plan(self, form, items, expected):
        assert text_plan(form, items) == expected


class TestTextCleaning:
    def test_html_to_text_drops_scripts_styles_head_and_the_ixbrl_header(self):
        text = html_to_text(_documents()[0].text())
        assert "HIDDEN-CONTEXT-NOISE" not in text and "SCRIPT-NOISE" not in text
        assert "font-size" not in text and "exmpl-20260730" not in text  # head/title
        assert "Item 2.02. Results of Operations" in text
        assert "  " not in text and "\n" not in text

    def test_html_to_text_handles_plain_text_and_entities(self):
        assert html_to_text("  plain\n text\tfile ") == "plain text file"
        assert (
            html_to_text("<p>a&nbsp;&amp;&nbsp;b</p><div style='display: none'>x</div>")
            == "a & b"
        )

    def test_cover_page_is_cut_at_the_first_item_after_the_emerging_growth_notice(self):
        body = strip_cover_page(html_to_text(_documents()[0].text()))
        assert body.startswith(
            "Item 2.02. Results of Operations and Financial Condition."
        )
        assert "Item 5.02" in body and "step down as Chief Financial Officer" in body
        assert "Washington" not in body and "Rule 425" not in body

    def test_cover_page_fallbacks(self):
        no_marker = "Header text. Item 1.01 Entry into an Agreement. Body."
        assert strip_cover_page(no_marker) == "Item 1.01 Entry into an Agreement. Body."
        assert strip_cover_page("no items at all") == "no items at all"
        assert (
            strip_cover_page("emerging growth company ... trailing text")
            == "... trailing text"
        )

    def test_excerpt_is_cut_at_a_word_boundary(self):
        assert make_excerpt("short text") == "short text"
        long = "word " * 1000
        excerpt = make_excerpt(long)
        assert excerpt.endswith("…") and len(excerpt) <= 3001
        assert excerpt[:-1].endswith("word")
        assert make_excerpt("x" * 5000) == "x" * 3000 + "…"  # no space to cut at


class TestPressReleaseSelection:
    def _doc(self, type_, sequence):
        return SgmlDocument(type_, sequence, f"{type_}.htm", b"<p>x</p>")

    def test_prefers_ex_99_1_then_ex_99_then_the_lowest_other(self):
        docs = [
            self._doc("EX-99.3", 4),
            self._doc("EX-99.2", 3),
            self._doc("EX-99.1", 2),
        ]
        assert select_press_release(docs).type == "EX-99.1"
        assert select_press_release(docs[:2]).type == "EX-99.2"
        assert (
            select_press_release([self._doc("EX-99.2", 3), self._doc("EX-99", 2)]).type
            == "EX-99"
        )
        assert (
            select_press_release([self._doc("EX-10.1", 2), self._doc("8-K", 1)]) is None
        )
        assert select_press_release([]) is None


class TestBuildTexts:
    def test_results_item_stores_the_press_release_and_stops_streaming(self):
        consumed = []

        def stream():
            for doc in _documents():
                consumed.append(doc.type)
                yield doc

        rows = build_texts(stream(), want_press_release=True, want_body=False)
        assert [r["exhibit"] for r in rows] == ["EX-99.1"]
        assert rows[0]["filename"] == "ex991.htm"
        assert rows[0]["excerpt"].startswith(
            "Exhibit 99.1 Example reports third quarter results"
        )
        assert "&mdash;" not in rows[0]["excerpt"] and "—" in rows[0]["excerpt"]
        assert rows[0]["full_length"] == len(html_to_text(_documents()[2].text()))
        # stopped at the preferred exhibit: GRAPHIC and the XBRL files were not read
        assert consumed == ["8-K", "EX-99.2", "EX-99.1"]

    def test_event_item_stores_the_body_and_reads_only_the_first_document(self):
        consumed = []

        def stream():
            for doc in _documents():
                consumed.append(doc.type)
                yield doc

        rows = build_texts(stream(), want_press_release=False, want_body=True)
        assert [r["exhibit"] for r in rows] == ["8-K"]
        assert rows[0]["excerpt"].startswith("Item 2.02. Results of Operations")
        assert consumed == ["8-K"]

    def test_both_wanted_gives_press_release_and_body(self):
        rows = build_texts(iter(_documents()), True, True)
        assert [r["exhibit"] for r in rows] == ["EX-99.1", "8-K"]

    def test_without_an_exhibit_the_body_is_the_fallback(self):
        only_body = [d for d in _documents() if d.type == "8-K"]
        rows = build_texts(iter(only_body), want_press_release=True, want_body=False)
        assert [r["exhibit"] for r in rows] == ["8-K"]

    def test_nothing_found_gives_no_rows(self):
        assert build_texts(iter([]), True, True) == []
        empty = SgmlDocument("EX-99.1", 2, "e.htm", b"<html><body> </body></html>")
        assert build_texts(iter([empty]), True, False) == []

    def test_long_documents_are_cut_to_the_excerpt_length(self):
        words = "<p>" + "revenue grew strongly " * 400 + "</p>"
        doc = SgmlDocument("EX-99.1", 2, "e.htm", words.encode())
        [row] = build_texts(iter([doc]), True, False)
        assert row["full_length"] > 3000 and len(row["excerpt"]) <= 3001
        assert row["excerpt"].endswith("…")


def _client(ciks, submissions=None, pages=None):
    client = MagicMock()
    client.get_cik.side_effect = lambda t: ciks.get(t)
    client.fetch_submissions.side_effect = lambda cik: copy.deepcopy(
        (submissions or {}).get(cik, SUBMISSIONS)
    )
    client.get_submissions_page.side_effect = lambda name: (pages or {})[name]
    client.iter_filing_documents.side_effect = lambda cik, acc: iter(_documents())
    return client


def _fetch(collector, tickers, stored=None):
    with (
        patch.object(mod, "date", _FixedDate),
        patch.object(collector, "get_active_tickers", return_value=tickers),
        patch.object(collector, "_stored_filings", return_value=stored or {}),
    ):
        return collector.fetch(MagicMock())


class TestFetch:
    def test_new_filings_and_the_press_release_text(self):
        client = _client({"AAPL": AAPL})
        collector = SecFilingsCollector(lookback_days=120, client=client)
        entries = _fetch(collector, ["AAPL"])

        # oldest first; the 4 whitelisted filings since 2026-06-20
        assert [e["filing"]["form"] for e in entries] == ["8-K", "10-Q", "8-K/A", "144"]
        assert all(e["texts_done"] for e in entries)
        results = entries[0]
        assert results["accession_number"] == ACC_8K_RESULTS
        assert [t["exhibit"] for t in results["texts"]] == ["EX-99.1"]
        filing = results["filing"]
        assert filing["cik"] == AAPL and filing["ticker"] == "AAPL"
        assert filing["tickers"] == ["AAPL"]
        assert filing["url"].endswith("/000032019326000018/aapl-20260730.htm")
        assert "texts_fetched_at" not in filing
        client.iter_filing_documents.assert_called_once_with(AAPL, ACC_8K_RESULTS)
        client.load_cik_mapping.assert_called_once()
        # 1 submissions request + 1 text request
        assert (collector._attempts, collector._errors) == (2, 0)

    def test_share_classes_share_one_download(self):
        client = _client({"GOOG": "0001652044", "GOOGL": "0001652044"})
        collector = SecFilingsCollector(lookback_days=120, client=client)
        entries = _fetch(collector, ["GOOGL", "GOOG"])
        assert client.fetch_submissions.call_count == 1
        filing = entries[0]["filing"]
        assert filing["ticker"] == "GOOG" and filing["tickers"] == ["GOOG", "GOOGL"]
        assert filing["cik"] == "0001652044"

    def test_stored_filings_are_skipped_and_unfinished_texts_are_retried(self):
        client = _client({"AAPL": AAPL})
        collector = SecFilingsCollector(lookback_days=120, client=client)
        acc = {
            r["form"]: r["accession_number"]
            for r in extract_filing_rows(_recent(), date(2026, 6, 20))
        }
        stored = {
            acc["8-K"]: False,  # stored, press release not fetched yet
            acc["10-Q"]: True,
            acc["144"]: True,
        }
        entries = _fetch(collector, ["AAPL"], stored)
        by_acc = {e["accession_number"]: e for e in entries}
        # the finished filings produce no entry; the 8-K is retried, the 8-K/A is new
        assert set(by_acc) == {acc["8-K"], acc["8-K/A"]}
        retry = by_acc[acc["8-K"]]
        assert retry["filing"] is None and retry["texts_done"] is True
        assert [t["exhibit"] for t in retry["texts"]] == ["EX-99.1"]
        assert by_acc[acc["8-K/A"]]["filing"]["form"] == "8-K/A"
        assert by_acc[acc["8-K/A"]]["texts_done"] is True  # no text plan for an 8-K/A

    def test_a_failed_text_fetch_keeps_the_filing_and_is_retried_later(self):
        client = _client({"AAPL": AAPL})
        client.iter_filing_documents.side_effect = RuntimeError("HTTP 503")
        collector = SecFilingsCollector(lookback_days=120, client=client)
        entries = _fetch(collector, ["AAPL"])
        results = entries[0]
        assert results["filing"]["form"] == "8-K"
        assert results["texts"] == [] and results["texts_done"] is False
        assert collector._errors == 1
        # the other (text-less) filings are finished
        assert all(e["texts_done"] for e in entries[1:])

    def test_a_dry_run_plans_the_texts_without_downloading(self):
        client = _client({"AAPL": AAPL})
        collector = SecFilingsCollector(
            lookback_days=120, fetch_texts=False, client=client
        )
        entries = _fetch(collector, ["AAPL"])
        client.iter_filing_documents.assert_not_called()
        results = entries[0]
        assert results["texts"] == [] and results["texts_done"] is False

    def test_a_submissions_error_does_not_stop_the_other_companies(self):
        client = _client({"AAPL": AAPL, "MSFT": "0000789019"})

        def fetch_submissions(cik):
            if cik == AAPL:
                raise RuntimeError("HTTP 503")
            return copy.deepcopy(SUBMISSIONS)

        client.fetch_submissions.side_effect = fetch_submissions
        collector = SecFilingsCollector(lookback_days=120, client=client)
        entries = _fetch(collector, ["AAPL", "MSFT"])
        assert {e["filing"]["ticker"] for e in entries} == {"MSFT"}
        assert collector._errors == 1

    def test_tickers_without_a_cik_are_skipped(self):
        client = _client({"AAPL": AAPL})
        collector = SecFilingsCollector(lookback_days=120, client=client)
        entries = _fetch(collector, ["AAPL", "SPY", "ARKK"])
        assert client.fetch_submissions.call_count == 1
        assert {e["filing"]["ticker"] for e in entries} == {"AAPL"}

    def test_only_tickers_restricts_the_run(self):
        client = _client({"AAPL": AAPL, "MSFT": "0000789019"})
        collector = SecFilingsCollector(
            lookback_days=120, client=client, only_tickers=["MSFT"]
        )
        _fetch(collector, ["AAPL", "MSFT"])
        client.fetch_submissions.assert_called_once_with("0000789019")

    def test_the_lookback_never_reaches_before_the_retention_window(self):
        client = _client({"AAPL": AAPL})
        collector = SecFilingsCollector(lookback_days=365, client=client)
        with patch.object(mod, "data_start_date", return_value=date(2026, 9, 1)):
            entries = _fetch(collector, ["AAPL"])
        assert [e["filing"]["form"] for e in entries] == ["8-K/A", "144"]
        assert all(e["filing"]["filing_date"] >= date(2026, 9, 1) for e in entries)

    def test_older_pages_are_read_when_the_recent_list_does_not_reach_back(self):
        submissions = copy.deepcopy(SUBMISSIONS)
        recent = submissions["filings"]["recent"]
        # keep only the newest filing in `recent`: it starts after the window
        recent.update({k: v[:1] for k, v in recent.items()})
        submissions["filings"]["files"] = [
            {"name": "CIK0000320193-submissions-001.json", "filingTo": "2026-09-30"},
            {"name": "CIK0000320193-submissions-000.json", "filingTo": "2019-01-01"},
        ]
        page = copy.deepcopy(SUBMISSIONS["filings"]["recent"])  # an older page
        client = _client(
            {"AAPL": AAPL},
            {AAPL: submissions},
            {"CIK0000320193-submissions-001.json": page},
        )
        collector = SecFilingsCollector(lookback_days=120, client=client)
        entries = _fetch(collector, ["AAPL"])
        client.get_submissions_page.assert_called_once_with(
            "CIK0000320193-submissions-001.json"  # the 2019 page is too old
        )
        # the 144 of `recent` is also on the page: one row per accession
        assert [e["filing"]["form"] for e in entries] == ["8-K", "10-Q", "8-K/A", "144"]
        assert collector._errors == 0 and collector._attempts == 2 + 1  # + 1 text

    def test_older_pages_are_not_read_when_recent_reaches_back(self):
        client = _client({"AAPL": AAPL})
        collector = SecFilingsCollector(lookback_days=120, client=client)
        _fetch(collector, ["AAPL"])
        client.get_submissions_page.assert_not_called()


class TestStoredFilings:
    def test_maps_accession_to_the_finished_flag_and_releases_the_transaction(self):
        collector = SecFilingsCollector(client=MagicMock())
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("acc-1", None),
            ("acc-2", datetime(2026, 10, 1, tzinfo=UTC)),
        ]
        assert collector._stored_filings(session, date(2026, 10, 1)) == {
            "acc-1": False,
            "acc-2": True,
        }
        session.commit.assert_called()

    def test_a_database_error_gives_an_empty_result(self):
        collector = SecFilingsCollector(client=MagicMock())
        session = MagicMock()
        session.execute.side_effect = RuntimeError("relation does not exist")
        assert collector._stored_filings(session, date(2026, 10, 1)) == {}
        session.rollback.assert_called_once()


class TestStore:
    def _entry(self, acc="0000320193-26-000018", filing=True, texts=1, done=True):
        return {
            "accession_number": acc,
            "filing": (
                {
                    "cik": AAPL,
                    "ticker": "AAPL",
                    "tickers": ["AAPL"],
                    "accession_number": acc,
                    "form": "8-K",
                    "filing_date": date(2026, 7, 30),
                    "acceptance_datetime": datetime(2026, 7, 30, 20, 30, tzinfo=UTC),
                    "acceptance_raw": "2026-07-31T00:30:28.000Z",
                    "report_date": date(2026, 7, 30),
                    "items": ["2.02", "9.01"],
                    "primary_document": "a.htm",
                    "url": "https://x/a.htm",
                    "is_amendment": False,
                }
                if filing
                else None
            ),
            "texts": [
                {
                    "exhibit": "EX-99.1",
                    "filename": "e.htm",
                    "excerpt": "text",
                    "full_length": 4,
                }
            ][:texts],
            "texts_done": done,
        }

    def _session(self, rowcounts=(1, 1, 1)):
        session = MagicMock()
        session.execute.side_effect = [MagicMock(rowcount=n) for n in rowcounts]
        return session

    def test_filings_texts_and_the_finished_marker_are_written_in_order(self):
        collector = SecFilingsCollector(client=MagicMock())
        session = self._session()
        fetched, written = collector.store(session, [self._entry()])
        assert (fetched, written) == (1, 2)  # 1 filing + 1 text
        sql = [_sql(c.args[0]) for c in session.execute.call_args_list]
        assert len(sql) == 3
        assert sql[0].startswith("INSERT INTO signals.sec_filings")
        assert "ON CONFLICT (accession_number) DO NOTHING" in sql[0]
        assert sql[1].startswith("INSERT INTO signals.sec_filing_texts")
        assert "ON CONFLICT (accession_number, exhibit) DO UPDATE" in sql[1]
        assert sql[2].startswith("UPDATE signals.sec_filings SET texts_fetched_at")
        assert "texts_fetched_at IS NULL" in sql[2]
        session.flush.assert_called()

    def test_a_retry_writes_only_the_text(self):
        collector = SecFilingsCollector(client=MagicMock())
        session = self._session((1, 1))
        fetched, written = collector.store(session, [self._entry(filing=False)])
        sql = [_sql(c.args[0]) for c in session.execute.call_args_list]
        assert [s.split()[0] for s in sql] == ["INSERT", "UPDATE"]
        assert "sec_filing_texts" in sql[0]
        assert (fetched, written) == (1, 1)

    def test_unfinished_text_steps_are_not_marked(self):
        collector = SecFilingsCollector(client=MagicMock())
        session = self._session((1,))
        collector.store(session, [self._entry(texts=0, done=False)])
        sql = [_sql(c.args[0]) for c in session.execute.call_args_list]
        assert len(sql) == 1 and sql[0].startswith("INSERT INTO signals.sec_filings")

    def test_nothing_to_store(self):
        collector = SecFilingsCollector(client=MagicMock())
        session = MagicMock()
        assert collector.store(session, []) == (0, 0)
        session.execute.assert_not_called()


def test_collector_identity():
    collector = SecFilingsCollector(client=MagicMock())
    assert collector.name == "sec_filings"
    assert collector.lookback_days == 14 and collector.fetch_texts is True
    assert collector.only_tickers is None
    assert SecFilingsCollector(client=MagicMock(), only_tickers=["A"]).only_tickers == {
        "A"
    }
