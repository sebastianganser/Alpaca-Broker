"""Tests for scripts/repair/collectors_refetch_prices.py (no DB, no HTTP)."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "repair"
    / "collectors_refetch_prices.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("collectors_refetch_prices", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _fake_session():
    s = MagicMock()
    s.execute.return_value.all.return_value = [("AAPL",), ("MSFT",)]
    yield s


def test_dry_run_is_default_and_writes_nothing():
    mod = _load()
    assert mod.parse_args([]).apply is False
    with (
        patch("trading_signals.db.session.get_session", side_effect=_fake_session),
        patch(
            "trading_signals.collectors.prices_alpaca.PriceCollectorAlpaca"
        ) as collector_cls,
    ):
        assert mod.main([]) == 0
    collector_cls.return_value.refresh_full_history.assert_not_called()


def test_apply_refreshes_batches_and_recomputes():
    mod = _load()
    with (
        patch("trading_signals.db.session.get_session", side_effect=_fake_session),
        patch(
            "trading_signals.collectors.prices_alpaca.PriceCollectorAlpaca"
        ) as collector_cls,
        patch(
            "trading_signals.derived.recompute.recompute_after_price_refresh",
            return_value={"tickers": 1},
        ) as recompute,
    ):
        collector_cls.return_value.refresh_full_history.return_value = 5
        assert mod.main(["--apply", "--batch", "1"]) == 0
    assert collector_cls.return_value.refresh_full_history.call_count == 2
    assert recompute.call_count == 2
