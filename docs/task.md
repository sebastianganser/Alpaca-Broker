# Sprint 8 – Feature Pipeline Tasks

## Milestone 1: FeaturePipeline Core
- [x] `derived/feature_pipeline.py` – Klasse + compute_daily()
- [x] ARK Features (point-in-time + temporal)
- [x] Insider Features (point-in-time + temporal)
- [x] Analyst Features (point-in-time + temporal)
- [x] Politician Features (dual-date)
- [x] 13F Features
- [x] Fundamentals Features (point-in-time + temporal)
- [x] Technical Features
- [x] Earnings Features
- [x] UPSERT-Logik + Commit

## Milestone 2: Target Backfill
- [x] `derived/target_backfill.py` – TargetBackfillComputer
- [x] Forward-Return-Berechnung (1d, 5d, 20d, 60d)
- [x] Batch-Update-Optimierung

## Milestone 3: Scheduler Integration
- [x] `scheduler/jobs.py` – run_feature_pipeline() + run_target_backfill()
- [x] `main.py` – Jobs registrieren (02:00 + 02:15 CET)
- [ ] Git Commit: "feat: add feature pipeline + target backfill scheduler jobs"

## Milestone 4: API & Dashboard
- [x] `api/schemas.py` – FeatureStats Schema
- [x] `api/routes/dashboard.py` – GET /dashboard/feature-stats
- [ ] Git Commit: "feat: add feature stats API endpoint"

## Milestone 5: Tests
- [x] `tests/test_feature_pipeline.py` – 8 Tests, alle grün ✅
- [ ] Git Commit: "test: add feature pipeline tests"

## Milestone 6: Documentation & Finalization
- [x] `CLAUDE.md` – Current Status aktualisiert (Sprint 8 Done, 12 Jobs)
- [x] `docs/ROADMAP.md` – Sprint 8 als Done, Tasks abgehakt
- [x] `docs/SESSION_LOG.md` – Session 19 dokumentiert
- [x] `docs/SCHEDULER.md` – Bereits aktuell (in vorheriger Session aktualisiert)
- [ ] Git Commits ausstehend
