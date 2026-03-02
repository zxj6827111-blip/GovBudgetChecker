# Production Readiness Plan (2026-03-02)

## Branch
- `feat/prod-readiness-phase1`

## Scope
- Goal: move from local/dev operation to cloud production readiness.
- Rule: execute items in order and block release on all `P0` items.

## Ordered Work Items
1. Baseline freeze and release gates
2. Durable job queue lifecycle
3. Persistent storage for uploads/reports
4. Production auth hardening
5. Streaming upload path with strict limits
6. Frontend proxy error transparency
7. Dependency source-of-truth unification
8. Readiness checks for critical dependencies
9. CI/E2E quality gates
10. Release runbook and rollback drill

## P0 Release Gates
- No in-process-only task execution path for production traffic.
- Upload/report data survives process restart and container recreation.
- Authentication is enabled by default in production deployment.
- Upload endpoint enforces size/type/signature while streaming to disk.
- Frontend proxy does not mask backend failures as HTTP 200.

## Initial Baseline Snapshot
- Backend tests: pass
- Frontend build: pass (with warnings)
- E2E: pass (contains placeholder smoke test that must be replaced)
- Known risk: job execution currently starts through `asyncio.create_task`.

## Change Tracking
- All changes in this plan should be committed on this branch with
  conventional commits and linked to the item number above.
