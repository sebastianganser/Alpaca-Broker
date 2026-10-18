"""Tests for scripts/repair/fix_sec_filing_subjects.py (no DB, no network)."""

import importlib.util
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from trading_signals.collectors.sec_client import HeaderParties, parse_header_parties

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fix = _load("scripts/repair/fix_sec_filing_subjects.py", "fix_sec_filing_subjects")

BLK = "0002012383"
AAPL = "0000320193"
DOCEAN = "0001582961"  # not a universe company
GROUPS = {AAPL: ["AAPL"], BLK: ["BLK"], "0001652044": ["GOOG", "GOOGL"]}


def _row(i=1, cik=BLK, ticker="BLK", form="SCHEDULE 13G/A", acc=None):
    return fix.Row(
        id=i,
        accession_number=acc or f"0001086364-26-{i:06d}",
        form=form,
        cik=cik,
        ticker=ticker,
        filing_date=date(2026, 10, 16),
        primary_document="xslSCHEDULE_13G_X02/primary_doc.xml",
    )


def _parties(subjects=(), others=()):
    return HeaderParties(tuple(subjects), frozenset(others))


class TestClassify:
    def test_the_stored_company_is_the_subject_keep(self):
        row = _row(cik=AAPL, ticker="AAPL")
        d = fix.classify_row(row, _parties([AAPL], [BLK]), GROUPS)
        assert d.action == fix.KEEP

    def test_the_stored_company_is_the_filer_and_the_subject_is_covered_update(self):
        d = fix.classify_row(_row(), _parties([AAPL], [BLK]), GROUPS)
        assert d.action == fix.UPDATE
        assert (d.new_cik, d.new_tickers) == (AAPL, ("AAPL",))

    def test_all_tickers_of_the_subject_cik_are_taken_over(self):
        d = fix.classify_row(_row(), _parties(["0001652044"], [BLK]), GROUPS)
        assert d.action == fix.UPDATE and d.new_tickers == ("GOOG", "GOOGL")

    def test_the_stored_filer_with_an_uncovered_subject_is_deleted(self):
        d = fix.classify_row(_row(), _parties([DOCEAN], [BLK]), GROUPS)
        assert d.action == fix.DELETE and d.new_cik is None

    def test_with_several_subjects_the_first_covered_one_wins(self):
        d = fix.classify_row(_row(), _parties([DOCEAN, AAPL], [BLK]), GROUPS)
        assert d.action == fix.UPDATE and d.new_cik == AAPL

    def test_a_stored_subject_stays_even_if_it_left_the_universe(self):
        row = _row(cik=DOCEAN, ticker="DOCN")
        d = fix.classify_row(row, _parties([DOCEAN]), GROUPS)
        assert d.action == fix.KEEP

    def test_without_a_subject_block_a_named_filer_is_deleted_others_are_kept(self):
        parties = _parties(others=[BLK])
        assert fix.classify_row(_row(), parties, GROUPS).action == fix.DELETE
        row = _row(cik=AAPL, ticker="AAPL")
        assert fix.classify_row(row, parties, GROUPS).action == fix.KEEP

    def test_real_headers(self):
        jpm_header = (FIXTURES / "sec_hdr_13g_filed_by_jpm.sgml").read_text()
        jpm = parse_header_parties(jpm_header)
        row = _row(cik="0000019617", ticker="JPM", acc="0000019617-26-000392")
        assert fix.classify_row(row, jpm, GROUPS).action == fix.DELETE
        wmt = parse_header_parties(
            (FIXTURES / "sec_hdr_144_walmart_self_submitted.sgml").read_text()
        )
        wrow = _row(cik="0000104169", ticker="WMT", form="144")
        assert fix.classify_row(wrow, wmt, GROUPS).action == fix.KEEP


class TestPlan:
    def test_decisions_and_errors_are_separated_and_progress_is_reported(self):
        rows = [_row(1), _row(2), _row(3, cik=AAPL, ticker="AAPL")]

        def fetch(cik, accession):
            if accession.endswith("000002"):
                raise RuntimeError("HTTP 503")
            return _parties([AAPL], [BLK])

        seen = []
        decisions, errors = fix.plan(
            rows, GROUPS, fetch, progress=lambda done, total: seen.append((done, total))
        )
        assert [d.action for d in decisions] == [fix.UPDATE, fix.KEEP]
        assert [r.id for r, _ in errors] == [2] and "HTTP 503" in errors[0][1]
        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_the_header_is_read_under_the_stored_cik(self):
        calls = []

        def fetch(cik, accession):
            calls.append((cik, accession))
            return _parties([AAPL])

        fix.plan([_row()], GROUPS, fetch)
        assert calls == [(BLK, "0001086364-26-000001")]


