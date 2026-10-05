"""Tests for NewsCollectorAlpaca – watermark window, pagination cap,
per-page retry/status and multi-row insert (M3 / H5)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors import news_collector as nc
from trading_signals.collectors.news_collector import NewsCollectorAlpaca


@pytest.fixture
def collector():
    with patch("trading_signals.collectors.news_collector.get_settings") as s:
        s.return_value.ALPACA_API_KEY = "k"
        s.return_value.ALPACA_SECRET_KEY = "s"
        c = NewsCollectorAlpaca(lookback_hours=36)
    c._reset_run_state()
    return c


NOW = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)


class TestStartTime:
    def test_no_watermark_uses_lookback(self, collector):
        assert collector._start_time(None, NOW) == NOW - timedelta(hours=36)

    def test_recent_watermark_never_shortens_window(self, collector):
        latest = (NOW - timedelta(hours=2)).replace(tzinfo=None)
        assert collector._start_time(latest, NOW) == NOW - timedelta(hours=36)

    def test_old_watermark_extends_window(self, collector):
        latest = (NOW - timedelta(days=3)).replace(tzinfo=None)  # naive = UTC
        expected = NOW - timedelta(days=3) - nc.WATERMARK_OVERLAP
        assert collector._start_time(latest, NOW) == expected

    def test_catch_up_bounded(self, collector):
        latest = (NOW - timedelta(days=30)).replace(tzinfo=None)
        expected = NOW - timedelta(days=nc.MAX_CATCHUP_DAYS)
        assert collector._start_time(latest, NOW) == expected


class TestPagination:
    @patch("trading_signals.collectors.news_collector._fetch_news_page")
    def test_follows_tokens_and_counts_pages(self, mock_page, collector):
        mock_page.side_effect = [
            {"news": [{"id": 1}], "next_page_token": "t1"},
            {"news": [{"id": 2}], "next_page_token": None},
        ]
        res = collector._fetch_paginated(["AAPL"], NOW, "batch 1")
        assert [a["id"] for a in res] == [1, 2]
        assert mock_page.call_args_list[1].args[3] == "t1"
        assert collector._attempts == 2 and collector._errors == 0

    @patch("trading_signals.collectors.news_collector._fetch_news_page")
    def test_cap_hit_warns_and_marks_partial(self, mock_page, collector):
        mock_page.return_value = {"news": [{"id": 1}], "next_page_token": "more"}
        res = collector._fetch_paginated(None, NOW, "global", max_pages=3)
        assert len(res) == 3
        assert collector._partial_reasons  # forces PARTIAL status
        assert mock_page.call_count == 3

    @patch("trading_signals.collectors.news_collector._fetch_news_page")
    def test_page_failure_keeps_earlier_pages(self, mock_page, collector):
        mock_page.side_effect = [
            {"news": [{"id": 1}], "next_page_token": "t1"},
            RuntimeError("boom"),
        ]
        res = collector._fetch_paginated(["AAPL"], NOW, "batch 1")
        assert [a["id"] for a in res] == [1]
        assert collector._attempts == 2 and collector._errors == 1

    @patch("trading_signals.collectors.news_collector.time", create=True)
    @patch("trading_signals.collectors.news_collector.requests.get")
    def test_retry_is_per_page(self, mock_get, _t):
        """The retry decorator wraps a single page request."""
        import requests

        bad = MagicMock(status_code=503)
        bad.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=bad
        )
        good = MagicMock(status_code=200)
        good.json.return_value = {"news": [], "next_page_token": None}
        mock_get.side_effect = [bad, good]

        with patch("trading_signals.utils.retry.time.sleep"):
            payload = nc._fetch_news_page(None, NOW, {}, "tok")

        assert payload == {"news": [], "next_page_token": None}
        assert mock_get.call_count == 2
        assert mock_get.call_args.kwargs["params"]["page_token"] == "tok"


class TestFetchAndStore:
    def test_fetch_reads_watermark_before_http(self, collector):
        session = MagicMock()
        wm = MagicMock()
        wm.scalar.return_value = None
        active = MagicMock()
        active.all.return_value = [("AAPL",), ("MSFT",)]
        session.execute.side_effect = [wm, active]
        collector._fetch_paginated = MagicMock(
            side_effect=[[{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 3}]]
        )

        res = collector.fetch(session)

        assert [a["id"] for a in res] == [1, 2, 3]  # dedup across requests
        session.commit.assert_called()
        assert collector._fetch_paginated.call_args_list[0].args[0] == [
            "AAPL", "MSFT"
        ]

    def test_store_single_multirow_insert(self, collector):
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        data = [
            {"id": 1, "headline": "H", "created_at": "2026-10-04T10:00:00Z",
             "symbols": ["AAPL"]},
            {"id": 2, "headline": "", "created_at": "2026-10-04T10:00:00Z"},
            {"id": 3, "headline": "G", "created_at": "2026-10-04T11:00:00Z"},
        ]
        fetched, written = collector.store(session, data)
        assert (fetched, written) == (3, 1)
        assert session.execute.call_count == 1
