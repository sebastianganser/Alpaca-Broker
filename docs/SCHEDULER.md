# SCHEDULER.md – Execution Schedule

> All scheduled and manual jobs in the Signal Warehouse.
> Jobs are managed by APScheduler (BackgroundScheduler) running in the same Python process as FastAPI.
>
> See also: [INDEX.md](INDEX.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

**Last updated:** 18 October 2026 (verified against `scheduler/setup.py` + `scheduler/registry.py`; production `job_count` is 29 since the deploy of 2026-10-18 15:57, `990c286`, migration 040, with `sec_filings_collector` and `sec_financials_collector`; re-read from `GET /api/v1/dashboard/summary` on the evening of 2026-10-18)

**29 registered jobs:** 14 daily (12 upstream jobs incl. `sec_filings_collector`, `log_retention`, `nightly_chain`), 1 weekday job in New York time (`forecast_seal`), 8 weekly (Sunday, incl. `data_retention`, `universe_validation`, `sec_financials_collector`), 2 monthly, 4 paused chain steps. All times Europe/Berlin except `forecast_seal` (America/New_York). (Production reports 29 since the deploy of 2026-10-18 15:57; `forecast_seal` and migration 039 have been live since the earlier deploy of the same day.)

**Log names:** `collection_log.collector_name` differs from the job id for some jobs (`scheduler/registry.JOB_COLLECTOR_NAMES`), e.g. `price_collector` → `prices_alpaca`, `news_collector` → `news_alpaca`, `fundamentals_collector` → `fundamentals_yf`, `short_interest_positions_collector` → `short_interest_positions`. `sec_filings_collector` → `sec_filings`, `sec_financials_collector` → `sec_financials`. The jobs `ark_holdings` and `form4_collector` additionally write the follow-up runs `ark_deltas` and `insider_clusters`.

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
| 04:00 | `sec_filings_collector` (`sec_filings`) | SEC EDGAR filings of all universe companies (`collectors/sec_filings_collector.py`): submissions JSON per CIK (≈ 650 requests, share classes once), whitelisted forms (8-K, 10-Q/K, 6-K, NT 10-K/Q, 144, 13D/13G, S-1/S-3, DEF 14A), 14-day lookback, accession-based dedup; for 8-Ks with item 2.02 / 7.01 / 8.01 the press release (EX-99.x) and for event items (1.01-1.05, 2.01, 2.05, 2.06, 3.01, 4.01, 4.02, 5.02) the 8-K body as a ≈ 3,000-character excerpt, one streamed request per filing (≈ 10-60 per normal day, up to ≈ 150 in peak earnings weeks); a failed text fetch is retried by the next runs. For multi-party forms (144, 13D/13G) one more small header request (1-5 KB) per not-yet-stored accession resolves the subject company: ≈ 45 new accessions per day plus ≈ 100 re-reads of filer copies that are never stored, within the 14-day lookback - under 2 minutes at 9 requests/s ([DATA_SOURCES.md](DATA_SOURCES.md) §13). **Why 04:00:** 04:00 Berlin = 22:00 ET, EDGAR's cut-off for same-day 8-Ks - the run sees the complete US day including after-the-close earnings releases, lies outside the 22:30-03:00 collector load and 30 minutes before the chain (≈ 5-10 minutes). A non-critical `NIGHTLY_UPSTREAM` input (`sec_filings: False`, since the context pack reads the table in section 15): the chain waits for a still running job and a failed night only makes it `partial`; the run is always after the close of the session the 04:30 chain targets (Saturday 04:00 = Friday 22:00 ET), so weekends and holidays cannot make it stale |
| 04:15 | `fred_collector` | FRED macro series |

## Weekdays – Forecast Seal (America/New_York)

| Time | Job | Description |
|---|---|---|
| Mon-Fri 09:00, 09:15, 09:25 **New York** (15:00 / 15:15 / 15:25 Berlin; one hour earlier Berlin time in the few weeks where the US and Europe are on different clock-change schedules) | `forecast_seal` | Seals `context_packs/<previous session>/forecasts.yaml` - the pack whose deadline is the coming 09:30 open: SHA-256 and size go to `forecast_file_seals` together with the time of sealing (`derived/forecast_seal.py`). The ingest counts a file as on time only if its exact content was sealed before the open ([FORECASTS.md](FORECASTS.md) §2): the file has to be **in place before 09:25 New York**, later edits are late. NYSE sessions only (holiday → `skipped`, no file → `skipped`); a run at or after the open seals nothing and ends `partial` (alert). Three runs because a half-written file or a changed content must be caught by a later run; the same content is sealed once and keeps its first time. `misfire_grace_time` 300 s: a run that is delayed by more is dropped as missed. Not part of the nightly chain, not a startup catch-up job |

**Do not deploy or restart the container between 14:55 and 15:35 Berlin time** (13:55-14:35 in the weeks of the clock change): a restart in the seal window can cost the runs after it, and a forecast file without a seal is late for good. `alpaca-ops deploy`/`restart` refuse Mon-Fri 08:55-09:35 New York time (`guard_seal_window`, in addition to 22:15-05:15 Berlin and running jobs/scripts); the seal runs take well under a second.

## Daily – Nightly Chain (04:30)

`nightly_chain` runs sequentially for the target session `last_completed_session()`. If that session was already processed successfully (weekends/holidays), the run ends as `skipped` (`no new session`).

1. `technical_indicators_computer` (catch-up)
2. **Session catch-up** – `feature_pipeline` for earlier sessions with missing snapshots (see below)
3. `feature_pipeline` (skipped on non-trading days → status `skipped`)
4. `target_backfill` (target = `close(d+h) / open(d+1) − 1` + barrier label; retries up to 3× on PostgreSQL deadlock since `97fcdb0`)
5. `forecast_review` (replaces `stage2_review`, migration 039; reads new `forecasts.yaml` files append-only - only folders whose deadline (the 09:30 New York open of the next session) has passed, on time only if the file was sealed by `forecast_seal` -, freezes the decisive sample of the pre-registered criterion and computes its verdict - each written once -, writes `context_packs/forecast_auswertung.md`; see [FORECASTS.md](FORECASTS.md)). Rejected files or rows make the step `partial`; no files = `success` with a report that says so. Two transactions: the ingest is committed before the evaluation starts, the freeze / verdict before the report is rendered
6. `context_pack_generator` (context pack v2, [CONTEXT_PACK.md](CONTEXT_PACK.md); only if `feature_pipeline` ended `success`/`partial`, otherwise `skipped`; writes `00_uebersicht.md`, `screen.csv`, the alphabetical ticker files and the `pack_candidates` rows of the session. **A pack that exists is never regenerated:** if the session folder holds a `forecasts.yaml` or `pack_candidates` rows of the session exist, the step keeps the pack (`success`, notes `pack kept (…) - not regenerated`, repeated in the chain notes), so a `partial` chain re-run for the same session on a later day - weekend, holiday - cannot rewrite what Claude has already worked on)

Before starting, the chain waits up to 2 h for upstream collectors (`registry.NIGHTLY_UPSTREAM`) that are still running. Inputs without a fresh run (started after the close of the target session) are listed in the notes and the chain ends as `partial`; `failed` if `feature_pipeline` failed. **Critical inputs** (`True` in `NIGHTLY_UPSTREAM`, i.e. `prices_alpaca`, plus a coverage check: real price rows of the target session ≥ 80 % (`MIN_PRICE_COVERAGE`) of the maximum over the last 5 sessions (`COVERAGE_BASELINE_SESSIONS`), and never 0; a failing coverage query also counts as critical) are different: TA, `target_backfill` and `forecast_review` still run, `feature_pipeline` and `context_pack_generator` are `skipped` (each writes a `skipped` log row), the chain ends `failed` (alert) with `critical inputs stale: …` and a repair hint in its notes. Reason: without the session's prices the pipeline would silently reuse the previous session's price features under the new date, and the next night already targets the next session. Repair: automatic – the session catch-up below rebuilds the session on the next chain runs once its prices are complete; manually `scripts/recompute_features.py --start D --end D --all-trading-days` (via `alpaca-ops run`). Steps run without individual alerts – the chain alerts once. `feature_pipeline`, `target_backfill`, `forecast_review` and `context_pack_generator` are registered as **paused** jobs (no own schedule); a manual trigger runs a one-shot clone.

**Session catch-up** (since 2026-10-16): every chain run checks the last 10 NYSE sessions before the target (`CATCHUP_LOOKBACK_SESSIONS`) for missing feature snapshots – per-date row count below 80 % (`MIN_SNAPSHOT_COVERAGE`) of the window's maximum, missing = 0; no baseline (fresh system) → nothing. Each such session is rebuilt oldest first, after TA and before the target's own `feature_pipeline` and `target_backfill` (so its targets are filled in the same run), but only once its own prices pass the coverage check; otherwise it stays `pending (prices X%)` and is retried the next night until it leaves the window. No context packs for past sessions. Notes: `; catch-up: 2026-10-14=success, 2026-10-13=pending (prices 60%)`. A failed catch-up session makes the chain `partial`; `pending` changes nothing (the original night already alerted). If the target is already processed (weekend, restart, manual trigger) but gaps exist, the run is `catch-up only` (TA + catch-up + `target_backfill`, notes `session=<target>; catch-up only; …`); without gaps it stays `skipped` as before. Recomputing a past session is point-in-time safe (same pipeline as `scripts/recompute_features.py`).

**Do not** run manual scripts that write `feature_snapshots` during the chain (≈ 04:30–05:00).

## Maintenance

| Time | Job | Description |
|---|---|---|
| daily 03:30 | `log_retention` | Delete collection_logs older than 90 days; mark stale `running` rows (> 6 h) as `failed` |
| Sun 03:00 | `data_retention` | Rolling **20-quarter** retention (decision 2026-10-05, no backups). Savepoint per table; insider by `filing_date`, politicians by `disclosure_date`, news sentiment via cascade, `sec_filings` by `filing_date` (their `sec_filing_texts` first), `financials_quarterly` by `period_end`. Excluded: `earnings_calendar`, `collection_log`, `universe` / `ticker_blacklist` / `index_membership` and the forecast evaluation records `forecast_reviews`, `forecasts`, `forecast_criterion_samples`, `forecast_file_seals`, `pack_candidates` (pinned by a unit test) |

## Weekly (Sunday)

| Time | Job | Description |
|---|---|---|
| 01:00 | `fundamentals_collector` | Fundamentals via yfinance (incl. `most_recent_quarter`) |
| 02:00 | `earnings_calendar_collector` | Earnings dates (BMO/AMC, `first_seen`/`last_seen`) |
| 10:00 | `form13f_collector` | 13F filings (all filings in window, amendments, PUT/CALL separated) |
| 11:00 | `politician_trades_collector` | Senate eFD PTRs (skips known source URLs) + auto-onboarding |
| 12:00 | `short_interest_positions_collector` (`short_interest_positions`) | FINRA short interest (settlement-date positions, twice a month) via Massive `GET /stocks/v1/short-interest`: 1 reference request (AAPL) for the settlement-date list + 1 bulk request per date to fetch (dates not stored yet, the 2 most recent for revisions, dates with < 50 % of the median row count; never before `data_start_date()`), 12 s pause, ~3 requests per run. Not part of `NIGHTLY_UPSTREAM` (features apply a 9-session publication lag). Initial history: `scripts/repair/collect_short_interest.py` (~120 requests, ~25 min) |
| 13:00 | `universe_validation` | Checks the active universe against Alpaca assets (`universe/validation.py`): tickers not found / not tradable are deactivated (`is_active = FALSE`, never deleted) – **except** tickers with an open `index_membership` interval, which stay active and produce a warning (run status `partial`; the monthly `index_sync` closes or re-keys the interval). For every such ticker Alpaca corporate actions of the last 365 days are written to the run notes (`OLD->NEW (rename)`, `OLD->ACQ (merger)`); a rename whose new symbol is not in `universe` is a warning. `partial` with warnings is expected while renames or index intervals are pending. Safety nets: fewer than 1,000 Alpaca assets or more than 10 % of the active universe to deactivate → the run fails without changing anything. Not part of `NIGHTLY_UPSTREAM`. Manual counterpart: `scripts/validate_universe.py` (before the job existed, 10 delisted/renamed tickers stayed active for months) |
| 14:00 | `sec_financials_collector` (`sec_financials`) | SEC XBRL quarterly financials (`collectors/sec_financials_collector.py`, `collectors/sec_xbrl.py`): `companyfacts` JSON per CIK (≈ 650 requests of 0.3-0.5 MB, share classes once, ≈ 5-10 minutes) → standalone fiscal quarters (revenue, gross profit, operating income, net income, diluted EPS, operating cash flow, capex, free cash flow, cash, debt, shares; Q4 = FY − 9M, cash flow by differences of year-to-date values, values as first reported) for the retention window, upsert on (ticker, period_end), `filed` = point-in-time key. The full fact history is re-read every week, so the weekly job is also its own backfill. After the other Sunday collectors, daytime, away from the nightly load and the seal runs. Not part of `NIGHTLY_UPSTREAM` |

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
| DB Reset | `POST /ops/db/reset?confirm=RESET` | Factory reset (all data tables except `universe`, `ticker_blacklist` and the forecast evaluation records `forecasts`, `forecast_reviews`, `forecast_criterion_samples`, `forecast_file_seals`, `pack_candidates`) |
| VACUUM/ANALYZE | `POST /ops/db/vacuum` | PostgreSQL maintenance |
| Trigger Job | `POST /ops/scheduler/{job_id}/trigger` | Trigger any job (chain steps as one-shot clone) |
| Feature Analysis | `POST /analysis/trigger` | Async (202), 409 if already running |

---

## Job Configuration

Jobs are registered in `src/trading_signals/scheduler/setup.py`; job → collector-name mapping in `scheduler/registry.py`; the shared wrapper `run_logged_job` in `scheduler/runner.py`.

**Key design choices:**
- **APScheduler** – BackgroundScheduler in the FastAPI process, CronTrigger in `Europe/Berlin` (a job may bring its own `timezone` in its cron dict: `forecast_seal` runs in `America/New_York`), `coalesce`, `max_instances=1`
- **run_logged_job** – every job writes a `collection_log` row (status constants from `utils/job_status.py`: running/success/partial/failed/skipped), captures logs (thread-filtered, secrets redacted) and holds a per-collector lock
- **Alerting** – `partial`/`failed` runs, `EVENT_JOB_ERROR` and `EVENT_JOB_MISSED` trigger a push via `ALERT_WEBHOOK_URL` (ntfy-compatible, optional)
- **Startup catch-up** – price collector, estimates and nightly chain are re-run after a restart if their last run was missed
- **Warning Demotion** – known harmless third-party warnings are downgraded to INFO

See [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) for the rationale behind these choices.
