"""Tests for InsiderClusterComputer – cluster detection logic."""

import math
from datetime import date
from unittest.mock import MagicMock, patch


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


# ── Review 2026-10: known_date + delete-and-rebuild (C2 / M5) ────────────


class TestKnownDate:
    """known_date = last day a member trade became public."""

    @staticmethod
    def _t(name, txn, filed=None, value=100_000):
        from types import SimpleNamespace

        return SimpleNamespace(
            insider_name=name, transaction_date=txn, filing_date=filed,
            total_value=value,
        )

    def test_known_date_is_max_filing_date(self):
        from trading_signals.derived.insider_clusters import find_clusters

        clusters = find_clusters([
            self._t("A", date(2026, 3, 2), date(2026, 3, 20)),
            self._t("B", date(2026, 3, 5), date(2026, 3, 6)),
        ])
        assert len(clusters) == 1
        assert clusters[0]["cluster_end"] == date(2026, 3, 5)
        assert clusters[0]["known_date"] == date(2026, 3, 20)

    def test_known_date_fallback_lag(self):
        from trading_signals.derived.insider_clusters import (
            FALLBACK_FILING_LAG_DAYS,
            find_clusters,
        )

        clusters = find_clusters([
            self._t("A", date(2026, 3, 2)),
            self._t("B", date(2026, 3, 5)),
        ])
        expected = date(2026, 3, 5).toordinal() + FALLBACK_FILING_LAG_DAYS
        assert clusters[0]["known_date"].toordinal() == expected

    def test_unsorted_input(self):
        from trading_signals.derived.insider_clusters import find_clusters

        clusters = find_clusters([
            self._t("B", date(2026, 3, 5), date(2026, 3, 6)),
            self._t("A", date(2026, 3, 2), date(2026, 3, 3)),
        ])
        assert clusters[0]["cluster_start"] == date(2026, 3, 2)

    def test_acceptance_after_close_is_next_day(self):
        from datetime import UTC, datetime

        from trading_signals.derived.insider_clusters import availability_date

        t = self._t("A", date(2026, 3, 2), date(2026, 3, 4))
        # 21:30 UTC = 16:30 EST → after the close → public on 03-05
        t.acceptance_datetime = datetime(2026, 3, 4, 21, 30, tzinfo=UTC)
        assert availability_date(t) == date(2026, 3, 5)
        # 20:00 UTC = 15:00 EST → before the close → public on 03-04
        t.acceptance_datetime = datetime(2026, 3, 4, 20, 0, tzinfo=UTC)
        assert availability_date(t) == date(2026, 3, 4)

    def test_acceptance_missing_falls_back_to_filing_date(self):
        from trading_signals.derived.insider_clusters import availability_date

        t = self._t("A", date(2026, 3, 2), date(2026, 3, 4))
        t.acceptance_datetime = None
        assert availability_date(t) == date(2026, 3, 4)


class TestRebuildAnchor:
    def test_since_date_clamped_to_retention(self):
        """compute_new never looks before data_start_date()."""
        from trading_signals.derived import insider_clusters as ic

        session = MagicMock()
        session.execute.return_value.all.return_value = [("AAA",)]
        computer = InsiderClusterComputer(session)
        with (
            patch.object(ic, "data_start_date", return_value=date(2021, 1, 1)),
            patch.object(computer, "_compute_for_ticker", return_value=2) as cft,
        ):
            assert computer.compute_new(date(2000, 1, 1)) == 2
        cft.assert_called_once_with("AAA", date(2021, 1, 1))

    def test_rebuild_window_starts_at_overlapping_cluster(self):
        """Anchor = earliest start of a stored cluster overlapping since_date."""
        session = MagicMock()
        session.execute.return_value.scalar.return_value = date(2026, 2, 20)
        anchor = InsiderClusterComputer(session)._rebuild_anchor(
            "AAA", date(2026, 3, 1)
        )
        assert anchor == date(2026, 2, 20)

    def test_rebuild_deletes_before_insert(self):
        """Stale clusters in the window are deleted even if nothing is rebuilt."""
        session = MagicMock()
        session.execute.return_value.scalar.return_value = None
        session.execute.return_value.scalars.return_value.all.return_value = []
        written = InsiderClusterComputer(session)._compute_for_ticker(
            "AAA", date(2026, 3, 1)
        )
        assert written == 0
        kinds = [c[0][0].__visit_name__ for c in session.execute.call_args_list]
        assert "delete" in kinds
