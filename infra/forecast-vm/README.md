# Prognose-VM – Claude Code headless auf einer Linux-VM (Unraid)

Dieses Paket lässt den Skill `signal-warehouse-prognose` (swp-v2) **täglich unbeaufsichtigt** auf einer kleinen Linux-Desktop-VM laufen, die auf dem Unraid-Server läuft: Claude Code CLI per Timer, Modell fest gepinnt, Ergebnis (`forecasts.yaml`, `trade_ideas.yaml`, `prognosen.md`, `research_sources.md`) landet vor der Versiegelung (09:25 New York) im Pack-Ordner auf dem Share. Format und Frist: [docs/FORECASTS.md](../../docs/FORECASTS.md), [docs/CONTEXT_PACK.md](../../docs/CONTEXT_PACK.md), [docs/TRADE_IDEAS.md](../../docs/TRADE_IDEAS.md); Skill: [.claude/skills/signal-warehouse-prognose/SKILL.md](../../.claude/skills/signal-warehouse-prognose/SKILL.md).

```
Container alpaca-broker  ──schreibt Pack 04:30──▶  Share /mnt/user/Workfiles/AlpacaBroker/context_packs/<Tag>/
                                                                  ▲  (virtiofs: direkt gemappt | rsync: cifs-Mount + sync.sh alle 5 min)
VM  07:30 Di–Sa:  run-forecast.sh ─▶ claude -p "/signal-warehouse-prognose pack=… forecaster_version=… mode=headless"
                                      └▶ liest Pack, recherchiert gezielt im Web, schreibt 4 Dateien in den Pack-Ordner
Container forecast_seal  09:00 / 09:15 / 09:25 New York: SHA-256 von forecasts.yaml  ·  forecast_review 04:30: liest sie ein
```

**Dateien in diesem Ordner**

| Datei | Zweck |
|---|---|
| `run-forecast.sh` | ein Prognoselauf (Vorprüfungen, `claude -p`, Nachprüfung, Push) |
| `sync.sh` | rsync-Abgleich Share ↔ VM (nur Option 2) |
| `common.sh` | gemeinsame Funktionen (wird eingebunden) |
| `forecast.env.example` | Konfiguration (Modell, Pfade, Limits) – nach `~/.config/forecast-vm/forecast.env` kopieren |
| `claude-settings.example.json` | Beispiel für `~/.claude/settings.json` (Berechtigungen) |
| `systemd/*.service`, `systemd/*.timer` | Timer-Units (systemd `--user`) |
| `crontab.example` | Alternative zu systemd |

Keine Zugangsdaten im Repository: SMB-Passwort in `/etc/forecast-vm/smb.cred`, Claude-Login in `~/.claude/`, optional ein Token in einer eigenen 600-Datei – nichts davon gehört in Git.

## 0. Die zwei Entscheidungen vorab (Sebastian)

1. **Modell** (`MODEL` in `forecast.env`, Standard `claude-opus-5-5`). Die volle Modell-ID, kein Alias. Sie steckt in `forecaster_version` (`swp-v2+claude-opus-5-5+pack2`). **Jeder Wechsel – Modell oder Skill-Version – startet die vorab registrierte Stichprobe (300 Handelstage) neu.** Also einmal entscheiden und dabei bleiben.
2. **Pack-Zugriff:** Option 1 *virtiofs* (kein Sync, einfachste Laufzeit, aber die VM sieht den ganzen gemappten Ordner und braucht passende UID) oder Option 2 *rsync/cifs* (VM sieht nur ihren Spiegel plus Zugriff auf den Share per SMB-Benutzer, Sync-Skript als zusätzliche Fehlerquelle). Empfehlung: **virtiofs**, wenn Unraid auf deiner Version es sauber anbietet; sonst rsync.

## 1. VM anlegen

