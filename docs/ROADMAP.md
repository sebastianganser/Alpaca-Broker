# ROADMAP.md – Sprint Planning & Progress

> **Living document.** Updated after every sprint.
> See [SESSION_LOG.md](SESSION_LOG.md) for detailed session-by-session history.
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** 17 October 2026 (S&P 500 survivorship repair finished: feature rebuild done, `d03955c` deployed, analyses and monthly `feature_analysis` re-run, K1/B/D2 conclusions unchanged, first short-interest evaluation – see [SESSION_LOG.md](SESSION_LOG.md), Session 34)

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
  - [x] Wikipedia S&P 500 / Nasdaq 100 changes parser + GitHub CSV backup *(S&P-500-Quelle am 16.10.2026 korrigiert: stiller Fallback ohne Austritte, siehe Offene Punkte)*
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
  - Scheduled daily at 00:15 Europe/Berlin since Oct 2026 (originally 04:45; ~2.5h runtime at free tier 5 req/min). Writes only `short_volume`; the `short_interest` table was filled later by its own weekly collector (16.10.2026, see open points)
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
    - 9.5c: Short Interest (B5, 3 Features; + 2 FINRA-Settlement-Features `short_interest_*` ab Migration 036)
  - Monatlich automatisch (1. des Monats, 07:00) via `feature_analysis`, oder manuell (`POST /analysis/trigger`); nächster regulärer Lauf 01.11.2026
  - [x] **Re-Run auf den reparierten Daten** (17.10., manuell mit Sebastians OK, 18:07–18:10, Bericht `2026-10-17`): 568.079 Snapshots, 752 Ticker, 87 Features – weiter **kein signifikantes Feature**, LASSO leer, Hypothesen unverändert, Konsens-Top-10 instabil (Überschneidung 0/10). Erste FINRA-Short-Interest-Bewertung: `short_interest_days_to_cover` mit durchgehend negativem Rank-IC (`return_20d` ρ −0,029, t_NW −3,20; `return_60d` ρ −0,044, t_NW −3,40), nach Bonferroni nicht signifikant → Kandidat für F1/einen vorab registrierten Test, inzwischen als H14 registriert ([LEARNINGS](LEARNINGS.md) 2026-10-17, [LEARNINGS_HYPOTHESES](LEARNINGS_HYPOTHESES.md)). Große t-Werte junger Quellen (Estimates, Options IV, Short Volume) nicht belastbar, in den nächsten Berichten beobachten
- [ ] **F1:** Rule-based Composite Score + Guardrails — offen; Einschätzung bei der Übergabe: ohne validiertes Ranking (R1, K1–K4 ohne Edge) derzeit nicht sinnvoll gewichtbar. Context Pack nutzt bis dahin den Risiko-Vorfilter (Stufe 1). Kandidat für die Gewichtung: `short_interest_days_to_cover` (negativer Rank-IC, Stand 17.10.; noch kein bestätigtes Signal) – **vorab registriert als H14** ([LEARNINGS_HYPOTHESES](LEARNINGS_HYPOTHESES.md)): einseitiger Test auf den ersten 250 Out-of-Sample-Handelstagen ab 2026-10-19, wird von der monatlichen `feature_analysis` automatisch mitgeführt (`hypothesis_results['H14']`), Urteil frühestens im Bericht vom 01.12.2027 (≈ Dezember 2027); nur bei `confirmed` darf Days-to-Cover mit negativem Vorzeichen in den F1-Score, sonst nicht als Einzelsignal
  - [ ] Min. liquidity, max 2/sector, pairwise correlation, churn lock, min-score — Schwellen **nicht hartcodiert**, sondern über die Einstellungen in der UI konfigurierbar (Entscheidung Sebastian, [DECISIONS_FEATURES](DECISIONS_FEATURES.md) 2026-08-18); Earnings in ≤ 5 Handelstagen markieren statt ausschließen (Konzept §5.1)
  - [ ] `candidate_selections` (append-only, mit Feature-Attribution je Auswahl) + `candidate_rejections` (Guardrail-Ausschlüsse) tables (Konzept §5.2, §8)
