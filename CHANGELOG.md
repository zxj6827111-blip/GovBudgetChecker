# Changelog

All notable changes to this project are documented in this file.

## [2026-03-02] Production Readiness Phase 1

### Added
- Durable job queue implementation: `api/job_queue.py`.
- Startup/shutdown queue lifecycle wiring in backend app.
- Production readiness/runbook documents:
  - `docs/PROD_READINESS_PLAN.md`
  - `docs/RELEASE_RUNBOOK.md`
  - `CLOUD_DEPLOY_EXECUTION_SHEET.md`
- CI workflow for lint/typecheck/unit/frontend build/e2e.

### Changed
- Upload path switched to streaming write with strict guards:
  - chunked write
  - size limit
  - PDF signature validation
  - partial-file cleanup on failure
- Auth is hardened for production:
  - API Key validation enforced by middleware (except exempt routes)
  - backend dev scripts default to auth enabled with local dev key
- Frontend proxy routes now return transparent upstream error codes (no forced 200 masking).
- Dependency source-of-truth unified through root `requirements.txt`.
- Readiness endpoint expanded to include:
  - upload directory checks
  - auth key presence check (when auth enabled)
  - DB reachability
  - AI extractor reachability
  - job queue startup status

### Fixed
- Prevented in-process-only task execution as default production path.
- Ensured upload/report data survives container recreation when persistent volume is mounted.

### Validation
- `ruff check .` passed
- `mypy api src tests` passed
- `python -m pytest` passed (58 passed)
- `npm --prefix app run build` passed
- `npm --prefix app run test:e2e` passed (2 passed)

## [2026-02-24] Rule Design and Coverage Refinement

### Added
- Budget rule design documentation and profile analysis artifacts.

### Changed
- Rule routing and validation coverage expanded for budget/final branches.

## [2026-02-23] Repo Hygiene and Layout Migration

### Changed
- Import path policy consolidated under `src.*`.
- Migration notes and repository hygiene docs updated.

## [2026-02-12] Security Hardening Baseline

### Changed
- Initial security hardening and risk cleanup delivered.

### Notes
- Historical reports from early February were removed/archived to avoid stale go-live conclusions.
- This changelog now tracks only actionable, code-verified milestones.
