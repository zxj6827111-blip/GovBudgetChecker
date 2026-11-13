#!/usr/bin/env python3
"""最终链路核验：导出内容、revision 一致、旧 revision 不变、basis 正负控。

对应复审 RF-03(c)。只看实际 HTTP 响应内容，不以 200/字节数/BOM 代替内容核验。
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

import httpx

RUN = Path(__file__).resolve().parents[1]
OUT = RUN / "evidence" / "final_chain"
OUT.mkdir(parents=True, exist_ok=True)

state = json.loads((RUN / "run_state.json").read_text(encoding="utf-8"))
item = state["items"]["2"]
job_id = item["job_id"]
base = f"http://127.0.0.1:{state['api_port']}"
uploads = RUN / "db_env" / "uploads"

headers = {"X-API-Key": state["api_key"]}
login = httpx.post(f"{base}/api/auth/login", headers=headers,
                   json={"username": "admin", "password": state["admin_password"]}, timeout=30)
login.raise_for_status()
auth = {**headers, "X-Session-Token": login.json()["token"]}

report: dict = {"job_id": job_id, "steps": {}}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def current_revision() -> str | None:
    pointer = uploads / job_id / "analysis_versions" / "current.json"
    if pointer.exists():
        return str(json.loads(pointer.read_text(encoding="utf-8")).get("revision_id") or "") or None
    return None


def fetch_all(revision: str | None = None):
    params = {"job_id": job_id}
    if revision:
        params["analysis_revision"] = revision
    with httpx.Client(timeout=180) as client:
        rj = client.get(f"{base}/api/reports/download", params={**params, "format": "json"}, headers=auth)
        rc = client.get(f"{base}/api/reports/download", params={**params, "format": "csv"}, headers=auth)
        rp = client.get(f"{base}/api/reports/download", params={**params, "format": "pdf"}, headers=auth)
        job = client.get(f"{base}/api/jobs/{job_id}", params={"analysis_revision": revision} if revision else None,
                         headers=auth, timeout=180)
    return rj, rc, rp, job


def parse_csv(data: bytes) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    header = lines[0].split(",") if lines else []
    return {"rows": max(0, len(lines) - 1), "columns": len(header), "header_sample": header[:8]}


def parse_pdf(data: bytes) -> dict:
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page_count = doc.page_count
        first = doc[0].get_text() if page_count else ""
        all_text = "".join(page.get_text() for page in doc)
    finally:
        doc.close()
    return {
        "pages": page_count,
        "chars": len(all_text),
        "first_page_has_report_title": ("决算" in first or "报告" in first or "检查" in first),
        "mentions_job": job_id[:8] in all_text,
    }


# 1) 当前 revision 的三类导出
rev_before = current_revision()
rj, rc, rp, job_before = fetch_all()
report["steps"]["exports_before"] = {
    "revision": rev_before,
    "json": {"http": rj.status_code, "sha256": sha_bytes(rj.content), "bytes": len(rj.content)},
    "csv": {"http": rc.status_code, "sha256": sha_bytes(rc.content), **parse_csv(rc.content)},
    "pdf": {"http": rp.status_code, "sha256": sha_bytes(rp.content), **parse_pdf(rp.content)},
    "job_http": job_before.status_code,
    "job_revision": (job_before.json() or {}).get("analysis_revision") if job_before.status_code == 200 else None,
}
(OUT / "export_before.json").write_bytes(rj.content)
(OUT / "export_before.csv").write_bytes(rc.content)
(OUT / "export_before.pdf").write_bytes(rp.content)

json_body = rj.json() if rj.status_code == 200 else {}
raw_issues = json_body.get("issues")
if isinstance(raw_issues, dict):
    issues = list(raw_issues.get("all") or [])
else:
    issues = list(raw_issues or [])
report["steps"]["exports_before"]["review_report_sha256"] = json_body.get("review_report_sha256")
report["steps"]["exports_before"]["json_issue_count"] = len(issues)
report["steps"]["exports_before"]["json_revision_field"] = json_body.get("analysis_revision")
report["steps"]["exports_before"]["json_matches_revision"] = (
    json_body.get("analysis_revision") == rev_before)
report["steps"]["exports_before"]["csv_rows_positive"] = report["steps"]["exports_before"]["csv"]["rows"] > 0
report["steps"]["exports_before"]["pdf_content_positive"] = (
    report["steps"]["exports_before"]["pdf"]["pages"] > 0
    and report["steps"]["exports_before"]["pdf"]["chars"] > 200)

# 1b) 取一条真实证据 ID，供 basis 探针使用
evidence_id = None
for issue in issues:
    for ref in issue.get("evidence") or []:
        if isinstance(ref, dict) and ref.get("evidence_id"):
            evidence_id = ref["evidence_id"]
            break
    if evidence_id:
        break

# 2) 旧 revision 基线哈希（重分析后必须逐字节不变）
old_rj, old_rc, old_rp, old_job = fetch_all(rev_before)
report["steps"]["old_revision_baseline"] = {
    "revision": rev_before,
    "json_sha256": sha_bytes(old_rj.content),
    "pdf_sha256": sha_bytes(old_rp.content),
    "http": old_rj.status_code,
}

# 3b) 取一条真实 fact_uid，供 basis 正负控使用
fact_uid = None
ing = httpx.get(f"{base}/api/jobs/{job_id}/structured-ingest", headers=auth, timeout=120)
if ing.status_code == 200:
    try:
        run_id = ing.json().get("parse_run_id")
        if run_id:
            import subprocess

            out = subprocess.run(
                ["docker", "exec", state["container"], "psql", "-U", "validator", "-d", "validation",
                 "-tAc", f"SELECT fact_uid FROM parse_facts WHERE parse_run_id={int(run_id)} LIMIT 1"],
                capture_output=True, text=True, timeout=60)
            fact_uid = (out.stdout or "").strip() or None
    except Exception:
        fact_uid = None

# 4) 重分析 → 新 revision
re = httpx.post(f"{base}/api/jobs/{job_id}/reanalyze", headers=auth,
                json={"mode": "dual", "use_local_rules": True, "use_ai_assist": False}, timeout=120)
final = None
if re.status_code == 200:
    start = time.monotonic()
    while time.monotonic() - start < 900:
        st = httpx.get(f"{base}/api/jobs/{job_id}/status", headers=auth, timeout=60)
        if st.status_code == 200:
            p = st.json()
            if p.get("status") in {"error", "failed", "review_required", "completed"}:
                final = p
                break
        time.sleep(3)
rev_after = (final or {}).get("analysis_revision") or current_revision()
report["steps"]["reanalyze"] = {
    "http": re.status_code,
    "status": (final or {}).get("status"),
    "error": (final or {}).get("error"),
    "revision_before": rev_before,
    "revision_after": rev_after,
    "revision_changed": bool(rev_before and rev_after and rev_after != rev_before),
}

# 5) 旧 revision 重分析后逐字节不变
old2_rj, old2_rc, old2_rp, old2_job = fetch_all(rev_before)
report["steps"]["old_revision_after"] = {
    "http": old2_rj.status_code,
    "json_sha256": sha_bytes(old2_rj.content),
    "pdf_sha256": sha_bytes(old2_rp.content),
    "json_unchanged": sha_bytes(old2_rj.content) == report["steps"]["old_revision_baseline"]["json_sha256"],
    "pdf_unchanged": sha_bytes(old2_rp.content) == report["steps"]["old_revision_baseline"]["pdf_sha256"],
}

# 6) 旧 basis 写回拒绝
stale = httpx.post(
    f"{base}/api/jobs/{job_id}/corrections", headers=auth,
    json={"analysis_basis_token": f"{job_id}:{rev_before}", "reason": "旧分析代际写回拒绝探针",
          "changes": [{"field": "raw_value", "target_type": "fact", "target_id": fact_uid or "probe",
                       "value": "1", "evidence_ids": [evidence_id or "probe"]}]},
    timeout=60,
)
try:
    stale_detail = stale.json().get("detail")
except Exception:
    stale_detail = stale.text[:200]
report["steps"]["stale_basis"] = {"http": stale.status_code, "detail": stale_detail,
                                  "rejected": stale.status_code == 409}

# 7) 当前 basis 正控（重分析之后，使用新 revision 的证据）
rev_now = current_revision() or rev_after
token_now = f"{job_id}:{rev_now}"
fresh = fetch_all(rev_now)
fresh_issues = []
try:
    fresh_body = fresh[0].json()
    raw = fresh_body.get("issues")
    fresh_issues = list(raw.get("all") or []) if isinstance(raw, dict) else list(raw or [])
except Exception:
    fresh_body = {}
fresh_evidence = None
for issue in fresh_issues:
    for ref in issue.get("evidence") or []:
        if isinstance(ref, dict) and ref.get("evidence_id"):
            fresh_evidence = ref["evidence_id"]
            break
    if fresh_evidence:
        break
positive = {"attempted": bool(fresh_evidence and fact_uid), "token": token_now,
            "revision": rev_now}
if positive["attempted"]:
    resp = httpx.post(
        f"{base}/api/jobs/{job_id}/corrections", headers=auth,
        json={"analysis_basis_token": token_now, "reason": "当前分析版本正控：证据可提交",
              "changes": [{"field": "raw_value", "target_type": "fact", "target_id": fact_uid,
                           "value": "0.00", "evidence_ids": [fresh_evidence]}]},
        timeout=120,
    )
    positive.update({"http": resp.status_code,
                     "accepted": resp.status_code in {200, 202},
                     "body": resp.text[:300]})
report["steps"]["current_basis_positive"] = positive

checks = [
    report["steps"]["exports_before"]["json"]["http"] == 200,
    report["steps"]["exports_before"]["csv"]["http"] == 200,
    report["steps"]["exports_before"]["pdf"]["http"] == 200,
    report["steps"]["exports_before"]["json_matches_revision"],
    report["steps"]["exports_before"]["csv_rows_positive"],
    report["steps"]["exports_before"]["pdf_content_positive"],
    report["steps"]["reanalyze"]["status"] not in {"error", "failed", None},
    report["steps"]["reanalyze"]["revision_changed"],
    report["steps"]["old_revision_after"]["json_unchanged"],
    report["steps"]["old_revision_after"]["pdf_unchanged"],
    report["steps"]["stale_basis"]["rejected"],
    report["steps"]["current_basis_positive"].get("accepted") is True,
]
report["completed"] = all(checks)
report["failed_checks"] = [index for index, ok in enumerate(checks, 1) if not ok]
(OUT / "final_chain_verification.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"completed": report["completed"], "failed_checks": report["failed_checks"],
                  "steps": {k: v for k, v in report["steps"].items() if k in
                            ("exports_before", "reanalyze", "old_revision_after", "stale_basis",
                             "current_basis_positive")}},
                 ensure_ascii=False, indent=2)[:3000])
