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
| 02 | `02_feature_return_correlations.py` | Spearman-Korrelationen, Politician Dual-Date, Quintil-Analyse |
| 03 | `03_feature_importance.py` | Random Forest, LASSO, Hypothesen-Tests H1–H13 |

## Hinweise

- **return_60d** ist aus der Analyse ausgeschlossen (nur 6.2% Fill Rate)
- **13F-Features** sind ausgeschlossen (kein Quarter seit Pipeline-Start)
- Alle Ergebnisse werden in `docs/LEARNINGS_HYPOTHESES.md` dokumentiert
