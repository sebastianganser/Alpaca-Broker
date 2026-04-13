# Sprint 7 – Dashboard & Operations UI – Detailplan

> **Erstellt:** 13. April 2026
> **Status:** Genehmigt, Umsetzung steht bevor
> **Design-Referenz:** Stitch-Projekt „Alpaca Scalable Broker" (Project ID: 15816383997657111432)

---

## 1. Ziel

Browserbasiertes Frontend für grafisches Feedback und Betriebssteuerung.
Nach Sprint 7 kann Sebastian über `http://192.168.1.93:8090`:
- Alle gesammelten Daten einsehen
- Collector-Status und Scheduler überwachen
- Backfills starten und den Fortschritt verfolgen
- DB-Operationen ausführen

**Keine CLI-Skripte auf dem Unraid-Server nötig.**

---

## 2. Architektur

### 2.1 Container-Architektur (Single Container)

```
┌─────────────────────────────────────────────────────┐
│  Docker Container: signal-collector (Port 8090)      │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │  Python Process (uvicorn)                     │    │
│  │                                               │    │
│  │  ┌─────────────┐  ┌───────────────────────┐   │    │
│  │  │ APScheduler │  │ FastAPI               │   │    │
│  │  │ (Background)│  │ /api/v1/...           │   │    │
│  │  │ 9 Jobs      │  │ + StaticFiles mount   │   │    │
│  │  └─────────────┘  │   /dist → React SPA   │   │    │
│  │                    └──────────┬────────────┘   │    │
│  └───────────────────────────────┼────────────────┘    │
│                                  │ :8090               │
└──────────────────────────────────┼──────────────────────┘
                                   │
                         ┌─────────┴─────────┐
                         │  Browser (React)   │
                         │  SPA Client        │
                         └───────────────────┘
```

### 2.2 Haupt-Änderung: `main.py` Umbau

**Vorher:** `BlockingScheduler` → blockiert den Prozess, kein Platz für API
**Nachher:** `BackgroundScheduler` + `uvicorn.run(app)` → FastAPI ist der Haupt-Prozess

```python
# Vereinfachter Ablauf (main.py):
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="Trading Signals", version="0.1.0")
scheduler = BackgroundScheduler(timezone="Europe/Berlin", ...)

@app.on_event("startup")
def startup():
    # Alle Jobs registrieren (wie bisher)
    scheduler.start()

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown(wait=False)

# API Router einbinden
app.include_router(dashboard_router, prefix="/api/v1")
app.include_router(universe_router, prefix="/api/v1")
# ...

# React SPA (gebaut als statische Dateien)
app.mount("/", StaticFiles(directory="frontend/dist", html=True))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
```

---

## 3. Design System (Stitch „Precision Architect")

### 3.1 Farb-Tokens

| Token | Hex | Verwendung |
|---|---|---|
| `--background` | `#121314` | Seiten-Hintergrund |
| `--surface` | `#1F2021` | Card-Hintergrund |
| `--surface-high` | `#292A2B` | Erhöhte Elemente |
| `--surface-highest` | `#343536` | Hover-State, Modals |
| `--surface-lowest` | `#0D0E0F` | Eingelassene Bereiche (Charts) |
| `--primary` | `#28EBCF` | Primäre Akzentfarbe (Buttons, Links) |
| `--primary-glow` | `#A6FFEC` | Gradient-Start, Glow-Effekte |
| `--on-surface` | `#E3E2E3` | Primärer Text |
| `--on-surface-variant` | `#BACAC5` | Sekundärer Text |
| `--outline` | `#84948F` | Subtile Trennungen |
| `--outline-variant` | `#3B4A46` | Ghost Borders (15% Opacity) |
| `--error` | `#FFB4AB` | Fehlerzustände |
| `--error-container` | `#93000A` | Fehler-Hintergrund |

### 3.2 Typografie

