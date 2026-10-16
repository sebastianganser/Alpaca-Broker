# SESSION_LOG.md – Chronological Session History

> What happened in each session. Aids re-entry for future sessions.
>
> See also: [INDEX.md](INDEX.md) · [ROADMAP.md](ROADMAP.md)

**Last updated:** 15 October 2026 (handover to Claude Code)

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

### Session 19 – 12 May 2026 – Sprint 8: Feature Pipeline Implementation
- **`FeaturePipeline`** class (`derived/feature_pipeline.py`): Core of the ML data pipeline
  - 8 independent feature groups with graceful degradation
  - ARK (11 features): ETF count, weights, deltas, conviction score, temporal trends (streak, regression)
  - Insider (8): Net buy count, buy value, cluster activity, cluster counts/scores, days since last
  - Analyst (7): Rating score, upgrades/downgrades, price target upside, sentiment, streak
  - Politician (4): Dual-date buy counts + distinct politicians (disclosure + transaction)
  - 13F (2): Top holder count, new positions (QoQ comparison)
  - Fundamentals (8): PE/Forward PE/PS/Revenue Growth/Margin/D/E + 4-week regression slopes
  - Technical (6): Price vs SMA50/200, RSI, relative strength, volume ratio, ATR%
  - Earnings (3): Days until next, consecutive beats, surprise trend
  - UPSERT on composite PK (snapshot_date, ticker) for idempotent recomputation
- **`TargetBackfillComputer`** (`derived/target_backfill.py`): Forward returns backfill
  - 4 horizons: 1d, 5d, 20d, 60d (trading days, not calendar)
  - Uses actual `prices_daily` entries for trading-day offset
- **Scheduler integration**: Feature Pipeline 02:00 CET + Target Backfill 02:15 CET
- **API**: `GET /dashboard/feature-stats` + `FeatureStats` schema + `feature_snapshots` in table stats
- **Tests**: 8 new unit tests (311 total, 299 passing – 12 pre-existing failures unchanged)
- **Documentation**: CLAUDE.md, ROADMAP.md, SCHEDULER.md, SESSION_LOG.md updated

### Session 20 – 13 May 2026 – Sprint 8b: Features Page & Exploration UI
- **Backend:** 4 new API endpoints in `api/routes/features.py`:
  - `GET /features/coverage` – Feature coverage matrix (ticker × 8 signal groups)
  - `GET /features/convergence` – Top tickers by active signal source count
  - `GET /features/returns` – Aggregated forward return statistics (all horizons)
  - `GET /features/ticker/{symbol}` – Full feature detail for a single ticker
- **Schemas:** 7 new Pydantic models (FeatureCoverageItem, FeatureCoverageResponse, SignalConvergenceItem, SignalConvergenceResponse, HorizonStats, ReturnStatsResponse, FeatureGroupDetail, TickerFeatureDetail)
- **Frontend:** New `FeaturesPage.tsx` with 5 sections:
  1. Pipeline Stats (4 stat cards: snapshot date, ticker count, coverage %, backfill %)
  2. Feature Coverage Heatmap (sortable, searchable, color-coded)
  3. Signal Convergence (top tickers by multi-source overlap with progress bars)
  4. Return Distribution (4 horizons with fill %, mean, median, std, min, max)
  5. Ticker Detail Modal (click-through, expandable feature groups)
- **Navigation:** Brain icon, 6th sidebar item (between Signals and Logs)
- **Version:** v0.2.0 · Sprint 8
- **Verified:** All 674 tickers visible, AAPL search works, modal shows 28/49 features
- **Documentation:** README.md, CLAUDE.md, ARCHITECTURE.md, SCHEDULER.md, ROADMAP.md updated
- **`sprint9_readiness.py`:** 6-section readiness check script for Sprint 9:
  - Feature snapshot volume & continuity (gap detection)
  - Target return fill rates per horizon (with Ø/σ)
  - Temporal feature maturity (rolling windows)
  - 13F quarterly data presence
  - Collector health (last 7d success/error)
  - Price data continuity
  - Output: ✅ BEREIT or ❌ + earliest start date with timeline
  - First run result: Sprint 9 estimated **~August 2026**

### Session 21 – 13 May 2026 – Sprint 8c: News Sentiment Pipeline
- **News Collector** (`collectors/news_collector.py`): Alpaca News API integration
  - 3-day lookback, paginated (50/request), deduplication via `article_id`
  - Multi-ticker: `symbols` ARRAY with GIN index, `is_global` flag for macro news
- **Sentiment Scorer** (`derived/sentiment_scorer.py`): FinBERT local inference
  - ProsusAI/finbert (110M params), batch_size=32, ~23s for 1700 scores
  - Model pre-cached in Docker image to avoid runtime downloads
  - Scores: sentiment_label (positive/negative/neutral), sentiment_score (-1.0..+1.0)
- **DB tables:** `news_articles` + `news_sentiment` (migrations 019 + 020)
- **Feature Pipeline extended:** 9 groups, 55 features (6 new sentiment features)
  - Bugfix: Changed `scored_at` → `published_at` filter (JOIN with `news_articles`)
