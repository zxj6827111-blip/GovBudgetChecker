import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path("/Users/zhangxingjin/gbc-acceptance-r3/.env"))
BASE = "http://127.0.0.1:18745"
OUT = Path(__file__).resolve().parent
PDF = Path("/Volumes/workSSD/software development/GovBudgetChecker/uploads/5c9899d5f9d57bff5f49a35b82514203/上海市文化和旅游局2022年度部门决算.pdf")

client = httpx.Client(timeout=180)
if os.getenv("GOVBUDGET_API_KEY"):
    client.headers["X-API-Key"] = os.getenv("GOVBUDGET_API_KEY", "")
login = client.post(BASE + "/api/auth/login", json={"username": "admin", "password": "admin123"})
login.raise_for_status()
client.headers["X-Session-Token"] = login.json()["token"]

with PDF.open("rb") as handle:
    uploaded = client.post(
        BASE + "/api/documents/upload",
        files={"file": (PDF.name, handle, "application/pdf")},
        data={"fiscal_year": "2022", "doc_type": "dept_final"},
    )
uploaded.raise_for_status()
job = uploaded.json()["job_id"]
(OUT / "NEG_rule_job.json").write_text(json.dumps(uploaded.json(), ensure_ascii=False, indent=2), encoding="utf-8")
analysis = client.post(
    BASE + f"/api/analyze/{job}",
    json={"mode": "dual", "use_ai_assist": False, "use_local_rules": True},
)
analysis.raise_for_status()
status = {}
for _ in range(180):
    time.sleep(5)
    status = client.get(BASE + f"/api/jobs/{job}/status").json()
    print("state", status.get("status"), status.get("progress"), status.get("stage"), flush=True)
    if status.get("status") in {"done", "review_required", "degraded", "error", "failed"}:
        break
(OUT / "NEG_rule_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
result = status.get("result") or {}
meta = result.get("meta") or {}
csv = client.get(BASE + f"/api/reports/download?job_id={job}&format=csv")
if csv.status_code == 200:
    (OUT / "NEG_rule_report.csv").write_bytes(csv.content)
print(json.dumps({
    "job_id": job,
    "status": status.get("status"),
    "use_ai_assist": status.get("use_ai_assist"),
    "ai_execution": {k: (meta.get("ai_execution") or {}).get(k) for k in ("requested", "state")},
    "ai_coverage": meta.get("ai_coverage"),
    "ai_findings": len(result.get("ai_findings") or []),
    "rule_findings": len(result.get("rule_findings") or []),
    "conclusion_scope": meta.get("conclusion_scope") or status.get("conclusion_scope"),
    "csv_status": csv.status_code,
}, ensure_ascii=False, indent=2), flush=True)
