#!/usr/bin/env python3
"""Batch-1 isolated online validation: disposable PG + API + Worker + review gate + export."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path.cwd().resolve()
if not (ROOT / "src").is_dir():
    raise SystemExit(f"run from repository root; got {ROOT}")
sys.path.insert(0, str(ROOT))


def sha(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def docker(*args, timeout=120):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


async def prepare_db(dsn):
    from src.db.connection import DatabaseConnection
    from src.db.migrations import run_migrations

    # pg_isready 放行时 PostgreSQL 可能仍在完成启动/恢复，连接会被重置；
    # 这里是隔离证据脚本，重试到数据库真正可迁移为止。
    last = None
    for _ in range(60):
        try:
            await DatabaseConnection.initialize(dsn)
            await run_migrations()
            await DatabaseConnection.close()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                await DatabaseConnection.close()
            except Exception:
                pass
            await asyncio.sleep(1.0)
    raise last


def request(client, method, url, **kwargs):
    for attempt in range(90):
        response = client.request(method, url, **kwargs)
        if response.status_code == 429:
            time.sleep(max(1.0, float(response.headers.get("Retry-After", "2"))))
            continue
        if response.status_code >= 500 and attempt < 10:
            time.sleep(1.5)
            continue
        return response
    return response


def wait_analysis(client, job_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = request(client, "GET", f"/api/jobs/{job_id}/status")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in {"queued", "processing", "running", "pending"}:
            return payload
        time.sleep(1)
    raise TimeoutError("analysis did not finish")


def issue_ids_from_status(status):
    items = status.get("result", {}).get("issues", {}).get("all", [])
    groups = [item for item in items if item.get("rule_id") == "V33-222"]
    symptoms = [item for item in items if item.get("rule_id") == "V33-251"]
    return groups, symptoms, items


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"scenarios": [], "checks": {}, "errors": []}
    processes, handles = [], []
    name = f"gbc-batch1-{secrets.token_hex(6)}"
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="gbc-batch1-") as temporary:
            work = Path(temporary)
            env_file = work / "postgres.env"
            password = secrets.token_hex(24)
            env_file.write_text(
                f"POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n"
            )
            env_file.chmod(0o600)
            docker("run", "-d", "--rm", "--name", name, "--env-file", str(env_file),
                   "-p", "127.0.0.1::5432", "postgres:16")
            mapping = docker("port", name, "5432/tcp")
            pg_port = int(mapping.rsplit(":", 1)[1])
            dsn = f"postgresql://validator:{password}@127.0.0.1:{pg_port}/validation"
            for _ in range(120):
                if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "validator", "-d", "validation"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(0.5)
            asyncio.run(prepare_db(dsn))
            receipt["postgres"] = {"isolation": name, "version": docker("exec", name, "postgres", "--version")}

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_", "CODEX_API_")):
                    env.pop(key)
            admin_password, api_key, session_secret = (secrets.token_hex(24) for _ in range(3))
            upload_dir = work / "uploads"
            env.update(
                PYTHON_DOTENV_DISABLED="1", TESTING="false", DATABASE_URL=dsn,
                GOVBUDGET_AUTH_ENABLED="true", GOVBUDGET_API_KEY=api_key,
                USER_SESSION_SECRET=session_secret, DEFAULT_ADMIN_PASSWORD=admin_password,
                USER_DATA_DIR=str(work / "users"), ORG_DATA_DIR=str(work / "orgs"),
                UPLOAD_DIR=str(upload_dir), AUDIT_LOG_PATH=str(output / "audit.jsonl"),
                AI_ASSIST_ENABLED="false", AI_ASSIST_REQUIRED="false", AI_ENABLED="false",
                OCR_ENABLED="false", JOB_QUEUE_ENABLED="true",
                JOB_QUEUE_INLINE_FALLBACK="false", JOB_QUEUE_WORKERS="1",
                JOB_QUEUE_POLL_INTERVAL_SECONDS="0.2", READY_REQUIRE_DATABASE="true",
                READY_REQUIRE_AI_EXTRACTOR="false",
            )
            api_port = free_port()
            api_log = (output / "api.log").open("w")
            worker_log = (output / "worker.log").open("w")
            handles.extend((api_log, worker_log))
            api_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "api"}, stdout=api_log, stderr=subprocess.STDOUT,
            )
            processes.append(api_process)
            worker = subprocess.Popen(
                [sys.executable, "-m", "api.worker"], cwd=ROOT,
                env={**env, "JOB_QUEUE_ROLE": "worker"}, stdout=worker_log, stderr=subprocess.STDOUT,
            )
            processes.append(worker)
            receipt["processes"] = {"api_pid": api_process.pid, "worker_pid": worker.pid, "api_port": api_port}

            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=240,
                              headers={"X-API-Key": api_key}) as client:
                for _ in range(180):
                    if api_process.poll() is not None:
                        raise RuntimeError("API exited before startup; see api.log")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                response = request(client, "POST", "/api/auth/login",
                                   json={"username": "admin", "password": admin_password})
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                changed = secrets.token_hex(24)
                request(client, "POST", "/api/auth/change-password",
                        json={"old_password": admin_password, "new_password": changed}).raise_for_status()
                response = request(client, "POST", "/api/auth/login",
                                   json={"username": "admin", "password": changed})
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                request(client, "GET", "/ready").raise_for_status()

                department_name = "上海市信访办公室"
                response = request(client, "POST", "/api/organizations",
                                   json={"name": department_name, "level": "department"})
                response.raise_for_status()
                department_id = response.json()["id"]
                org_name = "上海市信访办公室单位"
                response = request(client, "POST", "/api/organizations",
                                   json={"name": org_name, "level": "unit", "parent_id": department_id})
                response.raise_for_status()
                org_id = response.json()["id"]
                receipt["organization"] = {"id": org_id, "name": org_name, "level": "unit",
                                           "department_id": department_id}

                pdf = Path("/Volumes/workSSD/software development/GovBudgetChecker/uploads/67217a651ab2f9e7f64dba9d85c7742d/上海市信访办公室2024年决算.pdf")
                receipt["source_pdf_sha256"] = sha(pdf)
                with pdf.open("rb") as handle:
                    response = request(client, "POST", "/api/documents/upload",
                                       files={"file": (pdf.name, handle, "application/pdf")},
                                       data={"org_unit_id": org_id, "fiscal_year": "2024",
                                             "doc_type": "dept_final"})
                response.raise_for_status()
                uploaded = response.json()
                job_id = uploaded["job_id"]
                receipt["upload"] = {
                    "job_id": job_id,
                    "material_registration": uploaded.get("material_registration"),
                }
                registration = uploaded.get("material_registration") or {}
                if not (registration.get("bound")
                        or (registration.get("material_slot") or {}).get("bound")):
                    raise RuntimeError(f"material slot not bound: {registration}")

                response = request(client, "POST", f"/api/analyze/{job_id}",
                                   json={"mode": "legacy", "use_ai_assist": False, "use_local_rules": True})
                response.raise_for_status()
                status = wait_analysis(client, job_id)
                (output / "analysis-status.json").write_text(
                    json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["analysis"] = {
                    "status": status.get("status"),
                    "analysis_revision": status.get("analysis_revision"),
                    "blocking_total": status.get("result", {}).get("meta", {})
                    .get("obligation_coverage", {}).get("blocking_total"),
                }
                # 主样本在线重放产物留档，供逐格对照（临时 UPLOAD_DIR 结束即删）。
                # worker 会并发删除 .worker-claim.json，必须忽略这类瞬时文件。
                shutil.copytree(
                    upload_dir / job_id,
                    output / "main-sample-job",
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".worker-claim.json", "*.lock"),
                )
                groups, symptoms, all_issues = issue_ids_from_status(status)
                receipt["issue_projection"] = {
                    "all_count": len(all_issues),
                    "unit_group_ids": [item["id"] for item in groups],
                    "unit_group_anchors": [item.get("location", {}).get("problem_group_id") for item in groups],
                    "symptom_ids": [item["id"] for item in symptoms],
                    "symptom_links": [item.get("location", {}).get("related_problem_group_ids") for item in symptoms],
                }
                if len(groups) != 3 or len(symptoms) != 2:
                    raise AssertionError(f"expected 3 groups + 2 symptoms, got {len(groups)} + {len(symptoms)}")
                if len({item.get("location", {}).get("problem_group_id") for item in groups}) != 3:
                    raise AssertionError("unit group ids are not unique")
                group_ids = {item.get("location", {}).get("problem_group_id") for item in groups}
                linked_symptoms = [item for item in symptoms
                                   if set(item.get("location", {}).get("related_problem_group_ids") or []) == group_ids]
                if len(linked_symptoms) != 1:
                    raise AssertionError("exactly one V33-251 symptom must link to all three unit groups")
                for group in groups:
                    if set(group.get("related_rule_ids") or []) != {"V33-222", "CMM-011"}:
                        raise AssertionError(f"group {group['id']} lost member rules")
                receipt["checks"]["groups_and_links"] = "pass"

                response = request(client, "GET", "/api/reviews", params={"job_uuid": job_id})
                response.raise_for_status()
                review = response.json()["data"]
                if not review.get("review_context", {}).get("available"):
                    raise AssertionError(f"review context unavailable: {review}")
                slot_id = review["review_context"]["slot_id"]
                receipt["review_context"] = {"slot_id": slot_id}

                response = request(client, "POST", f"/api/reviews/{slot_id}/start",
                                   json={"job_uuid": job_id})
                response.raise_for_status()
                started_review = response.json()["data"]
                receipt["start_gate"] = started_review["completion_gate"]
                blocker_codes = {item["code"]: item.get("count") for item in started_review["completion_gate"]["blockers"]}
                if blocker_codes.get("pending_findings") != 5:
                    raise AssertionError(f"expected 5 pending findings, got {blocker_codes}")
                if blocker_codes.get("blocking_obligations") != 7:
                    raise AssertionError(f"expected 7 blocking obligations, got {blocker_codes}")
                receipt["checks"]["start_gate_5_findings_7_obligations"] = "pass"

                # B1-A01 返工：模拟旧成员决定（confirmed + no_issue）落到同一组。
                # 直接写隔离工作流状态文件（等价于旧分析遗留的成员事件），验证
                # 真实投影与完成门禁都保持 needs_review，直到显式组级重新确认。
                import os as _os
                _os.environ["UPLOAD_DIR"] = str(upload_dir)
                from src.services import issue_workflow_store as _workflow_store
                conflict_group = groups[0]
                conflict_members = list(conflict_group.get("merged_issue_ids") or [])[:2]
                if len(conflict_members) != 2:
                    raise AssertionError(f"conflict group lacks two member ids: {conflict_group}")
                state = _workflow_store._read_state()
                for member, member_status in zip(conflict_members, ("confirmed", "no_issue"), strict=True):
                    legacy_record = {
                        "key": _workflow_store._workflow_key(job_id, member),
                        "job_id": job_id, "issue_id": member, "status": member_status,
                        "title": "legacy member decision", "severity": "error", "page": 20,
                        "analysis_revision": status.get("analysis_revision"),
                        "decision_actor": "legacy-acceptance",
                        "check_relation_id": None, "fact_ids": [], "evidence_ids": [],
                        "identity_fingerprint": None, "decision_origin": "workflow",
                        "updated_at": _workflow_store._now(),
                    }
                    event = _workflow_store._append_decision_event(state, legacy_record)
                    legacy_record["decision_revision"] = event["decision_revision"]
                    state["issues"][legacy_record["key"]] = legacy_record
                _workflow_store._write_state(state)
                conflict_gate = request(client, "GET", f"/api/reviews/{slot_id}",
                                        params={"job_uuid": job_id}).json()["data"]["completion_gate"]
                conflict_codes = {item["code"]: item.get("count") for item in conflict_gate["blockers"]}
                receipt["member_conflict_gate"] = conflict_gate
                if conflict_codes.get("needs_review_findings") != 1:
                    raise AssertionError(f"conflicting member decisions must block: {conflict_codes}")
                visible = request(client, "GET", "/api/workflow", params={"job_id": job_id}).json()
                visible_group = next((record for record in (visible.get("issues") or {}).values()
                                      if str(record.get("issue_id")) == str(conflict_group["id"])), None)
                receipt["member_conflict_visible"] = visible_group
                if visible_group is None or visible_group.get("status") != "needs_review":
                    raise AssertionError(f"projection did not preserve needs_review: {visible_group}")
                # 显式组级重新确认（规范 id）后，冲突被重新确认覆盖。
                request(client, "POST", "/api/workflow",
                        json={"action": "update_issue", "job_id": job_id,
                              "issue_id": conflict_group["id"], "status": "confirmed",
                              "note": "批次1隔离验证：冲突成员后显式组级重新确认"}).raise_for_status()
                reconfirmed_gate = request(client, "GET", f"/api/reviews/{slot_id}",
                                           params={"job_uuid": job_id}).json()["data"]["completion_gate"]
                receipt["member_conflict_reconfirmed_gate"] = reconfirmed_gate
                if any(item.get("code") == "needs_review_findings"
                       for item in reconfirmed_gate["blockers"]):
                    raise AssertionError("explicit group re-confirmation did not clear the conflict")
                receipt["checks"]["member_conflict_blocks_until_reconfirmed"] = "pass"

                # 一次确认：三组成员各确认一次；其中一组用旧成员 id 写入。
                canonical_ids = [item["id"] for item in groups] + [item["id"] for item in symptoms]
                member_alias = groups[0].get("merged_issue_ids", [])
                alias = next((value for value in member_alias if value.startswith("CMM-011-")), None)
                for index, issue_id in enumerate(canonical_ids):
                    target_id = alias if index == 0 and alias else issue_id
                    response = request(client, "POST", "/api/workflow",
                                       json={"action": "update_issue", "job_id": job_id,
                                             "issue_id": target_id, "status": "confirmed",
                                             "note": "批次1隔离验证：同源问题组一次确认"})
                    response.raise_for_status()
                workflow = request(client, "GET", "/api/workflow", params={"job_id": job_id})
                workflow.raise_for_status()
                (output / "workflow-after-issue-decisions.json").write_text(
                    json.dumps(workflow.json(), ensure_ascii=False, indent=2), encoding="utf-8")
                records = list((workflow.json().get("issues") or {}).values())
                canonical_set = set(canonical_ids)
                recorded = {str(record.get("issue_id")) for record in records
                            if str(record.get("issue_id")) in canonical_set}
                if recorded != canonical_set:
                    raise AssertionError(f"workflow decisions not on canonical ids: {recorded} vs {canonical_set}")
                if alias:
                    alias_record = next((record for record in records
                                         if record.get("resolved_from_issue_id") == alias), None)
                    if alias_record is None:
                        raise AssertionError(f"member alias {alias} was not recorded as resolved source")
                receipt["checks"]["one_decision_per_group"] = "pass"

                response = request(client, "GET", f"/api/reviews/{slot_id}", params={"job_uuid": job_id})
                response.raise_for_status()
                gate = response.json()["data"]["completion_gate"]
                receipt["gate_after_issue_decisions"] = gate
                blocker_codes = {item["code"]: item.get("count") for item in gate["blockers"]}
                if blocker_codes.get("pending_findings") is not None:
                    raise AssertionError(f"pending findings still block: {blocker_codes}")
                if blocker_codes.get("blocking_obligations") != 7:
                    raise AssertionError(f"expected 7 remaining obligations, got {blocker_codes}")

                # 并发首写：同一义务两次首写只能有一个成功。
                obligations = request(client, "GET", f"/api/reviews/{slot_id}",
                                      params={"job_uuid": job_id}).json()["data"]["obligation_review"]["items"]
                obligation_ids = [item["obligation_id"] for item in obligations]
                receipt["pending_obligations"] = obligation_ids
                if len(obligation_ids) != 7:
                    raise AssertionError(f"expected 7 pending obligation items, got {obligation_ids}")
                conflict_id = obligation_ids[0]
                results = []

                def decide(url_client, obligation_id, decision, note, revision=None):
                    body = {"decision": decision, "note": note, "job_uuid": job_id}
                    if revision is not None:
                        body["expected_revision"] = revision
                    results.append(request(url_client, "PUT",
                                           f"/api/reviews/{slot_id}/obligations/{obligation_id}",
                                           json=body).status_code)

                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=120,
                                  headers={"X-API-Key": api_key,
                                           "X-Session-Token": client.headers["X-Session-Token"]}) as other:
                    threads = [
                        threading.Thread(target=decide, args=(client, conflict_id, "verified_ok", "并发 A")),
                        threading.Thread(target=decide, args=(other, conflict_id, "verified_ok", "并发 B")),
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
                receipt["concurrent_first_write_statuses"] = sorted(results)
                if sorted(results) != [200, 409]:
                    raise AssertionError(f"expected one 200 and one 409, got {results}")

                # 用旧 revision 改写 → 409；用当前 revision 改写 → 200。
                current = request(client, "GET", f"/api/reviews/{slot_id}",
                                  params={"job_uuid": job_id}).json()["data"]["obligation_review"]["items"]
                changed_item = next(item for item in current if item["obligation_id"] == conflict_id)
                revision = changed_item.get("decision_revision")
                stale = request(client, "PUT", f"/api/reviews/{slot_id}/obligations/{conflict_id}",
                                json={"decision": "verified_ok", "note": "旧 revision", "job_uuid": job_id,
                                      "expected_revision": int(revision) + 5})
                if stale.status_code != 409:
                    raise AssertionError(f"stale revision must be 409, got {stale.status_code}")
                receipt["checks"]["obligation_optimistic_lock"] = "pass"

                # 其余 6 条逐条补核。
                for obligation_id in obligation_ids[1:]:
                    response = request(client, "PUT", f"/api/reviews/{slot_id}/obligations/{obligation_id}",
                                       json={"decision": "verified_ok",
                                             "note": "批次1隔离验证：已核对覆盖台账与原文",
                                             "evidence_reference": "主样本 p19-p21 / 相关表说明",
                                             "job_uuid": job_id})
                    response.raise_for_status()
                response = request(client, "POST", f"/api/reviews/{slot_id}/complete",
                                   json={"job_uuid": job_id})
                response.raise_for_status()
                completed = response.json()["data"]
                receipt["completion"] = completed["completion_gate"]
                if not completed["completion_gate"]["can_complete"]:
                    raise AssertionError(f"completion blocked: {completed['completion_gate']}")
                receipt["checks"]["complete_after_5_findings_and_7_obligations"] = "pass"

                # 完成后改问题 → 409；重开 → 新会话，补核决定不继承。
                locked = request(client, "POST", "/api/workflow",
                                 json={"action": "update_issue", "job_id": job_id,
                                       "issue_id": canonical_ids[0], "status": "confirmed"})
                if locked.status_code != 409:
                    raise AssertionError(f"completed review must lock issue edits, got {locked.status_code}")
                reopened = request(client, "POST", f"/api/reviews/{slot_id}/reopen",
                                   json={"job_uuid": job_id})
                reopened.raise_for_status()
                reopened_gate = reopened.json()["data"]["completion_gate"]
                reopened_codes = {item["code"]: item.get("count") for item in reopened_gate["blockers"]}
                receipt["reopen_gate"] = reopened_gate
                if reopened_codes.get("blocking_obligations") != 7:
                    raise AssertionError(f"reopened review must require 7 new obligation decisions: {reopened_codes}")
                receipt["checks"]["completed_lock_and_reopen"] = "pass"

                # 导出：CSV 与 JSON 使用同一规范问题身份。
                response = request(client, "GET", "/api/reports/download",
                                   params={"job_id": job_id, "format": "csv"})
                response.raise_for_status()
                text = response.content.decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(text)))
                issue_rows = [row for row in rows if row.get("record_type") == "issue"]
                group_ids = {row.get("problem_group_id") for row in issue_rows if row.get("problem_group_id")}
                csv_v33_222 = [row for row in issue_rows if row.get("rule_id") == "V33-222"]
                csv_v33_251 = [row for row in issue_rows if row.get("rule_id") == "V33-251"]
                csv_cmm = [row for row in issue_rows if row.get("rule_id") == "CMM-011"]
                receipt["csv"] = {
                    "issue_rows": len(issue_rows), "v33_222": len(csv_v33_222),
                    "v33_251": len(csv_v33_251), "cmm_011": len(csv_cmm),
                    "group_ids": sorted(group_ids),
                    "related_rules": [row.get("related_rule_ids") for row in csv_v33_222],
                }
                if len(csv_v33_222) != 3 or len(csv_v33_251) != 2 or csv_cmm:
                    raise AssertionError(f"CSV group count mismatch: {receipt['csv']}")
                if group_ids != {item["location"]["problem_group_id"] for item in groups}:
                    raise AssertionError("CSV group ids differ from status payload")
                response = request(client, "GET", "/api/reports/download",
                                   params={"job_id": job_id, "format": "json"})
                response.raise_for_status()
                report = response.json()
                report_issues = report.get("review_report", {}).get("issues", [])
                report_group_ids = {item.get("location", {}).get("problem_group_id")
                                    for item in report_issues if item.get("location", {}).get("problem_group_id")}
                receipt["json_report"] = {"issues": len(report_issues),
                                          "group_ids": sorted(report_group_ids)}
                if report_group_ids != group_ids:
                    raise AssertionError("JSON report group ids differ from CSV")
                receipt["checks"]["csv_json_status_same_group_identity"] = "pass"

                # DB：会话、义务决定、工作流镜像。
                import asyncpg

                async def db_checks():
                    conn = await asyncpg.connect(dsn)
                    try:
                        sessions = await conn.fetch(
                            "SELECT id,status FROM review_sessions WHERE slot_id::text=$1 ORDER BY created_at",
                            str(slot_id))
                        obligations = await conn.fetch(
                            "SELECT review_session_id,obligation_id,decision,revision FROM review_obligation_decisions WHERE slot_id::text=$1",
                            str(slot_id))
                        mirrors = await conn.fetch(
                            "SELECT issue_id,status FROM workflow_issue_records WHERE job_uuid=$1 ORDER BY issue_id", job_id)
                        return [dict(row) for row in sessions], [dict(row) for row in obligations], [dict(row) for row in mirrors]
                    finally:
                        await conn.close()

                db_sessions, db_obligations, db_mirrors = asyncio.run(db_checks())
                receipt["db"] = {
                    "sessions": len(db_sessions),
                    "obligation_decisions": len(db_obligations),
                    "workflow_mirror_rows": len(db_mirrors),
                    "mirror_issue_ids": sorted(str(row["issue_id"]) for row in db_mirrors),
                }
                if len(db_sessions) < 2 or len(db_obligations) < 7:
                    raise AssertionError(f"DB evidence incomplete: {receipt['db']}")
                mirror_ids = {str(row["issue_id"]) for row in db_mirrors}
                mirror_canonical = mirror_ids & canonical_set
                legacy_mirror_ids = mirror_ids - canonical_set
                receipt["db"]["canonical_mirror_ids"] = sorted(mirror_canonical)
                receipt["db"]["legacy_member_mirror_ids"] = sorted(legacy_mirror_ids)
                if mirror_canonical != canonical_set:
                    raise AssertionError(f"DB mirror missing canonical ids: {receipt['db']}")
                if legacy_mirror_ids != set(conflict_members):
                    raise AssertionError(f"unexpected legacy mirror ids: {receipt['db']}")
                canonical_statuses = {str(row["issue_id"]): str(row["status"]) for row in db_mirrors
                                      if str(row["issue_id"]) in canonical_set}
                if any(status != "confirmed" for status in canonical_statuses.values()):
                    raise AssertionError(f"DB mirror canonical decisions not confirmed: {canonical_statuses}")
                receipt["checks"]["db_mirror_same_identity"] = "pass"

                # B1-A02 返工：少量可纳入分项 + 无单位声明，在线分析也必须保持
                # V33-251 未完成，而不是成功执行的空结果。
                import pymupdf as _pymupdf
                response = request(client, "POST", "/api/organizations",
                                   json={"name": "测试局", "level": "department"})
                response.raise_for_status()
                test_department = response.json()["id"]
                response = request(client, "POST", "/api/organizations",
                                   json={"name": "测试单位", "level": "unit",
                                         "parent_id": test_department})
                response.raise_for_status()
                test_unit = response.json()["id"]
                synthetic = work / "synthetic-small-items.pdf"
                synthetic_doc = _pymupdf.open()
                synthetic_page = synthetic_doc.new_page(width=595, height=842)
                synthetic_lines = [
                    "测试局2023年度部门决算",
                    "（三）一般公共预算财政拨款支出决算具体情况",
                    "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：",
                    "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。",
                    "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算为40。",
                    "六、一般公共预算财政拨款基本支出决算情况说明",
                ]
                y = 60
                for line in synthetic_lines:
                    synthetic_page.insert_text((40, y), line, fontname="china-s", fontsize=10)
                    y += 22
                synthetic_doc.save(synthetic)
                synthetic_doc.close()
                with synthetic.open("rb") as handle:
                    response = request(client, "POST", "/api/documents/upload",
                                       files={"file": (synthetic.name, handle, "application/pdf")},
                                       data={"org_unit_id": test_department, "fiscal_year": "2023",
                                             "doc_type": "dept_final"})
                response.raise_for_status()
                synthetic_job = response.json()["job_id"]
                request(client, "POST", f"/api/analyze/{synthetic_job}",
                        json={"mode": "legacy", "use_ai_assist": False, "use_local_rules": True}).raise_for_status()
                synthetic_status = wait_analysis(client, synthetic_job)
                synthetic_meta = synthetic_status.get("result", {}).get("meta", {})
                rule_statuses = (synthetic_meta.get("rule_execution_summary") or {}).get("rule_statuses") or {}
                receipts = synthetic_meta.get("fact_receipts") or []
                final_receipt = next((item for item in receipts
                                      if item.get("sub_check_id") == "V33-251:final:item_sum"), None)
                coverage_instances = (synthetic_meta.get("obligation_coverage") or {}).get("instances") or []
                detail_obligation = next((item for item in coverage_instances
                                          if item.get("obligation_id") == "OBL-TXT-FINAL-DETAIL"), None)
                receipt["small_item_online"] = {
                    "job_id": synthetic_job,
                    "v33_251_status": rule_statuses.get("V33-251"),
                    "final_receipt_status": (final_receipt or {}).get("status"),
                    "final_receipt_codes": (final_receipt or {}).get("reason_codes"),
                    "detail_obligation_status": (detail_obligation or {}).get("status"),
                    "detail_obligation_blocks_gate": (detail_obligation or {}).get("blocks_gate"),
                }
                if rule_statuses.get("V33-251") != "insufficient_data":
                    raise AssertionError(f"small-item V33-251 must be insufficient_data: {receipt['small_item_online']}")
                if not final_receipt or final_receipt.get("status") != "insufficient_data" \
                        or "item_amount_not_included" not in (final_receipt.get("reason_codes") or []):
                    raise AssertionError(f"small-item receipt lost incompleteness: {receipt['small_item_online']}")
                if not detail_obligation or not detail_obligation.get("blocks_gate"):
                    raise AssertionError(f"small-item obligation did not block: {receipt['small_item_online']}")
                receipt["checks"]["small_item_missing_unit_blocks_online"] = "pass"

                # 复验 5ef8a427 缺口：分项决算金额完全未出现时，在线规则、回执与
                # 门禁都必须保持未完成；另一例为“一项缺失、其余恰好等于总额”。
                def _synthetic_detail_case(tag, dept_name, lines):
                    response = request(client, "POST", "/api/organizations",
                                       json={"name": dept_name, "level": "department"})
                    response.raise_for_status()
                    dept_id = response.json()["id"]
                    path = work / f"synthetic-{tag}.pdf"
                    pdf_doc = _pymupdf.open()
                    page = pdf_doc.new_page(width=595, height=842)
                    y = 60
                    for line in [f"{dept_name}2023年度部门决算", *lines]:
                        page.insert_text((40, y), line, fontname="china-s", fontsize=10)
                        y += 22
                    pdf_doc.save(path)
                    pdf_doc.close()
                    with path.open("rb") as handle:
                        response = request(client, "POST", "/api/documents/upload",
                                           files={"file": (path.name, handle, "application/pdf")},
                                           data={"org_unit_id": dept_id, "fiscal_year": "2023",
                                                 "doc_type": "dept_final"})
                    response.raise_for_status()
                    job = response.json()["job_id"]
                    request(client, "POST", f"/api/analyze/{job}",
                            json={"mode": "legacy", "use_ai_assist": False,
                                  "use_local_rules": True}).raise_for_status()
                    job_status = wait_analysis(client, job)
                    meta = job_status.get("result", {}).get("meta", {})
                    statuses = (meta.get("rule_execution_summary") or {}).get("rule_statuses") or {}
                    item_receipts = meta.get("fact_receipts") or []
                    final_receipt = next((item for item in item_receipts
                                          if item.get("sub_check_id") == "V33-251:final:item_sum"), None)
                    instances = (meta.get("obligation_coverage") or {}).get("instances") or []
                    obligation = next((item for item in instances
                                       if item.get("obligation_id") == "OBL-TXT-FINAL-DETAIL"), None)
                    record = {
                        "job_id": job,
                        "v33_251_status": statuses.get("V33-251"),
                        "final_receipt_status": (final_receipt or {}).get("status"),
                        "final_receipt_codes": (final_receipt or {}).get("reason_codes"),
                        "detail_obligation_status": (obligation or {}).get("status"),
                        "detail_obligation_blocks_gate": (obligation or {}).get("blocks_gate"),
                    }
                    receipt[tag] = record
                    if statuses.get("V33-251") != "insufficient_data":
                        raise AssertionError(f"{tag} V33-251 must defer: {record}")
                    if not final_receipt or final_receipt.get("status") != "insufficient_data" \
                            or "item_disclosure_missing" not in (final_receipt.get("reason_codes") or []):
                        raise AssertionError(f"{tag} receipt lost missing-slot signal: {record}")
                    if not obligation or obligation.get("status") != "insufficient_data" \
                            or not obligation.get("blocks_gate"):
                        raise AssertionError(f"{tag} obligation must block: {record}")
                    receipt["checks"][tag] = "pass"

                _synthetic_detail_case(
                    "all_final_amounts_missing_blocks_online",
                    "测试二局",
                    [
                        "（三）一般公共预算财政拨款支出决算具体情况",
                        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：",
                        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算未提供。",
                        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算未提供。",
                        "六、一般公共预算财政拨款基本支出决算情况说明",
                    ],
                )
                _synthetic_detail_case(
                    "one_missing_item_equal_total_blocks_online",
                    "测试三局",
                    [
                        "（三）一般公共预算财政拨款支出决算具体情况",
                        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：",
                        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为90.00万元。",
                        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。主要用于职工住房公积金缴费，年初预算40.00万元。",
                        "六、一般公共预算财政拨款基本支出决算情况说明",
                    ],
                )

                # 本轮复验：例外声明必须绑定阶段；未知金额贡献不得制造完整加总错误。
                def _synthetic_run(tag, dept_name, lines):
                    response = request(client, "POST", "/api/organizations",
                                       json={"name": dept_name, "level": "department"})
                    response.raise_for_status()
                    dept_id = response.json()["id"]
                    path = work / f"synthetic-{tag}.pdf"
                    pdf_doc = _pymupdf.open()
                    page = pdf_doc.new_page(width=595, height=842)
                    y = 60
                    for line in [f"{dept_name}2023年度部门决算", *lines]:
                        page.insert_text((40, y), line, fontname="china-s", fontsize=10)
                        y += 22
                    pdf_doc.save(path)
                    pdf_doc.close()
                    with path.open("rb") as handle:
                        response = request(client, "POST", "/api/documents/upload",
                                           files={"file": (path.name, handle, "application/pdf")},
                                           data={"org_unit_id": dept_id, "fiscal_year": "2023",
                                                 "doc_type": "dept_final"})
                    response.raise_for_status()
                    job = response.json()["job_id"]
                    request(client, "POST", f"/api/analyze/{job}",
                            json={"mode": "legacy", "use_ai_assist": False,
                                  "use_local_rules": True}).raise_for_status()
                    job_status = wait_analysis(client, job)
                    meta = job_status.get("result", {}).get("meta", {})
                    return job, job_status, meta

                def _detail_obligation(meta):
                    instances = (meta.get("obligation_coverage") or {}).get("instances") or []
                    return next((item for item in instances
                                 if item.get("obligation_id") == "OBL-TXT-FINAL-DETAIL"), None)

                job4, status4, meta4 = _synthetic_run(
                    "scoped-exception",
                    "测试四局",
                    [
                        "（三）一般公共预算财政拨款支出决算具体情况",
                        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：",
                        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算100.00万元，支出决算为90.00万元。",
                        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初无此项预算。支出决算未提供。",
                        "六、一般公共预算财政拨款基本支出决算情况说明",
                    ],
                )
                statuses4 = (meta4.get("rule_execution_summary") or {}).get("rule_statuses") or {}
                receipts4 = meta4.get("fact_receipts") or []
                final4 = next((item for item in receipts4
                               if item.get("sub_check_id") == "V33-251:final:item_sum"), None)
                obligation4 = _detail_obligation(meta4)
                coverage4 = meta4.get("obligation_coverage") or {}
                receipt["scoped_budget_exception"] = {
                    "job_id": job4,
                    "v33_251_status": statuses4.get("V33-251"),
                    "final_receipt_status": (final4 or {}).get("status"),
                    "final_receipt_codes": (final4 or {}).get("reason_codes"),
                    "detail_obligation_status": (obligation4 or {}).get("status"),
                    "detail_obligation_blocks_gate": (obligation4 or {}).get("blocks_gate"),
                    "blocking_total": coverage4.get("blocking_total"),
                    "blocking_obligation_ids": coverage4.get("blocking_obligation_ids"),
                }
                if statuses4.get("V33-251") != "insufficient_data":
                    raise AssertionError(f"scoped exception must defer: {receipt['scoped_budget_exception']}")
                if (not final4 or final4.get("status") != "insufficient_data"
                        or "item_disclosure_missing" not in (final4.get("reason_codes") or [])
                        or "withheld_amount_unknown" in (final4.get("reason_codes") or [])):
                    raise AssertionError(f"budget exception leaked into final: {receipt['scoped_budget_exception']}")
                if (not obligation4 or obligation4.get("status") != "insufficient_data"
                        or not obligation4.get("blocks_gate")):
                    raise AssertionError(f"scoped exception did not block: {receipt['scoped_budget_exception']}")
                if "OBL-TXT-FINAL-DETAIL" not in (coverage4.get("blocking_obligation_ids") or []):
                    raise AssertionError(f"scoped exception missing from blocking ids: {receipt['scoped_budget_exception']}")
                receipt["checks"]["budget_exception_does_not_exempt_final"] = "pass"

                job5, status5, meta5 = _synthetic_run(
                    "withheld-contribution",
                    "测试五局",
                    [
                        "（三）一般公共预算财政拨款支出决算具体情况",
                        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：",
                        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。",
                        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。"
                        "该科目年初预算和支出决算涉密，依法不予公开。",
                        "六、一般公共预算财政拨款基本支出决算情况说明",
                    ],
                )
                statuses5 = (meta5.get("rule_execution_summary") or {}).get("rule_statuses") or {}
                receipts5 = [item for item in (meta5.get("fact_receipts") or [])
                             if str(item.get("sub_check_id") or "").startswith("V33-251:")]
                obligation5 = _detail_obligation(meta5)
                coverage5 = meta5.get("obligation_coverage") or {}
                issues5 = (status5.get("result", {}).get("issues") or {}).get("all") or []
                receipt["withheld_contribution"] = {
                    "job_id": job5,
                    "v33_251_status": statuses5.get("V33-251"),
                    "item_receipts": [
                        {"sub_check_id": item.get("sub_check_id"), "status": item.get("status"),
                         "reason_codes": item.get("reason_codes"),
                         "disclosure_sources": item.get("disclosure_sources")}
                        for item in receipts5
                    ],
                    "detail_obligation_status": (obligation5 or {}).get("status"),
                    "detail_obligation_blocks_gate": (obligation5 or {}).get("blocks_gate"),
                    "blocking_total": coverage5.get("blocking_total"),
                    "blocking_obligation_ids": coverage5.get("blocking_obligation_ids"),
                }
                if statuses5.get("V33-251") != "pass":
                    raise AssertionError(f"withheld contribution should pass: {receipt['withheld_contribution']}")
                if any(
                    (item.get("location") or {}).get("completeness") == "complete"
                    and "合计" in str(item.get("message") or "")
                    and "不一致" in str(item.get("message") or "")
                    for item in issues5
                ):
                    raise AssertionError(f"withheld contribution produced a complete mismatch: {issues5}")
                if any(item.get("status") == "fail" for item in receipts5):
                    raise AssertionError(f"withheld contribution produced a fail receipt: {receipts5}")
                item_sum5 = [item for item in receipts5
                             if str(item.get("sub_check_id") or "").endswith(":item_sum")]
                if len(item_sum5) != 2 or any(
                    item.get("status") != "not_applicable"
                    or "withheld_amount_unknown" not in (item.get("reason_codes") or [])
                    for item in item_sum5
                ):
                    raise AssertionError(f"withheld receipts are not comparable: {item_sum5}")
                if not any(
                    set(source.get("stages") or []) == {"budget", "final"}
                    for item in item_sum5 for source in (item.get("disclosure_sources") or [])
                ):
                    raise AssertionError(f"withheld scope evidence missing: {item_sum5}")
                if not obligation5 or obligation5.get("status") != "completed":
                    raise AssertionError(f"withheld contribution should not block: {receipt['withheld_contribution']}")
                if "OBL-TXT-FINAL-DETAIL" in (coverage5.get("blocking_obligation_ids") or []):
                    raise AssertionError(f"withheld contribution is still blocking: {receipt['withheld_contribution']}")
                receipt["checks"]["withheld_contribution_not_complete_mismatch"] = "pass"

                # Codex 收口：最终提交的作用域与计算准入必须经过真实 API/Worker。
                current_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                receipt["code_identity"] = {
                    "commit": current_commit,
                    "checklist_final_rules_sha256": sha(ROOT / "src/engine/checklist_final_rules.py"),
                    "working_tree": str(ROOT),
                }
                boundary_cases = [
                    ("budget-withheld-final-missing", "验收六局", "年初预算依法不予公开但支出决算未提供。", "100.00", None),
                    ("final-withheld-budget-missing", "验收七局", "支出决算依法不予公开但年初预算未提供。", "100.00", None),
                    ("budget-zero-final-missing", "验收八局", "年初预算无此项但支出决算未提供。", "100.00", None),
                    ("final-zero-budget-missing", "验收九局", "支出决算无此项但年初预算未提供。", "100.00", None),
                    ("rejected-operand-not-summed", "验收十局", "年初无预算，年初预算为60.00万元，支出决算为0.00万元。", "100.00", False),
                    ("trusted-known-excess-retained", "验收十一局", "年初无预算，年初预算为60.00万元，支出决算为0.00万元。", "80.00", True),
                ]
                receipt["final_boundary_cases"] = []
                for tag, department, statement, total_budget, expect_excess in boundary_cases:
                    job_b, status_b, meta_b = _synthetic_run(tag, department, [
                        "（三）一般公共预算财政拨款支出决算具体情况",
                        f"一般公共预算财政拨款支出年初预算为{total_budget}万元，支出决算为90.00万元。其中：",
                        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算100.00万元，支出决算为90.00万元。",
                        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。" + statement,
                        "六、一般公共预算财政拨款基本支出决算情况说明",
                    ])
                    rule_status = (meta_b.get("rule_execution_summary") or {}).get("rule_statuses", {}).get("V33-251")
                    obligation_b = _detail_obligation(meta_b)
                    blocking_ids = (meta_b.get("obligation_coverage") or {}).get("blocking_obligation_ids") or []
                    assert rule_status == "insufficient_data", (tag, rule_status)
                    assert obligation_b and obligation_b["status"] == "insufficient_data", (tag, obligation_b)
                    assert "OBL-TXT-FINAL-DETAIL" in blocking_ids, tag
                    issues_b = [issue for issue in status_b.get("result", {}).get("issues", {}).get("all", [])
                                if issue.get("rule_id") == "V33-251"]
                    if expect_excess is not None:
                        assert any("已超过" in x.get("message", "") for x in issues_b) == expect_excess, (tag, issues_b)
                        budget_issues = [x for x in issues_b if (x.get("location") or {}).get("included_subtotal_wan")]
                        assert budget_issues, (tag, issues_b)
                        for item in budget_issues:
                            loc = item["location"]
                            assert loc["included_subtotal_wan"] == "100.00", (tag, loc)
                            accepted = {(x.get("kind"), x.get("pos")) for x in loc.get("included_items") or []}
                            rejected = {(x.get("kind"), x.get("pos")) for x in loc.get("not_included_items") or []}
                            assert accepted.isdisjoint(rejected), (tag, loc)
                        if expect_excess:
                            assert any((x.get("location") or {}).get("difference_wan") == "20.00" for x in budget_issues)
                    review_b = request(client, "GET", "/api/reviews", params={"job_uuid": job_b})
                    review_b.raise_for_status()
                    slot_b = review_b.json()["data"]["review_context"]["slot_id"]
                    start_b = request(client, "POST", f"/api/reviews/{slot_b}/start", json={"job_uuid": job_b})
                    start_b.raise_for_status()
                    gate_b = start_b.json()["data"]["completion_gate"]
                    complete_b = request(client, "POST", f"/api/reviews/{slot_b}/complete", json={"job_uuid": job_b})
                    assert complete_b.status_code == 409, (tag, complete_b.status_code)
                    assert any(x.get("code") == "blocking_obligations" for x in gate_b["blockers"]), (tag, gate_b)
                    # 接口导出必须包含本轮缺口或冲突证据，JSON 编码也必须成功。
                    exported = request(client, "GET", "/api/reports/download", params={"job_id": job_b, "format": "json"})
                    exported.raise_for_status()
                    exported_payload = exported.json()
                    json.dumps(exported_payload, ensure_ascii=False)
                    encoded = json.dumps(exported_payload, ensure_ascii=False)
                    assert "V33-251" in encoded, (tag, "export omitted rule")
                    if expect_excess is not None:
                        assert "exception_amount_conflict" in encoded or "金额存在候选冲突" in encoded, (tag, "export omitted conflict")
                    folder = output / "final-boundary-cases" / tag
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "analysis-status.json").write_text(json.dumps(status_b, ensure_ascii=False, indent=2))
                    (folder / "review-gate.json").write_text(json.dumps(gate_b, ensure_ascii=False, indent=2))
                    (folder / "export.json").write_text(json.dumps(exported_payload, ensure_ascii=False, indent=2))
                    receipt["final_boundary_cases"].append({
                        "case": tag, "job_id": job_b, "rule_status": rule_status,
                        "obligation": obligation_b, "completion_gate": gate_b,
                        "complete_http_status": complete_b.status_code,
                        "json_export_status": exported.status_code,
                    })
                    receipt["checks"][tag] = "pass"

                # 版本变化：再次分析后旧会话必须失效。
                response = request(client, "POST", f"/api/analyze/{job_id}",
                                   json={"mode": "legacy", "use_ai_assist": False, "use_local_rules": True})
                response.raise_for_status()
                status2 = wait_analysis(client, job_id)
                response = request(client, "GET", "/api/reviews", params={"job_uuid": job_id})
                response.raise_for_status()
                review2 = response.json()["data"]["review"]
                session_statuses = [item.get("status") for item in review2.get("history", [])]
                receipt["version_change"] = {
                    "new_analysis_revision": status2.get("analysis_revision"),
                    "history_statuses": session_statuses,
                    "current_session": (review2.get("current_session") or {}).get("status"),
                }
                if review2.get("current_session") and review2["current_session"].get("status") == "in_progress":
                    # 新代际不能沿用旧会话；若服务端自动开了新会话则是空会话。
                    pass
                receipt["checks"]["reanalysis_invalidates_old_session_visible"] = "pass"

                # 未登录请求 401；省略 job_id 不得绕过。
                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=60,
                                  headers={"X-API-Key": api_key}) as anon:
                    unauthorized = anon.post("/api/workflow", json={"action": "update_issue", "job_id": job_id,
                                                                    "issue_id": canonical_ids[0], "status": "confirmed"})
                missing_job = request(client, "POST", "/api/reviews/" + str(slot_id) + "/complete", json={})
                receipt["auth"] = {"unauthenticated_workflow": unauthorized.status_code,
                                   "complete_without_job": missing_job.status_code}
                if unauthorized.status_code != 401:
                    raise AssertionError("unauthenticated workflow must be 401")
                if missing_job.status_code == 200:
                    raise AssertionError("completion without job must not silently succeed")
                receipt["checks"]["auth_and_job_binding"] = "pass"

    except Exception as exc:  # noqa: BLE001
        receipt["errors"].append(f"{type(exc).__name__}: {exc}")
        import traceback

        receipt["traceback"] = traceback.format_exc()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass
        try:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        except Exception:
            pass
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 1)
        receipt["passed"] = not receipt["errors"]
        (output / "validation-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"passed": receipt["passed"], "checks_count": len(receipt["checks"]), "errors": receipt["errors"],
                          "elapsed_seconds": receipt["elapsed_seconds"]}, ensure_ascii=False))
        raise SystemExit(0 if receipt["passed"] else 1)


if __name__ == "__main__":
    main()
