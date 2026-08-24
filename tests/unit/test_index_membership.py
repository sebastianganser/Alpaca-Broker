"""Tests for index membership model, interval generation, and point-in-time queries."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.db.models.index_membership import IndexMembership
from trading_signals.universe.wikipedia_index_history import (
    IndexChange,
    generate_intervals_from_changes,
)


class TestIndexMembershipModel:
    """Test the IndexMembership ORM model."""

    def test_repr_active_member(self):
        m = IndexMembership(
            ticker="AAPL",
            index_name="sp500",
            valid_from=date(2021, 1, 1),
            valid_to=None,
            source="wikipedia",
        )
        assert "AAPL" in repr(m)
        assert "sp500" in repr(m)
        assert "present" in repr(m)

    def test_repr_removed_member(self):
        m = IndexMembership(
            ticker="XRX",
            index_name="sp500",
            valid_from=date(2021, 1, 1),
            valid_to=date(2023, 6, 15),
            source="wikipedia",
        )
        assert "XRX" in repr(m)
        assert "2023-06-15" in repr(m)


class TestIntervalGeneration:
    """Test generate_intervals_from_changes()."""

    def test_simple_add_and_remove(self):
        """A ticker added and later removed creates one closed interval."""
        changes = [
            IndexChange(
                change_date=date(2022, 1, 15),
                ticker="TEST",
                index_name="sp500",
                action="added",
            ),
            IndexChange(
                change_date=date(2023, 6, 1),
                ticker="TEST",
                index_name="sp500",
                action="removed",
                reason="Delisted",
            ),
        ]
        intervals = generate_intervals_from_changes(
            changes, current_members={}, data_start=date(2021, 1, 1)
        )
        assert len(intervals) == 1
        iv = intervals[0]
        assert iv["ticker"] == "TEST"
        assert iv["valid_from"] == date(2022, 1, 15)
        assert iv["valid_to"] == date(2023, 6, 1)
        assert iv["reason"] == "Delisted"

    def test_add_still_active(self):
        """A ticker added but never removed has valid_to=None."""
        changes = [
            IndexChange(
                change_date=date(2022, 3, 1),
                ticker="STAY",
                index_name="sp500",
                action="added",
            ),
        ]
        intervals = generate_intervals_from_changes(
            changes, current_members={}, data_start=date(2021, 1, 1)
        )
        assert len(intervals) == 1
        assert intervals[0]["valid_to"] is None

    def test_removed_without_prior_add(self):
        """A removal with no prior add → interval from data_start."""
        changes = [
            IndexChange(
                change_date=date(2022, 6, 1),
                ticker="OLD",
                index_name="sp500",
                action="removed",
            ),
        ]
        intervals = generate_intervals_from_changes(
            changes, current_members={}, data_start=date(2021, 1, 1)
        )
        assert len(intervals) == 1
        iv = intervals[0]
        assert iv["valid_from"] == date(2021, 1, 1)
        assert iv["valid_to"] == date(2022, 6, 1)

    def test_current_members_not_in_changes(self):
        """Current members with no change history get interval from data_start."""
        intervals = generate_intervals_from_changes(
            changes=[],
            current_members={"sp500": {"AAPL", "MSFT"}},
            data_start=date(2021, 1, 1),
        )
        assert len(intervals) == 2
        tickers = {iv["ticker"] for iv in intervals}
        assert tickers == {"AAPL", "MSFT"}
        for iv in intervals:
            assert iv["valid_from"] == date(2021, 1, 1)
            assert iv["valid_to"] is None
            assert iv["source"] == "initial_seed"

    def test_add_remove_readd(self):
        """A ticker added, removed, and re-added creates two intervals."""
        changes = [
            IndexChange(
                change_date=date(2021, 3, 1),
                ticker="BACK",
                index_name="sp500",
                action="added",
            ),
            IndexChange(
                change_date=date(2022, 6, 1),
                ticker="BACK",
                index_name="sp500",
                action="removed",
            ),
            IndexChange(
                change_date=date(2023, 9, 1),
                ticker="BACK",
                index_name="sp500",
                action="added",
            ),
        ]
        intervals = generate_intervals_from_changes(
            changes, current_members={}, data_start=date(2021, 1, 1)
        )
        assert len(intervals) == 2
        # First interval: closed
        closed = [iv for iv in intervals if iv["valid_to"] is not None]
        assert len(closed) == 1
        assert closed[0]["valid_from"] == date(2021, 3, 1)
        assert closed[0]["valid_to"] == date(2022, 6, 1)
        # Second interval: open
        open_iv = [iv for iv in intervals if iv["valid_to"] is None]
        assert len(open_iv) == 1
        assert open_iv[0]["valid_from"] == date(2023, 9, 1)

    def test_replaced_by_tracked(self):
        """Replacement ticker info is preserved on removal."""
        changes = [
            IndexChange(
                change_date=date(2022, 1, 1),
                ticker="OLD",
                index_name="sp500",
                action="removed",
                replaced_by="NEW",
            ),
        ]
        intervals = generate_intervals_from_changes(
            changes, current_members={}, data_start=date(2021, 1, 1)
        )
        assert intervals[0]["replaced_by"] == "NEW"

    def test_multiple_indexes(self):
        """Changes across different indexes are tracked independently."""
        changes = [
            IndexChange(
                change_date=date(2022, 1, 1),
                ticker="DUAL",
                index_name="sp500",
                action="added",
            ),
            IndexChange(
                change_date=date(2022, 6, 1),
                ticker="DUAL",
                index_name="nasdaq100",
                action="added",
            ),
        ]
        intervals = generate_intervals_from_changes(
            changes, current_members={}, data_start=date(2021, 1, 1)
        )
        assert len(intervals) == 2
        index_names = {iv["index_name"] for iv in intervals}
        assert index_names == {"sp500", "nasdaq100"}


class TestWikipediaParser:
    """Test Wikipedia parser date parsing and ticker extraction."""

    def test_parse_date_formats(self):
        from trading_signals.universe.wikipedia_index_history import (
            WikipediaIndexHistoryParser,
        )
        p = WikipediaIndexHistoryParser

        assert p._parse_date("January 15, 2024") == date(2024, 1, 15)
        assert p._parse_date("Jan 15, 2024") == date(2024, 1, 15)
        assert p._parse_date("2024-01-15") == date(2024, 1, 15)
        assert p._parse_date("01/15/2024") == date(2024, 1, 15)
        assert p._parse_date("") is None
        assert p._parse_date("nan") is None
        assert p._parse_date("invalid") is None

    def test_extract_tickers_simple(self):
        from trading_signals.universe.wikipedia_index_history import (
            WikipediaIndexHistoryParser,
        )
        parser = WikipediaIndexHistoryParser()

        assert parser._extract_tickers_from_cell("AAPL") == ["AAPL"]
        assert parser._extract_tickers_from_cell("AAPL, MSFT") == ["AAPL", "MSFT"]
        assert parser._extract_tickers_from_cell("AAPL (Apple Inc.)") == ["AAPL"]
        assert parser._extract_tickers_from_cell("") == []
        assert parser._extract_tickers_from_cell("nan") == []
