# GovBudgetChecker Release Runbook

## 1. Pre-Release Checklist (Staging)
- Confirm branch: `feat/prod-readiness-phase1` merged into release branch.
- Confirm env vars:
  - `GOVBUDGET_AUTH_ENABLED=true`
  - `GOVBUDGET_API_KEY` is non-empty
  - `USER_SESSION_SECRET` is fixed and non-empty
  - `UPLOAD_DIR` points to persistent volume path
  - `DATABASE_URL` reachable from runtime
  - `AI_EXTRACTOR_URL` reachable from runtime
- Run gates:
  - `ruff check .`
  - `mypy api src tests`
  - `python -m pytest`
  - `npm --prefix app run build`
  - `npm --prefix app run test:e2e`
- Verify `/api/ready` is `ready` in staging.

## 2. Deployment Sequence
1. Build backend and frontend images from the release tag.
2. Apply database migrations (if any) before traffic cutover.
3. Deploy backend with persistent `uploads` mount.
4. Deploy frontend and route traffic to new backend.
5. Wait for workers to start and queue resume log to appear.
6. Open traffic (canary first, then full rollout).

## 3. Post-Deploy Validation
- Health endpoints:
  - `GET /health` returns `ok`
  - `GET /ready` returns `ready`
- Functional checks:
  - Upload a PDF and receive `job_id`
  - Start analysis and observe status transition: `queued -> processing -> done`
  - Download JSON/CSV/PDF report
- Security checks:
  - Request without API key returns 401/403
  - Request with valid key succeeds

## 4. Rollback Plan
1. Stop routing traffic to new version.
2. Roll back frontend to previous image tag.
3. Roll back backend to previous image tag.
4. Keep persistent `uploads` and DB intact (no destructive cleanup).
5. Re-run post-deploy validation on previous version.

## 5. Failure Playbook
- Symptom: `/ready` reports `db_reachable=false`
  - Action: verify DB network ACL and `DATABASE_URL`.
- Symptom: `/ready` reports `ai_extractor_reachable=false`
  - Action: verify AI service process and network route.
- Symptom: tasks stuck in `queued`
  - Action: verify job queue startup log and worker count (`JOB_QUEUE_WORKERS`).
- Symptom: upload fails 413 for normal files
  - Action: confirm `MAX_UPLOAD_MB` and upstream reverse-proxy body limit.

## 6. Drill Record Template
- Date:
- Environment:
- Release tag:
- Drill type: (deploy / rollback / failover)
- Result: (pass / fail)
- Issues found:
- Follow-up owner:
