# Alpaca Broker – Frontend

React-Oberfläche (Vite + TypeScript) für das Signal Warehouse: Dashboard,
Ticker-Universum, Signale, Feature-Pipeline, Logs und Betrieb.

## Entwicklung

```bash
npm install
npm run dev      # http://localhost:5173, /api wird an http://localhost:8090 weitergeleitet
npm run build    # Typecheck (tsc -b) + Produktions-Build nach dist/
npm run lint     # ESLint
```

Das Backend (FastAPI) muss für die Entwicklung lokal auf Port 8090 laufen.

## Authentifizierung

Schreibende bzw. geschützte Endpunkte erwarten einen API-Schlüssel. Er wird unter
**Settings → Zugang** im Browser (`localStorage`) gespeichert und von
`src/api.ts` bei jeder Anfrage als `X-API-Key` mitgeschickt (zusätzlich immer
`X-Requested-With: XMLHttpRequest`). Bei 401/403 zeigt die UI einen Hinweis auf
die Einstellungen.

## Struktur

| Pfad | Inhalt |
| --- | --- |
| `src/api.ts` | Fetch-Wrapper (`ApiError` mit Status + Backend-`detail`), Typen, Endpunkt-Funktionen |
| `src/queries.ts` | Zentrale TanStack-Query-Keys und `queryOptions` |
| `src/format.ts` | Gemeinsame Formatierer (Zahlen, Prozent, Datum, Dauer) |
| `src/components/` | `DataTable` (Sortieren/Filtern), `QueryState` (Laden/Fehler/Leer) |
| `src/pages/` | Seiten; Signal-Tabs unter `pages/signals/`, Settings-Karten unter `pages/settings/` |

Listen-Endpunkte liefern die ungekürzte Gesamtzahl im Header `X-Total-Count`;
die UI zeigt dann „Zeige N von M Einträgen“.

Die Versionsnummer in der Seitenleiste stammt aus `package.json` (`__APP_VERSION__`).
