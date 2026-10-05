"""Tests for PriceCollectorAlpaca."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from sqlalchemy.dialects import postgresql

from trading_signals.collectors.prices_alpaca import (
    BATCH_SIZE,
    PriceCollectorAlpaca,
    _fetch_bars_batch,
    _parse_bar_timestamp,
    bars_to_rows,
)

MOD = "trading_signals.collectors.prices_alpaca"
LAST_SESSION = date(2026, 4, 7)


def _bar(close, day="2026-04-07"):
    return {
        "c": close,
        "h": close + 1,
        "l": close - 1,
        "o": close,
        "v": 1000,
        "t": f"{day}T04:00:00Z",
    }


def _rows_result(rows):
    r = MagicMock()
    r.all.return_value = rows
    r.rowcount = len(rows)
    return r


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.fixture(autouse=True)
def _fixed_session_date():
    with (
        patch(f"{MOD}.last_completed_session", return_value=LAST_SESSION),
        patch(f"{MOD}.data_start_date", return_value=date(2021, 7, 1)),
    ):
        yield


class TestParseBarTimestamp:
    """Test timestamp parsing from Alpaca bar format."""

    def test_valid_timestamp(self):
        assert _parse_bar_timestamp("2026-04-07T04:00:00Z") == date(2026, 4, 7)

    def test_valid_date_only(self):
        assert _parse_bar_timestamp("2026-04-07") == date(2026, 4, 7)

    def test_empty_string(self):
        assert _parse_bar_timestamp("") is None

    def test_none(self):
        assert _parse_bar_timestamp(None) is None

    def test_invalid(self):
        assert _parse_bar_timestamp("not-a-date") is None


class TestBatchSize:
    """Verify batch configuration."""

    def test_batch_size_is_100(self):
        assert BATCH_SIZE == 100


class TestFeedAndEnd:
    def test_default_feed_is_sip_with_delayed_end(self):
        collector = PriceCollectorAlpaca(feed="sip")
        end = datetime.strptime(collector._end_param(), "%Y-%m-%dT%H:%M:%SZ")
        end = end.replace(tzinfo=UTC)
        assert end <= datetime.now(UTC) - timedelta(minutes=15)

    @patch(f"{MOD}.requests.get")
    def test_feed_param_passed(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = {"bars": {"AAPL": [_bar(1.0)]}}
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp
        _fetch_bars_batch(["AAPL"], "2026-04-01", "2026-04-07", {}, feed="sip")
        assert mock_get.call_args.kwargs["params"]["feed"] == "sip"
        assert mock_get.call_args.kwargs["params"]["adjustment"] == "all"

    @patch(f"{MOD}._fetch_bars_batch")
    def test_sip_subscription_error_falls_back_to_iex(self, mock_fetch):
        resp = MagicMock(
            status_code=403,
            text="subscription does not permit querying recent SIP data",
        )
        err = requests.exceptions.HTTPError(response=resp)
        mock_fetch.side_effect = [err, {"AAPL": [_bar(1.0)]}]

        collector = PriceCollectorAlpaca(feed="sip")
        bars = collector.fetch_bars(["AAPL"], date(2026, 4, 1))

        assert bars == {"AAPL": [_bar(1.0)]}
        assert collector.feed == "iex"
        assert mock_fetch.call_args.kwargs["feed"] == "iex"
        status, note = collector._compute_status(1)
        assert status == "partial"
        assert "IEX" in note

    @patch(f"{MOD}._fetch_bars_batch")
    def test_other_http_errors_propagate(self, mock_fetch):
        resp = MagicMock(status_code=401, text="unauthorized")
        mock_fetch.side_effect = requests.exceptions.HTTPError(response=resp)
        collector = PriceCollectorAlpaca(feed="sip")
        with pytest.raises(requests.exceptions.HTTPError):
            collector.fetch_bars(["AAPL"], date(2026, 4, 1))


class TestPriceCollectorAlpaca:
    """Test collector logic with mocked API."""

    @patch(f"{MOD}._fetch_bars_batch")
    def test_fetch_batches_tickers(self, mock_fetch):
        """Verify fetch creates proper batches."""
        mock_fetch.return_value = {"AAPL": [_bar(253.59)]}

        collector = PriceCollectorAlpaca(lookback_days=5)
        session = MagicMock()
        session.execute.side_effect = [
            _rows_result([("AAPL",), ("MSFT",), ("TSLA",)]),  # universe
            _rows_result([]),  # stored prices for re-adjust check
        ]

        data = collector.fetch(session)
        assert "AAPL" in data
        assert mock_fetch.call_count == 1
        assert collector.refresh_tickers == set()
        # read transaction released before/after HTTP work
        assert session.commit.called

    @patch(f"{MOD}._fetch_bars_batch")
    def test_failed_batches_counted_as_errors(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("boom")
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        session.execute.side_effect = [_rows_result([("AAPL",)]), _rows_result([])]
        data = collector.fetch(session)
        assert data == {}
        assert collector._compute_status(len(data))[0] == "failed"

    @patch(f"{MOD}._fetch_bars_batch")
    def test_readjustment_triggers_full_history_refresh(self, mock_fetch):
        # Daily window: AAPL close now 100 (stored 200 → split 2:1); MSFT unchanged
        daily = {"AAPL": [_bar(100.0)], "MSFT": [_bar(50.0)]}
        history = {"AAPL": [_bar(99.0, "2021-07-01"), _bar(100.0)]}
        mock_fetch.side_effect = [daily, history]

        collector = PriceCollectorAlpaca()
        session = MagicMock()
        session.execute.side_effect = [
            _rows_result([("AAPL",), ("MSFT",)]),
            _rows_result(
                [
                    ("AAPL", LAST_SESSION, 200.0),
                    ("MSFT", LAST_SESSION, 50.02),  # 0.04 % → below threshold
                ]
            ),
        ]
        data = collector.fetch(session)

        assert collector.refresh_tickers == {"AAPL"}
        assert len(data["AAPL"]) == 2  # full history replaced window bars
        assert mock_fetch.call_args_list[1].kwargs["symbols"] == ["AAPL"]
        assert mock_fetch.call_args_list[1].kwargs["start"] == "2021-07-01"

        # store → upsert + recompute for refreshed tickers
        store_session = MagicMock()
        store_session.execute.return_value.rowcount = 3
        with patch(
            "trading_signals.derived.recompute.recompute_after_price_refresh",
            return_value={"tickers": 1},
        ) as mock_recompute:
            collector.store(store_session, data)
        mock_recompute.assert_called_once_with(store_session, ["AAPL"])

    def test_recompute_failure_marks_partial(self):
        collector = PriceCollectorAlpaca()
        collector.refresh_tickers = {"AAPL"}
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        with patch(
            "trading_signals.derived.recompute.recompute_after_price_refresh",
            side_effect=NotImplementedError,
        ):
            collector.store(session, {"AAPL": [_bar(1.0)]})
        assert collector._compute_status(1)[0] == "partial"

    def test_store_upserts_all_ohlcv_columns(self):
        """store() must ON CONFLICT DO UPDATE (lookback window is refreshed)."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        session.execute.return_value.rowcount = 2

        data = {"AAPL": [_bar(253.59)], "MSFT": [_bar(372.4)]}
        fetched, written = collector.store(session, data)

        assert fetched == 2
        assert written == 2
        assert session.execute.call_count == 1  # one multi-row statement
        sql = _sql(session.execute.call_args_list[0][0][0])
        assert "ON CONFLICT (ticker, trade_date) DO UPDATE" in sql
        for col in ("open", "high", "low", "close", "adj_close", "volume"):
            assert f"{col} = excluded.{col}" in sql
        session.flush.assert_called()

    def test_store_skips_no_close(self):
        """Bars without close price should be skipped."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        bar = _bar(1.0)
        bar["c"] = None
        fetched, written = collector.store(session, {"AAPL": [bar]})
        assert fetched == 1
        assert written == 0
        session.execute.assert_not_called()

    def test_store_skips_no_timestamp(self):
        """Bars without timestamp should be skipped."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        bar = _bar(1.0)
        bar["t"] = ""
        fetched, written = collector.store(session, {"AAPL": [bar]})
        assert fetched == 1
        assert written == 0

    def test_incomplete_current_session_bar_dropped(self):
        rows = bars_to_rows("AAPL", [_bar(1.0, "2026-04-08")], max_date=LAST_SESSION)
        assert rows == []

    def test_adj_close_equals_close(self):
        """adj_close should equal close (adjustment=all means close IS adjusted)."""
        rows = bars_to_rows("AAPL", [_bar(253.59)])
        assert rows[0]["adj_close"] == rows[0]["close"] == 253.59
        assert rows[0]["is_extrapolated"] is False
        assert rows[0]["source"] == "alpaca"


