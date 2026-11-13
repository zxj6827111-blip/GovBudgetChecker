#!/usr/bin/env python3
"""Run real localhost API + separate Worker + disposable PostgreSQL downloads.

No production .env, data or database is used. Synthetic inputs are identified as
such; this run cannot establish independent blind-test accuracy or OCR trust.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
sys.path.insert(0,str(ROOT))
import shutil
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def amount_decimal(value):
    return Decimal(str(value).strip().replace(",", "").replace("，", "").replace(" ", ""))


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"Docker operation {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@contextmanager
def service_lifetime(processes, handles):
    """Quiesce every writer before the surrounding temporary directory exits."""
    try:
        yield
    finally:
        errors = []
        for process in reversed(processes):
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            except Exception as exc:
                errors.append(f"process cleanup: {type(exc).__name__}: {exc}")
        for handle in handles:
            try:
                handle.close()
            except Exception as exc:
                errors.append(f"handle cleanup: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))


async def prepare_db(dsn):
    from src.db.connection import DatabaseConnection
    from src.db.migrations import run_migrations

    await DatabaseConnection.initialize(dsn)
    await run_migrations()
    await DatabaseConnection.close()


def structured_ingest_from_payload(payload):
    return ((payload.get("result") or {}).get("meta") or {}).get("structured_ingest") or payload.get("structured_ingest") or {}


def structured_ingest_validation(payload, database):
    """Prove this result's ingest, not that previous jobs left rows in the DB."""
    ingest = structured_ingest_from_payload(payload)
    run = database.get("parse_run") or {}
    job = database.get("job") or {}
    storage = ingest.get("unified_storage") or {}
    passed = bool(
        ingest.get("status") == "done"
        and storage.get("status") == "completed"
        and all(storage.get(key) == "completed" for key in ("artifact_status", "facts_status", "evidence_status"))
        and storage.get("parse_run_id") == ingest.get("parse_run_id")
        and storage.get("document_version_id") == ingest.get("document_version_id")
        and ingest.get("job_id") == payload.get("job_id")
        and run.get("id") == ingest.get("parse_run_id")
        and run.get("document_version_id") == ingest.get("document_version_id")
        and run.get("status") == "completed"
        and run.get("source_file_hash") == job.get("file_hash")
        and run.get("version_file_hash") == job.get("file_hash")
        and database.get("result_revision") == payload.get("analysis_revision")
        and job.get('current_fact_revision_id') == payload.get('analysis_revision')
        and database.get('snapshot_mirror_pending') is False
        and database.get('effective_compatibility_projection_verified') is True
        and database.get("result_ingest_status") == "done"
        and database.get("run_facts_count", 0) >= ingest.get("unified_facts_count", 0)
        and ingest.get("unified_facts_count") == storage.get("expected_facts_count")
        and (not database.get("run_facts_count") or database.get("run_evidence_count"))
        and (database.get("artifact_readback") is None or database["artifact_readback"]["passed"])
        and not ingest.get("errors")
    )
    return {"passed": passed, "status": ingest.get("status"), "parse_run_id": ingest.get("parse_run_id"), "document_version_id": ingest.get("document_version_id"), "parse_cache": ingest.get("parse_cache"), "unified_storage": storage}


