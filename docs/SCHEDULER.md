# SCHEDULER.md – Execution Schedule

> All scheduled and manual jobs in the Signal Warehouse.
> Jobs are managed by APScheduler (BackgroundScheduler) running in the same Python process as FastAPI.
>
> See also: [INDEX.md](INDEX.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

**Last updated:** 17 October 2026 (verified against `scheduler/setup.py` + `scheduler/registry.py`; production `job_count` 26)

**26 registered jobs:** 13 daily (11 upstream jobs, `log_retention`, `nightly_chain`), 7 weekly (Sunday, incl. `data_retention`, `universe_validation`), 2 monthly, 4 paused chain steps. All times Europe/Berlin. (`universe_validation` is live since the deploy of 2026-10-17; production reports `job_count` 26.)

**Log names:** `collection_log.collector_name` differs from the job id for some jobs (`scheduler/registry.JOB_COLLECTOR_NAMES`), e.g. `price_collector` → `prices_alpaca`, `news_collector` → `news_alpaca`, `fundamentals_collector` → `fundamentals_yf`, `short_interest_positions_collector` → `short_interest_positions`. The jobs `ark_holdings` and `form4_collector` additionally write the follow-up runs `ark_deltas` and `insider_clusters`.

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

`nightly_chain` runs sequentially for the target session `last_completed_session()`. If that session was already processed successfully (weekends/holidays), the run ends as `skipped` (`no new session`).

1. `technical_indicators_computer` (catch-up)
2. **Session catch-up** – `feature_pipeline` for earlier sessions with missing snapshots (see below)
3. `feature_pipeline` (skipped on non-trading days → status `skipped`)
4. `target_backfill` (target = `close(d+h) / open(d+1) − 1` + barrier label + trailing-stop label `return_trail_60d`; retries up to 3× on PostgreSQL deadlock since `97fcdb0`)
5. `stage2_review` (reads `decisions.yaml` files, writes `context_packs/stage2_auswertung.md`; see [STAGE2_DECISIONS.md](STAGE2_DECISIONS.md))
6. `context_pack_generator` (only if `feature_pipeline` ended `success`/`partial`, otherwise `skipped`)

Before starting, the chain waits up to 2 h for upstream collectors (`registry.NIGHTLY_UPSTREAM`) that are still running. Inputs without a fresh run (started after the close of the target session) are listed in the notes and the chain ends as `partial`; `failed` if `feature_pipeline` failed. **Critical inputs** (`True` in `NIGHTLY_UPSTREAM`, i.e. `prices_alpaca`, plus a coverage check: real price rows of the target session ≥ 80 % (`MIN_PRICE_COVERAGE`) of the maximum over the last 5 sessions (`COVERAGE_BASELINE_SESSIONS`), and never 0; a failing coverage query also counts as critical) are different: TA, `target_backfill` and `stage2_review` still run, `feature_pipeline` and `context_pack_generator` are `skipped` (each writes a `skipped` log row), the chain ends `failed` (alert) with `critical inputs stale: …` and a repair hint in its notes. Reason: without the session's prices the pipeline would silently reuse the previous session's price features under the new date, and the next night already targets the next session. Repair: automatic – the session catch-up below rebuilds the session on the next chain runs once its prices are complete; manually `scripts/recompute_features.py --start D --end D --all-trading-days` (via `alpaca-ops run`). Steps run without individual alerts – the chain alerts once. `feature_pipeline`, `target_backfill`, `stage2_review` and `context_pack_generator` are registered as **paused** jobs (no own schedule); a manual trigger runs a one-shot clone.

**Session catch-up** (since 2026-10-16): every chain run checks the last 10 NYSE sessions before the target (`CATCHUP_LOOKBACK_SESSIONS`) for missing feature snapshots – per-date row count below 80 % (`MIN_SNAPSHOT_COVERAGE`) of the window's maximum, missing = 0; no baseline (fresh system) → nothing. Each such session is rebuilt oldest first, after TA and before the target's own `feature_pipeline` and `target_backfill` (so its targets are filled in the same run), but only once its own prices pass the coverage check; otherwise it stays `pending (prices X%)` and is retried the next night until it leaves the window. No context packs for past sessions. Notes: `; catch-up: 2026-10-14=success, 2026-10-13=pending (prices 60%)`. A failed catch-up session makes the chain `partial`; `pending` changes nothing (the original night already alerted). If the target is already processed (weekend, restart, manual trigger) but gaps exist, the run is `catch-up only` (TA + catch-up + `target_backfill`, notes `session=<target>; catch-up only; …`); without gaps it stays `skipped` as before. Recomputing a past session is point-in-time safe (same pipeline as `scripts/recompute_features.py`).

**Do not** run manual scripts that write `feature_snapshots` during the chain (≈ 04:30–05:00).

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
| 12:00 | `short_interest_positions_collector` (`short_interest_positions`) | FINRA short interest (settlement-date positions, twice a month) via Massive `GET /stocks/v1/short-interest`: 1 reference request (AAPL) for the settlement-date list + 1 bulk request per date to fetch (dates not stored yet, the 2 most recent for revisions, dates with < 50 % of the median row count; never before `data_start_date()`), 12 s pause, ~3 requests per run. Not part of `NIGHTLY_UPSTREAM` (features apply a 9-session publication lag). Initial history: `scripts/repair/collect_short_interest.py` (~120 requests, ~25 min) |
| 13:00 | `universe_validation` | Checks the active universe against Alpaca assets (`universe/validation.py`): tickers not found / not tradable are deactivated (`is_active = FALSE`, never deleted) – **except** tickers with an open `index_membership` interval, which stay active and produce a warning (run status `partial`; the monthly `index_sync` closes or re-keys the interval). For every such ticker Alpaca corporate actions of the last 365 days are written to the run notes (`OLD->NEW (rename)`, `OLD->ACQ (merger)`); a rename whose new symbol is not in `universe` is a warning. `partial` with warnings is expected while renames or index intervals are pending. Safety nets: fewer than 1,000 Alpaca assets or more than 10 % of the active universe to deactivate → the run fails without changing anything. Not part of `NIGHTLY_UPSTREAM`. Manual counterpart: `scripts/validate_universe.py` (before the job existed, 10 delisted/renamed tickers stayed active for months) |

## Monthly (1st of month)

| Time | Job | Description |
|---|---|---|
| 03:00 | `index_sync` | S&P 500 / Nasdaq 100 membership (sanity guard aborts on implausible scrapes) + sector enrichment. Intervals are dated with the **effective change date** from the Wikipedia change tables ("Historical components of the …"); the sync date is used only as a logged fallback (run notes `sync date used (not in changes table): …`). A member that disappears while its new symbol appears is a **rename**: the open interval is re-keyed to the new symbol (alias list `universe/ticker_aliases.py`, then Alpaca `name_change` corporate actions; run notes `renamed (interval re-keyed): OLD->NEW`), no exit is recorded. Notes also carry the S&P 500 / Nasdaq 100 counts |
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
