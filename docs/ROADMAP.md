# ROADMAP.md – Sprint Planning & Progress

> **Living document.** Updated after every sprint.
> See [SESSION_LOG.md](SESSION_LOG.md) for detailed session-by-session history.
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** 16 October 2026 (legacy AI-workflow documents removed; takeover in Claude Code – see [SESSION_LOG.md](SESSION_LOG.md), Session 32)

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
| 9.5| Data Hardening & Extension (+ Oct review fixes, short-term research K1–K5) | 🟡 In Progress | Aug–Oct 2026 |
| 10 | Signal Scoring Models | 🔴 Open | planned from mid-Dec 2026 (Sebastian, 2026-10-01) |
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
- [x] **A1 (Stufe 2/3):** ✅ `64524ef`
  - [x] Backfill price data for delisted tickers (23 tickers, 23.110 bars via Alpaca)
  - [x] 22 universe entries created (`is_active=False`)
  - [x] `scripts/recompute_features.py` — re-runs FeaturePipeline for 112 historical dates
  - [ ] Manual "Recompute History" button in Settings UI *(deferred — script-based for now)*
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
  - Scheduled daily at 23:15 Europe/Berlin (since Oct 2026; originally 04:30). IV-Rank needs ~1 year buildup (usable ~Sept 2027)
  - NYSE trading day check + zero-data warning on trading days
- [x] **B5:** Short Interest Collector ✅ `f721720` — *completed in 9.5c Phase 1 (Sept 2026)*

### Sprint 9.5c – Candidate Pipeline MVP 🟡 In Progress
*Minimum viable candidate selection and Context Pack generation.*
*Phase 1 (Sept): Datenqualität + letzte Datenquellen + Context Pack.*
*Phase 2 (ab Okt): ML-Analyse-Update mit allen neuen Features → Composite Score.*

**Phase 1 — Sept 2026:** ✅ Complete (`f721720`, `c309f7d`)
- [x] **B5:** Short Interest Collector (Massive/Polygon API, 757/765 Ticker, 0 Errors)
  - Scheduled daily at 00:15 Europe/Berlin since Oct 2026 (originally 04:45; ~2.5h runtime at free tier 5 req/min). Writes only `short_volume`; the `short_interest` table stays empty (see open points)
  - 3 new features: `short_volume_ratio_5d`, `short_volume_ratio_20d`, `short_volume_change_20d`
- [x] **C1:** Politician Trades Lag Filter (max 45 days, STOCK Act compliance)
- [x] **C2:** Ticker Parser Fix (leading/trailing strip + regex validation)
- [x] **C3:** Party Mapping (116 Congress Members dict + auto-enrichment + eFD name aliases)
  - Backfill: 853/1.220 historical rows enriched (remaining are non-senators)
- [x] **F2:** Context Pack MVP (daily Markdown reports with YAML frontmatter)
  - [x] Output to `CONTEXT_PACK_PATH` on Unraid (`/mnt/user/Workfiles/AlpacaBroker/context_packs`)
  - [x] Daily overview (`00_uebersicht.md`) + per-candidate files
  - [x] Preliminary scoring based on Sprint 9 ML importance weights
  - Since Oct 2026 last step of the `nightly_chain` (04:30 Europe/Berlin, folder = last NYSE session; originally 02:30). Since `98049b1`: stage-1 risk pre-filter instead of the unvalidated score ranking (`selection: risk_prefilter`, `ranking_validated: false`), "keine Kandidaten" allowed

**Phase 2 — Ab Okt 2026:** 🟡 R1 + K1–K4 erledigt, K5 läuft (wartet auf Daten), F1 zurückgestellt
- [x] **R1:** Feature Analysis Re-Run — ✅ 05./06.10.2026 auf dem neu gebauten Feature-Store (Rank-IC + Purged Walk-forward, 510k–527k Snapshots, 07/2022–10/2026). Ergebnis: **kein robustes Signal** (0 signifikante Features, LASSO leer), siehe LEARNINGS 2026-10-06
  - Correlations + Feature Importance neu berechnen mit **allen** neuen Features:
    - 9.5b: 19 Features (Macro, SUE, Breadth, Sector, Insider, 13F, Liquidity, Sentiment)
    - 9.5b: Options IV (ATM IV, Skew, Term Slope, Put/Call OI)
    - 9.5c: Short Interest (B5, 3 Features)
  - Monatlich automatisch (1. des Monats, 07:00) via `feature_analysis`, oder manuell (`POST /analysis/trigger`); nächster Lauf 01.11.2026
- [ ] **F1:** Rule-based Composite Score + Guardrails — offen; Einschätzung bei der Übergabe: ohne validiertes Ranking (R1, K1–K4 ohne Edge) derzeit nicht sinnvoll gewichtbar. Context Pack nutzt bis dahin den Risiko-Vorfilter (Stufe 1)
  - [ ] Min. liquidity, max 2/sector, pairwise correlation, churn lock, min-score — Schwellen **nicht hartcodiert**, sondern über die Einstellungen in der UI konfigurierbar (Entscheidung Sebastian, [DECISIONS_FEATURES](DECISIONS_FEATURES.md) 2026-08-18); Earnings in ≤ 5 Handelstagen markieren statt ausschließen (Konzept §5.1)
  - [ ] `candidate_selections` (append-only, mit Feature-Attribution je Auswahl) + `candidate_rejections` (Guardrail-Ausschlüsse) tables (Konzept §5.2, §8)
- [ ] **K1–K5:** Kurzfrist-Kandidaten ([Konzept 2026-10-06](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)) — K1–K4 abgeschlossen (alle ohne verwertbaren Edge), K5 läuft
  - [x] K1: Barrier-Backtest des aktuellen Scores (TP 0,5–1 %, SL 2–3×, max. 14 Tage) — Ergebnis: ≈ Zufall, siehe LEARNINGS 2026-10-06
  - [x] K2 (Schritt A): Barrier-Label +1 %/−2 %/14d (`return_barrier_14d`, Migration 033) als Zielgröße der Feature-Analyse — Ergebnis: kein robustes Signal, siehe LEARNINGS 2026-10-06
  - [x] K2b (Schritt A2): 9 Kurzfrist-Kennzahlen `st_*` aus Tageskursen (Migration 034, Backfill-Skript), Analyse neu und mit Stand A vergleichen — Ergebnis: erste Hinweise (Schlusslage, Earnings-Reaktion), nicht signifikant
  - [x] K3 (Schritt B): ML-Modell P(Treffer) (Logit L2 + Gradient Boosting), Purged Walk-forward, Schwelle → 0–5 Kandidaten oder „keine Kandidaten“ — Ergebnis: **nicht bestanden** (AUC ≈ 0,50, Holdout n. s.), siehe [Bericht](reports/2026-10-06_hit_model_walkforward.md)
  - [ ] K3b (Schritt C): Hyperopt nur für Trade-Parameter (Ziel/Stop, Schwelle, Filter), Bestätigung auf Holdout — ⏸ zurückgestellt, bis ein Modell den Walk-forward besteht
  - [x] K4 (Schritt D): Event-Studien 8-K-Rückkäufe (D1) und Index-Aufnahmen (D2) — Ergebnis: **D1 nicht bestanden** (648 Trades, Ø −0,06 %, n. s.), D2 nur Hinweis auf Schwäche nach Aufnahme; Reddit-Hype (D3) mangels Historie zurückgestellt, siehe [Konzept §7.6](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)
  - [ ] K5: Vorwärtstest Stufe 2 – Skill schreibt `decisions.yaml` ([Format](STAGE2_DECISIONS.md)), Nachtlauf-Schritt `stage2_review` (Migration 035) bewertet mit der Barrier-Regel, Bericht `context_packs/stage2_auswertung.md`; Index-Warnhinweis im Context Pack ([Konzept §7.7](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)) — Code fertig und seit 07.10. deployt (`1583d59`), läuft nächtlich. **⏸ Bewusst zurückgestellt** (Sebastian, 16.10.): Vorwärtstests erst, wenn die Sprints durch sind und die Daten reichen (Sprint-10-Readiness) – bis dahin keine `decisions.yaml` erwartet. Urteil frühestens ab 100 abgeschlossenen Käufen
  - [ ] K3 erneut (Walk-forward mit längerer Alternativdaten-Historie, neuer Holdout ab 2026-Q4) — frühestens Januar 2027