- **Frontend – Signals Page:** New "Sentiment" tab with Summary + Articles views
- **Frontend – Features Page:** Sentiment column in heatmap + convergence score
- **Log improvements:**
  - CollectorLogCapture: Demote known harmless warnings (HF Hub) to INFO
  - New `run_log_retention()` job: Auto-delete logs older than 90 days (daily 03:30)
- **Scheduler:** 15 jobs total (9 daily, 4 weekly, 1 monthly, 1 maintenance)
- **Documentation:** CLAUDE.md, README.md, ROADMAP.md, SCHEDULER.md, DATABASE.md, DATA_SOURCES.md, ARCHITECTURE.md, DECISIONS_FEATURES.md, SESSION_LOG.md updated

### Session 22 – 13 May 2026 – Signal Filters & Endpoint Bugfix
- **Column filters on Signals page** (`SignalsPage.tsx`):
  - Reusable `useColumnFilters` hook with text inputs in table headers
  - Filterable columns: Ticker, ETF, Typ, Politiker, Partei, Analyst, Aktion
  - Filter count display ("X von Y Einträgen") with clear button
  - URL ticker param from TickerPage pre-fills the ticker column filter
  - Replaced old ticker badge with integrated column filters
- **Insider tab → table layout:** Converted from card grid to consistent data table
  - Columns: Ticker, Score, Insider, Käufe, Verkäufe, Kaufvolumen, Zeitraum
- **Bugfix: `delay_days` missing in ticker-specific endpoint** (`ticker.py`):
  - `/ticker/{symbol}/signals` omitted `delay_days` calculation for politician trades
  - `/signals/politicians` (global) had it → inconsistency when filtering by ticker
  - Silent bug: Pydantic `Optional[int] = None` default masked the missing field
  - Found via user report: Hickenlooper LBRDK trade showed "—" instead of "351d"
- **Documentation:** LEARNINGS.md updated (new finding + meta-learning on endpoint parity)

### Session 23 – 1 September 2026 – Sprint 9.5c Phase 1 (Short Interest + Data Quality + Context Pack)
- **B5: Short Interest Collector** (`f721720`)
  - ORM Models: `ShortVolume` + `ShortInterest` tables (migration 027)
  - `ShortInterestCollector` via Massive API (formerly Polygon), 5 req/min rate limit
  - First run: 757/765 tickers collected, 0 errors, 2h 34min runtime
  - 3 new features: `short_volume_ratio_5d`, `short_volume_ratio_20d`, `short_volume_change_20d`
  - Scheduled daily at 04:45 CET
- **C1: Lag-Gewichtung** – STOCK Act 45-day filter in `_politician_features()`
- **C2: Ticker Parser** – `_normalize_ticker()` leading/trailing strip + regex validation `^[A-Z]{1,5}(\.[A-Z])?$`
- **C3: Party Mapping** (`f721720`, `c309f7d`)
  - `congress_members.py`: 116 members (119th Congress + historical + eFD aliases)
  - `lookup_member()` with suffix stripping (Jr., II, IV) and trailing comma handling
  - Auto-enrichment in `PoliticianTradesCollector.fetch()`
  - Backfill: 853/1.220 historical rows mapped (remaining `Alan Armstrong` = non-senator)
- **F2: Context Pack MVP** (`f721720`)
  - `ContextPackGenerator` (376 lines): preliminary scoring, YAML frontmatter, daily overview
  - Output to `/mnt/user/Workfiles/AlpacaBroker/context_packs` via `CONTEXT_PACK_PATH`
  - Docker Compose volume mount added
  - Scheduled daily at 02:30 CET (after Feature Pipeline)
- **Config:** `POLYGON_API_KEY` + `CONTEXT_PACK_PATH` in Settings + `.env.example`
- **Scheduler:** 21 jobs total (12 daily, 4 weekly, 1 monthly, 1 maintenance, 1 analysis, 2 new)
- **Tests:** 358 passed, 0 failed (no regressions)
- **Documentation:** ROADMAP.md, SCHEDULER.md, DATABASE.md, DATA_SOURCES.md, SESSION_LOG.md updated

### Session 24 – 2 September 2026 – Bugfixes + A1 Stufe 2/3 (Survivorship Bias)
- **Nachtlauf 01./02.09. verifiziert:** Alle 14 Jobs fehlerfrei, beide neuen Jobs (B5 + F2) im Regelbetrieb
- **Context Pack Fix:** (`a11e3cd`)
  - Encoding: utf-8 -> utf-8-sig (BOM) fuer Windows-Kompatibilitaet
  - Volume-Mount fehlte im Compose Manager (nur im Git-Repo, nicht im Stack)
  - Verifiziert: 6 Markdown-Files korrekt geschrieben
- **Feature Analysis Bugfix:** (`9191f47`)
  - `expire_on_commit=False` in SessionFactory (verhindert DetachedInstanceError global)
  - None-Guard fuer `engine.run()` Return (insufficient data case)
  - Doppelten `session.commit()` entfernt (Context Manager committed bereits)
- **A1 Stufe 2: Delisted Ticker Backfill** (`64524ef`)
  - 34 delistete Nasdaq 100 Ticker identifiziert (via `index_membership.valid_to`)
  - 22 Universe-Eintraege erstellt (`is_active=False`)
  - 23.110 Price Bars von Alpaca geladen (23 Ticker, 2021-01-01 bis Delisting-Datum)
