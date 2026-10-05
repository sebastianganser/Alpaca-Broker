"""Structured logging setup for the trading-signals application.

Provides a consistent logging configuration across all modules.
Log level is controlled via the LOG_LEVEL environment variable.

Secrets (API keys in query strings, bearer tokens, Alpaca key headers)
are masked by :class:`RedactingFilter`, which is attached to all root
handlers and to :class:`CollectorLogCapture` (whose lines are persisted
in ``collection_log.log_lines``).
"""

import logging
import re
import sys
import threading

# Patterns for secrets that may appear in URLs, exception strings or
# header dumps. Group 1 is kept, the secret itself is replaced by ***.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"((?:api_?key|apikey|access_token|token|secret|password)=)[^&\s'\"]+",
        re.IGNORECASE,
    ),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=\-]+", re.IGNORECASE),
    re.compile(
        r"((?:APCA-API-KEY-ID|APCA-API-SECRET-KEY|X-API-Key)['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+",
        re.IGNORECASE,
    ),
)


def redact(text: str) -> str:
    """Mask secrets (api keys, tokens, bearer credentials) in ``text``."""
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that masks secrets in the rendered log message.

    The message is rendered once (``record.getMessage()``); if anything was
    redacted, ``msg`` is replaced by the redacted text and ``args`` cleared.
    Exception text is redacted as well.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed record, let it pass
            return True
        redacted = redact(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            formatter = logging.Formatter()
            record.exc_text = formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_REDACTING_FILTER = RedactingFilter()


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the redacting filter to all handlers of ``logger`` (default root).

    Idempotent; safe to call multiple times (e.g. after uvicorn reconfigured
    logging).
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(_REDACTING_FILTER)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    log_format = (
        "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    )
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    # urllib3 logs full request URLs (incl. query-string API keys) at DEBUG
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    install_redaction()


def get_logger(name: str) -> logging.Logger:
    """Get a named logger for a module.

    Usage:
        from trading_signals.utils.logging import get_logger
        logger = get_logger(__name__)
        logger.info("Something happened")
    """
    return logging.getLogger(name)


class CollectorLogCapture(logging.Handler):
    """Captures log lines during a collector run for DB storage.

    Captures WARNING, ERROR, and CRITICAL messages by default.
    Also captures INFO lines that contain the collector name
    (for tracking onboarding, backfill progress etc.).

    Usage as context manager:
        with CollectorLogCapture("ark_holdings") as capture:
            # ... run collector ...
            log_lines = capture.get_lines()  # list of dicts

    Only records emitted by the thread that entered the context manager
    are captured, so parallel scheduler jobs don't mix their lines.
    Secrets are masked via :class:`RedactingFilter`.
    """

    def __init__(self, collector_name: str, max_lines: int = 200) -> None:
        super().__init__()
        self.collector_name = collector_name
        self.max_lines = max_lines
        self._lines: list[dict] = []
        self._thread_id: int | None = None
        self.setLevel(logging.DEBUG)  # Accept all, filter in emit()
        self.addFilter(_REDACTING_FILTER)

    # Known harmless warnings that should be displayed as INFO, not WARNING.
    # These are third-party messages that don't indicate real problems.
    _DEMOTE_PATTERNS = (
        "unauthenticated requests to the HF Hub",
        "HF_TOKEN",
    )

    def emit(self, record: logging.LogRecord) -> None:
        """Capture relevant log lines."""
        # Ignore records from other threads (parallel collector runs)
        if self._thread_id is not None and record.thread != self._thread_id:
            return

        # Demote known harmless warnings to INFO
        if record.levelno == logging.WARNING:
            msg = record.getMessage()
            if any(p in msg for p in self._DEMOTE_PATTERNS):
                record = logging.LogRecord(
                    record.name, logging.INFO, record.pathname,
                    record.lineno, record.msg, record.args, record.exc_info,
                )

        # Always capture WARNING+
        if record.levelno >= logging.WARNING:
            self._append(record)
            return

        # Capture INFO lines related to this collector or onboarder
        if record.levelno == logging.INFO:
            msg = record.getMessage()
            if (
                f"[{self.collector_name}]" in msg
                or "[onboarder]" in msg
            ):
                self._append(record)

    def _append(self, record: logging.LogRecord) -> None:
        """Add a log record to the captured lines."""
        if len(self._lines) >= self.max_lines:
            return  # Ring buffer full, prevent memory issues
        self._lines.append({
            "level": record.levelname,
            "ts": (
                self.format(record)
                if self.formatter
                else getattr(record, "asctime", "") or ""
            ),
            "msg": redact(record.getMessage())[:500],  # Truncate very long messages
        })

    def get_lines(self) -> list[dict]:
        """Return captured log lines."""
        return self._lines

    def __enter__(self) -> "CollectorLogCapture":
        """Attach to root logger."""
        root = logging.getLogger()
        # Use same formatter as the root handler
        if root.handlers:
            self.setFormatter(root.handlers[0].formatter)
        self._thread_id = threading.get_ident()
        install_redaction()
        root.addHandler(self)
        return self

    def __exit__(self, *args) -> None:
        """Detach from root logger."""
        logging.getLogger().removeHandler(self)