- [ ] **K1–K5:** Kurzfrist-Kandidaten ([Konzept 2026-10-06](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)) — K1–K4 abgeschlossen (alle ohne verwertbaren Edge), K5 läuft
  - [x] K1: Barrier-Backtest des aktuellen Scores (TP 0,5–1 %, SL 2–3×, max. 14 Tage) — Ergebnis: ≈ Zufall, siehe LEARNINGS 2026-10-06; am 17.10. auf den reparierten Daten bestätigt ([Bericht](reports/2026-10-17_barrier_backtest.md))
  - [x] K2 (Schritt A): Barrier-Label +1 %/−2 %/14d (`return_barrier_14d`, Migration 033) als Zielgröße der Feature-Analyse — Ergebnis: kein robustes Signal, siehe LEARNINGS 2026-10-06
  - [x] K2b (Schritt A2): 9 Kurzfrist-Kennzahlen `st_*` aus Tageskursen (Migration 034, Backfill-Skript), Analyse neu und mit Stand A vergleichen — Ergebnis: erste Hinweise (Schlusslage, Earnings-Reaktion), nicht signifikant
  - [x] K3 (Schritt B): ML-Modell P(Treffer) (Logit L2 + Gradient Boosting), Purged Walk-forward, Schwelle → 0–5 Kandidaten oder „keine Kandidaten“ — Ergebnis: **nicht bestanden** (AUC ≈ 0,50, Holdout n. s.), siehe [Bericht](reports/2026-10-06_hit_model_walkforward.md); am 17.10. auf den reparierten Daten wiederholt, weiter nicht bestanden ([Bericht](reports/2026-10-17_hit_model_walkforward.md))
  - [ ] K3b (Schritt C): Hyperopt nur für Trade-Parameter (Ziel/Stop, Schwelle, Filter), Bestätigung auf Holdout — ⏸ zurückgestellt, bis ein Modell den Walk-forward besteht
  - [x] K4 (Schritt D): Event-Studien 8-K-Rückkäufe (D1) und Index-Aufnahmen (D2) — Ergebnis: **D1 nicht bestanden** (648 Trades, Ø −0,06 %, n. s.), D2 nur Hinweis auf Schwäche nach Aufnahme (am 17.10. auf den reparierten Daten bestätigt: −0,320 %, [Bericht](reports/2026-10-17_event_study_index_add.md)); Reddit-Hype (D3) mangels Historie zurückgestellt, siehe [Konzept §7.6](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)
  - [ ] K5: Vorwärtstest Stufe 2 – Skill schreibt `decisions.yaml` ([Format](STAGE2_DECISIONS.md)), Nachtlauf-Schritt `stage2_review` (Migration 035) bewertet mit der Barrier-Regel, Bericht `context_packs/stage2_auswertung.md`; Index-Warnhinweis im Context Pack ([Konzept §7.7](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)) — Code fertig und seit 07.10. deployt (`1583d59`), läuft nächtlich (ab Plan-Schritt 4 durch `forecast_review` / `forecasts.yaml` ersetzt, Deploy ausstehend). **⏸ Bewusst zurückgestellt** (Sebastian, 16.10.): Vorwärtstests erst, wenn die Sprints durch sind und die Daten reichen (Sprint-10-Readiness) – bis dahin keine `decisions.yaml` erwartet. Urteil frühestens ab 100 abgeschlossenen Käufen
  - [ ] K3 erneut (Walk-forward mit längerer Alternativdaten-Historie, neuer Holdout ab 2026-Q4) — frühestens Januar 2027
