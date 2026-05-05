# SESSION_LOG.md – Chronological Session History

> What happened in each session. Aids re-entry for future sessions.
>
> See also: [INDEX.md](INDEX.md) · [ROADMAP.md](ROADMAP.md)

**Last updated:** May 2026

---

### Session 1 – April 2026 – Concept and Documentation
- Core concept discussed, video tutorial critically evaluated
- Decision: No trading at start, build Signal Warehouse instead
- Data sources strategy established (collect maximally, filter later)
- Architecture draft created
- Sprint planning (12 sprints + waiting phase)
- All documents created (CLAUDE.md, ARCHITECTURE.md, ROADMAP.md, DATA_SOURCES.md, DECISIONS.md, LEARNINGS.md)
- Next step: **Start Sprint 0**

### Session 2 – 12 April 2026 – Sprint 0 Implementation
- Local environment verified: Python 3.12.6, uv 0.11.2, Git 2.47.1, Docker 27.4.0
- GitHub repo created: `sebastianganser/Alpaca-Broker` (private)
- PostgreSQL 18.3 on Unraid confirmed: `postgresql18-alpaca` (192.168.1.93:5435, DB: broker_data)
- Complete project structure created (src layout with uv)
- Pydantic Settings with Alpaca safety check implemented
- SQLAlchemy 2.0 Base + Session Factory + Universe model
- Alembic configured: schema `signals` + table `universe` migrated
- 103 tickers (S&P 100 + SPY) loaded into universe
- 11 unit tests (all green)
- Docker Compose + Dockerfile for collector created
- Next step: **Git push, then Sprint 1 (Price Collector)**

### Session 3 – 12 April 2026 – Sprint 1 Implementation
- Documentation audit: 20 inconsistencies found and fixed
- Dependencies: yfinance, pandas, pandas-market-calendars, scipy added
- Migration 003: `prices_daily` + `collection_log`
- `@retry` decorator with exponential backoff
- **GapDetector**: NYSE calendar-based gap detection → reload → forward-fill extrapolation
- **BaseCollector**: Template Method Pattern with integrated gap check
- **PriceCollectorYFinance**: Batch download (50-batches), ticker mapping (BRK.B→BRK-B)
- First live run: 1,020 data points for 102 tickers (10 trading days)
- WBA (Walgreens) delisted on Yahoo → no data retrieval possible
- 29 new unit tests (40 total, all green)
- APScheduler entrypoint: `main.py` with BlockingScheduler, CronTrigger 22:15
- **Sprint 2 (ARK Holdings)**: arkfunds.io API, 322 holdings for 8 ETFs loaded
- 150 new tickers from ARK ETFs validated via Alpaca (102 → 252)
- ARKDeltaComputer implemented
- 71 tests total (all green)
- **Sprint 1b (Alpaca Prices + Universe)**: yfinance replaced by Alpaca Market Data API
- Universe expanded: S&P 500 + Nasdaq 100 → 644 active tickers
- PriceCollectorAlpaca: Multi-symbol batch (100/request), adjustment=all, IEX feed
- First Alpaca run: 2,700 new records for 540 tickers (<20s)
- 87 tests total (all green)

### Session 4 – 13 April 2026 – Sprint 3 Implementation
- **SECClient**: Central API client with rate limiting (10 req/s), CIK↔ticker mapping
- **Form4Collector**: Universe-driven (644 tickers), XML parsing
- **Form13FCollector**: Filer-driven (top-20 institutional investors)
- **InsiderClusterComputer**: Cluster detection (≥2 insiders buying in 21-day window)
- ORM models: `InsiderTrade`, `InsiderCluster`, `Form13FHolding`
- Alembic migrations 006 + 007
- 67 new tests (154 total, all green)
- Next step: **Sprint 4 (Politician Trades)**

### Session 5 – 13 April 2026 – Sprint 4 Implementation
- **Quiver Quantitative API rejected** ($30/month, constraint: stay free)
- **DisclosureClient**: Senate eFD scraper with CSRF handling, terms agreement
- **PoliticianTradesCollector**: Senate PTR retrieval, stock filtering, ticker normalization
- ORM model `PoliticianTrade` + migration 008
- Dependencies: `requests` + `beautifulsoup4`
- 46 new tests (200 total, all green)

### Session 6 – 13 April 2026 – Sprint 5 Implementation
- **YFinanceClient**: Shared client with rate limiting (0.5s/ticker, 3s/batch)
- **FundamentalsCollectorYF**: 18 metrics from `ticker.info` + `eps_growth_yoy`
- **AnalystRatingsCollector**: Upgrades/downgrades, 30-day lookback
- **EarningsCalendarCollector**: Earnings dates with EPS estimates and surprises
- ORM models + migrations 009–011
- Night slot 01:00–03:00 CET for all yfinance jobs
- 68 new tests (268 total, all green)

### Session 7 – 13 April 2026 – Sprint 6 Implementation
- **Historical price backfill**: Alpaca prices from 2021-01-01 (~882k rows, ~5.3 years)
- **TechnicalIndicatorsComputer**: 14 indicators via pandas-ta
- **Relative Strength**: Excess return instead of ratio
- ORM model + migration 012
- 35 new tests (303 total, all green)

