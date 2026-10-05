# Sprint 9 – Jupyter Notebooks

Analyse-Notebooks für die explorative Datenanalyse der Feature Snapshots.

## Setup

### 1. Dependencies installieren

```bash
# Im Projekt-Root (Windows):
uv pip install -e ".[analysis]"
```

### 2. Umgebungsvariablen

Die Notebooks verwenden die bestehende `.env` Datei im Projekt-Root.
Stelle sicher, dass die DB-Verbindung korrekt konfiguriert ist:

```
DB_HOST=192.168.1.93
DB_PORT=5435
DB_NAME=broker_data
DB_USER=sebastian
DB_PASSWORD=<dein-passwort>
```

### 3. Notebooks starten

**Option A: VS Code (empfohlen)**
- `.py`-Dateien mit `# %%` Cell Markers öffnen
- VS Code erkennt diese automatisch als Jupyter-Cells
- Cells einzeln mit `Ctrl+Enter` oder `Shift+Enter` ausführen

**Option B: JupyterLab**
```bash
cd notebooks/
jupyter lab
```
→ `.py`-Dateien können als Notebooks geöffnet werden (Rechtsklick → "Open With" → "Notebook")

## Notebooks

| Nr. | Datei | Inhalt |
|-----|-------|--------|
| 01 | `01_descriptive_statistics.py` | Datenqualität, Missing Rates, Verteilungen, Return-Analyse |
| 02 | `02_feature_return_correlations.py` | Tägliche Rang-ICs (ICIR, Newey-West-t), Politician Dual-Date, Quintile pro Datum, IC-Stabilität, Rang-Korrelationen |
| 03 | `03_feature_importance.py` | Random Forest & LASSO mit gepurgter Walk-Forward-CV, Hypothesen-Tests H1–H13 (pro Datum, Newey-West, Block-Bootstrap) |

## Hinweise

- Analysefenster: `snapshot_date >= ml_start_date()` (`trading_signals.utils.retention`),
  geladen werden nur Schlüssel-, Feature- und Target-Spalten (kein `SELECT *`).
- Feature-Liste kommt aus dem Modell (`FEATURE_COLUMNS` in `db/models/features.py`),
  Gruppen werden per Namenspräfix abgeleitet (`analysis/feature_groups.py`).
- Targets: `return_h = close(d+h) / open(d+1) - 1`; Notebooks 02/03 verwenden
  Überrenditen ggü. dem Querschnitts-Mittel pro Tag.
- Statistik-Helfer (Daily Rank IC, Newey-West, Purged CV, Block-Bootstrap) liegen in
  `trading_signals.analysis.stats` / `analysis.modeling` und sind unit-getestet.
- Ergebnisse der alten Notebook-Versionen (gepoolte Korrelationen, leaky CV) sind
  verzerrt – Schlussfolgerungen nach dem Rebuild neu ableiten und in
  `docs/LEARNINGS_HYPOTHESES.md` dokumentieren.
