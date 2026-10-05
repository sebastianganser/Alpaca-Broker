# SCHEDULER.md – Execution Schedule

> All scheduled and manual jobs in the Signal Warehouse.
> Jobs are managed by APScheduler (BackgroundScheduler) running in the same Python process as FastAPI.
>
> See also: [INDEX.md](INDEX.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

**Last updated:** October 2026 (review fixes: nightly chain, market-date labels, alerting)

---

## Daily – Upstream Collectors (Europe/Berlin)

All collectors label their data with the **last completed NYSE session** (`utils/market_calendar.last_completed_session()`), not with the Berlin calendar date.

| Time | Job | Description |
|---|---|---|
| 22:30 | `price_collector` (`prices_alpaca`) | OHLCV via Alpaca (feed from `ALPACA_DATA_FEED`, default SIP), upsert of lookback window, automatic full-history refresh + TA/target recompute when Alpaca re-adjusted a ticker (split/dividend), gap repair |
| 22:50 | `technical_indicators_computer` | TA indicators (also step 1 of the nightly chain as catch-up) |
| 23:00 | `ark_holdings` | ARK ETF holdings + deltas |
| 23:15 | `options_iv_collector` | Options IV snapshots (ATM IV, skew, term structure) |
| 23:30 | `form4_collector` | Form 4 filings (accession-based dedup, 4/A amendments) |
| 00:00 | `news_collector` | Alpaca News (watermark-based, per-page retry) |
| 00:15 | `short_interest_collector` | Short volume via Massive API (~2.5 h, skips if session already stored) |
| 00:30 | `sentiment_computer` | FinBERT scoring |
| 01:00 | `analyst_ratings_collector` | Analyst upgrades/downgrades |
| 01:30 | `estimates_collector` | EPS/revenue consensus + revisions (rolling 90-day window – critical, startup catch-up) |
| 04:15 | `fred_collector` | FRED macro series |

## Daily – Nightly Chain (04:30)

`nightly_chain` runs sequentially for the target session `last_completed_session()`:

1. `technical_indicators_computer` (catch-up)
2. `feature_pipeline` (skipped on non-trading days → status `skipped`)
3. `target_backfill` (target = `close(d+h) / open(d+1) − 1`)
4. `context_pack_generator`

Before starting, the chain waits up to 2 h for upstream collectors that are still running. Inputs without a fresh run (started after 16:00 NY on the target session) are listed in the notes and the chain ends as `partial`. `feature_pipeline`, `target_backfill` and `context_pack_generator` are registered as **paused** jobs (no own schedule); a manual trigger runs a one-shot clone.

## Maintenance

| Time | Job | Description |
|---|---|---|
| daily 03:30 | `log_retention` | Delete collection_logs older than 90 days; mark stale `running` rows (> 6 h) as `failed` |
| Sun 03:00 | `data_retention` | Rolling **20-quarter** retention (decision 2026-10-05, no backups). Savepoint per table; insider by `filing_date`, politicians by `disclosure_date`, news sentiment via cascade |

## Weekly (Sunday)

| Time | Job | Description |
|---|---|---|
| 01:00 | `fundamentals_collector` | Fundamentals via yfinance (incl. `most_recent_quarter`) |
| 02:00 | `earnings_calendar_collector` | Earnings dates (BMO/AMC, `first_seen`/`last_seen`) |
| 10:00 | `form13f_collector` | 13F filings (all filings in window, amendments, PUT/CALL separated) |
| 11:00 | `politician_trades_collector` | Senate eFD PTRs (skips known source URLs) + auto-onboarding |

## Monthly (1st of month)

| Time | Job | Description |
|---|---|---|
| 03:00 | `index_sync` | S&P 500 / Nasdaq 100 membership (sanity guard aborts on implausible scrapes) + sector enrichment |
| 07:00 | `feature_analysis` | Rank-IC / purged walk-forward feature analysis |

## Manual (via UI)

All `POST` endpoints under `/ops/*` and `/analysis/*` require the header `X-API-Key` (if `API_KEY` is configured) or at least `X-Requested-With` (CSRF guard). The UI sends both automatically; the key is entered under *Einstellungen*.

| Action | Endpoint | Description |
|---|---|---|
| Price Backfill | `POST /ops/backfill/prices` | Full-history refresh from `data_start_date()` (rolling 20 quarters) |
| Indicator Backfill | `POST /ops/backfill/indicators` | Recompute all TA indicators |
| Sector Enrichment | `POST /ops/backfill/sectors` | Reload sectors/industries + ETF blacklist check |
| DB Reset | `POST /ops/db/reset?confirm=RESET` | Factory reset (all data tables except universe/blacklist) |
| VACUUM/ANALYZE | `POST /ops/db/vacuum` | PostgreSQL maintenance |
| Trigger Job | `POST /ops/scheduler/{job_id}/trigger` | Trigger any job (chain steps as one-shot clone) |
| Feature Analysis | `POST /analysis/trigger` | Async (202), 409 if already running |

---

## Job Configuration

Jobs are registered in `src/trading_signals/scheduler/setup.py`; job → collector-name mapping in `scheduler/registry.py`; the shared wrapper `run_logged_job` in `scheduler/runner.py`.

**Key design choices:**
- **APScheduler** – BackgroundScheduler in the FastAPI process, CronTrigger in `Europe/Berlin`, `coalesce`, `max_instances=1`
- **run_logged_job** – every job writes a `collection_log` row (status constants from `utils/job_status.py`: running/success/partial/failed/skipped), captures logs (thread-filtered, secrets redacted) and holds a per-collector lock
- **Alerting** – `partial`/`failed` runs, `EVENT_JOB_ERROR` and `EVENT_JOB_MISSED` trigger a push via `ALERT_WEBHOOK_URL` (ntfy-compatible, optional)
- **Startup catch-up** – price collector, estimates and nightly chain are re-run after a restart if their last run was missed
- **Warning Demotion** – known harmless third-party warnings are downgraded to INFO

See [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) for the rationale behind these choices.
