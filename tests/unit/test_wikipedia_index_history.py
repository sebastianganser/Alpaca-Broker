"""Tests for the Wikipedia/GitHub index-history parser (all HTTP mocked).

Fixtures (``tests/fixtures/wikipedia_*.html``) are trimmed copies of the real
tables fetched on 2026-10-16:

* ``wikipedia_sp500_changes.html`` -- "Historical components of the S&P 500":
  nested ``Added``/``Removed`` headers with ``Ticker``/``Security`` sub-headers,
  ticker renames (CDAY->DAY, WLTW->WTW), a re-used symbol (Q: Quintiles until
  2017, Qnity 2025), junk cells ("ALLE |"), a 4-day member (MBC).
* ``wikipedia_nasdaq100_changes.html`` -- "Historical components of the
  Nasdaq-100" (same header shape).
* ``wikipedia_sp500_current.html`` -- constituents table with "Date added"
  (the additions-only fallback).
"""

import io
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from trading_signals.universe import wikipedia_index_history as w
from trading_signals.universe.ticker_aliases import TickerAlias
from trading_signals.universe.wikipedia_index_history import (
    GITHUB_SP500_CSV_URL,
    GITHUB_SP500_CSV_URL_STALE,
    GitHubSP500Backup,
    IndexChange,
    ParseResult,
    WikipediaIndexHistoryParser,
    clean_ticker,
    fetch_html,
    generate_intervals_from_changes,
    normalize_changes,
    seed_blockers,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SINCE = date(2021, 1, 1)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _tables(name: str) -> list[pd.DataFrame]:
    return pd.read_html(io.StringIO(_read(name)))


@pytest.fixture
def pages():
    """``fetch_html`` serves the saved fixtures; tests may override entries."""
    served = {
        w.SP500_CHANGES_URL: _read("wikipedia_sp500_changes.html"),
        w.SP500_CURRENT_URL: _read("wikipedia_sp500_current.html"),
        w.NASDAQ100_CHANGES_URL: _read("wikipedia_nasdaq100_changes.html"),
    }

    def fake(url, **kwargs):
        value = served[url]
        if isinstance(value, Exception):
            raise value
        return value

    with patch.object(w, "fetch_html", side_effect=fake) as mock:
        mock.served = served
        yield mock


def _events(result: ParseResult, action: str) -> dict[str, date]:
    return {c.ticker: c.change_date for c in result.changes if c.action == action}


# ── Changes table detection ──────────────────────────────────────────────


class TestFindChangesTable:
    parser = WikipediaIndexHistoryParser()

    def test_multiindex_group_headers_are_detected(self):
        tables = _tables("wikipedia_sp500_changes.html")
        df = self.parser._find_changes_table(tables, "sp500")
        assert df is not None and len(df) > 10
        assert isinstance(df.columns, pd.MultiIndex)
        assert {c[0] for c in df.columns} >= {"Added", "Removed"}

    def test_last_level_alone_does_not_contain_the_group_names(self):
        """The old ``get_level_values(-1)`` flattening dropped Added/Removed."""
        df = _tables("wikipedia_sp500_changes.html")[0]
        last = [str(c).lower() for c in df.columns.get_level_values(-1)]
        assert "added" not in last and "removed" not in last
        assert self.parser._find_changes_table([df], "sp500") is not None

    def test_constituents_table_is_not_a_changes_table(self):
        tables = _tables("wikipedia_sp500_current.html")
        assert self.parser._find_changes_table(tables, "sp500") is None

    def test_date_added_column_is_not_a_changes_table(self):
        df = pd.DataFrame({"Name": list("abcdefghijklm"), "Date added": ["2020"] * 13})
        assert self.parser._find_changes_table([df], "sp500") is None

    def test_small_table_is_ignored(self):
        df = pd.DataFrame({"Date": ["x"] * 5, "Added": ["A"] * 5, "Removed": ["B"] * 5})
        assert self.parser._find_changes_table([df], "sp500") is None

    def test_flat_headers_still_work(self):
        df = pd.DataFrame({
            "Date": ["January 5, 2023"] * 12,
            "Added": ["AAA"] * 12,
            "Removed": ["BBB"] * 12,
        })
        assert self.parser._find_changes_table([df], "sp500") is df


# ── S&P 500 parse ────────────────────────────────────────────────────────


class TestParseSp500Changes:
    def test_reads_the_historical_components_page(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        urls = [c.args[0] for c in pages.call_args_list]
        assert urls == [w.SP500_CHANGES_URL]  # no fallback fetch
        assert "Historical_components_of_the_S%26P_500" in w.SP500_CHANGES_URL
        assert result.source == "wikipedia_sp500"

    def test_result_is_complete_with_removals(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        assert result.complete is True
        assert result.incomplete_reason is None
        assert result.has_removals
        assert result.warnings == []
        assert result.as_of == date(2026, 10, 6)
        assert seed_blockers(result) == []

    def test_additions_and_removals_with_effective_dates(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        assert _events(result, "removed") == {
            "UNM": date(2021, 9, 20),
            "LEG": date(2021, 12, 20),
            "FBIN": date(2022, 12, 19),
            "MBC": date(2022, 12, 19),
            "SOLS": date(2025, 12, 22),
            "PAYC": date(2026, 3, 23),
            "TAP": date(2026, 9, 21),
            "TTD": date(2026, 9, 21),
            "BLDR": date(2026, 9, 21),
            "WBD": date(2026, 10, 6),
            "CTVA": date(2026, 10, 6),
        }
        added = _events(result, "added")
        assert added["P"] == date(2026, 9, 21)
        assert added["TWLO"] == date(2026, 10, 6)
        assert added["VYLR"] == date(2026, 10, 1)
        assert {"TAP", "WBD"}.isdisjoint(added)

    def test_replaced_by_and_reason_are_kept(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        wbd = next(c for c in result.changes if c.ticker == "WBD")
        assert wbd.replaced_by == "TWLO"
        assert "Paramount Skydance acquired" in wbd.reason
        ctva = next(c for c in result.changes if c.ticker == "CTVA")
        assert ctva.replaced_by is None  # row without an added ticker

    def test_ticker_renames_are_not_exits(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        tickers = {c.ticker for c in result.changes}
        # CDAY->DAY (2024-02-01) and WLTW->WTW (2022-01-10) rows produce no event
        assert "CDAY" not in tickers and "WLTW" not in tickers and "WTW" not in tickers
        assert not [c for c in result.changes if c.change_date == date(2024, 2, 1)]
        assert not [c for c in result.changes if c.change_date == date(2022, 1, 10)]
        assert {(a.old, a.new) for a in result.renames} == {
            ("CDAY", "DAY"),
            ("WLTW", "WTW"),
        }

    def test_events_of_old_symbols_are_keyed_by_the_current_symbol(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        by_ticker = {c.ticker: c for c in result.changes}
        # CDAY was added 2021-09-20 and became DAY
        assert by_ticker["DAY"].change_date == date(2021, 9, 20)
        assert by_ticker["DAY"].original_ticker == "CDAY"
        # SATS (added 2026-03-23) became ECHO on 2026-06-24
        assert by_ticker["ECHO"].original_ticker == "SATS"
        # FBHS left on 2022-12-19 after renaming FBIN on 2022-12-15
        assert by_ticker["FBIN"].original_ticker == "FBHS"
        assert by_ticker["FBIN"].action == "removed"
        assert "SATS" not in by_ticker and "FBHS" not in by_ticker

    def test_reused_symbol_is_not_rekeyed(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(
            since=date(2000, 1, 1)
        )
        # Q -> IQV (2017) is in the table, but Qnity's later "Q" stays Q
        assert ("Q", "IQV") in {(a.old, a.new) for a in result.renames}
        qnity = [c for c in result.changes if c.change_date == date(2025, 11, 3)]
        assert [(c.ticker, c.original_ticker) for c in qnity] == [("Q", None)]

    def test_junk_cells_are_cleaned(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(
            since=date(2000, 1, 1)
        )
        old = [c for c in result.changes if c.change_date == date(2013, 12, 2)]
        assert {(c.action, c.ticker) for c in old} == {
            ("added", "ALLE"),
            ("removed", "JCP"),
        }

    def test_since_filters_events_but_not_rename_detection(self, pages):
        parser = WikipediaIndexHistoryParser()
        recent = parser.parse_sp500_changes(since=date(2026, 1, 1))
        assert min(c.change_date for c in recent.changes) >= date(2026, 1, 1)
        assert recent.as_of == date(2026, 10, 6)  # newest of the whole table
        assert recent.renames == []  # both renames are older than 2026
        assert recent.complete  # completeness looks at the whole table

    def test_default_window_is_the_retention_start(self, pages):
        with patch.object(w, "data_start_date", return_value=date(2025, 1, 1)):
            result = WikipediaIndexHistoryParser().parse_sp500_changes()
        assert min(c.change_date for c in result.changes) >= date(2025, 1, 1)
        assert any(c.ticker == "SOLS" for c in result.changes)

    def test_parse_df_keeps_the_input_untouched(self):
        df = _tables("wikipedia_sp500_changes.html")[0]
        columns_before = list(df.columns)
        parser = WikipediaIndexHistoryParser()
        changes = parser._parse_sp500_changes_df(df, since=SINCE)
        assert list(df.columns) == columns_before
        assert len(changes) == 24

    def test_missing_columns_yield_nothing_and_no_crash(self):
        df = pd.DataFrame({"Foo": [1], "Bar": [2]})
        assert WikipediaIndexHistoryParser()._parse_sp500_changes_df(df) == []


class TestIncompleteSp500Sources:
    """The additions-only fallback must never look like a usable history."""

    def test_missing_table_falls_back_to_additions_and_is_flagged(self, pages, caplog):
        pages.served[w.SP500_CHANGES_URL] = _read("wikipedia_sp500_current.html")
        with caplog.at_level("ERROR"):
            result = WikipediaIndexHistoryParser().parse_sp500_changes(
                since=date(2026, 1, 1)
            )
        assert result.complete is False
        assert not result.has_removals
        assert {c.action for c in result.changes} == {"added"}
        assert {c.ticker for c in result.changes} == {"ECHO", "P", "TWLO", "VYLR"}
        assert "NO removals" in result.incomplete_reason
        assert any(x.startswith("INCOMPLETE") for x in result.warnings)
        assert "INCOMPLETE" in caplog.text  # logged at ERROR
        assert [r.levelname for r in caplog.records if "INCOMPLETE" in r.message] == [
            "ERROR"
        ]

    def test_seed_blockers_name_the_source_and_reason(self, pages):
        pages.served[w.SP500_CHANGES_URL] = _read("wikipedia_sp500_current.html")
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        blockers = seed_blockers(result)
        assert len(blockers) == 1
        assert blockers[0].startswith("wikipedia_sp500:")
        assert "no change table" in blockers[0]

    def test_changes_page_error_falls_back_and_is_flagged(self, pages):
        pages.served[w.SP500_CHANGES_URL] = requests.HTTPError("403 too many requests")
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        assert result.complete is False
        assert any(
            "Failed to fetch/parse S&P 500 changes page" in x for x in result.warnings
        )
        assert seed_blockers(result)

    def test_unparsable_changes_table_falls_back_and_is_flagged(self, pages):
        parser = WikipediaIndexHistoryParser()
        with patch.object(parser, "_parse_sp500_table", side_effect=KeyError("x")):
            result = parser.parse_sp500_changes(since=date(2026, 1, 1))
        assert result.complete is False
        assert result.renames == [] and result.as_of is None
        assert any(
            "Failed to parse the S&P 500 changes table" in x for x in result.warnings
        )
        assert {c.action for c in result.changes} == {"added"}  # fallback additions

    def test_both_pages_failing_gives_an_empty_incomplete_result(self, pages):
        pages.served[w.SP500_CHANGES_URL] = requests.HTTPError("boom")
        pages.served[w.SP500_CURRENT_URL] = requests.HTTPError("boom")
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        assert result.changes == [] and result.complete is False
        assert seed_blockers(result)

    def test_table_without_removals_is_incomplete(self, pages):
        rows = "".join(
            f"<tr><td>May {d}, 2023</td><td>T{d}</td><td>Sec {d}</td><td></td><td></td>"
            "<td>Market capitalization changes.</td><td></td></tr>"
            for d in range(1, 13)
        )
        pages.served[w.SP500_CHANGES_URL] = (
            "<html><body><table><tbody><tr><th rowspan='2'>Effective Date</th>"
            "<th colspan='2'>Added</th><th colspan='2'>Removed</th>"
            "<th rowspan='2'>Reason</th><th rowspan='2'>Refs</th></tr>"
            "<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>"
            f"{rows}</tbody></table></body></html>"
        )
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        assert len(result.changes) == 12
        assert result.complete is False
        assert "0 removals" in result.incomplete_reason

    def test_complete_results_do_not_block(self):
        assert seed_blockers(ParseResult(source="x", complete=True)) == []
        assert seed_blockers(
            ParseResult(source="a", complete=True), ParseResult(source="b")
        ) == ["b: incomplete (no removal data)"]


# ── Nasdaq-100 (unchanged behaviour) ─────────────────────────────────────


class TestNasdaq100:
    def test_parses_added_and_removed_with_dates(self, pages):
        result = WikipediaIndexHistoryParser().parse_nasdaq100_changes(since=SINCE)
        assert result.source == "wikipedia_nasdaq100"
        assert result.complete is True
        assert len(result.changes) == 26
        assert {c.index_name for c in result.changes} == {"nasdaq100"}
        assert all(c.original_ticker is None for c in result.changes)  # no re-keying
        removed, added = _events(result, "removed"), _events(result, "added")
        assert removed["WBD"] == date(2026, 10, 9)
        assert removed["EA"] == date(2026, 8, 4)
        assert removed["KHC"] == date(2026, 9, 14)
        assert added["MRNA"] == date(2026, 10, 9)
        assert added["SPCX"] == date(2026, 7, 7)
        assert added["SOLS"] == date(2025, 10, 30)
        assert removed["SOLS"] == date(2025, 11, 6)
        assert result.as_of == date(2026, 10, 9)

    def test_window_start_excludes_old_rows(self, pages):
        result = WikipediaIndexHistoryParser().parse_nasdaq100_changes(since=SINCE)
        assert min(c.change_date for c in result.changes) == date(2021, 7, 21)
        assert "LOGI" not in {c.ticker for c in result.changes}  # 2007 row

        default = WikipediaIndexHistoryParser().parse_nasdaq100_changes()
        assert min(c.change_date for c in default.changes) >= w.data_start_date()

    def test_fetch_error_is_an_incomplete_result(self, pages):
        pages.served[w.NASDAQ100_CHANGES_URL] = requests.HTTPError("403")
        result = WikipediaIndexHistoryParser().parse_nasdaq100_changes(since=SINCE)
        assert result.changes == [] and result.complete is False
        assert seed_blockers(result)

    def test_page_without_tables_is_incomplete(self, pages):
        pages.served[w.NASDAQ100_CHANGES_URL] = "<html><body><p>moved</p></body></html>"
        result = WikipediaIndexHistoryParser().parse_nasdaq100_changes(since=SINCE)
        assert result.complete is False

    def test_extract_tickers_from_cell(self):
        p = WikipediaIndexHistoryParser()
        assert p._extract_tickers_from_cell("AAPL, MSFT") == ["AAPL", "MSFT"]
        assert p._extract_tickers_from_cell("nan") == []


# ── Helpers ──────────────────────────────────────────────────────────────


class TestCleanTicker:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ALLE |", "ALLE"),
            (" brk.b ", "BRK.B"),
            ("TWLO[1]", "TWLO"),
            ("P", "P"),
            (float("nan"), None),
            (None, None),
            ("nan", None),
            ("", None),
            ("—", None),
        ],
    )
    def test_clean(self, raw, expected):
        assert clean_ticker(raw) == expected


class TestNormalizeChanges:
    @staticmethod
    def _c(day, ticker, action, **kw):
        return IndexChange(
            change_date=day, ticker=ticker, index_name="sp500", action=action, **kw
        )

    def test_rename_pair_is_cancelled(self):
        day = date(2022, 6, 9)
        out = normalize_changes([
            self._c(day, "FB", "removed"),
            self._c(day, "META", "added"),
            self._c(day, "OTHER", "added"),
        ])
        assert [(c.ticker, c.action) for c in out] == [("OTHER", "added")]

    def test_lone_old_symbol_event_is_rekeyed(self):
        out = normalize_changes([self._c(date(2021, 3, 1), "FB", "added")])
        assert (out[0].ticker, out[0].original_ticker) == ("META", "FB")

    def test_same_day_exit_and_readd_of_one_symbol_is_kept(self):
        day = date(2023, 1, 5)
        out = normalize_changes([
            self._c(day, "ABC1", "removed"),
            self._c(day, "ABC1", "added"),
        ])
        assert len(out) == 2

    def test_duplicates_are_dropped(self):
        day = date(2023, 1, 5)
        out = normalize_changes([self._c(day, "AAA", "added")] * 2)
        assert len(out) == 1

    def test_replaced_by_is_rekeyed(self):
        out = normalize_changes([
            self._c(date(2022, 6, 10), "OLDCO", "removed", replaced_by="FB")
        ])
        assert out[0].replaced_by == "META"

    def test_custom_alias_map(self):
        m = w.DEFAULT_ALIASES.with_extra(
            [TickerAlias("AAA", "BBB", date(2022, 1, 1), "test")]
        )
        out = normalize_changes([self._c(date(2021, 1, 1), "AAA", "added")], m)
        assert out[0].ticker == "BBB"


class TestFetchHtml:
    @staticmethod
    def _resp(status, text=""):
        r = MagicMock(status_code=status, text=text)
        r.raise_for_status.side_effect = (
            requests.HTTPError(f"{status}") if status >= 400 else None
        )
        return r

    def test_throttling_is_retried_with_backoff(self):
        session = MagicMock()
        session.get.side_effect = [
            self._resp(403),
            self._resp(429),
            self._resp(200, "ok"),
        ]
        with patch.object(w.time, "sleep") as sleep:
            assert fetch_html("https://x", session=session) == "ok"
        assert [c.args[0] for c in sleep.call_args_list] == [5.0, 20.0]
        assert session.get.call_count == 3

    def test_gives_up_after_the_last_attempt(self):
        session = MagicMock()
        session.get.return_value = self._resp(403)
        with patch.object(w.time, "sleep"), pytest.raises(requests.HTTPError):
            fetch_html("https://x", session=session)
        assert session.get.call_count == 3

    def test_not_found_is_not_retried(self):
        session = MagicMock()
        session.get.return_value = self._resp(404)
        with patch.object(w.time, "sleep") as sleep, pytest.raises(requests.HTTPError):
            fetch_html("https://x", session=session)
        sleep.assert_not_called()
        assert session.get.call_count == 1

    def test_without_session_sends_the_user_agent(self):
        with patch.object(w.requests, "get", return_value=self._resp(200, "t")) as get:
            assert fetch_html("https://x") == "t"
        assert get.call_args.kwargs["headers"]["User-Agent"] == w.USER_AGENT


# ── Intervals from the parsed fixture ────────────────────────────────────


class TestIntervalsFromParsedHistory:
    def test_renames_do_not_create_intervals_and_exits_close_them(self, pages):
        result = WikipediaIndexHistoryParser().parse_sp500_changes(since=SINCE)
        current = {"sp500": {"ECHO", "P", "TWLO", "BE", "ILMN", "VYLR", "WTW"}}
        intervals = generate_intervals_from_changes(
            result.changes, current, date(2021, 10, 1)
        )
        by_ticker = {}
        for iv in intervals:
            by_ticker.setdefault(iv["ticker"], []).append(iv)

        for old in ("CDAY", "WLTW", "SATS", "FBHS"):
            assert old not in by_ticker

        (echo,) = by_ticker["ECHO"]  # SATS added 2026-03-23, renamed ECHO
        assert (echo["valid_from"], echo["valid_to"]) == (date(2026, 3, 23), None)
        (day,) = by_ticker["DAY"]  # CDAY added 2021-09-20, one continuous interval
        assert (day["valid_from"], day["valid_to"]) == (date(2021, 9, 20), None)
        (fbin,) = by_ticker["FBIN"]  # exit after the rename, keyed by FBIN
        assert (fbin["valid_from"], fbin["valid_to"]) == (
            date(2021, 10, 1),
            date(2022, 12, 19),
        )
        (mbc,) = by_ticker["MBC"]
        assert (mbc["valid_from"], mbc["valid_to"]) == (
            date(2022, 12, 15),
            date(2022, 12, 19),
        )
        (tap,) = by_ticker["TAP"]
        assert tap["valid_to"] == date(2026, 9, 21)
        (wbd,) = by_ticker["WBD"]
        assert wbd["valid_to"] == date(2026, 10, 6)
        (wtw,) = by_ticker["WTW"]  # long-standing member, seeded from data start
        assert (wtw["valid_from"], wtw["source"]) == (date(2021, 10, 1), "initial_seed")


# ── GitHub CSV (cross-check source) ──────────────────────────────────────


def _csv(rows) -> str:
    return "date,tickers\n" + "\n".join(
        f'{d.isoformat()},"{",".join(t)}"' for d, t in rows
    )


def _gh_response(rows):
    resp = MagicMock(text=_csv(rows))
    resp.raise_for_status.return_value = None
    return resp


def _serve_github(rows):
    """Patch the CSV download with the given snapshot rows."""
    return patch.object(w.requests, "get", return_value=_gh_response(rows))


class TestGitHubBackup:
    @staticmethod
    def _rows(last: date):
        return [
            (date(2020, 12, 21), ["AAA", "BBB", "FB", "PARA", "CCC"]),
            (date(2022, 6, 9), ["AAA", "BBB", "META", "PARA", "CCC"]),  # FB->META
            (date(2024, 1, 2), ["AAA", "META", "PARA", "CCC", "DDD"]),
            (date(2025, 8, 8), ["AAA", "META", "PSKY", "CCC", "DDD"]),  # merger
            (last, ["AAA", "META", "PSKY", "CCC", "DDD", "EEE"]),
        ]

    def test_points_at_the_maintained_updated_file(self):
        assert "%28Updated%29" in GITHUB_SP500_CSV_URL
        assert GITHUB_SP500_CSV_URL != GITHUB_SP500_CSV_URL_STALE
        assert "Updated" not in GITHUB_SP500_CSV_URL_STALE
        last = date.today() - timedelta(days=10)
        with _serve_github(self._rows(last)) as get:
            GitHubSP500Backup().fetch_changes(since=SINCE)
        assert get.call_args.args[0] == GITHUB_SP500_CSV_URL

    def test_diffs_snapshots_and_cancels_known_renames(self):
        last = date.today() - timedelta(days=10)
        with _serve_github(self._rows(last)):
            result = GitHubSP500Backup().fetch_changes(since=SINCE)
        got = sorted((c.change_date, c.action, c.ticker) for c in result.changes)
        assert got == sorted([
            # 2022-06-09 FB->META: rename, no events
            (date(2024, 1, 2), "removed", "BBB"),
            (date(2024, 1, 2), "added", "DDD"),
            # PARA->PSKY is a merger, not a rename: exit + addition (the new
            # company is keyed by its current symbol, PSKY became SKYD in 2026)
            (date(2025, 8, 8), "removed", "PARA"),
            (date(2025, 8, 8), "added", "SKYD"),
            (last, "added", "EEE"),
        ])
        skyd = next(c for c in result.changes if c.ticker == "SKYD")
        assert skyd.original_ticker == "PSKY"
        assert result.complete is True
        assert result.as_of == last
        assert {c.source for c in result.changes} == {"github_csv"}

    def test_window_start_uses_the_previous_snapshot_as_baseline(self):
        last = date.today() - timedelta(days=10)
        with _serve_github(self._rows(last)):
            result = GitHubSP500Backup().fetch_changes(since=date(2024, 1, 2))
        assert min(c.change_date for c in result.changes) == date(2024, 1, 2)
        assert ("BBB", "removed") in {(c.ticker, c.action) for c in result.changes}

    def test_stale_file_is_flagged_incomplete(self, caplog):
        """The original file ends in 2019 -- never usable for seeding."""
        rows = [
            (date(2018, 6, 1), ["AAA", "BBB"]),
            (date(2019, 1, 11), ["AAA", "CCC"]),
        ]
        with _serve_github(rows), caplog.at_level("ERROR"):
            result = GitHubSP500Backup().fetch_changes(since=SINCE)
        assert result.changes == []
        assert result.complete is False
        assert "2019-01-11" in result.incomplete_reason
        assert "days old" in result.incomplete_reason
        assert seed_blockers(result)
        assert "INCOMPLETE" in caplog.text

    def test_fresh_file_without_removals_is_incomplete(self):
        last = date.today() - timedelta(days=5)
        rows = [(date(2025, 1, 2), ["AAA", "BBB"]), (last, ["AAA", "BBB", "CCC"])]
        with _serve_github(rows):
            result = GitHubSP500Backup().fetch_changes(since=SINCE)
        assert result.complete is False
        assert "no removals" in result.incomplete_reason

    def test_download_error_is_incomplete(self):
        down = requests.ConnectionError("down")
        with patch.object(w.requests, "get", side_effect=down):
            result = GitHubSP500Backup().fetch_changes(since=SINCE)
        assert result.changes == [] and result.complete is False
        assert any("Failed to fetch GitHub S&P 500 CSV" in x for x in result.warnings)

    def test_unexpected_format_is_incomplete(self):
        resp = MagicMock(text="only_one_column\n1\n")
        resp.raise_for_status.return_value = None
        with patch.object(w.requests, "get", return_value=resp):
            result = GitHubSP500Backup().fetch_changes(since=SINCE)
        assert result.complete is False
        assert "unexpected format" in result.incomplete_reason
