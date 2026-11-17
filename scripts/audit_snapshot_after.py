"""重建整改后的当前材料与审核工作台统计，并与 20261115 根因审计基线对照。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import asyncpg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = ROOT / "outputs" / "root_cause_audit_20261115"
OUT_DIR = ROOT / "outputs" / "remediation_after_20261115"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.material_detail_query_service import select_current_run  # noqa: E402
from src.services.review_worklist_service import load_review_worklist  # noqa: E402

UNRESOLVED = {
    "insufficient_data",
    "parse_error",
    "execution_error",
    "not_implemented",
    "not_executed",
    "parse_ambiguity",
    "profile_unresolved",
    "kind_conflict",
    "ai_not_run",
    "ai_failed",
}


def read_one_status(p: Path) -> Dict[str, Any]:
    d = json.loads(p.read_text(encoding="utf-8"))
    r = d.get("review_report") or {}
    m = (d.get("result") or {}).get("meta") or {}
    c = r.get("obligation_coverage") or m.get("obligation_coverage") or {}
    q = m.get("quality_gate") or r.get("conclusion") or {}
    summary = m.get("rule_execution_summary") or {}
    rr = d.get("review_reasons") or q.get("review_reasons") or []
    inst = c.get("instances") or c.get("items") or []
    unresolved = [x for x in inst if x.get("blocks_gate") and x.get("status") in UNRESOLVED]
    reading = ((d.get("structured_ingest") or {}).get("reading_import") or {}).get("reading_quality") or {}
    return {
        "job_id": d.get("job_id") or p.parent.name,
        "filename": d.get("filename"),
        "checksum": d.get("checksum"),
        "source": str(p.relative_to(ROOT)),
        "revision": d.get("analysis_revision"),
        "mtime": p.stat().st_mtime,
        "status": d.get("status"),
        "report_kind": d.get("report_kind"),
        "year": d.get("report_year"),
        "page_coverage": d.get("page_coverage"),
        "scanned_pages": d.get("scanned_page_count"),
        "catalog_version": c.get("catalog_version"),
        "issues": len(r.get("issues") or []),
        "tips": len(r.get("tips") or []),
        "manual_review_entries": len(r.get("manual_review") or []),
        "coverage_blocking": c.get("blocking_total"),
        "coverage_applicable": c.get("applicable_total"),
        "coverage_completed": c.get("completed_total"),
        "review_reasons": rr,
        "unresolved_obligations": unresolved,
        "unresolved_rules": summary.get("unresolved_rules") or [],
        "receipt_status_counts": dict(Counter(x.get("status") for x in (r.get("check_receipts") or []))),
        "evidence_completeness": m.get("evidence_completeness"),
        "reading_counts": {k: v for k, v in reading.items() if isinstance(v, (int, float, str, bool))},
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    reasons = Counter()
    rules = Counter()
    obligations = Counter()
    diags = Counter()
    receipts = Counter()
    for r in rows:
        reasons.update({x.get("code") for x in r["review_reasons"] if isinstance(x, dict)})
        rules.update({x.get("rule_id") for x in r["unresolved_rules"] if isinstance(x, dict)})
        obligations.update(x["obligation_id"] for x in r["unresolved_obligations"])
        diags.update(code for x in r["unresolved_obligations"] for code in x.get("reason_codes", []))
        receipts.update(r["receipt_status_counts"])
    return {
        "count": len(rows),
        "statuses": dict(Counter(x["status"] for x in rows)),
        "catalogs": dict(Counter(x["catalog_version"] for x in rows)),
        "has_scanned_pages": sum(bool(x.get("scanned_pages")) for x in rows),
        "has_formal_issues": sum(x["issues"] > 0 for x in rows),
        "has_blocking_checks": sum((x["coverage_blocking"] or 0) > 0 for x in rows),
        "blocking_checks": sum(x["coverage_blocking"] or 0 for x in rows),
        "review_required_without_blocking_checks": sum(
            x["status"] == "review_required" and not x["coverage_blocking"] for x in rows
        ),
        "review_required_without_any_unresolved_rule": sum(
            x["status"] == "review_required" and not x["unresolved_rules"] for x in rows
        ),
        "review_reason_documents": dict(reasons.most_common()),
        "unresolved_rule_documents": dict(rules.most_common()),
        "blocking_obligation_counts": dict(obligations.most_common()),
        "blocking_diagnostic_counts": dict(diags.most_common()),
        "receipt_status_counts": dict(receipts),
    }


async def main_async() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dotenv_values(ROOT / ".env")
    for k, v in cfg.items():
        if v is not None:
            os.environ.setdefault(k, v)

    conn = await asyncpg.connect(
        cfg["DATABASE_URL"],
        timeout=10,
        command_timeout=60,
        server_settings={"default_transaction_read_only": "on", "application_name": "audit_snapshot_after"},
    )
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(
                "SELECT set_config($1,$2,true)",
                "search_path",
                (cfg.get("PG_SCHEMA") or "public") + ",public",
            )
            rows = await conn.fetch(
                """
                SELECT s.id::text AS slot_id, s.status AS slot_status, s.status_reason,
                       s.subject_org_name, s.fiscal_year, s.report_kind,
                       s.current_document_version_id, s.mapping_key,
                       v.file_hash AS current_file_hash, v.original_filename,
                       j.id, j.job_uuid, j.status, j.analysis_revision,
                       j.completed_at, j.started_at, j.updated_at, j.created_at,
                       j.metadata->'result_meta'->'quality_gate' AS quality_gate,
                       j.metadata->'result_meta'->'rule_execution_summary' AS rule_summary,
                       j.result_obligation_coverage AS obligation_coverage
                FROM material_slots s
                LEFT JOIN fiscal_document_versions v
                  ON v.id = s.current_document_version_id AND v.slot_id = s.id
                LEFT JOIN analysis_jobs j
                  ON j.structured_document_version_id = v.id::numeric
                """
            )
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for rec in rows:
                r = dict(rec)
                for key in ("quality_gate", "rule_summary", "obligation_coverage"):
                    if isinstance(r.get(key), str):
                        r[key] = json.loads(r[key])
                grouped.setdefault(r["slot_id"], []).append(r)
            current = [
                select_current_run([r for r in grp if r["id"] is not None]) or grp[0]
                for grp in grouped.values()
            ]
            (OUT_DIR / "current_materials.json").write_text(
                json.dumps(current, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            wl_res = await load_review_worklist(
                conn,
                jobs=[],
                visible_org_ids=None,
                job_accessible=lambda _job: True,
                viewer="audit_snapshot_after",
            )
            wl_data = wl_res.model_dump(mode="json")
            (OUT_DIR / "worklist_readback.json").write_text(
                json.dumps(wl_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    finally:
        await conn.close()

    current_job_ids = {str(r["job_uuid"]) for r in current if r.get("job_uuid")}
    all_status_rows = [
        read_one_status(p)
        for p in sorted((ROOT / "uploads").glob("*/status.json"))
    ]
    current_status_rows = [r for r in all_status_rows if r["job_id"] in current_job_ids]
    new_catalog = [r for r in current_status_rows if "v12" in (r["catalog_version"] or "")]
    new_obls = [o for r in new_catalog for o in r["unresolved_obligations"]]

    wl_items = wl_data.get("items") or []
    counts = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "slot_status_breakdown": dict(Counter((r["slot_status"], r["status_reason"]) for r in current)),
        "slot_status_totals": dict(Counter(r["slot_status"] for r in current)),
        "slot_status_reason_totals": dict(Counter(r["status_reason"] for r in current)),
        "all_stored_runs": summarize(all_status_rows),
        "current_slot_runs": summarize(current_status_rows),
        "new_catalog_diagnostics": {
            "records": len(new_catalog),
            "blocking_instances": len(new_obls),
            "rule_no_reason_instances": sum("rule_no_reason" in o.get("reason_codes", []) for o in new_obls),
            "no_diagnostic_instances": sum(
                not o.get("reason_codes") and not o.get("root_cause_key") for o in new_obls
            ),
        },
        "worklist": {
            "count": len(wl_items),
            "review_status": dict(Counter(r.get("review_status") for r in wl_items)),
            "signoff_only_count": sum(bool(r.get("signoff_only")) for r in wl_items),
            "pending_check_documents": sum((r.get("pending_checks") or 0) > 0 for r in wl_items),
            "pending_checks": sum(r.get("pending_checks") or 0 for r in wl_items),
            "pending_finding_documents": sum((r.get("pending_findings") or 0) > 0 for r in wl_items),
        },
    }
    # Normalize tuple keys for JSON serialization
    counts["slot_status_breakdown"] = {
        f"{k[0]}|{k[1]}": v for k, v in counts["slot_status_breakdown"].items()
    }
    (OUT_DIR / "counts.json").write_text(
        json.dumps(counts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    before_counts = json.loads((BASELINE_DIR / "counts.json").read_text(encoding="utf-8"))
    before_wl = json.loads((BASELINE_DIR / "worklist_readback.json").read_text(encoding="utf-8")).get("items") or []
    print("=== BEFORE vs AFTER (current_slot_runs) ===")
    print("BEFORE:", json.dumps({
        k: before_counts["current_slot_runs"][k]
        for k in ("count", "statuses", "has_blocking_checks", "blocking_checks", "review_reason_documents")
    }, ensure_ascii=False))
    print("AFTER: ", json.dumps({
        k: counts["current_slot_runs"][k]
        for k in ("count", "statuses", "has_blocking_checks", "blocking_checks", "review_reason_documents")
    }, ensure_ascii=False))
    print("=== BEFORE vs AFTER (worklist) ===")
    print("BEFORE worklist:", json.dumps({
        "count": len(before_wl),
        "review_status": dict(Counter(r.get("review_status") for r in before_wl)),
        "pending_check_documents": sum((r.get("pending_checks") or 0) > 0 for r in before_wl),
        "pending_checks": sum(r.get("pending_checks") or 0 for r in before_wl),
        "pending_finding_documents": sum((r.get("pending_findings") or 0) > 0 for r in before_wl),
    }, ensure_ascii=False))
    print("AFTER  worklist:", json.dumps(counts["worklist"], ensure_ascii=False))
    print("AFTER  slot_status_breakdown:", json.dumps(counts["slot_status_breakdown"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main_async())