class TestSummary:
    def test_counts_per_action_and_form_examples_and_errors(self):
        to_aapl = _parties([AAPL], [BLK])
        decisions = [fix.classify_row(_row(i), to_aapl, GROUPS) for i in range(1, 13)]
        decisions += [
            fix.classify_row(_row(20, form="144"), _parties([DOCEAN], [BLK]), GROUPS)
        ]
        decisions += [
            fix.classify_row(
                _row(21, cik=AAPL, ticker="AAPL"), _parties([AAPL]), GROUPS
            )
        ]
        text = "\n".join(fix.summarize(decisions, [(_row(30), "ValueError: x")]))
        assert "rows checked: 15 (keep 1, update 12, delete 1, header errors 1)" in text
        assert "SCHEDULE 13G/A" in text and "144" in text
        assert "BLK 13" in text  # most affected tickers
        assert "examples - update (12 rows):" in text
        assert text.count("-> 0000320193 (AAPL)") == fix.EXAMPLES  # only 10 examples
        assert "examples - delete (1 rows):" in text
        assert "header errors (1; the rows stay untouched):" in text

    def test_nothing_checked(self):
        assert fix.summarize([], []) == [
            "rows checked: 0 (keep 0, update 0, delete 0, header errors 0)"
        ]


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestApply:
    def test_updates_and_deletes_are_written_in_the_callers_transaction(self):
        decisions = [
            fix.classify_row(_row(1), _parties([AAPL], [BLK]), GROUPS),  # update
            fix.classify_row(_row(2), _parties([DOCEAN], [BLK]), GROUPS),  # delete
            fix.classify_row(_row(3), _parties([DOCEAN], [BLK]), GROUPS),  # delete
        ]
        session = MagicMock()
        session.execute.return_value = MagicMock(rowcount=1)
        # (the sums use rowcount per statement: 1 update, 1 delete statement of 2 rows)
        session.execute.side_effect = [MagicMock(rowcount=1), MagicMock(rowcount=2)]
        done = fix.apply_decisions(session, decisions)
        assert done == {fix.UPDATE: 1, fix.DELETE: 2}
        update_stmt, delete_stmt = (c.args[0] for c in session.execute.call_args_list)
        sql = _sql(update_stmt)
        assert sql.startswith("UPDATE signals.sec_filings SET cik=")
        assert "ticker=" in sql and "tickers=" in sql and "url=" in sql
        params = update_stmt.compile(dialect=postgresql.dialect()).params
        assert params["cik"] == AAPL and params["ticker"] == "AAPL"
        assert params["tickers"] == ["AAPL"]
        # the URL is rebuilt with the SUBJECT's CIK (not the filer's)
        assert params["url"] == (
            "https://www.sec.gov/Archives/edgar/data/320193/000108636426000001/"
            "xslSCHEDULE_13G_X02/primary_doc.xml"
        )
        assert _sql(delete_stmt).startswith("DELETE FROM signals.sec_filings")
        session.commit.assert_not_called()  # the caller commits

    def test_keep_decisions_write_nothing(self):
        session = MagicMock()
        d = fix.classify_row(_row(cik=AAPL, ticker="AAPL"), _parties([AAPL]), GROUPS)
        assert fix.apply_decisions(session, [d]) == {fix.UPDATE: 0, fix.DELETE: 0}
        session.execute.assert_not_called()


@contextmanager
def _session_cm(session):
    yield session


class TestMain:
    def test_defaults_are_a_dry_run(self):
        args = fix.parse_args([])
        assert (args.apply, args.tickers) == (False, None)
        assert fix.parse_args(["--apply", "--tickers", "BLK"]).apply is True

    def _run(self, argv, rows, parties):
        read_session, write_session = MagicMock(), MagicMock()
        write_session.execute.return_value = MagicMock(rowcount=1)
        sessions = {True: read_session, False: write_session}
        client = MagicMock()
        client.get_cik.side_effect = lambda t: {"AAPL": AAPL, "BLK": BLK}.get(t)
        with (
            patch.object(fix, "setup_logging"),
            patch.object(fix, "announce_db_target"),
            patch.object(
                fix,
                "repair_session",
                side_effect=lambda read_only: _session_cm(sessions[read_only]),
            ) as rs,
            patch.object(fix, "load_rows", return_value=rows) as load,
            patch.object(fix, "SECClient", return_value=client),
            patch.object(
                fix,
                "header_parties",
                side_effect=lambda _client, cik, acc: parties(cik, acc),
            ),
            patch(
                "trading_signals.collectors._db.active_tickers",
                return_value=["AAPL", "BLK"],
            ),
        ):
            code = fix.main(argv)
        return code, rs, load, write_session, read_session

    def test_dry_run_reads_only_and_never_opens_a_write_session(self, capsys):
        code, rs, load, write_session, read_session = self._run(
            [], [_row(1)], lambda c, a: _parties([AAPL], [BLK])
        )
        assert code == 0
        rs.assert_called_once_with(read_only=True)
        last_sql = str(read_session.execute.call_args_list[0].args[0])
        assert "default_transaction_read_only" in last_sql
        load.assert_called_once_with(read_session, None)
        write_session.execute.assert_not_called()
        out = capsys.readouterr().out
        assert "mode: dry run" in out and "nothing written" in out
        assert "update 1" in out

    def test_apply_writes_in_one_committed_transaction(self, capsys):
        code, rs, _, write_session, _ = self._run(
            ["--apply", "--tickers", "blk"],
            [_row(1), _row(2)],
            lambda c, a: _parties([AAPL] if a.endswith("1") else [DOCEAN], [BLK]),
        )
        assert code == 0
        assert [c.kwargs for c in rs.call_args_list] == [
            {"read_only": True},
            {"read_only": False},
        ]
        write_session.commit.assert_called_once()
        assert "applied: updated 1/1, deleted 1/1" in capsys.readouterr().out

    def test_header_errors_leave_the_rows_alone_and_exit_with_1(self, capsys):
        def parties(cik, accession):
            raise RuntimeError("HTTP 503")

        code, _, _, write_session, _ = self._run(["--apply"], [_row(1)], parties)
        assert code == 1
        write_session.commit.assert_not_called()
        out = capsys.readouterr().out
        assert "header errors (1;" in out and "run the script again" in out

    def test_no_rows_is_a_no_op(self, capsys):
        code, _, _, _, _ = self._run([], [], lambda c, a: _parties([AAPL]))
        assert code == 0 and "nothing to do" in capsys.readouterr().out
