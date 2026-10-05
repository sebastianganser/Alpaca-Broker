"""Application configuration via environment variables.

Uses Pydantic Settings to load configuration from .env files
and environment variables. Secrets like DB_PASSWORD and API keys
must never have default values.
"""

from datetime import date
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_signals.utils.retention import data_start_date

# ── Universal Data Boundary ──────────────────────────────────────────
# DEPRECATED: evaluated once at import time and therefore stale in a
# long-running process. Use ``trading_signals.utils.retention.data_start_date()``
# (rolling 20-quarter window, decision 2026-10-05) instead.
DATA_START_DATE: date = data_start_date()


class Settings(BaseSettings):
    """Central configuration for the trading-signals application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────
    DB_HOST: str = "192.168.1.93"
    DB_PORT: int = 5435
    DB_NAME: str = "broker_data"
    DB_USER: str = "sebastian"
    DB_PASSWORD: str  # No default – must be provided!

    # ── Alpaca (Paper Trading only!) ──────────────────────────────────
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_ENDPOINT: str = "https://paper-api.alpaca.markets"
    # Market-data feed for daily bars: "sip" (consolidated, all US venues –
    # free tier allows it when end >= 15 min ago) or "iex" (~2-3% of volume).
    ALPACA_DATA_FEED: str = "sip"

    # ── SEC EDGAR ─────────────────────────────────────────────────
    # SEC fair-access policy: must identify a real, reachable contact
    # ("App/Version (email)"). Override via env if the mailbox changes;
    # generic/fake values get the IP rate-limited or blocked (HTTP 403).
    SEC_USER_AGENT: str = "TradingSignals/1.0 (sebastian.ganser@hotmail.com)"

    # ── FRED (St. Louis Fed) ─────────────────────────────────────
    FRED_API_KEY: str = ""

    # ── Polygon / Massive (Short Interest) ───────────────────────
    POLYGON_API_KEY: str = ""

    # ── Context Pack Output ──────────────────────────────────────
    CONTEXT_PACK_PATH: str = "/mnt/user/Workfiles/AlpacaBroker/context_packs"

    # ── API Security ─────────────────────────────────────────────
    # If set, mutating /ops/* and /analysis/trigger endpoints require the
    # header ``X-API-Key: <API_KEY>``. If empty, they still require the
    # header ``X-Requested-With`` (CSRF protection for cross-site requests).
    API_KEY: str = ""

    # ── Alerting ─────────────────────────────────────────────────
    # ntfy-compatible webhook URL for failed/partial job runs (optional).
    ALERT_WEBHOOK_URL: str = ""

    # ── Logging ───────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    # ── Derived Properties ────────────────────────────────────────────

    @property
    def database_url(self) -> str:
        """Build SQLAlchemy-compatible database URL."""
        # URL-encode password to handle special characters safely
        encoded_password = quote_plus(self.DB_PASSWORD)
        return (
            f"postgresql://{self.DB_USER}:{encoded_password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    def validate_alpaca_safety(self) -> None:
        """Ensure we NEVER connect to a live trading endpoint.

        This is a critical safety check. The system must only ever
        use Alpaca's paper trading API.
        """
        if self.ALPACA_ENDPOINT and "paper" not in self.ALPACA_ENDPOINT:
            raise ValueError(
                "SAFETY: Live trading is not allowed in this system! "
                f"Endpoint must contain 'paper', got: {self.ALPACA_ENDPOINT}"
            )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Uses lru_cache so the .env file is only read once per process.
    """
    return Settings()
