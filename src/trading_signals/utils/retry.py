"""Retry utilities for network requests and API calls.

Provides a decorator for automatic retry with exponential backoff,
designed for transient network failures (timeouts, connection errors)
and transient HTTP errors (429 Too Many Requests, 503 Service Unavailable).
"""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

import requests

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# HTTP status codes that indicate transient server issues worth retrying
RETRYABLE_HTTP_CODES = {429, 503}

# Exceptions that indicate transient network issues worth retrying
TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
    requests.exceptions.ConnectionError,
)


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
                    # Only retry on transient HTTP codes (429, 503)
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
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            # Should never reach here, but just in case
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
