"""ORM models for news articles and sentiment scoring.

NewsArticle: Raw layer – financial news from Alpaca News API.
NewsSentiment: Derived layer – sentiment scores from FinBERT (later Haiku).

The symbols column is a PostgreSQL ARRAY allowing efficient multi-ticker
lookups via GIN index. Each article can reference 0..N tickers.
Global market news (no specific tickers) is flagged via is_global=True.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class NewsArticle(Base):
    """A single news article from a financial news source."""

    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("article_id", name="uq_news_articles_article_id"),
        Index("idx_news_published", "published_at"),
        Index("idx_news_symbols", "symbols", postgresql_using="gin"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    article_id: Mapped[str | None] = mapped_column(String(100))
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    author: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    article_url: Mapped[str | None] = mapped_column(Text)
    symbols: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)))
    is_global: Mapped[bool] = mapped_column(Boolean, server_default="false")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<NewsArticle(id={self.id}, "
            f"headline={self.headline[:50]!r}..., "
            f"symbols={self.symbols}, "
            f"published={self.published_at})>"
        )


class NewsSentiment(Base):
    """Sentiment score for a news article, per ticker and model version.

    One article mentioning AAPL and MSFT generates two NewsSentiment rows.
    The model_version column allows coexistence of FinBERT and Haiku scores.
    """

    __tablename__ = "news_sentiment"
    __table_args__ = (
        UniqueConstraint(
            "article_id", "ticker", "model_version",
            name="uq_news_sentiment_article_ticker_model",
        ),
        Index("idx_sentiment_ticker_date", "ticker", "scored_at"),
        Index("idx_sentiment_model", "model_version"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("signals.news_articles.id", name="fk_news_sentiment_article_id"),
    )
    ticker: Mapped[str | None] = mapped_column(String(20))
    sentiment_label: Mapped[str] = mapped_column(String(20), nullable=False)
    sentiment_score: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(6, 4))
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<NewsSentiment(article={self.article_id}, "
            f"ticker={self.ticker!r}, "
            f"label={self.sentiment_label!r}, "
            f"score={self.sentiment_score}, "
            f"model={self.model_version!r})>"
        )
