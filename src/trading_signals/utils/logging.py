"""Structured logging setup for the trading-signals application.

Provides a consistent logging configuration across all modules.
Log level is controlled via the LOG_LEVEL environment variable.
"""

import logging
import sys


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
    """

    def __init__(self, collector_name: str, max_lines: int = 200) -> None:
        super().__init__()
        self.collector_name = collector_name
        self.max_lines = max_lines
        self._lines: list[dict] = []
        self.setLevel(logging.DEBUG)  # Accept all, filter in emit()

    def emit(self, record: logging.LogRecord) -> None:
        """Capture relevant log lines."""
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
            "ts": self.format(record) if self.formatter else record.asctime or "",
            "msg": record.getMessage()[:500],  # Truncate very long messages
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
        root.addHandler(self)
        return self

    def __exit__(self, *args) -> None:
        """Detach from root logger."""
        logging.getLogger().removeHandler(self)
