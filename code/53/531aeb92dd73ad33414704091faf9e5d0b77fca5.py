#!/usr/bin/env python3
"""Run real localhost API + separate Worker + disposable PostgreSQL downloads.

No production .env, data or database is used. Synthetic inputs are identified as
such; this run cannot establish independent blind-test accuracy or OCR trust.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


async def database_receipt(dsn, job_id):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        job = await conn.fetchrow(
            "SELECT job_uuid,status,analysis_revision,file_hash FROM analysis_jobs WHERE job_uuid=$1",
            job_id,
        )
        result_count = await conn.fetchval(
            "SELECT count(*) FROM analysis_results r JOIN analysis_jobs j ON j.id=r.job_id WHERE j.job_uuid=$1",
            job_id,
        )
        counts = {
            table: await conn.fetchval(f"SELECT count(*) FROM {table}")
            for table in ("parse_runs", "parse_facts", "parse_evidence", "schema_migrations")
        }
        return {
            "job": dict(job) if job else None,
            "analysis_results": result_count,
            "database_counts": counts,
        }
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-pdf", type=Path)
    parser.add_argument("--scanned-budget-pdf", type=Path)
    parser.add_argument("--mixed-final-pdf", type=Path)
    parser.add_argument("--original-scanned-budget-pdf", type=Path)
    parser.add_argument("--original-mixed-final-pdf", type=Path)
    parser.add_argument("--ocr-control-pdf", type=Path)
    parser.add_argument("--ocr-url")
    parser.add_argument("--ocr-model-fingerprint")
    parser.add_argument("--timeout-seconds", type=int, default=180)
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
                    "src/engine/rules_v33.py",
                    "src/engine/budget_rules.py",
                    "src/services/evidence_guard.py",
                    "src/services/ocr_qualification.py",
                    "src/services/ocr_fact_promotion.py",
                    "src/services/ocr_pipeline.py",
                    "src/services/scan_evidence_certification.py",
                    "scripts/run_isolated_service_validation.py",
                    "scripts/run_zc07_acceptance.py",
                )
            }
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
                response = client.post(
                    "/api/auth/login", json={"username": "admin", "password": admin_password}
                )
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                changed_password = secrets.token_hex(24)
                client.post(
                    "/api/auth/change-password",
                    json={"old_password": admin_password, "new_password": changed_password},
                ).raise_for_status()
                response = client.post(
                    "/api/auth/login", json={"username": "admin", "password": changed_password}
                )
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                ready = client.get("/ready")
                ready.raise_for_status()
                receipt["readiness"] = ready.json()
                budget = args.budget_pdf
                if budget is None:
                    # A controlled service test, deliberately not a real blind sample.
                    import fitz

                    budget = work / "synthetic-budget.pdf"
                    with fitz.open() as document:
                        page = document.new_page(width=650, height=700)
                        for point, text in [
                            ((40, 40), "2026年测试局部门预算公开"),
                            ((40, 70), "部门预算收支总表"),
                            ((40, 95), "单位：万元"),
                            ((40, 280), "2026年收入预算90万元，支出预算100万元。"),
                        ]:
                            page.insert_text(point, text, fontname="china-s", fontsize=11)
                        xs, ys = [40, 200, 320, 480, 610], [115, 155, 195, 235]
                        for x in xs:
                            page.draw_line((x, 115), (x, 235))
                        for y in ys:
                            page.draw_line((40, y), (610, y))
                        for r, row in enumerate(
                            [
                                ["收入项目", "本年收入", "支出项目", "本年支出"],
                                ["一般公共预算", "100.00", "基本支出", "100.00"],
                                ["收入总计", "100.00", "支出总计", "100.00"],
                            ]
                        ):
                            for c, value in enumerate(row):
                                page.insert_text(
                                    (xs[c] + 6, ys[r] + 25), value, fontname="china-s", fontsize=10
                                )
                        document.save(budget)
                scenes = [
                    ("native_budget", budget, args.budget_pdf is None, False),
                    ("native_final", args.final_pdf, False, False),
                ]
                if args.ocr_control_pdf:
                    scenes.append(("synthetic_scanned_control", args.ocr_control_pdf, True, False))
                if args.scanned_budget_pdf:
                    scenes.append(("derived_scanned_budget", args.scanned_budget_pdf, False, True))
                if args.mixed_final_pdf:
                    scenes.append(("derived_mixed_final", args.mixed_final_pdf, False, True))
                if args.original_scanned_budget_pdf:
                    scenes.append(
                        ("original_scanned_budget", args.original_scanned_budget_pdf, False, False)
                    )
                if args.original_mixed_final_pdf:
                    scenes.append(
                        ("original_mixed_final", args.original_mixed_final_pdf, False, False)
                    )
                receipt["expected_scenario_count"] = len(scenes)
                for label, pdf, synthetic, derived in scenes:
                    print(f"Running {label} through live API and Worker", flush=True)
                    target = out / label
                    target.mkdir(exist_ok=True)
                    (target / "source.pdf").write_bytes(pdf.read_bytes())
                    with pdf.open("rb") as file:
                        response = client.post(
                            "/api/documents/upload",
                            files={"file": (pdf.name, file, "application/pdf")},
                        )
                    response.raise_for_status()
                    job_id = response.json()["job_id"]
                    response = client.post(
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
                    paths = []
                    repeated_downloads = {}
                    for suffix in ("json", "csv", "docx", "pdf"):
                        response = client.get(
                            "/api/reports/download", params={"job_id": job_id, "format": suffix}
                        )
                        response.raise_for_status()
                        path = target / f"report.{suffix}"
                        path.write_bytes(response.content)
                        paths.append(str(path))
                        repeated = client.get(
                            "/api/reports/download", params={"job_id": job_id, "format": suffix}
                        )
                        repeated.raise_for_status()
                        repeated_downloads[suffix] = repeated.content == response.content
                    from scripts.run_zc07_acceptance import _evaluate_export_agreement

                    agreement = _evaluate_export_agreement(
                        {"documents": [{"doc_id": job_id, "export_paths": paths}]}, ROOT
                    )
                    db = asyncio.run(database_receipt(dsn, job_id))
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
                        "source_type": "native"
                        if label.startswith("native_")
                        else "mixed"
                        if "mixed" in label
                        else "scanned",
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
                    }
                    fact_snapshot = work / "uploads" / job_id / "fact_context.json"
                    if fact_snapshot.is_file():
                        (target / "fact_context.json").write_bytes(fact_snapshot.read_bytes())
                    if (
                        label
                        in {
                            "derived_mixed_final",
                            "original_mixed_final",
                            "synthetic_scanned_control",
                        }
                        and fact_snapshot.is_file()
                    ):
                        facts = json.loads(fact_snapshot.read_text()).get("facts", [])
                        usable = [
                            fact
                            for fact in facts
                            if fact.get("raw_value") and fact.get("evidence_ids")
                        ]
                        usable.sort(
                            key=lambda fact: (
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
                            corrected_raw = str(
                                float(str(selected["raw_value"]).replace(",", "").replace(" ", ""))
                                + 10
                            )
                            corrected = client.post(
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
                            new_paths, historical = [], {}
                            for suffix in ("json", "csv", "docx", "pdf"):
                                download = client.get(
                                    "/api/reports/download",
                                    params={"job_id": job_id, "format": suffix},
                                )
                                download.raise_for_status()
                                new_path = correction_dir / f"report.{suffix}"
                                new_path.write_bytes(download.content)
                                new_paths.append(str(new_path))
                                old = client.get(
                                    "/api/reports/download",
                                    params={
                                        "job_id": job_id,
                                        "format": suffix,
                                        "analysis_revision": old_revision,
                                    },
                                )
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
                                database_after=asyncio.run(database_receipt(dsn, job_id)),
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
                            scenario["correction"]["rule_status_changed"] = any(
                                item["before"].get("status") != item["after"].get("status")
                                for item in transitions
                            )
                            scenario["correction_validation_passed"] = bool(
                                corrected.json().get("reanalysis_status") == "queued"
                                and len(completion_after) > len(worker_completion_events)
                                and float(revised_target.get("raw_value", "nan"))
                                == float(corrected_raw)
                                and all(historical.values())
                                and scenario["correction"]["export_agreement"].get(
                                    "consistent_documents"
                                )
                                == 1
                                and scenario["correction"]["database_after"]["analysis_results"]
                                == 1
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
                        and payload.get("status") != "error"
                    )
                    receipt["scenarios"].append(scenario)
                    print(
                        label,
                        scenario["terminal_status"],
                        "service_passed=",
                        scenario["service_validation_passed"],
                        flush=True,
                    )
            receipt["resource_snapshot"] = docker(
                "stats", name, "--no-stream", "--format", "{{json .}}"
            )
    except Exception as exc:
        receipt["errors"].append(f"{type(exc).__name__}: {exc}")
        print("Validation failed:", type(exc).__name__, str(exc), flush=True)
    finally:
        pg_cleanup = subprocess.run(
            ["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        receipt["postgres_removed"] = pg_cleanup.returncode == 0
        if not receipt["postgres_removed"]:
            receipt["errors"].append(f"PostgreSQL cleanup failed: exit {pg_cleanup.returncode}")
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
        receipt["temporary_workspace_removed"] = not Path(
            receipt.get("temporary_workspace", "")
        ).exists()
        receipt["processes_stopped"] = all(process.poll() is not None for process in processes)
        receipt["logs_closed"] = all(handle.closed for handle in handles)
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
