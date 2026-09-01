# ROADMAP.md – Sprint Planning & Progress

> **Living document.** Updated after every sprint.
> See [SESSION_LOG.md](SESSION_LOG.md) for detailed session-by-session history.
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** May 2026

---

## Status Overview

| Sprint | Title | Status | Date |
|---|---|---|---|
| 0 | Foundation (Docker, DB, Structure) | 🟢 Done | April 2026 |
| 1 | Price Collector (yfinance → Alpaca) | 🟢 Done | April 2026 |
| 2 | ARK Holdings Tracker | 🟢 Done | April 2026 |
| 3 | SEC EDGAR (Form 4 + 13F) | 🟢 Done | April 2026 |
| 4 | Politician Trades (Senate eFD) | 🟢 Done | April 2026 |
| 5 | Fundamentals + Analyst Data | 🟢 Done | April 2026 |
| 6 | Technical Indicators | 🟢 Done | April 2026 |
| 7 | Dashboard & Operations UI | 🟢 Done | April 2026 |
| 8 | Feature Pipeline | 🟢 Done | May 2026 |
| 8c | News Sentiment Pipeline | 🟢 Done | May 2026 |
| **⏸ Wait** | **2–3 months data collection** | **–** | **–** |
| 9 | Exploratory Analysis (Jupyter) | 🟢 Done | Aug 2026 |
| 9.5| Data Hardening & Extension | 🔴 Open | Aug 2026 |
| 10 | Signal Scoring Models | 🔴 Open | Nov 2026 |
| 11 | Backtest Framework | 🔴 Open | – |
| 12 | Paper Trading Integration | 🔴 Open | – |

**Legend:** 🔴 Open · 🟡 In Progress · 🟢 Done · ⏸ Paused

---

## Completed Sprints

### Sprint 0 – Foundation ✅
- Project structure, Git repo, PostgreSQL 18 on Unraid
- SQLAlchemy 2.0 + Alembic + universe table
- 103 tickers (S&P 100 + SPY), 11 tests

### Sprint 1 – Price Collector ✅
- `BaseCollector` (Template Method Pattern), `PriceCollectorYFinance`, gap detection
- Sprint 1b: `PriceCollectorAlpaca` (primary), universe expanded to 644 tickers (S&P 500 + Nasdaq 100)
- 87 tests

### Sprint 2 – ARK Holdings Tracker ✅
- arkfunds.io JSON API (8 ETFs), `ARKDeltaComputer`, 150 new tickers via Alpaca validation
- 71 tests

### Sprint 3 – SEC EDGAR ✅
- `SECClient`, `Form4Collector` (universe-driven), `Form13FCollector` (top-20 filers)
- `InsiderClusterComputer` (≥2 insiders in 21 days)
- 154 tests

### Sprint 4 – Politician Trades ✅
- `DisclosureClient` (Senate eFD, curl_cffi + AJAX), `PoliticianTradesCollector`
- 200 tests

### Sprint 5 – Fundamentals + Analyst Data ✅
- `YFinanceClient` (shared), `FundamentalsCollectorYF`, `AnalystRatingsCollector`, `EarningsCalendarCollector`
- Night slot 01:00–03:00 CET
- 268 tests

### Sprint 6 – Technical Indicators ✅
- Historical price backfill from 2021 (~882k rows)
- `TechnicalIndicatorsComputer` (14 indicators via pandas-ta)
- 303 tests

### Sprint 7 – Dashboard & Operations UI ✅
- FastAPI backend (5 routers, 20+ endpoints) + Vite/React SPA
- Stitch "Precision Architect" Design System
- Docker 3-stage build, deployed on Unraid (192.168.1.93:8090)
- 303 tests

