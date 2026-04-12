"""Tests for ARKDeltaComputer."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.derived.ark_deltas import ARKDeltaComputer


class MockHolding:
    """Simple mock for ARKHolding ORM objects."""

    def __init__(self, ticker, shares, weight_pct):
        self.ticker = ticker
        self.shares = shares
        self.weight_pct = weight_pct


class TestClassification:
    """Test the _classify static method."""

    def test_new_position(self):
        curr = MockHolding("TSLA", 1000, 5.0)
        delta_type, shares_delta, weight_delta = ARKDeltaComputer._classify(curr, None)
        assert delta_type == "new_position"
        assert shares_delta is None

    def test_closed_position(self):
        prev = MockHolding("TSLA", 1000, 5.0)
        delta_type, shares_delta, weight_delta = ARKDeltaComputer._classify(None, prev)
        assert delta_type == "closed"
        assert shares_delta is None

    def test_increased(self):
        curr = MockHolding("TSLA", 1500, 6.0)
        prev = MockHolding("TSLA", 1000, 5.0)
        delta_type, shares_delta, weight_delta = ARKDeltaComputer._classify(curr, prev)
        assert delta_type == "increased"
        assert shares_delta == 500
        assert weight_delta == pytest.approx(1.0)

    def test_decreased(self):
        curr = MockHolding("TSLA", 800, 4.0)
        prev = MockHolding("TSLA", 1000, 5.0)
        delta_type, shares_delta, weight_delta = ARKDeltaComputer._classify(curr, prev)
        assert delta_type == "decreased"
        assert shares_delta == -200
        assert weight_delta == pytest.approx(-1.0)

    def test_unchanged(self):
        curr = MockHolding("TSLA", 1000, 5.0)
        prev = MockHolding("TSLA", 1000, 5.0)
        delta_type, shares_delta, weight_delta = ARKDeltaComputer._classify(curr, prev)
        assert delta_type == "unchanged"
        assert shares_delta == 0

    def test_none_shares_treated_as_zero(self):
        """Positions with None shares should be treated as 0."""
        curr = MockHolding("TSLA", None, None)
        prev = MockHolding("TSLA", None, None)
        delta_type, shares_delta, weight_delta = ARKDeltaComputer._classify(curr, prev)
        assert delta_type == "unchanged"
        assert shares_delta == 0


class TestComputeForDate:
    """Test the compute_for_date method."""

    def test_no_previous_snapshot_returns_zero(self):
        """If there's no prior snapshot, no deltas can be computed."""
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        computer = ARKDeltaComputer(session)
        count = computer.compute_for_date(date(2026, 4, 10), "ARKK")
        assert count == 0

    def test_compute_with_empty_current(self):
        """If current holdings are empty, should return 0."""
        session = MagicMock()
        # First call: previous date exists
        # Second call: no current holdings
        session.execute.return_value.scalar_one_or_none.return_value = date(2026, 4, 9)
        session.execute.return_value.scalars.return_value.all.return_value = []

        computer = ARKDeltaComputer(session)
        count = computer.compute_for_date(date(2026, 4, 10), "ARKK")
        assert count == 0
