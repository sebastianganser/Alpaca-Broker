"""Tests for the BaseCollector abstract class."""

import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from trading_signals.collectors._db import bulk_insert
from trading_signals.collectors.base import BaseCollector
from trading_signals.db.models.collection_log import CollectionLog
from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import CollectorLogCapture, redact


class MockCollector(BaseCollector):
    """Concrete test implementation of BaseCollector."""

    name = "test_collector"

    def __init__(self, fetch_data=None, store_result=(10, 8), attempts=0, errors=0):
        self._fetch_data = fetch_data if fetch_data is not None else [{"key": "value"}]
        self._store_result = store_result
        self._n_attempts = attempts
        self._n_errors = errors

    def fetch(self, session) -> Any:
        self.record_success(self._n_attempts - self._n_errors)
        self.record_error(self._n_errors)
        return self._fetch_data

    def store(self, session, data: Any) -> tuple[int, int]:
        return self._store_result


class FailingCollector(BaseCollector):
    """Collector that always fails during fetch."""

    name = "failing_collector"

    def fetch(self, session) -> Any:
        raise RuntimeError("fetch exploded")

    def store(self, session, data: Any) -> tuple[int, int]:
        return (0, 0)


def _create_mock_get_session(sessions: list | None = None):
    """Create a replacement for get_session() that works with BaseCollector.run().

    BaseCollector.run() calls get_session() 6 times as a context manager:
      1. Create log entry + flush → assigns log.id
      2. Gap check   3. Fetch   4. Store (separate short transactions)
      5. Finalize log entry → updates status/records on the log
      6. Re-fetch log for return → expunges log for detached use

    We use a real CollectionLog so that attribute assignments
    (e.g. log.status = 'success') and format strings (e.g. {duration:.1f})
    work correctly. MagicMock breaks f-string formatting.
    """
    # Shared log object across all sessions
    log = CollectionLog(collector_name="pending", started_at=datetime.now())
    log.id = 42

    @contextmanager
    def mock_get_session():
        session = MagicMock()

        def add_side_effect(obj):
            if isinstance(obj, CollectionLog):
                obj.id = 42
                log.collector_name = obj.collector_name
                log.started_at = obj.started_at

        session.add.side_effect = add_side_effect
        session.get.return_value = log
        if sessions is not None:
            sessions.append(session)
        yield session

    return mock_get_session


@pytest.fixture(autouse=True)
def _no_alerts():
    with patch("trading_signals.collectors.base.notify_run_status") as m:
        yield m


