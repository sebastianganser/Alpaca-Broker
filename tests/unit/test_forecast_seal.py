"""The forecast seal (derived/forecast_seal.py, job ``forecast_seal``; no DB).

``docs/FORECASTS.md`` section 2: a ``forecasts.yaml`` counts as on time only if
its SHA-256 was sealed before the open (09:30 New York) of the session after the
pack day.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from trading_signals.derived import forecast_seal as seal
from trading_signals.derived.forecasts import FORECASTS_FILE, SqlForecastStore
from trading_signals.scheduler import jobs
from trading_signals.utils import job_status

#: Friday pack day 2026-10-16 -> the open is Monday 2026-10-19 09:30 EDT = 13:30 UTC.
PACK_DAY = date(2026, 10, 16)
OPEN = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
AT_0900 = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
AT_0925 = datetime(2026, 10, 19, 13, 25, tzinfo=UTC)


class MemorySealStore:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def add_seal(self, session_date, file_path, file_sha256, file_size, sealed_at):
        if any(r[0] == session_date and r[2] == file_sha256 for r in self.rows):
            return False
        self.rows.append((session_date, file_path, file_sha256, file_size, sealed_at))
        return True


def _folder(root: Path, content: bytes | None) -> Path:
    d = root / PACK_DAY.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    if content is not None:
        (d / FORECASTS_FILE).write_bytes(content)
    return d


# ── which pack a run covers ───────────────────────────────────────────────


def test_a_monday_run_covers_the_friday_pack_and_the_monday_open():
    pack_day, deadline = seal.seal_target(AT_0900)
    assert pack_day == PACK_DAY and deadline == OPEN


def test_a_midweek_run_covers_the_previous_session():
    tue = datetime(2026, 10, 20, 13, 15, tzinfo=UTC)  # 09:15 New York
    pack_day, deadline = seal.seal_target(tue)
    assert pack_day == date(2026, 10, 19)
    assert deadline == datetime(2026, 10, 20, 13, 30, tzinfo=UTC)


def test_winter_time_moves_the_open_in_utc_not_in_new_york_time():
    # Wednesday 2026-12-02 09:00 EST = 14:00 UTC; the open is 14:30 UTC
    pack_day, deadline = seal.seal_target(datetime(2026, 12, 2, 14, 0, tzinfo=UTC))
    assert pack_day == date(2026, 12, 1)
    assert deadline == datetime(2026, 12, 2, 14, 30, tzinfo=UTC)


def test_after_a_holiday_the_pack_is_the_last_session_before_it():
    # Thursday 2026-11-26 is Thanksgiving: no session, no seal
    assert seal.seal_target(datetime(2026, 11, 26, 14, 0, tzinfo=UTC)) is None
    # Friday 2026-11-27 (09:00 EST = 14:00 UTC): the pack is Wednesday the 25th
    pack_day, deadline = seal.seal_target(datetime(2026, 11, 27, 14, 0, tzinfo=UTC))
    assert pack_day == date(2026, 11, 25)
    assert deadline == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)


def test_the_new_york_date_decides_not_the_utc_date():
    # 02:30 UTC on Saturday is still Friday evening in New York: a session,
    # but the run is far past the open of that day
    late = datetime(2026, 10, 17, 2, 30, tzinfo=UTC)
    result = seal.seal_forecast_file(MemorySealStore(), Path("."), late)
    assert result.status == seal.TOO_LATE
    # the weekend itself has no session
    assert seal.seal_target(datetime(2026, 10, 17, 14, 0, tzinfo=UTC)) is None
    assert seal.seal_target(datetime(2026, 10, 18, 14, 0, tzinfo=UTC)) is None


# ── sealing ───────────────────────────────────────────────────────────────


def test_a_run_seals_hash_size_and_time_of_the_file(tmp_path: Path):
    content = b"schema: forecasts/v1\nforecasts: []\n"
    _folder(tmp_path, content)
    store = MemorySealStore()
    res = seal.seal_forecast_file(store, tmp_path, AT_0900)
    sha = hashlib.sha256(content).hexdigest()
    assert (res.status, res.session_date, res.sha256, res.size) == (
        seal.SEALED, PACK_DAY, sha, len(content)
    )
    ((day, path, stored_sha, size, at),) = store.rows
    assert (day, stored_sha, size, at) == (PACK_DAY, sha, len(content), AT_0900)
    assert path.endswith(FORECASTS_FILE) and PACK_DAY.isoformat() in path
    assert f"sha256={sha[:12]}" in res.notes() and "session=2026-10-16" in res.notes()


def test_the_same_content_is_sealed_once_and_keeps_its_first_time(tmp_path: Path):
    _folder(tmp_path, b"a: 1\n")
    store = MemorySealStore()
    first = seal.seal_forecast_file(store, tmp_path, AT_0900)
    again = seal.seal_forecast_file(store, tmp_path, AT_0925)
    assert first.status == seal.SEALED and again.status == seal.UNCHANGED
    assert len(store.rows) == 1 and store.rows[0][4] == AT_0900


def test_an_edit_between_two_runs_is_sealed_as_new_content(tmp_path: Path):
    d = _folder(tmp_path, b"a: 1\n")
    store = MemorySealStore()
    seal.seal_forecast_file(store, tmp_path, AT_0900)
    (d / FORECASTS_FILE).write_bytes(b"a: 2\n")
    res = seal.seal_forecast_file(store, tmp_path, AT_0925)
    assert res.status == seal.SEALED
    assert [r[2] for r in store.rows] == [
        hashlib.sha256(b"a: 1\n").hexdigest(),
        hashlib.sha256(b"a: 2\n").hexdigest(),
    ]
    assert [r[4] for r in store.rows] == [AT_0900, AT_0925]


def test_no_file_no_session_and_no_folder_seal_nothing(tmp_path: Path):
    store = MemorySealStore()
    assert seal.seal_forecast_file(store, tmp_path, AT_0900).status == seal.NO_FILE
    _folder(tmp_path, None)  # the pack exists, nobody wrote forecasts.yaml
    assert seal.seal_forecast_file(store, tmp_path, AT_0900).status == seal.NO_FILE
    sunday = datetime(2026, 10, 18, 13, 0, tzinfo=UTC)
    assert seal.seal_forecast_file(store, tmp_path, sunday).status == seal.NO_SESSION
    assert store.rows == []


def test_nothing_is_sealed_at_or_after_the_open(tmp_path: Path):
    _folder(tmp_path, b"a: 1\n")
    store = MemorySealStore()
    for late in (OPEN, datetime(2026, 10, 19, 13, 40, tzinfo=UTC)):
        res = seal.seal_forecast_file(store, tmp_path, late)
        assert res.status == seal.TOO_LATE and res.session_date == PACK_DAY
    # one second before the open is still in time
    just = datetime(2026, 10, 19, 13, 29, 59, tzinfo=UTC)
    assert seal.seal_forecast_file(store, tmp_path, just).status == seal.SEALED
    assert len(store.rows) == 1


# ── database side ─────────────────────────────────────────────────────────


def test_sql_seal_store_never_overwrites_the_first_seal():
    session = MagicMock()
    session.execute.return_value.rowcount = 1
    store = seal.SqlSealStore(session)
    assert store.add_seal(PACK_DAY, "/x/forecasts.yaml", "a" * 64, 10, AT_0900) is True
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert sql.startswith("INSERT INTO signals.forecast_file_seals")
    assert "ON CONFLICT ON CONSTRAINT" in sql and "DO NOTHING" in sql
    assert "uq_forecast_file_seals_session_date_file_sha256" in sql
    session.execute.return_value.rowcount = 0
    assert store.add_seal(PACK_DAY, "/x/forecasts.yaml", "a" * 64, 10, AT_0925) is False


def test_the_ingest_reads_the_earliest_seal_of_a_content():
    session = MagicMock()
    session.execute.return_value.all.return_value = [("a" * 64, AT_0900)]
    times = SqlForecastStore(session).seal_times(PACK_DAY)
    assert times == {"a" * 64: AT_0900}
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "min(signals.forecast_file_seals.sealed_at)" in sql
    assert "GROUP BY signals.forecast_file_seals.file_sha256" in sql


def test_each_review_is_stored_in_its_own_savepoint():
    session = MagicMock()
    session.add.side_effect = lambda row: setattr(row, "id", 7)
    from trading_signals.derived.forecasts import ReviewRecord

    review = ReviewRecord(
        PACK_DAY, "/x", "b" * 64, AT_0900, None, None, None, False, "rejected", 0, 0, []
    )
    assert SqlForecastStore(session).add_review(review, []) == 7
    session.begin_nested.assert_called_once()
    # a failing flush propagates (the ingest records a rejected review instead)
    # and the savepoint context manager sees the exception to roll back to it
    session.reset_mock()
    cm = session.begin_nested.return_value
    cm.__exit__.return_value = False
    session.flush.side_effect = RuntimeError("NUL")
    try:
        SqlForecastStore(session).add_review(review, [])
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("the error must propagate")
    assert cm.__exit__.call_args.args[0] is RuntimeError


# ── the scheduler job ─────────────────────────────────────────────────────


@contextmanager
def _fake_session(session):
    yield session


def _run_body(status: str, sha: str | None = None):
    result = seal.SealResult(status, PACK_DAY, sha, 12 if sha else None)
    settings = SimpleNamespace(CONTEXT_PACK_PATH="/packs")
    with (
        patch("trading_signals.config.get_settings", return_value=settings),
        patch(
            "trading_signals.db.session.get_session",
            lambda: _fake_session(MagicMock()),
        ),
        patch.object(seal, "seal_forecast_file", return_value=result) as run,
    ):
        out = jobs._forecast_seal_body()
    return out, run


def test_the_job_reports_a_seal_as_success():
    out, run = _run_body(seal.SEALED, "c" * 64)
    assert out.status == job_status.SUCCESS and out.records_written == 1
    assert "status=sealed" in out.notes and "session=2026-10-16" in out.notes
    assert str(run.call_args.args[1]) == str(Path("/packs"))
    out, _ = _run_body(seal.UNCHANGED, "c" * 64)
    assert out.status == job_status.SUCCESS and out.records_written == 0


def test_the_job_skips_when_there_is_nothing_to_seal():
    for status in (seal.NO_FILE, seal.NO_SESSION):
        out, _ = _run_body(status)
        assert out.status == job_status.SKIPPED, status


def test_a_run_that_missed_the_open_is_partial_so_it_alerts():
    out, _ = _run_body(seal.TOO_LATE)
    assert out.status == job_status.PARTIAL and "status=too_late" in out.notes


def test_run_forecast_seal_is_logged_under_its_own_name():
    with patch.object(jobs, "run_logged_job") as logged:
        jobs.run_forecast_seal()
    assert logged.call_args.args == (
        "forecast_seal",
        "forecast_seal",
        jobs._forecast_seal_body,
    )
