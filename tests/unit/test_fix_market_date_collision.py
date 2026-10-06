"""Tests for scripts/repair/fix_market_date_collision.py (pure helpers)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair" / "fix_market_date_collision.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("fix_market_date_collision", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_parse_dates(mod):
    assert mod.parse_dates("2026-10-02, 2026-10-05,") == [date(2026, 10, 2), date(2026, 10, 5)]
    assert mod.parse_dates("") == []


def test_labels_are_consistent(mod):
    # The stale label must be the session right after the true one.
    from trading_signals.utils import market_calendar

    assert market_calendar.next_trading_day(mod.TRUE_SESSION) == mod.STALE_LABEL


def test_estimates_sql_restricted_to_old_rows(mod):
    for sql in (mod.ESTIMATES_STATS, mod.ESTIMATES_DELETE, mod.ESTIMATES_UPDATE):
        assert "fetched_at < CAST(:fetched_before AS timestamp)" in sql


def test_abort_when_session_moved_on(mod, monkeypatch):
    from contextlib import contextmanager

    from trading_signals.utils import market_calendar

    monkeypatch.setattr(market_calendar, "last_completed_session", lambda: date(2026, 10, 6))
    monkeypatch.setattr(mod, "print_stats", lambda s: (0, 0))

    @contextmanager
    def fake_session():
        yield object()

    import trading_signals.db.session as db_session

    monkeypatch.setattr(db_session, "get_session", fake_session)
    called = []
    monkeypatch.setattr(mod, "relabel", lambda s: called.append("relabel"))
    monkeypatch.setattr(mod, "rerun_collectors", lambda: called.append("rerun"))
    assert mod.main(["--apply", "--rerun"]) == 1
    assert called == []
