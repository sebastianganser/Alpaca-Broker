# SCHEDULER.md – Execution Schedule

> All scheduled and manual jobs in the Signal Warehouse.
> Jobs are managed by APScheduler (BackgroundScheduler) running in the same Python process as FastAPI.
>
> See also: [INDEX.md](INDEX.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

**Last updated:** May 2026

---

## Daily – Night Slot (00:00–03:30 CET)

| Time (CET) | Job | Description | Sprint |
|---|---|---|---|
| 00:00 | `news_collector` | News articles via Alpaca News API (~830 articles/day) | 8c ✅ |
| 00:30 | `sentiment_computer` | FinBERT sentiment scoring on collected articles (~23s) | 8c ✅ |
| 01:00 | `analyst_ratings_collector` | Analyst upgrades/downgrades via yfinance (~10 min) | 5 ✅ |
| 01:30 | `estimates_collector` | EPS/Revenue consensus + revisions via yfinance (rolling 90-day window) | 9.5a ✅ |
| 02:00 | `feature_pipeline` | Compute daily feature snapshots (after all collectors) | 8 ✅ |
| 02:15 | `target_backfill` | Backfill return targets for older snapshots | 8 ✅ |
| 03:30 | `log_retention` | Delete collection_logs older than 90 days | 8c ✅ |

## Daily – Evening Slot (after US EOD)

| Time (CET) | Job | Description | Sprint |
|---|---|---|---|
| 22:15 | `prices_alpaca` | OHLCV for entire universe (Alpaca Multi-Symbol Batch) | 1b ✅ |
| 22:30 | `technical_indicators_computer` | Compute TA indicators from price data | 6 ✅ |
| 23:00 | `ark_holdings` | ARK ETF holdings via arkfunds.io + delta computation | 2 ✅ |
| 23:30 | `form4_collector` | New Form 4 filings (last 24h) + cluster computation | 3 ✅ |

## Weekly (Sunday)

| Time (CET) | Job | Description | Sprint |
|---|---|---|---|
| 01:00 | `fundamentals_collector` | Fundamental metrics via yfinance | 5 ✅ |
| 01:00 | `analyst_ratings_collector` | Also runs on Sundays | 5 ✅ |
| 02:00 | `earnings_calendar_collector` | Earnings dates via yfinance | 5 ✅ |
| 10:00 | `form13f_collector` | New 13F filings (if quarter-end occurred) | 3 ✅ |
| 11:00 | `politician_trades_collector` | Senate eFD PTR scraping + **auto-onboarding new tickers** | 4 ✅ |

## Monthly (1st of month)

| Time (CET) | Job | Description | Sprint |
|---|---|---|---|
| 03:00 | `index_sync` | S&P 500 / Nasdaq 100 membership update + **sector enrichment** for new tickers | 7+ ✅ |

## Manual (via UI)

| Action | Endpoint | Description |
|---|---|---|
| Price Backfill | `POST /ops/backfill/prices` | Load historical prices from 2021-01-01 (Settings > Backfill) |
| Indicator Backfill | `POST /ops/backfill/indicators` | Recompute all TA indicators (Settings > Backfill) |
| Target Backfill | `POST /ops/scheduler/target_backfill/trigger` | Manually trigger target return backfill (Settings > Scheduler) |
| Sector Enrichment | `POST /ops/backfill/sectors` | Reload sectors/industries for ALL active tickers from yfinance + ETF blacklist check (Settings > Sectors) |
| DB Reset | `POST /ops/db/reset` | Factory reset: delete all data tables (Settings > Factory Reset) |
| VACUUM/ANALYZE | `POST /ops/db/vacuum` | PostgreSQL VACUUM + ANALYZE (Settings > VACUUM) |
| Trigger Job | `POST /ops/scheduler/{job_id}/trigger` | Manually trigger any scheduled job (Settings > Scheduler) |

---

## Job Configuration

Jobs are registered in `src/trading_signals/scheduler/jobs.py`.

**Key design choices:**
- **APScheduler** (not cron) – integrated Python logging, error handling, dynamic control
- **BackgroundScheduler** – runs alongside FastAPI in the same process
- **CronTrigger** – timezone-aware (`Europe/Berlin`)
- **JobTracker** – APScheduler event listener provides live running status to the UI
- **CollectorLogCapture** – every job captures WARNING/ERROR + collector-specific INFO lines
- **Log Retention** – automatic cleanup of collection_logs older than 90 days (daily 03:30)
- **Warning Demotion** – known harmless third-party warnings (e.g., HF Hub token) are automatically downgraded to INFO level

See [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) for the rationale behind these choices.