- **A1 Stufe 3: Feature Recompute Script** (`64524ef`)
  - `scripts/recompute_features.py` erstellt (112 historische Tage, ~30-60 Min Laufzeit)
  - Ausfuehrung auf Unraid via `docker exec`
- **Tests:** 358 passed, 0 failed

> **Nachtrag (15.10.2026):** Die folgenden Sessions 24a–24e liefen in Antigravity und fehlten bisher im Log. Buchstaben statt neuer Nummern, damit die bestehende Nummerierung (25 ff.) gleich bleibt.

### Session 24a – 3./4. September 2026 – Log-Check + Options-IV-/Estimates-Features (`bda67d8`)
- **Log-Check:** alle 14 Jobs `success`, keine Gaps; Form 4 mit 3 SEC-Timeouts (Retry erfolgreich); Options IV für 717 Ticker
- **Befund:** `scripts/sprint10_readiness.py` prüfte nur 8 Feature-Gruppen; Options IV und Estimates wurden gesammelt, aber nicht als Features genutzt
- **Migration 028:** 9 neue Spalten in `feature_snapshots` (4× Options IV, 5× Estimates-Revisionen), Feature-Pipeline um beide Gruppen erweitert
- **Readiness-Skript:** alle Feature-Gruppen + neuer Check 8 „Data Source Maturity“ (Mindesthistorie je Quelle, IV-Rank 252 Tage nur informativ)

### Session 24b – 8./9. September 2026 – Features-API + Versionslabel
- **Befund:** Neue Feature-Gruppen fehlten auf der Features-Seite – `api/routes/features.py` und `api/schemas.py` hatten eigene Gruppenlisten (`4409caf`: 9 → 16 Gruppen, `total_possible` 55 → 86)
- **UI:** Versionslabel `v0.2.0 · Sprint 8` → `v0.9.5c · Sprint 9.5c` (`b50614a`); nach dem Deploy erst nach Browser-Hard-Reload sichtbar
- **Frage Context-Pack-Ordner:** Ordnername = Datum der Daten (damals `today − 1`), nicht Ausführungsdatum – gewollt. Seit 05.10. = letzte NYSE-Session

### Session 24c – 15. und 20. September 2026 – Log-Checks
- Alle Jobs `success`, 0 Gaps; Options IV ~735 Ticker/Tag, Estimates ~2.976 Zeilen/Tag, Feature-Pipeline ~518 Ticker/Tag
- Nur bekannte Warnungen: Plausibilitätsprüfungen Fundamentals (JOBY, NET, SLMT, GDDY, TEM), einzelne IV-Warnung

### Session 24d – 22. September 2026 – Log-Historie + Data Retention (`a2978cf`)
- **Log-Historie:** ältester Eintrag 24.06.2026 (89 Tage, 1.001 Einträge) – 90-Tage-`log_retention` greift bereits
- **Datenalter:** 2,69 Mio. Zeilen; älter als 5 Jahre: `earnings_calendar` (ab 1999), `insider_trades` (ab 2019), `prices_daily`/`technical_indicators` (ab Nov. 2020)
- **Entscheidung Sebastian:** globale Retention 20 Kalenderquartale, Stichtag = Quartalsbeginn, `earnings_calendar` ausgenommen ([DECISIONS_DATA](DECISIONS_DATA.md)); wöchentlicher Job `data_retention` (So 03:00)

### Session 24e – 1. Oktober 2026 – Dashboard-Tabellen + Readiness-Korrekturen
- **Dashboard:** Tabellen-Statistik 14 → 24 Tabellen (News, Sentiment, Estimates, Options IV, Short Volume/Interest, Makro, Analyse-Reports, Index Membership, Blacklist) (`9db0519`)
- **Readiness** (`a008047`): FRED-Check fragte nicht existierende Tabelle `fred_observations` ab (→ `macro_series`); Schwellen ARK 15 → 3 %, Politiker 20 → 5 %, 13F 10 → 5 %
- **Ergebnis:** 26/30 Checks bestanden. Offen: Snapshot-Anzahl, Historie Options IV/Short Volume/Estimates (zeitabhängig), 13F 0 % (damals noch ohne Ticker), Ranking-Stabilität 40 %
- **Planung Sebastian:** Sprint 10 eventuell ab Mitte Dezember 2026

