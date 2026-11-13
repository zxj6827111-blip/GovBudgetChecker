#!/usr/bin/env python3
"""Isolated real-material diagnostic run.

- Starts a disposable PostgreSQL, API and worker from the fixed candidate worktree.
- Uploads the 10 manifest PDFs through the real /api/documents/upload entry.
- Forces mode=dual + use_local_rules=true + use_ai_assist=false.
- Hard-disables AI via env + settings copy + discarded extractor URL.
- Saves upload/status/review/report evidence per material and a run summary.

Does not touch production uploads, production DB, production jobs or any service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
MAIN = Path("/Volumes/workSSD/software development/GovBudgetChecker")
BUSINESS = Path("/Volumes/workSSD/software development/gbc-worktrees/real-painpoint-fixes-20261104")
EXPECTED_HEAD = "6f95cf59d15beb779580881a9f0f59bf9ad2b805"
MANIFEST = MAIN / "outputs/unified_closeout_round6_review_codex_20261104/diagnostic_manifest_10.json"
PREFLIGHT = RUN / "preflight.json"
ORG_SELECTION_PATH = RUN / "org_selection.json"
DB_ENV = RUN / "db_env"
UPLOADS = DB_ENV / "uploads"
ORG_DIR = DB_ENV / "org"
USERS = DB_ENV / "users"
TMP = DB_ENV / "tmp"
HOME = DB_ENV / "home"
AI_DIR = DB_ENV / "ai"
LOGS = RUN / "logs"
EVIDENCE = RUN / "evidence/current"
STATE_PATH = RUN / "run_state.json"
SUMMARY_PATH = RUN / "run_summary.json"

TERMINAL = {"review_required", "completed", "complete", "error", "failed", "cancelled", "canceled"}
ACTIVE = {"queued", "queued_waiting_worker", "processing", "running", "pending"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def docker(*args: str, timeout: int = 120) -> str:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if res.returncode:
        raise RuntimeError(f"docker {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"items": {}, "run_id": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def http_json(method: str, url: str, *, headers: dict, json_body: dict | None = None,
              form=None, files=None, timeout: float = 120.0):
    import httpx

    with httpx.Client(timeout=timeout) as client:
        resp = client.request(method, url, headers=headers, json=json_body, data=form, files=files)
        return resp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", default="", help="comma-separated orders, default all")
    parser.add_argument("--per-item-timeout", type=int, default=1800)
    parser.add_argument("--total-timeout", type=int, default=14400)
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--reuse-stack", action="store_true", help="reuse running container/API if recorded in run_state")
    args = parser.parse_args()

    preflight = json.loads(PREFLIGHT.read_text())
    org_selection = json.loads(ORG_SELECTION_PATH.read_text())
    org_by_order = {int(e["order"]): e for e in org_selection["items"]}
    manifest = json.loads(MANIFEST.read_text())
    items = manifest["items"]
    wanted = [int(x) for x in args.orders.split(",") if x.strip()] or [i["order"] for i in items]
    state = load_state()
    state.setdefault("items", {})
    run_id = state.get("run_id") or f"realpainpoint-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    state["run_id"] = run_id

    if not preflight.get("identity_ok"):
        raise SystemExit("identity mismatch in preflight; refusing to run")
    head = subprocess.run(["git", "-C", str(BUSINESS), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != EXPECTED_HEAD:
        raise SystemExit(f"business HEAD drift: {head} != {EXPECTED_HEAD}")

    LOGS.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for d in (UPLOADS, ORG_DIR, USERS, TMP, HOME, AI_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # --- isolated org data (read-only copy of production catalog/links) ---
    for name in ("organizations.json", "job_org_links.json"):
        src = MAIN / "data" / name
        dst = ORG_DIR / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    # --- template store copy (read-only) ---
    src_templates = MAIN / "uploads/.templates"
    dst_templates = UPLOADS / ".templates"
    if src_templates.is_dir() and not dst_templates.exists():
        shutil.copytree(src_templates, dst_templates)

    # --- isolated disabled AI settings copy ---
    ai_path = AI_DIR / "ai-model-settings.json"
    if not ai_path.exists():
        ai_path.write_text(json.dumps({
            "revision": 1, "enabled": False, "default_use_ai": False,
            "active_profile_id": None, "profiles": [],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }, ensure_ascii=False, indent=1))
    calendar = DB_ENV / "disclosure-calendar.json"
    if not calendar.exists():
        calendar.write_text("{}")

    # --- OCR preflight (must be healthy before the run) ---
    try:
        with urllib.request.urlopen("http://127.0.0.1:9010/health", timeout=10) as resp:
            ocr_health = json.loads(resp.read().decode())
        if not ocr_health.get("models_ready"):
            raise SystemExit("OCR service reachable but models_ready is not true")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"OCR preflight failed: {type(exc).__name__}: {exc}")

    # --- disposable PostgreSQL ---
    container = state.get("container") or f"gbc-realpainpointfix-{time.strftime('%m%d')}"
    pg_password = state.get("pg_password") or secrets.token_hex(24)
    pg_port = state.get("pg_port")
    dsn = state.get("dsn")
    api_key = state.get("api_key") or secrets.token_hex(24)
    session_secret = state.get("session_secret") or secrets.token_hex(24)
    admin_password = state.get("admin_password") or secrets.token_hex(18)
    state.update({"container": container, "pg_password": pg_password, "api_key": api_key,
                  "session_secret": session_secret, "admin_password": admin_password})

    existing = docker("ps", "-a", "--filter", f"name=^/{container}$", "--format", "{{.Names}}")
    if not existing:
        env_file = DB_ENV / "postgres.env"
        env_file.write_text(f"POSTGRES_PASSWORD={pg_password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n")
        env_file.chmod(0o600)
        pg_port = pg_port or free_port()
        docker("run", "-d", "--name", container, "--env-file", str(env_file),
               "-p", f"127.0.0.1:{pg_port}:5432", "postgres:16")
        dsn = f"postgresql://validator:{pg_password}@127.0.0.1:{pg_port}/validation"
        for _ in range(120):
            if subprocess.run(["docker", "exec", container, "pg_isready", "-U", "validator", "-d", "validation"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise SystemExit("isolated postgres did not become ready")
        state.update({"pg_port": pg_port, "dsn": dsn})
    else:
        if docker("inspect", "--format", "{{.State.Status}}", container) != "running":
            docker("start", container)
        pg_port = pg_port or int(docker("port", container, "5432/tcp").rsplit(":", 1)[1])
        dsn = dsn or f"postgresql://validator:{pg_password}@127.0.0.1:{pg_port}/validation"
        state.update({"pg_port": pg_port, "dsn": dsn})

    # --- migrations ---
    base_env = os.environ.copy()
    for key in list(base_env):
        if key.startswith(("AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_", "CODEX_API_", "MOONSHOT")):
            base_env.pop(key)
    common_env = {
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(BUSINESS),
        "PYTHONUNBUFFERED": "1",
        "TESTING": "false",
        "DATABASE_URL": dsn,
        "GOVBUDGET_AUTH_ENABLED": "true",
        "GOVBUDGET_API_KEY": api_key,
        "USER_SESSION_SECRET": session_secret,
        "DEFAULT_ADMIN_PASSWORD": admin_password,
        "REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE": "false",
        "ALLOW_INSECURE_DEFAULT_ADMIN": "1",
        "USER_DATA_DIR": str(USERS),
        "ORG_DATA_DIR": str(ORG_DIR),
        "UPLOAD_DIR": str(UPLOADS),
        "AUDIT_LOG_PATH": str(LOGS / "audit.jsonl"),
        "TMPDIR": str(TMP),
        "HOME": str(HOME),
        "AI_ASSIST_ENABLED": "false",
        "AI_ASSIST_REQUIRED": "false",
        "AI_ENABLED": "false",
        "AI_EXTRACTOR_DIRECT_FALLBACK": "false",
        "AI_EXTRACTOR_URL": "http://127.0.0.1:9/ai/extract/v1",
        "AI_MODEL_SETTINGS_PATH": str(ai_path),
        "OCR_ENABLED": "true",
        "OCR_SERVICE_URL": "http://127.0.0.1:9010",
        "OCR_MODEL_FINGERPRINT": str(ocr_health.get("model_fingerprint") or ""),
        "OCR_EVIDENCE_ATTESTATIONS_PATH": "",
        "OCR_EVIDENCE_ATTESTATIONS_SHA256": "",
        "OCR_IMAGE_GATE": "true",
        "JOB_QUEUE_ENABLED": "true",
        "JOB_QUEUE_INLINE_FALLBACK": "false",
        "JOB_QUEUE_WORKERS": "1",
        "JOB_QUEUE_POLL_INTERVAL_SECONDS": "0.5",
        "JOB_QUEUE_RESUME_ON_START": "true",
        "READY_REQUIRE_DATABASE": "true",
        "READY_REQUIRE_AI_EXTRACTOR": "false",
        "DISCLOSURE_CALENDAR_PATH": str(calendar),
        "GBC_TEMPLATE_STORAGE": str(UPLOADS / ".templates"),
        "JOB_LIST_CACHE_WARMUP": "false",
    }
    child_env = {**base_env, **common_env}

    if not state.get("migrations_done"):
        migrate = subprocess.run(
            [sys.executable, "-c",
             "import asyncio\n"
             "from src.db.connection import DatabaseConnection\n"
             "from src.db.migrations import run_migrations\n"
             "async def main():\n"
             "    await DatabaseConnection.initialize(__import__('os').environ['DATABASE_URL'])\n"
             "    await run_migrations()\n"
             "    await DatabaseConnection.close()\n"
             "asyncio.run(main())\n"],
            cwd=BUSINESS, env=child_env, capture_output=True, text=True, timeout=600,
        )
        (LOGS / "migrations.log").write_text(migrate.stdout + migrate.stderr)
        if migrate.returncode:
            raise SystemExit(f"migrations failed: {migrate.stderr[-2000:]}")
        state["migrations_done"] = True

    # --- API + worker ---
    procs = state.setdefault("procs", {})
    def proc_alive(pid):
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    if not proc_alive(procs.get("api_pid")):
        api_port = state.get("api_port") or free_port()
        state["api_port"] = api_port
        api_log = (LOGS / "api.log").open("ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=BUSINESS, env={**child_env, "JOB_QUEUE_ROLE": "api"},
            stdout=api_log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        procs["api_pid"] = proc.pid
        api_log.close()
    if not proc_alive(procs.get("worker_pid")):
        worker_log = (LOGS / "worker.log").open("ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "api.worker"],
            cwd=BUSINESS, env={**child_env, "JOB_QUEUE_ROLE": "worker"},
            stdout=worker_log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        procs["worker_pid"] = proc.pid
        worker_log.close()
    save_state(state)

    base = f"http://127.0.0.1:{state['api_port']}"
    headers = {"X-API-Key": api_key}
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=3) as resp:
                if resp.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    else:
        raise SystemExit("API did not become healthy; see logs/api.log")

    login = http_json("POST", f"{base}/api/auth/login", headers=headers,
                      json_body={"username": "admin", "password": admin_password})
    if login.status_code != 200:
        raise SystemExit(f"login failed: {login.status_code} {login.text[:500]}")
    token = login.json()["token"]
    auth = {**headers, "X-Session-Token": token}

    capabilities = http_json("GET", f"{base}/api/health", headers=auth)
    (RUN / "service_health.json").write_text(capabilities.text)
    identity = {
        "run_id": run_id,
        "business_head": head,
        "business_cwd": str(BUSINESS),
        "api_module": str(BUSINESS / "api/main.py"),
        "worker_module": str(BUSINESS / "api/worker.py"),
        "python_executable": sys.executable,
        "api_port": state["api_port"],
        "pg": {"container": container, "port": state["pg_port"], "isolation": "disposable-docker"},
        "ocr_health": ocr_health,
        "request_upload_params": {
            "mode": "dual", "use_local_rules": "true", "use_ai_assist": "false",
            "doc_type": "dept_final", "enqueue": "true",
            "org_unit_id": "explicit operator selection from org_selection.json",
        },
        "org_selection": {"path": str(ORG_SELECTION_PATH), "sha256": sha(ORG_SELECTION_PATH),
                          "kind": org_selection.get("kind"), "note": org_selection.get("note")},
        "env_redacted": {k: ("<redacted>" if "KEY" in k or "PASSWORD" in k or "SECRET" in k else v)
                         for k, v in common_env.items()},
        "env_keys": sorted(common_env),
        "process_cwd": str(BUSINESS),
        "migrations_done": True,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (RUN / "RUN_IDENTITY.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2))

    started = time.monotonic()
    pilot_orders = {1, 2}
    for order in wanted:
        item = next(i for i in items if i["order"] == order)
        job_state = state["items"].get(str(order)) or {}
        if job_state.get("terminal"):
            print(f"[{order}] already terminal, skip")
            continue
        if time.monotonic() - started > args.total_timeout:
            print("total timeout reached; stopping")
            break
        if not job_state.get("job_id"):
            pdf = Path(item["pdf_path"])
            selection = org_by_order[order]
            t0 = time.monotonic()
            with pdf.open("rb") as fh:
                resp = http_json(
                    "POST", f"{base}/api/documents/upload", headers=auth,
                    form={
                        "fiscal_year": str(item["year"]),
                        "doc_type": "dept_final",
                        "org_unit_id": selection["org_id"],
                        "enqueue": "true",
                        "mode": "dual",
                        "use_local_rules": "true",
                        "use_ai_assist": "false",
                    },
                    files={"file": (item["filename"], fh, "application/pdf")},
                    timeout=300,
                )
            upload_text = resp.text
            if resp.status_code != 200:
                out_dir = EVIDENCE / f"{order:02d}_{item['job_id'][:8]}"
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "upload_response.json").write_text(upload_text)
                job_state.update({"order": order, "upload_status": resp.status_code,
                                  "upload_error": resp.text[:2000], "terminal": True,
                                  "phase": "upload_failed"})
                state["items"][str(order)] = job_state
                save_state(state)
                print(f"[{order}] upload failed {resp.status_code}: {resp.text[:200]}")
                continue
            body = resp.json()
            job_id = body.get("job_id") or body.get("id")
            if not job_id:
                raise SystemExit(f"upload response has no job id: {resp.text[:500]}")
            out_dir = EVIDENCE / f"{order:02d}_{job_id[:8]}"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "upload_response.json").write_text(upload_text)
            job_state.update({"order": order, "job_id": job_id, "upload_status": 200,
                              "filename": item["filename"], "year": item["year"],
                              "org_selection": selection,
                              "upload_seconds": round(time.monotonic() - t0, 2),
                              "upload_body_keys": sorted(body.keys()),
                              "material_registration": body.get("material_registration")})
            state["items"][str(order)] = job_state
            save_state(state)
            print(f"[{order}] uploaded -> {job_id}")

        job_id = job_state["job_id"]
        out_dir = EVIDENCE / f"{order:02d}_{job_id[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        timeline = (out_dir / "timeline.jsonl").open("a")
        t0 = time.monotonic()
        deadline = t0 + args.per_item_timeout
        last_key = None
        final = None
        while time.monotonic() < deadline:
            st = http_json("GET", f"{base}/api/jobs/{job_id}/status", headers=auth, timeout=120)
            if st.status_code != 200:
                timeline.write(json.dumps({"ts": time.time(), "http": st.status_code, "body": st.text[:300]}, ensure_ascii=False) + "\n")
                time.sleep(5)
                continue
            payload = st.json()
            stage = payload.get("stage")
            status = payload.get("status")
            progress = payload.get("progress")
            key = (status, stage, progress)
            if key != last_key:
                timeline.write(json.dumps({"ts": round(time.time(), 3), "elapsed": round(time.monotonic() - t0, 1),
                                           "status": status, "stage": stage, "progress": progress,
                                           "stage_progress": payload.get("stage_progress"),
                                           "ocr_progress": payload.get("ocr_progress")}, ensure_ascii=False) + "\n")
                timeline.flush()
                last_key = key
            if status in TERMINAL:
                final = payload
                break
            time.sleep(5)
        timeline.close()
        if final is None:
            cancel = http_json("POST", f"{base}/api/jobs/{job_id}/cancel", headers=auth, timeout=30)
            job_state.update({"terminal": True, "phase": "timeout", "elapsed_seconds": round(time.monotonic() - t0, 1),
                              "cancel_status": cancel.status_code})
            state["items"][str(order)] = job_state
            save_state(state)
            print(f"[{order}] TIMEOUT after {args.per_item_timeout}s; cancel={cancel.status_code}")
            continue

        elapsed = round(time.monotonic() - t0, 1)
        (out_dir / "status_final.json").write_text(json.dumps(final, ensure_ascii=False, indent=1))
        review = http_json("GET", f"{base}/api/jobs/{job_id}/review", headers=auth, timeout=180)
        (out_dir / "review.json").write_text(review.text)
        import httpx
        with httpx.Client(timeout=180) as client:
            rj = client.get(f"{base}/api/reports/download", params={"job_id": job_id, "format": "json"}, headers=auth)
            (out_dir / "report.json").write_text(rj.text)
            rc = client.get(f"{base}/api/reports/download", params={"job_id": job_id, "format": "csv"}, headers=auth)
            (out_dir / "report.csv").write_bytes(rc.content)
            ing = client.get(f"{base}/api/jobs/{job_id}/structured-ingest", headers=auth)
            (out_dir / "structured_ingest.json").write_text(ing.text)
        cov = final.get("check_coverage") or {}
        job_state.update({
            "terminal": True,
            "phase": "done",
            "elapsed_seconds": elapsed,
            "status": final.get("status"),
            "quality_status": final.get("quality_status"),
            "analysis_conclusion": final.get("analysis_conclusion"),
            "analysis_revision": final.get("analysis_revision"),
            "mode": final.get("mode"),
            "use_ai_assist": final.get("use_ai_assist"),
            "dual_mode_enabled": final.get("dual_mode_enabled"),
            "doc_type": final.get("doc_type"),
            "report_year": final.get("report_year"),
            "organization_name": final.get("organization_name"),
            "page_coverage": final.get("page_coverage"),
            "scanned_page_count": final.get("scanned_page_count"),
            "review_reasons": final.get("review_reasons"),
            "check_coverage_summary": {k: v for k, v in cov.items() if k != "instances"},
            "check_instance_count": len(cov.get("instances") or []),
            "ocr_progress": final.get("ocr_progress"),
            "pipeline_budget": final.get("pipeline_budget"),
            "structured_ingest": final.get("structured_ingest"),
            "review_report_available": review.status_code == 200,
            "report_json_status": rj.status_code,
            "report_csv_status": rc.status_code,
            "ai_execution": (((final.get("result") or {}).get("meta") or {}).get("ai_execution")
                             or final.get("ai_execution")),
        })
        state["items"][str(order)] = job_state
        save_state(state)
        print(f"[{order}] {final.get('status')} in {elapsed}s "
              f"applicable={cov.get('applicable_total')} completed={cov.get('completed_total')} "
              f"unresolved={cov.get('unresolved_total')} ocr={(final.get('ocr_progress') or {}).get('requested_pages')}")
        if order == 2 and not state.get("pilot_gate"):
            gate = pilot_gate(state)
            state["pilot_gate"] = gate
            save_state(state)
            (RUN / "PILOT_GATE.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2))
            if not gate["passed"]:
                print(f"PILOT GATE FAILED: {gate['reasons']}")
                break

    # --- AI egress audit over logs ---
    patterns = ["api.openai.com", "generativelanguage", "dashscope", "bigmodel.cn", "moonshot",
                "deepseek.com", "ai/extract", "127.0.0.1:9009", "ai_client", "AIClient"]
    hits = {}
    for log in LOGS.glob("*.log"):
        text = log.read_text(errors="ignore")
        for pat in patterns:
            n = text.count(pat)
            if n:
                hits.setdefault(log.name, {})[pat] = n
    state["ai_egress_log_scan"] = hits
    state["ai_model_calls_observed"] = 0 if not hits else None
    save_state(state)

    # --- DB dump ---
    if not state.get("db_dump"):
        dump = subprocess.run(["docker", "exec", container, "pg_dump", "-U", "validator", "validation"],
                              capture_output=True, timeout=600)
        if dump.returncode == 0:
            (EVIDENCE / "db_dump.sql").write_bytes(dump.stdout)
            state["db_dump"] = str(EVIDENCE / "db_dump.sql")
            state["db_dump_sha256"] = hashlib.sha256(dump.stdout).hexdigest()
        else:
            (LOGS / "pg_dump.err").write_bytes(dump.stderr)
        save_state(state)

    (RUN / "run_summary.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps({"run_id": run_id, "items": {
        k: {kk: v.get(kk) for kk in ("phase", "status", "elapsed_seconds", "check_coverage_summary")}
        for k, v in state["items"].items()}}, ensure_ascii=False, indent=1))
    if not args.keep_container:
        subprocess.run(["docker", "stop", container], capture_output=True, timeout=120)
    return 0


def pilot_gate(state: dict) -> dict:
    reasons = []
    items = state.get("items") or {}
    for order in ("1", "2"):
        entry = items.get(order) or {}
        if not entry.get("terminal") or entry.get("phase") != "done":
            reasons.append(f"order {order} not terminal/done: {entry.get('phase')}")
            continue
        cov = entry.get("check_coverage_summary") or {}
        if not cov.get("applicable_total"):
            reasons.append(f"order {order} has no applicable_total")
        if not entry.get("check_instance_count"):
            reasons.append(f"order {order} has no check instances")
        if not entry.get("review_report_available"):
            reasons.append(f"order {order} review report not available")
        if entry.get("report_json_status") != 200 or entry.get("report_csv_status") != 200:
            reasons.append(f"order {order} export not 200")
        if entry.get("use_ai_assist") is not False:
            reasons.append(f"order {order} use_ai_assist is not false")
        if entry.get("mode") != "dual":
            reasons.append(f"order {order} mode is not dual: {entry.get('mode')}")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "orders": ["1", "2"],
        "evidence": [f"evidence/current/01_{(items.get('1') or {}).get('job_id','')[:8]}",
                     f"evidence/current/02_{(items.get('2') or {}).get('job_id','')[:8]}"],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
