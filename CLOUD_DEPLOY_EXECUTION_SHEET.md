# GovBudgetChecker Cloud Deploy Execution Sheet

## Purpose
Use this as the step-by-step command sheet for production rollout, canary validation, and rollback.

## Scope
- Environment: cloud production server
- Mode: Docker Compose rollout (`docker-compose.ai.yml`)
- Date prepared: 2026-03-02

## 0. Variables (replace before running)
Set these values in your terminal first:

```bash
export RELEASE_TAG="vX.Y.Z"
export APP_DIR="/opt/GovBudgetChecker"
export PROD_API_BASE="https://<your-api-domain>"
export PROD_WEB_BASE="https://<your-web-domain>"
export GOVBUDGET_API_KEY="<your-production-api-key>"
export TEST_PDF_PATH="/tmp/smoke.pdf"
```

## 1. T-60 min: local quality gates
Run from repository root:

```bash
git fetch --all --tags
git checkout feat/prod-readiness-phase1
ruff check .
mypy api src tests
python -m pytest
npm --prefix app run build
npm --prefix app run test:e2e
```

Pass criteria:
- All commands exit with code `0`
- No failing tests

## 2. T-45 min: production env precheck
On production server:

```bash
cd "$APP_DIR"
git fetch --all --tags
git checkout "$RELEASE_TAG"
git status --short
```

Check `.env` contains:
- `GOVBUDGET_AUTH_ENABLED=true`
- `GOVBUDGET_API_KEY=<non-empty>`
- `UPLOAD_DIR=/app/uploads`
- `DATABASE_URL=<reachable>`
- `AI_EXTRACTOR_URL=<reachable>`

Check persistent mount path exists:

```bash
mkdir -p "$APP_DIR/uploads" "$APP_DIR/data" "$APP_DIR/logs"
```

## 3. T-30 min: config rendering check
Validate compose config before deploy:

```bash
docker compose -f docker-compose.ai.yml --env-file .env config > /tmp/gbc.compose.rendered.yaml
```

Pass criteria:
- No render error
- `UPLOAD_DIR` present for backend
- `./uploads:/app/uploads` volume present

## 4. T-20 min: baseline snapshot (for rollback confidence)

```bash
docker compose -f docker-compose.ai.yml ps
docker compose -f docker-compose.ai.yml images
curl -sS "$PROD_API_BASE/health"
curl -sS "$PROD_API_BASE/ready"
```

Record:
- Current running image tags
- Current `/ready` output

## 5. T-15 min: backend rollout first
Roll backend-related services first:

```bash
docker compose -f docker-compose.ai.yml --env-file .env pull
docker compose -f docker-compose.ai.yml --env-file .env up -d ai-extractor backend
docker compose -f docker-compose.ai.yml logs --tail=100 backend
```

Pass criteria:
- Backend container is `healthy`
- No startup crash loops
- Job queue startup logs visible

## 6. T-10 min: backend readiness and auth verification

```bash
curl -sS "$PROD_API_BASE/health"
curl -sS "$PROD_API_BASE/ready"
curl -sS -o /dev/null -w "%{http_code}\n" "$PROD_API_BASE/api/jobs"
curl -sS -o /dev/null -w "%{http_code}\n" -H "X-API-Key: $GOVBUDGET_API_KEY" "$PROD_API_BASE/api/jobs"
```

Pass criteria:
- `/health` -> `status=ok`
- `/ready` -> `status=ready`
- without key -> `401` or `403`
- with key -> `200`

## 7. T-8 min: frontend rollout

```bash
docker compose -f docker-compose.ai.yml --env-file .env up -d frontend
docker compose -f docker-compose.ai.yml logs --tail=100 frontend
```

Pass criteria:
- Frontend container running
- Homepage opens from `PROD_WEB_BASE`

## 8. T-5 min: canary smoke test (real business flow)
1) Upload:

```bash
curl -sS -X POST "$PROD_API_BASE/upload" \
  -H "X-API-Key: $GOVBUDGET_API_KEY" \
  -F "file=@$TEST_PDF_PATH;type=application/pdf"
```

Save returned `job_id` as `JOB_ID`.

2) Start analysis:

```bash
curl -sS -X POST "$PROD_API_BASE/api/analyze/$JOB_ID" \
  -H "X-API-Key: $GOVBUDGET_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode":"dual","use_local_rules":true,"use_ai_assist":true}'
```

3) Poll until `done`:

```bash
watch -n 2 "curl -sS -H 'X-API-Key: $GOVBUDGET_API_KEY' '$PROD_API_BASE/api/jobs/$JOB_ID/status'"
```

4) Export checks:

```bash
curl -sS -H "X-API-Key: $GOVBUDGET_API_KEY" "$PROD_API_BASE/api/reports/download?job_id=$JOB_ID&format=json" | head
curl -sS -H "X-API-Key: $GOVBUDGET_API_KEY" "$PROD_API_BASE/api/reports/download?job_id=$JOB_ID&format=csv" | head
curl -sS -H "X-API-Key: $GOVBUDGET_API_KEY" -o "/tmp/$JOB_ID.pdf" "$PROD_API_BASE/api/reports/download?job_id=$JOB_ID&format=pdf"
```

Pass criteria:
- status path `queued -> processing -> done`
- JSON/CSV/PDF downloads all succeed

## 9. T-0 min: full traffic switch
After canary success:
- Shift all traffic to new frontend/backend stack
- Keep previous tag available for fast rollback
- Continue watching logs for 15 to 30 minutes

Suggested checks during observation:

```bash
docker compose -f docker-compose.ai.yml logs --since=15m backend | tail -n 200
docker compose -f docker-compose.ai.yml logs --since=15m frontend | tail -n 200
```

## 10. Rollback commands (if any P0 fails)
Trigger rollback when:
- `/ready` is not `ready`
- auth unexpectedly open
- upload/analyze/export chain fails

Rollback sequence:

```bash
cd "$APP_DIR"
git checkout <previous_good_tag>
docker compose -f docker-compose.ai.yml --env-file .env up -d
docker compose -f docker-compose.ai.yml ps
curl -sS "$PROD_API_BASE/health"
curl -sS "$PROD_API_BASE/ready"
```

Important:
- Do not delete `uploads` or DB data during rollback.
- Re-run section 8 smoke test on previous good tag.

## 11. Release record template
Fill after deployment:

```text
Release tag:
Operator:
Start time:
End time:
Canary result:
Full rollout result:
Rollback needed: yes/no
Issue summary:
Follow-up owner:
```

