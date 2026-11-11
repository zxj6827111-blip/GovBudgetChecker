#!/usr/bin/env python
"""D4 真实链路验收（IR-08 重做版，2026-11-10）。

一次性隔离栈：docker 一次性 PostgreSQL + 真实 uvicorn API + 真实 Worker
（与 scripts/run_isolated_service_validation.py 同一启动配方，不使用生产
.env / 生产数据 / 生产数据库）。

与旧版（被 Codex IR-08 判为证明不足）的区别：

1. **逐条**核对产品实际交付的全部证据（不再 ``evidence_ids[:20]``）：
   每条 evidence 经 ``GET /api/jobs/{id}/evidence/{eid}`` 读回，要求
   页码为文档内正整数（``page=None`` 不算定位成功）、引文非空，并在
   **原件 PDF 的该页文本层**逐字命中（只折叠空白，与证据门同一归一）。
   findings 的 ``table_refs`` 每一侧都要在自己的页上命中；跨页/双侧
   关系要求每一侧的页码都出现在该 finding 的交付证据里。
2. confirm-report 的**失败与成功两条真实路径**都验：先验证系统未完成时
   409 拒绝且不落记录；口径确认触发重分析后，验证写侧成功签认、刷新
   读回、重复请求幂等、并发同 key 不产生重复记录、持久化失败返回 500
   且不留下半状态、旧版本页面确认被 409 REVISION_CHANGED 拒绝。
3. 不再把全部问题自动设为 confirmed、全部义务写 verified_ok。复核决定
   只对"本脚本已逐条核对原件（证据页码+原文命中）"的正式问题作出，并
   在回执中逐条记录核对依据；任何一条无法独立核对就保持未决、由完成
   门禁如实阻塞（回执同时报告）。脚本以"复核人"身份操作，仅用于验证
   写侧机制，不计为程序自动完成，也不作为人工工作量下降的证据。
4. 完成/重开/导出都要求**成功**路径：开始幂等、完成后重开 200 并读回、
   重开后可再次完成、导出四格式非空且重复下载一致、导出问题成员与
   当前分析一致、导出绑定当前版本。
5. 回执固定：git HEAD、关键源文件 sha256、原件 sha256、页数、分析
   revision、配置开关；任何一步失败即非零退出。

用法:
    .venv/bin/python scripts/d4_real_link_acceptance.py \
        --output <目录> [--pdf <PDF>] [--timeout-seconds 1800]
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_isolated_service_validation import (  # noqa: E402
    docker,
    get_report_with_backoff,
    port,
    prepare_db,
    request_with_backoff,
    service_lifetime,
)

DEFAULT_PDF = (
    ROOT
    / "uploads/6000a11c0737d5149975516b44fd8682"
    / "上海市普陀区人民政府宜川路街道办事处2025年度部门决算.pdf"
)


class Steps:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def step(self, name: str, ok: bool, evidence: dict) -> None:
        self.records.append({"step": name, "ok": bool(ok), "evidence": evidence})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}", flush=True)
        if not ok:
            raise AssertionError(
                f"D4 验收步骤失败: {name}: {json.dumps(evidence, ensure_ascii=False)[:1200]}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalise(text: str) -> str:
    """与证据门同一归一：只折叠空白，不改数字/单位/百分号。"""
    return "".join(str(text or "").split())


def _page_text(page_texts: list[str], page: int) -> str:
    if type(page) is int and 1 <= page <= len(page_texts):
        return _normalise(page_texts[page - 1])
    return ""


def _quote_on_page(quote: str, page: int, page_texts: list[str]) -> bool:
    needle = _normalise(quote)
    return bool(needle) and needle in _page_text(page_texts, page)


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json_result(payload: dict) -> dict:
    result = payload.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = {}
    return result if isinstance(result, dict) else {}


def _iter_findings(result: dict):
    from src.services.evidence_guard import iter_findings

    return list(iter_findings(result))


def _dedupe_findings(findings: list[dict]) -> list[dict]:
    """同一 finding 可能同时出现在 rule_findings 与 legacy 分桶：按 id 去重。"""
    seen: set = set()
    deduped: list[dict] = []
    for finding in findings:
        key = str(finding.get("id") or "")
        if not key:
            key = "|".join(str(finding.get(k) or "") for k in
                           ("rule_id", "rule", "page", "message"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _finding_evidence_status(finding: dict) -> str:
    verification = finding.get("evidence_verification")
    if isinstance(verification, dict):
        return str(verification.get("status") or "unverified")
    return "unverified"


def _evidence_items(finding: dict) -> list[dict]:
    return [item for item in (finding.get("evidence") or []) if isinstance(item, dict)]


def _is_document_level(finding: dict) -> bool:
    from src.services.evidence_guard import is_document_level_finding

    return is_document_level_finding(finding)


def verify_all_evidence(
    client: httpx.Client,
    job_id: str,
    result: dict,
    page_texts: list[str],
    original_hash: str,
    page_count: int,
) -> dict:
    """逐条核对全部交付证据 + 每一侧 table_refs + page=None 判定。

    返回可 JSON 化的核对结果；``ok`` 为 False 时携带前若干失败明细。
    """
    findings = _dedupe_findings(_iter_findings(result))
    evidence_by_id: dict[str, dict] = {}
    failures: list[dict] = []
    verified_findings = 0
    evidence_total = 0
    evidence_grounded = 0
    refs_total = 0
    refs_grounded = 0
    multi_side_total = 0
    multi_side_grounded = 0
    documents_without_page: list[str] = []

    for finding in findings:
        finding_id = str(finding.get("id") or finding.get("rule_id") or "")
        items = _evidence_items(finding)
        finding_ok = True
        evidence_pages: set[int] = set()
        if not items and not _is_document_level(finding):
            failures.append({"finding": finding_id, "reason": "no_evidence_items"})
            finding_ok = False
        for item in items:
            evidence_total += 1
            eid = str(item.get("evidence_id") or "").strip()
            item_page = _positive_int(item.get("page")) or _positive_int(
                item.get("physical_page"))
            item_text = str(item.get("quote") or item.get("text") or "").strip()
            if not eid:
                failures.append({"finding": finding_id, "reason": "evidence_without_id",
                                 "page": item_page})
                finding_ok = False
                continue
            answer = client.get(f"/api/jobs/{job_id}/evidence/{eid}")
            if answer.status_code != 200:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": f"evidence_http_{answer.status_code}"})
                finding_ok = False
                continue
            body = answer.json()
            evidence_by_id[eid] = body
            page = _positive_int(body.get("page")) or _positive_int(
                body.get("physical_page"))
            if page is None:
                # IR-08：page=None 不算定位成功（旧验收器把 None 计通过）
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "page_missing"})
                finding_ok = False
                continue
            if page > page_count:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "page_out_of_range",
                                 "page": page, "page_count": page_count})
                finding_ok = False
                continue
            if item_page is not None and item_page != page:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "payload_page_mismatch",
                                 "payload_page": item_page, "endpoint_page": page})
                finding_ok = False
            quote = str(body.get("quote") or body.get("text")
                        or body.get("text_snippet") or item_text or "").strip()
            if not quote:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "quote_missing", "page": page})
                finding_ok = False
            elif not _quote_on_page(quote, page, page_texts):
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "quote_not_on_original_page",
                                 "page": page, "quote": quote[:80]})
                finding_ok = False
            else:
                evidence_grounded += 1
            body_hash = str(body.get("file_hash") or "").strip()
            if body_hash and body_hash != original_hash:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "file_hash_mismatch",
                                 "file_hash": body_hash[:16]})
                finding_ok = False
            evidence_pages.add(page)

        # 每一侧 table_refs：span 必须在自己声明的页上逐字命中；
        # 跨页关系要求每一侧的页都出现在该 finding 的交付证据里。
        location = finding.get("location") or {}
        refs = [ref for ref in (location.get("table_refs") or [])
                if isinstance(ref, dict) and str(ref.get("span") or "").strip()]
        ref_pages: set[int] = set()
        for ref in refs:
            refs_total += 1
            ref_page = _positive_int(ref.get("page"))
            if ref_page is None:
                failures.append({"finding": finding_id, "reason": "table_ref_page_missing",
                                 "span": str(ref.get("span"))[:40]})
                finding_ok = False
                continue
            ref_pages.add(ref_page)
            if _quote_on_page(str(ref.get("span")), ref_page, page_texts):
                refs_grounded += 1
            else:
                failures.append({"finding": finding_id, "reason": "table_ref_span_not_on_page",
                                 "page": ref_page, "span": str(ref.get("span"))[:80]})
                finding_ok = False
        if len(ref_pages) >= 2:
            multi_side_total += 1
            missing_side = ref_pages - evidence_pages
            if missing_side:
                failures.append({"finding": finding_id,
                                 "reason": "multi_side_evidence_missing",
                                 "ref_pages": sorted(ref_pages),
                                 "evidence_pages": sorted(evidence_pages),
                                 "missing": sorted(missing_side)})
                finding_ok = False
            else:
                multi_side_grounded += 1

        # 交付页：正式问题必须有可定位页；page=None 不算定位成功。
        severity = str(finding.get("severity") or "").strip().lower()
        page = _positive_int((location.get("page")) or finding.get("page"))
        if page is None or page > page_count:
            if severity in {"error", "warn", "high", "medium", "critical", "low"}:
                documents_without_page.append(finding_id)
                finding_ok = False
                failures.append({"finding": finding_id, "reason": "finding_page_missing",
                                 "severity": severity})
        if _finding_evidence_status(finding) != "verified":
            failures.append({"finding": finding_id,
                             "reason": f"evidence_status_{_finding_evidence_status(finding)}"})
            finding_ok = False
        if finding_ok:
            verified_findings += 1

    return {
        "ok": not failures,
        "findings_total": len(findings),
        "findings_grounded": verified_findings,
        "evidence_total": evidence_total,
        "evidence_grounded": evidence_grounded,
        "table_refs_total": refs_total,
        "table_refs_grounded": refs_grounded,
        "multi_side_total": multi_side_total,
        "multi_side_grounded": multi_side_grounded,
        "failures": failures[:40],
        "failure_count": len(failures),
        "documents_without_page": documents_without_page,
    }


def gate_truth_check(gate_body: dict, result: dict) -> dict:
    """用产品实际结果结构独立复算三分类，核对门禁没有漏读/错分类。"""
    findings = _dedupe_findings(_iter_findings(result))
    expected_confirmed: list[str] = []
    seen: set[str] = set()
    manual_review_ids: set[str] = set()
    failed_evidence_ids: set[str] = set()
    for finding in findings:
        issue_id = str(finding.get("id") or "").strip()
        severity = str(finding.get("severity") or "").strip().lower()
        status = _finding_evidence_status(finding)
        if severity == "manual_review":
            manual_review_ids.add(issue_id)
        if status == "failed":
            failed_evidence_ids.add(issue_id)
        if not issue_id or issue_id in seen:
            continue
        if status == "verified" and severity != "manual_review":
            seen.add(issue_id)
            expected_confirmed.append(issue_id)
    categories = gate_body.get("categories") or {}
    actual_confirmed = [
        str(item.get("id")) for item in (categories.get("confirmed_material_issues") or [])
        if item.get("id")
    ]
    obligations = ((result.get("meta") or {}).get("obligation_coverage") or {}).get(
        "instances") or []
    expected_blocking_obligations = [
        str(item.get("obligation_id")) for item in obligations
        if isinstance(item, dict)
        and str(item.get("status") or "").strip() not in {"completed", "not_applicable"}
        and item.get("blocks_gate") is not False
    ]
    system_ids = {
        str(item.get("rule_id") or "") for item in (categories.get("system_incomplete") or [])
    }
    problems: list[str] = []
    if set(actual_confirmed) != set(expected_confirmed):
        problems.append(
            f"confirmed_set_mismatch expected={len(expected_confirmed)} actual={len(actual_confirmed)}")
    leaked_manual = manual_review_ids & set(actual_confirmed)
    if leaked_manual:
        problems.append(f"manual_review_in_confirmed={sorted(leaked_manual)}")
    leaked_failed = failed_evidence_ids & set(actual_confirmed)
    if leaked_failed:
        problems.append(f"failed_evidence_in_confirmed={sorted(leaked_failed)}")
    unrepresented = [
        oid for oid in expected_blocking_obligations
        if oid and oid not in system_ids
    ]
    if unrepresented:
        problems.append(f"blocking_obligations_missing_from_system_incomplete={unrepresented}")
    return {
        "ok": not problems,
        "problems": problems,
        "expected_confirmed": len(expected_confirmed),
        "actual_confirmed": len(actual_confirmed),
        "manual_review_ids": len(manual_review_ids),
        "failed_evidence_ids": len(failed_evidence_ids),
        "expected_blocking_obligations": expected_blocking_obligations,
        "gate": gate_body.get("gate"),
        "counts": gate_body.get("counts"),
    }


def review_problem_set(job_id: str, result: dict, structured_ingest=None) -> list:
    """与复核完成门禁同源的正式问题集合（唯一口径在服务端模块）。"""
    from src.services import issue_workflow_store
    from src.services.review_problem_set import collect_review_problems

    try:
        snapshot = issue_workflow_store.get_job_problem_snapshot(job_id)
    except Exception:  # noqa: BLE001 - 读不到按空快照，与门禁 fail-closed 一致
        snapshot = {"ignored_issue_ids": []}
    return collect_review_problems(
        job_uuid=job_id,
        ai_findings=result.get("ai_findings"),
        rule_findings=result.get("rule_findings"),
        merged_result=result.get("merged"),
        structured_ingest=structured_ingest,
        result=result,
        ignored_issue_ids=snapshot.get("ignored_issue_ids") or [],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--expect-session-complete-http", type=int, default=200,
                        help="完成后重开的完成请求期望状态（默认要求成功 200）")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF 不存在: {pdf_path}")
    original_hash = _sha(pdf_path)

    # 原件文本层：证据核对必须回到原件，而不是产品返回的页码/引文。
    import pdfplumber

    from src.services.pdf_page_extract import extract_visible_text_from_page

    with pdfplumber.open(str(pdf_path)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
    page_count = len(page_texts)

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    frozen = {
        "git_head": head,
        "key_source_sha256": {
            str(rel): _sha(ROOT / rel) for rel in (
                "src/services/report_confirmation_gate.py",
                "src/services/evidence_guard.py",
                "src/services/rule_source_binding.py",
                "src/engine/pipeline.py",
                "src/engine/issue_evidence.py",
                "api/routes/jobs.py",
            )
        },
        "original_sha256": original_hash,
        "page_count": page_count,
    }

    steps = Steps()
    run_id = f"d4link-{secrets.token_hex(6)}"
    name = f"govbudget-{run_id}"
    processes, handles = [], []
    receipt: dict = {"run_id": run_id, "pdf": str(pdf_path), "steps": steps.records,
                     "frozen": frozen}
    started = time.monotonic()

    def _get_status(client, job_id):
        answer = client.get(f"/api/jobs/{job_id}/status")
        if answer.status_code == 429:
            time.sleep(max(1, float(answer.headers.get("Retry-After", "2"))))
            return None
        answer.raise_for_status()
        return answer.json()

    def _wait_analysis(client, job_id, previous_revision=None):
        for _ in range(args.timeout_seconds):
            payload = _get_status(client, job_id)
            if payload is None:
                continue
            if payload.get("status") not in {"queued", "processing", "running", "pending"}:
                revision = payload.get("analysis_revision")
                if previous_revision is None or revision != previous_revision:
                    return payload
            time.sleep(1)
        raise TimeoutError("分析未在时限内完成/版本未更新")

    try:
        import tempfile

        with (
            tempfile.TemporaryDirectory(prefix="govbudget-d4link-") as temporary,
            service_lifetime(processes, handles),
        ):
            work = Path(temporary)
            env_file = work / "postgres.env"
            password = secrets.token_hex(24)
            env_file.write_text(
                f"POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n")
            env_file.chmod(0o600)
            docker("run", "-d", "--rm", "--name", name, "--env-file", str(env_file),
                   "-p", "127.0.0.1::5432", "postgres:16")
            mapping = docker("port", name, "5432/tcp")
            pg_port = int(mapping.rsplit(":", 1)[1])
            dsn = f"postgresql://validator:{password}@127.0.0.1:{pg_port}/validation"
            for _ in range(90):
                if docker_exec_ready(name):
                    break
                time.sleep(0.5)
            asyncio.run(prepare_db(dsn))

            import os

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_", "CODEX_API_")):
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
                OCR_ENABLED="false",
                JOB_QUEUE_ENABLED="true",
                JOB_QUEUE_INLINE_FALLBACK="false",
                JOB_QUEUE_WORKERS="1",
                JOB_QUEUE_POLL_INTERVAL_SECONDS="0.2",
                READY_REQUIRE_DATABASE="true",
                READY_REQUIRE_AI_EXTRACTOR="false",
                # 整份确认写侧在本隔离实例开启（默认关闭路径由单测覆盖）
                GOVBUDGET_CONFIRM_REPORT_ENABLED="1",
            )
            api_port = port()
            api_log = (out / "api.log").open("w")
            worker_log = (out / "worker.log").open("w")
            handles.extend((api_log, worker_log))
            import subprocess as _sp

            api_process = _sp.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                 "--port", str(api_port)],
                cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "api"},
                stdout=api_log, stderr=_sp.STDOUT,
            )
            processes.append(api_process)
            worker = _sp.Popen(
                [sys.executable, "-m", "api.worker"],
                cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "worker"},
                stdout=worker_log, stderr=_sp.STDOUT,
            )
            processes.append(worker)

            # trust_env=False：绕过系统代理（本机 shell 带 http_proxy 时，
            # 对 127.0.0.1 的请求会被转发到代理端口导致永久阻塞）。
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=300,
                              headers={"X-API-Key": api_key}, trust_env=False) as client:
                for _ in range(120):
                    if api_process.poll() is not None:
                        raise RuntimeError("API exited before startup; see api.log")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                response = request_with_backoff(client, "POST", "/api/auth/login",
                                                json={"username": "admin", "password": admin_password})
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                changed = secrets.token_hex(24)
                request_with_backoff(client, "POST", "/api/auth/change-password",
                                     json={"old_password": admin_password,
                                           "new_password": changed}).raise_for_status()
                response = request_with_backoff(client, "POST", "/api/auth/login",
                                                json={"username": "admin", "password": changed})
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                client.get("/ready").raise_for_status()

                # ---- S0 组织目录就绪（真实产品流：上传前目录在位）----
                org_create = client.post("/api/organizations", json={
                    "name": "上海市普陀区人民政府宜川路街道办事处", "level": "department"})
                steps.step("S0_org_catalog_ready",
                           org_create.status_code in (200, 201, 422),
                           {"org_http": org_create.status_code})

                # ---- S1 真实上传 + 分析 ----
                with pdf_path.open("rb") as file:
                    response = request_with_backoff(
                        client, "POST", "/api/documents/upload",
                        files={"file": (pdf_path.name, file, "application/pdf")})
                response.raise_for_status()
                job_id = response.json()["job_id"]
                receipt["job_id"] = job_id
                request_with_backoff(
                    client, "POST", f"/api/analyze/{job_id}",
                    json={"mode": "legacy", "use_ai_assist": False,
                          "use_local_rules": True}).raise_for_status()
                payload = _wait_analysis(client, job_id)
                result = _json_result(payload)
                findings = _dedupe_findings(_iter_findings(result))
                revision_r1 = payload.get("analysis_revision")
                steps.step("S1_upload_analyze",
                           payload.get("status") in {"done", "review_required"}
                           and bool(revision_r1) and bool(findings),
                           {"status": payload.get("status"), "job_id": job_id,
                            "revision": revision_r1,
                            "findings": len(findings),
                            "result_keys": sorted(result.keys())[:24]})

                # ---- S2 持久化/读回（事实快照分页读回 + 哈希一致）----
                from src.services.analysis_fact_snapshot import (
                    canonical_facts,
                    validate_snapshot,
                )

                frozen_path = Path(env["UPLOAD_DIR"]) / job_id / "fact_snapshots" / f"{revision_r1}.json"
                snapshot = validate_snapshot(json.loads(frozen_path.read_text()))
                hash_ok = True
                for view in ("raw", "effective"):
                    retrieved = []
                    total = max(1, len(snapshot[f"{view}_context"].get("facts", [])))
                    for offset in range(0, total, 1000):
                        answer = client.get(f"/api/jobs/{job_id}/fact-snapshot",
                                            params={"view": view, "offset": offset, "limit": 1000})
                        answer.raise_for_status()
                        if answer.json()["snapshot_hash"] != snapshot["snapshot_hash"]:
                            hash_ok = False
                        retrieved.extend(answer.json()["facts"])
                    if canonical_facts(retrieved) != canonical_facts(
                            snapshot[f"{view}_context"].get("facts", [])):
                        hash_ok = False
                versions = client.get(f"/api/jobs/{job_id}/analysis-versions")
                versions.raise_for_status()
                steps.step("S2_persist_readback", hash_ok and versions.status_code == 200,
                           {"snapshot_hash": snapshot["snapshot_hash"],
                            "raw": len(snapshot["raw_context"].get("facts", [])),
                            "effective": len(snapshot["effective_context"].get("facts", [])),
                            "versions_http": versions.status_code})

                # ---- S3 全量证据核对（第 1 版；口径确认后还会再核对一次）----
                check_r1 = verify_all_evidence(client, job_id, result, page_texts,
                                               original_hash, page_count)
                steps.step("S3_all_evidence_grounded", check_r1["ok"],
                           {k: v for k, v in check_r1.items() if k != "ok"})

                # ---- S4 门禁三分类与真实结果结构对账（此时口径未确认，
                #      阻塞性义务应在 system_incomplete 中且确认被 409 拒绝）----
                gate = client.get(f"/api/jobs/{job_id}/report-confirmation")
                gate.raise_for_status()
                gate_body = gate.json()
                truth = gate_truth_check(gate_body, result)
                gate_gate = gate_body.get("gate") or {}
                blocking_count = int(gate_gate.get("blocking_system_incomplete") or 0)
                # 若当前结果确有阻塞性未完成项 → 必须 409 且不落记录；
                # 若门禁已打开（本材料修复后无阻塞项）→ 不伪造阻塞，改为
                # 断言"尚未有任何确认记录"（不能自动替用户确认）。
                blocked_post = None
                post_detail = None
                if blocking_count:
                    blocked_post = client.post(
                        f"/api/jobs/{job_id}/confirm-report",
                        json={"expected_analysis_revision": revision_r1,
                              "idempotency_key": f"blocked-{secrets.token_hex(4)}"})
                    post_detail = blocked_post.json().get("detail")
                gate_after = client.get(f"/api/jobs/{job_id}/report-confirmation")
                gate_after.raise_for_status()
                records_after_block = gate_after.json().get("confirm_records") or []
                blocked_ok = (
                    blocked_post is not None
                    and blocked_post.status_code == 409
                    and post_detail == "SYSTEM_INCOMPLETE"
                ) if blocking_count else (blocked_post is None)
                steps.step("S4_gate_truth_and_state",
                           truth["ok"] and blocked_ok and not records_after_block,
                           {"truth": truth,
                            "blocking_system_incomplete": blocking_count,
                            "blocked_branch_exercised": bool(blocking_count),
                            "post_http": blocked_post.status_code if blocked_post else None,
                            "post_detail": post_detail,
                            "records_after": len(records_after_block)})

                # ---- S5a 材料口径确认（真实产品流；确认会派发完整重分析）----
                rematch = client.post("/api/jobs/rematch-organizations", json={})
                dims_get = client.get(f"/api/jobs/{job_id}/material-dimensions")
                dims_get.raise_for_status()
                dims_body = dims_get.json()
                document_version_id = int(dims_body.get("document_version_id") or 0) or None
                file_hash = str(dims_body.get("file_hash") or "")
                basis_from_dims = str(dims_body.get("analysis_basis_token") or "")
                expected_revision = 0
                for dim in (dims_body.get("dimensions") or []):
                    if isinstance(dim, dict) and dim.get("expected_revision") is not None:
                        expected_revision = int(dim.get("expected_revision"))
                        break
                import uuid as _uuid

                def _dims_payload(revision):
                    return {
                        "request_id": str(_uuid.uuid4()),
                        "document_version_id": document_version_id,
                        "file_hash": file_hash,
                        "analysis_basis_token": basis_from_dims,
                        "writing": {"value": "department", "expected_revision": revision,
                                    "note": "D4 验收：封面为部门决算，写法=部门",
                                    "evidence_reference": "材料口径读取接口",
                                    "acknowledge_conflicts": True, "cover_independent": True},
                        "amount": {"value": "department_summary", "expected_revision": revision,
                                   "note": "D4 验收：部门决算汇总本级及所属单位",
                                   "evidence_reference": "材料口径读取接口",
                                   "acknowledge_conflicts": True, "cover_independent": True},
                    }

                dims_ok = bool(document_version_id and len(file_hash) == 64 and basis_from_dims)
                dims_post = None
                if dims_ok:
                    dims_post = client.post(f"/api/jobs/{job_id}/material-dimensions",
                                            json=_dims_payload(expected_revision))
                    attempts = 1
                    while dims_post.status_code == 409 and attempts < 3:
                        refreshed = client.get(f"/api/jobs/{job_id}/material-dimensions")
                        refreshed.raise_for_status()
                        for dim in (refreshed.json().get("dimensions") or []):
                            if isinstance(dim, dict) and dim.get("expected_revision") is not None:
                                expected_revision = int(dim.get("expected_revision"))
                        dims_post = client.post(f"/api/jobs/{job_id}/material-dimensions",
                                                json=_dims_payload(expected_revision))
                        attempts += 1
                payload_r2 = _wait_analysis(client, job_id, previous_revision=revision_r1)
                result_r2 = _json_result(payload_r2)
                revision_r2 = payload_r2.get("analysis_revision")
                steps.step("S5a_identity_and_scope",
                           rematch.status_code == 200 and dims_ok
                           and dims_post is not None
                           and dims_post.status_code in (200, 202)
                           and bool(revision_r2) and revision_r2 != revision_r1,
                           {"rematch_http": rematch.status_code,
                            "dims_get": dims_get.status_code,
                            "dims_post": dims_post.status_code if dims_post else None,
                            "revision_r1": revision_r1, "revision_r2": revision_r2,
                            "dims_detail": str(dims_post.json())[:200]
                            if dims_post is not None and dims_post.status_code not in (200, 202)
                            else None})

                # ---- S3b 新版本全量证据复核 ----
                check_r2 = verify_all_evidence(client, job_id, result_r2, page_texts,
                                               original_hash, page_count)
                steps.step("S3b_all_evidence_grounded_after_scope",
                           check_r2["ok"],
                           {k: v for k, v in check_r2.items() if k != "ok"})

                # ---- S4b 持久化失败（事务失败）：目录只读时写侧必须 500，
                #      且读取接口看不到任何半状态记录 ----
                job_dir = Path(env["UPLOAD_DIR"]) / job_id
                gate_r2 = client.get(f"/api/jobs/{job_id}/report-confirmation")
                gate_r2.raise_for_status()
                gate_body_r2 = gate_r2.json()
                truth_r2 = gate_truth_check(gate_body_r2, result_r2)
                can_confirm = bool((gate_body_r2.get("gate") or {}).get(
                    "can_confirm_whole_report"))
                fail_key = f"txfail-{secrets.token_hex(4)}"
                records_before = list(gate_body_r2.get("confirm_records") or [])
                import stat as _stat
                original_mode = _stat.S_IMODE(job_dir.stat().st_mode)
                persist_failed = None
                try:
                    job_dir.chmod(0o500)
                    persist = client.post(
                        f"/api/jobs/{job_id}/confirm-report",
                        json={"expected_analysis_revision": revision_r2,
                              "idempotency_key": fail_key})
                    persist_failed = persist.status_code
                    persist_detail = persist.json().get("detail") if persist.status_code != 200 else None
                finally:
                    job_dir.chmod(original_mode)
                after_fail = client.get(f"/api/jobs/{job_id}/report-confirmation")
                after_fail.raise_for_status()
                records_after_fail = list(after_fail.json().get("confirm_records") or [])
                fail_key_records = [r for r in records_after_fail
                                    if r.get("idempotency_key") == fail_key]
                # 门禁在重分析后必须已打开（阻塞性义务由程序完成，非人工代算）
                steps.step("S4b_persist_failure_is_transactional",
                           truth_r2["ok"] and can_confirm
                           and persist_failed == 500
                           and not fail_key_records
                           and len(records_after_fail) == len(records_before),
                           {"truth": truth_r2, "can_confirm": can_confirm,
                            "persist_http": persist_failed,
                            "persist_detail": persist_detail,
                            "records_before": len(records_before),
                            "records_after": len(records_after_fail),
                            "failed_key_records": len(fail_key_records)})

                # ---- S4c 成功签认：版本绑定 + 稳定问题 ID + 幂等 + 并发 ----
                confirm_key = f"ok-{secrets.token_hex(4)}"
                body = {"expected_analysis_revision": revision_r2,
                        "idempotency_key": confirm_key}
                first = client.post(f"/api/jobs/{job_id}/confirm-report", json=body)
                repeat = client.post(f"/api/jobs/{job_id}/confirm-report", json=body)
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    concurrent_results = list(pool.map(
                        lambda _: client.post(f"/api/jobs/{job_id}/confirm-report",
                                              json={"expected_analysis_revision": revision_r2,
                                                    "idempotency_key": confirm_key}),
                        range(3)))
                readback = client.get(f"/api/jobs/{job_id}/report-confirmation")
                readback.raise_for_status()
                readback_body = readback.json()
                records = readback_body.get("confirm_records") or []
                key_records = [r for r in records if r.get("idempotency_key") == confirm_key]
                record = first.json().get("record") or {} if first.status_code == 200 else {}
                expected_confirmed_ids = [
                    str(item.get("id")) for item in
                    (gate_body_r2.get("categories", {}).get("confirmed_material_issues") or [])
                    if item.get("id")
                ]
                steps.step("S4c_confirm_success_idempotent_concurrent",
                           first.status_code == 200
                           and first.json().get("idempotent") is False
                           and record.get("analysis_revision") == revision_r2
                           and record.get("confirmed_issue_ids") == expected_confirmed_ids
                           and repeat.status_code == 200
                           and repeat.json().get("idempotent") is True
                           and repeat.json().get("record") == record
                           and all(r.status_code == 200 for r in concurrent_results)
                           and len(key_records) == 1
                           and readback_body.get("latest_confirm_record") == record,
                           {"first_http": first.status_code,
                            "repeat_http": repeat.status_code,
                            "concurrent": [r.status_code for r in concurrent_results],
                            "record_revision": record.get("analysis_revision"),
                            "confirmed_count": record.get("confirmed_count"),
                            "expected_confirmed_count": len(expected_confirmed_ids),
                            "records_for_key": len(key_records),
                            "latest_matches": readback_body.get("latest_confirm_record") == record,
                            "excluded": record.get("excluded")})

                # ---- S4d 版本变化：旧页面（旧 revision）确认必须 409 ----
                stale = client.post(
                    f"/api/jobs/{job_id}/confirm-report",
                    json={"expected_analysis_revision": revision_r1,
                          "idempotency_key": f"stale-{secrets.token_hex(4)}"})
                stale_detail = (stale.json().get("detail")
                                if stale.status_code != 200 else None)
                stale_records = client.get(f"/api/jobs/{job_id}/report-confirmation")
                stale_records.raise_for_status()
                records_on_r1 = [
                    r for r in (stale_records.json().get("confirm_records") or [])
                    if r.get("analysis_revision") == revision_r1]
                steps.step("S4d_stale_revision_rejected",
                           revision_r1 != revision_r2
                           and stale.status_code == 409
                           and isinstance(stale_detail, dict)
                           and stale_detail.get("error") == "REVISION_CHANGED"
                           and stale_detail.get("expected") == revision_r1
                           and stale_detail.get("current") == revision_r2
                           and not records_on_r1,
                           {"revision_old": revision_r1,
                            "revision_current": revision_r2,
                            "stale_http": stale.status_code,
                            "stale_detail": stale_detail,
                            "records_on_old_revision": len(records_on_r1)})

                # ---- S5 复核生命周期：开始幂等 → 完成门禁先如实阻塞 →
                #      逐条核对后才作决定 → 完成 200 → 重开 200 → 再完成 200 ----
                review_by_job = client.get("/api/reviews", params={"job_uuid": job_id})
                review_by_job.raise_for_status()
                review_data = review_by_job.json().get("data") or {}
                context = review_data.get("review_context") or {}
                slot_id = (review_data.get("review") or {}).get("slot_id")
                if not context.get("available") or not slot_id:
                    raise AssertionError(
                        f"复核上下文不可用: {json.dumps(context, ensure_ascii=False)[:400]}")
                start_a = client.post(f"/api/reviews/{slot_id}/start", json={"job_uuid": job_id})
                start_b = client.post(f"/api/reviews/{slot_id}/start", json={"job_uuid": job_id})
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    starts = list(pool.map(
                        lambda _: client.post(f"/api/reviews/{slot_id}/start",
                                              json={"job_uuid": job_id}), range(2)))

                def _sid(resp):
                    return (((resp.json().get("data") or {}).get("session") or {})
                            .get("review_session_id"))

                same_session = (start_a.status_code == 200 and start_b.status_code == 200
                                and _sid(start_a) and _sid(start_a) == _sid(start_b)
                                and all(s.status_code == 200 for s in starts))
                lifecycle = client.get(f"/api/reviews/{slot_id}", params={"job_uuid": job_id})
                lifecycle.raise_for_status()
                lifecycle_data = lifecycle.json().get("data") or {}
                basis_token = ((lifecycle_data.get("current_analysis") or {})
                               .get("analysis_basis_token")) or ""
                session = (lifecycle_data.get("current_session") or {})
                session_token = session.get("analysis_basis_token") or basis_token
                steps.step("S5d_start_idempotent", same_session,
                           {"start_a": start_a.status_code, "start_b": start_b.status_code,
                            "starts": sorted(s.status_code for s in starts),
                            "session_present": bool(_sid(start_a))})

                # 完成门禁在任何决定之前必须如实阻塞（不允许"零决定也完成"）
                pre_complete = client.post(f"/api/reviews/{slot_id}/complete", json={})
                pre_blockers = []
                if pre_complete.status_code == 409:
                    detail = pre_complete.json().get("detail")
                    pre_blockers = (detail or {}).get("blockers") if isinstance(detail, dict) else []
                # 与门禁同源：正式问题集合（无决定 → 必须全部待处理）。
                # review_problem_set 复用服务端模块（含 issue_workflow_store），
                # 导入 api.runtime 需要同一套安全/目录配置；只在本验收进程
                # 设置与隔离栈一致的环境，不改服务端状态。
                os.environ.update(
                    GOVBUDGET_AUTH_ENABLED="true",
                    GOVBUDGET_API_KEY=api_key,
                    USER_SESSION_SECRET=session_secret,
                    UPLOAD_DIR=env["UPLOAD_DIR"],
                    USER_DATA_DIR=env["USER_DATA_DIR"],
                    ORG_DATA_DIR=env["ORG_DATA_DIR"],
                    DATABASE_URL=dsn,
                    TESTING="false",
                )
                ingest = client.get(f"/api/jobs/{job_id}/structured-ingest")
                ingest.raise_for_status()
                structured_ingest = ingest.json() or {}
                problems = review_problem_set(job_id, result_r2, structured_ingest)
                formal_problems = [p for p in problems if p.is_blocking_candidate]
                formal_from_rule_findings = [
                    p for p in formal_problems if p.source == "finding"]
                structured_formal = [
                    p for p in formal_problems if p.source != "finding"]

                # 逐条决定：只有在本脚本已核对原件（且正式问题对应 finding
                # 的证据 verified、页码可定位）之后才作 confirmed；否则保持
                # 未决。结构化待复核项不在确定性证据覆盖内，一律未决。
                grounded_ids = {
                    str(finding.get("id")) for finding
                    in _dedupe_findings(_iter_findings(result_r2))
                    if str(finding.get("id") or "").strip()
                    and _finding_evidence_status(finding) == "verified"
                    and str(finding.get("severity") or "").strip().lower() != "manual_review"
                    and _positive_int(((finding.get("location") or {}).get("page"))
                                      or finding.get("page")) is not None
                }
                decisions, decision_log, undecided = [], [], []
                for problem in structured_formal:
                    undecided.append({"issue_id": problem.issue_id,
                                      "reason": "structured_review_requires_user",
                                      "severity": problem.severity})
                for problem in formal_from_rule_findings:
                    candidates = {problem.issue_id, *problem.member_issue_ids}
                    if not (candidates & grounded_ids):
                        undecided.append({"issue_id": problem.issue_id,
                                          "reason": "not_grounded_by_original_evidence",
                                          "severity": problem.severity})
                        continue
                    answer = client.post("/api/workflow", json={
                        "action": "update_issue", "job_id": job_id,
                        "issue_id": problem.issue_id, "status": "confirmed",
                        "note": ("D4 验收脚本以复核人身份核对原件后确认：证据页码与原文命中"
                                 "见 S3b 覆盖清单；规则=" + str(problem.severity))})
                    decision_log.append({"issue_id": problem.issue_id,
                                         "http": answer.status_code,
                                         "severity": problem.severity})
                    if answer.status_code == 200:
                        decisions.append(problem.issue_id)
                # 结构化待复核项（若有）不在 S3b 证据覆盖内：如实记未决，
                # 不能由脚本代替用户判定。
                remaining = [p.issue_id for p in formal_problems
                             if p.issue_id not in set(decisions)]
                complete_after = client.post(f"/api/reviews/{slot_id}/complete", json={})
                complete_blockers_after = []
                if complete_after.status_code == 409:
                    detail = complete_after.json().get("detail")
                    complete_blockers_after = ((detail or {}).get("blockers")
                                               if isinstance(detail, dict) else [])

                reopen_status, re_complete_status = None, None
                refreshed_session = {}
                if complete_after.status_code == 200:
                    reopened = client.post(f"/api/reviews/{slot_id}/reopen", json={})
                    reopen_status = reopened.status_code
                    refreshed = client.get(f"/api/reviews/{slot_id}",
                                           params={"job_uuid": job_id})
                    refreshed.raise_for_status()
                    refreshed_session = ((refreshed.json().get("data") or {})
                                         .get("current_session") or {})
                    # 重开后再完成：本版本上已有全部决定，必须成功 200
                    # （旧验收器允许 409 也算通过，判据被 Codex 判为不足）
                    re_complete = client.post(f"/api/reviews/{slot_id}/complete", json={})
                    re_complete_status = re_complete.status_code
                steps.step("S5_review_lifecycle_decisions",
                           pre_complete.status_code == 409
                           and bool(pre_blockers)
                           and not undecided
                           and complete_after.status_code == 200
                           and reopen_status == 200
                           and refreshed_session.get("status") in {"in_progress", None}
                           and re_complete_status == 200,
                           {"pre_complete_http": pre_complete.status_code,
                            "pre_blockers": str(pre_blockers)[:300],
                            "formal_problems": len(formal_problems),
                            "decided": len(decisions), "undecided": undecided,
                            "decisions_sample": decision_log[:5],
                            "complete_after_http": complete_after.status_code,
                            "complete_after_blockers": str(complete_blockers_after)[:300],
                            "reopen_http": reopen_status,
                            "recomplete_http": re_complete_status,
                            "refreshed_session_status": refreshed_session.get("status")})

                # ---- S6 导出：四格式非空、重复一致、成员与当前分析一致、
                #      绑定当前版本 ----
                export_sizes, export_ok, repeat_identical = {}, True, True
                exported_json = None
                for suffix in ("json", "csv", "docx", "pdf"):
                    first_export = get_report_with_backoff(
                        client, params={"job_id": job_id, "format": suffix})
                    first_export.raise_for_status()
                    again = get_report_with_backoff(
                        client, params={"job_id": job_id, "format": suffix})
                    again.raise_for_status()
                    if first_export.content != again.content:
                        repeat_identical = False
                    export_sizes[suffix] = len(first_export.content)
                    if not first_export.content:
                        export_ok = False
                    if suffix == "json":
                        exported_json = json.loads(first_export.content)
                current_status = client.get(f"/api/jobs/{job_id}/status")
                current_status.raise_for_status()
                current_payload = current_status.json()
                current_revision = current_payload.get("analysis_revision")
                current_result = _json_result(current_payload)
                review_report = (exported_json or {}).get("review_report") or {}
                exported_issues = [item for item in (review_report.get("issues") or [])
                                   if isinstance(item, dict)]
                from src.schemas.review_report import _is_confirmed_material_finding

                expected_export_ids = {
                    str(finding.get("id"))
                    for finding in _dedupe_findings(_iter_findings(current_result))
                    if finding.get("id") and _is_confirmed_material_finding(finding)
                }
                exported_ids = {str(item.get("id")) for item in exported_issues
                                if item.get("id")}
                membership_ok = exported_ids == expected_export_ids
                # 导出中的每条问题都必须带可定位页（page=None 不算）
                export_pages_ok = all(
                    _positive_int(((item.get("location") or {}).get("page"))
                                  or item.get("page")) is not None
                    for item in exported_issues)
                revision_binding = bool(exported_json) and (
                    exported_json.get("analysis_revision") == current_revision)
                steps.step("S6_export_membership",
                           export_ok and repeat_identical and membership_ok
                           and export_pages_ok and revision_binding,
                           {"sizes": export_sizes,
                            "repeat_identical": repeat_identical,
                            "exported_issues": len(exported_issues),
                            "expected_export_issues": len(expected_export_ids),
                            "membership_ok": membership_ok,
                            "export_pages_ok": export_pages_ok,
                            "revision_binding": revision_binding,
                            "missing_from_export": sorted(expected_export_ids - exported_ids)[:10],
                            "extra_in_export": sorted(exported_ids - expected_export_ids)[:10]})

                # ---- S7 阻塞分支（真实配置负控）：AI 被配置为必需但不可用时，
                #      AI 义务必须成为阻塞项，整份确认必须 409 且不落记录。
                #      做法：停掉当前 worker，换一个 AI_ASSIST_REQUIRED=true 的
                #      worker 处理第二个真实任务；不伪造任何结果结构。 ----
                worker.terminate()
                try:
                    worker.wait(timeout=30)
                except Exception:  # noqa: BLE001
                    worker.kill()
                worker_strict = _sp.Popen(
                    [sys.executable, "-m", "api.worker"],
                    cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "worker",
                                   "AI_ASSIST_REQUIRED": "true"},
                    stdout=(out / "worker_ai_required.log").open("w"),
                    stderr=_sp.STDOUT,
                )
                processes.append(worker_strict)
                time.sleep(2)
                # 上传带校验和去重：同一原件二次上传会被 409 拒绝（产品行为）。
                # 为验证"必需 AI 未执行"的阻塞分支，这里用**同内容字节副本**
                # （仅在文件尾追加 PDF 注释、不改页面/文本）绕过去重；原件不变，
                # 解析结果与首份一致。回执中如实记录该副本的存在与用途。
                control_pdf = work / f"ai-required-control-{pdf_path.name}"
                control_pdf.write_bytes(
                    pdf_path.read_bytes() + b"\n% D4 AI-required control copy\n")
                with control_pdf.open("rb") as file:
                    strict_upload = request_with_backoff(
                        client, "POST", "/api/documents/upload",
                        files={"file": (pdf_path.name, file, "application/pdf")})
                strict_upload.raise_for_status()
                strict_job = strict_upload.json()["job_id"]
                request_with_backoff(
                    client, "POST", f"/api/analyze/{strict_job}",
                    json={"mode": "legacy", "use_ai_assist": False,
                          "use_local_rules": True}).raise_for_status()
                strict_payload = _wait_analysis(client, strict_job)
                strict_result = _json_result(strict_payload)
                strict_revision = strict_payload.get("analysis_revision")
                strict_gate = client.get(f"/api/jobs/{strict_job}/report-confirmation")
                strict_gate.raise_for_status()
                strict_body = strict_gate.json()
                strict_truth = gate_truth_check(strict_body, strict_result)
                strict_post = client.post(
                    f"/api/jobs/{strict_job}/confirm-report",
                    json={"expected_analysis_revision": strict_revision,
                          "idempotency_key": f"strict-{secrets.token_hex(4)}"})
                strict_after = client.get(f"/api/jobs/{strict_job}/report-confirmation")
                strict_after.raise_for_status()
                strict_records = strict_after.json().get("confirm_records") or []
                strict_instances = ((strict_result.get("meta") or {}).get(
                    "obligation_coverage") or {}).get("instances") or []
                ai_instance = next(
                    (item for item in strict_instances
                     if item.get("obligation_id") == "OBL-AI-SEMANTIC-REVIEW"), {})
                steps.step("S7_required_ai_not_run_blocks_confirm",
                           bool(strict_revision)
                           and (strict_body.get("gate") or {}).get(
                               "can_confirm_whole_report") is False
                           and int((strict_body.get("gate") or {}).get(
                               "blocking_system_incomplete") or 0) >= 1
                           and ai_instance.get("status") == "ai_not_run"
                           and ai_instance.get("blocks_gate") is True
                           and strict_truth["ok"]
                           and strict_post.status_code == 409
                           and strict_post.json().get("detail") == "SYSTEM_INCOMPLETE"
                           and not strict_records,
                           {"job_id": strict_job,
                            "revision": strict_revision,
                            "control_input": "same-content byte copy (PDF comment appended)",
                            "gate": strict_body.get("gate"),
                            "counts": strict_body.get("counts"),
                            "ai_instance": {"status": ai_instance.get("status"),
                                            "blocks_gate": ai_instance.get("blocks_gate")},
                            "truth_ok": strict_truth["ok"],
                            "truth_problems": strict_truth["problems"],
                            "post_http": strict_post.status_code,
                            "post_detail": strict_post.json().get("detail"),
                            "records": len(strict_records)})

                receipt["elapsed_seconds"] = round(time.monotonic() - started, 1)
                receipt["conclusion"] = {
                    "steps_passed": len(steps.records),
                    "confirmed_issue_ids": record.get("confirmed_issue_ids"),
                    "excluded": record.get("excluded"),
                    "scripted_reviewer_decisions": decisions,
                    "note": (
                        "本回执验证链路机制：证据逐条回原件核对、门禁如实阻塞/放行、"
                        "确认写侧版本绑定/幂等/事务失败、刷新读回、复核完成/重开、"
                        "导出成员与版本一致。复核决定由脚本以复核人身份按原件核对后"
                        "作出（逐条依据见 S3b 覆盖与 decisions），不计为程序自动完成，"
                        "也不构成人工工作量下降的证据。"
                    ),
                }
                print("D4 真实链路验收：全部步骤 PASS", flush=True)
    except AssertionError as exc:
        receipt["failure"] = str(exc)
        raise
    finally:
        receipt["steps"] = steps.records
        (out / "d4_receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def docker_exec_ready(name: str) -> bool:
    return subprocess.run(
        ["docker", "exec", name, "pg_isready", "-U", "validator", "-d", "validation"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


if __name__ == "__main__":
    main()
