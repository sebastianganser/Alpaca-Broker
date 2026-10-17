"""Tests for NewTickerOnboarder (M5: never commit the caller's session).

Also covers the learned ETF filter of the onboarder (``_verify_equity_type``,
``_enrich_sector``): it never blacklists a current index member -- BF.B was
blacklisted as MUTUALFUND on 2026-04-15 after a lookup with the wrong Yahoo
symbol. No DB: sessions are fakes, yfinance and the blacklist are patched.
"""

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from trading_signals.universe.onboarder import NewTickerOnboarder

MOD = "trading_signals.universe.onboarder"
ADD = "trading_signals.universe.blacklist.add_to_blacklist"
GET_SESSION = "trading_signals.db.session.get_session"


def _own_sessions(existing):
    sessions = []

    @contextmanager
    def fake_get_session():
        s = MagicMock()
        s.execute.return_value.all.return_value = [(t,) for t in existing]
        sessions.append(s)
        yield s

    return fake_get_session, sessions


class TestOnboarder:
    def test_uses_own_session_and_never_commits_callers(self):
        caller = MagicMock()
        fake, sessions = _own_sessions(existing=["AAPL"])
        asset = MagicMock(tradable=True, exchange="NYSE")
        asset.name = "New Corp"

        with (
            patch("trading_signals.db.session.get_session", side_effect=fake),
            patch(
                "trading_signals.universe.blacklist.filter_blacklisted",
                side_effect=lambda s, t: (t, set()),
            ),
            patch(f"{MOD}.AlpacaAssetValidator") as validator,
            patch(f"{MOD}.UniverseManager") as manager_cls,
            patch.object(NewTickerOnboarder, "_verify_equity_type", lambda self, c: c),
            patch.object(NewTickerOnboarder, "_backfill_prices") as bf_prices,
            patch.object(NewTickerOnboarder, "_backfill_indicators"),
            patch.object(NewTickerOnboarder, "_backfill_fundamentals"),
            patch.object(NewTickerOnboarder, "_enrich_sector"),
        ):
            validator.return_value.fetch_all_assets.return_value = {"NEWX": asset}
            added = NewTickerOnboarder(caller).onboard({"AAPL", "NEWX"}, source="test")

        assert added == ["NEWX"]
        caller.commit.assert_not_called()
        caller.execute.assert_not_called()
        manager_cls.return_value.add_ticker.assert_called_once()
        assert manager_cls.call_args[0][0] is sessions[-1]
        bf_prices.assert_called_once_with(["NEWX"])

    def test_backfill_prices_reuses_collector_upsert(self):
        fake, _ = _own_sessions(existing=[])
        with (
            patch("trading_signals.db.session.get_session", side_effect=fake),
            patch(
                "trading_signals.collectors.prices_alpaca.PriceCollectorAlpaca"
            ) as collector_cls,
        ):
            collector_cls.return_value.refresh_full_history.return_value = 10
            NewTickerOnboarder()._backfill_prices(["NEWX"])
        args, kwargs = collector_cls.return_value.refresh_full_history.call_args
        assert args[1] == ["NEWX"]
        assert kwargs == {"recompute": False}


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


def _shared_session(session):
    """``get_session`` replacement yielding ``session``; counts the blocks."""
    opened = []

    @contextmanager
    def fake_get_session():
        opened.append(session)
        yield session

    return fake_get_session, opened


def _yahoo(quote_types):
    """``yfinance.Ticker`` replacement: Yahoo symbol -> ``info`` with quoteType."""
    asked = []

    def ticker(symbol):
        asked.append(symbol)
        quote_type = quote_types[symbol]
        if isinstance(quote_type, Exception):
            raise quote_type
        return MagicMock(info={"quoteType": quote_type})

    return ticker, asked


def _record(ticker, quote_type="EQUITY", sector=None, industry=None):
    return {
        "ticker": ticker,
        "quote_type": quote_type,
        "sector": sector,
        "industry": industry,
    }


