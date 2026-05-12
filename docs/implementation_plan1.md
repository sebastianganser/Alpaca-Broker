# Sprint 8 – Feature Pipeline (Fortführung)

## Situation

Die Arbeit an Sprint 8 wurde in einem vorherigen Chat begonnen und durch Token-Limit unterbrochen. 

### ✅ Bereits erledigt (im vorherigen Chat)

| Item | Status |
|---|---|
| `FeatureSnapshot` ORM Model (`db/models/features.py`) | ✅ Erstellt, ~53 Feature-Spalten + 4 Target-Variablen |
| Alembic Migration `018_create_table_feature_snapshots.py` | ✅ Erstellt |
| `db/models/__init__.py` – FeatureSnapshot export | ✅ Aktualisiert |
| `docs/DATABASE.md` – Schema-Dokumentation | ✅ Aktualisiert |
| `docs/DECISIONS_FEATURES.md` – Sprint 8 Entscheidungen | ✅ 6 neue Entscheidungen dokumentiert |
| `docs/ROADMAP.md` – Sprint 8 Status "In Progress" | ✅ Aktualisiert |
| `docs/SCHEDULER.md` – Feature Pipeline + Target Backfill Jobs | ✅ Aktualisiert |

### ❌ Noch offen (diese Session)

| Item | Priorität | Status |
|---|---|---|
| **`FeaturePipeline`** Klasse (`derived/feature_pipeline.py`) | 🔴 Kern | ✅ Erledigt |
| **`TargetBackfill`** Klasse (`derived/target_backfill.py`) | 🔴 Kern | ✅ Erledigt |
| **Scheduler Jobs** (`scheduler/jobs.py`) – `run_feature_pipeline` + `run_target_backfill` | 🔴 Kern | ✅ Erledigt |
| **Scheduler Registration** (`main.py`) – Jobs bei 02:00 + 02:15 CET | 🔴 Kern | ✅ Erledigt |
| **API Endpoint** – Feature Snapshot Views | 🟡 Dashboard | ✅ Erledigt |
| **Frontend** – Feature Snapshot Anzeige im Dashboard | 🟡 Dashboard | ⏳ Offen |
| **Tests** | 🟡 Qualität | ⏳ Offen |
| **Documentation Update** – SESSION_LOG, ROADMAP finalisieren | 🟢 Abschluss | ⏳ Offen |

---

## Proposed Changes

### 1. Feature Pipeline Core (`derived/feature_pipeline.py`)

#### [NEW] [feature_pipeline.py](file:///d:/Sebastian/Dokumente/Privat/Rudi/Coding/Workspaces/Alpaca-Broker/src/trading_signals/derived/feature_pipeline.py)

Die Kernklasse des gesamten Projekts. Aggregiert alle Rohdaten zu einem Feature-Vektor pro Ticker pro Tag.

**Architektur:**
- `FeaturePipeline(session)` – nimmt eine DB-Session
- `compute_daily(target_date)` – Hauptmethode, berechnet Features für ein Datum
- Unabhängige private Methoden pro Feature-Gruppe:
  - `_compute_ark_features(ticker, date)` → 11 Features
  - `_compute_insider_features(ticker, date)` → 8 Features
  - `_compute_analyst_features(ticker, date)` → 7 Features
  - `_compute_politician_features(ticker, date)` → 4 Features
  - `_compute_13f_features(ticker, date)` → 2 Features
  - `_compute_fundamentals_features(ticker, date)` → 8 Features
  - `_compute_technical_features(ticker, date)` → 6 Features
  - `_compute_earnings_features(ticker, date)` → 3 Features
- Jede Methode gibt ein `dict` mit den Feature-Spalten zurück
- Wenn eine Methode fehlschlägt, werden die anderen trotzdem ausgeführt (graceful degradation)
- UPSERT-Pattern für idempotente Neuberechnung

**Feature-Berechnungslogik (Kernstück):**