### Betrieb & Wartung (Sept–Okt 2026)
- [x] Options IV + Estimates als Feature-Gruppen, Migration 028 (`bda67d8`), Features-API/Schemas nachgezogen (`4409caf`)
- [x] Globale Data Retention 20 Quartale, wöchentlich So 03:00 (`a2978cf`, verfeinert im Review 05.10.); erster Lauf 11.10.: 135.084 Zeilen gelöscht
- [x] Dashboard-Tabellenstatistik 14 → 24 Tabellen (`9db0519`); Datumsfix für `news_*` (`050ff32`, **nicht deployt**)
- [x] Sprint-10-Readiness: FRED-Tabellenname + realistische Schwellen für dünne Gruppen (`a008047`); Stand 01.10.: 26/30
- [x] Review-Fixes 05.10. (Migrationen 029–031), 13F-CUSIP-Auflösung (032), alle Reparatur-Skripte gelaufen (Details: [SESSION_LOG](SESSION_LOG.md), Session 31)
- [x] `target_backfill` mit Deadlock-Retry (`97fcdb0`, **nicht deployt**)

### Offene Punkte (Stand Übergabe 15.10.2026)
- [x] **Deploy** `050ff32` + `97fcdb0` auf Unraid – deployt am 15.10. als `b60038e` (Session 32), Dashboard zeigt echte Zeilenzahlen für `news_*`
- [x] **Test-Isolation:** `test_store_writes_trades` und `test_store_dedup_counts` (`tests/unit/test_politician_trades.py`) öffneten über `NewTickerOnboarder` eine **echte DB-Session** – behoben am 15.10. in Session 32 (`NewTickerOnboarder` gepatcht, 850 Tests laufen ohne DB)
- [x] **Migrationskette auf leerer DB:** 024 und 025 legten beide `sector_relative_*` an → frisches `upgrade head` brach an 025 ab. Behoben 16.10. (Session 33): 025 idempotent (`ADD COLUMN IF NOT EXISTS`, Downgrade no-op), auf Wegwerf-`postgres:18` geprüft; Produktion unberührt
- [x] **Migration ↔ ORM angeglichen** (16.10., Session 33): 39 Abweichungen behoben, indem das ORM an das migrierte Schema angepasst wurde (Produktion unverändert); `alembic check` schema-aware inkl. Server-Defaults, Ergebnis „No new upgrade operations detected“ ([DATABASE.md](DATABASE.md))
- [x] `registry.NIGHTLY_UPSTREAM` „kritisch“ wirksam (16.10., Session 33): veraltete Preise oder Kurs-Abdeckung der Session < 80 % der Vorsession → Features + Context Pack übersprungen, Chain `failed` mit Reparatur-Hinweis; übrige Eingänge weiter nur `partial` ([SCHEDULER.md](SCHEDULER.md)). Dazu **automatisches Nachholen** fehlender Sessions (letzte 10 Handelstage, datenbasiert über die Snapshot-Abdeckung, sobald die Kurse vollständig sind)
- [ ] `short_interest`-Tabelle (Migration 027) wird von keinem Collector befüllt – FINRA-Short-Interest (zweiwöchentlich) sammeln oder Tabelle bewusst leer lassen
- [x] Historische 13F-Feature-Spalten nach dem Ticker-Backfill geprüft (16.10., Session 33, Lese-Abfrage): `cusip_map` aufgelöst 05.10. 17:58–19:18, gesamte Feature-Historie direkt danach neu berechnet (19:18–23:27) → 13F-Abdeckung ab Dez 2021 konstant 89–93 % (heute 92,3 %), `delta_qoq` ab März 2022. Anlaufphase erwartet: erste 13F-Meldung in der DB 12.11.2021 (Okt 2021 0 %, Nov 50 %). Keine Neuberechnung nötig; ML-Fenster ab 28.07.2022 voll abgedeckt
- [x] Sprint-10-Readiness erneut laufen lassen – 16.10. (`alpaca-ops run scripts/sprint10_readiness.py`): **29/31, „GO“** (01.10.: 26/30). Offen: Ranking-Stabilität **0/10** Top-10-Überschneidung erster vs. letzter Monatsbericht (01.10.: 40 %), Options IV 33 Handelstage (min. 60, ≈ 20.11.), Estimates 52 (min. 60 ≈ 27.10., 90 für Revisions-Momentum ≈ 10.12.); IV-Rank 252 Tage ≈ September 2027 (nicht blockierend)
- [ ] Readiness Anfang Dezember erneut prüfen (nach den Monatsberichten 01.11./01.12.): Ranking-Stabilität, Options-IV-/Estimates-Historie. Hinweis: Abschnitt 3 des Skripts misst die Feature-Abdeckung nur am letzten Tag – IV, Estimates und Short Volume haben erst 1–2 Monate Historie und fehlen im ML-Fenster ab 2022 fast überall
- [ ] `sector_relative_momentum` erneut prüfen (Nov 2026), IV-Rank ab ~252 Sessions IV-Historie (IV-Daten seit 31.08.2026 → ~September 2027)
- [ ] „Recompute History“-Button in den Einstellungen (zurückgestellt, skriptbasiert)
- [x] Auf Unraid prüfen, ob noch ein alter täglicher `pg_dump`-Cron existiert – geprüft 16.10. mit `alpaca-ops status`: kein Cron-Eintrag und kein User Script greift auf die Alpaca-DB zu (Session 33)