async def database_receipt(dsn, job_id, ingest=None, target_fact=None):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        job = await conn.fetchrow(
            "SELECT job_uuid,status,analysis_revision,file_hash,current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1",
            job_id,
        )
        result_count = await conn.fetchval(
            "SELECT count(*) FROM analysis_results r JOIN analysis_jobs j ON j.id=r.job_id WHERE j.job_uuid=$1",
            job_id,
        )
        result_binding = await conn.fetchrow(
            """SELECT r.raw_response->>'analysis_revision' AS result_revision,
                      COALESCE(r.raw_response#>>'{result,meta,structured_ingest,status}',
                               r.raw_response#>>'{structured_ingest,status}') AS result_ingest_status
               FROM analysis_results r JOIN analysis_jobs j ON j.id=r.job_id WHERE j.job_uuid=$1""",
            job_id,
        )
        counts = {
            table: await conn.fetchval(f"SELECT count(*) FROM {table}")
            for table in ("parse_runs", "parse_facts", "parse_evidence", "schema_migrations")
        }
        run = None
        artifact_readback = None
        run_facts_count = run_evidence_count = 0
        if ingest and ingest.get("parse_run_id"):
            run = await conn.fetchrow(
                """SELECT r.id,r.document_version_id,r.source_file_hash,r.status,
                          v.file_hash AS version_file_hash
                   FROM parse_runs r JOIN fiscal_document_versions v
                   ON v.id=r.document_version_id WHERE r.id=$1""",
                ingest["parse_run_id"],
            )
            run_facts_count = await conn.fetchval("SELECT count(*) FROM parse_facts WHERE parse_run_id=$1", ingest["parse_run_id"])
            run_evidence_count = await conn.fetchval("SELECT count(*) FROM parse_evidence WHERE parse_run_id=$1", ingest["parse_run_id"])
            from src.services.parse_artifact_store import load_parse_artifact, load_parse_evidence

            artifact = await load_parse_artifact(conn, ingest["parse_run_id"])
            physical = {f"{table.fragment_uid}:r{cell.row_idx}c{cell.col_idx}": cell
                        for table in artifact.physical_fragments for cell in table.cells}
            errors = []
            aligned = headers = checked = 0
            for table in artifact.table_fragments:
                headers += int(table.header_row_count > 0)
                for cell in table.cells:
                    uid = cell.origin_fragment_uid or table.fragment_uid
                    row = cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx
                    col = cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx
                    aligned += int(col != cell.col_idx)
                    source = physical.get(f"{uid}:r{row}c{col}")
                    if (source is None or source.raw_text != cell.raw_text
                            or source.page_number != cell.page_number or source.bbox != cell.bbox):
                        errors.append({"type": "logical_source", "source_cell_id": f"{uid}:r{row}c{col}"})
                    if str(table.rows[cell.row_idx][cell.col_idx] or "") != cell.raw_text:
                        errors.append({"type": "logical_position", "instance": table.fragment_uid,
                                       "row": cell.row_idx, "column": cell.col_idx})
            for evidence in await load_parse_evidence(conn, ingest["parse_run_id"]):
                if not evidence.get("source_cell_id"):
                    continue
                checked += 1
                source = physical.get(evidence["source_cell_id"])
                if (source is None or str(evidence["quote"] or "").strip() != source.raw_text.strip()
                        or evidence["physical_page"] != source.page_number
                        or evidence["file_hash"] != artifact.source_file_hash
                        or list(evidence["bbox"] or []) != list(source.bbox or [])):
                    errors.append({"type": "stored_evidence", "source_cell_id": evidence["source_cell_id"]})
            artifact_readback = {"passed": not errors, "logical_tables": len(artifact.logical_tables),
                                 "tables_with_headers": headers, "aligned_columns": aligned,
                                 "source_evidence_checked": checked, "errors": errors}
        target_original_facts, target_original_evidence = [], []
        compatibility_projection_verified = False
        if job and job['current_fact_revision_id']:
            from src.services.fact_compatibility_projection import verify_legacy_fact_projection,verify_shared_fact_projection
            snapshot_row=await conn.fetchrow('SELECT s.payload,p.document_version_id,p.receipt FROM analysis_fact_snapshots s '
                'JOIN published_fact_projections p ON p.job_uuid=s.job_uuid AND p.revision_id=s.revision_id '
                'WHERE s.job_uuid=$1 AND s.revision_id=$2',job_id,job['current_fact_revision_id'])
            if snapshot_row:
                snapshot=json.loads(snapshot_row['payload']) if isinstance(snapshot_row['payload'],str)else snapshot_row['payload']
                projection=json.loads(snapshot_row['receipt']) if isinstance(snapshot_row['receipt'],str)else snapshot_row['receipt']
                await verify_legacy_fact_projection(conn,snapshot_row['document_version_id'],snapshot)
                await verify_shared_fact_projection(conn,uuid.UUID(projection['shared_report_id']),snapshot)
                compatibility_projection_verified=True
        if run is not None and target_fact is not None:
            target_original_facts = [dict(row) for row in await conn.fetch(
                "SELECT fact_uid,indicator,subject_name,raw_value,normalized_value::text AS normalized_value,evidence_ids FROM parse_facts WHERE parse_run_id=$1 AND fact_uid=$2",
                ingest["parse_run_id"], target_fact["fact_uid"],
            )]
            target_original_evidence = [dict(row) for row in await conn.fetch(
                "SELECT evidence_id,file_hash,physical_page,quote FROM parse_evidence WHERE parse_run_id=$1 AND evidence_id=ANY($2::text[])",
                ingest["parse_run_id"], target_fact["evidence_ids"],
            )]
        return {
            "observed_at": (await conn.fetchval("SELECT clock_timestamp()")).isoformat(),
            "observation_scope": "current job, result revision and declared parse run queried before disposable DB cleanup",
            "job": dict(job) if job else None,
            "snapshot_mirror_pending": await conn.fetchval('SELECT mirror_pending FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2',job_id,job['current_fact_revision_id']) if job else None,
            "effective_compatibility_projection_verified": compatibility_projection_verified,
            "analysis_results": result_count,
            "result_revision": result_binding["result_revision"] if result_binding else None,
            "result_ingest_status": result_binding["result_ingest_status"] if result_binding else None,
            "database_counts": counts,
            "parse_run": dict(run) if run else None,
            "run_facts_count": run_facts_count,
            "run_evidence_count": run_evidence_count,
            "artifact_readback": artifact_readback,
            "target_original_facts": target_original_facts,
            "target_original_evidence": target_original_evidence,
        }
    finally:
        await conn.close()


def observed_source_type(pdf):
    """Classify physical input from its native text and large page images."""
    import fitz
    scanned = native = 0
    with fitz.open(pdf) as document:
        for page in document:
            chars = len("".join(page.get_text().split()))
            image_area = max((rect.get_area() for item in page.get_images() for rect in page.get_image_rects(item[0])), default=0)
            if chars < 50 and image_area >= page.rect.get_area() * .5:
                scanned += 1
            elif chars:
                native += 1
    return "mixed" if scanned and native else "scanned" if scanned else "native"


def request_with_backoff(client, method, url, **kwargs):
    """Retry only explicit rate rejections, including uploads and analysis.

    Multipart file streams must be rewound before replay. Other responses are
    returned unchanged so failures cannot become a passing validation receipt.
    """
    streams = []
    for value in (kwargs.get("files") or {}).values():
        stream = value[1] if isinstance(value, tuple) and len(value) > 1 else value
        if hasattr(stream, "seek") and hasattr(stream, "tell"):
            streams.append((stream, stream.tell()))
    for attempt in range(4):
        for stream, offset in streams:
            stream.seek(offset)
        response = client.request(method, url, **kwargs)
        if response.status_code != 429 or attempt == 3:
            return response
        try:
            delay = max(1, min(60, int(response.headers.get("Retry-After", "60"))))
        except ValueError:
            delay = 60
        print(f"Service rate limit: retrying {method} {url} after {delay}s", flush=True)
        time.sleep(delay)
    raise AssertionError("unreachable")


