# GovBudgetChecker Release Runbook

## 1. Pre-Release Checklist (Staging)
- Confirm branch: `feat/prod-readiness-phase1` merged into release branch.
- Confirm env vars:
  - `GOVBUDGET_AUTH_ENABLED=true`
  - `GOVBUDGET_API_KEY` is non-empty
  - `USER_SESSION_SECRET` is fixed and non-empty
  - `GOVBUDGET_API_KEY` and `USER_SESSION_SECRET` are different random secrets
  - `READY_EXPOSE_DETAILS` is unset unless the service is private-only
  - `POSTGRES_USER` and `POSTGRES_PASSWORD` are supplied by deployment secrets
  - `UPLOAD_DIR` points to persistent volume path
  - `DATABASE_URL` reachable from runtime
  - `AI_EXTRACTOR_URL` reachable from runtime
- Validate the deployment topology before rollout:
  - `docker compose --env-file .env -f docker-compose.yml -f docker-compose.ai.yml config --quiet`
  - `docker compose -f docker-compose.yml -f docker-compose.ai.yml config --services` must list
    exactly `postgres`, `ai-extractor`, `backend`, `worker`, `frontend`
  - `backend` runs with `JOB_QUEUE_ROLE=api` and `worker` with `JOB_QUEUE_ROLE=worker`;
    both mount the **same** `UPLOAD_DIR` volume (the queue claim lock is a file lock in that
    directory, so a split mount causes duplicate consumption)
  - the backend has `READY_REQUIRE_DATABASE=true` and `DATABASE_URL` points to the internal `postgres` service
  - `/app/data`, `/app/logs`, and `/app/uploads` are mapped to persistent host paths
- Run gates:
  - `ruff check .`
  - `python scripts/check_log_message_safety.py`（日志 message 不含材料原文/凭据）
  - `python scripts/check_env_consistency.py`（代码 / `.env.example` / compose 三方对账）
  - `mypy api src tests`
  - `python -m pytest`
  - `npm --prefix app run build`
  - `npm --prefix app run test:e2e`
  - dependency audit for Python and npm packages
  - `git diff --check`
- Verify `/api/ready` is `ready` in staging.
  Confirm `/api/ready` is redacted by default and detailed readiness requires an
  administrator session.

## 2. Deployment Sequence
1. Build backend and frontend images from the release tag with `docker compose -f docker-compose.yml -f docker-compose.ai.yml up -d --build`.
2. Apply database migrations (if any) before traffic cutover.
3. Deploy backend with persistent `data`, `logs`, and `uploads` mounts. The backend must wait for PostgreSQL health before startup.
4. Deploy frontend and route traffic to new backend.
5. Wait for workers to start and queue resume log to appear.
   The `worker` service shares the backend image and runs `python -m api.worker`;
   confirm with `docker compose logs worker | grep "GovBudget worker started"`.
   With `JOB_QUEUE_ROLE=api` on the backend, **no analysis runs until the worker is up** —
   jobs stay in `queued`, which is the expected (not failed) state.
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
  - Five failed login attempts trigger lockout
  - A normal user cannot access another user's unassigned job
  - `/ready?details=true` is rejected without an administrator session

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