### Session 25 – 5 October 2026 – Opus 5.5 Konzept- & Code-Review + Bereinigung aller Befunde
- **Review:** Konzept + gesamter Code in 4 Bereichen (Collectors, Feature-Pipeline/ML, Backend/Infra, Frontend)
- **Entscheidung:** Rollierende 20-Quartale-Retention gewollt, keine Backups → Prinzip 2 in CLAUDE.md angepasst, `utils/retention.data_start_date()` ersetzt alle festen Startdaten
- **Neue Shared Utils:** `market_calendar` (NYSE-Session statt Berlin-Datum), `job_status`, `retention`, `alerting` (ntfy-Webhook)
- **Collectors:** 13F-Werte (×1000-Bug) + Dedup-Key (PUT/CALL, Amendments), Split-/Dividenden-Re-Adjustierung mit Full-Refresh + Recompute, SIP-Feed, Insider-Dedup via Accession, Index-Sync-Sanity-Guard, Market-Date-Labels (IV, Estimates, Short Interest), API-Key-Redaction, PARTIAL/FAILED-Status, Gap-Repair, Bulk-Inserts
- **Feature-Pipeline:** Lookahead-Fixes (Insider-Cluster, Politiker, News-Cutoff 16:00 ET, Makro-Lag), neue Targets `close(d+h)/open(d+1)−1`, Upsert inkl. NULL + `feature_version`, nur NYSE-Handelstage, ARK-Exit, Breadth, 0-vs-NULL, Analyse auf Rank-IC + Purged Walk-forward
- **Backend/Infra:** Auth (`X-API-Key`/`X-Requested-With`) auf `/ops/*`, Retention-Bugs (Rollback-Kette, News-FK, `ArkHolding`-Import-Crash), Nightly Chain 04:30, Alerting + Startup-Catch-up, Healthcheck, Non-Root-Container, uv gepinnt, 18 Skripte archiviert
- **Frontend:** strict TS, Fehlerzustände, dynamische Feature-Gruppen, Signal-Marker im Kurschart, Ticker-Filter serverseitig, API-Key-Einstellung
- **Migrationen:** 029 (Collector-Integrität), 030 (Feature-Store), 031 (timestamptz, Cascade-FK, Indizes)
- **Tests:** 352 → 695 passed (Baseline hatte 6 Fehler)
- **Offen:** Deployment + Reparatur-Skripte auf Unraid ausführen, danach R1 (Feature-Analyse) neu

### Session 26 – 5 October 2026 – Deployment, Reparatur, 13F-Ticker + ARK-Universe
- **Deployment:** Migrationen 029–031 live, Preis-Refetch (923k Zeilen, SIP) + Feature-Rebuild (605k Snapshots) erfolgreich, Feature-Analyse auf 510k Snapshots (07/2022–10/2026)
- **Befund:** 13F-Features waren immer leer – Collector speicherte nur CUSIPs (	icker = None)
- **Fix:** collectors/cusip_resolver.py (Cache cusip_map → ARK/Universe → OpenFIGI, optional OPENFIGI_API_KEY), Collector setzt Ticker beim Fetch, scripts/repair/form13f_resolve_tickers.py (Dry-Run, --apply, --backfill 13F-Historie)
- **Universe:** get_universe_as_of = Indexmitglieder ∪ ARK-Holdings der letzten 30 Tage (point-in-time, nur Universe-Ticker) → ARK-Ticker außerhalb der Indizes bekommen wieder Features
- **Docker:** /app gehört dem App-User (mkdir im Container war nicht möglich)
- **Migration:** 032 (cusip_map, Index orm13f_holdings(ticker, report_period))
- **Tests:** 713 passed

### Session 27 – 6 October 2026 – Analyse-Guard + Log-Check 48 h
- **Analyse:** `MIN_INDEPENDENT_PERIODS=10` – Korrelationen mit `reliable`-Flag, Hypothesen mit zu wenig unabhängigen Perioden → `insufficient_data`; aktuell kein robustes Signal
- **Log-Check:** options_iv (05.10.) und estimates (06.10.) lieferten 0 Items – alte Läufe hatten Freitagsdaten unter dem Label 2026-10-05 gespeichert, neuer Code (`last_completed_session`) übersprang deshalb Montag. Context Pack scheiterte an Host-Rechten (Container 99:100)
- **Fix:** scripts/repair/fix_market_date_collision.py (Relabel 05.10. → 02.10., Rerun, Context Packs); Host `chown -R 99:100 /mnt/user/Workfiles/AlpacaBroker`
- **Ergebnis:** IV 657 + Estimates 2984 Zeilen für 05.10. nachgeholt, Features 02.–05.10. neu, Context Packs 02.10./05.10. erzeugt
- **Tests:** 720 passed