### Session 8 – 13 April 2026 – Sprint 7 Implementation
- **FastAPI backend**: `main.py` rebuilt from BlockingScheduler to BackgroundScheduler + FastAPI
- **5 API routers**: Dashboard, Universe, Signals, Ticker, Operations (20+ endpoints)
- **19 Pydantic schemas**
- **BackfillManager**: Thread-based async backfills with progress tracking
- **Vite/React SPA**: 5 pages (Dashboard, Universe, Signals, Settings, Ticker Detail)
- **Design System**: Stitch "Precision Architect" – Dark Mode, Cyan Primary, Inter Font
- **Docker**: 3-stage multi-stage build
- 303 tests (all green, no regression)

### Session 9 – 13 April 2026 – Unraid Deployment + Operational Fixes
- **Deployment on Unraid**: Container running on `192.168.1.93:8090`
- Compose Manager configuration
- Fix: README.md in Docker build
- Fix: Dashboard health check (alembic query)
- Data population: ~820k price records + ~818k TA indicators via backfill
- **Backfill Progress Tracking**: Completely refactored with ETA estimation
- **Factory Reset**: DELETE FROM in transaction, preserves universe
- **Monthly Index Sync**: New scheduler job (1st of month, 03:00 CET)
- **SPA Routing Fix**: Catch-all fallback for Ctrl+F5
- **Live Job Status**: JobTracker with APScheduler event listener

### Session 10 – 13 April 2026 – Data Quality Tile
- **Data quality tile** on TickerPage: Per-ticker completeness status
- 4 dimensions: Prices, TA indicators, Fundamentals, Signal updates
- New endpoint: `GET /api/v1/ticker/{symbol}/data-quality`

### Session 11 – 13 April 2026 – Sector Enrichment
- **Problem:** ~740 of 845 tickers had no sector
- **Solution:** Sector/industry enrichment via yfinance `ticker.info`
- Backend + frontend implementation with progress tracking
- **UI Polish:** Sidebar rebrand, world clock, exchange status indicators
- **Logs page**: Scheduler logs with filtering

### Session 12 – 14/15 April 2026 – SEC Form 4 & Senate eFD Bugfix
- **SEC Form 4 (404 errors):** XSLT prefix bug + CIK routing bug fixed → 1,329 insider transactions imported
- **Senate eFD:** TLS fingerprinting (403) → curl_cffi fix; DataTables AJAX → JSON endpoint; Session flow → 636 politician trades
- New dependency: `curl_cffi>=0.7`

### Session 13 – 15 April 2026 – World Clock Tooltip
- Hover tooltip on world clock entries showing exchange info + trading hours + real-time status

### Session 14 – 15 April 2026 – Fundamentals Quality & Auto-Onboarding
- **Bug fix: Dividend yield 95% instead of 0.92%** – yfinance format inconsistency → backend normalization + migration 013
- **Plausibility checks** for all 17 fundamental fields
- **Auto-universe expansion + auto-backfill** via new `NewTickerOnboarder` service
- **Log-line capture + UI display** (migration 014)

### Session 15 – 15 April 2026 – ARK Deltas Bugfix
- **Bug 1:** 322 deltas instead of ~71 – `unchanged` positions stored → fixed: skip unchanged
- **Bug 2:** API schema mismatch – ARCHITECTURE.md defined Sprint 2 draft, ORM implemented differently, API in Sprint 7 written against docs not code → fixed: align schema to ORM

### Session 16 – 15 April 2026 – ETF Blacklist & Universe Cleanup
- ~50+ ETFs/funds deactivated via self-learning blacklist system
- New table `signals.ticker_blacklist` (migration 015)
- yfinance `quoteType` as authoritative check
- Onboarder refactoring: blacklist → Alpaca → quoteType → backfill
- API active-filter as default

### Session 17 – 16 April 2026 – Collector Bugfixes & Log Quality
- **Form13F:** 6/20 filers fixed via 4-stage infotable detection → +64% holdings
- **Earnings Calendar:** yfinance logger suppressed (expected ERRORs)
- **Fundamentals:** Plausibility ranges widened (format guard, not value filter)
- **Insider Clusters:** UniqueConstraint + UPSERT (migration 017)
- **TA Job:** CollectorLogCapture integration

### Session 18 – 02-05 May 2026 – Insider Backfill & Data Quality Hardening
- **Form 4 Historical Backfill:** 313,544 insider trades across 647 tickers (96% coverage)
  - Two backfill runs (~30h + ~20h) for full 3-year depth
  - Resume-safe logic: skip only tickers with deep data (`MIN(filing_date) ≤ 2023-12-31`)
  - First run interrupted by Windows update → resume mechanism proven
- **`DATA_START_DATE = 2021-01-01`:** Universal data boundary constant in `config.py`
- **Outlier cleanup:** 392 records removed (year 0024 typos, 2033 vesting schedules)
- **`verify_insider_backfill.py`:** Distribution analysis + backfill depth check
- **`cleanup_insider_outliers.py`:** Removes trades outside `DATA_START_DATE..today`
- **`sprint8_readiness.py`:** Rewritten with depth + coverage validation per table
  - Caught the original gap: 50% of tickers had only 7 days of data
- **Insider Clusters:** Recomputed (304 clusters) on top of full backfill
- **Scripts added:** `scripts/verify_insider_backfill.py`, `scripts/cleanup_insider_outliers.py`

