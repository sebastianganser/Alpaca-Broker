"""Tests for the BaseCollector abstract class."""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.base import BaseCollector


class MockCollector(BaseCollector):
    """Concrete test implementation of BaseCollector."""

    name = "test_collector"

    def __init__(self, fetch_data=None, store_result=(10, 8)):
        self._fetch_data = fetch_data or {"key": "value"}
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


class TestBaseCollector:
    """Test the template method pattern in BaseCollector."""

    @patch("trading_signals.collectors.base.get_session")
    def test_successful_run_creates_log(self, mock_get_session):
        """A successful run should create a log entry with status='success'."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(
            return_value=mock_session
        )
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

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
        """A failed run should log the error and re-raise."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(
            return_value=mock_session
        )
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        collector = FailingCollector()

        with pytest.raises(RuntimeError, match="fetch exploded"):
            collector.run()

    @patch("trading_signals.collectors.base.get_session")
    def test_run_calls_methods_in_order(self, mock_get_session):
        """run() should call check_and_repair_gaps, fetch, store in order."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(
            return_value=mock_session
        )
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

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
