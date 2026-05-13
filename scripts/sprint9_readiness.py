"""Sprint 9 Readiness Check — Feature ↔ Return Correlation Readiness.

Validates all prerequisites for Sprint 9 (Exploratory Analysis):
1. Feature snapshot volume & continuity (gap detection)
2. Target return fill rates per horizon
3. Temporal feature maturity (rolling windows need history)
4. 13F quarterly data presence
5. Overall statistical readiness

Run inside the Docker container:
    docker exec -it alpaca-broker uv run python scripts/sprint9_readiness.py

Or locally:
    uv run python scripts/sprint9_readiness.py
"""

from datetime import date, timedelta

from sqlalchemy import func, text

from trading_signals.config import DATA_START_DATE
from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session

today = date.today()

# ── Minimum thresholds for Sprint 9 ──────────────────────────────────

MIN_SNAPSHOT_DAYS = 60          # At least 60 trading days of feature snapshots
MIN_RETURN_1D_PCT = 90.0        # 1d returns should be mostly filled
MIN_RETURN_5D_PCT = 80.0        # 5d returns
MIN_RETURN_20D_PCT = 50.0       # 20d returns (needs ~1 month)
MIN_RETURN_60D_PCT = 30.0       # 60d returns (needs ~3 months, relax threshold)
MIN_TICKER_COVERAGE = 600       # At least 600 tickers with snapshots
MIN_13F_QUARTERS = 1            # At least 1 quarter of 13F data post-pipeline start
MAX_GAP_DAYS = 5                # Max allowed gap between consecutive snapshot dates


def safe_scalar(s, sql, label):
    try:
        return s.execute(text(sql)).scalar()
    except Exception as e:
        s.rollback()
        print(f"  [{label}] Query failed: {e.__class__.__name__}")
        return None


def safe_query(s, sql, label):
    try:
        return s.execute(text(sql)).fetchall()
    except Exception as e:
        s.rollback()
        print(f"  [{label}] Query failed: {e.__class__.__name__}")
        return None


def safe_first(s, sql, label):
    try:
        return s.execute(text(sql)).first()
    except Exception as e:
        s.rollback()
        print(f"  [{label}] Query failed: {e.__class__.__name__}")
        return None


def check_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


def pct_icon(pct: float, threshold: float) -> str:
    if pct >= threshold:
        return "✅"
    elif pct >= threshold * 0.5:
        return "⚠️"
    else:
        return "❌"


def _next_quarter_end() -> date:
    """Return the end date of the current or next quarter."""
    q_ends = [
        date(today.year, 3, 31),
        date(today.year, 6, 30),
        date(today.year, 9, 30),
        date(today.year, 12, 31),
    ]
    for qe in q_ends:
        if qe >= today:
            return qe
    return date(today.year + 1, 3, 31)


