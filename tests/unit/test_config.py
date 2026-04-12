"""Tests for application configuration."""

import os

import pytest


class TestSettings:
    """Test the Pydantic Settings configuration."""

    def test_settings_loads_from_env(self, monkeypatch):
        """Settings should load values from environment variables."""
        monkeypatch.setenv("DB_HOST", "testhost")
        monkeypatch.setenv("DB_PORT", "5555")
        monkeypatch.setenv("DB_NAME", "testdb")
        monkeypatch.setenv("DB_USER", "testuser")
        monkeypatch.setenv("DB_PASSWORD", "testpass")

        # Clear the cached settings
        from trading_signals.config import Settings
        settings = Settings()

        assert settings.DB_HOST == "testhost"
        assert settings.DB_PORT == 5555
        assert settings.DB_NAME == "testdb"
        assert settings.DB_USER == "testuser"
        assert settings.DB_PASSWORD == "testpass"

    def test_database_url_construction(self, monkeypatch):
        """database_url should construct a valid PostgreSQL URL."""
        monkeypatch.setenv("DB_HOST", "192.168.1.93")
        monkeypatch.setenv("DB_PORT", "5435")
        monkeypatch.setenv("DB_NAME", "broker_data")
        monkeypatch.setenv("DB_USER", "sebastian")
        monkeypatch.setenv("DB_PASSWORD", "secret123")

        from trading_signals.config import Settings
        settings = Settings()

        assert settings.database_url == (
            "postgresql://sebastian:secret123@192.168.1.93:5435/broker_data"
        )

    def test_alpaca_safety_check_passes_for_paper(self, monkeypatch):
        """Safety check should pass for paper trading endpoint."""
        monkeypatch.setenv("DB_PASSWORD", "test")
        monkeypatch.setenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")

        from trading_signals.config import Settings
        settings = Settings()

        # Should not raise
        settings.validate_alpaca_safety()

    def test_alpaca_safety_check_blocks_live(self, monkeypatch):
        """Safety check should BLOCK live trading endpoint."""
        monkeypatch.setenv("DB_PASSWORD", "test")
        monkeypatch.setenv("ALPACA_ENDPOINT", "https://api.alpaca.markets")

        from trading_signals.config import Settings
        settings = Settings()

        with pytest.raises(ValueError, match="SAFETY"):
            settings.validate_alpaca_safety()

    def test_alpaca_safety_check_allows_empty_endpoint(self, monkeypatch):
        """Safety check should allow empty endpoint (not configured yet)."""
        monkeypatch.setenv("DB_PASSWORD", "test")
        monkeypatch.setenv("ALPACA_ENDPOINT", "")

        from trading_signals.config import Settings
        settings = Settings()

        # Empty endpoint is okay (Alpaca not configured yet)
        settings.validate_alpaca_safety()

    def test_db_password_is_required(self, monkeypatch):
        """DB_PASSWORD must be provided – no default allowed."""
        # Clear any existing DB_PASSWORD
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        # Also prevent .env file from being read
        monkeypatch.setattr(os, "getcwd", lambda: "/nonexistent")

        from trading_signals.config import Settings

        with pytest.raises(Exception):
            Settings(_env_file=None)
