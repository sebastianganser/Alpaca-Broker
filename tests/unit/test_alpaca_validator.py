"""Tests for AlpacaAssetValidator."""

from unittest.mock import MagicMock, patch

import pytest

from trading_signals.universe.alpaca_validator import (
    AlpacaAsset,
    AlpacaAssetValidator,
    ValidationResult,
)


class TestValidationResult:
    """Test the ValidationResult dataclass."""

    def test_default_values(self):
        result = ValidationResult()
        assert result.total_checked == 0
        assert result.active_tradeable == 0
        assert result.not_found == []
        assert result.not_tradeable == []

    def test_fields_are_independent(self):
        """Ensure mutable defaults don't share state."""
        r1 = ValidationResult()
        r2 = ValidationResult()
        r1.not_found.append("XYZ")
        assert r2.not_found == []


class TestAlpacaAssetValidator:
    """Test validation logic with mocked API responses."""

    @patch("trading_signals.universe.alpaca_validator.requests")
    @patch("trading_signals.universe.alpaca_validator.get_settings")
    def test_validate_active_ticker(self, mock_settings, mock_requests):
        """Active tradeable ticker should be marked as 'keep'."""
        mock_settings.return_value = MagicMock(
            ALPACA_API_KEY="test",
            ALPACA_SECRET_KEY="test",
            ALPACA_ENDPOINT="https://paper-api.alpaca.markets",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "class": "us_equity",
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_response

        validator = AlpacaAssetValidator()
        result = validator.validate_tickers(["AAPL"])

        assert result.active_tradeable == 1
        assert result.not_found == []
        assert result.details["AAPL"]["action"] == "keep"

    @patch("trading_signals.universe.alpaca_validator.requests")
    @patch("trading_signals.universe.alpaca_validator.get_settings")
    def test_validate_missing_ticker(self, mock_settings, mock_requests):
        """Ticker not in Alpaca should be marked for deactivation."""
        mock_settings.return_value = MagicMock(
            ALPACA_API_KEY="test",
            ALPACA_SECRET_KEY="test",
            ALPACA_ENDPOINT="https://paper-api.alpaca.markets",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = []  # No assets
        mock_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_response

        validator = AlpacaAssetValidator()
        result = validator.validate_tickers(["WBA"])

        assert result.active_tradeable == 0
        assert result.not_found == ["WBA"]
        assert result.details["WBA"]["action"] == "deactivate"

    @patch("trading_signals.universe.alpaca_validator.requests")
    @patch("trading_signals.universe.alpaca_validator.get_settings")
    def test_validate_not_tradeable(self, mock_settings, mock_requests):
        """Ticker that exists but isn't tradeable should be flagged."""
        mock_settings.return_value = MagicMock(
            ALPACA_API_KEY="test",
            ALPACA_SECRET_KEY="test",
            ALPACA_ENDPOINT="https://paper-api.alpaca.markets",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "symbol": "DELISTED",
                "name": "Delisted Corp",
                "exchange": "NYSE",
                "status": "active",
                "tradable": False,
                "class": "us_equity",
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_response

        validator = AlpacaAssetValidator()
        result = validator.validate_tickers(["DELISTED"])

        assert result.not_tradeable == ["DELISTED"]
        assert result.details["DELISTED"]["action"] == "deactivate"

    @patch("trading_signals.universe.alpaca_validator.requests")
    @patch("trading_signals.universe.alpaca_validator.get_settings")
    def test_is_tradeable(self, mock_settings, mock_requests):
        """Quick single-ticker check."""
        mock_settings.return_value = MagicMock(
            ALPACA_API_KEY="test",
            ALPACA_SECRET_KEY="test",
            ALPACA_ENDPOINT="https://paper-api.alpaca.markets",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"symbol": "SPY", "name": "SPDR S&P 500", "exchange": "ARCA",
             "status": "active", "tradable": True, "class": "us_equity"},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_response

        validator = AlpacaAssetValidator()
        assert validator.is_tradeable("SPY") is True
        assert validator.is_tradeable("FAKE") is False