- [ ] **Prognose-Ziel neutral (60d/20d)** (Klarstellung Sebastian, 17.10. abends, [DECISIONS_FEATURES](DECISIONS_FEATURES.md) 2026-10-17 „Scope: Alpaca-Broker forecasts, a separate bot trades“) — Alpaca-Broker prognostiziert, welche Ticker in den nächsten Tagen bis Monaten steigen (später auch fallen), und übergibt die gesammelten Daten per Context Pack an Claude, der die Ticker wie ein Wall-Street-Broker beurteilt. Strategie, Ein-/Ausstiegsregeln (Trailing-Stop, Take-Profit …) und der Handel gehören einem **separaten, späteren Bot**. Der am selben Abend gebaute Trailing-Stop-Trend-Trade (ehemals T1–T5, `608674c`, Migration 037, 19:03 deployt) war ein Missverständnis und ist wieder entfernt. Anlass der 60-Tage-Ausrichtung bleibt: erste Hinweise beim Ziel `return_60d` (`insider_buy_ratio_30d` t_NW +3,0, `insider_buy_value_30d` +2,8, `form13f_top_holder_count` +2,3, `short_interest_days_to_cover` −3,4; beim 14-Tage-Barrier ≈ 0) – nach Bonferroni nicht signifikant, also Hypothesen
  - [x] Trailing-Label entfernt — ✅ Code (Migration 038 `DROP COLUMN IF EXISTS` für `return_trail_60d` / `trail_exit_day` / `trail_stopped`, 037 unverändert; `analysis/trailing.py`, Label-Teil von `derived/target_backfill.py`, ORM-Spalten und Trailing-Tests gelöscht). Noch nicht committet/deployt (038 pending deploy)
  - [x] Modellziel der Feature-Analyse strategieneutral — ✅ Code: `MODEL_TARGET = 'return_60d'` (Horizont 60, „in erster Linie Monate“), Fallback `return_20d` (Abdeckungsregel unverändert, `MODEL_TARGET_CHAIN` 60d → 20d); das Barrier-Label ist nicht mehr Teil der Kette; H14 unverändert
  - [ ] Deploy (`/deploy`, nicht 22:15–05:15, nicht während eines laufenden Jobs): Produktion steht auf 037, die Migration 038 verwirft beim Containerstart die drei Trailing-Spalten (abgeleitete Werte, keine Rohdaten; der Backfill der nächsten Nightly Chain hat sie bis dahin eventuell schon gefüllt – das ist unerheblich)
  - [ ] Feature-Analyse auf dem neuen Modellziel (nächster regulärer Lauf 01.11.): die vier ersten Hinweise gegen `return_60d` prüfen; was als Bestätigung zählt, vorab festlegen (ggf. wie H14 vorab registrieren, da alle Daten bis 16.10. bereits angesehen wurden)
  - [ ] **Context Pack und Barrier-Label strategieneutral umbauen** ([Plan 17.10.](2026-10-17_Plan_Strategieneutral.md), von Sebastian am 17.10. freigegeben; Entscheidung: [DECISIONS_FEATURES](DECISIONS_FEATURES.md) „Strategy-Neutral Rework Approved“): Claude prognostiziert die **absolute** Richtung je Ticker und Horizont, ausgewertet wird absolut **und** relativ (vs. Universum und SPY); Kandidatenauswahl Variante D (Screen + 10 bullisch / 10 bärisch (anfangs aus) / 5 Zufall / Wunsch-Ticker); das Barrier-Label wird gelöscht. Barrier-Label, Vorfilter (ATR 1–3 %, Earnings-Sperre, „Top 5“) und K5/Stage 2 stammen aus dem Trade-Konzept. Schritte des Plans:
    - [x] Schritt 1 – Doku: [FORECASTS.md](FORECASTS.md) (Format `forecasts/v1`, vorab registrierte Auswertung, Entscheidungskriterium – am 18.10. mit 300 Sitzungen vorab registriert), [SCREEN.md](SCREEN.md) (Gate, 12 Evidenzgruppen mit Literatur-Vorzeichen), Entscheidungseintrag; Konzept 06.10. und [STAGE2_DECISIONS.md](STAGE2_DECISIONS.md) als historisch markiert (gelten bis zum Deploy der Prognose-Pipeline)
    - [x] Schritt 2 – Code (Tests 1407 → 1544, alle ohne DB; Review-Korrekturen vom 17.10. eingearbeitet): `block_bootstrap_diff` nach `analysis/stats.py` (+ Batch-Jackknife-Intervalle); `analysis/criterion_sim.py` + `scripts/analysis/simulate_criterion_size.py` (simulierte Größe und Power des Kriteriums); `analysis/screen.py` (Qualitäts-Gate, Gruppen-Perzentile, Evidenzbreite, Buckets, Ereignis-Flags); `analysis/screen_backtest.py` + `scripts/analysis/screen_backtest.py` (Rückblick-Test auf dem ML-Fenster, absolut/relativ/SPY, Jackknife-KI, Jahrestabelle); `analysis/forecast_eval.py` (Auswertung der Prognosen, Bericht, `freeze_sample` / `verdict_from_frozen`); `analysis/benchmark.py` (SPY-Renditen); Event-Studie im Horizont-Modus (20/60 Tage roh und Überschuss; Barrier-Modus bleibt bis Schritt 6)
    - [x] Bestätigt von Sebastian (17.10.): entscheidend ist das **System** (Claudes Prognosen gegen das Gate-Universum), der Mehrwert gegenüber der Shortlist wird zusätzlich berichtet; die rohe absolute Trefferquote wird nur berichtet; jede Änderung von `forecaster_version` startet eine neue eingefrorene Stichprobe
    - [x] **Entscheidungskriterium vorab registriert (Sebastian, 18.10.):** [FORECASTS.md §6](FORECASTS.md) mit **300 Sitzungen** statt der vorgeschlagenen 200 (≈ 14 Monate; ≥ 100 Hoch- und ≥ 100 Runter-Prognosen einer `forecaster_version` bei 20 Tagen, Modus `system`, ein Blick, 95-%-Batch-Jackknife-Untergrenzen von R und S > 0, Batches von 40 Sitzungen, t mit 6 Freiheitsgraden = 2,447, Karenz 10 Sitzungen); simulierte Fehlbestehensrate 1,2 %, Power 38 % bei +1,7 % und 85 % bei +2,9 % relativem Vorsprung; Konstanten dürfen wie bei H14 nicht mehr geändert werden (Tripwire-Test `test_registered_criterion_constants`)
    - [x] **Screen bestätigt (Sebastian, 18.10.):** Liquiditätsgate 20-Tage-Handelsvolumen ≥ 5 Mio. USD; an Tagen mit weniger qualifizierten bullischen Tickern bleibt die Liste kürzer (kein Auffüllen auf 10; `MIN_BREADTH_BULL` = 1 / `MAX_BREADTH_BEAR` = −1) – kein Verhaltenswechsel, Tripwire-Test `test_confirmed_gate_liquidity_and_breadth_constants` ([SCREEN.md §3/§6](SCREEN.md))
    - [x] Restliche Gate-Schwellen des Screens (≥ 60 Kurstage, ≥ 50 % der Gruppen mit Daten) sind seit 18.10. **technische Defaults** (keine Bestätigung mehr nötig; mit `SCREEN_VERSION` festgehalten, [SCREEN.md §3](SCREEN.md))
    - [ ] Screen-Backtest auf den Produktionsdaten laufen lassen (`alpaca-ops run scripts/analysis/screen_backtest.py --out /app/repair_logs`, nur lesend) und das Ergebnis lesen, bevor der Screen in den Context Pack geht (noch nie gelaufen; Vorzeichen werden danach nicht angepasst)
    - [ ] Schritt 3 – Context Pack v2 (`pack_format: 2`, Gate, Screen, Abschnitte, `screen.csv`, `pack_candidates`)
    - [x] Schritt 4 – ✅ Code (18.10., **nicht committet/deployt**): Parser/Ingest `derived/forecasts.py` (Kopf-Fehler lehnen die Datei ab und werden einmalig als `rejected`-Review gespeichert; Zeilenfehler – verbotene/unbekannte Felder, Enums, Bereiche, doppeltes Ticker×Horizont, Ticker ohne Snapshot am Sitzungstag – lehnen nur die Zeile ab, Review `partial`; Idempotenz per SHA-256, geänderte Datei = neues Review, die Auswertung nimmt je Sitzung das jüngste Review), `analysis/forecast_review.py` (Lader, `freeze_sample` je `forecaster_version` und `verdict_from_frozen` mit einmaliger Persistenz in `forecast_criterion_samples`, Bericht `context_packs/forecast_auswertung.md`), Chain-Schritt `stage2_review` → `forecast_review` (Jobzahl bleibt 26), Hilfsfunktion `derived/pack_candidates.py` für Schritt 3, Migration 039 (`forecast_reviews`, `forecasts`, `pack_candidates`, `forecast_criterion_samples`; `stage2_*` per `DROP TABLE IF EXISTS`); entfernt: `derived/stage2_decisions.py`, `analysis/stage2_eval.py`, ORM `stage2.py` samt Tests. Test auf frischer DB (`upgrade head`, `alembic check` sauber, `downgrade 038` stellt die Stage-2-Tabellen identisch zu 035 her, `upgrade head`) und ein Durchlauf gegen die Test-DB (330 Sitzungen: Ingest, Einfrieren, Urteil, Urteil bleibt bei geänderten Renditen unverändert). **Vor dem Deploy:** Hauptsitzung prüft produktiv, dass `stage2_reviews` / `stage2_decisions` leer sind ([FORECASTS.md §6](FORECASTS.md))
    - [ ] Schritt 5 – Deploy 1, Skill-Prompt `aktien-analyse` anpassen (liegt bei Sebastian), Probelauf mit einem Tag
    - [ ] Schritt 6 – Rückbau: `analysis/barrier.py`, `hit_model.py`, Barrier-Modus der Event-Studie, `backtest_barrier.py`, `walkforward_hit_model.py`; Migration 040 (Barrier-Spalten), Git-Tag `pre-neutral`, Test auf frischer DB
    - [ ] Schritt 7 – Deploy 2 und Doku-Durchgang (Mission, ROADMAP: Sprint 11/12 wandern zum Bot)
  - [ ] (Ausführungsphase, später, separater Bot): Strategie, Ausstiegsregeln und Orders – nicht Teil von Alpaca-Broker; nur Paper, Orders nur mit Sebastians Freigabe