| Feature-Gruppe | Datenquellen | Berechnungsmethode |
|---|---|---|
| **ARK Point-in-Time** | `ark_holdings`, `ark_deltas` | COUNT/SUM/BOOL-Aggregation über aktuellsten Snapshot |
| **ARK Temporal** | `ark_deltas` (10d/20d rolling) | COUNT von `delta_type='increased'`, streak detection, lineare Regression |
| **Insider Point-in-Time** | `insider_trades`, `insider_clusters` | Net buy count (30d), value sum, aktiver Cluster-Check |
| **Insider Temporal** | `insider_clusters` (30d/60d rolling) | COUNT/SUM Cluster, days_since_last |
| **Analyst Point-in-Time** | `analyst_ratings` | Rating-Score Mapping, COUNT upgrades, Price Target Upside |
| **Analyst Temporal** | `analyst_ratings` (30d/60d) | Upgrades - Downgrades, streak detection |
| **Politician** | `politician_trades` (60d/90d) | COUNT buys + DISTINCT politicians, dual-date (disclosure + transaction) |
| **13F** | `form13f_holdings` | COUNT top holders, COUNT new positions (QoQ comparison) |
| **Fundamentals** | `fundamentals_snapshot` | Latest snapshot values + 4-week regression slopes |
| **Technical** | `technical_indicators`, `prices_daily` | Relative values (price/SMA-1), volume ratio, ATR% |
| **Earnings** | `earnings_calendar` | Days until next, consecutive beats, surprise trend |

---

### 2. Target Backfill (`derived/target_backfill.py`)

#### [NEW] [target_backfill.py](file:///d:/Sebastian/Dokumente/Privat/Rudi/Coding/Workspaces/Alpaca-Broker/src/trading_signals/derived/target_backfill.py)

Separate Klasse für das Rückfüllen der Target-Variablen (Forward Returns).

**Logik:**
- Für jeden `feature_snapshots`-Eintrag wo `return_Xd IS NULL`:
  - Prüfe ob der Preis für `snapshot_date + X trading days` existiert
  - Berechne: `return_Xd = (future_price / current_price) - 1`
- Trading-Day-Berechnung: Skip Wochenenden/Feiertage via tatsächliche `prices_daily`-Einträge
- Optimierung: Batch-Update via SQL statt Einzelzeilen

---

### 3. Scheduler Integration

#### [MODIFY] [jobs.py](file:///d:/Sebastian/Dokumente/Privat/Rudi/Coding/Workspaces/Alpaca-Broker/src/trading_signals/scheduler/jobs.py)

Zwei neue Funktionen:
- `run_feature_pipeline()` – Täglicher Feature-Snapshot (02:00 CET)
- `run_target_backfill()` – Target-Rückfüllung (02:15 CET)

Beide folgen dem etablierten Pattern: CollectorLogCapture, CollectionLog-Eintrag, try/except.

#### [MODIFY] [main.py](file:///d:/Sebastian/Dokumente/Privat/Rudi/Coding/Workspaces/Alpaca-Broker/src/trading_signals/main.py)

Zwei neue `scheduler.add_job()` Aufrufe:
- `feature_pipeline` bei `CronTrigger(hour=2, minute=0)`
- `target_backfill` bei `CronTrigger(hour=2, minute=15)`

---

### 4. API & Dashboard Integration

#### [MODIFY] [dashboard.py](file:///d:/Sebastian/Dokumente/Privat/Rudi/Coding/Workspaces/Alpaca-Broker/src/trading_signals/api/routes/dashboard.py)

Neuer Endpoint `GET /api/v1/dashboard/feature-stats` der Feature-Snapshot-Statistiken zurückgibt:
- Datum des letzten Snapshots
- Anzahl Ticker mit Snapshots
- Feature-Coverage (% der Non-NULL-Spalten)
- Target-Backfill-Status (% der gefüllten Returns)

#### [MODIFY] [schemas.py](file:///d:/Sebastian/Dokumente/Privat/Rudi/Coding/Workspaces/Alpaca-Broker/src/trading_signals/api/schemas.py)

Neues Pydantic Schema `FeatureStats`.

---

### 5. Tests

#### [NEW] tests/test_feature_pipeline.py

- Test jeder Feature-Gruppe mit Mock-Daten
- Integration-Test: Gesamtpipeline mit echten DB-Fixtures
- Test Target-Backfill Logik

---

## Open Questions

> [!IMPORTANT]
> **Migration 018 – bereits auf Unraid applied?**  
> Die Migration `018_create_table_feature_snapshots.py` ist erstellt aber noch nicht committed. Ist sie bereits auf dem Unraid-Server angewandt worden, oder muss sie beim nächsten Deploy automatisch laufen?

> [!NOTE]
> **Commit-Strategie:** Soll ich alle Änderungen am Ende committen, oder zwischendurch Checkpoints setzen?

---

## Verification Plan

### Automated Tests
1. `uv run pytest` – alle bestehenden 303+ Tests müssen weiterhin grün sein
2. Neue Tests für FeaturePipeline und TargetBackfill

### Manual Verification
1. Feature Pipeline manuell einmal ausführen (via API-Trigger oder Script)
2. Prüfen dass `feature_snapshots`-Tabelle korrekt befüllt wird
3. Dashboard-Statistiken anzeigen lassen
4. Deploy auf Unraid + Scheduler-Verifizierung
