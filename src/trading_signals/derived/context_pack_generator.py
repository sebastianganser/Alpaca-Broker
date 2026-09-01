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

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.config import get_settings
from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.models.fundamentals import FundamentalsSnapshot
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


# Preliminary score weights — based on Sprint 9 ML analysis top features.
# Will be replaced by ML-importance from R1 (October 2026).
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
    "breadth_advance_decline": 0.05,
    "macro_vix_regime": -0.05,  # negative = low vol is bullish
    "volume_ratio_20d": 0.04,
}

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

        # 4. Rank and select top_n (filter: must have dollar_volume_20d > 0)
        candidates = [
            s for s in scored
            if s["score"] is not None
            and s.get("dollar_volume_20d") is not None
            and float(s.get("dollar_volume_20d", 0)) > 0
        ]
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:top_n]

        if not top:
            logger.warning(
                f"[context_pack] No scorable candidates for {target_date}"
            )
            return 0

        # 5. Create output directory
        day_dir = self.output_dir / target_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)

        # 6. Generate overview
        universe_size = len(snapshots)
        self._write_overview(day_dir, target_date, top, candidates, percentiles, universe_size)

        # 7. Generate per-candidate files
        for rank, candidate in enumerate(top, 1):
            ticker = candidate["ticker"]
            self._write_candidate(
                day_dir, target_date, rank, candidate,
                percentiles.get(ticker, {}), universe_size
            )

        logger.info(
            f"[context_pack] Generated {len(top)} candidate files "
            f"for {target_date} in {day_dir}"
        )
        return len(top)

    def _load_snapshots(self, target_date: date) -> list[dict]:
        """Load all feature snapshots for a given date."""
        rows = self.session.execute(
            select(FeatureSnapshot)
            .where(FeatureSnapshot.snapshot_date == target_date)
        ).scalars().all()

        results = []
        for row in rows:
            d = {}
            for col in FeatureSnapshot.__table__.columns:
                val = getattr(row, col.name, None)
                d[col.name] = float(val) if val is not None and col.name not in ("snapshot_date", "ticker", "computed_at") and not isinstance(val, (str, bool, date)) else val
            results.append(d)
        return results

    def _compute_scores(self, snapshots: list[dict]) -> list[dict]:
        """Compute preliminary score for each snapshot."""
        for snap in snapshots:
            score = 0.0
            contributing = 0
            for feature, weight in PRELIMINARY_WEIGHTS.items():
                val = snap.get(feature)
                if val is not None and isinstance(val, (int, float)):
                    score += float(val) * weight
                    contributing += 1
            snap["score"] = round(score, 6) if contributing >= 3 else None
            snap["score_features_used"] = contributing
        return snapshots

    def _compute_percentiles(self, snapshots: list[dict]) -> dict[str, dict[str, float]]:
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

    def _write_overview(self, day_dir, target_date, top, all_candidates, percentiles, universe_size):
        """Write the daily overview file."""
        lines = [
            f"# Tagesübersicht — {target_date.isoformat()}",
            "",
            f"Universum: {universe_size} Ticker · Scorbare Kandidaten: {len(all_candidates)}",
            f"Preliminary Score (Platzhalter bis F1 Composite Score, Okt 2026)",
            "",
            "## Top-Kandidaten",
            "",
            "| Rang | Ticker | Score | Sektor | Features Used |",
            "|---|---|---|---|---|",
        ]
        for rank, c in enumerate(top, 1):
            info = self._get_universe_info(c["ticker"])
            lines.append(
                f"| {rank} | **{c['ticker']}** | {c['score']:.4f} "
                f"| {info.get('sector', '–')} | {c.get('score_features_used', 0)} |"
            )

        # Market context
        macro = top[0] if top else {}
        lines.extend([
            "",
            "## Marktkontext",
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| VIX | {macro.get('macro_vix', '–')} |",
            f"| Yield Spread (10Y-2Y) | {macro.get('macro_yield_spread', '–')} |",
            f"| HY Spread | {macro.get('macro_hy_spread', '–')} |",
            f"| Breadth (Advance/Decline) | {macro.get('breadth_advance_decline', '–')} |",
            f"| % über SMA50 | {macro.get('breadth_pct_above_sma50', '–')} |",
            f"| VIX Regime | {['Low Vol', 'Medium', 'High Vol'][int(macro.get('macro_vix_regime', 1))] if macro.get('macro_vix_regime') is not None else '–'} |",
            "",
            "## Datenqualität",
            "",
            "Preliminary Score basiert auf fest gewichteten Features.",
            "Ab Oktober 2026 (R1): ML-gewichteter Composite Score mit Guardrails.",
            "",
            "---",
            f"*Generiert {target_date.isoformat()} · Sprint 9.5c F2 MVP · Keine Anlageberatung*",
        ])

        filepath = day_dir / "00_uebersicht.md"
        filepath.write_text("\n".join(lines), encoding="utf-8")

    def _write_candidate(self, day_dir, target_date, rank, candidate, pctiles, universe_size):
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
            val_str = f"{val:.4f}" if isinstance(val, float) else str(val) if val is not None else "–"
            pct_str = f"{pct:.0f}" if pct is not None else "–"
            body.append(f"| {feature} | {val_str} | {pct_str} |")

        # Score attribution
        body.extend([
            "",
            "## 2. Score-Attribution (Preliminary Weights)",
            "",
            "| Feature | Wert | Gewicht | Beitrag |",
            "|---|---|---|---|",
        ])
        for feature, weight in sorted(PRELIMINARY_WEIGHTS.items(), key=lambda x: abs(x[1]), reverse=True):
            val = candidate.get(feature)
            if val is not None and isinstance(val, (int, float)):
                contribution = float(val) * weight
                body.append(
                    f"| {feature} | {float(val):.4f} | {weight:+.2f} "
                    f"| {contribution:+.4f} |"
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
            f"Vollständigkeit: {completeness:.0%} ({filled}/{len(DISPLAY_FEATURES)} Features verfügbar)",
            "",
            "> **Hinweis:** Preliminary Score (Sprint 9.5c MVP). Ab Oktober 2026: ML-gewichteter Composite Score.",
            "",
            "---",
            f"*Generiert {target_date.isoformat()} · Keine Anlageberatung*",
        ])

        filename = f"{rank:02d}_{ticker}.md"
        filepath = day_dir / filename
        filepath.write_text("\n".join(frontmatter + body), encoding="utf-8")