### Betrieb & Wartung (Sept–Okt 2026)
- [x] Options IV + Estimates als Feature-Gruppen, Migration 028 (`bda67d8`), Features-API/Schemas nachgezogen (`4409caf`)
- [x] Globale Data Retention 20 Quartale, wöchentlich So 03:00 (`a2978cf`, verfeinert im Review 05.10.); erster Lauf 11.10.: 135.084 Zeilen gelöscht
- [x] Dashboard-Tabellenstatistik 14 → 24 Tabellen (`9db0519`); Datumsfix für `news_*` (`050ff32`, deployt 15.10. als `b60038e`)
- [x] Sprint-10-Readiness: FRED-Tabellenname + realistische Schwellen für dünne Gruppen (`a008047`); Stand 01.10.: 26/30
- [x] Review-Fixes 05.10. (Migrationen 029–031), 13F-CUSIP-Auflösung (032), alle Reparatur-Skripte gelaufen (Details: [SESSION_LOG](SESSION_LOG.md), Session 31)
- [x] `target_backfill` mit Deadlock-Retry (`97fcdb0`, deployt 15.10. als `b60038e`)

### Offene Punkte (Stand Übergabe 15.10.2026)
- [x] **Deploy** `050ff32` + `97fcdb0` auf Unraid – deployt am 15.10. als `b60038e` (Session 32), Dashboard zeigt echte Zeilenzahlen für `news_*`
- [x] **Test-Isolation:** `test_store_writes_trades` und `test_store_dedup_counts` (`tests/unit/test_politician_trades.py`) öffneten über `NewTickerOnboarder` eine **echte DB-Session** – behoben am 15.10. in Session 32 (`NewTickerOnboarder` gepatcht, 850 Tests laufen ohne DB)
- [x] **Migrationskette auf leerer DB:** 024 und 025 legten beide `sector_relative_*` an → frisches `upgrade head` brach an 025 ab. Behoben 16.10. (Session 33): 025 idempotent (`ADD COLUMN IF NOT EXISTS`, Downgrade no-op), auf Wegwerf-`postgres:18` geprüft; Produktion unberührt
- [x] **Migration ↔ ORM angeglichen** (16.10., Session 33): 39 Abweichungen behoben, indem das ORM an das migrierte Schema angepasst wurde (Produktion unverändert); `alembic check` schema-aware inkl. Server-Defaults, Ergebnis „No new upgrade operations detected“ ([DATABASE.md](DATABASE.md))
- [x] `registry.NIGHTLY_UPSTREAM` „kritisch“ wirksam (16.10., Session 33): veraltete Preise oder Kurs-Abdeckung der Session < 80 % der Vorsession → Features + Context Pack übersprungen, Chain `failed` mit Reparatur-Hinweis; übrige Eingänge weiter nur `partial` ([SCHEDULER.md](SCHEDULER.md)). Dazu **automatisches Nachholen** fehlender Sessions (letzte 10 Handelstage, datenbasiert über die Snapshot-Abdeckung, sobald die Kurse vollständig sind)
- [x] `short_interest`-Tabelle (Migration 027) wurde von keinem Collector befüllt – gelöst 16.10. (Session 33, deployt als `b873d4d`, Historie befüllt): FINRA-Short-Interest wird gesammelt (Entscheidung Sebastian, [DECISIONS_DATA](DECISIONS_DATA.md) 2026-10-16). Wöchentlicher Job `short_interest_positions_collector` (So 12:00, Massive `GET /stocks/v1/short-interest`, 1 Bulk-Request je Settlement-Datum), Features `short_interest_days_to_cover` / `short_interest_change_1m` (Migration 036, `FEATURE_VERSION` 2026.10-3) mit 9-Handelstage-Veröffentlichungsverzug; Skripte `scripts/repair/collect_short_interest.py` und `backfill_short_interest_features.py`
- [x] Historische 13F-Feature-Spalten nach dem Ticker-Backfill geprüft (16.10., Session 33, Lese-Abfrage): `cusip_map` aufgelöst 05.10. 17:58–19:18, gesamte Feature-Historie direkt danach neu berechnet (19:18–23:27) → 13F-Abdeckung ab Dez 2021 konstant 89–93 % (heute 92,3 %), `delta_qoq` ab März 2022. Anlaufphase erwartet: erste 13F-Meldung in der DB 12.11.2021 (Okt 2021 0 %, Nov 50 %). Keine Neuberechnung nötig; ML-Fenster ab 28.07.2022 voll abgedeckt
- [x] Sprint-10-Readiness erneut laufen lassen – 16.10. (`alpaca-ops run scripts/sprint10_readiness.py`): **29/31, „GO“** (01.10.: 26/30). Offen: Ranking-Stabilität **0/10** Top-10-Überschneidung erster vs. letzter Monatsbericht (01.10.: 40 %), Options IV 33 Handelstage (min. 60, ≈ 20.11.), Estimates 52 (min. 60 ≈ 27.10., 90 für Revisions-Momentum ≈ 10.12.); IV-Rank 252 Tage ≈ September 2027 (nicht blockierend). Am 17.10. auf den reparierten Daten wiederholt: weiter 29/31 „GO“ (Options IV 34 Tage, Estimates 53, Ranking-Stabilität 0/10)
- [ ] Readiness Anfang Dezember erneut prüfen (nach den Monatsberichten 01.11./01.12.): Ranking-Stabilität, Options-IV-/Estimates-Historie. Hinweis: Abschnitt 3 des Skripts misst die Feature-Abdeckung nur am letzten Tag – IV, Estimates und Short Volume haben erst 1–2 Monate Historie und fehlen im ML-Fenster ab 2022 fast überall
- [ ] `sector_relative_momentum` erneut prüfen (Nov 2026), IV-Rank ab ~252 Sessions IV-Historie (IV-Daten seit 31.08.2026 → ~September 2027)
- [ ] „Recompute History“-Button in den Einstellungen (zurückgestellt, skriptbasiert)
- [x] Auf Unraid prüfen, ob noch ein alter täglicher `pg_dump`-Cron existiert – geprüft 16.10. mit `alpaca-ops status`: kein Cron-Eintrag und kein User Script greift auf die Alpaca-DB zu (Session 33)
- [x] **Universum-Pflege** (16.10., Session 33): 10 aktive Ticker ohne Daten per `scripts/validate_universe.py` deaktiviert (757 aktiv). Ursache laut Alpaca Corporate Actions: Fusionen AVB→EQR, CTRA→DVN, LBRDK→CHTR; Barübernahmen EA, WBD; Namenswechsel BK→BNY, EQR→VMRK, PSTG→P, SATS→ECHO, PSKY→SKYD. Die neuen Symbole stehen bereits im Universum (SKYD kommt mit dem Index-Abgleich am 01.11.)
- [x] **Universum automatisch prüfen** (16.10., Session 33, Code in `c290f65`, deployt 17.10. mit `771ab1d`): wöchentlicher Job `universe_validation` (So 13:00, `universe/validation.py`) gleicht die aktiven Ticker mit den Alpaca-Assets ab, deaktiviert nicht handelbare Ticker (Ausnahme: offenes `index_membership`-Intervall → Warnung, Lauf `partial`) und schreibt Namenswechsel/Fusionen aus Alpaca Corporate Actions in die Lauf-Notizen; 26 Jobs ([SCHEDULER.md](SCHEDULER.md)). Das manuelle Gegenstück `scripts/validate_universe.py` bleibt bestehen
- [x] **S&P-500-Austritte fehlten in `index_membership` (Survivorship) – Reparatur in drei Schritten, abgeschlossen 17.10.** Befund 16.10.: 416 heutige Mitglieder ab 2021-01-01 + 90 datierte Aufnahmen, nur 3 Austritte; das Punkt-in-Zeit-S&P-500-Universum hat 426 (2022) … 490 (2026) statt ~503 Mitglieder, 92 echte Austritte im Datenfenster fehlen. Ursache: die Änderungstabelle liegt auf einer anderen Wikipedia-Seite, der Parser fiel still auf „Date added“ (nur Aufnahmen) zurück. Betroffen waren Features und alle Analysen auf dem S&P-500-Universum seit August 2026 (R1, K1–K4, Feature-Analyse, Walk-forward, Readiness) – **Stand 17.10.: Mitgliedschaft, Kurse und Features sind repariert, die Analysen sind neu gelaufen (K1, B und D2 unverändert); die monatliche `feature_analysis` ist am 17.10. (mit Sebastians OK) auf den reparierten Daten neu gelaufen: weiter kein signifikantes Feature.** Details: [Inventur + Runbook](reports/2026-10-16_sp500_survivorship_inventory.md) (Abschnitt 8), [DECISIONS_DATA](DECISIONS_DATA.md) 2026-10-16, [LEARNINGS](LEARNINGS.md) 2026-10-16
  - [x] **Schritt 1 – Code** (16.10., `c290f65`, `3d85fcf`, deployt 17.10. mit `771ab1d`): Parser liest „Historical components of the S&P 500“; eine unvollständige Quelle (nur Aufnahmen) kann nicht mehr seeden (Seed-Endpunkt antwortet 502); `universe/ticker_aliases.py` (24 Umbenennungen, Konvention: ein durchgehendes Intervall unter dem aktuellen Symbol) und `universe/corporate_actions.py`; `IndexSyncer` mit Wirksamkeitsdatum und Umbenennungs-Erkennung; Job `universe_validation`; Reparaturskripte `scripts/repair/rebuild_sp500_membership.py` und `onboard_removed_members.py` (Probelauf als Standard, Sicherungs-CSV, `--restore`)
  - [x] **Schritt 1b – Folgefixes** (17.10.): `158bc59` `alpaca-ops run-bg` / `run-log` / `run-stop` (abgekoppelte Skriptläufe, Deploy-/Restart-Sperre während eines Skripts), `8de96a1` Reaktivierung aktueller Index-Mitglieder (`--add-current-members`) und Schutz in `apply_sector_records`, `d03955c` derselbe Schutz im `NewTickerOnboarder`, Benchmark-ETFs nie deaktiviert; 1384 Tests. `8de96a1` deployt 17.10. 09:44, `d03955c` mit `f92fb28` deployt 17.10. 17:22
  - [x] **Schritt 2a – `index_membership`** (17.10. 09:35, `--apply` mit Sebastians Freigabe): Probelauf deckte sich exakt mit dem Runbook (INSERT 105, UPDATE 9, DELETE 1 PSKY, unverändert 496); `sp500` jetzt 503 offen / 610 gesamt, an allen 1.266 Sessions 503–506 Mitglieder, Nasdaq-100 unberührt; Sicherung `ops/sp500_membership_backup_20261017_093532.csv`
  - [x] **Schritt 2b – Austritte onboarden, PARA reparieren** (17.10. 09:51, `--fix-para --add-current-members`, `--apply` mit ausdrücklicher Freigabe): 71 Universe-Zeilen (68 inaktive Austritte + SKYD/TWLO/VYLR aktiv), BF.B reaktiviert und von der Blacklist genommen (Fehlalarm `MUTUALFUND` seit 15.04.), 43.530 Kurszeilen für 75 Ticker (darunter 1.555 IEX→SIP-Überschreibungen: ANSS, ATVI, XLNX), PARA: 301 Banzai-Zeilen gelöscht und 965 Paramount-Zeilen geschrieben, 44.405 Indikatorzeilen; aktives Universum jetzt **760**; Sicherungs-CSVs `ops/…_20261017_095146.csv`. Zahlen und Verifikation: Runbook Abschnitt 8, „Execution log 2026-10-17“
  - [x] **Schritt 3 – Features und Analysen** (17.10.):
    - [x] Feature-Neuaufbau (`alpaca-ops run-bg scripts/repair/features_rebuild.py --apply --skip-ta --skip-clusters`) gestartet 09:53, **fertig 17:13, Exit-Code 0** (≈ 7,3 h inkl. Targets für 768 Ticker; ≈ 20 s/Session): `feature_snapshots` **676.953** Zeilen (vorher 627.412, +49.541), 1.266 Sessions 2021-10-01..2026-10-16, alle `FEATURE_VERSION` 2026.10-3. Lese-Prüfung: 0 fehlende Mitglieds-Sessions im Punkt-in-Zeit-S&P-500, am 2022-01-03 505 Mitglieder mit Snapshot (Runbook-Erwartung); BF.B 1.266 Snapshots mit Kursfeatures und Targets, PARA 966 Snapshots bis 2025-08-07, Austritte wie ATVI/FRC/SIVB/TWTR bis zu ihrem letzten Mitgliedstag
    - [x] `d03955c` deployt mit `f92fb28` (17.10. 17:22, 26 Jobs, Alembic `036`)
    - [x] Analysen auf den reparierten Daten neu gelaufen (Runbook Schritt 8, nur lesend, Berichte `reports/2026-10-17_*.md`): **D2** 110 Ereignisse, Differenz zum Universum −0,320 % (vorher −0,321 %) → unverändert negativ ([Bericht](reports/2026-10-17_event_study_index_add.md)); **Schritt B** 557.275 Snapshots, Ø Holdout-AUC logit 0,511 / gbm 0,499 (vorher 0,510 / 0,509), gewählte Regel „gbm, max. 3/Tag, P ≥ 0,683“, Holdout 954 Trades, Ø netto −0,006 %, Differenz zum Universum +0,014 % n. s. → weiter ❌ ([Bericht](reports/2026-10-17_hit_model_walkforward.md)); **K1** 557.166 Universe-Trades, 1.060 Tage, kein signifikant positiver Netto-Ertrag, nur „Top 5 gefiltert“ schlägt das Universum leicht (TP 0,5 %/SL 1,5 %: +0,026 % im OHLC-Pfad, netto weiter negativ) → Score ≈ Zufall ([Bericht](reports/2026-10-17_barrier_backtest.md)); **Readiness** 29/31, 🟢 GO (ML-Fenster 568.079 Snapshots, 752 Ticker; offen: Ranking-Stabilität 0/10, Options IV 34 Tage, Estimates 53 Tage). Gesamtbild: keine Forschungsaussage hat sich geändert – die Nullergebnisse waren kein Survivorship-Artefakt
    - [x] „Survivor-biased“-Hinweise entfernt (CLAUDE.md Invarianten, DECISIONS_DATA, LEARNINGS 2026-10-16, diese ROADMAP); die Historie bleibt in DECISIONS/LEARNINGS/SESSION_LOG