### Session 28 – 6 October 2026 – Kurzfrist-Ziel, Barrier-Backtest (K1) + Barrier-Label (K2)
- **Ziel:** 0–5 Long-Kandidaten/Tag, „keine Kandidaten“ erlaubt, Haltedauer ≤ 14 Tage; Hauptkonfiguration +1 % / −2 % / 14 Handelstage ([Konzept](2026-10-06_Konzept_Kurzfrist_Kandidaten.md))
- **K1:** `analysis/barrier.py` + `scripts/analysis/backtest_barrier.py` – aktueller Context-Pack-Score ≈ Zufall (siehe LEARNINGS)
- **K2 (Schritt A):** Migration 033 (`return_barrier_14d`, `barrier_outcome`, `barrier_ambiguous`), `target_backfill.compute_barrier_labels`, Feature-Analyse nutzt das Barrier-Label als Modellziel (Fallback `return_20d` bei < 50 % Abdeckung)
- **Plan:** A Label → B ML P(Treffer) mit Walk-forward + Schwelle → C Hyperopt nur für Trade-Parameter → D neue Quellen
- **Ergebnis Analyse (527k Snapshots):** kein signifikantes Feature für das Barrier-Ziel, LASSO wählt nichts, RF-Importance ≈ 0
- **Tests:** 740 passed
- **A2:** 9 Kurzfrist-Kennzahlen `st_*` (`derived/short_term_features.py`, Migration 034, `FEATURE_VERSION` 2026.10-2), Backfill-Skript `scripts/repair/backfill_short_term_features.py` (621k Zeilen in 199 s), Neuberechnung nach Kurs-Readjustierung
- **Ergebnis A2:** `st_close_location` (t 3,5) und `st_earnings_reaction` (t 2,7) jetzt beste Kennzahlen, aber weiterhin nicht signifikant, LASSO leer → Effekt-Richtung wie Literatur, Größe zu klein
- **Tests:** 755 passed
- **Schritt B:** `analysis/hit_model.py` + `scripts/analysis/walkforward_hit_model.py` (Design vorab in Konzept §7.2 festgelegt), Fix: Spaltenfilter je Fold für HGB
- **Ergebnis B:** Tages-AUC ≈ 0,50, Holdout +0,062 %/Trade (konservativ −0,039 %), Differenz zum Universum n. s. → **nicht bestanden**; C (Hyperopt) zurückgestellt ([Bericht](reports/2026-10-06_hit_model_walkforward.md))
- **Tests:** 766 passed
- **Schritt D vorbereitet:** Konzept §7.4 (Event-Studien D1 8-K-Rückkäufe, D2 Index-Aufnahmen, vorab festgelegt) + §7.5 Risiko-Vorfilter
- **Vorfilter (Stufe 1):** `context_pack_generator.prefilter_reasons` – ATR 1–3 %, keine Earnings ≤ 21 Kalendertage, Liquidität; Übersicht mit Ausschlussgründen und „Heute keine Kandidaten“, Frontmatter `selection: risk_prefilter`, `ranking_validated: false`; im Mittel 216–308 geeignete Aktien/Tag (2022–2026)
- **Tests:** 781 passed
- **Schritt D:** `collectors/buyback_events.py` (EDGAR-Volltextsuche + Satz-Klassifizierer), `analysis/event_study.py`, Skripte `scripts/analysis/collect_buyback_events.py` (CSV + Satz-Cache) und `scripts/analysis/event_study.py` (`--source buyback|index_add`), nur lesend
- **Ergebnis D:** D1 Rückkäufe 648 Trades, Ø −0,063 %, Differenz n. s. → **nicht bestanden**; D2 Index-Aufnahmen −0,32 % vs. Universum (explorativ, nur Hinweis) ([Konzept §7.6](2026-10-06_Konzept_Kurzfrist_Kandidaten.md))
- **Tests:** 822 passed


### Session 29 – 7 October 2026 – Log-Check + Vorwärtstest Stufe 2 (K5)
- **Log-Check Nacht 06./07.10.:** alle Läufe `success`, Nightly Chain für Session 2026-10-06 vollständig (Context Pack erzeugt); Adjustment-Wechsel bei 4 Tickern (A, DG, DGX, JPM) automatisch neu geladen; short_interest wie immer ~2,5 h. Risiko-Vorfilter (`98049b1`) noch nicht deployt
- **K5:** Konzept §7.7 (vorab festgelegt), Format `docs/STAGE2_DECISIONS.md`, Migration 035 (`stage2_reviews`, `stage2_decisions`), `derived/stage2_decisions.py` (Einlesen + Prüfung, rechtzeitig = vor Eröffnung d+1), `analysis/stage2_eval.py` (Vergleich mit geeigneten Aktien/Universum, Urteil ab 100 Käufen), Nachtlauf-Schritt `stage2_review` → `context_packs/stage2_auswertung.md`
- **Context Pack:** Abschnitt „Rückmeldung Stufe 2“ mit YAML-Vorlage; Index-Warnhinweis (Aufnahme ±7 Kalendertage, Hinweis aus D2) in Übersicht und Kandidaten-Dokument
- **Abhängigkeit:** `pyyaml` explizit in `pyproject.toml` (bisher nur transitiv)
- **Tests:** 850 passed

### Session 30 – 15. Oktober 2026 – Log-Check 01.–15.10. + zwei Robustheits-Fixes
- **Log-Check (278 Läufe, 26 Collector-Namen):** seit 07.10. alle Läufe `success`, alle 24 Jobs eingeplant, Nightly Chain inkl. `stage2_review` täglich vollständig; am Wochenende `skipped` (keine neue Session) wie vorgesehen
- **Erster `data_retention`-Lauf** So 11.10. 03:00: Stichtag 2021-10-01, 135.084 Zeilen gelöscht (`prices_daily` 133.954, `macro_series` 943, `technical_indicators` 187), ~10 s
- **Einmalige Fehler 05./06.10.** (Übergang Non-Root-Container): Context Pack `PermissionError` auf root-eigenen Host-Dateien, Matplotlib-Cache-Warnung; `target_backfill`-Deadlock 05.10. 12:10 bei manuellem Chain-Lauf gegen einen nicht protokollierten parallelen Schreiber
- **Fix `050ff32`:** Dashboard-Tabellenstatistik normalisiert Timestamps auf `date` – `news_articles`/`news_sentiment` zeigten `row_count = -1` (Pydantic-Validierung im Catch-all)
- **Fix `97fcdb0`:** `target_backfill` wiederholt bei PostgreSQL-Deadlock (40P01) bis zu 3× (5 s/15 s); gefahrlos, da nur Zeilen mit NULL-Targets bearbeitet werden
- **Einordnung (kein Fehler):** `insider_trades`/`form13f_holdings` zeigen im Dashboard Daten vor dem Stichtag (Retention nach `filing_date`, Anzeige nach Ereignisdatum); `short_interest` leer (Collector schreibt nur `short_volume`); 2.594 Gaps stammen aus dem manuellen Preis-Refetch 05.10. (2.545 repariert)
- **Tests:** 52 passend gefilterte Tests grün (`-k "job or dashboard or backfill or retention"`)

