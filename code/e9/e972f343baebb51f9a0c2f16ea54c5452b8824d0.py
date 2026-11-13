#!/usr/bin/env python3
"""Merge preflight, run state and current results into the final RUN_IDENTITY.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
MAIN = Path("/Volumes/workSSD/software development/GovBudgetChecker")
BUSINESS = Path("/Volumes/workSSD/software development/gbc-worktrees/unified-closeout-20261103")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    preflight = json.loads((RUN / "preflight.json").read_text())
    manifest = json.loads((MAIN / "outputs/unified_closeout_round6_review_codex_20261104/diagnostic_manifest_10.json").read_text())
    manifest_by_order = {i["order"]: i for i in manifest["items"]}
    state = json.loads((RUN / "run_state.json").read_text())
    results = json.loads((RUN / "painpoint_results.json").read_text())

    scripts = {}
    for path in sorted((RUN / "scripts").glob("*.py")):
        scripts[path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}

    first_doc = None
    for doc in results["per_document"]:
        if doc.get("current_status") != "error" and doc.get("rule_execution_summary"):
            first_doc = doc
            break

    identity = {
        "kind": "real_painpoint_diag_final_identity",
        "run_id": state.get("run_id"),
        "status": "AWAITING_CODEX_EFFECT_REVIEW",
        "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": {
            "samples": 10,
            "document_type": "final accounts only (dept_final); no budget samples",
            "year_coverage": [2022, 2023, 2024, 2025],
            "mode_requested": "dual + use_local_rules=true + use_ai_assist=false",
            "ai": "external AI disabled for the whole run; 0 calls observed",
            "ocr": "existing local OCR service enabled; not triggered by any sample",
            "not_a_blind_test": True,
            "not_accuracy_sample": True,
        },
        "fixed_identities": preflight["worktrees"],
        "scripts": scripts,
        "org_selection": {
            "path": str(RUN / "org_selection.json"),
            "sha256": sha(RUN / "org_selection.json"),
            "kind": "operator selection mirroring batch-upload department/unit choice",
            "note": "Not an automatic recovery; each choice is justified by original cover/目录 plus catalog level.",
        },
        "manifest": preflight["manifest"],
        "samples": [
            {
                "order": item["order"],
                "job_id": item["job_id"],
                "historical_job_id": item["job_id"],
                "filename": item["filename"],
                "year": item["year_expected"],
                "historical_doc_type": item["historical_doc_type"],
                "historical_mode": item["historical_mode"],
                "historical_ai_state": item["historical_ai_state"],
                "historical_revision": item["historical_revision"],
                "historical_unfinished_total": item["unfinished_total"],
                "pdf_path": manifest_by_order[item["order"]]["pdf_path"],
                "pdf_sha256": item.get("pdf_sha256"),
                "pdf_sha256_match_manifest": item.get("pdf_sha256_match"),
                "pdf_classification": item.get("pdf_classification"),
                "historical_status_path": str(RUN / f"evidence/historical/{item['job_id']}/status.json"),
                "historical_status_sha256": item.get("historical_status_sha256"),
                "historical_status_sha256_match": item.get("historical_status_match"),
                "current_job_id": next((d.get("current_job_id") for d in results["per_document"] if d["order"] == item["order"]), None),
                "current_status": next((d.get("current_status") for d in results["per_document"] if d["order"] == item["order"]), None),
                "current_error": next((d.get("current_error") for d in results["per_document"] if d["order"] == item["order"]), None),
            }
            for item in preflight["items"]
        ],
        "configuration": {
            "cwd": str(BUSINESS),
            "api_module": str(BUSINESS / "api/main.py"),
            "worker_module": str(BUSINESS / "api/worker.py"),
            "python_executable": preflight["environment"]["python_executable"],
            "python_version": preflight["environment"]["python"],
            "api_port": state.get("api_port"),
            "postgres": {
                "container": state.get("container"),
                "port": state.get("pg_port"),
                "isolation": "disposable Docker postgres:16; production fiscal_postgres untouched",
            },
            "database_url_redacted": "postgresql://validator:<redacted>@127.0.0.1:%s/validation" % state.get("pg_port"),
            "env": {
                "TESTING": "false",
                "PYTHON_DOTENV_DISABLED": "1",
                "GOVBUDGET_AUTH_ENABLED": "true",
                "AI_ASSIST_ENABLED": "false",
                "AI_ASSIST_REQUIRED": "false",
                "AI_ENABLED": "false",
                "AI_EXTRACTOR_DIRECT_FALLBACK": "false",
                "AI_EXTRACTOR_URL": "http://127.0.0.1:9/ai/extract/v1 (discard guard)",
                "AI_MODEL_SETTINGS_PATH": str(RUN / "db_env/ai/ai-model-settings.json"),
                "AI_MODEL_SETTINGS_SHA256": sha(RUN / "db_env/ai/ai-model-settings.json"),
                "OCR_ENABLED": "true",
                "OCR_SERVICE_URL": "http://127.0.0.1:9010",
                "OCR_MODEL_FINGERPRINT": (preflight.get("ocr_preflight", {}).get("body", {}) or {}).get("model_fingerprint"),
                "OCR_IMAGE_GATE": "true",
                "JOB_QUEUE_ENABLED": "true",
                "JOB_QUEUE_WORKERS": "1",
                "JOB_QUEUE_ROLE": "api/worker",
                "GBC_TEMPLATE_STORAGE": str(RUN / "db_env/uploads/.templates"),
                "DISCLOSURE_CALENDAR_PATH": str(RUN / "db_env/disclosure-calendar.json"),
            },
        },
        "fingerprints": {
            "ocr_health": preflight.get("ocr_preflight"),
            "catalog_version": (results["identity"] or {}).get("catalog_version_current"),
            "catalog_fingerprint": (results["identity"] or {}).get("catalog_fingerprint_current"),
            "rule_policy_version": (first_doc or {}).get("versions"),
            "rule_execution_summary": (first_doc or {}).get("rule_execution_summary"),
            "input_processing_identity": (first_doc or {}).get("document_profile") and None,
        },
        "ai_evidence": {
            "historical_ai_succeeded_in_batch": [3, 5, 9, 10],
            "historical_ai_succeeded_outside_batch": 1,
            "current_model_calls_observed": state.get("ai_model_calls_observed"),
            "log_egress_scan": state.get("ai_egress_log_scan"),
            "per_document_ai_state": {str(d["order"]): (d.get("ai_execution") or {}).get("state") for d in results["per_document"]},
        },
        "ocr_evidence": {
            "triggered": False,
            "reason": "page_extraction.scanned_pages=[] and table_recovery_pages=[] on all 10; only cover pages lacked text, and OCR page selection targets table-reading recovery, so no page qualified. Health alone is not execution evidence.",
            "per_document": {str(d["order"]): d.get("ocr") for d in results["per_document"]},
        },
        "artifacts": {
            "preflight": str(RUN / "preflight.json"),
            "historical_status_snapshots": str(RUN / "evidence/historical"),
            "current_evidence_index": str(RUN / "evidence/current"),
            "painpoint_results_json": str(RUN / "painpoint_results.json"),
            "painpoint_results_csv": str(RUN / "painpoint_results.csv"),
            "run_state": str(RUN / "run_state.json"),
            "db_dump": state.get("db_dump"),
            "db_dump_sha256": state.get("db_dump_sha256"),
            "db_dump_after_repro": state.get("db_dump_after_repro"),
            "db_dump_after_repro_sha256": state.get("db_dump_after_repro_sha256"),
            "current_index": str(RUN / "CURRENT_INDEX.json"),
            "current_index_sha256": sha(RUN / "CURRENT_INDEX.json") if (RUN / "CURRENT_INDEX.json").exists() else None,
            "diagnostic_summary": str(RUN / "DIAGNOSTIC_SUMMARY.json"),
            "diagnostic_summary_sha256": sha(RUN / "DIAGNOSTIC_SUMMARY.json") if (RUN / "DIAGNOSTIC_SUMMARY.json").exists() else None,
            "api_log": str(RUN / "logs/api.log"),
            "worker_log": str(RUN / "logs/worker.log"),
            "verification_ledger": str(RUN / "VERIFICATION_LEDGER.json"),
            "verification_ledger_sha256": sha(RUN / "VERIFICATION_LEDGER.json") if (RUN / "VERIFICATION_LEDGER.json").exists() else None,
            "verified_recoveries": str(RUN / "VERIFIED_RECOVERIES.md"),
            "remaining_causes": str(RUN / "REMAINING_CAUSES.md"),
            "match_failures": str(RUN / "MATCH_FAILURES.md"),
            "decision": str(RUN / "DECISION.txt"),
        },
        "headline": {
            "a_layer_total": 131,
            "a_layer_completed_current": 42,
            "a_layer_still_insufficient": 74,
            "a_layer_ai_not_run": 5,
            "a_layer_blocked_by_job_error": 10,
            "b_applicable": 556,
            "b_completed": 445,
            "b_unresolved": 111,
            "b_not_applicable": 2,
            "b_ai_not_run": 9,
            "formal_issues": {"total": 90, "high": 5, "medium": 31, "low": 17, "info": 37},
            "high_issue_verdicts": {"true_positive": 4, "false_positive": 1},
            "medium_sampled": 4,
            "ocr_triggered_documents": 0,
            "model_calls_observed": 0,
            "historical_ai_succeeded_in_batch": 4,
            "historical_ai_succeeded_outside_batch": 1,
        },
        "preservation": {
            "production_services_untouched": True,
            "production_jobs_not_reanalyzed": True,
            "production_db_untouched": True,
            "production_uploads_untouched": True,
            "business_code_modified": False,
            "comparator_modified": False,
            "gold_or_b1_modified": False,
            "superseded_attempt": str(RUN / "superseded_attempt1"),
        },
    }
    (RUN / "RUN_IDENTITY.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2))
    print(json.dumps({"run_id": identity["run_id"], "scripts": list(scripts), "samples": len(identity["samples"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
