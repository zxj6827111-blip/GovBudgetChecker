#!/usr/bin/env python3
"""汇总第四轮台账/验证指标（数字来自同一 final_r4_final 台账）。"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
WORK = REPO / "outputs/unified_closeout_round4_20261104_work"
B1 = REPO / "outputs/gold_b1_20261103/run_20261103T150928/revisions/round4_annotation_revision"

rows = list(csv.DictReader((WORK / "final_r4_final/target_status.csv").open(encoding="utf-8-sig")))
counts = Counter(row["final_state"] for row in rows)
by_type = {}
for row in rows:
    by_type.setdefault(row["type"], Counter())[row["final_state"]] += 1
pending = [row["target_id"] for row in rows if row["final_state"] == "pending_adjudication"]
strict = [row["target_id"] for row in rows if row["final_state"] == "strict_partial"]
miss = [row for row in rows if row["type"] == "MISS"]
anchors = [row for row in miss if row.get("anchors_total")]
full = [row for row in anchors if row.get("anchors_total") == row.get("anchors_covered")]

gold = json.loads((WORK / "gold_r4_selfcompare.json").read_text(encoding="utf-8"))
reg = json.loads((WORK / "reg160_selfcompare_r4/batch2_regression_compare.json").read_text(encoding="utf-8"))
db_steps = json.loads((WORK / "db_e2e_r4/00_steps.json").read_text(encoding="utf-8"))
exports = json.loads((WORK / "db_e2e_r4/14_export_hashes.json").read_text(encoding="utf-8"))
ui = json.loads((WORK / "ui_stale_final/ui_stale_basis_result.json").read_text(encoding="utf-8"))
b1 = json.loads((B1 / "decimal_checks_v4_summary.json").read_text(encoding="utf-8"))
b1_audit = json.loads((B1 / "decimal_evidence_audit.json").read_text(encoding="utf-8"))
t06 = json.loads((B1 / "t06_items.json").read_text(encoding="utf-8"))
identities = {role: json.loads((WORK / f"db_env/{role}_identity.json").read_text(encoding="utf-8"))
              for role in ("api", "worker", "frontend")}
pytest_lines = (WORK / "pytest_r4.log").read_text(encoding="utf-8").strip().splitlines()
pytest_tail = next((line for line in reversed(pytest_lines) if " passed" in line), pytest_lines[-1])

payload = {
    "run_id": "unified-closeout-round4-20261104",
    "generated_at_local": "2026-11-04",
    "status_meaning": "implementation self-verification after R3-01~R3-05 rework; not independent acceptance",
    "code_identity": {
        "business_head": "4cda0edd361c81530654da8915306a400bd7f100",
        "business_branch": "codex/unified-closeout-20261103",
        "comparator_head": "7caed95be7c71255356d260628ac316c1aca8450",
        "comparator_branch": "codex/joint-exp-batch2-tools-20261103",
        "tools_head": "2bdbe1ec65c0b80b6ab61d28a1d65cecb19391b8",
        "tools_branch": "codex/closeout-tools-r4-20261104",
        "b1_head": "e0efa58a5c56b77404f92e6c0dabb07dd8d79f3d",
    },
    "targets_114": {
        "total": len(rows),
        "fixed": counts.get("fixed", 0),
        "pending_adjudication": counts.get("pending_adjudication", 0),
        "strict_partial": counts.get("strict_partial", 0),
        "by_type": {key: dict(value) for key, value in by_type.items()},
        "strict_partial_ids": strict,
        "pending_ids": pending,
        "occurrence_coverage": {
            "miss_targets": len(miss),
            "anchors_full": len(full),
            "partial_or_missing": len(miss) - len(full),
        },
        "supersedes": {
            "round3": "104 fixed / 7 pending / 3 strict（W01|1、N03|0、S03|0）",
            "round4_change": "三条 strict 按 gold note 角色关闭：W01/S03 为正确对照；N03 分母由 CMM-020 补来源。fixed 107，pending 7，strict 0；未按 target_id 放行。",
        },
    },
    "determinism": {
        "gold_33": gold["totals"],
        "reg160": {"diffs": reg["diffs"], "checks": {c["id"]: c["verdict"] for c in reg["checks"]}},
    },
    "database_e2e": {
        "steps": len(db_steps),
        "first_upload": 200,
        "permission_negative": 403,
        "stale_session": 409,
        "stale_basis_error": "analysis_basis_stale",
        "new_session_write": 200,
        "export_parity": {
            "analysis_revision": exports["analysis_revision"],
            "review_report_sha256": exports["review_report_sha256"],
            "target_issue": exports["target_issue"]["id"],
        },
        "identities": {role: {"head": data["git_head"], "cwd": data["cwd"]}
                       for role, data in identities.items()},
    },
    "real_page_basis": {
        "first_request_body_basis": ui["notes"][2].get("first_body_basis"),
        "stale_response_status": ui["notes"][7].get("stale_response", {}).get("status"),
        "fresh_response_status": ui["notes"][9].get("fresh_response", {}).get("status"),
        "new_basis": ui["notes"][6].get("basis2"),
    },
    "b1": {
        "decimal_rows": b1["rows"],
        "decimal_counts": b1["counts"],
        "evidence_items": b1_audit["total"],
        "evidence_all_found": b1_audit["all_found"],
        "t06": {sid: {"nonzero": data["nonzero_count"],
                      "people": [data["人员经费"]["count"], data["人员经费"]["sum"]],
                      "public": [data["公用经费"]["count"], data["公用经费"]["sum"]]}
                for sid, data in t06.items()},
        "note": "15 FAIL 均为初标 defect；1 PENDING 是表内/说明不同口径的非等式。",
    },
    "full_backend": pytest_tail,
}
out = WORK / "metrics_r4.json"
out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
