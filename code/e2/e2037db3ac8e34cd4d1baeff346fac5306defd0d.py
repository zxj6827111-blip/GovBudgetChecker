#!/usr/bin/env python3
"""真实痛点盘点：从本地 uploads 任务状态筛技术性人工补核候选（只读）。"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
UPLOADS = REPO / "uploads"
OUT = Path(__file__).resolve().parent / "painpoint_scan.json"
GOLD_INPUTS = Path("/Volumes/workSSD/software development/gbc-worktrees/"
                   "joint-execution-20261103-evidence/s1_prep/preflight_gold/inputs")
B1_PDFS = json.loads((REPO / "outputs/gold_b1_20261103/run_20261103T150928/b1_pdfs.json")
                     .read_text(encoding="utf-8"))

TECHNICAL_REASON_CODES = {
    "rules_insufficient_data", "rule_evidence_incomplete",
    "table_reading_incomplete", "rules_not_executed",
}
BUSINESS_RECORD_TYPES = {"structured_review", "finding_review_candidate"}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded_hashes() -> dict:
    excluded = {}
    for path in B1_PDFS.values():
        try:
            excluded[sha256(Path(path))] = f"B1:{Path(path).name}"
        except OSError:
            pass
    if GOLD_INPUTS.exists():
        for path in GOLD_INPUTS.iterdir():
            if path.is_file():
                try:
                    excluded[sha256(path)] = f"gold:{path.name}"
                except OSError:
                    pass
    return excluded


def main() -> int:
    excluded = excluded_hashes()
    materials = {}
    for directory in sorted(glob.glob(str(UPLOADS / "*/"))):
        status_path = Path(directory) / "status.json"
        if not status_path.exists():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta_path = Path(directory) / "job_meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        checksum = str(meta.get("checksum") or "")
        if not checksum:
            continue
        manual = (status.get("review_report") or {}).get("manual_review") or []
        unfinished, business, reasons = [], 0, Counter()
        ai_not_run = 0
        for item in manual:
            if not isinstance(item, dict):
                continue
            if item.get("record_type") == "unfinished_check":
                unfinished.append({
                    "obligation_id": item.get("obligation_id"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                })
                if item.get("status") == "ai_not_run" or item.get("reason") == "语义模型未执行":
                    ai_not_run += 1
                elif item.get("status") == "insufficient_data" or item.get("reason") == "取数不足":
                    reasons["取数不足"] += 1
                elif item.get("reason"):
                    reasons[str(item.get("reason"))] += 1
            elif item.get("record_type") in BUSINESS_RECORD_TYPES:
                business += 1
        for reason in status.get("review_reasons") or []:
            code = str(reason.get("code") or "")
            if code in TECHNICAL_REASON_CODES:
                reasons[code] += 1
        result_meta = (status.get("result") or {}).get("meta") or {}
        ocr_status = ((result_meta.get("ocr") or {}).get("status")) or ""
        recovery = ((((result_meta.get("page_extraction") or {}).get("table_reading") or {})
                     .get("recovery") or {}).get("status")) or ""
        if ocr_status == "review_required":
            reasons["ocr_review_required"] += 1
        if recovery == "review_required":
            reasons["table_reading_recovery"] += 1
        technical = bool(reasons)
        summary = result_meta.get("rule_execution_summary") or {}
        record = {
            "job_id": status.get("job_id") or Path(directory).name,
            "checksum": checksum,
            "filename": meta.get("filename") or status.get("filename"),
            "organization": meta.get("organization_name"),
            "fiscal_year": _int(meta.get("fiscal_year") or status.get("fiscal_year")),
            "doc_type": meta.get("doc_type") or status.get("doc_type"),
            "report_kind": meta.get("report_kind") or status.get("report_kind"),
            "analysis_revision": status.get("analysis_revision"),
            "job_status": status.get("status"),
            "quality_status": status.get("quality_status"),
            "mode": status.get("mode"),
            "versions": result_meta.get("versions"),
            "rule_registry_version": result_meta.get("rule_registry_version"),
            "rule_policy_version": result_meta.get("rule_policy_version"),
            "rule_counts": {key: summary.get(key) for key in
                            ("total_rules", "executed", "insufficient_data",
                             "parse_error", "execution_error")},
            "updated_ts": _float(meta.get("updated_at") or status.get("ts")),
            "status_path": str(status_path),
            "technical": technical,
            "technical_reasons": dict(reasons),
            "technical_score": sum(reasons.values()),
            "unfinished_checks": unfinished[:20],
            "unfinished_total": len(unfinished),
            "business_review_candidates": business,
            "ai_not_run_checks": ai_not_run,
            "excluded": excluded.get(checksum, ""),
        }
        current = materials.get(checksum)
        if current is None or (record["updated_ts"] or 0) > (current["updated_ts"] or 0):
            if current is not None:
                record["duplicate_jobs"] = current.get("duplicate_jobs", 1) + 1
            materials[checksum] = record
        else:
            current["duplicate_jobs"] = current.get("duplicate_jobs", 1) + 1

    unique = list(materials.values())
    technical = [item for item in unique if item["technical"] and not item["excluded"]
                 and "fixture" not in str(item.get("filename") or "").lower()
                 and "tiny.pdf" != str(item.get("filename") or "").lower()]
    technical.sort(key=lambda item: (-item["technical_score"], -(item["fiscal_year"] or 0),
                                     -(item["updated_ts"] or 0)))
    candidates = technical[:15]
    business_only = [item for item in unique
                     if not item["technical"] and item["business_review_candidates"]
                     and not item["excluded"]]
    payload = {
        "scanned_jobs": sum(1 for _ in glob.glob(str(UPLOADS / "*/status.json"))),
        "unique_materials": len(unique),
        "technical_materials": len(technical),
        "excluded_gold_or_b1": sum(1 for item in unique if item["excluded"]),
        "candidates": candidates,
        "business_ambiguity_materials": len(business_only),
        "business_ambiguity_examples": [
            {"job_id": item["job_id"], "filename": item["filename"],
             "fiscal_year": item["fiscal_year"], "doc_type": item["doc_type"],
             "business_review_candidates": item["business_review_candidates"],
             "status_path": item["status_path"]}
            for item in sorted(business_only, key=lambda row: -(row["updated_ts"] or 0))[:10]],
        "ai_not_run_total": sum(item.get("ai_not_run_checks", 0) for item in unique),
        "reason_totals": dict(Counter(
            reason for item in technical for reason in item["technical_reasons"])),
        "note": "只读 uploads 任务状态；不含未落盘的新任务；AI 未执行单列为配置原因，不计技术故障。",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in
                      ("scanned_jobs", "unique_materials", "technical_materials",
                       "excluded_gold_or_b1", "business_ambiguity_materials",
                       "reason_totals")}, ensure_ascii=False, indent=2))
    for item in candidates:
        print(item["job_id"], item["fiscal_year"], item["filename"],
              item["technical_score"], item["technical_reasons"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
