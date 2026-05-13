# Sprint 8b – Features Page & Exploration UI

> Detailplan: [implementation_plan2.md](implementation_plan2.md)

## Task A: Backend-Endpoints (API + Schemas)
- [ ] A1: Pydantic Schemas (FeatureCoverageItem, SignalConvergenceItem, ReturnStats, TickerFeatureDetail)
- [ ] A2: `features.py` Router + `GET /features/coverage`
- [ ] A3: `GET /features/convergence`
- [ ] A4: `GET /features/returns`
- [ ] A5: `GET /features/ticker/{symbol}`
- [ ] A6: Router in `main.py` registrieren + Syntax-Check

## Task B: Frontend API-Layer + Routing
- [ ] B1: TypeScript Interfaces + fetch-Funktionen in `api.ts`
- [ ] B2: Route `/features` in `App.tsx` + Sidebar in `Layout.tsx` (Brain-Icon)

## Task C: FeaturesPage – Pipeline Stats + Coverage
- [ ] C1: Page-Grundgerüst + Pipeline Stats Kacheln
- [ ] C2: Feature Coverage Heatmap Tabelle

## Task D: FeaturesPage – Convergence + Returns
- [ ] D1: Signal Convergence Sektion
- [ ] D2: Return Distribution Sektion

## Task E: FeaturesPage – Ticker Detail
- [ ] E1: Ticker Feature Detail (Click-Through Modal/Expand)

## Task F: Polish, Tests, Docs
- [ ] F1: Syntax-Check + Build-Prüfung (Frontend + Backend)
- [ ] F2: Unit Tests für neue Backend-Endpoints
- [ ] F3: Doku-Update (ROADMAP, SESSION_LOG)
- [ ] F4: Git Commits + Push

---

## Fortschritt Sprint 8 (abgeschlossen)

- [x] Milestone 1: FeaturePipeline Core
- [x] Milestone 2: Target Backfill
- [x] Milestone 3: Scheduler Integration
- [x] Milestone 4: API & Dashboard
- [x] Milestone 5: Tests (8/8 grün)
- [x] Milestone 6: Documentation
- [x] Git Commits + Push
- [x] Unraid Deploy + manueller Testlauf verifiziert
