"""E1：独立数据库、组织副本、API/Worker/Next 的真实上传至导出验证。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_isolated_service_validation import (
    database_receipt,
    docker,
    port,
    prepare_db,
    service_lifetime,
    structured_ingest_from_payload,
    structured_ingest_validation,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def source_fingerprints():
    paths = subprocess.check_output([
        "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
        "src", "api", "config", "rules", "scripts", "app",
    ], cwd=ROOT).decode().split("\0")
    return {path: sha(ROOT / path) for path in sorted(set(paths)) if path
            and (ROOT / path).is_file() and Path(path).suffix in {
                ".py", ".ts", ".tsx", ".js", ".cjs", ".json", ".yaml", ".yml"}}


def check_e1_synthetic_receipts(status):
    """检查真实任务回执，禁止用 HTTP 200 或“有规则运行”替代关系级真值。"""
    receipts = ((status.get("result") or {}).get("meta") or {}).get("fact_receipts") or []
    by_id = {record.get("sub_check_id"): record for record in receipts if record.get("rule_id") == "V33-240"}
    for code in ("205", "20502"):
        assert by_id.get(f"FIN_02_income:{code}:income_sources", {}).get("status") == "pass", "应通过关系未实际执行"
    for code in ("total", "208"):
        assert by_id.get(f"FIN_02_income:{code}:income_sources", {}).get("status") == "fail", "错误关系未实际报错"
    vertical = []
    for code in ("total", "205", "208"):
        for role in ("total_actual", "fiscal_allocation", "superior_subsidy_income", "business_income",
                     "operational_income", "subsidiary_income", "other_income"):
            key = f"FIN_02_income:{code}:{role}"
            assert by_id.get(key, {}).get("status") == "pass", f"纵向关系未实际执行并通过: {key}"
            vertical.append(key)
    return {"passed": True, "verified_pass_rows": ["205", "20502"], "verified_fail_rows": ["total", "208"],
            "verified_vertical_relations": vertical, "relation_receipts": len(receipts)}


def wait_http(client, path, process, *, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("隔离服务提前退出，请查看日志")
        try:
            answer = client.get(path)
            if answer.status_code < 400:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"隔离服务等待超时: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--org-data-dir", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=Path.home() / "Desktop/决算测试上海市")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    specification = ROOT / "scripts/intake_remediation_e1.json"
    spec = json.loads(specification.read_text())
    samples = []
    for item in spec["samples"]:
        path = (
            (ROOT / item["file"])
            if item["file"].startswith("outputs/")
            else args.corpus / item["file"]
        )
        if sha(path) != item["sha256"]:
            raise ValueError(f"冻结样本哈希不符: {item['id']}")
        samples.append({**item, "path": path})
    work = out / "instance"
    work.mkdir()
    orgs = work / "orgs"
    orgs.mkdir()
    shutil.copy2(args.org_data_dir / "organizations.json", orgs / "organizations.json")
    save(orgs / "job_org_links.json", {"links": []})
    # 复用已安装依赖；构建缓存与服务状态仍落在独立 worktree / instance。
    modules = ROOT / "app/node_modules"
    if not modules.exists():
        parser.error("当前 worktree 缺少 app/node_modules，请先链接本机已安装依赖")
    name = f"gbc-intake-e1-{uuid.uuid4().hex[:12]}"
    password, admin_password, api_key, session_secret = (secrets.token_hex(24) for _ in range(4))
    pg_env = work / "postgres.env"
    pg_env.write_text(
        f"POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n"
    )
    pg_env.chmod(0o600)
    processes, handles = [], []
    receipt = {
        "status": "running",
        "specification_sha256": sha(specification),
        "samples": [],
        "source_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "org_source_sha256": sha(args.org_data_dir / "organizations.json"),
        "limits": ["仅验证冻结 E1 样本，不代表全语料准确率", "不启用生产 U-5 空白计零约定"],
    }
    fingerprints = source_fingerprints()
    save(out / "code-fingerprints.json", fingerprints)
    started_container = False
    try:
        docker(
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--env-file",
            str(pg_env),
            "-p",
            "127.0.0.1::5432",
            "postgres:16",
        )
        started_container = True
        pg_port = int(docker("port", name, "5432/tcp").rsplit(":", 1)[1])
        dsn = f"postgresql://validator:{password}@127.0.0.1:{pg_port}/validation"
        for _ in range(100):
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "validator",
                    "-d",
                    "validation",
                ],
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(0.2)
        asyncio.run(prepare_db(dsn))
        api_port, web_port = port(), port()
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_")):
                env.pop(key)
        env.pop("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", None)
        env.update(
            PYTHON_DOTENV_DISABLED="1",
            TESTING="false",
            DATABASE_URL=dsn,
            GOVBUDGET_AUTH_ENABLED="true",
            GOVBUDGET_API_KEY=api_key,
            USER_SESSION_SECRET=session_secret,
            DEFAULT_ADMIN_PASSWORD=admin_password,
            USER_DATA_DIR=str(work / "users"),
            ORG_DATA_DIR=str(orgs),
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
            BACKEND_URL=f"http://127.0.0.1:{api_port}",
            GOVBUDGET_RATE_LIMIT="10000",
        )

        def start(command, log, role=None, cwd=ROOT):
            handle = (out / log).open("w")
            handles.append(handle)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env={**env, **({"JOB_QUEUE_ROLE": role} if role else {})},
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
            return process

        with service_lifetime(processes, handles):
            api = start(
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
                "api.log",
                "api",
            )
            start([sys.executable, "-m", "api.worker"], "worker.log", "worker")
            web = start(
                [
                    shutil.which("node"),
                    str(modules / "next/dist/bin/next"),
                    "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
                "web.log",
                cwd=ROOT / "app",
            )
            with httpx.Client(
                base_url=env["BACKEND_URL"], headers={"X-API-Key": api_key}, timeout=180
            ) as client:
                wait_http(client, "/health", api)
                login = client.post(
                    "/api/auth/login", json={"username": "admin", "password": admin_password}
                )
                login.raise_for_status()
                client.headers["X-Session-Token"] = login.json()["token"]
                changed_password = secrets.token_hex(24)
                client.post(
                    "/api/auth/change-password",
                    json={"old_password": admin_password, "new_password": changed_password},
                ).raise_for_status()
                login = client.post(
                    "/api/auth/login", json={"username": "admin", "password": changed_password}
                )
                login.raise_for_status()
                client.headers["X-Session-Token"] = login.json()["token"]
                department = client.post(
                    "/api/organizations", json={"name": "上海市E1测试主管局", "level": "department"}
                )
                department.raise_for_status()
                unit = client.post(
                    "/api/organizations",
                    json={
                        "name": "上海市E1测试中心",
                        "level": "unit",
                        "parent_id": department.json()["id"],
                    },
                )
                unit.raise_for_status()
                receipt["org_test_sha256"] = sha(orgs / "organizations.json")
                for item in samples:
                    target = out / item["id"]
                    target.mkdir()
                    with item["path"].open("rb") as handle:
                        preflight = client.post(
                            "/api/documents/preflight",
                            files={"file": (item["path"].name, handle, "application/pdf")},
                        )
                    preflight.raise_for_status()
                    save(target / "preflight.json", preflight.json())
                    with item["path"].open("rb") as handle:
                        upload = client.post(
                            "/api/documents/upload",
                            files={"file": (item["path"].name, handle, "application/pdf")},
                        )
                    upload.raise_for_status()
                    uploaded = upload.json()
                    save(target / "upload.json", uploaded)
                    job = uploaded["job_id"]
                    client.post(
                        f"/api/analyze/{job}",
                        json={"mode": "legacy", "use_local_rules": True, "use_ai_assist": False},
                    ).raise_for_status()
                    deadline = time.monotonic() + args.timeout
                    while time.monotonic() < deadline:
                        response = client.get(f"/api/jobs/{job}/status")
                        response.raise_for_status()
                        status = response.json()
                        if status.get("status") not in {
                            "queued",
                            "processing",
                            "running",
                            "pending",
                        }:
                            break
                        time.sleep(0.5)
                    else:
                        raise TimeoutError(f"E1 分析超时: {item['id']}")
                    save(target / "status.json", status)
                    snapshot = json.loads(
                        (
                            work
                            / "uploads"
                            / job
                            / "fact_snapshots"
                            / (status["analysis_revision"] + ".json")
                        ).read_text()
                    )
                    save(target / "snapshot.json", snapshot)
                    facts = snapshot["effective_context"]["facts"]
                    assert facts and all(fact.get("org_scope") is None for fact in facts), (
                        "不能自动补汇总口径"
                    )
                    ingest = structured_ingest_from_payload(status)
                    db = asyncio.run(database_receipt(dsn, job, ingest))
                    save(target / "database.json", db)
                    storage = structured_ingest_validation(status, db)
                    assert storage["passed"], storage
                    exported = client.get(
                        "/api/reports/download", params={"job_id": job, "format": "json"}
                    )
                    exported.raise_for_status()
                    (target / "report.json").write_bytes(exported.content)
                    serialized = json.dumps(exported.json(), ensure_ascii=False)
                    assert "汇总口径待确认" in serialized
                    assert exported.json()["analysis_revision"] == status["analysis_revision"]
                    summary = status["result"]["meta"]["rule_execution_summary"]
                    assert summary["rule_statuses"]["CMM-018"] == "insufficient_data"
                    assert any(row["rule_id"] == "CMM-018" and "口径" in row["detail"]
                               for row in summary["unresolved_rules"])
                    checks = {"scope_rule_incomplete": True, "same_export_revision": True}
                    if item["id"] == "E1-C":
                        assert uploaded.get("report_year") is None
                        assert all(fact.get("fiscal_year") is None for fact in facts)
                        assert summary["rule_statuses"]["V33-240"] == "insufficient_data"
                        assert not any(issue["rule_id"] == "V33-240" and issue["severity"] in {"error", "warn"}
                                       for issue in status["result"]["issues"]["all"])
                        checks["missing_year_blocks_arithmetic"] = True
                    else:
                        assert uploaded.get("report_year") == 2025
                    if item["id"] in {"E1-R1", "E1-R2"}:
                        baseline = 41 if item["id"] == "E1-R1" else 39
                        assert summary["insufficient_data"] < baseline
                        checks["incomplete_rules"] = summary["insufficient_data"]
                    if item["id"] == "E1-S":
                        assert "10.00" in serialized and "208" in serialized
                        # 真值来自冻结 E1-v1，验证操作数本身及实际 pass/fail 回执。
                        index = {
                            (fact.get("subject_code"), fact["indicator"]): fact for fact in facts
                        }
                        assert str(index[("208", "total_actual")]["normalized_value"]) in {
                            "100",
                            "100.00",
                        }
                        for code, values in {"total": ("300.00", "200.00", "90.00"),
                                             "208": ("100.00", "50.00", "40.00")}.items():
                            from decimal import Decimal
                            terms = [index[(code, role)] for role in ("total_actual", "fiscal_allocation", "business_income")]
                            assert [Decimal(str(term["normalized_value"])) for term in terms] == list(map(Decimal, values))
                            assert all(any(ref.get("physical_page") == 2 and ref.get("source_cell_id")
                                           for ref in term["extra_dims"]["evidence_refs"]) for term in terms)
                        checks["source_values_and_page_verified"] = True
                        save(target / "result.json", status["result"])
                        save(target / "relation-validation.json", check_e1_synthetic_receipts(status))
                    receipt["samples"].append(
                        {
                            "id": item["id"],
                            "job_id": job,
                            "analysis_revision": status["analysis_revision"],
                            "source_sha256": item["sha256"],
                            "storage": storage,
                            "checks": checks,
                        }
                    )
                    save(out / "receipt.json", receipt)
                browser_config = work / "browser-config.json"
                save(
                    browser_config,
                    {
                        "origin": f"http://127.0.0.1:{web_port}",
                        "password": changed_password,
                        "output": str(out),
                        "samples": receipt["samples"],
                    },
                )
                browser_config.chmod(0o600)
                with httpx.Client(base_url=f"http://127.0.0.1:{web_port}", timeout=180) as frontend:
                    wait_http(frontend, "/login", web, seconds=180)
                subprocess.run(
                    [
                        shutil.which("node"),
                        str(ROOT / "scripts/validate_intake_e1_browser.cjs"),
                        str(browser_config),
                    ],
                    cwd=ROOT,
                    check=True,
                    timeout=360,
                )
                browser_config.unlink()
                receipt["browser"] = json.loads((out / "browser-receipt.json").read_text())
                assert source_fingerprints() == fingerprints, "验证期间源码发生变化，不能交付混合版本证据"
                receipt["source_unchanged"] = True
                receipt["status"] = "passed"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if started_container:
            try:
                docker("stop", name)
                receipt["postgres_removed"] = True
            except Exception:
                receipt["postgres_removed"] = False
        pg_env.unlink(missing_ok=True)
        (work / "browser-config.json").unlink(missing_ok=True)
        save(out / "receipt.json", receipt)


if __name__ == "__main__":
    main()
