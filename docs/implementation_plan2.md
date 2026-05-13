# Sprint 8b – Features Page & Exploration UI

> **Erweiterung Sprint 8**: Feature Pipeline Visualisierung + explorative Analysefunktionen.
> Vorgezogen aus Sprint 9, da die Pipeline nun produktiv läuft und Daten erzeugt.

## Hintergrund

Sprint 8 (Feature Pipeline) ist vollständig implementiert:
- `FeaturePipeline` berechnet täglich 49 Features für ~674 Ticker
- `TargetBackfillComputer` füllt Forward Returns nach
- API-Endpoint `GET /dashboard/feature-stats` existiert bereits
- Beide Jobs laufen im Scheduler (02:00 + 02:15 CET)

**Was fehlt:** Eine dedizierte Frontend-Seite zur Visualisierung und Exploration der Feature-Daten. Bisher gibt es keine Möglichkeit, die berechneten Features direkt einzusehen – nur den Stats-Endpoint.

## Geplante Architektur

### Neue Seite: `/features` (6. Menüpunkt in der Sidebar)

**Icon:** `Brain` (lucide-react) – passt zum ML/Feature-Kontext

**Sektionen der Seite:**

#### 1. Pipeline Overview (Stats-Kacheln)
- Letzter Snapshot-Datum, Ticker-Anzahl, Feature Coverage %, Target Backfill %
- Datenquelle: `GET /dashboard/feature-stats` (existiert)
- Ähnlich wie System Health auf dem Dashboard

#### 2. Feature Coverage Heatmap
- Tabelle: Ticker (Zeilen) × Feature-Gruppen (Spalten: ARK, Insider, Analyst, Politician, 13F, Fundamentals, Technical, Earnings)
- Jede Zelle zeigt an, wie viele Features der Gruppe für diesen Ticker befüllt sind (farbcodiert: grün=voll, gelb=teilweise, grau=leer)
- Sortierbar nach Coverage, filterbar nach Ticker-Suche
- **Neuer Backend-Endpoint nötig:** `GET /features/coverage`

#### 3. Signal Convergence (Multi-Source Overlap)
- Top-Ticker nach Anzahl aktiver Signal-Quellen im letzten Snapshot
- Zeigt: Welche Ticker haben aktuell die meisten gleichzeitig aktiven Signale? (→ H3, H13)
- **Neuer Backend-Endpoint nötig:** `GET /features/convergence`

#### 4. Return Distribution (Target Variables)
- Zusammenfassung der berechneten Forward Returns: Median, Std, % gefüllt pro Horizont
- Für Sprint 9 vorbereitet: Basis für Feature ↔ Return Korrelationen
- **Datenquelle:** `GET /dashboard/feature-stats` erweitern ODER neuer Endpoint `GET /features/returns`

#### 5. Ticker Feature Detail (Click-Through)
- Klick auf einen Ticker → Detailansicht aller Features des letzten Snapshots
- Gruppiert nach Signal-Quelle, mit farbcodiertem NULL/Wert-Status
- **Neuer Backend-Endpoint nötig:** `GET /features/ticker/{symbol}`

---

## Neue Backend-Endpoints

| Endpoint | Response | Beschreibung |
|---|---|---|
| `GET /features/coverage` | `FeatureCoverageResponse` | Ticker × Feature-Group Coverage Matrix |
| `GET /features/convergence` | `SignalConvergenceResponse` | Top-Ticker nach aktiven Signal-Quellen |
| `GET /features/returns` | `ReturnStatsResponse` | Aggregierte Return-Statistiken |
| `GET /features/ticker/{symbol}` | `TickerFeatureDetail` | Alle Features eines Tickers (letzter Snapshot) |

---

## Unterteilung in Subtasks

### Task A: Backend-Endpoints (API + Schemas)
**Dateien:** `api/schemas.py`, `api/routes/features.py` (NEU), `main.py`

1. **A1:** `FeatureCoverageItem` + `SignalConvergenceItem` + `ReturnStats` + `TickerFeatureDetail` Schemas
2. **A2:** `features.py` Router mit `GET /features/coverage`
3. **A3:** `GET /features/convergence` Endpoint
4. **A4:** `GET /features/returns` Endpoint
5. **A5:** `GET /features/ticker/{symbol}` Endpoint
6. **A6:** Router in `main.py` registrieren, Syntax prüfen

### Task B: Frontend API-Layer + Routing
**Dateien:** `api.ts`, `App.tsx`, `Layout.tsx`

1. **B1:** TypeScript Interfaces + fetch-Funktionen in `api.ts`
2. **B2:** Route `/features` in `App.tsx` + Sidebar-Eintrag in `Layout.tsx` (mit Brain-Icon)

### Task C: FeaturesPage – Pipeline Stats + Coverage
**Dateien:** `FeaturesPage.tsx` (NEU)

1. **C1:** Page-Grundgerüst + Pipeline Stats Kacheln (feature-stats Endpoint)
2. **C2:** Feature Coverage Heatmap Tabelle (coverage Endpoint)

### Task D: FeaturesPage – Convergence + Returns
**Dateien:** `FeaturesPage.tsx`

1. **D1:** Signal Convergence Sektion (convergence Endpoint)
2. **D2:** Return Distribution Sektion (returns Endpoint)

### Task E: FeaturesPage – Ticker Detail Modal
**Dateien:** `FeaturesPage.tsx`

1. **E1:** Ticker Feature Detail (Click-Through Modal/Expand)

### Task F: Polish, Tests, Docs
1. **F1:** Syntax-Check + Build-Prüfung
2. **F2:** Unit Tests für neue Endpoints
3. **F3:** Dokumentation (ROADMAP, SESSION_LOG, SCHEDULER aktualisieren)
4. **F4:** Git Commits + Push

---

## Abhängigkeiten

```
Task A (Backend) ─── muss vor ──→ Task C, D, E (Frontend-Sektionen)
Task B (Routing) ─── muss vor ──→ Task C (Page-Grundgerüst)
Task A + B ────────── parallel möglich
Task C ─────────────── muss vor ──→ Task D, E (bauen auf Page auf)
Task F ─────────────── nach allem
```

## Risiken / Entscheidungen

> [!IMPORTANT]
> **Token-Window:** Jeder Task (A–F) ist eigenständig abschließbar und dokumentierbar.
> Bei Chat-Wechsel: `docs/task.md` enthält den exakten Fortschritt.

> [!NOTE]
> **Performance:** Coverage-Endpoint aggregiert über alle Ticker × Features.
> Bei ~674 Tickern und 49 Features ist das machbar, aber wir begrenzen die Default-Antwort
> auf den letzten Snapshot-Tag (nicht historisch).
