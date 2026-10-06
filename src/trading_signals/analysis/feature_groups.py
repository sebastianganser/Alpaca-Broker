"""Feature grouping for reporting – derived from the FeatureSnapshot model.

The single source of truth for *which* features exist is
``trading_signals.db.models.features.FEATURE_COLUMNS``. This module only
maps each feature name to a reporting group via name prefixes. Every
feature is guaranteed to land in exactly one group (``"Other"`` is the
fallback), so newly added model columns automatically show up in all
analyses without touching hard-coded lists.
"""

from __future__ import annotations

from collections.abc import Iterable

from trading_signals.db.models.features import FEATURE_COLUMNS

OTHER_GROUP = "Other"

#: Ordered (group, name-prefixes) rules. First match wins.
GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ARK", ("ark_",)),
    ("Insider", ("insider_", "cluster_", "days_since_last_cluster")),
    ("Analyst", ("analyst_",)),
    ("Politician", ("politician_",)),
    ("13F", ("form13f_",)),
    (
        "Fundamentals",
        (
            "pe_ratio", "forward_pe", "ps_ratio", "revenue_growth",
            "profit_margin", "debt_to_equity", "pe_trend", "margin_trend",
        ),
    ),
    (
        "Technical",
        ("price_vs_", "rsi_", "relative_strength_", "volume_ratio_", "atr_"),
    ),
    ("Short Term", ("st_",)),
    (
        "Earnings",
        (
            "earnings_", "consecutive_beats", "surprise_", "sue_",
            "days_since_last_earnings",
        ),
    ),
    ("Sentiment", ("sentiment_", "market_sentiment_", "news_")),
    ("Liquidity", ("dollar_volume_", "amihud_")),
    ("Macro", ("macro_",)),
    ("Breadth", ("breadth_",)),
    ("Sector", ("sector_",)),
    ("Short Interest", ("short_",)),
    ("Options IV", ("options_",)),
    ("Estimates", ("eps_", "revenue_revision_")),
)

#: Prefixes of features that are identical for all tickers on a date
#: (market-wide). They carry no cross-sectional information; daily rank
#: ICs / cross-sectional models skip them automatically.
MARKET_WIDE_PREFIXES: tuple[str, ...] = ("macro_", "breadth_", "market_sentiment_")


def feature_group_of(feature: str) -> str:
    """Return the reporting group of a feature name (``"Other"`` fallback)."""
    for group, prefixes in GROUP_RULES:
        if feature.startswith(prefixes):
            return group
    return OTHER_GROUP


def group_features(features: Iterable[str] | None = None) -> dict[str, list[str]]:
    """Group features by :data:`GROUP_RULES`.

    Args:
        features: Feature names (defaults to ``FEATURE_COLUMNS``).

    Returns:
        Ordered ``{group: [features...]}``; empty groups are omitted,
        ``"Other"`` (if non-empty) comes last. Every input feature appears
        exactly once.
    """
    feats = list(FEATURE_COLUMNS if features is None else features)
    order = [g for g, _ in GROUP_RULES] + [OTHER_GROUP]
    grouped: dict[str, list[str]] = {g: [] for g in order}
    for f in feats:
        grouped[feature_group_of(f)].append(f)
    return {g: fs for g, fs in grouped.items() if fs}


def is_market_wide(feature: str) -> bool:
    """True for features that are constant across tickers on a date."""
    return feature.startswith(MARKET_WIDE_PREFIXES)