### Session 31 – 15. Oktober 2026 – Übergabe an Claude Code
Die Arbeit geht ab jetzt in Claude Code weiter. Dokumentation vollständig nachgezogen (dieser Commit, nur `*.md`).

**Deploy-Stand auf Unraid (geprüft 15.10.2026 ~10:10 über die API):**
- **Deployt: `1583d59`** (07.10.2026) – Indizien: Alembic-Revision `035`, Container-Uptime seit 07.10. ~07:30, Job `stage2_review` läuft nächtlich, Dashboard zeigt bei `news_*` noch `-1`
- **Nicht deployt:** `050ff32` (Dashboard-Datumsfix), `97fcdb0` (Deadlock-Retry) und der Doku-Commit dieser Session (ohne Laufzeitwirkung)
- Preis-Feed in Produktion: **SIP** (Log `prices_alpaca`: `feed=sip`, kein IEX-Fallback)

**Reparatur-Skripte (`scripts/repair/`):**

| Skript | Status | Beleg |
|---|---|---|
| `deploy_review_fixes.sh` | ✅ gelaufen 05.10. | Session 26, Migrationen 029–031 live |
| `collectors_refetch_prices.py` | ✅ gelaufen 05.10. | Session 26 (SIP-Refetch), Log `prices_alpaca` 05.10. 11:56 |
| `features_rebuild.py` | ✅ gelaufen 05.10. | Session 26; `feature_snapshots` ab 2021-10-01 (626k Zeilen) |
| `form13f_resolve_tickers.py` | ✅ gelaufen (`--apply`, Historie) | 619.780 Zeilen ab 2021-03-31; 13F-Features am letzten Snapshot für 597/647 Ticker gefüllt |
| `fix_market_date_collision.py` | ✅ gelaufen 06.10. | Session 27 (inkl. Context Packs 02.10./05.10.) |
| `backfill_short_term_features.py` | ✅ gelaufen 06.10. | Session 28 (621k Zeilen) |

Offen ist kein Reparatur-Skript. **Nicht über die API prüfbar:** ob nach dem 13F-Ticker-Backfill auch die historischen 13F-Feature-Spalten neu berechnet wurden (`features_rebuild.py --apply --skip-ta --skip-clusters`). Prüfung per SQL: `SELECT date_trunc('quarter', snapshot_date) q, count(*) FILTER (WHERE form13f_holder_delta_qoq IS NOT NULL) FROM signals.feature_snapshots GROUP BY 1 ORDER BY 1;`

**Offene manuelle Schritte auf Unraid:**
1. `97fcdb0` deployen: `cd /mnt/user/appdata/alpaca-broker && git pull`, dann Compose Manager → Stack „Alpaca-Broker“ → Compose Down → Compose Up (nicht zwischen 04:30 und 05:00). Danach prüfen: Dashboard zeigt echte Zeilenzahlen für `news_articles`/`news_sentiment`.
2. Prüfen, ob `/mnt/user/Workfiles/AlpacaBroker/context_packs/2026-10-05/` existiert (laut Session 27 vom Reparatur-Skript erzeugt). Nur falls nicht: `fix_market_date_collision.py --context-packs 2026-10-05` im Container ausführen (ohne `-u root`).
3. Optional: SQL-Prüfung der historischen 13F-Features (siehe oben).

**Angefangene/unterbrochene Arbeiten:** keine im Code. K5 (Vorwärtstest Stufe 2) ist deployt und wartet auf `decisions.yaml`-Dateien des Skills – bis 15.10. hat `stage2_review` 0 Dateien eingelesen. F1 (Composite Score) offen, nach den K-Ergebnissen zurückgestellt.

**Bekannte Probleme:**
- Die Tests `test_store_writes_trades` und `test_store_dedup_counts` (`tests/unit/test_politician_trades.py`) übergeben zwar eine `MagicMock`-Session, `PoliticianTradesCollector.store()` ruft aber `NewTickerOnboarder` auf, der eine **echte DB-Session** öffnet. Nur mit erreichbarer Datenbank lauffähig, nicht isoliert (anders als der Test bei Zeile ~572, der `NewTickerOnboarder` patcht). Dokumentiert, nicht behoben.
- `short_interest`-Tabelle leer (nur `short_volume` wird gesammelt).
- **Migrationen 024/025:** beide fügen `feature_snapshots.sector_relative_return_20d`/`sector_relative_momentum` hinzu, 025 ohne Existenzprüfung → `alembic upgrade head` auf einer **leeren** Datenbank scheitert vermutlich an 025. Produktion (bereits auf 035) nicht betroffen. Weitere kleine Abweichungen Migration ↔ ORM sind in [DATABASE.md](DATABASE.md) notiert.
- `registry.NIGHTLY_UPSTREAM` markiert `prices_alpaca` als kritisch (`True`), `check_upstream()` wertet das Flag aber nicht aus.
- Dashboard-Datumsbereich von `insider_trades`/`form13f_holdings` zeigt Ereignisdaten, nicht das Retention-Datum.
- Bekannte, harmlose Warnungen: SEC-Timeouts bei Form 4 (Retry ok), Fundamentals-Plausibilitätsprüfungen.