### Sprint 8 – Feature Pipeline ✅
- `FeaturePipeline` class: 8 feature groups, 49 features, graceful degradation, UPSERT
- `TargetBackfillComputer`: Forward returns (1d, 5d, 20d, 60d) via trading-day offset
- Temporal rolling-window features: persistence, recurrence, convergence
- Scheduler: Feature Pipeline 02:00 CET + Target Backfill 02:15 CET
- API: `GET /dashboard/feature-stats` (coverage, backfill status)
- 311 tests (8 new)

### Sprint 8b – Features Page & Exploration UI ✅
- 4 new API endpoints (`/features/coverage`, `/convergence`, `/returns`, `/ticker/{symbol}`)
- 7 new Pydantic schemas (FeatureCoverageItem, SignalConvergenceItem, HorizonStats, etc.)
- New `/features` page (6th sidebar item with Brain icon)
- Pipeline Stats cards, Feature Coverage Heatmap, Signal Convergence, Return Distribution
- Ticker Feature Detail (click-through modal with expandable groups)
- 29 new unit tests (340 total)
- `sprint9_readiness.py` diagnostic script (6-section readiness check)

---

### Sprint 8c – News Sentiment Pipeline ✅
- **7th data source:** Alpaca News API for headline collection (830+ articles/day)
- **NLP scoring:** ProsusAI/finbert (110M params, local CPU, ~50-200ms/headline)
  - Batch processing (batch_size=32), ~23s for ~1700 scores
  - Model pre-cached in Docker image (~440 MB, no runtime download)
- **DB tables:** `news_articles` + `news_sentiment` (migration 019–020)
- **6 new features** in `feature_snapshots`: `sentiment_avg_7d`, `sentiment_avg_30d`, `sentiment_momentum`, `sentiment_neg_count_7d`, `sentiment_article_count_7d`, `market_sentiment_7d`
- **Feature pipeline** extended to 9 groups, 55 features total
- **2 new API endpoints:** `GET /signals/sentiment/summary`, `GET /signals/sentiment/articles`
- **Frontend:**
  - Features page: Sentiment column in heatmap (x/6) + convergence score
  - Signals page: New "Sentiment" tab with Summary + Articles views
  - Toggles: 7/14/30 day lookback, ticker/source/sentiment filters
- **Scheduler:** News Collector (00:00) + Sentiment Scorer (00:30)
- **Log retention:** 90-day automatic cleanup (daily 03:30)
- **Bugfixes:**
  - Fixed `scored_at` → `published_at` date filter in feature pipeline
  - Demoted HF Hub warnings from WARNING to INFO in log capture

---

### ⏸ Waiting Phase: 2–3 Months Data Collection

**No active sprint, but important activities:**
- Regularly check that all collectors run stably
- **Run `sprint9_readiness.py`** periodically to track progress:
  ```bash
  docker exec -it alpaca-broker uv run python scripts/sprint9_readiness.py
  ```
- Occasionally explore data ad-hoc with Claude Desktop
- Record observations in `LEARNINGS.md`
- If data gaps appear: improve collectors
- Run a benchmark portfolio (S&P 500 only) in paper trading account
- **Estimated Sprint 9 start: ~August 2026** (60d returns + 60 snapshot days needed)

---

### Sprint 9 – Exploratory Analysis (Jupyter) ✅
- ✅ Jupyter notebook setup, analysis dependencies (`[analysis]` optional group)
- ✅ Feature ↔ Return correlation analysis (93 snapshot days, 748 tickers)
- ✅ Feature importance: Random Forest, LASSO, correlation matrix
- ✅ Politician dual-date evaluation (disclosure vs. transaction)
- ✅ Documented findings in LEARNINGS.md
- **Note:** return_60d excluded (6.2% fill), 13F features excluded (no quarterly filing since pipeline start)