class TestGapRepairWiring:
    @patch(f"{MOD}.GapDetector")
    def test_gap_repair_uses_alpaca_and_retention_window(self, mock_detector_cls):
        detector = mock_detector_cls.return_value
        detector.detect_gaps_bulk.return_value = {"AAPL": [date(2026, 3, 2)]}
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        session.execute.return_value.all.return_value = [("AAPL",)]

        collector.check_and_repair_gaps(session)

        kwargs = detector.detect_gaps_bulk.call_args.kwargs
        assert kwargs["start_bound"] == date(2021, 7, 1)
        assert kwargs["end"] == LAST_SESSION
        repair_kwargs = detector.repair_gaps.call_args.kwargs
        assert repair_kwargs["batch_fetch_fn"] == collector._gap_fetch
        assert repair_kwargs["min_extrapolate_age_days"] >= 5
        assert mock_detector_cls.call_args.kwargs["source"] == "alpaca"

    @patch(f"{MOD}._fetch_bars_batch")
    def test_gap_fetch_adapter_returns_frames(self, mock_fetch):
        mock_fetch.return_value = {"AAPL": [_bar(10.0, "2026-03-02")]}
        collector = PriceCollectorAlpaca()
        frames = collector._gap_fetch(["AAPL"], date(2026, 3, 2), date(2026, 3, 3))
        df = frames["AAPL"]
        assert float(df.iloc[0]["Close"]) == 10.0
        assert float(df.iloc[0]["Adj Close"]) == 10.0
