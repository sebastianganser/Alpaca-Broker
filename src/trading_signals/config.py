"""Application configuration via environment variables.

Uses Pydantic Settings to load configuration from .env files
and environment variables. Secrets like DB_PASSWORD and API keys
must never have default values.
"""

from urllib.parse import quote_plus

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── SEC EDGAR ─────────────────────────────────────────────────
    SEC_USER_AGENT: str = "TradingSignals/1.0 (contact@example.com)"

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