### Sprint 9.5a – Data Hardening (Bias Prevention) ✅ Complete
*Based on Opus 5 Concept (Aug 2026). Blocker items that must be resolved before Sprint 10.*
- [x] **B1:** `EstimatesCollector` (yfinance) for EPS/Revenue consensus + revisions — *CRITICAL: rolling 90-day window*
- [x] **A1 (Stufe 1):** Point-in-Time Index Membership (Survivorship Bias Prevention)
  - [x] `index_membership` table with `valid_from` / `valid_to` intervals (migration 023)
  - [x] Wikipedia S&P 500 / Nasdaq 100 changes parser + GitHub CSV backup
  - [x] Layout change detection with warning logs
  - [x] `UniverseManager.get_universe_as_of(target_date)` point-in-time query
  - [x] Feature Pipeline uses historical universe composition
  - [x] `IndexSyncer` maintains ongoing intervals automatically
  - [x] Manual seeding endpoint `POST /ops/seed/index-membership`
  - ~~Alpaca Assets `asof` parameter~~ *(does not exist — corrected from Opus 5 concept)*
- [ ] **A1 (Stufe 2/3):** *(Follow-up, planned for later sprint)*
  - [ ] Backfill price data for delisted tickers (via Alpaca historical bars)
  - [ ] Full feature computation for delisted tickers
  - [ ] Manual "Recompute History" button in Settings UI
- [x] **A2:** Lookahead Bias Prevention in Feature Pipeline ✅
  - [x] 13F features: `filing_date <= d` statt `report_period <= d` (45-day fix)
  - [x] Insider features: `filing_date <= d` guard auf alle Trade-Queries (2-10 day fix)
  - [x] Earnings features: 90-day forward window limit + documented remaining risk
  - Note: Politician features already correct (dual disclosure/transaction date)
- [x] **C4:** Validate `adjustment=all` against known stock splits ✅ (AMZN, GOOGL, SHOP, NVDA, CMG — all smooth)

### Sprint 9.5b – Data Extension (New Sources + Derived Features) ✅ Complete
*New data dimensions identified by Opus 5 to close the biggest feature gaps.*
*19 new feature columns → ~77 total. 2 new collectors (FRED, Options IV).*
- [x] **D1:** FRED Macro Regime Collector ✅ `00cb112`
  - 6 macro features: VIX, DGS2, DGS10, HY Spread, Dollar Index, Inflation Expectation
  - Scheduled daily at 04:15 CET
- [x] **B3:** Benchmark & Sector ETFs ✅ `d0c1c37`
  - 14 ETFs (SPY, QQQ, IWM + 11 GICS Sector SPDRs) in universe
  - Seed endpoint `POST /ops/seed/benchmark-etfs`
- [x] **B2:** Earnings Surprise / SUE ✅ `2da97a1`
  - `sue_last`, `days_since_last_earnings`
- [x] **E3:** Sentiment Momentum ✅ `2da97a1`
  - `news_volume_ratio_7d`
- [x] **E4:** Liquidity Measures ✅ `2da97a1`
  - `dollar_volume_20d`, `amihud_illiquidity_20d`
- [x] **E2:** Continuous Insider Ratio ✅ `18141d8`
  - `insider_buy_ratio_30d`, `insider_buy_ratio_90d`
- [x] **E1:** 13F Deltas ✅ `18141d8`
  - `form13f_exited_positions_count`, `form13f_holder_delta_qoq`
- [x] **D2:** Market Breadth ✅ `80b9a40` + fixes `72eedd5` `f3232f7`
  - `breadth_advance_decline`, `breadth_pct_above_sma50`
- [x] **B4:** Sector-Neutralization ✅ `81446a4` + migration `32168a0`
  - `sector_relative_return_20d`, `sector_relative_momentum`
  - Note: `sector_relative_momentum` needs ~50 trading days ETF SMA50 data (retest Nov 2026)
- [x] **D3:** Options IV Collector ✅ `74d06ab` + fixes `aaff609` `4051994` `90f0538` `24a777d` `664ed77`
  - ATM IV 30d/60d, 25d Skew, Term Structure, Put/Call OI
  - Source: yfinance (Alpaca free tier lacks Greeks/IV)
  - Scheduled daily at 04:30 CET. IV-Rank needs ~1 year buildup (usable ~Sept 2027)
  - NYSE trading day check + zero-data warning on trading days
