"""Tests for InsiderClusterComputer – cluster detection logic."""

import math
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.derived.insider_clusters import (
    InsiderClusterComputer,
    CLUSTER_WINDOW_DAYS,
    MIN_INSIDERS,
)


class TestClusterConstants:
    """Test cluster configuration."""

    def test_window_is_21_days(self):
        assert CLUSTER_WINDOW_DAYS == 21

    def test_minimum_two_insiders(self):
        assert MIN_INSIDERS == 2


class TestFindClusters:
    """Test cluster detection logic with mock InsiderTrade objects."""

    def _make_trade(
        self, insider_name: str, transaction_date: date, total_value: float = 100000
    ) -> MagicMock:
        """Create a mock InsiderTrade for testing."""
        trade = MagicMock()
        trade.insider_name = insider_name
        trade.transaction_date = transaction_date
        trade.total_value = total_value
        trade.transaction_type = "P"
        trade.is_derivative = False
        trade.ticker = "AAPL"
        return trade

    def test_two_insiders_same_week_is_cluster(self):
        """Two different insiders buying within the window → cluster."""
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 4, 1)),
            self._make_trade("Bob CFO", date(2026, 4, 5)),
        ]

        clusters = computer._find_clusters(purchases)

        assert len(clusters) == 1
        assert clusters[0]["n_insiders"] == 2
        assert clusters[0]["n_buys"] == 2
        assert clusters[0]["cluster_start"] == date(2026, 4, 1)
        assert clusters[0]["cluster_end"] == date(2026, 4, 5)

    def test_same_insider_twice_is_not_cluster(self):
        """Same insider buying twice → not a cluster (need distinct insiders)."""
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 4, 1)),
            self._make_trade("Alice CEO", date(2026, 4, 5)),
        ]

        clusters = computer._find_clusters(purchases)

        assert len(clusters) == 0

    def test_three_insiders_strong_cluster(self):
        """Three insiders → stronger cluster."""
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 4, 1), 50000),
            self._make_trade("Bob CFO", date(2026, 4, 3), 75000),
            self._make_trade("Carol CTO", date(2026, 4, 10), 100000),
        ]

        clusters = computer._find_clusters(purchases)

        assert len(clusters) == 1
        assert clusters[0]["n_insiders"] == 3
        assert clusters[0]["n_buys"] == 3

    def test_beyond_window_no_cluster(self):
        """Insiders buying >21 days apart → no cluster."""
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 3, 1)),
            self._make_trade("Bob CFO", date(2026, 4, 1)),  # 31 days later
        ]

        clusters = computer._find_clusters(purchases)

        assert len(clusters) == 0

    def test_at_window_boundary(self):
        """Exactly 21 days apart → should be a cluster."""
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 4, 1)),
            self._make_trade("Bob CFO", date(2026, 4, 22)),  # 21 days later
        ]

        clusters = computer._find_clusters(purchases)

        assert len(clusters) == 1

    def test_empty_purchases(self):
        computer = InsiderClusterComputer(session=MagicMock())
        assert computer._find_clusters([]) == []

    def test_single_purchase(self):
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 4, 1)),
        ]
        assert computer._find_clusters(purchases) == []


class TestClusterScore:
    """Test cluster score calculation."""

    def test_score_formula(self):
        """Score should be n_insiders * log(1 + total_value / 10000)."""
        computer = InsiderClusterComputer(session=MagicMock())
        purchases = [
            self._make_trade("Alice CEO", date(2026, 4, 1), 100000),
            self._make_trade("Bob CFO", date(2026, 4, 5), 200000),
        ]

        clusters = computer._find_clusters(purchases)

        expected_score = 2 * math.log(1 + 300000 / 10000)
        assert len(clusters) == 1
        assert abs(clusters[0]["score"] - expected_score) < 0.01

    def _make_trade(
        self, insider_name: str, transaction_date: date, total_value: float = 100000
    ) -> MagicMock:
        trade = MagicMock()
        trade.insider_name = insider_name
        trade.transaction_date = transaction_date
        trade.total_value = total_value
        return trade
