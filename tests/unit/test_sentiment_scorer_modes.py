"""Tests for FinBERT result normalisation (review finding M9)."""

from unittest.mock import MagicMock

import pytest

from trading_signals.derived.sentiment_scorer import (
    FinBERTScorer,
    _normalize_finbert_result,
)


class TestTopLabel:
    def test_positive(self):
        r = _normalize_finbert_result({"label": "positive", "score": 0.9})
        assert r.score == pytest.approx(0.9)
        assert r.label == "positive"

    def test_negative(self):
        r = _normalize_finbert_result({"label": "Negative", "score": 0.8})
        assert r.score == pytest.approx(-0.8)

    def test_neutral_is_zero(self):
        r = _normalize_finbert_result({"label": "neutral", "score": 0.6})
        assert r.score == 0.0
        assert r.confidence == pytest.approx(0.6)


class TestProbDiff:
    def test_prob_difference(self):
        raw = [
            {"label": "neutral", "score": 0.55},
            {"label": "positive", "score": 0.40},
            {"label": "negative", "score": 0.05},
        ]
        r = _normalize_finbert_result(raw)
        assert r.label == "neutral"
        assert r.score == pytest.approx(0.35)
        assert r.confidence == pytest.approx(0.55)

    def test_empty_list(self):
        r = _normalize_finbert_result([])
        assert r.score == 0.0 and r.label == "neutral"


class TestScorerModes:
    def test_default_mode_keeps_v1(self):
        assert FinBERTScorer().model_version == "finbert-v1"

    def test_prob_diff_mode_has_own_version(self):
        s = FinBERTScorer(mode="prob_diff")
        assert s.model_version == "finbert-v2-probdiff"
        assert FinBERTScorer.model_version == "finbert-v1"  # class unchanged

    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            FinBERTScorer(mode="bogus")

    def test_score_batch_with_list_output(self):
        s = FinBERTScorer(mode="prob_diff")
        s._pipe = MagicMock(
            return_value=[
                [
                    {"label": "positive", "score": 0.7},
                    {"label": "negative", "score": 0.1},
                    {"label": "neutral", "score": 0.2},
                ]
            ]
        )
        out = s.score_batch(["", "Great quarter"])
        assert out[0].score == 0.0
        assert out[1].score == pytest.approx(0.6)
