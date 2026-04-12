"""Alpaca Asset Validator – validates universe against Alpaca's tradeable assets.

Uses the Alpaca Market API to check which tickers in our universe
are actually tradeable. This is the authoritative source for:
  - Confirming a ticker is active and tradeable
  - Detecting delistings (ticker not found → deactivate)
  - Detecting symbol changes (future: name matching)

Only tickers that are tradeable on Alpaca are worth collecting data for,
since we can only paper-trade what Alpaca offers.
"""

from dataclasses import dataclass, field

import requests

from trading_signals.config import get_settings
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AlpacaAsset:
    """Simplified representation of an Alpaca asset."""

    symbol: str
    name: str
    exchange: str
    status: str
    tradable: bool
    asset_class: str


@dataclass
class ValidationResult:
    """Result of validating the universe against Alpaca."""

    total_checked: int = 0
    active_tradeable: int = 0
    not_found: list[str] = field(default_factory=list)
    not_tradeable: list[str] = field(default_factory=list)
    details: dict[str, dict] = field(default_factory=dict)


class AlpacaAssetValidator:
    """Validate ticker universe against Alpaca's tradeable assets.

    Uses the Alpaca v2/assets endpoint to build a lookup of all
    tradeable US equities, then checks our universe against it.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }
        self._base_url = settings.ALPACA_ENDPOINT
        self._asset_cache: dict[str, AlpacaAsset] | None = None

    def fetch_all_assets(self) -> dict[str, AlpacaAsset]:
        """Fetch all active US equity assets from Alpaca.

        Returns a dict mapping symbol → AlpacaAsset.
        Results are cached for the lifetime of this instance.
        """
        if self._asset_cache is not None:
            return self._asset_cache

        url = f"{self._base_url}/v2/assets"
        params = {"status": "active", "asset_class": "us_equity"}

        logger.info("Fetching Alpaca asset list...")
        response = requests.get(url, headers=self._headers, params=params, timeout=30)
        response.raise_for_status()

        assets = {}
        for item in response.json():
            asset = AlpacaAsset(
                symbol=item["symbol"],
                name=item.get("name", ""),
                exchange=item.get("exchange", ""),
                status=item["status"],
                tradable=item.get("tradable", False),
                asset_class=item.get("class", "us_equity"),
            )
            assets[asset.symbol] = asset

        logger.info(f"Loaded {len(assets)} active US equities from Alpaca")
        self._asset_cache = assets
        return assets

    def validate_tickers(self, tickers: list[str]) -> ValidationResult:
        """Validate a list of tickers against Alpaca's assets.

        Args:
            tickers: List of ticker symbols to validate.

        Returns:
            ValidationResult with details about each ticker.
        """
        assets = self.fetch_all_assets()
        result = ValidationResult(total_checked=len(tickers))

        for ticker in tickers:
            asset = assets.get(ticker)

            if asset is None:
                result.not_found.append(ticker)
                result.details[ticker] = {
                    "status": "not_found",
                    "action": "deactivate",
                    "reason": "Not found in Alpaca active assets",
                }
                logger.warning(f"  {ticker}: NOT FOUND in Alpaca -> should deactivate")

            elif not asset.tradable:
                result.not_tradeable.append(ticker)
                result.details[ticker] = {
                    "status": "not_tradeable",
                    "action": "deactivate",
                    "exchange": asset.exchange,
                    "reason": f"Found but not tradeable (exchange: {asset.exchange})",
                }
                logger.warning(f"  {ticker}: not tradeable → should deactivate")

            else:
                result.active_tradeable += 1
                result.details[ticker] = {
                    "status": "active",
                    "action": "keep",
                    "exchange": asset.exchange,
                    "name": asset.name,
                }

        logger.info(
            f"Validation complete: {result.active_tradeable} active, "
            f"{len(result.not_found)} not found, "
            f"{len(result.not_tradeable)} not tradeable"
        )
        return result

    def is_tradeable(self, ticker: str) -> bool:
        """Quick check if a single ticker is tradeable on Alpaca."""
        assets = self.fetch_all_assets()
        asset = assets.get(ticker)
        return asset is not None and asset.tradable