- Unraid → VMs → Linux-VM (z. B. **Ubuntu Server 24.04 LTS** oder Debian 12; „Desktop“ geht auch, ist aber nicht nötig). 2 vCPU, 4–8 GB RAM, 20–30 GB Disk. Netzwerk: Bridge `br0`, feste IP.
- **Zeitzone und Uhr sind kritisch** (`decided_at` kommt von der VM-Uhr, die Versiegelung von Unraid): `sudo timedatectl set-timezone Europe/Berlin` und NTP an (`timedatectl` zeigt `System clock synchronized: yes`).
- Pakete: `sudo apt update && sudo apt install -y git rsync cifs-utils jq curl ca-certificates`.
- Benutzer `forecast`:
  - bei **virtiofs** mit der UID/GID des Unraid-Benutzers `nobody:users`, sonst kann die VM nicht in die vom Container angelegten Pack-Ordner schreiben und der Container nicht lesen, was die VM schreibt: `sudo useradd -m -u 99 -g 100 -s /bin/bash forecast` (UID 99 / GID 100 müssen in der VM frei bzw. `users` sein – unter Ubuntu/Debian sind sie es);
  - bei **rsync/cifs** reicht ein normaler Benutzer: `sudo adduser forecast`.
- Alles Weitere als Benutzer `forecast`: `sudo -iu forecast`. Der Rest geht davon aus: Home `/home/forecast`.

## 2. Pack-Zugriff

### Option 1 – virtiofs (Unraid-Share in die VM mappen)

