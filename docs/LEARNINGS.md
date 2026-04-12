# LEARNINGS.md – Erkenntnisse aus den Daten

> Lebendes Dokument. Wächst mit, während wir Daten sammeln und auswerten.
> Hier landen alle Beobachtungen, Aha-Momente, gescheiterte Hypothesen und bestätigte Muster.

---

## Wie dieses Dokument genutzt wird

Jeder Eintrag folgt einem einfachen Schema:

```markdown
### [YYYY-MM-DD] Titel der Erkenntnis

**Beobachtung:** Was haben wir gesehen?
**Daten:** Auf welcher Datenbasis?
**Hypothese:** Was könnte dahinter stecken?
**Nächste Schritte:** Wie verifizieren oder widerlegen wir das?
**Status:** 🟡 Offen / 🟢 Bestätigt / 🔴 Widerlegt
```

---

## Kategorien von Erkenntnissen

Wir tracken Erkenntnisse in mehreren Kategorien:

- **📊 Datenqualität** – Welche Quellen sind wie zuverlässig?
- **🎯 Signalstärke** – Welche Features korrelieren mit zukünftiger Performance?
- **🔬 Muster** – Wiederkehrende Phänomene in den Daten
- **⚠️ Fallen** – Dinge, die irreführend aussehen, aber kein echtes Signal sind
- **💡 Hypothesen** – Neue Ideen, die wir testen könnten
- **🛠️ Technisches** – Lessons Learned bei Implementierung und Betrieb

---

## Erkenntnisse

*(Dieses Dokument ist noch leer – die erste Erkenntnis kommt, sobald der erste Datensammler läuft.)*

---

## Geplante Untersuchungen

Sobald genug Daten vorliegen, wollen wir diese Fragen systematisch untersuchen:

### Phase 1 – Nach 1 Monat Datensammlung

- [ ] **Datenqualität:** Wie viele Handelstage haben wir lückenlos? Wie oft sind Quellen ausgefallen?
- [ ] **Universum-Wachstum:** Wie schnell wächst das Titel-Universum? Welche Quellen fügen am meisten Titel hinzu?
- [ ] **ARK-Aktivität:** Wie oft ändern sich ARK-Holdings? Sind die meisten Deltas Rebalances oder echte Conviction?
- [ ] **Insider-Frequenz:** Wie viele Form-4-Filings pro Tag? Welche Unternehmen haben die meisten Insider-Trades?

### Phase 2 – Nach 3 Monaten Datensammlung

- [ ] **Korrelationen Feature ↔ Returns:** Welche Features korrelieren mit 1/5/20-Tages-Returns?
- [ ] **Cluster-Analyse:** Wie oft gibt es Insider-Cluster? Wie korrelieren sie mit Kursbewegungen?
- [ ] **ARK-Predictive-Power:** Wenn ARK nachkauft, wie entwickelt sich der Titel in den nächsten 20 Tagen?
- [ ] **Politiker-Delay:** Ist das Signal wirklich so verzögert wie befürchtet?
- [ ] **Multi-Signal-Überlappungen:** Wie oft stimmen zwei oder drei Signalquellen bei demselben Titel überein?

### Phase 3 – Nach 6 Monaten Datensammlung

- [ ] **Feature-Importance:** Welche Features sind laut Random Forest am wichtigsten?
- [ ] **Optimales Scoring:** Welche Gewichtungen liefern die besten Backtests?
- [ ] **Sektor-Unterschiede:** Funktionieren Signale in allen Sektoren gleich gut?
- [ ] **Zeitliche Stabilität:** Sind Signale über Marktphasen (Bullen/Bären) stabil?
- [ ] **False-Positive-Rate:** Wie viele "gute Scores" führten zu Verlusten?

---

## Hypothesen (zu testen)

Liste von Hypothesen, die wir in den Daten verifizieren wollen:

### H1: ARK-Käufe in mehreren ETFs parallel sind starkes Signal
Wenn Cathie Woods' Team denselben Titel in mehreren ARK-ETFs aufstockt, ist das eher Conviction als Rebalancing.

### H2: Insider-Cluster schlagen einzelne Insider-Käufe
Wenn mehrere Insider eines Unternehmens innerhalb weniger Tage kaufen, ist das prädiktiver als ein einzelner großer Kauf.

### H3: Form 4 in Kombination mit ARK ist stärker als jedes einzeln
Signale aus verschiedenen Quellen sollten unabhängig sein und sich gegenseitig verstärken.

### H4: Gewichtungsänderungen in ARK sind aussagekräftiger als absolute Shares
Weil Share-Änderungen auch von In-/Outflows getrieben sein können, sind Weight-Änderungen das reinere Signal.

### H5: ARK-Verkäufe sind schlechter prädiktiv als ARK-Käufe
Verkäufe können viele Gründe haben (Rebalancing, Outflows), Käufe sind gezielter.

### H6: Kleine Titel reagieren stärker auf ARK-Trades als große
Market-Impact-Effekt: ARK bewegt bei Small Caps den Preis mit.

### H7: Politiker-Trades sind zu verzögert für Alpha
Aber vielleicht als Feature in einer Kombination trotzdem hilfreich.

### H8: Technische Indikatoren alleine erzeugen keine Alpha-Signale
Aber in Kombination mit Fundamentaldaten könnten sie einen Beitrag leisten.

### H9: Analyst-Downgrades sind stärkere Signale als Upgrades
Weil Banken selten negativ über ihre Kunden schreiben – wenn sie es tun, ist es ernst.

### H10: Cluster-Käufe von Insidern nach einem Earnings-Drop sind ein Konstruktionsdach-Signal
Insider kaufen, wenn der Markt überreagiert hat.

---

## Gescheiterte Ansätze (für später)

Hier landen Strategien, die wir ausprobiert haben und die sich als nicht funktionsfähig erwiesen. Genauso wichtig wie bestätigte Signale, um Wiederholungen zu vermeiden.

*(Noch leer)*

---

## Meta-Learnings zum Projekt selbst

*(Noch leer – füllt sich mit Lessons Learned zur Umsetzung)*