- [ ] **Offen nach der Survivorship-Reparatur:** (a) ✅ monatliche `feature_analysis` auf den reparierten Daten – am 17.10. mit Sebastians OK gelaufen (Bericht 2026-10-17, erste Short-Interest-Bewertung: `days_to_cover` negativer IC, nicht signifikant); nächster regulärer Lauf 01.11. (Konsens-Ranking, `days_to_cover` und große t-Werte junger Quellen beobachten); (b) `index_sync` am 01.11. darf für den S&P 500 nichts mehr ändern (Kontrolle laut Runbook); (c) Log-Check der nächsten Nightly Chain am Di 20.10. 04:30 (erster Lauf auf dem reparierten Universum, 760 aktive Ticker)
- [ ] **Delisting-Renditen fehlen (bekannte Einschränkung, nicht dringend):** Bei abrupt verschwindenden Tickern (z. B. SIVB, FRC) gibt es nach dem letzten Kurs keinen Folgepreis; die Targets der letzten ≈ 20 Sessions vor dem Delisting sind `NULL` (bei `return_60d` ≈ 60 Sessions). Damit sind die schlechtesten Ergebnisse (bis −100 %) in Targets, Walk-forward und Backtests unterrepräsentiert. Mögliche spätere Lösung: Delisting-Rendite aus letztem Handel/Corporate Action (CRSP-Stil) oder Trades mit dem letzten verfügbaren Kurs labeln. Details: [LEARNINGS](LEARNINGS.md) 2026-10-17