- **Font:** Inter (Google Fonts) – Headline, Body, Label
- **Display:** Tight tracking (-2%), für Portfolio-Summen und Hero-Preise
- **Labels:** ALL-CAPS, +5% letter-spacing → HUD-Ästhetik
- **Body:** 1.6x line-height für Lesbarkeit

### 3.3 Design-Regeln

1. **No-Line-Rule:** Keine 1px Borders für Sektions-Trennung. Nur tonale Tiefe (Farbstufen).
2. **Kein reines Weiß:** Immer `#E3E2E3` (`on-surface`)
3. **Primary-Buttons:** Gradient `#A6FFEC` → `#28EBCF` (135°), schwarzer Text
4. **Hover:** Surface-Farbstufe erhöhen (z.B. `surface` → `surface-high`)
5. **Glassmorphism:** Für Modals: `surface-highest` mit 70% Opacity, 24px backdrop-blur
6. **Breathing Room:** Content nie näher als 32px zum Rand
7. **Charts:** Line-Stroke = `primary`, Gradient-Fill (20% primary → 0% surface) darunter
8. **Keine Standard-Grüntöne:** Nur `primary` Cyan für Erfolg/Positiv

---

## 4. API-Endpoints

### 4.1 Dashboard Router (`/api/v1/dashboard`)

| Method | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/summary` | Collector-Status + Tabellen-Stats + System-Health |

**Response-Schema:**
```json
{
  "collectors": [
    {
      "name": "price_collector",
      "last_run": "2026-04-13T22:15:00",
      "status": "success",
      "records_written": 2700,
      "next_run": "2026-04-14T22:15:00"
    }
  ],
  "table_stats": [
    { "table": "prices_daily", "row_count": 882000, "min_date": "2021-01-04", "max_date": "2026-04-11" }
  ],
  "system_health": {
    "db_connected": true,
    "alembic_revision": "012_technical_indicators",
    "uptime_seconds": 86400,
    "scheduler_running": true
  }
}
```

### 4.2 Universe Router (`/api/v1/universe`)

| Method | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/` | Alle Ticker (paginiert, filterbar) |
| `GET` | `/{ticker}` | Ticker-Details mit letztem Preis |

**Query-Parameter:** `?index=sp500&sector=Technology&active=true&search=AAPL&page=1&limit=50`

### 4.3 Signals Router (`/api/v1/signals`)

| Method | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/ark` | ARK Deltas (letzte 7 Tage) |
| `GET` | `/insider` | Aktive Insider-Cluster |
| `GET` | `/politicians` | Politiker-Trades (letzte 30 Tage) |
| `GET` | `/ratings` | Analyst-Upgrades/Downgrades (letzte 7 Tage) |

### 4.4 Ticker Router (`/api/v1/ticker`)

| Method | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/{symbol}/prices` | OHLCV-Daten (Zeitraum per Query-Param) |
| `GET` | `/{symbol}/indicators` | TA-Indikatoren |
| `GET` | `/{symbol}/fundamentals` | Letzter Fundamentals-Snapshot |
| `GET` | `/{symbol}/signals` | Alle Signale für diesen Ticker |

### 4.5 Operations Router (`/api/v1/ops`)

