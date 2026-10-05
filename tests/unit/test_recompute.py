"""Tests for recompute_after_price_refresh (price-refresh contract)."""

from unittest.mock import MagicMock, patch

from trading_signals.derived import recompute as rc


def _patched(ta_side_effect=None, targets=7):
    ta_instance = MagicMock()
    ta_instance.recompute_ticker.side_effect = ta_side_effect or (lambda t: 10)
    tb_instance = MagicMock()
    tb_instance.recompute_tickers.return_value = targets
    return (
        patch.object(rc, "TechnicalIndicatorsComputer", return_value=ta_instance),
        patch.object(rc, "TargetBackfillComputer", return_value=tb_instance),
        ta_instance,
        tb_instance,
    )


def test_empty_list():
    session = MagicMock()
    assert rc.recompute_after_price_refresh(session, []) == {
        "tickers": 0,
        "ta_rows": 0,
        "targets": 0,
        "errors": 0,
    }
    session.execute.assert_not_called()


def test_counters_and_no_commit():
    session = MagicMock()
    p_ta, p_tb, ta, tb = _patched()
    with p_ta, p_tb:
        result = rc.recompute_after_price_refresh(session, ["MSFT", "AAPL", "AAPL", ""])
    assert result == {"tickers": 2, "ta_rows": 20, "targets": 7, "errors": 0}
    tb.recompute_tickers.assert_called_once_with(["AAPL", "MSFT"])
    assert session.begin_nested.call_count == 2
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_failing_ticker_is_isolated():
    session = MagicMock()

    def _ta(ticker):
        if ticker == "AAPL":
            raise RuntimeError("boom")
        return 5

    p_ta, p_tb, ta, tb = _patched(ta_side_effect=_ta)
    with p_ta, p_tb:
        result = rc.recompute_after_price_refresh(session, ["AAPL", "MSFT"])
    assert result["errors"] == 1
    assert result["ta_rows"] == 5
    # targets are still recomputed for all tickers
    tb.recompute_tickers.assert_called_once_with(["AAPL", "MSFT"])
    session.commit.assert_not_called()


def test_spy_refresh_logs_warning():
    session = MagicMock()
    p_ta, p_tb, _, _ = _patched()
    with p_ta, p_tb, patch.object(rc, "logger") as log:
        rc.recompute_after_price_refresh(session, ["SPY"])
    assert log.warning.called
