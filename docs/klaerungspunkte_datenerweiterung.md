# Klärungspunkte & Fragen zur Datenerweiterung (Signal Warehouse)

Dieses Dokument dient als zentrale Checkliste für alle offenen Fragen aus Claudes Konzept und unseren architektonischen Überlegungen. Wir können die Punkte hier schrittweise abarbeiten und die Antworten dokumentieren.

## 1. Abgeklärte technische Annahmen (von Antigravity geprüft)
- [x] **Universum (A1):** Ist `universe` das heutige oder ein historisch korrektes Index-Universum? 
  *-> Die Tabelle `signals.universe` speichert nur den aktuellen Zustand (`is_active`, `index_membership` als einfaches Array). Es gibt keine `valid_from`/`valid_to` Historie. Claude hat also recht: Das aktuelle Setup ist anfällig für Survivorship Bias.*
- [x] **Analyst Ratings (B1):** Enthält `analyst_ratings` derzeit nur Ratings und Kursziele, oder auch den EPS-Konsens? 
  *-> Die Tabelle enthält nur Ratings und Kursziele (`rating_new`, `price_target_new` etc.). Es gibt keinen EPS-Konsens. Der vorgeschlagene Estimates-Collector ist also eine echte Lücke.*
- [x] **Kursdaten (C4):** Läuft der Abruf für `prices_daily` über Alpaca mit `adjustment=all`?
  *-> Ja, im Code (`prices_alpaca.py`) ist `adjustment=all` korrekt konfiguriert. Hier droht keine Verzerrung durch Splits/Dividenden.*
- [x] **Tech-Stack:** 
  *-> Ja, PostgreSQL ist im Einsatz (`broker_data` auf `192.168.1.93`). Die Collectoren haben eine saubere Basis-Klasse (`BaseCollector`). Das Scheduling läuft über ein eigenes `jobs.py`-Skript.*
- [x] **Modellierung (G):** Welcher Modelltyp erzeugt aktuell die Feature-Importance?
  *-> Laut `feature_report.py` nutzt du Spearman-Korrelationen sowie Random Forest + LASSO für die Feature-Importance.*
## 2. Strategische & Architektonische Entscheidungen
- [x] **Umgang mit Survivorship Bias (A1) & Lookahead-Prüfung (A2):**
  *-> Entscheidung: Ja, wir bereinigen die Historie. Da die historischen Index-Änderungen öffentlich dokumentiert sind und die Meldedaten (Filing/Disclosure Dates) in den Rohdaten vorliegen, ist das nachträgliche Pflegen absolut machbar. Vorgehen: Wir nutzen dafür temporäre Staging-Tabellen (Schatten-Tabellen). Wir laden die Historie dort rein, prüfen sie auf Vollständigkeit und migrieren sie erst im zweiten Schritt in die Live-Tabellen.*
- [x] **Datenquellen / API-Risiko (B1, B2 & B5):**
  *-> Entscheidung: Wir nutzen die kostenlosen Schnittstellen (`yfinance` für Estimates/SUE, Massive/FINRA für Short Interest). Um Sperrungen zu vermeiden, werden wir die Abfragen strikt throttlen (Rate Limiting, Delays zwischen den Calls) und ein sauberes Error-Handling mit Exponential Backoff einbauen.*
- [x] **Guardrails für Kandidaten (F1):** 
  *-> Entscheidung: Claudes vorgeschlagene harte Filter (Mindest-Liquidität, Max. 2 pro Sektor, Churn-Sperre, "Keine Kandidaten ist ein gültiges Ergebnis") werden übernommen. Allerdings werden diese Schwellenwerte nicht hardcodiert, sondern so umgesetzt, dass sie über die Settings in der UI konfigurierbar sind.*
- [x] **Output-Format (F2):** 
  *-> Entscheidung: Der Vorschlag wird übernommen. Pro Tag wird ein neues Verzeichnis (z.B. `/context_packs/2026-08-19/`) mit einer Übersicht und bis zu 5 einzelnen Kandidaten-Markdowns erstellt.*
- [x] **Skill-Integration (F3):** 
  *-> Entscheidung: Die vom System erzeugten Context-Packs (Markdown-Dateien) werden auf dem Unraid-Server im Pfad `/mnt/user/Workfiles/AlpacaBroker` abgelegt. Um portabel zu bleiben, werden wir diesen Pfad als Umgebungsvariable (Environment Variable) in der App konfigurieren.*

## 3. Priorisierung & Nächste Schritte
- [x] **Startpunkt:** 
  *-> Entscheidung: Wir folgen Claudes dringender Empfehlung und beginnen mit dem **Estimates-Collector (B1)** via `yfinance`, um keine weiteren historischen 90-Tage-Fenster-Daten zu verlieren.*