| Method | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/scheduler` | Alle Jobs mit Status + nächster Run |
| `POST` | `/scheduler/{job_id}/trigger` | Job manuell auslösen |
| `GET` | `/alembic` | Aktuelle Migration + History |
| `POST` | `/backfill/prices` | Price Backfill starten (async) |
| `POST` | `/backfill/indicators` | TA Backfill starten (async) |
| `GET` | `/backfill/status` | Fortschritt laufender Backfills |
| `POST` | `/db/vacuum` | VACUUM ANALYZE auslösen |
| `GET` | `/db/stats` | Tabellen-Größen, Index-Nutzung |

---

## 5. Frontend-Pages

### 5.1 Dashboard (`/`)

**Layout:**
```
┌──────────────────────────────────────────────────┐
│  SIGNAL WAREHOUSE          [●] Online    ⚙️      │
├──────────────────────────────────────────────────┤
│                                                  │
│  COLLECTOR STATUS                                │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│  │Price ✅│ │TA  ✅│ │ARK ✅│ │Form4✅│ │13F ✅│  │
│  │22:15  │ │22:30 │ │23:00 │ │23:30 │ │So 10 │  │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘  │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐            │
│  │Polit✅│ │Fund ✅│ │Rtg ✅│ │Earn ✅│            │
│  │So 11  │ │So 01 │ │01:00 │ │So 02 │            │
│  └──────┘ └──────┘ └──────┘ └──────┘            │
│                                                  │
│  DATENBESTAND                                    │
│  ┌─────────────────────────┬───────┬────────┐    │
│  │ prices_daily            │882.4k │ 2021→  │    │
│  │ ark_holdings            │  1.2k │ Apr 26 │    │
│  │ insider_trades          │   340 │ Apr 26 │    │
│  │ technical_indicators    │882.4k │ 2021→  │    │
│  │ ...                     │   ... │ ...    │    │
│  └─────────────────────────┴───────┴────────┘    │
│                                                  │
│  SYSTEM HEALTH                                   │
│  DB: Connected · Alembic: 012 · Uptime: 3d 4h   │
└──────────────────────────────────────────────────┘
```

### 5.2 Universe (`/universe`)

- Filterable Data Table mit Paginierung
- Spalten: Ticker, Company, Exchange, Sector, Index, Status, Last Price, Last Date
- Sortierbar nach jeder Spalte
- Click auf Row → Ticker Detail

### 5.3 Signals (`/signals`)

- Tab-basierte Navigation: ARK | Insider | Politiker | Analyst
- Jeder Tab zeigt eine Timeline/Liste der neuesten Ereignisse
- Badges für Signal-Stärke (z.B. Cluster-Score bei Insider)

### 5.4 Settings (`/settings`)

- **Scheduler:** Job-Cards mit Toggle + Manual Trigger
- **Backfill:** Buttons mit Fortschrittsbalken + Status
- **DB:** Stats-Tabelle + Maintenance-Buttons
- **Config:** Read-only Anzeige der Umgebungsvariablen (maskiert)

### 5.5 Ticker Detail (`/ticker/:symbol`)

- **Hero:** Ticker + Name + aktueller Preis + Tageschange
- **Chart:** Interaktiver Preischart mit TA-Overlays
- **Indikatoren:** RSI Gauge, MACD Histogram, Bollinger Bands
- **Fundamentals:** Key-Metriken in Cards
- **Signal-History:** Timeline aller Signale für diesen Ticker

---

## 6. Docker Multi-Stage Build

```dockerfile
# ── Stage 1: Frontend Build ──
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output: /frontend/dist/

# ── Stage 2: Python Dependencies ──
FROM python:3.12-slim AS python-builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml .python-version ./
RUN uv sync --no-install-project
COPY src/ ./src/
COPY alembic.ini ./
COPY scripts/ ./scripts/
RUN uv sync

# ── Stage 3: Runtime ──
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=python-builder /app /app
COPY --from=frontend-builder /frontend/dist /app/frontend/dist
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY infra/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8090

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD pg_isready -h ${DB_HOST:-localhost} -p ${DB_PORT:-5432} || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uv", "run", "python", "-m", "trading_signals.main"]
```

---

## 7. Neue Dependencies

### Python (pyproject.toml)

| Paket | Version | Zweck |
|---|---|---|
| `fastapi` | `>=0.115` | Backend API Framework |
| `uvicorn[standard]` | `>=0.30` | ASGI Server |

### Frontend (frontend/package.json)

| Paket | Zweck |
|---|---|
| `react` + `react-dom` | UI Framework |
| `react-router-dom` | Client-Side Routing |
| `@tanstack/react-query` | Server-State Management + Caching |
| `recharts` | Charts (Preise, Indikatoren) |
| `lucide-react` | Icons (MIT-lizenziert) |

---

## 8. Backfill-Integration (async)

### Konzept

Backfill-Operationen laufen **asynchron** in einem separaten Thread:

```python
class BackfillManager:
    """Manages long-running backfill operations."""
    
    def __init__(self):
        self._tasks: dict[str, BackfillTask] = {}
    
    def start_price_backfill(self, start_date: str) -> str:
        """Start price backfill in background thread. Returns task_id."""
        
    def start_indicator_backfill(self) -> str:
        """Start TA indicator backfill in background thread."""
        
    def get_status(self, task_id: str) -> BackfillStatus:
        """Get current progress (percent, current_ticker, eta)."""
