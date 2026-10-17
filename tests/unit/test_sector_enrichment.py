"""Tests for the sector enrichment's learned ETF filter (no DB, mocked session).

The filter blacklists + deactivates non-equity tickers, but never a current
index member: BF.B (S&P 500) was blacklisted as MUTUALFUND on 2026-04-15 after
a lookup with the wrong Yahoo symbol.
"""

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from trading_signals.scheduler import sector_enrichment as se

LOGGER = "trading_signals.scheduler.sector_enrichment"
ADD = "trading_signals.universe.blacklist.add_to_blacklist"
SESSION = "trading_signals.db.session.get_session"


class FakeSession:
    """Answers the open-membership query from ``members``; records everything."""

    def __init__(self, members=()):
        self.members = set(members)
        self.statements = []

    def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if isinstance(stmt, Select):
            wanted = stmt.compile().params["ticker_1"]
            return MagicMock(
                all=MagicMock(return_value=[(t,) for t in wanted if t in self.members])
            )
        return MagicMock()

    @property
    def selects(self):
        return [s for s in self.statements if isinstance(s, Select)]

    @property
    def writes(self):
        """``(table, values)`` of every UPDATE."""
        found = []
        for stmt in self.statements:
            if isinstance(stmt, Select):
                continue
            compiled = stmt.compile(dialect=postgresql.dialect())
            found.append((stmt.table.name, compiled.params))
        return found


def record(ticker, quote_type="EQUITY", sector=None, industry=None):
    return {
        "ticker": ticker,
        "quote_type": quote_type,
        "sector": sector,
        "industry": industry,
    }


class TestIndexMemberGuard:
    def test_index_member_is_not_blacklisted_but_gets_its_sector(self, caplog):
        session = FakeSession(members={"BF.B"})
        records = [record("BF.B", "MUTUALFUND", "Consumer Defensive", "Beverages")]
        with patch(ADD) as add, caplog.at_level(logging.WARNING, logger=LOGGER):
            result = se.apply_sector_records(session, records, "manual_enrichment")
        add.assert_not_called()
        assert result.deactivated == [] and result.enriched == 1
        (table, values) = session.writes[0]
        assert table == "universe" and "is_active" not in values
        assert values["sector"] == "Consumer Defensive"
        assert values["ticker_1"] == "BF.B"
        assert (
            "[manual_enrichment] quoteType=MUTUALFUND for index member BF.B"
            " - not blacklisted"
        ) in caplog.messages

    def test_index_member_without_sector_data_is_left_untouched(self):
        session = FakeSession(members={"BF.B"})
        with patch(ADD) as add:
            result = se.apply_sector_records(
                session, [record("BF.B", "MUTUALFUND")], "index_sync"
            )
        add.assert_not_called()
        assert result.deactivated == [] and result.enriched == 0
        assert session.writes == []  # no deactivation, no empty overwrite

    def test_non_member_is_still_blacklisted_and_deactivated(self, caplog):
        session = FakeSession(members={"BF.B"})
        with patch(ADD) as add, caplog.at_level(logging.WARNING, logger=LOGGER):
            result = se.apply_sector_records(
                session, [record("XYZF", "ETF")], "index_sync"
            )
        add.assert_called_once_with(
            session, "XYZF", quote_type="ETF", source="index_sync"
        )
        assert result.deactivated == ["XYZF"] and result.enriched == 0
        ((table, values),) = session.writes
        assert table == "universe" and values["is_active"] is False
        assert values["ticker_1"] == "XYZF"
        assert "[index_sync] blacklisted + deactivated XYZF: quoteType=ETF" in (
            caplog.messages
        )

    def test_mixed_batch(self):
        session = FakeSession(members={"BF.B", "AAPL"})
        records = [
            record("AAPL", "EQUITY", "Technology", "Consumer Electronics"),
            record("BF.B", "MUTUALFUND", "Consumer Defensive", "Beverages"),
            record("XYZF", "ETF", "Financial", None),
            record("MMF", "mutualfund"),  # case-insensitive, not a member
        ]
        with patch(ADD) as add:
            result = se.apply_sector_records(session, records, "index_sync")
        assert [c.args[1] for c in add.call_args_list] == ["XYZF", "MMF"]
        assert result.deactivated == ["XYZF", "MMF"]
        assert result.enriched == 2  # AAPL and BF.B; the deactivated ones skip it
        sectors = {
            v["ticker_1"]: v.get("sector") for t, v in session.writes if "sector" in v
        }
        assert sectors == {"AAPL": "Technology", "BF.B": "Consumer Defensive"}

    def test_equity_quote_type_is_case_insensitive_and_never_blacklisted(self):
        session = FakeSession()
        with patch(ADD) as add:
            result = se.apply_sector_records(
                session, [record("AAPL", "equity", "Technology")], "index_sync"
            )
        add.assert_not_called()
        assert result.deactivated == [] and result.enriched == 1

    def test_missing_quote_type_is_not_non_equity(self):
        session = FakeSession()
        with patch(ADD) as add:
            result = se.apply_sector_records(
                session, [record("AAPL", None, "Technology")], "index_sync"
            )
        add.assert_not_called()
        assert session.selects == []
        assert result.enriched == 1


class TestMembershipQuery:
    def test_one_query_per_call_not_per_record(self):
        session = FakeSession(members={"A", "B"})
        records = [record(t, "MUTUALFUND") for t in ("A", "B", "C", "D")]
        records += [record("AAPL", "EQUITY", "Technology")]
        with patch(ADD):
            se.apply_sector_records(session, records, "index_sync")
        (select,) = session.selects
        # only the suspects are asked for, never the equities
        assert select.compile().params["ticker_1"] == ["A", "B", "C", "D"]

    def test_query_looks_at_open_intervals_of_any_index(self):
        session = FakeSession()
        with patch(ADD):
            se.apply_sector_records(session, [record("X", "ETF")], "index_sync")
        (select,) = session.selects
        sql = str(select.compile(dialect=postgresql.dialect()))
        assert "signals.index_membership" in sql
        assert "valid_to IS NULL" in sql
        assert "index_name" not in sql  # S&P 500 and Nasdaq 100 alike

    def test_no_query_without_a_non_equity_record(self):
        session = FakeSession()
        with patch(ADD):
            se.apply_sector_records(
                session, [record("AAPL", "EQUITY", "Technology")], "index_sync"
            )
            se.apply_sector_records(session, [], "index_sync")
        assert session.selects == []

    def test_open_index_members_helper(self):
        session = FakeSession(members={"BF.B"})
        assert se.open_index_members(session, ["BF.B", "SPYX"]) == {"BF.B"}
        assert se.open_index_members(session, []) == set()
        assert len(session.selects) == 1  # the empty call asked nothing


class TestEnrichSectors:
    def test_end_to_end_a_wrong_lookup_does_not_blacklist_an_index_member(self):
        session = FakeSession(members={"BF.B"})

        @contextmanager
        def fake_get_session():
            yield session

        client = MagicMock()
        client.fetch_sector_info.return_value = [
            record("BF.B", "MUTUALFUND", "Consumer Defensive", "Beverages")
        ]
        with (
            patch(SESSION, side_effect=fake_get_session),
            patch(ADD) as add,
        ):
            result = se.enrich_sectors(
                ["BF.B"], "manual_enrichment", client=client, batch_pause=0
            )
        add.assert_not_called()
        assert result.checked == 1 and result.failed == 0
        assert result.deactivated == [] and result.enriched == 1
