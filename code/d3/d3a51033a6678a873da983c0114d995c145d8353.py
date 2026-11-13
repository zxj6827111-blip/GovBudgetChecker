import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path("/Users/zhangxingjin/gbc-acceptance-r3/.env"))
BASE = "http://127.0.0.1:18745"
OUT = Path(__file__).resolve().parent
MAIN = Path("/Volumes/workSSD/software development/GovBudgetChecker")
SAMPLES = {
    "S1": (
        MAIN / "uploads/933e79a25e74b58365580b45f5df61ad/上海市文化和旅游局执法总队2022年度部门决算.pdf",
        "2022",
        "dept_final",
        "机关事业单位基本养老保险缴费支出表文金额不一致",
    ),
    "S2": (
        MAIN / "uploads/2dc9c68127b0808d7661de536e2e6616/上海虹桥国际中央商务区管理委员会.pdf",
        "2024",
        "dept_final",
        "说明缺少实质性原因",
    ),
    "S3": (
        MAIN / "uploads/5c9899d5f9d57bff5f49a35b82514203/上海市文化和旅游局2022年度部门决算.pdf",
        "2022",
        "dept_final",
        "文字表述疑似错误",
    ),
}

client = httpx.Client(timeout=180)
api_key = os.getenv("GOVBUDGET_API_KEY", "")
if api_key:
    client.headers["X-API-Key"] = api_key
login = client.post(BASE + "/api/auth/login", json={"username": "admin", "password": "admin123"})
login.raise_for_status()
token = login.json()["token"]
client.headers["X-Session-Token"] = token
summary = {}

for sid, (path, year, doc_type, target_title) in SAMPLES.items():
    record = {"sample": sid, "pdf": str(path), "pdf_sha256": None, "year": year}
    try:
        import hashlib
        record["pdf_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open("rb") as handle:
            response = client.post(
                BASE + "/api/documents/upload",
                files={"file": (path.name, handle, "application/pdf")},
                data={"fiscal_year": year, "doc_type": doc_type},
            )
        response.raise_for_status()
        upload = response.json()
        job = upload["job_id"]
        record["job_id"] = job
        record["upload"] = upload
        (OUT / f"{sid}_job.json").write_text(
            json.dumps({"job_id": job, "upload": upload}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        analyze = client.post(
            BASE + f"/api/analyze/{job}",
            json={"mode": "dual", "use_ai_assist": True, "use_local_rules": True},
        )
        record["analyze_status"] = analyze.status_code
        record["analyze_body"] = analyze.text[:500]
        analyze.raise_for_status()

        deadline = time.time() + 1800
        status = {}
        while time.time() < deadline:
            time.sleep(10)
            status_response = client.get(BASE + f"/api/jobs/{job}/status")
            status_response.raise_for_status()
            status = status_response.json()
            state = status.get("status")
            print(sid, "state", state, status.get("progress"), status.get("stage"), flush=True)
            if state in {"done", "review_required", "degraded", "error", "failed"}:
                break
        else:
            record["error"] = "timeout"
        (OUT / f"{sid}_status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        detail = client.get(BASE + f"/api/jobs/{job}")
        if detail.status_code == 200:
            (OUT / f"{sid}_detail.json").write_text(
                json.dumps(detail.json(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        csv_response = client.get(BASE + f"/api/reports/download?job_id={job}&format=csv")
        record["csv_status"] = csv_response.status_code
        if csv_response.status_code == 200:
            (OUT / f"{sid}_report.csv").write_bytes(csv_response.content)

        result = status.get("result") or {}
        meta = result.get("meta") or {}
        execution = meta.get("ai_execution") or {}
        coverage = meta.get("ai_coverage") or {}
        record.update(
            {
                "terminal_state": status.get("status"),
                "quality_status": status.get("quality_status"),
                "analysis_conclusion": status.get("analysis_conclusion"),
                "conclusion_scope": status.get("conclusion_scope") or meta.get("conclusion_scope"),
                "ai_state": execution.get("state"),
                "ai_attempts": execution.get("attempts"),
                "coverage_status": coverage.get("status"),
                "planned_calls": coverage.get("planned_calls"),
                "succeeded_calls": coverage.get("succeeded_calls"),
                "failed_calls": coverage.get("failed_calls"),
                "retried_calls": coverage.get("retried_calls"),
                "failed_units": coverage.get("failed_units"),
                "uncovered_ranges": coverage.get("uncovered_ranges"),
                "ai_findings": len(result.get("ai_findings") or []),
                "rule_findings": len(result.get("rule_findings") or []),
                "target_title": target_title,
                "target_ai": [
                    item.get("id")
                    for item in (result.get("ai_findings") or [])
                    if target_title in str(item.get("title") or item.get("message") or "")
                ],
            }
        )
        trace = Path(status.get("result", {}).get("meta", {}).get("ai_trace_path") or "")
        job_dir = Path(status.get("saved_path") or "")
        record["job_saved_path"] = str(job_dir)
        print(sid, "DONE", json.dumps({k: record.get(k) for k in (
            "terminal_state", "ai_state", "ai_attempts", "coverage_status",
            "planned_calls", "succeeded_calls", "failed_calls", "retried_calls",
            "ai_findings", "rule_findings", "target_ai", "csv_status")}, ensure_ascii=False), flush=True)
    except Exception as exc:  # keep going to collect other samples
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(sid, "ERROR", record["error"], flush=True)
    summary[sid] = record
    (OUT / "acceptance3_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

print("ALL DONE", json.dumps(summary, ensure_ascii=False)[:4000], flush=True)
