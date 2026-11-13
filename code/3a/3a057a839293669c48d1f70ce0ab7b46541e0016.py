#!/usr/bin/env python3
"""Assemble a machine-readable summary of the diagnostic."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]


def main() -> int:
    results = json.loads((RUN / "painpoint_results.json").read_text())
    ledger = json.loads((RUN / "VERIFICATION_LEDGER.json").read_text())
    state = json.loads((RUN / "run_state.json").read_text())

    per_order = {}
    for key, count in Counter((r["order"], r["classification"]) for r in results["a_layer"]["rows"]).items():
        per_order.setdefault(str(key[0]), {})[key[1]] = count

    docs = []
    for doc in results["b_layer"]["documents"]:
        docs.append({
            "order": doc["order"],
            "filename": doc["filename"],
            "current_status": doc["current_status"],
            "current_error": doc.get("current_error"),
            "coverage": doc.get("current_coverage"),
            "issue_counts": doc.get("current_severity"),
            "historical_issue_count": doc.get("historical_issue_count"),
        })

    summary = {
        "kind": "real_painpoint_diag_summary",
        "status": "AWAITING_CODEX_EFFECT_REVIEW",
        "run_id": state.get("run_id"),
        "business_head": "4cda0edd361c81530654da8915306a400bd7f100",
        "catalog_version": results["identity"]["catalog_version_current"],
        "catalog_fingerprint": results["identity"]["catalog_fingerprint_current"],
        "scope": {
            "documents": 10,
            "type": "final accounts only",
            "years": [2022, 2023, 2024, 2025],
            "mode": "dual + local rules, AI disabled",
            "ocr_triggered": 0,
            "not_a_blind_test": True,
            "not_accuracy_sample": True,
        },
        "a_layer": {
            "total": 131,
            "by_classification": results["a_layer"]["by_classification"],
            "per_order": per_order,
            "unmatched_historical": len(results["a_layer"]["unmatched_historical"]),
            "unmatched_historical_note": "all 10 are order-2 job-error records with no current instances; no obligation id is missing from the catalog",
            "excluding_order1_material_incomplete": {
                "total": 99, "completed": 41, "still_insufficient": 44,
                "ai_not_run": 4, "blocked_by_job_error": 10,
                "note": "order 1 has 6 blank core tables, 1 missing table and abbreviated narrative; its 30 unfinished records are mostly material-side",
            },
        },
        "b_layer": {
            "documents_with_results": 9,
            "totals": results["b_layer"]["totals"],
            "by_reason": results["b_layer"]["by_reason"],
            "documents": docs,
        },
        "issues": {
            "total": 90, "high": 5, "medium": 31, "low": 17, "info": 37,
            "high_verdicts": {"true_positive": 4, "false_positive": 1},
            "medium_sampled": 4,
            "medium_sampled_all_true": True,
            "low_info_individually_verified": False,
        },
        "recoveries": {
            "claimed_completed": 42,
            "verdict_counts": ledger["verdict_counts"],
            "independently_verified_or_representative": 36,
            "completed_with_defect": 2,
            "completed_with_legacy_noise": 1,
            "receipt_only": 3,
        },
        "root_causes": [
            {"id": "RC-01", "title": "Decimal 结果无法提交", "severity": "blocker",
             "affected_orders": [2], "blocked_records": 10,
             "location": "src/services/analysis_fact_snapshot.py publish_snapshot json.dumps",
             "reproduced_twice": True},
            {"id": "RC-02", "title": "空白/缺失核心表与可选空白列被报成取数不足", "severity": "high",
             "affected_orders": [1, 4, 5, 6, 7, 8], "historical_completed_now_unresolved": 24,
             "material_facts": "order1: 6 blank core tables + 1 missing table + abbreviated narrative; order4: blank T6/T7; order5/6/7/8: blank optional cells provable as zero",
             "location": "problem projection (blank/missing table -> formal defect) + src/engine/check_obligations.py operand requirements + fact_table_projection/fiscal_row_roles"},
            {"id": "RC-03", "title": "真实 fact 绑定/版式映射失败（少数）", "severity": "medium",
             "affected_orders": [1, 9], "still_unresolved_records": 6,
             "location": "src/services/pdf_parser.py, table_recognizer.py, fact_table_projection (order9 T3 3 facts + r0c3; order1 T2 single-column)"},
            {"id": "RC-04", "title": "CMM-007 零基数误报（显示精度包络）", "severity": "high (1 false positive)",
             "affected_orders": [5], "false_high": 1,
             "location": "src/engine/common_rules.py CMM-007 zero-base branch"},
            {"id": "RC-05", "title": "空表说明已存在但旧规则仍报缺说明", "severity": "low (manual-review noise)",
             "affected_orders": [3, 4, 7, 8],
             "location": "rules_v33 V33-109/V33-114 suppression when V33-122 passes"},
            {"id": "RC-06", "title": "文本披露缺失被分类为取数不足", "severity": "medium",
             "affected_orders": [1, 4, 6, 8],
             "location": "text disclosure obligations classification; 机关机关 duplicate normalization"},
        ],
        "ai": {
            "enabled": False,
            "model_calls_observed": 0,
            "historical_ai_succeeded_in_batch": 4,
            "historical_ai_succeeded_outside_batch": 1,
            "ai_not_run_a_layer": 5,
            "ai_not_run_b_layer": 9,
            "recommended": "not needed for current technical root causes; if later needed, cap at 5 documents / 120k tokens and require explicit authorization",
        },
        "ocr": {
            "triggered": False,
            "reason": "no table-recovery pages; only covers lacked text",
            "cannot_claim": "OCR improvement or OCR uselessness",
        },
        "trial_scope": {
            "allowed": "native-text final accounts with all nine tables recognized and no blank optional-column dependency; assistant review with human adjudication",
            "excluded": ["scanned/mixed-table materials", "2024 abbreviated narrative layouts", "materials with blank optional columns", "Decimal-triggering materials", "budget materials (not covered)"],
        },
        "stop_development_recommendation": "No — three specific fixes are required first; do not expand to M3-M7.",
    }
    (RUN / "DIAGNOSTIC_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({"status": summary["status"], "a": summary["a_layer"]["by_classification"],
                      "b": summary["b_layer"]["totals"], "root_causes": [r["id"] for r in summary["root_causes"]]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
