"""Sentiment scoring for financial news headlines.

Provides an abstract SentimentScorer base class and a concrete
FinBERTScorer implementation that runs locally on CPU.

Architecture:
  - SentimentScorer (ABC): defines the interface for all scorers
  - FinBERTScorer: Uses ProsusAI/finbert (110M params, ~440MB)
  - HaikuScorer: Placeholder for future Claude Haiku API integration

The model_version field allows coexistence of multiple scorer
implementations in the database, enabling quality comparison
and gradual migration from FinBERT to Haiku.

Performance (Ryzen 7 5700G CPU):
  - FinBERT: ~50-200ms per headline, batch_size=32
  - ~5000 headlines → ~5-10 minutes total
  - RAM: ~500 MB for model + inference
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SentimentResult:
    """Result of sentiment analysis for a single text."""

    label: str  # 'positive', 'negative', 'neutral'
    score: float  # Normalized: -1.0 (negative) to +1.0 (positive)
    confidence: float  # Model confidence: 0.0 to 1.0


class SentimentScorer(ABC):
    """Abstract base class for sentiment scoring models."""

    model_version: str = "unknown"

    @abstractmethod
    def score_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Score a batch of text strings for sentiment.

        Args:
            texts: List of headlines or summaries to score.

        Returns:
            List of SentimentResult, one per input text.
            Order matches the input list.
        """
        ...


class FinBERTScorer(SentimentScorer):
    """FinBERT-based financial sentiment scorer.

    Uses ProsusAI/finbert, a BERT model fine-tuned on financial text.
    Runs on CPU, loaded lazily on first use.

    Labels: 'positive', 'negative', 'neutral'
    """

    model_version = "finbert-v1"

    def __init__(self, batch_size: int = 32) -> None:
        """Initialize FinBERT scorer.

        Args:
            batch_size: Number of texts to process per inference batch.
                        32 is optimal for CPU (balances throughput vs latency).
        """
        self._batch_size = batch_size
        self._pipe = None  # Lazy-loaded

    def _ensure_model(self) -> None:
        """Lazy-load the transformers pipeline on first use."""
        if self._pipe is not None:
            return

        logger.info(
            f"[{self.model_version}] Loading FinBERT model "
            f"(first call, this may take a moment)..."
        )
        from transformers import pipeline

        self._pipe = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            device=-1,  # CPU
            batch_size=self._batch_size,
        )
        logger.info(f"[{self.model_version}] FinBERT model loaded successfully")

    def score_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Score a batch of financial texts using FinBERT.

        Args:
            texts: List of headlines/summaries. Empty strings are scored
                   as neutral with zero confidence.

        Returns:
            List of SentimentResult with normalized scores.
        """
        if not texts:
            return []

        self._ensure_model()

        # Filter empty strings and track their positions
        valid_indices: list[int] = []
        valid_texts: list[str] = []
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_indices.append(i)
                valid_texts.append(text.strip())

        # Score valid texts
        results: list[SentimentResult] = [
            SentimentResult(label="neutral", score=0.0, confidence=0.0)
        ] * len(texts)

        if valid_texts:
            raw_results = self._pipe(
                valid_texts,
                truncation=True,
                max_length=512,
            )
            for idx, raw in zip(valid_indices, raw_results):
                results[idx] = _normalize_finbert_result(raw)

        return results


def _normalize_finbert_result(raw: dict) -> SentimentResult:
    """Normalize a FinBERT pipeline result to a SentimentResult.

    FinBERT returns:
      {"label": "positive"|"negative"|"neutral", "score": 0.0-1.0}

    We normalize to:
      score: -1.0 (negative) to +1.0 (positive), 0.0 (neutral)
      confidence: the raw model confidence (0.0-1.0)
    """
    label = raw.get("label", "neutral").lower()
    confidence = float(raw.get("score", 0.0))

    if label == "positive":
        score = confidence  # 0.0 to 1.0
    elif label == "negative":
        score = -confidence  # -1.0 to 0.0
    else:
        score = 0.0  # neutral

    return SentimentResult(
        label=label,
        score=round(score, 4),
        confidence=round(confidence, 4),
    )
