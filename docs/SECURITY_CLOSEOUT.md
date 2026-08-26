# GovBudgetChecker Security Closeout

This checklist records the remaining operator-owned controls after the P0/P1
security hardening work. Treat it as a release gate before exposing the system to
non-local users.

## 1. Required Production Secrets

Set these values in the deployment secret store. Do not commit real values to
Git.

```env
GOVBUDGET_AUTH_ENABLED=true
GOVBUDGET_API_KEY=<random-32-byte-or-longer-secret>
USER_SESSION_SECRET=<different-random-32-byte-or-longer-secret>
DEFAULT_ADMIN_PASSWORD=<unique-bootstrap-password>
POSTGRES_USER=<deployment-specific-db-user>
POSTGRES_PASSWORD=<random-strong-db-password>
POSTGRES_DB=fiscal_db
DATABASE_URL=postgres://<user>:<password>@<host>:5432/<db>
```

Rules:

- `GOVBUDGET_API_KEY` and `USER_SESSION_SECRET` must be different values.
- `DEFAULT_ADMIN_PASSWORD` is now enforced as a bootstrap-only password: the
  seeded administrator is created with `must_change_password=true`, and every
  endpoint except `/api/auth/login`, `/api/auth/me`, `/api/auth/logout` and
  `/api/auth/change-password` returns `403` until the password is changed
  (`REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE`, default on outside tests).
  Accounts that already exist in `users.json` are not affected.
- `.env`, `.env.local`, `app/.env.local`, database dumps, and runtime logs must
  stay untracked.

## 2. Readiness Endpoint Exposure

Default behavior:

- `/ready` returns a redacted readiness payload.
- `/ready?details=true` requires an administrator session.

Production rule:

- Do not set `READY_EXPOSE_DETAILS=true` on public deployments.
- If it is needed for diagnostics, enable it only inside a trusted private
  network and turn it off after the incident window.

The detailed readiness payload may include internal paths, dependency names,
database reachability, and AI extractor status.

## 3. Reverse Proxy Requirements

The public reverse proxy must enforce:

- HTTPS only.
- A fixed allowlist of `Host` values for the production domain.
- Request body limit at or above `MAX_UPLOAD_MB`, with no unlimited upload path.
- Upload and analysis start timeouts long enough for large PDF uploads.
- Forwarded headers configured intentionally, not passed through blindly.

Recommended headers:

- `Strict-Transport-Security` for HTTPS deployments.
- `X-Forwarded-Proto` set by the proxy, not by clients.
- `X-Forwarded-Host` only if the application stack requires it.

Application-emitted security headers (do not strip them at the proxy):

- Backend (`src/security.SecurityHeadersMiddleware`): `Content-Security-Policy`,
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and
  `Strict-Transport-Security` (only when the request is HTTPS or
  `X-Forwarded-Proto: https` is present; `SECURITY_HSTS_ALWAYS=true` forces it).
- Frontend (`app/middleware.ts`): `Content-Security-Policy`,
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, and `Strict-Transport-Security` in production builds.

If the proxy also sets these headers, keep the values consistent; the
application only fills in headers that are not already present on the response.

## 4. Docker Compose Credential Rule

`docker-compose.yml` intentionally requires `POSTGRES_USER` and
`POSTGRES_PASSWORD` from `.env` or the shell environment. Local startup should
fail fast if they are missing.

For local development:

1. Copy `.env.example` to `.env`.
2. Replace every `change_me_*` value before sharing the environment.
3. Run `docker compose up -d`.

For production:

- Prefer managed PostgreSQL or a deployment secret store.
- Do not reuse the sample `.env.example` password.

## 5. Final Validation Gate

Before release, run these checks from a clean checkout:

```powershell
ruff check api src tests
mypy api src tests
pytest
npm --prefix app run lint
npm --prefix app run build
npm --prefix app run test:ui-adapters
git diff --check
```

Then run dependency audit in an environment with registry access:

```powershell
python -m pip install pip-audit
python -m pip_audit
npm --prefix app audit --audit-level=moderate
npm audit --audit-level=moderate
```

If registry access is blocked, record it as an unresolved release gate rather
than treating it as a pass.

## 6. Manual Smoke Test

Run the application with production-like secrets and verify:

- Login succeeds for a valid user.
- Five failed login attempts trigger lockout.
- PDF upload returns a `job_id`.
- Analysis transitions through `queued`, `processing`, and `done` or a clear
  `error`.
- Report download works for an authorized user.
- A normal user cannot read jobs outside self-owned or assigned organization
  scope.
- An administrator can manage users and organization scopes.
- `/ready` is redacted by default.
- `/ready?details=true` is rejected without an administrator session.

## 7. Residual Follow-Up Items

These are not blockers for the current code hardening closeout, but they remain
useful production improvements:

- Add a dead-letter queue for permanently failed jobs.
- Queue capacity and backpressure metrics are now available via `/metrics`
  (see `docs/OBSERVABILITY_AND_ALERTS.md`); wiring them into the monitoring
  stack and alert rules is still operator-owned.
- Add audit entries for sensitive read operations such as report download.
- Add a staging rollback drill record for every production release.
- Force a password change when an administrator resets another user's password
  (the current control only covers the bootstrap administrator's first login).