with get_session() as s:
    blockers = []
    warnings = []
    readiness_dates = []  # (metric_name, earliest_ready_date)

    # ── Header ───────────────────────────────────────────────────────
    active = s.query(func.count()).select_from(Universe).filter(
        Universe.is_active.is_(True)
    ).scalar()

    print(f"\n{'=' * 72}")
    print(f"  SPRINT 9 READINESS CHECK — Exploratory Analysis")
    print(f"  Date: {today}  |  Active Universe: {active} tickers")
    print(f"{'=' * 72}")

    # ══════════════════════════════════════════════════════════════════
    # 1. FEATURE SNAPSHOT VOLUME & CONTINUITY
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  1. FEATURE SNAPSHOTS — Volume & Continuity")
    print(f"{'─' * 72}")

    snap_count = safe_scalar(
        s, "SELECT COUNT(*) FROM signals.feature_snapshots", "snap_count"
    ) or 0
    snap_min = safe_scalar(
        s, "SELECT MIN(snapshot_date) FROM signals.feature_snapshots", "snap_min"
    )
    snap_max = safe_scalar(
        s, "SELECT MAX(snapshot_date) FROM signals.feature_snapshots", "snap_max"
    )
    snap_distinct_dates = safe_scalar(
        s,
        "SELECT COUNT(DISTINCT snapshot_date) FROM signals.feature_snapshots",
        "snap_dates",
    ) or 0
    snap_distinct_tickers = safe_scalar(
        s,
        "SELECT COUNT(DISTINCT ticker) FROM signals.feature_snapshots",
        "snap_tickers",
    ) or 0

    days_ok = snap_distinct_dates >= MIN_SNAPSHOT_DAYS
    ticker_ok = snap_distinct_tickers >= MIN_TICKER_COVERAGE

    print(f"  Total snapshots:     {snap_count:>10,}")
    print(f"  Distinct dates:      {snap_distinct_dates:>10}  "
          f"{check_icon(days_ok)}  (min: {MIN_SNAPSHOT_DAYS})")
    print(f"  Distinct tickers:    {snap_distinct_tickers:>10}  "
          f"{check_icon(ticker_ok)}  (min: {MIN_TICKER_COVERAGE})")
    print(f"  Date range:          {snap_min} → {snap_max}")

    if not days_ok:
        days_needed = MIN_SNAPSHOT_DAYS - snap_distinct_dates
        # ~5 trading days per calendar week
        cal_days = int(days_needed * 7 / 5) + 1
        target = today + timedelta(days=cal_days)
        blockers.append(f"Nur {snap_distinct_dates}/{MIN_SNAPSHOT_DAYS} Snapshot-Tage")
        readiness_dates.append(("Feature Snapshots", target))

    if not ticker_ok:
        blockers.append(
            f"Nur {snap_distinct_tickers}/{MIN_TICKER_COVERAGE} Ticker mit Snapshots"
        )

    # ── Gap Detection ────────────────────────────────────────────────
    print(f"\n  Gap Detection:")
    gap_rows = safe_query(
        s,
        """
        WITH dates AS (
            SELECT DISTINCT snapshot_date FROM signals.feature_snapshots
            ORDER BY snapshot_date
        ),
        gaps AS (
            SELECT
                snapshot_date AS gap_start,
                LEAD(snapshot_date) OVER (ORDER BY snapshot_date) AS gap_end,
                LEAD(snapshot_date) OVER (ORDER BY snapshot_date) - snapshot_date AS gap_days
            FROM dates
        )
        SELECT gap_start, gap_end, gap_days
        FROM gaps
        WHERE gap_days > 3
        ORDER BY gap_days DESC
        LIMIT 10
        """,
        "gaps",
    ) or []

    if gap_rows:
        for row in gap_rows:
            severity = "❌" if row[2] > MAX_GAP_DAYS else "⚠️"
            print(f"    {severity} {row[0]} → {row[1]}  ({row[2]} Tage Lücke)")
            if row[2] > MAX_GAP_DAYS:
                blockers.append(
                    f"Snapshot-Lücke: {row[0]} → {row[1]} ({row[2]} Tage)"
                )
    else:
        if snap_distinct_dates > 1:
            print(f"    ✅ Keine Lücken > 3 Tage erkannt")
        else:
            print(f"    ⏳ Noch nicht genug Datenpunkte für Gap-Analyse")

    # ── Ticker consistency check ─────────────────────────────────────
    print(f"\n  Ticker-Konsistenz:")
    consistency = safe_first(
        s,
        """
        SELECT
            AVG(ticker_count) AS avg_tickers,
            MIN(ticker_count) AS min_tickers,
            MAX(ticker_count) AS max_tickers
        FROM (
            SELECT snapshot_date, COUNT(DISTINCT ticker) AS ticker_count
            FROM signals.feature_snapshots
            GROUP BY snapshot_date
        ) sub
        """,
        "consistency",
    )
    if consistency and consistency[0]:
        avg_t = float(consistency[0])
        min_t = consistency[1]
        max_t = consistency[2]
        spread = max_t - min_t
        spread_ok = spread < 50
        print(f"    Ø Ticker/Tag: {avg_t:.0f}  |  Min: {min_t}  |  Max: {max_t}  "
              f"{check_icon(spread_ok)}")
        if not spread_ok:
            warnings.append(
                f"Ticker-Spread pro Tag: {min_t}–{max_t} (Ø {avg_t:.0f})"
            )
    else:
        print(f"    ⏳ Noch keine Daten")

    # ══════════════════════════════════════════════════════════════════
    # 2. TARGET RETURN FILL RATES
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  2. TARGET RETURNS — Forward Return Fill Rates")
    print(f"{'─' * 72}")

    if snap_count > 0:
        for col, label, threshold, trading_days in [
            ("return_1d", "1d", MIN_RETURN_1D_PCT, 1),
            ("return_5d", "5d", MIN_RETURN_5D_PCT, 5),
            ("return_20d", "20d", MIN_RETURN_20D_PCT, 20),
            ("return_60d", "60d", MIN_RETURN_60D_PCT, 60),
        ]:
            r = safe_first(
                s,
                f"""
                SELECT
                    COUNT({col}) AS filled,
                    COUNT(*) AS total,
                    ROUND(100.0 * COUNT({col}) / NULLIF(COUNT(*), 0), 1) AS pct,
                    AVG({col}) AS mean_return,
                    STDDEV({col}) AS std_return
                FROM signals.feature_snapshots
                """,
                f"return_{label}",
            )
            if r:
                filled = r[0] or 0
                total = r[1] or 0
                pct = float(r[2] or 0)
                mean_r = f"{float(r[3]) * 100:.3f}%" if r[3] else "—"
                std_r = f"{float(r[4]) * 100:.3f}%" if r[4] else "—"
                icon = pct_icon(pct, threshold)

                print(f"    {label:>4}:  {filled:>8,} / {total:>8,}  "
                      f"({pct:5.1f}%)  {icon}  "
                      f"Ø={mean_r:>8}  σ={std_r:>8}  "
                      f"(min: {threshold}%)")

                if pct < threshold:
                    # Estimate when we'll have enough
                    cal_days = int(trading_days * 7 / 5) + 3  # buffer
                    target = today + timedelta(days=cal_days)
                    if snap_min:
                        # Better estimate: first snapshot + horizon + buffer
                        target = max(target, snap_min + timedelta(
                            days=int(trading_days * 7 / 5) + 7
                        ))
                    readiness_dates.append((f"return_{label}", target))
                    if threshold >= 50:
                        blockers.append(
                            f"return_{label}: {pct:.1f}% (braucht {threshold}%)"
                        )
                    else:
                        warnings.append(
                            f"return_{label}: {pct:.1f}% (Ziel: {threshold}%)"
                        )
    else:
        print(f"    ⏳ Noch keine Feature-Snapshots vorhanden")
        blockers.append("Keine Feature-Snapshots")

    # ══════════════════════════════════════════════════════════════════
    # 3. TEMPORAL FEATURE MATURITY
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  3. TEMPORAL FEATURES — Rolling Window Maturity")
    print(f"{'─' * 72}")

    temporal_checks = [
        ("ark_weight_trend_20d", "ARK 20d Trend", 20),
        ("ark_conviction_streak", "ARK Conviction Streak", 10),
        ("cluster_count_60d", "Insider Cluster 60d", 60),
        ("analyst_net_sentiment_60d", "Analyst Sentiment 60d", 60),
        ("pe_trend_4w", "PE Trend 4w", 28),
        ("sentiment_avg_7d", "News Sentiment 7d", 7),
        ("sentiment_avg_30d", "News Sentiment 30d", 30),
        ("sentiment_momentum", "Sentiment Momentum", 30),
    ]

    for col, label, min_days in temporal_checks:
        r = safe_first(
            s,
            f"""
            SELECT
                COUNT({col}) AS filled,
                COUNT(*) AS total,
                ROUND(100.0 * COUNT({col}) / NULLIF(COUNT(*), 0), 1) AS pct
            FROM signals.feature_snapshots
            WHERE snapshot_date = (
                SELECT MAX(snapshot_date) FROM signals.feature_snapshots
            )
            """,
            f"temporal_{col}",
        )
        if r and r[1] and r[1] > 0:
            pct = float(r[2] or 0)
            icon = "✅" if pct >= 20 else "⚠️" if pct > 0 else "❌"
            print(f"    {label:<28} {r[0]:>5}/{r[1]:<5}  ({pct:5.1f}%)  {icon}  "
                  f"(benötigt ~{min_days} Tage)")
        else:
            print(f"    {label:<28} —                    ⏳")

    # ══════════════════════════════════════════════════════════════════
    # 4. 13F QUARTERLY DATA
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  4. 13F HOLDINGS — Quarterly Filing Presence")
    print(f"{'─' * 72}")

    f13_quarters = safe_query(
        s,
        """
        SELECT
            report_period,
            COUNT(DISTINCT filer_name) AS filers,
            COUNT(*) AS holdings,
            COUNT(DISTINCT ticker) AS tickers
        FROM signals.form13f_holdings
        WHERE report_period >= '2026-01-01'
        GROUP BY report_period
        ORDER BY report_period DESC
        LIMIT 4
        """,
        "13f_quarters",
    ) or []

    if f13_quarters:
        for row in f13_quarters:
            print(f"    Q ending {row[0]}:  {row[1]:>3} Filer  |  "
                  f"{row[2]:>6,} Holdings  |  {row[3]:>4} Tickers")

        # Check if we have data from a quarter AFTER pipeline start
        latest_13f = f13_quarters[0][0] if f13_quarters else None
        if snap_min and latest_13f and latest_13f >= snap_min:
            print(f"    ✅ 13F-Daten vorhanden nach Pipeline-Start ({snap_min})")
        else:
            next_q_end = _next_quarter_end()
            filing_deadline = next_q_end + timedelta(days=45)
            warnings.append(
                f"Kein 13F-Quarter nach Pipeline-Start. Nächste Deadline: ~{filing_deadline}"
            )
            print(f"    ⚠️ Kein 13F-Quarter nach Pipeline-Start")
            print(f"       Nächste Quartalsmeldungen erwartet: ~{filing_deadline}")
    else:
        print(f"    ❌ Keine 13F-Daten in 2026 gefunden")
        warnings.append("Keine 13F-Daten in 2026")

    # ══════════════════════════════════════════════════════════════════
    # 4b. NEWS SENTIMENT DATA HEALTH (Sprint 8c)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  4b. NEWS SENTIMENT — Article & Scoring Coverage")
    print(f"{'─' * 72}")

    news_stats = safe_first(
        s,
        """
        SELECT
            COUNT(*) AS total_articles,
            COUNT(DISTINCT CASE WHEN NOT is_global THEN article_id END) AS ticker_articles,
            COUNT(DISTINCT CASE WHEN is_global THEN article_id END) AS global_articles,
            MIN(published_at)::date AS earliest,
            MAX(published_at)::date AS latest
        FROM signals.news_articles
        """,
        "news_stats",
    )

    if news_stats and news_stats[0] and news_stats[0] > 0:
        print(f"    Total Articles:    {news_stats[0]:>8,}")
        print(f"    Ticker-specific:   {news_stats[1]:>8,}")
        print(f"    Global/Market:     {news_stats[2]:>8,}")
        print(f"    Date range:        {news_stats[3]} → {news_stats[4]}")

        # Scoring coverage
        scoring = safe_first(
            s,
            """
            SELECT
                COUNT(DISTINCT ns.article_id) AS scored,
                (SELECT COUNT(*) FROM signals.news_articles) AS total,
                ROUND(100.0 * COUNT(DISTINCT ns.article_id)
                    / NULLIF((SELECT COUNT(*) FROM signals.news_articles), 0), 1) AS pct,
                COUNT(DISTINCT ns.ticker) AS tickers_with_sentiment
            FROM signals.news_sentiment ns
            """,
            "scoring_coverage",
        )
        if scoring:
            scored = scoring[0] or 0
            total = scoring[1] or 0
            pct = float(scoring[2] or 0)
            tickers = scoring[3] or 0
            score_ok = pct >= 90
            print(f"    Scored:            {scored:>8,} / {total:>8,}  "
                  f"({pct:.1f}%)  {check_icon(score_ok)}")
            print(f"    Tickers w/ score:  {tickers:>8,}")
            if pct < 50:
                warnings.append(f"Nur {pct:.0f}% der News-Artikel sind scored")

        # Model version distribution
        models = safe_query(
            s,
            """
            SELECT model_version, COUNT(*) AS cnt
            FROM signals.news_sentiment
            GROUP BY model_version
            ORDER BY cnt DESC
            """,
            "model_versions",
        ) or []
        if models:
            print(f"    Model versions:")
            for m in models:
                print(f"      {m[0]}: {m[1]:,} scores")
    else:
        print(f"    ⏳ Noch keine News-Artikel gesammelt")
        print(f"       News Collector startet täglich um 00:00 CET")

    # ══════════════════════════════════════════════════════════════════
    # 5. RAW DATA SOURCE HEALTH
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  5. RAW DATA HEALTH — Collector Continuity")
    print(f"{'─' * 72}")

    # Check if recent runs were successful
    recent_runs = safe_query(
        s,
        """
        SELECT
            collector_name,
            MAX(started_at)::date AS last_run,
            COUNT(*) FILTER (WHERE status = 'success'
                            AND started_at > NOW() - INTERVAL '7 days') AS ok_7d,
            COUNT(*) FILTER (WHERE status = 'error'
                            AND started_at > NOW() - INTERVAL '7 days') AS err_7d
        FROM signals.collection_log
        GROUP BY collector_name
        ORDER BY collector_name
        """,
        "collector_health",
    ) or []

    for row in recent_runs:
        name = row[0]
        last = row[1]
        ok = row[2]
        err = row[3]
        stale = (today - last).days if last else 999
        icon = "✅" if stale <= 2 and err == 0 else "⚠️" if stale <= 7 else "❌"
        err_str = f"  ⚠️ {err} errors" if err > 0 else ""
        print(f"    {icon} {name:<30}  last: {last}  "
              f"({ok} ok / 7d){err_str}")

        if stale > 7:
            warnings.append(f"Collector '{name}' zuletzt vor {stale} Tagen")

    # ══════════════════════════════════════════════════════════════════
    # 6. PRICE DATA CONTINUITY (basis for returns)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 72}")
    print(f"  6. PRICE DATA — Continuity for Return Calculation")
    print(f"{'─' * 72}")

    price_latest = safe_scalar(
        s,
        "SELECT MAX(trade_date) FROM signals.prices_daily",
        "price_latest",
    )
    price_tickers = safe_scalar(
        s,
        """
        SELECT COUNT(DISTINCT ticker) FROM signals.prices_daily
        WHERE trade_date >= CURRENT_DATE - INTERVAL '7 days'
        """,
        "price_recent_tickers",
    )

    if price_latest:
        stale_days = (today - price_latest).days
        price_ok = stale_days <= 3  # Allow weekends
        print(f"    Letzte Preise:       {price_latest}  "
              f"({stale_days}d ago)  {check_icon(price_ok)}")
        print(f"    Tickers (letzte 7d): {price_tickers}")
        if not price_ok:
            blockers.append(f"Preisdaten veraltet ({stale_days} Tage)")
    else:
        print(f"    ❌ Keine Preisdaten gefunden")
        blockers.append("Keine Preisdaten")

    # ══════════════════════════════════════════════════════════════════
    # FINAL ASSESSMENT
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 72}")
    print(f"  READINESS ASSESSMENT")
    print(f"{'=' * 72}")

    # System uptime
    first_run = safe_scalar(
        s,
        "SELECT MIN(started_at)::date FROM signals.collection_log "
        "WHERE status = 'success'",
        "uptime",
    )
    first_snap = snap_min
    days_collecting = (today - first_run).days if first_run else 0
    days_snapshots = snap_distinct_dates

    print(f"\n  System seit:           {first_run}  ({days_collecting} Tage)")
    print(f"  Snapshots seit:        {first_snap}  ({days_snapshots} Snapshot-Tage)")
    print(f"  Ticker im Universe:    {active}")
    print(f"  Snapshots gesamt:      {snap_count:,}")

    # Blockers
    if blockers:
        print(f"\n  {'─' * 40}")
        print(f"  ❌ BLOCKER ({len(blockers)}):")
        for b in blockers:
            print(f"     • {b}")

    # Warnings
    if warnings:
        print(f"\n  ⚠️  WARNUNGEN ({len(warnings)}):")
        for w in warnings:
            print(f"     • {w}")

    # Final verdict
    print(f"\n  {'═' * 40}")
    if not blockers:
        print(f"  ✅✅✅  SPRINT 9 BEREIT!  ✅✅✅")
        print(f"  Alle Voraussetzungen erfüllt.")
        print(f"  Empfehlung: Jupyter-Analyse jetzt starten.")
    else:
        # Calculate earliest ready date
        if readiness_dates:
            earliest = max(d for _, d in readiness_dates)
            print(f"  ❌  SPRINT 9 NOCH NICHT BEREIT")
            print(f"")
            print(f"  Frühestes Startdatum:  📅 {earliest}")
            print(f"  (basierend auf: {', '.join(n for n, _ in readiness_dates)})")
            print(f"")

            # Detailed timeline
            print(f"  Timeline:")
            for name, d in sorted(readiness_dates, key=lambda x: x[1]):
                delta = (d - today).days
                bar = "█" * min(delta, 40)
                print(f"    {name:<20}  {d}  ({delta:>3}d)  {bar}")
        else:
            print(f"  ❌  SPRINT 9 NOCH NICHT BEREIT")
            print(f"  Keine Zeitschätzung möglich – zu wenig Daten.")

    print(f"\n{'=' * 72}\n")