**Nächster konkreter Schritt:** `97fcdb0` auf Unraid deployen und am Folgetag den Log-Check wiederholen (Nightly Chain, Dashboard `news_*`). Danach die beiden Politician-Tests isolieren (`NewTickerOnboarder` patchen) und `scripts/sprint10_readiness.py` erneut laufen lassen.

### Session 32 – 15. Oktober 2026 – Übernahme in Claude Code
- **Prüfung für den Wechsel:** Codebasis, Doku und Konfiguration nur lesend geprüft, Ergebnis in `.claude/UEBERNAHME_PRUEFUNG.md` (nach der Übernahme entfernt, siehe Git-Historie)
- **Abschluss Antigravity:** Abschluss-Auftrag `.claude/ANTIGRAVITY_ABSCHLUSS.md` (nach der Übernahme entfernt, siehe Git-Historie) → Doku-Commit `e09cc90`. Nachprüfung am Code: DATABASE.md deckt alle 35 Migrationen (001–035) und alle 27 ORM-Tabellen ab, SCHEDULER.md alle 24 Jobs aus `scheduler/setup.py`; alle 68 `.py`-Dateinamen in ARCHITECTURE.md existieren; relative Links gültig (nur 6 absolute `file://`-Links im archivierten `docs/archive/implementation_plan1.md`, inzwischen entfernt); keine Secrets im Diff
- **Befunde bestätigt:** Migrationen 024 und 025 fügen beide `feature_snapshots.sector_relative_return_20d`/`sector_relative_momentum` hinzu (025 ohne Existenzprüfung); das Flag „kritisch“ in `registry.NIGHTLY_UPSTREAM` wird von `check_upstream()` nicht ausgewertet
- **Fix Test-Isolation:** `test_store_writes_trades` und `test_store_dedup_counts` (`tests/unit/test_politician_trades.py`) patchen jetzt `trading_signals.universe.onboarder.NewTickerOnboarder` (wie der Test bei Zeile ~572) → keine Unit-Tests mehr mit DB-Verbindung (geprüft mit gesperrtem DB-Zugang `DB_HOST=127.0.0.1`, `DB_PORT=1`)
- **Entscheidung (Sebastian):** Weiterentwicklung in Claude Code, Antigravity bearbeitet das Repo nicht mehr; die Repo-Doku bleibt das einzige Projektgedächtnis ([DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md))
- **Doku:** CLAUDE.md (Status, Tests, Model Routing → Claude Code), ARCHITECTURE.md (Entwicklungsumgebung), Entscheidung in DECISIONS_ARCHITECTURE.md, INDEX.md (Übernahme-Notizen), ROADMAP.md (Test-Isolation erledigt)
- **Zugriff:** kein SSH auf Unraid → Deploy bleibt manueller Schritt für Sebastian. Freigabe per IP (`\\192.168.1.93\Workfiles`) verweigert, per Hostname `\\HOMENAS\Workfiles` lesbar → Context Packs und `decisions.yaml` sind von Claude Code aus prüfbar
- **Deploy (Sebastian, 15.10.):** `1583d59` → `b60038e`, Build ok. API danach: neuer Container, Alembic `035`, 24 Jobs, `news_articles` 72.308 / `news_sentiment` 144.727 Zeilen (vorher `-1`), keine Tabelle mehr mit negativer Zeilenzahl → `050ff32` und `97fcdb0` live. `context_packs/2026-10-05/` existiert (Übersicht + 5 Kandidaten, erzeugt 06.10.) → kein Repair nötig
- **Tests:** 850 passed (ohne DB)

