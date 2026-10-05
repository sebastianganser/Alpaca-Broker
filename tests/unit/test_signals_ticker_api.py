"""Tests for the read-only signal / ticker / universe API helpers.

No database: ORM rows are simulated with ``SimpleNamespace`` and request
validation is exercised via FastAPI's TestClient with a dummy session.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_signals.api.deps import get_db
from trading_signals.api.routes import features, signals, ticker, universe
from trading_signals.api.schemas import AnalystRatingItem, TickerSignals
from trading_signals.api.serializers import (
    analyst_rating_item,
    ark_delta_item,
    insider_cluster_item,
    politician_trade_item,
    to_float,
)
from trading_signals.utils import job_status

# ── Serializers ──────────────────────────────────────────────────────────


class TestSerializers:
    def test_to_float_preserves_zero_and_none(self):
        assert to_float(Decimal("0")) == 0.0
        assert to_float(0) == 0.0
        assert to_float(None) is None

    def test_ark_delta_zero_values_survive(self):
        d = SimpleNamespace(
            delta_date=date(2026, 1, 2),
            etf_ticker="ARKK",
            ticker="TSLA",
            delta_type="closed",
            shares_delta=Decimal("-100"),
            shares_prev=Decimal("100"),
            shares_curr=Decimal("0"),
            weight_delta=Decimal("-0.5"),
            weight_prev=Decimal("0.5"),
            weight_curr=Decimal("0"),
        )
        item = ark_delta_item(d)
        assert item.shares_curr == 0.0
        assert item.weight_curr == 0.0

    def test_insider_cluster_zero_score(self):
        c = SimpleNamespace(
            ticker="AAPL",
            cluster_start=date(2026, 1, 1),
            cluster_end=date(2026, 1, 5),
            n_insiders=3,
            n_buys=0,
            n_sells=3,
            total_buy_value=Decimal("0"),
            cluster_score=Decimal("0"),
        )
        item = insider_cluster_item(c)
        assert item.total_buy_value == 0.0
        assert item.cluster_score == 0.0

    def test_politician_delay(self):
        t = SimpleNamespace(
            politician_name="X",
            party="D",
            ticker="NVDA",
            transaction_date=date(2026, 1, 1),
            disclosure_date=date(2026, 1, 11),
            transaction_type="Purchase",
            amount_range="$1K-$15K",
        )
        assert politician_trade_item(t).delay_days == 10

    def test_rating_separates_firm_and_consensus_target(self):
        r = SimpleNamespace(
            ticker="MSFT",
            firm="GS",
            rating_date=date(2026, 1, 3),
            rating_new="Buy",
            rating_old="Neutral",
            action="upgrade",
            price_target_new=Decimal("500"),
            price_target_old=Decimal("450"),
        )
        item = analyst_rating_item(r, {"MSFT": 480.0})
        assert item.firm_target_new == 500.0
        assert item.firm_target_old == 450.0
        assert item.consensus_target == 480.0

    def test_rating_without_consensus(self):
        r = SimpleNamespace(
            ticker="MSFT",
            firm=None,
            rating_date=None,
            rating_new=None,
            rating_old=None,
            action=None,
            price_target_new=None,
            price_target_old=None,
        )
        item = analyst_rating_item(r, {})
        assert item.consensus_target is None
        assert item.firm_target_new is None

    def test_rating_schema_has_no_ambiguous_fields(self):
        fields = set(AnalystRatingItem.model_fields)
        assert "price_target_new" not in fields
        assert {"firm_target_new", "firm_target_old", "consensus_target"} <= fields


# ── ARK summary aggregation ──────────────────────────────────────────────


class TestArkSummary:
    @staticmethod
    def _delta(ticker, etf, day, shares, weight):
        return SimpleNamespace(
            ticker=ticker,
            etf_ticker=etf,
            delta_date=date(2026, 1, day),
            shares_delta=Decimal(shares),
            weight_delta=Decimal(weight),
        )

    def test_aggregates_and_sorts_by_abs_weight(self):
        rows = [
            self._delta("TSLA", "ARKK", 1, "100", "0.10"),
            self._delta("TSLA", "ARKW", 2, "50", "0.05"),
            self._delta("COIN", "ARKK", 2, "-500", "-0.90"),
        ]
        result = signals.summarize_ark_deltas(rows)
        assert [r.ticker for r in result] == ["COIN", "TSLA"]
        tsla = result[1]
        assert tsla.n_etfs == 2
        assert tsla.direction == "increased"
        assert tsla.total_weight_delta_bps == pytest.approx(15.0)
        assert result[0].direction == "decreased"

    def test_zero_shares_is_mixed(self):
        rows = [self._delta("X", "ARKK", 1, "0", "0")]
        assert signals.summarize_ark_deltas(rows)[0].direction == "mixed"


# ── Ticker data-quality: price collector status ──────────────────────────


class TestSignalUpdateDimension:
    def test_scheduler_inactive(self):
        dim = ticker.signal_update_dimension(False, job_status.SUCCESS, None)
        assert dim.status == "missing"

    @pytest.mark.parametrize("raw", ["failed", "error", "partial"])
    def test_failed_or_partial_is_partial(self, raw):
        dim = ticker.signal_update_dimension(True, raw, datetime(2026, 1, 5, 22, 0))
        assert dim.status == "partial"
        assert "05.01. 22:00" in dim.summary

    def test_success(self):
        dim = ticker.signal_update_dimension(True, "success", None)
        assert dim.status == "complete"

    def test_uses_real_collector_name(self):
        assert ticker.PRICE_COLLECTOR_NAME == "prices_alpaca"


# ── Universe price helpers ───────────────────────────────────────────────


class TestUniversePrices:
    def test_build_price_map(self):
        rows = [
            SimpleNamespace(
                ticker="AAPL", close=Decimal("110"), trade_date=date(2026, 1, 2), rn=1
            ),
            SimpleNamespace(
                ticker="AAPL", close=Decimal("100"), trade_date=date(2026, 1, 1), rn=2
            ),
            SimpleNamespace(
                ticker="NEW", close=Decimal("0"), trade_date=date(2026, 1, 2), rn=1
            ),
        ]
        result = universe.build_price_map(rows)
        assert result["AAPL"] == {
            "close": 110.0,
            "trade_date": date(2026, 1, 2),
            "prev_close": 100.0,
        }
        assert result["NEW"]["close"] == 0.0
        assert result["NEW"]["prev_close"] is None

    def test_price_change_pct(self):
        assert universe.price_change_pct(110.0, 100.0) == 10.0
        assert universe.price_change_pct(100.0, 100.0) == 0.0
        assert universe.price_change_pct(100.0, None) is None
        assert universe.price_change_pct(None, 100.0) is None
        assert universe.price_change_pct(100.0, 0.0) is None


# ── Request validation (no DB access needed) ─────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    for module in (signals, ticker, universe, features):
        app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


class TestRequestValidation:
    def test_invalid_period_rejected(self, client):
        assert client.get("/api/v1/ticker/AAPL/prices?period=10y").status_code == 422
        assert client.get("/api/v1/ticker/AAPL/indicators?period=x").status_code == 422

    def test_convergence_limit_bounded(self, client):
        assert client.get("/api/v1/features/convergence?limit=0").status_code == 422
        assert (
            client.get("/api/v1/features/convergence?limit=100000").status_code == 422
        )

    def test_sentiment_sort_validated(self, client):
        assert (
            client.get("/api/v1/signals/sentiment/summary?sort=bogus").status_code
            == 422
        )

    def test_signal_limits_bounded(self, client):
        for path in ("ark", "insider", "politicians", "ratings", "sentiment/articles"):
            assert client.get(f"/api/v1/signals/{path}?limit=100000").status_code == 422

    def test_feature_groups_endpoint(self, client):
        resp = client.get("/api/v1/features/groups")
        assert resp.status_code == 200
        keys = {g["key"] for g in resp.json()}
        assert {"ark", "form13f", "short_interest", "macro"} <= keys


class TestTickerSignalsSchema:
    def test_counts_default_zero(self):
        ts = TickerSignals(ticker="AAPL", days=90)
        assert ts.counts.ark_deltas == 0
        assert ts.analyst_ratings == []

    def test_route_declares_response_model(self):
        route = next(
            r
            for r in ticker.router.routes
            if getattr(r, "path", "") == "/ticker/{symbol}/signals"
        )
        assert route.response_model is TickerSignals
