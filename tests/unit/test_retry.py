"""Tests for the retry decorator."""

import time

import pytest

from trading_signals.utils.retry import retry


class TestRetryDecorator:
    """Test retry logic with exponential backoff."""

    def test_succeeds_on_first_try(self):
        """Function that succeeds should only be called once."""
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def success():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = success()
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_transient_error(self):
        """Should retry on ConnectionError and eventually succeed."""
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary failure")
            return "recovered"

        result = fails_then_succeeds()
        assert result == "recovered"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        """Should raise after exhausting all attempts."""
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("persistent failure")

        with pytest.raises(ConnectionError, match="persistent failure"):
            always_fails()
        assert call_count == 3

    def test_no_retry_on_logic_error(self):
        """Should NOT retry on ValueError, KeyError, etc."""
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def logic_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            logic_error()
        assert call_count == 1  # No retry!

    def test_retries_on_timeout(self):
        """Should retry on TimeoutError."""
        call_count = 0

        @retry(max_attempts=2, base_delay=0.01)
        def timeout_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timed out")
            return "ok"

        result = timeout_once()
        assert result == "ok"
        assert call_count == 2

    def test_custom_exception_types(self):
        """Should accept custom exception types for retry."""
        call_count = 0

        @retry(
            max_attempts=2,
            base_delay=0.01,
            transient_exceptions=(RuntimeError,),
        )
        def custom_error():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("custom transient")
            return "ok"

        result = custom_error()
        assert result == "ok"
        assert call_count == 2


class TestRetryHttp:
    """M8: 5xx gateway errors are retried, Retry-After is honoured."""

    @staticmethod
    def _http_error(status, headers=None):
        from unittest.mock import MagicMock

        import requests

        resp = MagicMock(status_code=status)
        resp.headers = headers or {}
        return requests.exceptions.HTTPError(response=resp)

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
    def test_retries_transient_http(self, status, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = []

        @retry(max_attempts=3, base_delay=0.01)
        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise self._http_error(status)
            return "ok"

        assert flaky() == "ok"
        assert len(calls) == 2

    def test_does_not_retry_404(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = []

        @retry(max_attempts=3, base_delay=0.01)
        def missing():
            calls.append(1)
            raise self._http_error(404)

        import requests

        with pytest.raises(requests.exceptions.HTTPError):
            missing()
        assert len(calls) == 1

    def test_honours_retry_after(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        calls = []

        @retry(max_attempts=2, base_delay=0.01)
        def limited():
            calls.append(1)
            if len(calls) == 1:
                raise self._http_error(429, {"Retry-After": "7"})
            return "ok"

        assert limited() == "ok"
        assert sleeps == [7.0]

    def test_retry_after_is_capped(self):
        from trading_signals.utils.retry import MAX_RETRY_AFTER, _retry_after_seconds

        resp = type("R", (), {"headers": {"Retry-After": "99999"}})()
        assert _retry_after_seconds(resp) == 99999.0
        assert min(_retry_after_seconds(resp), MAX_RETRY_AFTER) == MAX_RETRY_AFTER