class TestBaseCollector:
    """Test the template method pattern in BaseCollector."""

    @patch("trading_signals.collectors.base.get_session")
    def test_successful_run_creates_log(self, mock_get_session):
        """A successful run should return a log with status='success'."""
        mock_get_session.side_effect = _create_mock_get_session()

        collector = MockCollector(store_result=(50, 42))
        log = collector.run()

        assert log.collector_name == "test_collector"
        assert log.status == "success"
        assert log.records_fetched == 50
        assert log.records_written == 42
        assert log.started_at is not None
        assert log.finished_at is not None

    @patch("trading_signals.collectors.base.get_session")
    def test_failed_run_creates_error_log(self, mock_get_session, _no_alerts):
        """A failed run should return a log with status='failed'."""
        mock_get_session.side_effect = _create_mock_get_session()

        collector = FailingCollector()
        # BaseCollector catches exceptions and returns log with status='failed'
        log = collector.run()
        assert log.status == "failed"
        assert log.errors is not None
        assert "fetch exploded" in log.errors["error"]
        _no_alerts.assert_called_once()
        assert _no_alerts.call_args[0][:2] == ("failing_collector", "failed")

    @patch("trading_signals.collectors.base.get_session")
    def test_run_calls_methods_in_order(self, mock_get_session):
        """run() should call check_and_repair_gaps, fetch, store in order."""
        mock_get_session.side_effect = _create_mock_get_session()

        call_order = []

        class OrderTracker(BaseCollector):
            name = "order_tracker"

            def check_and_repair_gaps(self, session):
                call_order.append("gaps")
                return None

            def fetch(self, session):
                call_order.append("fetch")
                return []

            def store(self, session, data):
                call_order.append("store")
                return (0, 0)

        collector = OrderTracker()
        collector.run()

        assert call_order == ["gaps", "fetch", "store"]

    @patch("trading_signals.collectors.base.get_session")
    def test_fetch_and_store_use_separate_sessions(self, mock_get_session):
        sessions: list = []
        mock_get_session.side_effect = _create_mock_get_session(sessions)
        seen = {}

        class Tracker(BaseCollector):
            name = "tracker"

            def fetch(self, session):
                seen["fetch"] = session
                return [1]

            def store(self, session, data):
                seen["store"] = session
                return (1, 1)

        Tracker().run()
        assert seen["fetch"] is not seen["store"]
        assert len(sessions) == 6

    @patch("trading_signals.collectors.base.get_session")
    def test_error_share_above_10_percent_is_partial(
        self, mock_get_session, _no_alerts
    ):
        mock_get_session.side_effect = _create_mock_get_session()
        log = MockCollector(attempts=10, errors=2).run()
        assert log.status == "partial"
        assert "2/10 requests failed" in log.notes
        assert _no_alerts.call_args[0][1] == "partial"

    @patch("trading_signals.collectors.base.get_session")
    def test_small_error_share_is_success(self, mock_get_session):
        mock_get_session.side_effect = _create_mock_get_session()
        log = MockCollector(attempts=100, errors=5).run()
        assert log.status == "success"

    @patch("trading_signals.collectors.base.get_session")
    def test_all_requests_failed_is_failed(self, mock_get_session):
        mock_get_session.side_effect = _create_mock_get_session()
        log = MockCollector(attempts=5, errors=5, fetch_data=[]).run()
        assert log.status == "failed"

    @patch("trading_signals.collectors.base.get_session")
    def test_errors_and_nothing_fetched_is_failed(self, mock_get_session):
        mock_get_session.side_effect = _create_mock_get_session()
        log = MockCollector(attempts=20, errors=1, fetch_data=[]).run()
        assert log.status == "failed"

    def test_mark_partial(self):
        c = MockCollector()
        c.mark_partial("feed fallback")
        assert c._compute_status(1) == ("partial", "feed fallback")

    def test_counters_reset_between_runs(self):
        c = MockCollector()
        c.record_error(3)
        c._reset_run_state()
        assert c._compute_status(1) == ("success", None)

    def test_get_active_tickers_releases_transaction(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [("AAPL",), ("MSFT",)]
        assert BaseCollector.get_active_tickers(session) == ["AAPL", "MSFT"]
        session.commit.assert_called_once()


class TestBulkInsert:
    def test_chunks_and_do_nothing(self):
        session = MagicMock()
        session.execute.return_value.rowcount = 2
        rows = [{"ticker": "A", "trade_date": i, "close": 1.0} for i in range(5)]
        written = bulk_insert(
            session, PriceDaily, rows, ["ticker", "trade_date"], chunk=2
        )
        assert session.execute.call_count == 3
        assert written == 6
        sql = str(
            session.execute.call_args_list[0][0][0].compile(
                dialect=postgresql.dialect()
            )
        )
        assert "ON CONFLICT (ticker, trade_date) DO NOTHING" in sql

    def test_dedupes_conflict_keys_and_fills_missing(self):
        session = MagicMock()
        rows = [
            {"ticker": "A", "trade_date": 1, "close": 1.0},
            {"ticker": "A", "trade_date": 1, "close": 2.0, "volume": 5},
        ]
        bulk_insert(
            session, PriceDaily, rows, ["ticker", "trade_date"], update_cols=["close"]
        )
        stmt = session.execute.call_args[0][0]
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert params["close_m0"] == 2.0
        assert "close_m1" not in params

    def test_empty_rows_no_statement(self):
        session = MagicMock()
        assert bulk_insert(session, PriceDaily, [], ["ticker"]) == 0
        session.execute.assert_not_called()


class TestLogCapture:
    def test_redacts_secrets(self):
        logger = logging.getLogger("test.redact")
        with CollectorLogCapture("x") as cap:
            logger.warning("GET https://api.x/v1?apiKey=SECRET123&a=1 failed")
            logger.warning("header Authorization: Bearer abc.def-ghi")
            logger.warning("url ?api_key=FREDKEY")
        msgs = " | ".join(line["msg"] for line in cap.get_lines())
        assert "SECRET123" not in msgs
        assert "abc.def-ghi" not in msgs
        assert "FREDKEY" not in msgs
        assert "apiKey=***" in msgs

    def test_ignores_other_threads(self):
        logger = logging.getLogger("test.threads")
        with CollectorLogCapture("x") as cap:
            t = threading.Thread(target=lambda: logger.warning("from other job"))
            t.start()
            t.join()
            logger.warning("own line")
        msgs = [line["msg"] for line in cap.get_lines()]
        assert msgs == ["own line"]

    def test_redact_helper(self):
        assert redact("token=abc&x=1") == "token=***&x=1"
        assert redact("APCA-API-SECRET-KEY: s3cr3t") == "APCA-API-SECRET-KEY: ***"
