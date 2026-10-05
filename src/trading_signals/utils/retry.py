"""Retry utilities for network requests and API calls.

Provides a decorator for automatic retry with exponential backoff,
designed for transient network failures (timeouts, connection errors)
and transient HTTP errors (429 Too Many Requests, 5xx gateway errors).
A ``Retry-After`` header (seconds or HTTP date) is honoured, capped at
``MAX_RETRY_AFTER`` seconds.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import wraps
from typing import Any

import requests

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# HTTP status codes that indicate transient server issues worth retrying
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# Upper bound for a server-provided Retry-After delay (seconds)
MAX_RETRY_AFTER = 120.0

# Exceptions that indicate transient network issues worth retrying
TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def _retry_after_seconds(response: Any) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if response is None:
        return None
    try:
        value = response.headers.get("Retry-After")
    except Exception:
        return None
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (dt - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, IndexError):
        return None


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    transient_exceptions: tuple[type[Exception], ...] = TRANSIENT_EXCEPTIONS,
) -> Callable:
    """Retry decorator with exponential backoff.

    Only retries on transient network errors. Logic errors (ValueError,
    KeyError, etc.) are raised immediately.

    Args:
        max_attempts: Maximum number of attempts (including the first).
        base_delay: Initial delay in seconds before first retry.
        backoff_factor: Multiplier for delay after each retry.
        transient_exceptions: Tuple of exception types to retry on.

    Usage:
        @retry(max_attempts=3, base_delay=1.0)
        def fetch_data():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    # Only retry on transient HTTP codes (429, 5xx gateway errors)
                    status = e.response.status_code if e.response is not None else 0
                    if status not in RETRYABLE_HTTP_CODES:
                        raise  # 403, 404, etc. are not transient
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: "
                            f"HTTP {status}: {e}"
                        )
                        raise
                    delay = base_delay * (backoff_factor ** (attempt - 1))
                    retry_after = _retry_after_seconds(e.response)
                    if retry_after is not None:
                        delay = max(delay, min(retry_after, MAX_RETRY_AFTER))
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} "
                        f"HTTP {status}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                except transient_exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    delay = base_delay * (backoff_factor ** (attempt - 1))
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} "
                        f"failed: {e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            # Should never reach here, but just in case
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
