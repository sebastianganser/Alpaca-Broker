"""Context Pack Generator — daily Markdown reports for top candidates.

Generates structured Markdown files with YAML frontmatter for each
top candidate, plus a daily overview. Designed to be consumed by
the aktien-analyse skill for qualitative research augmentation.

Output structure:
  context_packs/
  └── 2026-09-01/
      ├── 00_uebersicht.md      (ranking, market context, data quality)
      ├── 01_NVDA.md            (YAML frontmatter + feature data)
      ├── 02_MSFT.md
      └── ...

The Context Pack contains what web research CANNOT provide:
  - Cross-sectional percentiles over our own universe
  - Point-in-time history since March/April 2026
  - Signal stack with availability dates
  - Feature attribution (preliminary, until F1 composite score)

Sprint 9.5c F2. MVP version — uses preliminary score weights
until R1/F1 provide ML-based importance rankings.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_signals.config import get_settings
from trading_signals.db.models.features import (
    KEY_COLUMNS,
    META_COLUMNS,
    FeatureSnapshot,
)
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


# PROVISIONAL score weights — NOT validated.
# Originally taken from the Sprint 9 ML analysis top features, but that
# analysis was biased (pooled correlations, row-iid tests, leaky CV; see
# review finding H4). Treat these purely as a hand-tuned placeholder until
# the rebuilt analysis (daily rank IC / purged CV) or R1/F1 provide
# validated importances.
#
# Weights are applied to per-date cross-sectional percentile ranks
# (centered: pct_rank - 0.5), never to raw values, so the scale of a
# feature does not matter. Market-wide features (macro_*, breadth_*) were
# removed: they are identical for all tickers on a date and cannot rank
# candidates (they would be skipped automatically anyway).
PRELIMINARY_WEIGHTS = {
    "analyst_rating_score": 0.15,
    "insider_net_buy_count_30d": 0.12,
    "ark_conviction_score": 0.10,
    "price_vs_sma50": 0.10,
    "sentiment_momentum": 0.08,
    "sue_last": 0.08,
    "rsi_14": -0.07,  # negative = oversold is bullish
    "relative_strength_spy": 0.10,
    "sector_relative_return_20d": 0.08,
    "volume_ratio_20d": 0.04,
}

#: Minimum number of contributing (non-constant, non-null) features.
MIN_CONTRIBUTING_FEATURES = 3

# Stage-1 risk pre-filter (concept §7.5). Same definition as the K1
# barrier backtest (scripts/analysis/backtest_barrier.py): the daily range
# must fit the +1 % / −2 % barriers and no earnings may fall inside the
# 14-session holding window (≈ 14 × 7/5 calendar days + 1 for the entry gap).
FILTER_ATR_MIN = 0.01
FILTER_ATR_MAX = 0.03
EARNINGS_BLOCK_CAL_DAYS = 21


def prefilter_reasons(snap: dict) -> list[str]:
    """Reasons why a snapshot fails the stage-1 risk pre-filter (pure).

    Returns an empty list if the ticker is eligible. Missing ATR or
    dollar volume excludes the ticker (unknown risk); an unknown earnings
    date does not (most tickers without a scheduled date have none soon).
    """
    reasons: list[str] = []
    dv = _numeric(snap.get("dollar_volume_20d"))
    if dv is None or dv <= 0:
        reasons.append("keine Liquidität")
    atr = _numeric(snap.get("atr_14_pct"))
    if atr is None:
        reasons.append("ATR fehlt")
    elif atr < FILTER_ATR_MIN:
        reasons.append("ATR < 1 %")
    elif atr > FILTER_ATR_MAX:
        reasons.append("ATR > 3 %")
    earn = _numeric(snap.get("earnings_days_until"))
    if earn is not None and 0 <= earn <= EARNINGS_BLOCK_CAL_DAYS:
        reasons.append("Earnings im Haltefenster")
    return reasons


def _numeric(val: Any) -> float | None:
    """Float value of a snapshot entry (bool -> 0/1), None if not numeric."""
    if val is None or isinstance(val, (str, date)):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def compute_preliminary_scores(
    snapshots: list[dict],
    weights: dict[str, float] | None = None,
    min_features: int = MIN_CONTRIBUTING_FEATURES,
) -> list[dict]:
    """Rank-based preliminary score for one date's snapshots (pure function).

    For every weighted feature the cross-sectional percentile rank over the
    snapshots that have a value is computed (average ties,
    ``(rank-1)/(n-1)``) and centered (``- 0.5`` -> range [-0.5, 0.5]).
    The score is ``sum(weight * centered_rank)`` over the features a ticker
    has; missing features count as neutral (0).

    Features with fewer than 2 distinct values on that date (e.g.
    market-wide features, or all-equal values) carry no information and get
    NO weight (they do not count as contributing).

    Args:
        snapshots: Feature dicts of ONE snapshot date (must contain "ticker").
        weights: Feature -> weight (default :data:`PRELIMINARY_WEIGHTS`).
        min_features: Tickers with fewer contributing features get
            ``score = None``.

    Returns:
        New dicts (input is not mutated) with ``score``,
        ``score_features_used`` and ``score_contributions``
        (``{feature: {value, pct_rank, weight, contribution}}``).
    """
    weights = PRELIMINARY_WEIGHTS if weights is None else weights
    out = [dict(s) for s in snapshots]

    # feature -> {row index: centered pct rank}
    ranks: dict[str, dict[int, float]] = {}
    for feature in weights:
        vals = [(i, _numeric(s.get(feature))) for i, s in enumerate(out)]
        vals = [(i, v) for i, v in vals if v is not None]
        if len({v for _, v in vals}) < 2:
            continue  # constant / empty on this date -> no weight
        sorted_vals = sorted(v for _, v in vals)
        n = len(sorted_vals)
        # average rank for ties (1-based)
        first_pos: dict[float, int] = {}
        counts: dict[float, int] = {}
        for pos, v in enumerate(sorted_vals, start=1):
            first_pos.setdefault(v, pos)
            counts[v] = counts.get(v, 0) + 1
        ranks[feature] = {
            i: ((first_pos[v] + (counts[v] - 1) / 2.0) - 1.0) / (n - 1) - 0.5
            for i, v in vals
        }

    for i, snap in enumerate(out):
        contributions: dict[str, dict[str, float]] = {}
        score = 0.0
        for feature, weight in weights.items():
            r = ranks.get(feature, {}).get(i)
            if r is None:
                continue
            contrib = weight * r
            score += contrib
            contributions[feature] = {
                "value": _numeric(snap.get(feature)),
                "pct_rank": r + 0.5,
                "weight": weight,
                "contribution": contrib,
            }
        enough = len(contributions) >= min_features
        snap["score"] = round(score, 6) if enough else None
        snap["score_features_used"] = len(contributions)
        snap["score_contributions"] = contributions
    return out

# Features used for display in candidate files
DISPLAY_FEATURES = [
    "analyst_rating_score", "analyst_upgrades_30d", "analyst_price_target_upside",
    "insider_net_buy_count_30d", "insider_buy_ratio_90d", "insider_cluster_active",
    "ark_conviction_score", "ark_weight_delta_20d",
    "price_vs_sma50", "price_vs_sma200", "rsi_14", "relative_strength_spy",
    "sentiment_avg_7d", "sentiment_momentum", "news_volume_ratio_7d",
    "sue_last", "days_since_last_earnings", "earnings_days_until",
    "pe_ratio", "forward_pe", "revenue_growth_yoy", "profit_margin",
    "macro_vix", "macro_yield_spread", "macro_hy_spread",
    "breadth_advance_decline", "breadth_pct_above_sma50",
    "sector_relative_return_20d", "sector_relative_momentum",
    "dollar_volume_20d", "amihud_illiquidity_20d",
    "volume_ratio_20d", "atr_14_pct",
    "st_return_1d", "st_return_5d", "st_gap", "st_dist_52w_high", "st_rsi_2",
    "st_earnings_reaction",
]


class ContextPackGenerator:
    """Generates daily Context Pack Markdown files for top candidates."""

    def __init__(self, session: Session, output_dir: str | None = None):
        self.session = session
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            settings = get_settings()
            self.output_dir = Path(settings.CONTEXT_PACK_PATH)

    def generate_daily(self, target_date: date, top_n: int = 5) -> int:
        """Generate Context Pack for top_n candidates on target_date.

        Returns:
            Number of candidate files written.
        """
        # 1. Load all feature snapshots for target_date
        snapshots = self._load_snapshots(target_date)
        if not snapshots:
            logger.warning(
                f"[context_pack] No feature snapshots for {target_date} — skipping"
            )
            return 0

        # 2. Compute preliminary score for each ticker
        scored = self._compute_scores(snapshots)

        # 3. Compute cross-sectional percentiles
        percentiles = self._compute_percentiles(snapshots)

        # 4. Stage-1 risk pre-filter (concept §7.5), then rank the eligible
        #    tickers by the (unvalidated) preliminary score
        excluded: dict[str, int] = {}
        candidates = []
        for s in scored:
            reasons = prefilter_reasons(s)
            if s["score"] is None:
                reasons = [*reasons, "zu wenige Kennzahlen"]
            for r in reasons:
                excluded[r] = excluded.get(r, 0) + 1
            if not reasons:
                candidates.append(s)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:top_n]

        # 5. Create output directory (drop candidate files of earlier runs)
        day_dir = self.output_dir / target_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        for old in day_dir.glob("[0-9][0-9]_*.md"):
            if old.name != "00_uebersicht.md":
                old.unlink()

        # 6. Generate overview (also on days without candidates)
        universe_size = len(snapshots)
        self._write_overview(
            day_dir, target_date, top, candidates, percentiles, universe_size,
            excluded=excluded, market=snapshots[0],
        )

        if not top:
            logger.warning(
                f"[context_pack] No eligible candidates for {target_date} "
                f"(excluded: {excluded})"
            )
            return 0

        # 7. Generate per-candidate files
        for rank, candidate in enumerate(top, 1):
            ticker = candidate["ticker"]
            self._write_candidate(
                day_dir, target_date, rank, candidate,
                percentiles.get(ticker, {}), universe_size
            )

        logger.info(
            f"[context_pack] Generated {len(top)} candidate files "
            f"for {target_date} in {day_dir} ({len(candidates)} eligible)"
        )
        return len(top)

    def _load_snapshots(self, target_date: date) -> list[dict]:
        """Load all feature snapshots for a given date.

        Key and metadata columns (snapshot_date, ticker, computed_at,
        feature_version) keep their native types; numeric feature/target
        values (Decimal/int) become float, bools stay bool.
        """
        rows = self.session.execute(
            select(FeatureSnapshot)
            .where(FeatureSnapshot.snapshot_date == target_date)
        ).scalars().all()

        passthrough = set(KEY_COLUMNS) | set(META_COLUMNS)
        results = []
        for row in rows:
            d = {}
            for col in FeatureSnapshot.__table__.columns:
                val = getattr(row, col.name, None)
                convert = (
                    val is not None
                    and col.name not in passthrough
                    and not isinstance(val, (str, bool, date))
                )
                d[col.name] = float(val) if convert else val
            results.append(d)
        return results

    def _compute_scores(self, snapshots: list[dict]) -> list[dict]:
        """Compute the rank-based preliminary score for each snapshot."""
        return compute_preliminary_scores(snapshots, PRELIMINARY_WEIGHTS)

    def _compute_percentiles(
        self, snapshots: list[dict]
    ) -> dict[str, dict[str, float]]:
        """Compute cross-sectional percentiles for each feature.

        Returns: {ticker: {feature_name: percentile_0_to_100, ...}}
        """
        if not snapshots:
            return {}

        # For each feature, collect all non-None values and compute rank
        feature_names = [f for f in DISPLAY_FEATURES if any(
            s.get(f) is not None for s in snapshots
        )]

        result: dict[str, dict[str, float]] = {s["ticker"]: {} for s in snapshots}

        for feature in feature_names:
            values = []
            for s in snapshots:
                v = s.get(feature)
                if v is not None and isinstance(v, (int, float)):
                    values.append((s["ticker"], float(v)))

            if len(values) < 5:
                continue

            # Sort and assign percentile rank
            sorted_vals = sorted(values, key=lambda x: x[1])
            n = len(sorted_vals)
            for rank_idx, (ticker, val) in enumerate(sorted_vals):
                pctile = round(rank_idx / (n - 1) * 100, 1) if n > 1 else 50.0
                result[ticker][feature] = pctile

        return result

    def _get_universe_info(self, ticker: str) -> dict:
        """Get sector/industry/company info from universe table."""
        row = self.session.execute(
            select(Universe.sector, Universe.industry)
            .where(Universe.ticker == ticker)
        ).first()
        return {
            "sector": row[0] if row else None,
            "industry": row[1] if row else None,
        }

    def _get_latest_price(self, ticker: str, target_date: date) -> float | None:
        """Get the most recent closing price."""
        val = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= target_date)
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()
        return float(val) if val else None

    def _write_overview(
        self, day_dir, target_date, top, all_candidates, percentiles, universe_size,
        excluded: dict[str, int] | None = None, market: dict | None = None,
    ):
        """Write the daily overview file (also on days without candidates)."""
        lines = [
            f"# Tagesübersicht — {target_date.isoformat()}",
            "",
            f"Universum: {universe_size} Ticker · "
            f"Geeignet nach Risiko-Vorfilter: {len(all_candidates)}",
            "",
            "## Top-Kandidaten",
            "",
        ]
        if top:
            lines.extend([
                "| Rang | Ticker | Score | Sektor | Features Used |",
                "|---|---|---|---|---|",
            ])
        else:
            lines.append(
                "**Heute keine Kandidaten** – keine Aktie erfüllt den "
                "Risiko-Vorfilter."
            )
        for rank, c in enumerate(top, 1):
            info = self._get_universe_info(c["ticker"])
            lines.append(
                f"| {rank} | **{c['ticker']}** | {c['score']:.4f} "
                f"| {info.get('sector', '–')} | {c.get('score_features_used', 0)} |"
            )

        # Pre-filter summary
        lines.extend([
            "",
            "## Risiko-Vorfilter",
            "",
            "Geeignet = Dollar-Volumen > 0, ATR(14) zwischen 1 % und 3 %, "
            f"keine Quartalszahlen in den nächsten {EARNINGS_BLOCK_CAL_DAYS} "
            "Kalendertagen (Ziel +1 % / Stop −2 % / max. 14 Handelstage).",
            "",
        ])
        if excluded:
            lines.extend(["| Ausschlussgrund | Ticker |", "|---|---|"])
            for reason, count in sorted(excluded.items(), key=lambda kv: -kv[1]):
                lines.append(f"| {reason} | {count} |")

        # Market context (market-wide features are identical for all tickers)
        macro = market or (top[0] if top else {})
        regime = macro.get('macro_vix_regime')
        regime_str = (
            ['Low Vol', 'Medium', 'High Vol'][int(regime)]
            if regime is not None else '–'
        )
        lines.extend([
            "",
            "## Marktkontext",
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| VIX | {macro.get('macro_vix', '–')} |",
            f"| Yield Spread (10Y-2Y) | {macro.get('macro_yield_spread', '–')} |",
            f"| HY Spread | {macro.get('macro_hy_spread', '–')} |",
            "| Breadth (Advance/Decline) | "
            f"{macro.get('breadth_advance_decline', '–')} |",
            f"| % über SMA50 | {macro.get('breadth_pct_above_sma50', '–')} |",
            f"| VIX Regime | {regime_str} |",
            "",
            "## Datenqualität",
            "",
            "Die Vorauswahl ist ein **Risikofilter, keine Vorhersage**. "
            "Die Reihenfolge nach dem",
            "Preliminary Score (gewichtete Perzentil-Ränge, vorläufige Gewichte) "
            "ist **nicht validiert**:",
            "Im Walk-forward-Test (2026-10-06) konnte kein Modell Aktien besser "
            "als Zufall ordnen.",
            "Die eigentliche Auswahl erfolgt in Stufe 2 mit Live-Daten.",
            "",
            "---",
            f"*Generiert {target_date.isoformat()} · Keine Anlageberatung*",
        ])

        filepath = day_dir / "00_uebersicht.md"
        filepath.write_text("\n".join(lines), encoding="utf-8-sig")

    def _write_candidate(
        self, day_dir, target_date, rank, candidate, pctiles, universe_size
    ):
        """Write a single candidate Markdown file with YAML frontmatter."""
        ticker = candidate["ticker"]
        info = self._get_universe_info(ticker)
        close = self._get_latest_price(ticker, target_date)

        # YAML frontmatter
        frontmatter = [
            "---",
            f"ticker: {ticker}",
            f"as_of: {target_date.isoformat()}",
            f"rank: {rank}",
            f"preliminary_score: {candidate['score']:.6f}",
            f"universe_size: {universe_size}",
            f"sector: {info.get('sector', 'Unknown')}",
            f"industry: {info.get('industry', 'Unknown')}",
        ]
        if close:
            frontmatter.append(f"close: {close:.2f}")
        dv = candidate.get("dollar_volume_20d")
        if dv:
            frontmatter.append(f"adv_20d_musd: {float(dv) / 1_000_000:.0f}")
        ed = candidate.get("earnings_days_until")
        if ed is not None:
            frontmatter.append(f"earnings_in_days: {int(ed)}")
        atr = _numeric(candidate.get("atr_14_pct"))
        if atr is not None:
            frontmatter.append(f"atr_14_pct: {atr:.4f}")
        frontmatter.append("selection: risk_prefilter")
        frontmatter.append("ranking_validated: false")

        # Data completeness: fraction of display features that have values
        filled = sum(1 for f in DISPLAY_FEATURES if candidate.get(f) is not None)
        completeness = round(filled / len(DISPLAY_FEATURES), 2)
        frontmatter.append(f"data_completeness: {completeness}")
        frontmatter.append("---")

        # Body
        body = [
            "",
            f"# {ticker} — Context Pack, {target_date.isoformat()}",
            "",
            f"Rang {rank} · Preliminary Score {candidate['score']:.4f}",
            "",
            "## 1. Feature-Übersicht",
            "",
            "| Feature | Wert | Perzentil |",
            "|---|---|---|",
        ]

        for feature in DISPLAY_FEATURES:
            val = candidate.get(feature)
            pct = pctiles.get(feature)
            if isinstance(val, float):
                val_str = f"{val:.4f}"
            else:
                val_str = str(val) if val is not None else "–"
            pct_str = f"{pct:.0f}" if pct is not None else "–"
            body.append(f"| {feature} | {val_str} | {pct_str} |")

        # Score attribution (rank-based; contributions sum to the score)
        body.extend([
            "",
            "## 2. Score-Attribution (vorläufige Gewichte, nicht validiert)",
            "",
            "Beitrag = Gewicht × (Perzentil-Rang im Tagesquerschnitt − 0.5). "
            "Features ohne Querschnitts-Varianz an diesem Tag zählen nicht.",
            "",
            "| Feature | Wert | Perzentil-Rang | Gewicht | Beitrag |",
            "|---|---|---|---|---|",
        ])
        contributions = candidate.get("score_contributions", {})
        for feature, c in sorted(
            contributions.items(),
            key=lambda kv: abs(kv[1]["contribution"]),
            reverse=True,
        ):
            val = c.get("value")
            val_str = f"{val:.4f}" if val is not None else "–"
            body.append(
                f"| {feature} | {val_str} | {c['pct_rank'] * 100:.0f} "
                f"| {c['weight']:+.2f} | {c['contribution']:+.4f} |"
            )

        # Market context
        body.extend([
            "",
            "## 3. Marktkontext",
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| VIX | {candidate.get('macro_vix', '–')} |",
            f"| Yield Spread | {candidate.get('macro_yield_spread', '–')} |",
            f"| HY Spread | {candidate.get('macro_hy_spread', '–')} |",
            f"| Breadth A/D | {candidate.get('breadth_advance_decline', '–')} |",
            f"| % > SMA50 | {candidate.get('breadth_pct_above_sma50', '–')} |",
            "",
            "## 4. Datenqualität",
            "",
            f"Vollständigkeit: {completeness:.0%} "
            f"({filled}/{len(DISPLAY_FEATURES)} Features verfügbar)",
            "",
            "> **Hinweis:** Auswahl = Risiko-Vorfilter (ATR 1–3 %, keine "
            "Earnings im Haltefenster). Die Reihenfolge nach dem Preliminary "
            "Score ist nicht validiert (Walk-forward 2026-10-06 ohne Vorsprung).",
            "",
            "---",
            f"*Generiert {target_date.isoformat()} · Keine Anlageberatung*",
        ])

        filename = f"{rank:02d}_{ticker}.md"
        filepath = day_dir / filename
        filepath.write_text("\n".join(frontmatter + body), encoding="utf-8-sig")
