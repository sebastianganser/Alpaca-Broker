"""Tests for the BaseCollector abstract class."""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

import pytest

from trading_signals.collectors.base import BaseCollector
from trading_signals.db.models.collection_log import CollectionLog


class MockCollector(BaseCollector):
    """Concrete test implementation of BaseCollector."""

    name = "test_collector"

    def __init__(self, fetch_data=None, store_result=(10, 8)):
        self._fetch_data = fetch_data or [{"key": "value"}]
        self._store_result = store_result

    def fetch(self, session) -> Any:
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


def _create_mock_get_session():
    """Create a replacement for get_session() that works with BaseCollector.run().

    BaseCollector.run() calls get_session() 4 times as a context manager:
      1. Create log entry + flush → assigns log.id
      2. Fetch + store data (main work session)
      3. Finalize log entry → updates status/records on the log
      4. Re-fetch log for return → expunges log for detached use

    We use a real CollectionLog so that attribute assignments
    (e.g. log.status = 'success') and format strings (e.g. {duration:.1f})
    work correctly. MagicMock breaks f-string formatting.
    """
    # Shared log object across all sessions
    log = CollectionLog(collector_name="pending", started_at=datetime.now())
    log.id = 42

    call_count = [0]

    @contextmanager
    def mock_get_session():
        call_count[0] += 1
        n = call_count[0]
        session = MagicMock()

        if n == 1:
            # Session 1: Create + commit log entry
            def add_side_effect(obj):
                if isinstance(obj, CollectionLog):
                    obj.id = 42
                    log.collector_name = obj.collector_name
                    log.started_at = obj.started_at
            session.add.side_effect = add_side_effect

        elif n == 3:
            # Session 3: Finalize log (set status, records, finished_at)
            session.get.return_value = log

        elif n == 4:
            # Session 4: Re-fetch for return value
            session.get.return_value = log
            session.expunge.return_value = None

        yield session

    return mock_get_session


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
    def test_failed_run_creates_error_log(self, mock_get_session):
        """A failed run should return a log with status='failed'."""
        mock_get_session.side_effect = _create_mock_get_session()

        collector = FailingCollector()
        # BaseCollector catches exceptions and returns log with status='failed'
        log = collector.run()
        assert log.status == "failed"
        assert log.errors is not None
        assert "fetch exploded" in log.errors["error"]

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