```

### API-Flow

1. Frontend: POST `/api/v1/ops/backfill/prices` → Backend startet Thread
2. Backend: Returns `{ task_id: "bf_20260413_001", status: "running" }`
3. Frontend: Pollt GET `/api/v1/ops/backfill/status` alle 2 Sekunden
4. Backend: Returns `{ progress_pct: 45, current_ticker: "MSFT", eta_seconds: 120 }`
5. Frontend: Zeigt Fortschrittsbalken mit Live-Updates

---

## 9. Datei-Übersicht (neue/geänderte Dateien)

### Neue Dateien

| Pfad | Beschreibung |
|---|---|
| `src/trading_signals/api/__init__.py` | API-Package |
| `src/trading_signals/api/deps.py` | Dependencies (DB Session, Scheduler) |
| `src/trading_signals/api/schemas.py` | Pydantic Response-Modelle |
| `src/trading_signals/api/routes/dashboard.py` | Dashboard-Endpoint |
| `src/trading_signals/api/routes/universe.py` | Universe-Endpoints |
| `src/trading_signals/api/routes/signals.py` | Signals-Endpoints |
| `src/trading_signals/api/routes/ticker.py` | Ticker-Detail-Endpoints |
| `src/trading_signals/api/routes/operations.py` | Operations-Endpoints |
| `src/trading_signals/api/tasks.py` | BackfillManager (async) |
| `frontend/` | Gesamtes React-Projekt (Vite + TypeScript) |
| `tests/unit/test_api_dashboard.py` | Dashboard API Tests |
| `tests/unit/test_api_operations.py` | Operations API Tests |
| `tests/unit/test_api_universe.py` | Universe API Tests |
| `tests/unit/test_api_signals.py` | Signals API Tests |

### Geänderte Dateien

| Pfad | Änderung |
|---|---|
| `src/trading_signals/main.py` | BlockingScheduler → BackgroundScheduler + FastAPI + uvicorn |
| `pyproject.toml` | `fastapi`, `uvicorn[standard]` hinzufügen |
| `infra/Dockerfile.collector` | Multi-Stage Build (Node + Python) |
| `infra/docker-compose.yml` | Port 8090 Mapping |
| `.env.example` | Ggf. `FRONTEND_DIR` Variable |

---

## 10. Verifikationsplan

### Automatisierte Tests
```bash
uv run pytest tests/unit/test_api_*.py -v
# Erwartung: Alle API-Tests grün
uv run pytest tests/ -q
# Erwartung: 303 + ~40 neue = ~343 Tests grün
```

### Lokale Verifikation
1. `uv run python -m trading_signals.main` → Server startet auf :8090
2. Browser: `http://localhost:8090` → Dashboard wird angezeigt
3. Dashboard: Collector-Status sichtbar
4. Universe: 644 Ticker, Filter funktionieren
5. Settings: Backfill-Button klicken → Fortschritt sichtbar
6. Ticker Detail: `/ticker/AAPL` → Preischart + Indikatoren

### Docker-Verifikation
```bash
cd infra && docker compose up --build
# Browser: http://192.168.1.93:8090
# Gleiche Checks wie lokal
```