### Nächste Schritte
1. ✅ `b60038e` deployt (15.10.); Log-Check am 16.10. (erste Nightly Chain mit neuem Image)
2. ✅ Politician-Tests isoliert (Session 32), Migrationskette 024/025 auf leerer DB behoben (Session 33); Migration ↔ ORM angeglichen (Session 33)
3. Daten weiter sammeln, Readiness Anfang Dezember erneut prüfen (16.10.: 29/31 „GO“, aber Ranking-Stabilität 0/10); K5 (Vorwärtstest) bewusst zurückgestellt bis nach den Sprints
4. Sprint 10 frühestens ab Mitte Dezember 2026 – Inhalt hängt davon ab, ob bis dahin ein Ranking den Walk-forward besteht (K3 erneut frühestens Januar 2027)

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
- 24 scheduler jobs registered (`scheduler/setup.py`): 13 daily (incl. `nightly_chain`, `log_retention`), 5 weekly (incl. `data_retention`), 2 monthly (`index_sync`, `feature_analysis`), 4 paused chain steps (`feature_pipeline`, `target_backfill`, `stage2_review`, `context_pack_generator`) – see [SCHEDULER.md](SCHEDULER.md)
- Volume mount: `/mnt/user/Workfiles/AlpacaBroker` for Context Pack output (must be owned by `99:100`)
- **Deployed as of 2026-10-15:** `1583d59` (not yet: `050ff32`, `97fcdb0`)

**Update workflow:**
1. `git push` from Windows
2. Unraid terminal: `cd /mnt/user/appdata/alpaca-broker && git pull && docker compose -f /boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml up --build -d` (alternatively Compose Manager → Stack "Alpaca-Broker" → Compose Down → Compose Up). Avoid 04:30–05:00 (nightly chain).

---

## Long-term Ideas (Backlog)

- **News sentiment:** ~~NLP analysis of headlines with Haiku~~ ✅ Implemented with FinBERT (Sprint 8c). Haiku upgrade planned for Sprint 9.
- **Reddit/social sentiment:** StockTwits, WSB mentions as contrarian signal
- **Options flow:** Unusual options activity (paid flow feeds at ≈ 150 USD/month not recommended, [concept §11](2026-08-18_Konzept_Datenerweiterung.md))
- **ML models:** XGBoost, neural networks after 12+ months of data
- **Multiple ETFs:** Not only ARK but other "smart money" funds
- **European stocks:** German/European small caps
- **Live trading:** Only after 12+ months of successful paper trading
- **Context-pack extensions ([concept 2026-08-18](2026-08-18_Konzept_Datenerweiterung.md) §6, not implemented):** cross-sectional percentiles (universe/sector), own point-in-time history blocks (estimates, ratings, fundamentals drift), signal stack with availability dates, peer set computed from the universe, and the historical analogue (feature constellation → 20/60-day forward returns with hit rate and sample size, §6.2). The current pack has the stage-1 risk pre-filter, provisional score attribution and the stage-2 feedback template. The analogue only makes sense once a ranking passes the walk-forward (K3).
- **Small data/feature ideas from the same concept, not implemented:** insider buy ratio split into discretionary vs. planned 10b5-1 sales and a 180-day window (E2; the Form 4 10b5-1 flag is not collected); 13F holder concentration (top-10 share, E1); `market_cap_bucket` in `universe` (B4); exponential lag decay for politician trades instead of the hard 45-day cut-off (C1); quarantine table for rejected ticker symbols instead of silently discarding them (C3); a uniform `available_from` column per signal table (A2 – lookahead is currently guarded per feature, see DECISIONS_FEATURES "Information Cut-offs").