### Session 33 – 16. Oktober 2026 – Claude-Code-Einrichtung, Altlasten entfernt, Unraid über WebGUI
- **Arbeitsweise (Sebastian):** agentenbasiert, je Aufgabe das günstigste passende Modell (Haiku/Sonnet/Opus), so autonom wie möglich → in CLAUDE.md („Working Agreements“) festgehalten
- **Altlasten entfernt** (Subagent, Sonnet): `docs/archive/` (Antigravity-Pläne Sprint 8), `klaerungspunkte_datenerweiterung.md`, `2026-08-18_Anmerkungen_Datenerweiterung.md`, Übernahme-Notizen in `.claude/`. Vorher übertragen: Entscheidungen 18.08. (Survivorship/Lookahead, freie Quellen, Guardrails/Pack-Format) in DECISIONS_DATA/FEATURES, Lag-Befund Politiker in LEARNINGS, offene Konzeptpunkte in ROADMAP (Backlog, F1). Keine ChatGPT-/Gemini-Dateien im Repo gefunden (Code-Treffer nur CSS-`cursor`, `conn.cursor()`, `astral-sh`)
- **Claude-Setup** (Subagent, Sonnet, nachgeprüft): CLAUDE.md neu gegliedert (Commands, Working Agreements, Invariants & Gotchas, WebGUI-Scope), `.claude/agents/` (`log-checker` Haiku, `doc-keeper` Sonnet, `code-reviewer` Opus), `.claude/skills/` (`/log-check`, `/deploy`, `/session-end`), `.claude/settings.json` (Freigaben, `.env` gesperrt), ARCHITECTURE „Operations Access“
- **Unraid-Zugang:** SSH auf dem Server aus; vorbereiteter SSH-Weg (Forced-Command-Key) auf Wunsch verworfen und Schlüssel gelöscht. Stattdessen WebGUI im Browser von Claude Code (Sebastian loggt sich ein) + ein User Script `alpaca-ops` (Logik `infra/unraid/alpaca-ops.sh`, per `git show` von `origin/main`, Ausgabe nach `Workfiles/AlpacaBroker/ops/`) → Entscheidung in DECISIONS_ARCHITECTURE. Erster Lauf `status` ok: Container healthy seit 15.10., deployt `b60038e`
- **Backup-Prüfung:** kein Cron-Eintrag und kein User Script greift auf die Alpaca-DB zu → kein alter `pg_dump`-Job (ROADMAP-Punkt erledigt)
- **Unraid-Umfeld:** Unraid 7.3.2 (Update 7.3.3 verfügbar – Sache von Sebastian), Plugins User Scripts + Compose Manager; weitere Skripte auf dem Host gehören zu anderen Projekten („Hashwatch“) und sind tabu
- **Log-Check Nacht 15./16.10.** (erster Nachtlauf mit `b60038e`, Haiku-Agent nach `log-checker`): alle 24 Jobs `success`, nichts `failed`/`partial`; Nightly Chain `session=2026-10-15` vollständig (TA, Features, Targets, `stage2_review`, Context Pack mit 5 Kandidaten); kein Deadlock-Retry nötig; keine negativen Zeilenzahlen; `max_date` Preise/TA/Features = 2026-10-15
- **K5:** `stage2_auswertung.md` meldet 0 eingelesene `decisions.yaml` seit 07.10. – so gewollt: Vorwärtstests warten laut Sebastian, bis die Sprints durch sind und die Daten für Sprint 10 reichen (Entscheidung in DECISIONS_FEATURES)
- **Sprint-10-Readiness** (auf Sebastians Anweisung über `alpaca-ops run scripts/sprint10_readiness.py`; erster Versuch ohne Anweisung von der automatischen Rechteprüfung blockiert): **29/31, „GO“** (01.10.: 26/30). ML-Fenster ab 2022-07-28: 1.059 Handelstage, 532.264 Snapshots, 685 Ticker; Targets 1d–60d 92–99 % gefüllt; 18 Earnings-Quartale; SPY-Monatsrenditen σ 4,1 % (34 positive, 17 negative Monate); 5 Monatsberichte. **Nicht erfüllt:** Ranking-Stabilität 0/10 (Top-10 erster vs. letzter Monatsbericht, 01.10.: 40 %), Options IV 33 Handelstage (min. 60), Estimates 52 (min. 60). Einordnung: Datenmenge reicht, ein stabiles Signal ist weiterhin nicht belegt (passt zu K1–K4); die neuen Quellen (IV, Estimates, Short Volume) haben erst 1–2 Monate Historie, die Abdeckung in Abschnitt 3 wird nur am letzten Tag gemessen
- **Bedienung:** „Run In Background“ in User Scripts fragt kein Argument ab und nimmt den Standard `status` → für andere Befehle immer „Run Script“ (Argument-Dialog); die Ausgabe erscheint dort live im Popup und zusätzlich in `ops/<befehl>.log`
- **Migrationen 024/025** (Subagent Sonnet, von mir gegengeprüft): Ursache bestätigt – 024 wurde nach dem Einspielen in Produktion erweitert (`81446a4`), 025 (`32168a0`) legte die Spalten dort an; frisches `upgrade head` brach an 025 mit `DuplicateColumn` ab. Fix: 025 `ADD COLUMN IF NOT EXISTS`, Downgrade no-op (024 besitzt die Spalten). Geprüft auf Wegwerf-`postgres:18` (Docker Desktop lokal, alle `DB_*` überschrieben): frisch bis 035, Round Trip `025 → 023 → head`, Produktionshistorie simuliert; Produktion unberührt (läuft 025 nie erneut), kein Deploy nötig. Regel + Testbefehl in CLAUDE.md, Entscheidung in DECISIONS_ARCHITECTURE
- **Befund Migration ↔ ORM:** 39 Abweichungen (Nullability, Indizes nur in Migrationen, FK `technical_indicators → universe` fehlt, `DESC`-Index); `alembic check` unbrauchbar ohne `include_schemas=True` in `env.py` → neuer offener Punkt in ROADMAP, Details in DATABASE.md
- **Tests:** 850 passed (ohne DB)
