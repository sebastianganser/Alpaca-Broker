"""Tests for recompute_after_price_refresh (price-refresh contract)."""

from unittest.mock import MagicMock, patch

from trading_signals.derived import recompute as rc


def _patched(ta_side_effect=None, targets=7, short_term=11, st_side_effect=None):
    ta_instance = MagicMock()
    ta_instance.recompute_ticker.side_effect = ta_side_effect or (lambda t: 10)
    tb_instance = MagicMock()
    tb_instance.recompute_tickers.return_value = targets
    st_instance = MagicMock()
    st_instance.recompute_tickers.return_value = short_term
    if st_side_effect is not None:
        st_instance.recompute_tickers.side_effect = st_side_effect
    return (
        patch.object(rc, "TechnicalIndicatorsComputer", return_value=ta_instance),
        patch.object(rc, "TargetBackfillComputer", return_value=tb_instance),
        patch.object(rc, "ShortTermBackfill", return_value=st_instance),
        ta_instance,
        tb_instance,
        st_instance,
    )


def test_empty_list():
    session = MagicMock()
    assert rc.recompute_after_price_refresh(session, []) == {
        "tickers": 0,
        "ta_rows": 0,
        "targets": 0,
        "short_term_rows": 0,
        "errors": 0,
    }
    session.execute.assert_not_called()


def test_counters_and_no_commit():
    session = MagicMock()
    p_ta, p_tb, p_st, ta, tb, st = _patched()
    with p_ta, p_tb, p_st:
        result = rc.recompute_after_price_refresh(session, ["MSFT", "AAPL", "AAPL", ""])
    assert result == {
        "tickers": 2,
        "ta_rows": 20,
        "targets": 7,
        "short_term_rows": 11,
        "errors": 0,
    }
    tb.recompute_tickers.assert_called_once_with(["AAPL", "MSFT"])
    st.recompute_tickers.assert_called_once_with(["AAPL", "MSFT"])
    # one SAVEPOINT per ticker for TA + one for the short-term features
    assert session.begin_nested.call_count == 3
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_failing_ticker_is_isolated():
    session = MagicMock()

    def _ta(ticker):
        if ticker == "AAPL":
            raise RuntimeError("boom")
        return 5

    p_ta, p_tb, p_st, ta, tb, st = _patched(ta_side_effect=_ta)
    with p_ta, p_tb, p_st:
        result = rc.recompute_after_price_refresh(session, ["AAPL", "MSFT"])
    assert result["errors"] == 1
    assert result["ta_rows"] == 5
    # targets are still recomputed for all tickers
    tb.recompute_tickers.assert_called_once_with(["AAPL", "MSFT"])
    session.commit.assert_not_called()


def test_short_term_failure_is_isolated():
    session = MagicMock()
    p_ta, p_tb, p_st, _, _, _ = _patched(st_side_effect=RuntimeError("boom"))
    with p_ta, p_tb, p_st:
        result = rc.recompute_after_price_refresh(session, ["AAPL"])
    assert result["errors"] == 1
    assert result["short_term_rows"] == 0
    assert result["targets"] == 7
    session.commit.assert_not_called()


def test_spy_refresh_logs_warning():
    session = MagicMock()
    p_ta, p_tb, p_st, _, _, _ = _patched()
    with p_ta, p_tb, p_st, patch.object(rc, "logger") as log:
        rc.recompute_after_price_refresh(session, ["SPY"])
    assert log.warning.called