def get_report_with_backoff(client, *, params):
    return request_with_backoff(client, "GET", "/api/reports/download", params=params)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-pdf", type=Path)
    parser.add_argument("--scanned-budget-pdf", type=Path)
    parser.add_argument("--mixed-final-pdf", type=Path)
    parser.add_argument("--original-scanned-budget-pdf", type=Path)
    parser.add_argument("--original-mixed-final-pdf", type=Path)
    parser.add_argument("--ocr-control-pdf", type=Path)
    parser.add_argument("--additional-pdfs-dir", type=Path, help="Original user-supplied development PDFs; never independent blind truth")
    parser.add_argument("--native-correction-pdf", type=Path, help="Exercise correction and parse-cache reanalysis on an original native PDF")
    parser.add_argument("--extended-validation", action="store_true", help="Exercise live CSV/browser and backup/restore on the disposable stack")
    parser.add_argument('--history-rebuild-validation',action='store_true',help='Exercise candidate-only analysis, transactional batch failure and rollback on disposable services')
    parser.add_argument("--ocr-url")
    parser.add_argument("--ocr-model-fingerprint")
    parser.add_argument("--timeout-seconds", type=int, default=3600, help="API wait deadline per original analysis/correction, distinct from Worker resource budget")
    parser.add_argument("--pipeline-timeout-seconds", type=int, help="Optional explicit Worker total budget; omitted uses page-aware production budget")
    parser.add_argument(
        "--final-pdf",
        type=Path,
        default=ROOT / "samples/bad/中共上海市普陀区委社会工作部 2024 年度部门决算.pdf",
    )
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    run_id = f"isolated-{uuid.uuid4().hex[:12]}"
    name = f"govbudget-{run_id}"
    receipt = {
        "run_id": run_id,
        "scope": "real_service_development_validation_without_frozen_truth",
        "business_acceptance": "not_evaluated",
        "scenarios": [],
        "errors": [],
        "wait_timeout_seconds": args.timeout_seconds,
        "worker_budget_configuration": {"timeout_seconds": args.pipeline_timeout_seconds, "mode": "explicit" if args.pipeline_timeout_seconds else "page_aware_default"},
    }
    processes, handles = [], []
    started = time.monotonic()
    try:
        with (
            tempfile.TemporaryDirectory(prefix="govbudget-isolated-") as temporary,
            service_lifetime(processes, handles),
        ):
            work = Path(temporary)
            receipt["temporary_workspace"] = str(work)
            env_file = work / "postgres.env"
            password = secrets.token_hex(24)
            env_file.write_text(
                f"POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n"
            )
            env_file.chmod(0o600)
            docker(
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "--env-file",
                str(env_file),
                "-p",
                "127.0.0.1::5432",
                "postgres:16",
            )
            mapping = docker("port", name, "5432/tcp")
            pg_port = int(mapping.rsplit(":", 1)[1])
            dsn = f"postgresql://validator:{password}@127.0.0.1:{pg_port}/validation"
            for _ in range(90):
                if (
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            name,
                            "pg_isready",
                            "-U",
                            "validator",
                            "-d",
                            "validation",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    ).returncode
                    == 0
                ):
                    break
                time.sleep(0.5)
            asyncio.run(prepare_db(dsn))
            receipt["postgres"] = {
                "isolation": name,
                "image_id": docker("inspect", "--format", "{{.Image}}", name),
                "version": docker("exec", name, "postgres", "--version"),
            }
            env = os.environ.copy()
            env.pop("PIPELINE_TIMEOUT_SEC", None)
            if args.pipeline_timeout_seconds is not None:
                env["PIPELINE_TIMEOUT_SEC"] = str(args.pipeline_timeout_seconds)
            for key in list(env):
                if key.startswith(
                    ("AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_", "CODEX_API_")
                ):
                    env.pop(key)
            admin_password, api_key, session_secret = (secrets.token_hex(24) for _ in range(3))
            env.update(
                PYTHON_DOTENV_DISABLED="1",
                TESTING="false",
                DATABASE_URL=dsn,
                GOVBUDGET_AUTH_ENABLED="true",
                GOVBUDGET_API_KEY=api_key,
                USER_SESSION_SECRET=session_secret,
                DEFAULT_ADMIN_PASSWORD=admin_password,
                USER_DATA_DIR=str(work / "users"),
                ORG_DATA_DIR=str(work / "orgs"),
                UPLOAD_DIR=str(work / "uploads"),
                AUDIT_LOG_PATH=str(out / "audit.jsonl"),
                AI_ASSIST_ENABLED="false",
                AI_ASSIST_REQUIRED="false",
                AI_ENABLED="false",
                OCR_ENABLED="true" if args.ocr_url else "false",
                OCR_SERVICE_URL=args.ocr_url or "http://127.0.0.1:9010",
                OCR_MODEL_FINGERPRINT=args.ocr_model_fingerprint or "",
                JOB_QUEUE_ENABLED="true",
                JOB_QUEUE_INLINE_FALLBACK="false",
                JOB_QUEUE_WORKERS="1",
                JOB_QUEUE_POLL_INTERVAL_SECONDS="0.2",
                READY_REQUIRE_DATABASE="true",
                READY_REQUIRE_AI_EXTRACTOR="false",
            )
            api_port = port()
            api_log = (out / "api.log").open("w")
            worker_log = (out / "worker.log").open("w")
            handles.extend((api_log, worker_log))
            api_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=ROOT,
                env={**env, "JOB_QUEUE_ROLE": "api"},
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(api_process)
            worker = subprocess.Popen(
                [sys.executable, "-m", "api.worker"],
                cwd=ROOT,
                env={**env, "JOB_QUEUE_ROLE": "worker"},
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(worker)
            receipt["processes"] = {
                "api_pid": api_process.pid,
                "worker_pid": worker.pid,
                "api_port": api_port,
            }
            receipt["code_fingerprints"] = {
                p: sha(ROOT / p)
                for p in (
                    "api/main.py",
                    "api/worker.py",
                    "api/routes/reports.py",
                    "api/routes/jobs.py",
                    "src/engine/pipeline.py",
                    "src/engine/fact_context.py",
                    "src/engine/fact_table_projection.py",
                    "src/services/fiscal_row_roles.py",
                    "src/services/expected_material_import.py",
                    "src/services/org_storage.py",
                    "scripts/validate_real_browser.cjs",
                    "scripts/backup_all.py",
                    "scripts/validate_extended_acceptance.py",
                    "src/engine/rules_v33.py",
                    "src/engine/budget_rules.py",
                    "src/services/evidence_guard.py",
                    "src/services/ocr_qualification.py",
                    "src/services/ocr_fact_promotion.py",
                    "src/services/ocr_pipeline.py",
                    "src/services/parse_artifact_store.py",
                    "src/services/structure_view.py",
                    "src/services/pdf_parser.py",
                    "src/services/fiscal_fact_materializer.py",
                    "src/services/structured_ingest_runner.py",
                    "src/services/pdf_parse_process.py",
                    "src/services/rule_process.py",
                    "src/services/scan_evidence_certification.py",
                    "scripts/run_isolated_service_validation.py",
                    "scripts/run_zc07_acceptance.py",
                )
            }
            # Freeze every backend/validation module, including rules reached
            # indirectly; an explicit entry-point list cannot cover that graph.
            receipt["code_fingerprints"].update({
                str(path.relative_to(ROOT)): sha(path)
                for directory in ("api", "src", "scripts")
                for path in (ROOT / directory).rglob("*")
                if path.is_file() and path.suffix in {".py", ".cjs"}
            })
            receipt['code_fingerprints'].update({str(path.relative_to(ROOT)):sha(path)
                for directory in ('app/app','app/lib')for path in (ROOT/directory).rglob('*')
                if path.is_file()and path.suffix in {'.ts','.tsx','.js','.css'}})
            with httpx.Client(
                base_url=f"http://127.0.0.1:{api_port}", timeout=180, headers={"X-API-Key": api_key}
            ) as client:
                for _ in range(120):
                    if api_process.poll() is not None:
                        raise RuntimeError("API exited before startup; see api.log")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                response = request_with_backoff(client, "POST",
                    "/api/auth/login", json={"username": "admin", "password": admin_password}
                )
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                changed_password = secrets.token_hex(24)
                request_with_backoff(client, "POST",
                    "/api/auth/change-password",
                    json={"old_password": admin_password, "new_password": changed_password},
                ).raise_for_status()
                response = request_with_backoff(client, "POST",
                    "/api/auth/login", json={"username": "admin", "password": changed_password}
                )
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                ready = request_with_backoff(client, "GET", "/ready")
                ready.raise_for_status()
                receipt["readiness"] = ready.json()
                inventory=json.loads((out.parent/'source_manifest.json').read_text())
                pilots=['上海市交通委员会.pdf','上海市商务委员会.pdf','上海市农场管理局.pdf',
                        '上海市人民政府研究室.pdf','上海化学工业区管理委员会.pdf',
                        '上海市水务局（上海市海洋局）.pdf','上海推进科技创新中心建设办公室.pdf']
                order=sorted(inventory,key=lambda r:(pilots.index(r['file']) if r['file'] in pilots else 99,r['file']))
                scenes=[('native_final' if i==0 else 'native_budget' if i==2 else r['doc_id'],Path(r['frozen_path']),False,False)
                        for i,r in enumerate(order)]
                scenes.append(('native_correction',Path(order[0]['frozen_path']),False,False))
                receipt['pilot_files']=pilots
                receipt['scope']='new_shanghai2022_native_layer_runtime_supplement_OCR_disabled_not_full_OCR_acceptance'
                receipt['scene_labels_note']='All 48 originals are budget publications by cover; legacy scene labels do not imply document kind'
                receipt["expected_scenario_count"] = len(scenes)
                for label, pdf, synthetic, derived in scenes:
                    job_id=None
                    try:
                        scenario_started = time.monotonic()
                        print(f"Running {label} through live API and Worker", flush=True)
                        target = out / label
                        target.mkdir(exist_ok=True)
                        (target / "source.pdf").write_bytes(pdf.read_bytes())
                        with pdf.open("rb") as file:
                            response = request_with_backoff(client, "POST",
                                "/api/documents/upload",
                                files={"file": (pdf.name, file, "application/pdf")},
                            )
                        response.raise_for_status()
                        job_id = response.json()["job_id"]
                        response = request_with_backoff(client, "POST",
                            f"/api/analyze/{job_id}",
                            json={"mode": "legacy", "use_ai_assist": False, "use_local_rules": True},
                        )
                        response.raise_for_status()
                        claims = []
                        for _ in range(args.timeout_seconds):
                            claim_file = work / "uploads" / job_id / ".worker-claim.json"
                            if claim_file.exists():
                                try:
                                    claim = json.loads(claim_file.read_text())
                                    if claim not in claims:
                                        claims.append(claim)
                                except (OSError, ValueError):
                                    pass
                            response = client.get(f"/api/jobs/{job_id}/status")
                            if response.status_code == 429:
                                time.sleep(max(1, float(response.headers.get("Retry-After", "2"))))
                                continue
                            response.raise_for_status()
                            payload = response.json()
                            if payload.get("status") not in {
                                "queued",
                                "processing",
                                "running",
                                "pending",
                            }:
                                break
                            time.sleep(1)
                        else:
                            raise TimeoutError(f"Worker analysis did not finish for {label}")
                        (target / "status.json").write_text(
                            json.dumps(payload, ensure_ascii=False, indent=2)
                        )
                        from src.services.analysis_fact_snapshot import canonical_facts, validate_snapshot
                        frozen_facts_path = Path(env['UPLOAD_DIR']) / job_id / 'fact_snapshots' / (payload['analysis_revision'] + '.json')
                        frozen_facts = validate_snapshot(json.loads(frozen_facts_path.read_text()))
                        for view in ('raw', 'effective'):
                            retrieved = []
                            for offset in range(0, max(1,len(frozen_facts[view+'_context'].get('facts',[]))),1000):
                                answer = client.get(f'/api/jobs/{job_id}/fact-snapshot',params={'view':view,'offset':offset,'limit':1000})
                                answer.raise_for_status()
                                assert answer.json()['snapshot_hash'] == frozen_facts['snapshot_hash']
                                retrieved.extend(answer.json()['facts'])
                            assert canonical_facts(retrieved) == canonical_facts(frozen_facts[view+'_context'].get('facts',[]))
                        (target / 'fact_snapshot_receipt.json').write_text(json.dumps({
                            'snapshot_hash':frozen_facts['snapshot_hash'],'revision_id':frozen_facts['revision_id'],
                            'raw_count':len(frozen_facts['raw_context'].get('facts',[])),
                            'effective_count':len(frozen_facts['effective_context'].get('facts',[])),
                            'authenticated_http_both_views_verified':True},ensure_ascii=False,indent=2))
                        paths = []
                        repeated_downloads = {}
                        for suffix in ("json", "csv", "docx", "pdf"):
                            response = get_report_with_backoff(client, params={"job_id": job_id, "format": suffix})
                            response.raise_for_status()
                            path = target / f"report.{suffix}"
                            path.write_bytes(response.content)
                            paths.append(str(path))
                            repeated = get_report_with_backoff(client, params={"job_id": job_id, "format": suffix})
                            repeated.raise_for_status()
                            repeated_downloads[suffix] = repeated.content == response.content
                        exported_report = json.loads((target/'report.json').read_text())
                        assert exported_report['analysis_revision'] == frozen_facts['revision_id']
                        assert exported_report['review_report']['metadata']['fact_snapshot']['snapshot_hash'] == frozen_facts['snapshot_hash']
                        from scripts.run_zc07_acceptance import _evaluate_export_agreement

                        agreement = _evaluate_export_agreement(
                            {"documents": [{"doc_id": job_id, "export_paths": paths}]}, ROOT
                        )
                        db = asyncio.run(database_receipt(dsn, job_id, structured_ingest_from_payload(payload)))
                        worker_completion_events = []
                        for line in (out / "worker.log").read_text().splitlines():
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            if (
                                isinstance(event, dict)
                                and event.get("job_id") == job_id
                                and event.get("job_status") == payload.get("status")
                                and event.get("progress") == 100
                            ):
                                worker_completion_events.append(event)
                        scenario = {
                            "scenario": label,
                            "source_type": observed_source_type(pdf),
                            "synthetic": synthetic,
                            "derived": derived,
                            "original_material_eligible": not synthetic and not derived,
                            "formal_sample_eligible": False,
                            "formal_sample_incomplete_reasons": [
                                "independent_frozen_truth_not_provided"
                            ],
                            "job_id": job_id,
                            "source_sha256": sha(pdf),
                            "terminal_status": payload.get("status"),
                            "analysis_revision": payload.get("analysis_revision"),
                            "worker_claims": claims,
                            "worker_completion_events": worker_completion_events,
                            "database": db,
                            "exports": {Path(p).name: sha(p) for p in paths},
                            "export_agreement": agreement,
                            "repeated_downloads_identical": repeated_downloads,
                            "business_accuracy": "not_evaluated_without_independent_frozen_truth",
                            "ocr": (payload.get("result") or {}).get("meta", {}).get("ocr"),
                            "pipeline_budget": payload.get("pipeline_budget"),
                            "elapsed_seconds": round(time.monotonic() - scenario_started, 2),
                            "structured_ingest_validation": structured_ingest_validation(payload, db),
                        }
                        (target/'immutable_snapshot.json').write_bytes(frozen_facts_path.read_bytes())
                        from src.db.connection import DatabaseConnection
                        async def persist_artifact():
                            from src.services.parse_artifact_store import load_parse_artifact
                            import asyncpg
                            conn=await asyncpg.connect(dsn)
                            try:
                                artifact=await load_parse_artifact(conn,structured_ingest_from_payload(payload)['parse_run_id'])
                                (target/'artifact.json').write_text(json.dumps(artifact.to_dict(),ensure_ascii=False))
                                snap_payload=await conn.fetchval('SELECT payload FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2',job_id,payload['analysis_revision'])
                                db_snapshot=json.loads(snap_payload) if isinstance(snap_payload,str) else snap_payload
                                assert db_snapshot['snapshot_hash']==frozen_facts['snapshot_hash']
                                assert canonical_facts(db_snapshot['raw_context']['facts'])==canonical_facts(frozen_facts['raw_context']['facts'])
                                assert canonical_facts(db_snapshot['effective_context']['facts'])==canonical_facts(frozen_facts['effective_context']['facts'])
                                (target/'database_snapshot.json').write_text(json.dumps(db_snapshot,ensure_ascii=False))
                                facts=[dict(row) for row in await conn.fetch('SELECT * FROM parse_facts WHERE parse_run_id=$1 ORDER BY id',structured_ingest_from_payload(payload)['parse_run_id'])]
                                evidence=[dict(row) for row in await conn.fetch('SELECT * FROM parse_evidence WHERE parse_run_id=$1 ORDER BY id',structured_ingest_from_payload(payload)['parse_run_id'])]
                                (target/'database_parse_facts.json').write_text(json.dumps(facts,ensure_ascii=False,default=str))
                                (target/'database_parse_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,default=str))
                                raw_by_uid={f['fact_uid']:f for f in frozen_facts['raw_context']['facts']}
                                drift=[]
                                for f in facts:
                                    raw=raw_by_uid.get(f['fact_uid'])
                                    if raw is None:drift.append({'fact_uid':f['fact_uid'],'reason':'not_in_snapshot_raw'});continue
                                    for key in ['raw_value','indicator','subject_name','recognition_status']:
                                        if f[key]!=raw.get(key):drift.append({'fact_uid':f['fact_uid'],'field':key,'db':f[key],'snapshot':raw.get(key)})
                                    if f['normalized_value'] is not None and amount_decimal(f['normalized_value'])!=amount_decimal(raw['normalized_value']):drift.append({'fact_uid':f['fact_uid'],'field':'normalized_value'})
                                (target/'database_full_readback.json').write_text(json.dumps({'snapshot_hash':db_snapshot['snapshot_hash'],'raw_effective_snapshot_payload_equal':True,'parse_facts_checked':len(facts),'snapshot_raw_facts':len(raw_by_uid),'drift':drift,'passed':not drift and len(facts)==len(raw_by_uid)},ensure_ascii=False,default=str,indent=2))

                            finally: await conn.close()
                        asyncio.run(persist_artifact())
                        fact_snapshot = work / "uploads" / job_id / "fact_context.json"
                        if fact_snapshot.is_file():
                            (target / "fact_context.json").write_bytes(fact_snapshot.read_bytes())
                        if (
                            label
                            in {
                                "derived_mixed_final",
                                "original_mixed_final",
                                "synthetic_scanned_control",
                                "native_correction",
                            }
                            and fact_snapshot.is_file()
                        ):
                            facts = json.loads(fact_snapshot.read_text()).get("facts", [])
                            usable = [
                                fact
                                for fact in facts
                                if fact.get("raw_value") and fact.get("evidence_ids")
                            ]
                            native_expense_first_page = min((ref.get("physical_page", 1)
                                for fact in usable if fact.get("table_code") == "FIN_03_expenditure"
                                for ref in fact.get("extra_dims", {}).get("evidence_refs", [])), default=1)
                            usable.sort(
                                key=lambda fact: (
                                    not (label == "native_correction" and fact.get("table_code") == "FIN_03_expenditure"
                                         and any(ref.get("physical_page", 1) > native_expense_first_page
                                                 for ref in fact.get("extra_dims", {}).get("evidence_refs", []))),
                                    not (fact.get("indicator") == "total_actual" and fact.get("subject_name") in {"合计", "总计", "本年收入合计", "本年支出合计"}),
                                    not any(
                                        reference.get("source_method") == "ocr"
                                        for reference in fact.get("extra_dims", {}).get(
                                            "evidence_refs", []
                                        )
                                    )
                                )
                            )
                            if usable:
                                from src.services.correction_overlay_store import (
                                    current_analysis_basis_token,
                                )

                                directory = work / "uploads" / job_id
                                selected = usable[0]
                                old_revision = payload.get("analysis_revision")
                                before_sha = {Path(p).suffix: sha(p) for p in paths}
                                corrected_raw = str(amount_decimal(selected["raw_value"]) + Decimal("10"))
                                corrected = request_with_backoff(client, "POST",
                                    f"/api/jobs/{job_id}/corrections",
                                    json={
                                        "analysis_basis_token": current_analysis_basis_token(
                                            job_id, directory
                                        ),
                                        "reason": "明确标为开发验证的数值覆盖，核验重审与历史隔离",
                                        "changes": [
                                            {
                                                "target_type": "fact",
                                                "target_id": selected["fact_uid"],
                                                "field": "raw_value",
                                                "value": corrected_raw,
                                                "evidence_ids": [selected["evidence_ids"][0]],
                                            }
                                        ],
                                    },
                                )
                                corrected.raise_for_status()
                                for _ in range(args.timeout_seconds):
                                    new_response = client.get(f"/api/jobs/{job_id}/status")
                                    if new_response.status_code == 429:
                                        time.sleep(
                                            max(1, float(new_response.headers.get("Retry-After", "2")))
                                        )
                                        continue
                                    new_response.raise_for_status()
                                    new_payload = new_response.json()
                                    if new_payload.get(
                                        "analysis_revision"
                                    ) != old_revision and new_payload.get("status") not in {
                                        "queued",
                                        "processing",
                                        "running",
                                        "pending",
                                    }:
                                        break
                                    time.sleep(1)
                                else:
                                    raise TimeoutError("Correction did not publish a new revision")
                                correction_dir = target / "corrected"
                                correction_dir.mkdir(exist_ok=True)
                                (correction_dir / "status.json").write_text(
                                    json.dumps(new_payload, ensure_ascii=False, indent=2)
                                )
                                (correction_dir / "fact_context.json").write_bytes(
                                    fact_snapshot.read_bytes()
                                )
                                correction_ledger = directory / "corrections.jsonl"
                                saved_ledger = correction_dir / "corrections.jsonl"
                                if not correction_ledger.is_file():
                                    raise RuntimeError("Correction dispatch completed without its original append-only ledger")
                                saved_ledger.write_bytes(correction_ledger.read_bytes())
                                new_paths, historical = [], {}
                                for suffix in ("json", "csv", "docx", "pdf"):
                                    download = get_report_with_backoff(client, params={"job_id": job_id, "format": suffix})
                                    download.raise_for_status()
                                    new_path = correction_dir / f"report.{suffix}"
                                    new_path.write_bytes(download.content)
                                    new_paths.append(str(new_path))
                                    old = get_report_with_backoff(client, params={
                                            "job_id": job_id,
                                            "format": suffix,
                                            "analysis_revision": old_revision,
                                        })
                                    old.raise_for_status()
                                    historical[suffix] = (
                                        hashlib.sha256(old.content).hexdigest()
                                        == before_sha["." + suffix]
                                    )
                                scenario["correction"] = {
                                    "dispatch": corrected.json(),
                                    "old_revision": old_revision,
                                    "new_revision": new_payload.get("analysis_revision"),
                                    "target_id": selected["fact_uid"],
                                    "original_raw_value": selected["raw_value"],
                                    "corrected_raw_value": corrected_raw,
                                    "corrections_ledger": {"path": str(saved_ledger), "sha256": sha(saved_ledger),
                                                           "source": "original append-only job corrections.jsonl copied before cleanup"},
                                    "history_identical": historical,
                                    "exports": {Path(p).name: sha(p) for p in new_paths},
                                    "export_agreement": _evaluate_export_agreement(
                                        {"documents": [{"doc_id": job_id, "export_paths": new_paths}]},
                                        ROOT,
                                    ),
                                }
                                revised_facts = json.loads(fact_snapshot.read_text()).get("facts", [])
                                revised_target = next(
                                    (
                                        fact
                                        for fact in revised_facts
                                        if fact["fact_uid"] == selected["fact_uid"]
                                    ),
                                    {},
                                )
                                completion_after = []
                                for line in (
                                    (out / "worker.log").read_text(errors="replace").splitlines()
                                ):
                                    try:
                                        event = json.loads(line)
                                    except ValueError:
                                        continue
                                    if (
                                        isinstance(event, dict)
                                        and event.get("job_id") == job_id
                                        and event.get("job_status") == new_payload.get("status")
                                        and event.get("progress") == 100
                                    ):
                                        completion_after.append(event)
                                scenario["correction"].update(
                                    persisted_raw_value=revised_target.get("raw_value"),
                                    worker_completion_events_after=completion_after,
                                    database_after=asyncio.run(database_receipt(dsn, job_id, structured_ingest_from_payload(new_payload), selected)),
                                    source_methods=[
                                        reference.get("source_method")
                                        for reference in selected.get("extra_dims", {}).get(
                                            "evidence_refs", []
                                        )
                                    ],
                                )

                                def relations(view, target_uid=selected["fact_uid"]):
                                    report = view.get("review_report") or {}
                                    rows = report.get("check_receipts") or (
                                        (view.get("result") or {}).get("meta", {}).get("check_receipts")
                                        or []
                                    )
                                    return {
                                        row.get("sub_check_id"): row
                                        for row in rows
                                        if target_uid in (row.get("operand_fact_ids") or [])
                                    }

                                old_relations, new_relations = (
                                    relations(payload),
                                    relations(new_payload),
                                )
                                transitions = [
                                    {
                                        "sub_check_id": key,
                                        "before": old_relations[key],
                                        "after": new_relations[key],
                                    }
                                    for key in sorted(old_relations.keys() & new_relations.keys())
                                ]
                                scenario["correction"]["affected_relation_transitions"] = transitions
                                scenario["correction"]["structured_ingest_validation"] = structured_ingest_validation(new_payload, scenario["correction"]["database_after"])
                                new_cache = scenario["correction"]["structured_ingest_validation"].get("parse_cache") or {}
                                scenario["correction"]["cache_readback_validation_passed"] = bool(
                                    new_cache.get("hit") is True and new_cache.get("readback_completed") is True
                                    and scenario["correction"]["structured_ingest_validation"]["parse_run_id"] == scenario["structured_ingest_validation"]["parse_run_id"]
                                )
                                original_rows = scenario["correction"]["database_after"]["target_original_facts"]
                                original_evidence = scenario["correction"]["database_after"]["target_original_evidence"]
                                scenario["correction"]["original_parse_basis_preserved"] = bool(
                                    any(amount_decimal(row["raw_value"]) == amount_decimal(selected["raw_value"]) for row in original_rows)
                                    and {row["evidence_id"] for row in original_evidence} == set(selected["evidence_ids"])
                                    and all(row["file_hash"] == scenario["source_sha256"] for row in original_evidence)
                                )
                                scenario["correction"]["rule_status_changed"] = any(
                                    item["before"].get("status") != item["after"].get("status")
                                    for item in transitions
                                )
                                scenario["correction_validation_passed"] = bool(
                                    corrected.json().get("reanalysis_status") == "queued"
                                    and len(completion_after) > len(worker_completion_events)
                                    and amount_decimal(revised_target.get("raw_value", "NaN"))
                                    == amount_decimal(corrected_raw)
                                    and all(historical.values())
                                    and scenario["correction"]["export_agreement"].get(
                                        "consistent_documents"
                                    )
                                    == 1
                                    and scenario["correction"]["database_after"]["analysis_results"]
                                    == 1
                                    and scenario["structured_ingest_validation"]["passed"]
                                    and scenario["correction"]["structured_ingest_validation"]["passed"]
                                    and scenario["correction"]["cache_readback_validation_passed"]
                                    and new_payload.get("status") != "error"
                                    and scenario["correction"]["original_parse_basis_preserved"]
                                    and scenario["correction"]["database_after"]["job"][
                                        "analysis_revision"
                                    ]
                                    > db["job"]["analysis_revision"]
                                    and (
                                        label != "synthetic_scanned_control"
                                        or scenario["correction"]["rule_status_changed"]
                                    )
                                )
                            else:
                                scenario["correction_validation_passed"] = False
                                scenario["correction"] = {
                                    "status": "not_exercised",
                                    "reason": "no addressable numeric fact",
                                }
                        scenario["service_validation_passed"] = bool(
                            (claims or worker_completion_events)
                            and db["job"]
                            and db["analysis_results"]
                            and agreement.get("status") == "computed"
                            and agreement.get("consistent_documents") == 1
                            and all(repeated_downloads.values())
                            and scenario.get("correction_validation_passed", True)
                            and scenario["structured_ingest_validation"]["passed"]
                            and payload.get("status") != "error"
                        )
                        receipt["scenarios"].append(scenario)
                        (out/"live_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
                        print(
                            label,
                            scenario["terminal_status"],
                            "service_passed=",
                            scenario["service_validation_passed"],
                            flush=True,
                        )
                    except Exception as exc:
                        failure={'scenario':label,'file':str(pdf),'source_sha256':sha(pdf),'job_id':job_id,
                                 'error_type':type(exc).__name__,'error':str(exc),'service_validation_passed':False,
                                 'elapsed_seconds':round(time.monotonic()-scenario_started,2)}
                        receipt['scenarios'].append(failure)
                        (out/label).mkdir(exist_ok=True)
                        (out/label/'first_failure.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2))
                        print(json.dumps(failure,ensure_ascii=False),flush=True)
                    finally:
                        (out/'live_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
                        if job_id and (work/'uploads'/job_id).exists():
                            shutil.copytree(work/'uploads'/job_id,out/'runtime_uploads'/job_id,dirs_exist_ok=True)
                    if len(receipt['scenarios'])==7:
                        (out/'pilot_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
                        native_pilot=receipt['scenarios'][:6]
                        if not all(x.get('structured_ingest_validation',{}).get('passed') for x in native_pilot):
                            receipt['pilot_chain_defects']='Preserved; continue other documents to determine scope without production logic changes'
                if args.history_rebuild_validation:
                    from scripts.validate_column_mapping import run_mapping_validation
                    mapping_job = next((s['job_id'] for s in receipt['scenarios']
                                        if s['scenario'] == 'native_final'), None)
                    if mapping_job:
                        receipt['column_mapping_validation'] = run_mapping_validation(client=client,
                            env=env, work=work, out=out, job_id=mapping_job,
                            timeout_seconds=args.timeout_seconds)
                    from scripts.validate_historical_rebuild import run_history_validation
                    receipt['history_rebuild_validation']=run_history_validation(env=env,work=work,out=out,
                        jobs=[s['job_id']for s in receipt['scenarios'][:3] if s.get('service_validation_passed')],
                        legacy_job=next((s['job_id']for s in receipt['scenarios']if s['scenario']=='native_budget'),None))
                if args.extended_validation:
                    from scripts.validate_extended_acceptance import run_extended_validation
                    corrected_scene=next((s for s in receipt['scenarios'] if s.get('correction_validation_passed')),None)
                    fact_case=({'job_id':corrected_scene['job_id'],
                        'old_revision':corrected_scene['correction']['old_revision'],
                        'new_revision':corrected_scene['correction']['new_revision'],
                        'old_json_sha256':corrected_scene['exports']['report.json'],
                        'new_json_sha256':corrected_scene['correction']['exports']['report.json']}
                        if corrected_scene else None)
                    receipt["extended_validation"] = run_extended_validation(client=client, env=env, work=work, out=out, container=name, pg_port=pg_port, password=changed_password, processes=processes, handles=handles,fact_case=fact_case)
                    if not receipt["extended_validation"]["passed"]:
                        receipt["errors"].append("extended live validation failed")
            shutil.copytree(work/'uploads',out/'runtime_uploads',dirs_exist_ok=True)
            dump=subprocess.run(['docker','exec',name,'pg_dump','-U','validator','-d','validation','-Fc'],capture_output=True,check=True)
            (out/'acceptance_database.dump').write_bytes(dump.stdout)
            receipt["resource_snapshot"] = docker(
                "stats", name, "--no-stream", "--format", "{{json .}}"
            )
    except Exception as exc:
        receipt["errors"].append(f"{type(exc).__name__}: {exc}")
        print("Validation failed:", type(exc).__name__, str(exc), flush=True)
    finally:
        try:
            dump=subprocess.run(['docker','exec',name,'pg_dump','-U','validator','-d','validation','-Fc'],capture_output=True)
            if dump.returncode==0:
                (out/'acceptance_database.dump').write_bytes(dump.stdout)
                receipt['database_dump_sha256']=sha(out/'acceptance_database.dump')
            schema=subprocess.run(['docker','exec',name,'psql','-U','validator','-d','validation','-Atc','SELECT id FROM schema_migrations ORDER BY id'],capture_output=True,text=True)
            (out/'applied_migrations.txt').write_text(schema.stdout)
            if 'work' in locals() and work.exists():
                shutil.copytree(work/'uploads',out/'runtime_uploads',dirs_exist_ok=True)
        except Exception as preserve_error:
            receipt['errors'].append('preservation: '+str(preserve_error))
        try:
            pg_cleanup = subprocess.run(
                ["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            receipt["postgres_removed"] = pg_cleanup.returncode == 0
            if not receipt["postgres_removed"]:
                receipt["errors"].append(f"PostgreSQL cleanup failed: exit {pg_cleanup.returncode}")
        except Exception as exc:
            receipt["postgres_removed"] = False
            receipt["errors"].append(f"PostgreSQL cleanup failed: {type(exc).__name__}: {exc}")
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
        receipt["temporary_workspace_removed"] = not Path(
            receipt.get("temporary_workspace", "")
        ).exists()
        receipt["processes_stopped"] = all(process.poll() is not None for process in processes)
        receipt["logs_closed"] = all(handle.closed for handle in handles)
        worker_errors = []
        if (out / 'worker.log').exists():
            for line in (out / 'worker.log').read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('level') in {'ERROR','CRITICAL'} and event.get('logger') == 'api.job_queue':
                    worker_errors.append({'logger':event['logger'],'function':event.get('function'),
                                          'job_id':event.get('job_id'),'message':event.get('message')})
        receipt['unhandled_worker_errors'] = worker_errors
        if worker_errors:
            receipt['errors'].append(f'{len(worker_errors)} unhandled Worker exceptions; terminal status alone is not acceptance')
        cleanup_ok = all(
            receipt[key]
            for key in (
                "temporary_workspace_removed",
                "processes_stopped",
                "logs_closed",
                "postgres_removed",
            )
        )
        receipt["cleanup"] = "complete" if cleanup_ok else "incomplete"
        receipt["source_unchanged_during_run"] = all(
            sha(ROOT / path) == digest
            for path, digest in receipt.get("code_fingerprints", {}).items()
        ) and bool(receipt.get("code_fingerprints"))
        if not receipt["source_unchanged_during_run"]:
            receipt["errors"].append(
                "source files changed during validation or startup did not record fingerprints"
            )
        receipt["status"] = (
            "passed"
            if len(receipt["scenarios"]) == receipt.get("expected_scenario_count", 2)
            and all(s["service_validation_passed"] for s in receipt["scenarios"])
            and not receipt["errors"]
            and cleanup_ok
            else "failed"
        )
        receipt["artifact_sha256"] = {p.name: sha(p) for p in out.glob("*.log")}
        (out / "service_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