class TestVerifyEquityType:
    def _verify(self, tickers, quote_types, members=()):
        session = FakeSession(members=members)
        fake_get_session, opened = _shared_session(session)
        yahoo, asked = _yahoo(quote_types)
        with (
            patch(GET_SESSION, side_effect=fake_get_session),
            patch("yfinance.Ticker", side_effect=yahoo),
            patch(ADD) as add,
        ):
            verified = NewTickerOnboarder()._verify_equity_type(tickers)
        return verified, add, session, opened, asked

    def test_index_member_with_non_equity_type_passes_and_is_not_blacklisted(
        self, caplog
    ):
        with caplog.at_level(logging.WARNING, logger=MOD):
            verified, add, _, _, asked = self._verify(
                ["BF.B"], {"BF-B": "MUTUALFUND"}, members={"BF.B"}
            )
        assert verified == ["BF.B"]
        add.assert_not_called()
        assert asked == ["BF-B"]  # Yahoo symbol, not the Alpaca one
        assert any(
            "BF.B" in m and "MUTUALFUND" in m and "not blacklisted" in m
            for m in caplog.messages
        )

    def test_non_member_etf_is_still_blacklisted(self):
        verified, add, session, _, _ = self._verify(
            ["XYZF"], {"XYZF": "ETF"}, members={"BF.B"}
        )
        assert verified == []
        add.assert_called_once_with(
            session, "XYZF", quote_type="ETF", source="onboarder/quoteType_check"
        )

    def test_mixed_batch_keeps_order_and_asks_once_for_the_suspects_only(self):
        quote_types = {
            "AAPL": "EQUITY",
            "BF-B": "MUTUALFUND",
            "XYZF": "ETF",
            "MSFT": "equity",  # case-insensitive
            "MMF": "mutualfund",
        }
        verified, add, session, opened, _ = self._verify(
            ["AAPL", "BF.B", "XYZF", "MSFT", "MMF"], quote_types, members={"BF.B"}
        )
        assert verified == ["AAPL", "BF.B", "MSFT"]
        assert [c.args[1] for c in add.call_args_list] == ["XYZF", "MMF"]
        (select,) = session.selects  # one membership query for the whole call
        assert select.compile().params["ticker_1"] == ["BF.B", "XYZF", "MMF"]
        assert len(opened) == 2  # membership read + blacklist writes

    def test_all_equities_need_no_membership_query_and_no_session(self):
        verified, add, session, opened, _ = self._verify(
            ["AAPL", "MSFT"], {"AAPL": "EQUITY", "MSFT": "EQUITY"}
        )
        assert verified == ["AAPL", "MSFT"]
        add.assert_not_called()
        assert session.statements == [] and opened == []

    def test_only_index_members_need_no_blacklist_session(self):
        verified, add, session, opened, _ = self._verify(
            ["BF.B"], {"BF-B": "MUTUALFUND"}, members={"BF.B"}
        )
        assert verified == ["BF.B"]
        add.assert_not_called()
        assert len(session.selects) == 1 and len(opened) == 1

    def test_yahoo_failure_lets_the_ticker_through_without_a_query(self):
        verified, add, session, opened, _ = self._verify(
            ["NEWX"], {"NEWX": RuntimeError("rate limited")}
        )
        assert verified == ["NEWX"]
        add.assert_not_called()
        assert session.statements == [] and opened == []

    def test_query_looks_at_open_intervals_of_any_index(self):
        _, _, session, _, _ = self._verify(["XYZF"], {"XYZF": "ETF"})
        (select,) = session.selects
        sql = str(select.compile(dialect=postgresql.dialect()))
        assert "signals.index_membership" in sql
        assert "valid_to IS NULL" in sql


class TestEnrichSector:
    def _enrich(self, tickers, records, members=()):
        session = FakeSession(members=members)
        fake_get_session, opened = _shared_session(session)
        with (
            patch(GET_SESSION, side_effect=fake_get_session),
            patch(
                "trading_signals.collectors.yfinance_client.YFinanceClient"
            ) as client_cls,
            patch(ADD) as add,
        ):
            client_cls.return_value.fetch_sector_info.return_value = records
            NewTickerOnboarder()._enrich_sector(tickers)
        return add, session, opened, client_cls.return_value.fetch_sector_info

    def test_index_member_is_not_blacklisted_but_gets_its_sector(self, caplog):
        records = [_record("BF.B", "MUTUALFUND", "Consumer Defensive", "Beverages")]
        with caplog.at_level(logging.INFO, logger=MOD):
            add, session, _, fetch = self._enrich(["BF.B"], records, members={"BF.B"})
        fetch.assert_called_once_with(["BF.B"])
        add.assert_not_called()
        ((table, values),) = session.writes
        assert table == "universe" and "is_active" not in values
        assert values["sector"] == "Consumer Defensive"
        assert values["industry"] == "Beverages"
        assert values["ticker_1"] == "BF.B"
        assert "[onboarder] Sector enrichment: 1/1 tickers enriched" in caplog.messages

    def test_non_member_etf_is_blacklisted_and_deactivated(self, caplog):
        with caplog.at_level(logging.INFO, logger=MOD):
            add, session, _, _ = self._enrich(
                ["XYZF"], [_record("XYZF", "ETF")], members={"BF.B"}
            )
        add.assert_called_once_with(
            session, "XYZF", quote_type="ETF", source="sector_enrichment"
        )
        ((table, values),) = session.writes
        assert table == "universe" and values["is_active"] is False
        assert values["ticker_1"] == "XYZF"
        assert "[onboarder] Blacklisted 1 non-equity tickers: ['XYZF']" in (
            caplog.messages
        )
        assert "[onboarder] Sector enrichment: 0/1 tickers enriched" in caplog.messages

    def test_one_membership_query_for_the_whole_call(self):
        records = [
            _record("AAPL", "EQUITY", "Technology", "Consumer Electronics"),
            _record("BF.B", "MUTUALFUND", "Consumer Defensive", "Beverages"),
            _record("XYZF", "ETF"),
        ]
        add, session, opened, _ = self._enrich(
            ["AAPL", "BF.B", "XYZF"], records, members={"BF.B"}
        )
        (select,) = session.selects
        # only the suspects are asked for, never the equities
        assert select.compile().params["ticker_1"] == ["BF.B", "XYZF"]
        assert [c.args[1] for c in add.call_args_list] == ["XYZF"]
        assert len(opened) == 1  # one transaction for membership read + writes

    def test_equities_need_no_membership_query(self):
        add, session, _, _ = self._enrich(
            ["AAPL"], [_record("AAPL", "EQUITY", "Technology")]
        )
        add.assert_not_called()
        assert session.selects == []
        assert len(session.writes) == 1

    def test_empty_sector_data_never_overwrites_existing_values(self):
        # Shared-helper semantics (the old onboarder wrote the empty values)
        add, session, _, _ = self._enrich(["AAPL"], [_record("AAPL", "EQUITY")])
        add.assert_not_called()
        assert session.writes == []
