#!/usr/bin/env python3
"""Build the A/B comparison for the real-material diagnostic.

A layer: each historical unfinished check (131 records) traced to the current
obligation instance, with the current receipts/evidence attached.
B layer: all current applicable checks per document, plus formal issues and
manual-review reasons, with explicit denominators.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
MAIN = Path("/Volumes/workSSD/software development/GovBudgetChecker")
MANIFEST = MAIN / "outputs/unified_closeout_round6_review_codex_20261104/diagnostic_manifest_10.json"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def find_current_dir(order: int, job_id: str) -> Path | None:
    matches = sorted((RUN / "evidence/current").glob(f"{order:02d}_*"))
    for path in matches:
        status = path / "status_final.json"
        if status.exists():
            payload = load_json(status, {})
            if str(payload.get("job_id") or "") == job_id or not payload:
                return path
    return matches[0] if matches else None


def receipt_matches(receipt: dict, obligation_id: str) -> bool:
    return receipt.get("obligation_id") == obligation_id or str(receipt.get("sub_check_id") or "").startswith(obligation_id + ":")


def main() -> int:
    manifest = load_json(MANIFEST)
    state = load_json(RUN / "run_state.json", {})
    preflight = load_json(RUN / "preflight.json", {})

    rows = []
    per_doc = []
    unmatched_historical = []
    current_ids_without_history = []

    for item in manifest["items"]:
        order = item["order"]
        job_id = item["job_id"]
        hist = load_json(RUN / f"evidence/historical/{job_id}/status.json", {})
        cur_dir = find_current_dir(order, job_id)
        cur_status = load_json(cur_dir / "status_final.json", {}) if cur_dir else {}
        report = load_json(cur_dir / "report.json", {}) if cur_dir else {}
        meta = ((cur_status.get("result") or {}).get("meta") or {})
        receipts = meta.get("check_receipts") or []
        cov = cur_status.get("check_coverage") or {}
        instances = cov.get("instances") or []
        inst_by_id = defaultdict(list)
        for inst in instances:
            inst_by_id[inst.get("obligation_id")].append(inst)

        cur_issues = report.get("issues") or []
        hist_issues_obj = (hist.get("result") or {}).get("issues") or {}
        if isinstance(hist_issues_obj, dict):
            hist_issues = hist_issues_obj.get("all") or []
            if not hist_issues:
                hist_issues = [*(hist_issues_obj.get("error") or []), *(hist_issues_obj.get("warn") or []), *(hist_issues_obj.get("info") or [])]
        else:
            hist_issues = hist_issues_obj or []

        doc = {
            "order": order,
            "job_id": job_id,
            "current_job_id": cur_status.get("job_id") or (state.get("items", {}).get(str(order)) or {}).get("job_id"),
            "filename": item["filename"],
            "year": item["year"],
            "pdf_kind": (preflight.get("items") or [{}])[order - 1].get("pdf_classification", {}).get("kind") if len(preflight.get("items") or []) >= order else None,
            "current_status": cur_status.get("status"),
            "current_error": cur_status.get("error"),
            "historical_status": hist.get("status"),
            "historical_quality": hist.get("quality_status"),
            "current_quality": cur_status.get("quality_status"),
            "historical_unfinished": item["unfinished_total"],
            "current_coverage": {k: cov.get(k) for k in (
                "catalog_version", "catalog_fingerprint", "applicable_total", "completed_total",
                "not_applicable_total", "unresolved_total", "blocking_total", "coverage_rate",
                "auto_completion_rate", "by_reason")},
            "historical_coverage": {k: (hist.get("check_coverage") or {}).get(k) for k in (
                "catalog_version", "catalog_fingerprint", "applicable_total", "completed_total",
                "not_applicable_total", "unresolved_total", "blocking_total", "coverage_rate",
                "auto_completion_rate", "by_reason")},
            "historical_issue_count": len(hist_issues),
            "current_issue_count": len(cur_issues),
            "historical_severity": dict(Counter(str(i.get("severity") or "") for i in hist_issues if isinstance(i, dict))),
            "current_severity": dict(Counter(str(i.get("severity") or "") for i in cur_issues)),
            "historical_rules": sorted({str(i.get("rule_id") or i.get("rule") or "") for i in hist_issues if isinstance(i, dict)}),
            "current_rules": sorted({str(i.get("rule_id") or "") for i in cur_issues}),
            "elapsed_seconds": (state.get("items", {}).get(str(order)) or {}).get("elapsed_seconds"),
            "mode": cur_status.get("mode"),
            "use_ai_assist": cur_status.get("use_ai_assist"),
            "ai_execution": meta.get("ai_execution"),
            "ocr": {
                "ocr_progress": cur_status.get("ocr_progress"),
                "ocr_applied": ((meta.get("page_extraction") or {}).get("ocr_applied")),
                "scanned_pages": ((meta.get("page_extraction") or {}).get("scanned_pages")),
                "table_recovery_pages": ((meta.get("page_extraction") or {}).get("table_recovery_pages")),
                "table_reading_gaps": ((meta.get("page_extraction") or {}).get("table_reading") or {}).get("gaps"),
            },
            "page_coverage": cur_status.get("page_coverage"),
            "scanned_page_count": cur_status.get("scanned_page_count"),
            "historical_scanned_page_count": hist.get("scanned_page_count"),
            "document_profile": {
                "subject_level": ((cur_status.get("document_profile") or {}).get("subject_level")),
                "caliber": ((cur_status.get("document_profile") or {}).get("caliber")),
                "writing_scope": ((cur_status.get("document_profile") or {}).get("writing_scope")),
            },
            "rule_execution_summary": meta.get("rule_execution_summary"),
            "versions": meta.get("versions"),
            "org_bound": {
                "id": cur_status.get("organization_id"),
                "name": cur_status.get("organization_name"),
                "match_type": cur_status.get("organization_match_type"),
            },
            "manual_review_counts": dict(Counter(str(m.get("code") or m.get("record_type") or "") for m in ((cur_status.get("review_report") or {}).get("manual_review") or []))),
        }
        per_doc.append(doc)

        current_obligation_ids = set(inst_by_id)
        for record in item["unfinished_checks_full"]:
            oid = record["obligation_id"]
            matches = inst_by_id.get(oid) or []
            matched = matches[0] if matches else None
            if not matches:
                unmatched_historical.append({"order": order, "job_id": job_id, "obligation_id": oid,
                                             "title": record.get("title"), "reason": record.get("reason")})
            related_receipts = [r for r in receipts if receipt_matches(r, oid)]
            receipt_status = Counter(str(r.get("status") or "") for r in related_receipts)
            operandful = [r for r in related_receipts if (r.get("operand_fact_ids") or r.get("operand_sources") or r.get("cell_observations") or r.get("required_operand_roles") or r.get("disclosure_sources"))]
            issue_refs = sorted({ref for r in related_receipts for ref in (r.get("issue_refs") or [])})
            job_failed = str(cur_status.get("status") or "").lower() in {"error", "failed"} and not instances
            current_status = "blocked_by_job_error" if job_failed else (matched.get("status") if matched else "missing_from_current_catalog")
            if job_failed:
                cls = "blocked_by_job_error"
                matched = None
            elif current_status == "completed":
                cls = "completed_current"
            elif current_status == "not_applicable":
                cls = "not_applicable_current"
            elif matched:
                cls = f"still_{current_status}"
            else:
                cls = "missing_from_current_catalog"
            if matched and matched.get("status") == "completed":
                if operandful:
                    evidence_strength = "operand_receipt"
                elif any(r.get("disclosure_sources") for r in related_receipts):
                    evidence_strength = "disclosure_receipt"
                elif any(r.get("formula") for r in related_receipts):
                    evidence_strength = "formula_only"
                else:
                    evidence_strength = "legacy_pass_only"
            elif matched and matched.get("status") == "not_applicable":
                evidence_strength = "not_applicable"
            else:
                evidence_strength = None
            if cls == "completed_current":
                actual_action = "automatic_pipeline_completion"
            elif cls == "blocked_by_job_error":
                actual_action = "none_job_error"
            elif cls == "still_ai_not_run":
                actual_action = "none_ai_disabled"
            else:
                actual_action = "none"
            if order == 1 and cls.startswith("still"):
                material_note = "原件核心表空白/缺表（见 REMAINING_CAUSES 3.1）；未完成不等于解析器失败"
            elif order == 9 and cls.startswith("still"):
                material_note = "T3 有数据但 fact 未绑定（见 REMAINING_CAUSES 3.3）"
            elif order in (4, 5, 6, 7, 8) and cls.startswith("still"):
                material_note = "空白/可选列或文本披露缺口（见 REMAINING_CAUSES 3.2/4/6）"
            elif order == 2:
                material_note = "Decimal 结果提交失败（见 REMAINING_CAUSES 2）"
            else:
                material_note = None
            rows.append({
                "order": order,
                "job_id": job_id,
                "current_job_id": doc["current_job_id"],
                "filename": item["filename"],
                "year": item["year"],
                "obligation_id": oid,
                "title": record.get("title"),
                "historical_status": record.get("status"),
                "historical_reason": record.get("reason"),
                "historical_check_relation_id": record.get("check_relation_id"),
                "historical_next_action": record.get("next_action"),
                "current_status": current_status,
                "current_reason": matched.get("reason") if matched else None,
                "current_detail": matched.get("detail") if matched else None,
                "current_checkers": matched.get("checkers") if matched else None,
                "current_missing_checkers": matched.get("missing_checkers") if matched else None,
                "current_evidence_kind": matched.get("evidence_kind") if matched else None,
                "current_gap_note": matched.get("gap_note") if matched else None,
                "current_basis": matched.get("basis") if matched else None,
                "current_blocks_gate": matched.get("blocks_gate") if matched else None,
                "current_root_cause_key": matched.get("root_cause_key") if matched else None,
                "current_reason_codes": matched.get("reason_codes") if matched else None,
                "current_missing_operands": matched.get("missing_operands") if matched else None,
                "current_blocked_by": matched.get("blocked_by") if matched else None,
                "current_next_action": matched.get("next_action") if matched else None,
                "has_fail_receipt": any(str(r.get("status") or "") in {"fail", "error"} for r in related_receipts),
                "classification": cls,
                "actual_action": actual_action,
                "human_participation": "operator_org_selection_only",
                "elapsed_seconds": (state.get("items", {}).get(str(order)) or {}).get("elapsed_seconds"),
                "material_note": material_note,
                "evidence_strength": evidence_strength,
                "receipt_count": len(related_receipts),
                "receipt_status": dict(receipt_status),
                "receipts_with_operands": len(operandful),
                "issue_refs": issue_refs,
                "receipt_formulas": sorted({str(r.get("formula") or "") for r in related_receipts if r.get("formula")}),
                "evidence_dir": str(cur_dir) if cur_dir else None,
            })

        history_ids = {r["obligation_id"] for r in item["unfinished_checks_full"]}
        for oid in sorted(current_obligation_ids - history_ids):
            current_ids_without_history.append({"order": order, "job_id": job_id, "obligation_id": oid})

    # B layer aggregates
    totals = Counter()
    by_reason = Counter()
    by_doc = []
    for doc in per_doc:
        cov = doc["current_coverage"]
        if cov.get("applicable_total") is None:
            by_doc.append({**{k: doc[k] for k in ("order", "job_id", "current_job_id", "filename", "year", "current_status", "current_error")},
                           "note": "no analysis result (job error); denominator unavailable"})
            continue
        totals["applicable"] += cov.get("applicable_total") or 0
        totals["completed"] += cov.get("completed_total") or 0
        totals["unresolved"] += cov.get("unresolved_total") or 0
        totals["not_applicable"] += cov.get("not_applicable_total") or 0
        for reason, count in (cov.get("by_reason") or {}).items():
            by_reason[reason] += count
        by_doc.append({**{k: doc[k] for k in ("order", "job_id", "current_job_id", "filename", "year", "current_status")},
                       "current_coverage": cov, "current_issue_count": doc["current_issue_count"],
                       "current_severity": doc["current_severity"], "historical_issue_count": doc["historical_issue_count"]})

    result = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "identity": {
            "business_head": "4cda0edd361c81530654da8915306a400bd7f100",
            "manifest_sha256": __import__("hashlib").sha256(MANIFEST.read_bytes()).hexdigest(),
            "run_state_run_id": state.get("run_id"),
            "catalog_version_current": (per_doc[0]["current_coverage"] or {}).get("catalog_version"),
            "catalog_fingerprint_current": (per_doc[0]["current_coverage"] or {}).get("catalog_fingerprint"),
        },
        "a_layer": {
            "historical_records": len(rows),
            "by_classification": dict(Counter(r["classification"] for r in rows)),
            "by_obligation": dict(Counter(r["obligation_id"] for r in rows)),
            "rows": rows,
            "unmatched_historical": unmatched_historical,
            "current_ids_without_history": current_ids_without_history,
        },
        "b_layer": {
            "documents": by_doc,
            "totals": dict(totals),
            "by_reason": dict(by_reason),
        },
        "per_document": per_doc,
    }
    (RUN / "painpoint_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))

    fieldnames = list(rows[0].keys()) if rows else []
    with (RUN / "painpoint_results.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in row.items()})

    print(json.dumps({
        "a_layer": result["a_layer"]["by_classification"],
        "historical_records": len(rows),
        "unmatched_historical": len(unmatched_historical),
        "b_totals": result["b_layer"]["totals"],
        "b_by_reason": result["b_layer"]["by_reason"],
        "docs": [{k: d.get(k) for k in ("order", "current_status", "current_error")} | {"coverage": d.get("current_coverage")} for d in by_doc],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