- [ ] **B5:** Short Interest Collector — *deferred, needs Massive/Polygon API key (free registration)*

### Sprint 9.5c – Candidate Pipeline MVP 🔴 Open
*Minimum viable candidate selection and Context Pack generation.*
*Phase 1 (Sept): Datenqualität + letzte Datenquellen + Context Pack.*
*Phase 2 (ab Okt): ML-Analyse-Update mit allen neuen Features → Composite Score.*

**Phase 1 — Sept 2026:**
- [ ] **B5:** Short Interest Collector *(requires free Massive/Polygon API key registration)*
  - Must be completed early September so data accumulates before R1 in October
  - User action required: register at https://massive.io or https://polygon.io
- [ ] **C1-C3:** Data quality fixes (Politician lag weighting, ticker parser, party mapping)
- [ ] **F2:** Context Pack MVP (Candidates + Top Features + Market Context, YAML frontmatter)
  - [ ] Output to configurable Unraid path (`CONTEXT_PACK_PATH` env var)
  - [ ] Daily overview + per-candidate Markdown

**Phase 2 — Ab Okt 2026 (nach ≥4 Wochen Datenakkumulation):**
- [ ] **R1:** Feature Analysis Re-Run — Sprint 9 ML-Analyse aktualisieren
  - Correlations + Feature Importance neu berechnen mit **allen** neuen Features:
    - 9.5b: 19 Features (Macro, SUE, Breadth, Sector, Insider, 13F, Liquidity, Sentiment)
    - 9.5b: Options IV (ATM IV, Skew, Term Slope, Put/Call OI)
    - 9.5c: Short Interest (B5, sofern bis dahin aktiv)
  - Automatisch am 1. Okt via `feature_analysis` Job, oder manuell triggern
  - Ergebnis: aktualisierte Importance-Rankings für alle ~80 Features
- [ ] **F1:** Rule-based Composite Score + Guardrails (gewichtet nach aktualisierten ML-Importance-Werten):
  - [ ] Min. liquidity, max 2/sector, pairwise correlation, churn lock, min-score
  - [ ] `candidate_selections` + `candidate_rejections` tables

### Sprint 10 – Signal Scoring
- Weighted scoring model, optional LASSO/gradient boosting
- Daily score calculation job

### Sprint 11 – Backtest Framework
- Walk-forward testing engine, transaction cost model, performance metrics (Sharpe, max drawdown, hit rate)

### Sprint 12 – Paper Trading Integration
- Alpaca broker adapter with paper guard, order management, position sizing
- Manual approval before every trade

---

## 🚢 Deployment on Unraid – Completed ✅

**Status:** System running in production on `192.168.1.93:8090`.

**Infrastructure:**
- Clone-and-build on Unraid: `/mnt/user/appdata/alpaca-broker`
- Docker Compose via Compose Manager
- PostgreSQL 18 external (`postgresql18-alpaca`, port 5435)
- Alembic migrations run automatically via `entrypoint.sh`
- 16 scheduler jobs active (10 daily, 4 weekly, 1 monthly, 1 maintenance)

**Update workflow:**
1. `git push` from Windows
2. Unraid terminal: `cd /mnt/user/appdata/alpaca-broker && git pull && docker compose -f /boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml up --build -d`

---

## Long-term Ideas (Backlog)

- **News sentiment:** ~~NLP analysis of headlines with Haiku~~ ✅ Implemented with FinBERT (Sprint 8c). Haiku upgrade planned for Sprint 9.
- **Reddit/social sentiment:** StockTwits, WSB mentions as contrarian signal
- **Options flow:** Unusual options activity
- **ML models:** XGBoost, neural networks after 12+ months of data
- **Multiple ETFs:** Not only ARK but other "smart money" funds
- **European stocks:** German/European small caps
- **Live trading:** Only after 12+ months of successful paper trading
