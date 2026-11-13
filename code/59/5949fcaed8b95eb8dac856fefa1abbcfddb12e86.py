#!/usr/bin/env python3
"""Reproduce the order-2 commit failure on the same HEAD/config (second run).

Uses the real reanalyze entry on the isolated job. Records request/status/worker log
tail. Does not change candidate code.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

RUN = Path(__file__).resolve().parents[1]
state = json.loads((RUN / "run_state.json").read_text())
item = state["items"]["2"]
base = f"http://127.0.0.1:{state['api_port']}"
headers = {"X-API-Key": state["api_key"]}
login = httpx.post(f"{base}/api/auth/login", headers=headers,
                   json={"username": "admin", "password": state["admin_password"]}, timeout=30)
login.raise_for_status()
auth = {**headers, "X-Session-Token": login.json()["token"]}
job_id = item["job_id"]
body = {"mode": "dual", "use_local_rules": True, "use_ai_assist": False}
resp = httpx.post(f"{base}/api/jobs/{job_id}/reanalyze", headers=auth, json=body, timeout=120)
out = RUN / "evidence/current" / f"02_{job_id[:8]}" / "reanalyze_repro"
out.mkdir(parents=True, exist_ok=True)
(out / "request.json").write_text(json.dumps({"job_id": job_id, "body": body, "status": resp.status_code,
                                              "response": resp.text[:2000]}, ensure_ascii=False, indent=1))
if resp.status_code != 200:
    print(f"reanalyze rejected: {resp.status_code} {resp.text[:500]}")
    sys.exit(1)
start = time.monotonic()
final = None
while time.monotonic() - start < 600:
    st = httpx.get(f"{base}/api/jobs/{job_id}/status", headers=auth, timeout=60)
    if st.status_code == 200:
        payload = st.json()
        if payload.get("status") in {"error", "failed", "review_required", "completed"}:
            final = payload
            break
    time.sleep(3)
if final is None:
    print("no terminal status within 600s")
    sys.exit(2)
(out / "status_final.json").write_text(json.dumps(final, ensure_ascii=False, indent=1))
result = {
    "job_id": job_id,
    "analysis_revision": final.get("analysis_revision"),
    "status": final.get("status"),
    "error": final.get("error"),
    "elapsed_seconds": round(time.monotonic() - start, 1),
    "reproduced": str(final.get("error") or "").startswith("trusted_analysis_result_not_committed"),
}
(out / "repro_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False, indent=1))