### Nächste Schritte
1. ✅ `b60038e` deployt (15.10.); Log-Check am 16.10. (erste Nightly Chain mit neuem Image)
2. ✅ Politician-Tests isoliert (Session 32), Migrationskette 024/025 auf leerer DB behoben (Session 33); Migration ↔ ORM angeglichen (Session 33)
3. ✅ Short Interest: deployt (`b873d4d`, Migration 036), Historie befüllt (94.370 Zeilen, 120 Stichtage 2021-10-15 bis 2026-09-30), Features nachgerechnet (610.887 Snapshots, Abdeckung im ML-Fenster 99 %) – erste Bewertung im Monatsbericht vom 17.10. (`days_to_cover` negativer Rank-IC, nicht signifikant; `change_1m` nichts), weiter beobachten 01.11. / Readiness Anfang Dezember
4. ✅ **S&P-500-Survivorship-Reparatur abgeschlossen (17.10.):** Code, `index_membership`, Austritte/PARA/BF.B, Feature-Neuaufbau (676.953 Snapshots), Deploy `f92fb28` und Analysen-Neulauf (K1, B, D2 unverändert, Readiness 29/31 GO). Monatliche `feature_analysis` ebenfalls am 17.10. neu gelaufen (kein signifikantes Feature). Offen: Log-Check der Nightly Chain am Di 20.10. 04:30 (erster Lauf auf dem reparierten Universum), `index_sync` am 01.11. darf für den S&P 500 nichts ändern – siehe Offene Punkte
5. **Prognose-Ziel neutral:** Trailing-Label am 17.10. abends wieder entfernt (Migration 038, Modellziel `return_60d` → `return_20d`) → committen und deployen (`/deploy`); danach den Plan für das strategieneutrale Context Pack / Barrier-Label / K5 erstellen (siehe Offene Punkte „Prognose-Ziel neutral“)
6. Daten weiter sammeln, Readiness Anfang Dezember erneut prüfen (16.10.: 29/31 „GO“, aber Ranking-Stabilität 0/10); K5 (Vorwärtstest) bewusst zurückgestellt bis nach den Sprints
7. Sprint 10 frühestens ab Mitte Dezember 2026 – Inhalt hängt davon ab, ob bis dahin ein Ranking den Walk-forward besteht (K3 erneut frühestens Januar 2027)

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
- 26 scheduler jobs (`scheduler/setup.py`, production reports 26 since the deploy of 2026-10-17): 13 daily (incl. `nightly_chain`, `log_retention`), 7 weekly (incl. `data_retention`, `short_interest_positions_collector`, `universe_validation`), 2 monthly (`index_sync`, `feature_analysis`), 4 paused chain steps (`feature_pipeline`, `target_backfill`, `stage2_review`, `context_pack_generator`) – see [SCHEDULER.md](SCHEDULER.md)
- Volume mount: `/mnt/user/Workfiles/AlpacaBroker` for Context Pack output (must be owned by `99:100`)
- **Deployed as of 2026-10-17 17:22:** `f92fb28` (contains `d03955c`), Alembic revision `036` (survivorship-repair code, `universe_validation` and FINRA short interest live)

