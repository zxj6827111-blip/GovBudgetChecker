#!/usr/bin/env python3
"""A 步真实链路验收：第 2 份 Decimal 修复后的上传/落库/读回/导出/重分析/旧 basis 拒绝。

只读候选 API 与隔离 PG，不写业务库以外的任何环境。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx

RUN = Path(__file__).resolve().parents[1]
OUT = RUN / "evidence" / "a_chain"
OUT.mkdir(parents=True, exist_ok=True)

state = json.loads((RUN / "run_state.json").read_text(encoding="utf-8"))
item = state["items"]["2"]
job_id = item["job_id"]
base = f"http://127.0.0.1:{state['api_port']}"
container = state["container"]
uploads = RUN / "db_env" / "uploads"

headers = {"X-API-Key": state["api_key"]}
login = httpx.post(f"{base}/api/auth/login", headers=headers,
                   json={"username": "admin", "password": state["admin_password"]}, timeout=30)
login.raise_for_status()
auth = {**headers, "X-Session-Token": login.json()["token"]}

report: dict = {"job_id": job_id, "steps": {}}


def psql(sql: str) -> str:
    res = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "validator", "-d", "validation", "-tAc", sql],
        capture_output=True, text=True, timeout=60,
    )
    if res.returncode:
        raise RuntimeError(res.stderr.strip())
    return res.stdout.strip()


def current_revision() -> str | None:
    pointer = uploads / job_id / "analysis_versions" / "current.json"
    if pointer.exists():
        return str(json.loads(pointer.read_text(encoding="utf-8")).get("revision_id") or "") or None
    return None


# 1) 当前状态与 revision
st = httpx.get(f"{base}/api/jobs/{job_id}/status", headers=auth, timeout=60)
st.raise_for_status()
status_payload = st.json()
rev1 = status_payload.get("analysis_revision") or current_revision()
report["steps"]["status"] = {
    "http": st.status_code,
    "status": status_payload.get("status"),
    "quality_status": status_payload.get("quality_status"),
    "analysis_revision": rev1,
    "error": status_payload.get("error"),
}

# 2) 不可变结果与快照确实落库且当前指针指向它
row = psql(
    "SELECT (SELECT count(*) FROM analysis_revision_results r WHERE r.job_uuid='%s'),"
    "(SELECT publication_state FROM analysis_fact_snapshots s WHERE s.job_uuid='%s' AND s.revision_id='%s'),"
    "(SELECT current_fact_revision_id FROM analysis_jobs j WHERE j.job_uuid='%s');"
    % (job_id, job_id, rev1, job_id)
)
count, publication_state, current = (row.split("|") + ["", "", ""])[:3]
report["steps"]["db"] = {
    "revision_results": int(count or 0),
    "snapshot_publication_state": publication_state,
    "current_fact_revision_id": current,
    "current_matches_active": current == rev1,
}

# 3) 导出与当前 revision 一致
with httpx.Client(timeout=180) as client:
    rj = client.get(f"{base}/api/reports/download", params={"job_id": job_id, "format": "json"}, headers=auth)
    rc = client.get(f"{base}/api/reports/download", params={"job_id": job_id, "format": "csv"}, headers=auth)
(OUT / "report.json").write_text(rj.text)
(OUT / "report.csv").write_bytes(rc.content)
issues = []
try:
    body = rj.json()
    issues = (body.get("issues") or {}).get("all") if isinstance(body.get("issues"), dict) else body.get("issues") or []
except Exception:
    body = {}
report["steps"]["exports"] = {
    "json_status": rj.status_code,
    "csv_status": rc.status_code,
    "json_issues": len(issues or []),
    "json_analysis_revision": body.get("analysis_revision") if isinstance(body, dict) else None,
    "csv_bytes": len(rc.content),
    "csv_has_utf8_bom": rc.content[:3] == b"\xef\xbb\xbf",
}

# 4) 旧 revision 可读（重分析前）
old = httpx.get(f"{base}/api/jobs/{job_id}", params={"analysis_revision": rev1}, headers=auth, timeout=120)
report["steps"]["old_revision_before"] = {
    "http": old.status_code,
    "analysis_revision": (old.json() or {}).get("analysis_revision") if old.status_code == 200 else None,
}

# 5) 重分析：真实第二份必须能再次提交并产生新 revision
token1 = f"{job_id}:{rev1}"
re = httpx.post(f"{base}/api/jobs/{job_id}/reanalyze", headers=auth,
                json={"mode": "dual", "use_local_rules": True, "use_ai_assist": False}, timeout=120)
report["steps"]["reanalyze_request"] = {"http": re.status_code, "body": re.text[:300]}
final = None
if re.status_code == 200:
    start = time.monotonic()
    while time.monotonic() - start < 900:
        s = httpx.get(f"{base}/api/jobs/{job_id}/status", headers=auth, timeout=60)
        if s.status_code == 200:
            p = s.json()
            if p.get("status") in {"error", "failed", "review_required", "completed"} and not p.get("stage_progress"):
                final = p
                break
            if p.get("status") in {"error", "failed", "review_required", "completed"}:
                final = p
                break
        time.sleep(3)
report["steps"]["reanalyze_result"] = {
    "status": (final or {}).get("status"),
    "error": (final or {}).get("error"),
    "analysis_revision": (final or {}).get("analysis_revision"),
    "elapsed_seconds": round(time.monotonic() - start, 1) if re.status_code == 200 else None,
}
rev2 = (final or {}).get("analysis_revision") or current_revision()
report["steps"]["reanalyze_result"]["revision_changed"] = bool(rev2 and rev1 and rev2 != rev1)

# 6) 旧 revision 仍可读（重分析后）
old2 = httpx.get(f"{base}/api/jobs/{job_id}", params={"analysis_revision": rev1}, headers=auth, timeout=120)
report["steps"]["old_revision_after"] = {
    "http": old2.status_code,
    "analysis_revision": (old2.json() or {}).get("analysis_revision") if old2.status_code == 200 else None,
}

# 7) 旧 basis 写回必须拒绝（409 analysis_basis_changed）
stale = httpx.post(
    f"{base}/api/jobs/{job_id}/corrections", headers=auth,
    json={
        "analysis_basis_token": token1,
        "changes": [{"field": "amount", "previous_value": "0", "value": "1",
                     "evidence_ids": ["stale-basis-probe"]}],
        "reason": "旧分析代际写回拒绝探针",
    },
    timeout=60,
)
try:
    detail = stale.json().get("detail")
except Exception:
    detail = stale.text[:300]
report["steps"]["stale_basis_writeback"] = {
    "http": stale.status_code,
    "detail": detail,
    "rejected": stale.status_code == 409,
}

report["completed"] = (
    report["steps"]["db"]["current_matches_active"]
    and report["steps"]["exports"]["json_status"] == 200
    and report["steps"]["exports"]["csv_status"] == 200
    and report["steps"]["reanalyze_result"]["status"] not in {"error", "failed", None}
    and report["steps"]["reanalyze_result"]["revision_changed"]
    and report["steps"]["old_revision_before"]["http"] == 200
    and report["steps"]["old_revision_after"]["http"] == 200
    and report["steps"]["stale_basis_writeback"]["rejected"]
)
(OUT / "a_chain_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
