# Stufe 2 – Rückmeldung per `decisions.yaml`

Stand 2026-10-07 · Konzept: [§7.7](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)

Der Skill `aktien-analyse` (Claude-Broker) prüft die Kandidaten eines Context Packs mit Live-Daten und schreibt das Ergebnis **in denselben Ordner** wie das Pack:

```
/mnt/user/Workfiles/AlpacaBroker/context_packs/2026-10-06/
├── 00_uebersicht.md
├── 01_NVDA.md
├── ...
└── decisions.yaml      ← schreibt der Skill
```

Der Nachtlauf (04:30, Schritt `stage2_review`) liest die Datei ein und bewertet sie mit der festen Trade-Regel. Laufender Stand: `context_packs/stage2_auswertung.md`.

## Format (Version 1)

```yaml
schema: stage2-decisions/v1
session: 2026-10-06                     # = as_of des Packs (Ordnername), NICHT das heutige Datum
decided_at: 2026-10-07T09:40:00+02:00   # Zeitpunkt der Entscheidung, mit Zeitzone
decisions:
  - ticker: NVDA
    action: buy                         # buy | no_entry
    reason: "Guidance bestätigt, CRV 2,1 am aktuellen Kurs"
    limit: 182.50                       # optional, nur Information
    target_pct: 1.0                     # optional, nur Information
    stop_pct: -2.0                      # optional, nur Information
  - ticker: MSFT
    action: no_entry
    reason: "Kartellverfahren, Abwärtsrisiko über Stop"
```

| Feld | Pflicht | Regel |
|---|---|---|
| `schema` | ja | genau `stage2-decisions/v1` |
| `session` | ja | Datum des Packs = Ordnername |
| `decided_at` | ja | ISO-8601 mit Zeitzone |
| `decisions` | ja | Liste, darf leer sein (`decisions: []` = geprüft, kein Einstieg) |
| `ticker` | ja | Großbuchstaben, jeder Ticker nur einmal; auch Aktien außerhalb der Top 5 erlaubt |
| `action` | ja | `buy` oder `no_entry` |
| `reason` | nein | ein Satz |
| `limit`, `target_pct`, `stop_pct` | nein | werden gespeichert, aber **nicht** bewertet |

## Regeln für den Skill

1. **Jeden geprüften Kandidaten aufführen** – auch abgelehnte (`no_entry`). Erst der Vergleich `buy` gegen `no_entry` zeigt, ob die Prüfung trennt.
2. **Vor der Eröffnung am nächsten Handelstag schreiben** (09:30 New York, meist 15:30 Uhr deutscher Zeit, in den Wochen der Zeitumstellung 14:30 Uhr). Bewertet wird der Einstieg zur Eröffnung. Spätere Dateien oder spätere Änderungen gelten als *verspätet* und zählen nicht zum Erfolgskriterium.
3. **Nach der Eröffnung nichts mehr ändern.** Eine geänderte Datei ersetzt den Stand des Tages, wird über die Dateizeit aber als verspätet erkannt.
4. „Kein Einstieg“ ist ein gültiges Ergebnis – dann `decisions: []` oder nur `no_entry`-Zeilen.

## Bewertung (fest, siehe Konzept §7.7)

- Trade je `buy`: Einstieg `open` des nächsten Handelstags, Ziel +1 %, Stop −2 %, max. 14 Handelstage, 0,05 % Kosten (Barrier-Label aus `feature_snapshots`).
- Vergleich am selben Tag mit allen nach dem Risiko-Vorfilter geeigneten Aktien und dem Universum.
- Urteil frühestens bei ≥ 100 abgeschlossenen, rechtzeitigen `buy`-Trades: Ø Netto > 0 **und** 95 %-KI der Differenz zu den geeigneten Aktien > 0. Davor nur vorläufige Zahlen.
