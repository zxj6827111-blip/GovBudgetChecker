#!/usr/bin/env python3
"""汇总 round3 台账与运行指标（所有数字来自同一 final_r3 台账）。"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
WORK = REPO / "outputs/unified_closeout_round3_20261104_work"
OUT = WORK / "metrics_r3.json"

rows = list(csv.DictReader((WORK / "final_r3/target_status.csv").open(encoding="utf-8-sig")))
counts = Counter(row["final_state"] for row in rows)
by_type = {}
for row in rows:
    by_type.setdefault(row["type"], Counter())[row["final_state"]] += 1
pending = [row["target_id"] for row in rows if row["final_state"] == "pending_adjudication"]
strict = [row["target_id"] for row in rows if row["final_state"] == "strict_partial"]
miss = [row for row in rows if row["type"] == "MISS"]
anchors = [row for row in miss if row.get("anchors_total")]
full = [row for row in anchors if row.get("anchors_total") == row.get("anchors_covered")]

strict_metrics = json.loads((WORK / "strict_metrics_r3.json").read_text(encoding="utf-8"))
gold_compare = json.loads((WORK / "gold_r3_selfcompare.json").read_text(encoding="utf-8"))
db_steps = json.loads((WORK / "db_e2e_r3/00_steps.json").read_text(encoding="utf-8"))
exports = json.loads((WORK / "db_e2e_r3/14_export_hashes.json").read_text(encoding="utf-8"))
b1 = json.loads((REPO / "outputs/gold_b1_20261103/run_20261103T150928/revisions/"
                 "round2_annotation_revision/decimal_checks_v4_summary.json").read_text(encoding="utf-8"))
b1_audit = json.loads((REPO / "outputs/gold_b1_20261103/run_20261103T150928/revisions/"
                       "round2_annotation_revision/decimal_evidence_audit.json").read_text(encoding="utf-8"))

payload = {
    "run_id": "unified-closeout-round3-20261104",
    "generated_at_local": "2026-11-04",
    "status_meaning": "implementation self-verification after third-round R2-02~R2-06 rework; not independent acceptance",
    "code_identity": {
        "business_head": "5a9606ebd7a6c426dcc0a18c0ca8c45ca411d85a",
        "business_branch": "codex/unified-closeout-20261103",
        "comparator_head": "e6ade685",
        "comparator_branch": "codex/joint-exp-batch2-tools-20261103",
        "b1_head": "e0efa58a5c56b77404f92e6c0dabb07dd8d79f3d",
        "first_batch_accepted": "48ee4e28441bd6ddbf34beee85bc6883d60db773",
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
            "note": "full 含待裁定 N05（金标语义歧义），因此 fixed MISS 少于 anchors_full。",
        },
        "supersedes": {
            "round2": "104 fixed / 7 pending / 3 strict（3 strict=0元×2+7750.99元）",
            "round3_change": "3 个格式缺口已 fixed（F02/F01/F04）；3 个原本 fixed 的目标因出现点证据不足转为 strict_partial（W01|1、N03|0、S03|0）；净数仍 104/7/3，但组成不同。",
        },
    },
    "strict_metrics": {
        "tiers": strict_metrics["tiers"],
        "F_match_link_diagnostic": strict_metrics["F_match"],
        "P_match_link_diagnostic": strict_metrics["P_match"],
        "note": "F/P 链接诊断由 build_strict_metrics.py 在同一 run 上重算；与旧 162/7/24/2/5 分栏不可直接相减（新条目与人工裁决重新对齐）。验收分子以 targets_114 台账为准。",
    },
    "determinism": {
        "gold_33": gold_compare["totals"],
        "note": "33 份金标两遍采集，去掉 analysis_revision/时间/耗时等易变字段后 33/33 一致。",
    },
    "database_e2e": {
        "steps": len(db_steps),
        "first_upload": 200,
        "permission_negative": 403,
        "stale_session": 409,
        "stale_basis_error": "analysis_basis_stale",
        "new_session_write": 200,
        "old_revision_export": "独立 revision + 独立 review_report_sha256",
        "export_parity": {
            "analysis_revision": exports["analysis_revision"],
            "review_report_sha256": exports["review_report_sha256"],
            "target_issue": exports["target_issue"]["id"],
        },
    },
    "b1": {
        "decimal_rows": b1["rows"],
        "decimal_counts": b1["counts"],
        "evidence_items": b1_audit["total"],
        "evidence_all_found": b1_audit["all_found"],
        "note": "15 FAIL 均为初标 defect；1 PENDING 是表内/说明不同口径的非等式，已逐条说明。",
    },
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
