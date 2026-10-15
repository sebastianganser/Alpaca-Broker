# Abschluss-Auftrag für Antigravity (vor Übergabe an Claude Code)

Stand 15.10.2026. Diesen Text komplett in Antigravity einfügen.
Hintergrund: [UEBERNAHME_PRUEFUNG.md](UEBERNAHME_PRUEFUNG.md)

---

```text
Abschluss vor Übergabe: Die Arbeit an diesem Projekt geht künftig in Claude Code weiter.
Claude Code sieht nur das Repository, nicht deine Unterhaltungen, Pläne oder Artefakte.
Bring deshalb die Projekt-Dokumentation auf den vollständigen, aktuellen Stand,
sodass eine neue Session nur aus dem Repo heraus weiterarbeiten kann.

REGELN
- Nur Dokumentation ändern (*.md in docs/, CLAUDE.md, README.md). Keinen Code, keine Tests,
  keine Migrationen, keine Konfiguration ändern.
- .env nicht lesen und nicht ändern. Keine Passwörter, Keys oder Tokens in die Doku schreiben.
- Den Ordner .claude/ nicht anfassen und nicht committen.
- Fakten am Code prüfen, nicht aus dem Gedächtnis übernehmen.
- Tests NICHT ausführen: zwei Tests in test_politician_trades.py verbinden sich mit der echten DB.
  Für die Testanzahl nur: uv run pytest --collect-only -q
- Sprache des jeweiligen Dokuments beibehalten.

AUFGABEN
1. Wissen aus deinen Unterhaltungen sichern: Geh deine Pläne, Aufgabenlisten und
   Zusammenfassungen zu diesem Projekt durch. Alles, was noch nicht im Repo steht
   (Entscheidungen, Erkenntnisse, offene Punkte, angefangene Arbeiten, Ideen),
   in die passende Datei übertragen: DECISIONS_*.md, LEARNINGS*.md, ROADMAP.md.
2. SESSION_LOG.md: alle Sessions nach Session 29 nachtragen, auch die Commits vom 15.10.
   (050ff32, 97fcdb0) und die heutige Arbeit. Als letzten Eintrag eine Session
   "Übergabe an Claude Code" schreiben mit:
   - welcher Commit auf Unraid deployt ist und was noch nicht deployt ist
   - welche Repair-Skripte (scripts/repair/) schon gelaufen sind und welche noch offen sind
   - offene manuelle Schritte auf Unraid
   - angefangene oder unterbrochene Arbeiten
   - bekannte Probleme, auch: die zwei Tests test_store_writes_trades und
     test_store_dedup_counts öffnen über NewTickerOnboarder eine echte DB-Session
     (nur dokumentieren, nicht beheben)
   - konkreter nächster Schritt
3. ROADMAP.md: "Last updated", Status von Sprint 9.5c (Phase 1/2, K1–K5) und nächste Schritte
   aktualisieren. Den Test-Befund aus Punkt 2 als offenen Punkt aufnehmen.
4. CLAUDE.md: nur "Current Status" und die Tabelle "Quick Facts" aktualisieren, alle Werte am Code
   geprüft: Anzahl und Art der Scheduler-Jobs (scheduler/registry.py, scheduler/setup.py),
   Preis-Feed (Standard in config.py ist "sip", in CLAUDE.md steht "IEX": klären, was in
   Produktion läuft), Anzahl aktiver Ticker, Testanzahl. Die Tabelle "Model Routing" und die
   Session-Checklisten NICHT ändern, die passt Claude Code bei der Übernahme selbst an.
5. ARCHITECTURE.md (Stand Mai 2026): auf den aktuellen Aufbau bringen, u. a. analysis/,
   derived/ (Context Pack, Stage 2, Short-Term-Features, Target Backfill), Nightly Chain,
   Context-Pack-Ausgabe auf Unraid.
6. DATABASE.md: prüfen, dass alle Migrationen 001–035 mit ihren Tabellen/Spalten beschrieben sind.
7. SCHEDULER.md: prüfen, dass jeder Job aus dem Code mit Zeitplan drinsteht, und umgekehrt.
8. INDEX.md: alle vorhandenen Dokumente aufnehmen (u. a. STAGE2_DECISIONS.md, die datierten
   Konzept-Dateien, docs/reports/, notebooks/README.md), "Last updated" setzen.
9. docs/task.md, docs/implementation_plan1.md und docs/implementation_plan2.md: falls dort noch
   etwas steht, das nirgends sonst dokumentiert ist, übertragen. Danach mit git mv nach
   docs/archive/ verschieben (nicht löschen) und in INDEX.md als Archiv erwähnen.

ABSCHLUSS
- Alles in einem Commit: "docs: complete documentation for handover to Claude Code".
  Nicht pushen.
- Kurzer Bericht: welche Dateien du geändert hast, was du nicht klären konntest,
  und was du nur aus deinen Unterhaltungen wusstest (nicht aus dem Code).
```
