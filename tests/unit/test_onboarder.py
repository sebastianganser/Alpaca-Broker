"""Tests for NewTickerOnboarder (M5: never commit the caller's session)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from trading_signals.universe.onboarder import NewTickerOnboarder

MOD = "trading_signals.universe.onboarder"


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