1. VM ausschalten → in Unraid die VM bearbeiten → Abschnitt **Unraid Share**: *Mode* **Virtiofs**, *Unraid Source Path* `/mnt/user/Workfiles/AlpacaBroker`, *Unraid Mount Tag* `alpaca`. (Beschriftungen je nach Unraid-Version; akzeptiert die Maske nur ganze Shares, nimm `/mnt/user/Workfiles` – dann liegt der Pack-Ordner in der VM unter `…/AlpacaBroker/context_packs`.) Das mappt den Ordner **vollständig** in die VM (also z. B. auch `ops/`); die Claude-Berechtigungen (Abschnitt 6) begrenzen, was der Agent anfasst. Ist dir das zu breit: Option 2.
2. Startet die VM nicht mit einer Meldung zu *shared memory*/`memoryBacking`, braucht virtiofs gemeinsamen Gastspeicher – im Unraid-XML-Editor `<memoryBacking><source type='memfd'/><access mode='shared'/></memoryBacking>` ergänzen ([libvirt: virtiofs](https://libvirt.org/kbase/virtiofs.html)).
3. In der VM (als root/sudo):
   ```
   sudo mkdir -p /mnt/alpaca
   echo 'alpaca /mnt/alpaca virtiofs defaults,nofail 0 0' | sudo tee -a /etc/fstab
   sudo mount -a && ls -ln /mnt/alpaca/context_packs | head
   ```
   Der Besitzer neuer Pack-Ordner muss `99 100` sein. Wichtig: ein Fehler in `/etc/fstab` kann den Boot aufhalten – deshalb `nofail` und vor dem Neustart `sudo mount -a` testen.
4. `forecast.env`: `PACK_MODE=virtiofs`, `SHARE_PACK_DIR=/mnt/alpaca/context_packs`. Kein `sync.sh`, keine Timer dafür.

### Option 2 – rsync über SMB (cifs)

1. **In Unraid** (machst du selbst): einen eigenen Benutzer, z. B. `forecastvm`, nur mit Lese-/Schreibrecht auf den Share `Workfiles` – kein root, kein Zugriff auf andere Shares.
2. In der VM die Zugangsdaten **nicht ins Repo**, sondern in eine root-Datei:
   ```
   sudo mkdir -p /etc/forecast-vm
   sudo sh -c 'umask 077; printf "username=forecastvm\npassword=DEIN_PASSWORT\n" > /etc/forecast-vm/smb.cred'
   sudo chmod 600 /etc/forecast-vm/smb.cred && sudo chown root:root /etc/forecast-vm/smb.cred
   ```
3. Mount (lazy per systemd-automount, übersteht Unraid-Neustarts): `sudo mkdir -p /mnt/unraid-workfiles`, dann in `/etc/fstab`:
   ```
   //HOMENAS/Workfiles /mnt/unraid-workfiles cifs credentials=/etc/forecast-vm/smb.cred,uid=forecast,gid=users,file_mode=0664,dir_mode=0775,vers=3.0,noperm,nofail,_netdev,x-systemd.automount,x-systemd.idle-timeout=10min,x-systemd.mount-timeout=30 0 0
   ```
   `getent hosts HOMENAS` muss auflösen (sonst die IP des Unraid-Servers eintragen). Test: `sudo systemctl daemon-reload && ls /mnt/unraid-workfiles/AlpacaBroker/context_packs`.
4. Schreibtest (kein echtes Ergebnis!): `touch /mnt/unraid-workfiles/AlpacaBroker/context_packs/vm_write_test.txt`, in Windows unter `\\HOMENAS\Workfiles\AlpacaBroker\context_packs\` prüfen, dann `rm` in der VM. Dass der Container Dateien aus der VM lesen kann, zeigt sich spätestens an `context_packs/forecast_auswertung.md` am nächsten Morgen („Eingelesene Dateien“).
5. `forecast.env`: `PACK_MODE=rsync`, `SHARE_PACK_DIR=/mnt/unraid-workfiles/AlpacaBroker/context_packs`, `LOCAL_PACK_ROOT=$HOME/alpaca/context_packs`.
6. `sync.sh` zieht neue, **vollständige** Packs (Ordner mit `00_uebersicht.md`) auf die VM und schiebt **nur** `forecasts.yaml` (nie ein vorhandenes auf dem Share überschreiben – `--ignore-existing`), `trade_ideas.yaml`, `prognosen.md`, `research_sources.md` in den passenden, schon existierenden Session-Ordner zurück; nie mit `--delete`. Fällt der Mount aus, beendet es sich mit Fehlercode 2 und versucht es in 5 Minuten erneut.

## 3. Claude Code installieren und anmelden

```
curl -fsSL https://claude.ai/install.sh | bash        # nativer Installer, legt ~/.local/bin/claude an
claude --version && claude doctor
claude                                                # einmal INTERAKTIV: Login im Browser (URL öffnen, Code einfügen)
claude auth status                                    # Exit-Code 0 = angemeldet
```

- Der interaktive Login liegt in `~/.claude/.credentials.json` (Modus 600). Läuft er irgendwann unbemerkt ab, scheitert der Lauf mit Exit-Code 10 („not logged in“). **Robuster für Unbeaufsichtigtes:** `claude setup-token` druckt ein Jahrestoken – **allein** in eine Datei (`chmod 600`) legen und in `forecast.env` `OAUTH_TOKEN_FILE=…` setzen (kann nur Modellanfragen, braucht ein Abo-Konto).
- Auto-Update (`autoUpdatesChannel: stable` im Beispiel) bleibt an; die CLI-Version steht in jedem Lauf-Log (`claude 2.x.y`). Ein neues Modell kann eine neuere CLI verlangen.
- Verfügt das Konto über ein Abo, zählt der Lauf gegen dessen Limits; `MAX_BUDGET_USD` ist eine clientseitige Schätzung, keine Rechnung.

## 4. Repository klonen (nur das Nötige)

Das Repo ist privat. Auf der VM einen **schreibgeschützten Deploy-Key** einrichten und nur die benötigten Ordner auschecken (kein `src/`, keine Daten):

```
ssh-keygen -t ed25519 -f ~/.ssh/alpaca_deploy -C forecast-vm -N ""
cat ~/.ssh/alpaca_deploy.pub     # → GitHub: Repo Settings → Deploy keys → Add (Allow write access NICHT anhaken)
mkdir -p ~/alpaca && cd ~/alpaca
GIT_SSH_COMMAND='ssh -i ~/.ssh/alpaca_deploy -o IdentitiesOnly=yes' \
  git clone --filter=blob:none --sparse git@github.com:sebastianganser/Alpaca-Broker.git repo
cd repo && git sparse-checkout set .claude/skills docs infra/forecast-vm
git config core.sshCommand 'ssh -i ~/.ssh/alpaca_deploy -o IdentitiesOnly=yes'
```

Aktualisieren später **bewusst** (siehe Abschnitt 8): `git -C ~/alpaca/repo pull`. Das Skript bricht ab (Exit-Code 43), wenn die ausgecheckte Skill-Version nicht zu `EXPECT_SKILL_VERSION` passt.

## 5. Konfiguration

```
mkdir -p ~/.config/forecast-vm ~/alpaca/workdir ~/.claude
cp ~/alpaca/repo/infra/forecast-vm/forecast.env.example ~/.config/forecast-vm/forecast.env
chmod 600 ~/.config/forecast-vm/forecast.env
cp ~/alpaca/repo/infra/forecast-vm/claude-settings.example.json ~/.claude/settings.json   # existiert die Datei schon: zusammenführen
$EDITOR ~/.config/forecast-vm/forecast.env      # PACK_MODE, SHARE_PACK_DIR, MODEL, Limits
```

`~/alpaca/workdir` bleibt **leer**: Claude startet dort, also wird die `CLAUDE.md` des Repos (Produktions-Betrieb, „deploy ohne zu fragen“) nicht geladen. Den Skill findet Claude über `--add-dir <Repo>`. Fehlt der Skill in deiner CLI-Version trotzdem („Unknown command“ im Lauf-Log), lege ihn im Arbeitsverzeichnis an: `mkdir -p ~/alpaca/workdir/.claude/skills && ln -s ~/alpaca/repo/.claude/skills/signal-warehouse-prognose ~/alpaca/workdir/.claude/skills/`.

## 6. Berechtigungen (warum `settings.json` hier wichtig ist)

`claude -p` zeigt nie einen Vertrauensdialog. **Berechtigungsregeln (`permissions.allow`) in der `.claude/settings.json` eines Projekts werden deshalb unter `-p` für einen nie vertrauten Ordner ignoriert** (Claude Code meldet das mit `this workspace has not been trusted` auf stderr); `deny`-Regeln gelten immer. Hier kommt dazu, dass das Repo nur als *zusätzliches* Verzeichnis (`--add-dir`) geladen wird – dessen Projekteinstellungen greifen ohnehin nicht (aus solchen Verzeichnissen werden nur Skills, Befehle und Agenten geladen). Darum liegen die Regeln an zwei anderen Stellen, die sich überlappen dürfen:

1. **Auf der Kommandozeile** (`--allowedTools`, `--disallowedTools`, `--permission-mode dontAsk`) – gebaut aus `forecast.env` für den tatsächlichen Pack-Pfad. Maßgeblich.
2. **In `~/.claude/settings.json`** (Benutzereinstellungen; wirkt unabhängig vom Ordner) – Beispiel mit den statischen Teilen und den gesperrten Domains. Bei virtiofs den Pfad `~/alpaca/context_packs` im Beispiel durch den Mount-Pfad ersetzen (`//mnt/alpaca/context_packs/**`, absolute Pfade beginnen mit `//`).

Was der Lauf darf (Modus `dontAsk` = alles Nicht-Erlaubte wird ohne Rückfrage verweigert):

| Erlaubt | Anmerkung |
|---|---|
| Lesen: Pack-Ordner, Repo (Skill + Docs), Arbeitsverzeichnis | über `--add-dir`; außerhalb nichts (`~/.claude`, `~/.ssh`, `~/.config/forecast-vm` zusätzlich per Regel gesperrt) |
| Schreiben: genau vier Dateien im Ordner des bewerteten Packs | `forecasts.yaml`, `trade_ideas.yaml`, `prognosen.md`, `research_sources.md` – Ticker-Dateien, Übersicht und `screen.csv` bleiben unantastbar |
| `WebSearch`, `WebFetch` | siehe unten |
| Bash | nur `date` plus die eingebauten Nur-Lese-Befehle (`ls`, `cat`, `grep`, `head`, `tail`, `wc`, `find`); `curl`, `wget`, `ssh`, `scp`, `sudo` ausdrücklich gesperrt |
| Nicht vorhanden | Subagenten, Notebooks, MCP – `--tools` listet nur Read, Write, Edit, Glob, Grep, Bash, WebFetch, WebSearch, Skill |

**WebFetch: uneingeschränkt, mit Sperrliste – bewusst.** Eine Positivliste (`WebFetch(domain:sec.gov)`, `…stockanalysis.com`) wäre die strengere Variante, schneidet aber genau die geforderte Recherche ab: Investor-Relations-Seiten und Pressemitteilungen liegen auf hunderten firmenspezifischen Domains (`investor.…`, `ir.…`, Newswire-Dienste), die vorher nicht bekannt sind. Deshalb ist `WebFetch` offen und die Seiten, deren Nutzungsbedingungen automatisierten Zugriff verbieten (TradingView, StockCharts, Barchart, Investing.com, Yahoo, Seeking Alpha, MarketBeat, Zacks, Motley Fool), sind per `deny` technisch gesperrt – zusätzlich zur Anweisung im Skill. Restrisiko: Eine präparierte Webseite könnte versuchen, den Agenten zu Abrufen mit angehängten Daten zu verleiten (Prompt-Injection). Schutz: die Injektionsregel im Skill, ein Agent ohne Schreib-/Shell-Zugriff außerhalb der vier Dateien, kein Zugriff auf Zugangsdaten, und die Daten im Pack sind abgeleitete Marktdaten ohne Geheimnisse. Wer die strenge Variante will, ersetzt in `settings.json` und in `run-forecast.sh` das bare `WebFetch` durch `WebFetch(domain:www.sec.gov)`, `WebFetch(domain:stockanalysis.com)` und die gewünschten Newswire-Domains und nimmt in Kauf, dass Firmen-IR-Seiten nur über Suchtreffer auf diesen Domains erreichbar sind.

Cookie-Banner: Beim reinen Textabruf (`WebFetch`) gibt es keinen Banner zu bestätigen; die stehende Erlaubnis von Sebastian steht trotzdem im Skill.

## 7. Testlauf (bevor ein Timer läuft)

```
# a) Anmeldung, Modell-ID und eine erlaubte Abfrage (kostet Cent-Beträge)
cd ~/alpaca/workdir
claude -p "Antworte mit genau einem Wort: OK" --model claude-opus-5-5 --permission-mode dontAsk --max-budget-usd 0.5

# b) Vorprüfungen ohne claude-Aufruf (zeigt die vollständige Kommandozeile)
bash ~/alpaca/repo/infra/forecast-vm/run-forecast.sh --dry-run

# c) WebFetch gegen SEC EDGAR (bekanntes Risiko: sec.gov verlangt einen identifizierenden User-Agent und kann 403 liefern -
#    dann nicht umgehen, der Skill nimmt die Pressemitteilung der Firma / eine Suche)
claude -p "Rufe https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=5 per WebFetch ab und nenne das erste Unternehmen." \
  --model claude-opus-5-5 --permission-mode dontAsk --allowedTools WebFetch --max-budget-usd 0.5
```

**d) Volltest auf einer Kopie eines alten Packs – berührt den Share nie.** `PACK_MODE=local` schreibt nur in den Test-Ordner:

```
mkdir -p ~/alpaca/test/context_packs
cp -r <Share>/context_packs/2026-10-16 ~/alpaca/test/context_packs/        # ein Ordner, mit pack_format: 2
rm -f ~/alpaca/test/context_packs/2026-10-16/{forecasts.yaml,trade_ideas.yaml,prognosen.md,research_sources.md}
cat > ~/.config/forecast-vm/test.env <<'EOF'
PACK_MODE=local
LOCAL_PACK_ROOT=$HOME/alpaca/test/context_packs
STATE_DIR=$HOME/alpaca/test/state
LOG_DIR=$HOME/alpaca/test/logs
EXPECT_SKILL_VERSION=swp-v2
EOF
FORECAST_ENV=~/.config/forecast-vm/test.env bash ~/alpaca/repo/infra/forecast-vm/run-forecast.sh --ignore-deadline
```

Der Skill läuft dann mit `mode=test` (Frist nicht erzwungen, nur 1–2 Webabrufe, damit ein alter Pack keine späteren Kurse sieht). Prüfen: `~/alpaca/test/context_packs/…/forecasts.yaml` ist gültig, der Lauf-Log meldet `check: …`, im JSON stehen `total_cost_usd` und `num_turns` (daraus `MAX_BUDGET_USD` ableiten). **Die Testdateien nie auf den Share kopieren.**

## 8. Timer aktivieren

**systemd (empfohlen):**

```
sudo loginctl enable-linger forecast        # Benutzer-Timer laufen ohne Login
mkdir -p ~/.config/systemd/user && cp ~/alpaca/repo/infra/forecast-vm/systemd/* ~/.config/systemd/user/
systemd-analyze calendar "Tue..Sat 07:30 Europe/Berlin"       # Syntax prüfen
systemctl --user daemon-reload
systemctl --user enable --now forecast-run.timer
systemctl --user enable --now forecast-sync.timer              # nur Option 2
systemctl --user list-timers
```

Hinweis: Bei UID 99 (virtiofs) verweigern manche Distributionen Benutzer-Timer für Systembenutzer-UIDs – dann `crontab.example` nehmen (`crontab -e`, VM-Zeitzone muss Europe/Berlin sein). **Entweder systemd oder cron, nicht beides.**

Der Lauf startet Di–Sa um **07:30** (die Nightly Chain schreibt den Pack um ~04:30–05:00 für die Vorsitzung Mo–Fr) und versucht es um **09:30** und **11:30** erneut – diese Wiederholungen tun nichts, wenn `forecasts.yaml` schon da ist, und teilen sich das Limit `MAX_ATTEMPTS` (2 Claude-Starts je Pack), damit kein Fehler die Kosten vervielfacht. Bei Packs nach einem Wochenende (Freitag-Pack, Samstag-Lauf) liegt die Frist am Montag, 09:25 New York.

## 9. Windows-Aufgabe `signal-warehouse-prognose` abschalten

Sobald die VM den ersten echten Lauf geliefert hat (oder spätestens **vor dem ersten offiziellen Prognosetag**), in der Claude-Desktop-App unter *Geplante Aufgaben* die Aufgabe **`signal-warehouse-prognose` deaktivieren** (das machst du in der App; hier wird nichts angefasst). Sonst konkurrieren zwei Prognostiker: Der zweite findet `forecasts.yaml` vor und bricht ab – aber wer zuerst schreibt, bestimmt die `forecaster_version`, und ein Wechsel startet die Stichprobe neu. Der Windows-Skill-Stand (swp-v1) und die VM (swp-v2) sind **verschiedene Versionen**.

## 10. Betrieb

**Jeden Morgen (oder bei Verdacht):**

```
cat ~/alpaca/state/last-run                      # Zeit, Pack, Version, Probleme, Push-Status des letzten Laufs
tail -n 40 ~/alpaca/logs/run.log                 # Verlauf; Prüfzeilen "check: forecasts.yaml has N rows, version …"
systemctl --user status forecast-run.service     # letzter Exit-Code
systemctl --user --failed
ls -l <Share>/context_packs/<Tag>/forecasts.yaml # liegt die Datei vor 09:25 New York auf dem Share?
```

Danach zeigt `context_packs/forecast_auswertung.md` (Nachtlauf 04:30), was eingelesen und was abgelehnt wurde. Lauf-JSON und Fehlerausgabe: `~/alpaca/logs/run-<Pack>-<Zeit>.json` / `.stderr`; vollständige Transkripte (welche Seiten wurden abgerufen) unter `~/.claude/projects/…` (`cleanupPeriodDays: 90` im Beispiel).

| Exit-Code | Bedeutung | Was tun |
|---|---|---|
| 0 | fertig, oder nichts zu tun (Datei schon da, noch kein Pack) | – |
| 10 | Claude nicht angemeldet | `claude` interaktiv, `/login`; oder `OAUTH_TOKEN_FILE` |
| 20 | Lauf ohne `forecasts.yaml` | Lauf-JSON/-stderr lesen; Retry-Timer versucht es (max. `MAX_ATTEMPTS`) |
| 21 | Zeitlimit `RUN_TIMEOUT_MIN` | Log lesen, ggf. Limits anpassen |
| 30 | Ausgabeprüfung fehlgeschlagen (Version, Zeitzone, verbotenes Feld, …) | **Datei von Hand korrigieren, solange vor 09:25 New York**; danach gilt jede Änderung als verspätet |
| 31 | Push auf den Share fehlgeschlagen | Mount prüfen (`ls /mnt/…`), `bash sync.sh`; die Datei muss vor 09:25 New York auf dem Share liegen |
| 40 | zu spät für diesen Pack | Tag verpasst (Folgen: [FORECASTS.md](../../docs/FORECASTS.md) §6) – Ursache klären (Chain spät? VM aus?) |
| 41 | Versuchslimit erreicht | Logs ansehen, bevor `~/alpaca/state/attempts-<Tag>` gelöscht wird |
| 42 | Pack nicht `pack_format: 2` | Skill/Pack-Format abgleichen |
| 43 | Skill-Pin passt nicht | `git pull` hat den Skill verändert – bewusst entscheiden (unten) |

**Skill oder Modell ändern (bewusst):** Jede Änderung von `MODEL`, der Skill-Version `swp-vN` oder des Pack-Formats ändert `forecaster_version` und **startet die 300-Tage-Stichprobe von vorn** ([FORECASTS.md](../../docs/FORECASTS.md) §6; eine unterbrochene Version wird nie ausgewertet, ein früherer Name darf nicht wieder verwendet werden). Ablauf: Entscheidung treffen → `git pull` → `EXPECT_SKILL_VERSION` (und ggf. `EXPECT_SKILL_COMMIT`) und `MODEL` in `forecast.env` anpassen → Testlauf (Abschnitt 7d). Reine Wortlaut-Korrekturen am Skill, die den Prognostiker nicht ändern sollen, brauchen trotzdem eine ehrliche Entscheidung: Im Zweifel ist es eine neue Version.

**Kosten:** `MAX_BUDGET_USD` (Standard 12) und `MAX_TURNS` (300) sind Notbremsen. Nach den ersten Läufen aus `total_cost_usd` im Lauf-JSON den Wert auf ~2× den Normalverbrauch setzen.

## 11. Fehlersuche

- **`Unknown command /signal-warehouse-prognose`** → Skill nicht gefunden: Symlink aus Abschnitt 5 anlegen, `ls ~/alpaca/repo/.claude/skills/signal-warehouse-prognose/SKILL.md` prüfen.
- **Lauf endet ohne Datei, im JSON `permission_denied`/„not allowed“** → die Berechtigungsregel passt nicht zum tatsächlichen Pfad (Symlink im Mount? `readlink -f $PACK_ROOT`). `--dry-run` zeigt die Regeln.
- **`this workspace has not been trusted` im stderr** → harmlos: Projektregeln gelten unter `-p` nicht; die Regeln stehen deshalb auf der Kommandozeile und in den Benutzereinstellungen (Abschnitt 6).
- **Share-Dateien gehören in Unraid `1000:1000` o. ä.** → bei virtiofs läuft der Benutzer nicht mit UID 99/GID 100 (Abschnitt 1).
- **`flock`/Sperrdatei-Fehler** → `STATE_DIR` muss auf der lokalen Platte liegen, nicht auf dem Share (virtiofs/cifs sperren unzuverlässig).
- **`forecaster_version … != expected`** (Exit 30) → der Agent hat die vorgegebene Version nicht übernommen; Datei vor 09:25 korrigieren und im Lauf-Log nachsehen, warum.

## Offene Entscheidungen für Sebastian

1. **Modell-Pin** (`MODEL`, Standard `claude-opus-5-5`) – und damit die erste `forecaster_version` `swp-v2+claude-opus-5-5+pack2`.
2. **virtiofs oder rsync/cifs** (Abschnitt 2); bei virtiofs ob der ganze `AlpacaBroker`-Ordner in der VM sichtbar sein darf.
3. **WebFetch offen + Sperrliste** (Standard) oder strenge Domain-Positivliste (Abschnitt 6).
4. **Anmeldung:** interaktiver Login (Standard) oder Jahrestoken per `claude setup-token` (robuster für Unbeaufsichtigtes).
5. **Budget-Notbremse** (`MAX_BUDGET_USD`) nach den ersten Läufen.