**Update workflow:**
1. Commit + `git push` from Windows (tests green)
2. Unraid WebGUI → Settings → User Scripts → `alpaca-ops` → "Run Script" with argument `deploy` (git pull → build → recreate, detached; refused 22:15–05:15, while a job runs or while a project script runs in the container); progress `deploy-log`, verify via `/api/v1/dashboard/summary` (skill `/deploy`)

---

## Long-term Ideas (Backlog)

- **News sentiment:** ~~NLP analysis of headlines with Haiku~~ ✅ Implemented with FinBERT (Sprint 8c). Haiku upgrade planned for Sprint 9.
- **Reddit/social sentiment:** StockTwits, WSB mentions as contrarian signal
- **Options flow:** Unusual options activity (paid flow feeds at ≈ 150 USD/month not recommended, [concept §11](2026-08-18_Konzept_Datenerweiterung.md))
- **ML models:** XGBoost, neural networks after 12+ months of data
- **Multiple ETFs:** Not only ARK but other "smart money" funds
- **European stocks:** German/European small caps
- **Live trading:** Only after 12+ months of successful paper trading
- **Delisting returns:** targets of abruptly delisted tickers (SIVB, FRC) are `NULL` for the last ≈ 20 sessions, so the worst outcomes are under-represented; possible fix: CRSP-style delisting return from the last trade / corporate action, or label such trades with the last available price (not urgent, [LEARNINGS.md](LEARNINGS.md) 2026-10-17)
- **Context-pack extensions ([concept 2026-08-18](2026-08-18_Konzept_Datenerweiterung.md) §6, not implemented):** cross-sectional percentiles (universe/sector), own point-in-time history blocks (estimates, ratings, fundamentals drift), signal stack with availability dates, peer set computed from the universe, and the historical analogue (feature constellation → 20/60-day forward returns with hit rate and sample size, §6.2). The current pack has the stage-1 risk pre-filter, provisional score attribution and the stage-2 feedback template. The analogue only makes sense once a ranking passes the walk-forward (K3).
- **Small data/feature ideas from the same concept, not implemented:** insider buy ratio split into discretionary vs. planned 10b5-1 sales and a 180-day window (E2; the Form 4 10b5-1 flag is not collected); 13F holder concentration (top-10 share, E1); `market_cap_bucket` in `universe` (B4); exponential lag decay for politician trades instead of the hard 45-day cut-off (C1); quarantine table for rejected ticker symbols instead of silently discarding them (C3); a uniform `available_from` column per signal table (A2 – lookahead is currently guarded per feature, see DECISIONS_FEATURES "Information Cut-offs").
