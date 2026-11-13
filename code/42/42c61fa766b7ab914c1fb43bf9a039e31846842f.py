from pathlib import Path
import hashlib
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
import pymupdf

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_zc07_acceptance import _evaluate_export_agreement

def read(path):
    return json.loads(path.read_text())

checks = {}
log = (OUT / "pytest_pass.log").read_text()
assert re.search(r"2651 passed, 137 skipped", log) and "FAILED " not in log
checks["pytest"] = {"passed": 2651, "skipped": 137, "failed": 0}
assert "All checks passed!" in (OUT / "ruff_final.log").read_text()
checks["ruff"] = "passed"
assert "All installed packages are compatible" in (OUT / "dependency_check.log").read_text()
assert "环境变量三方对账通过" in (OUT / "env_consistency.log").read_text()
assert read(OUT / "log_message_safety.json")["violation_count"] == 0
assert (OUT / "diff_check.log").read_text() == ""
checks["dependencies_environment_logs_diff"] = "passed"
manifest = read(OUT / "independent_intake_final/code_manifest.json")
changed = [name for name, digest in manifest.items()
           if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
assert not changed, changed
checks["frozen_source"] = {"file_count": len(manifest), "changed_files": changed}

receipts = []
export_checks = []
for run in ("services_run1", "services_run2"):
    receipt = read(OUT / run / "service_receipt.json")
    assert receipt["status"] == "passed", (run, receipt["errors"])
    assert receipt["source_unchanged_during_run"] and receipt["cleanup"] == "complete"
    assert len(receipt["scenarios"]) == receipt["expected_scenario_count"] == 7
    for scene in receipt["scenarios"]:
        assert scene["service_validation_passed"], (run, scene["scenario"])
        assert scene["structured_ingest_validation"]["passed"]
        assert scene["export_agreement"]["export_agreement_rate"] == 1
        assert all(scene["repeated_downloads_identical"].values())
        for view, folder, recorded in [("initial", OUT / run / scene["scenario"], scene["exports"])] + (
            [("corrected", OUT / run / scene["scenario"] / "corrected", scene["correction"]["exports"])] if "correction" in scene else []
        ):
            paths = [folder / ("report." + suffix) for suffix in ("json", "csv", "docx", "pdf")]
            assert all(hashlib.sha256(path.read_bytes()).hexdigest() == recorded[path.name] for path in paths)
            agreement = _evaluate_export_agreement({"documents": [{"doc_id": scene["job_id"], "export_paths": [str(path) for path in paths]}]}, ROOT)
            assert agreement["export_agreement_rate"] == 1, (run, scene["scenario"], view, agreement)
            export_checks.append({"run": run, "scenario": scene["scenario"], "view": view, "artifact_identity": "matched", **agreement})
        if "correction" in scene:
            assert scene["correction_validation_passed"]
            assert scene["correction"]["cache_readback_validation_passed"]
            assert scene["correction"]["original_parse_basis_preserved"]
    scan = next(s for s in receipt["scenarios"] if s["scenario"] == "original_scanned_budget")
    assert scan["source_sha256"] == "5978b569d1f4bf58d07281db4150e72b9cf867fa45a051da4976aabdc6a5eec7"
    assert scan["ocr"]["requested_pages"] == list(range(1, 22))
    assert [p["page"] for p in scan["ocr"]["pages"] if p["ok"]] == list(range(1, 22))
    assert not scan["ocr"]["failed_pages"]
    assert scan["pipeline_budget"]["total_seconds"] == 3120
    assert 0 < scan["pipeline_budget"]["elapsed_seconds"] < 3120
    status = read(OUT / run / "original_scanned_budget/status.json")
    meta = status["result"]["meta"]
    assert meta["rule_execution_summary"]["rule_statuses"]["BUD-001"] == "insufficient_data"
    assert meta["evidence_completeness"]["formal_issue_total"] == 0
    assert not any("缺失预算表:" in row["message"] for row in status["result"]["issues"]["all"])
    assert meta["structure_view"]["unknown_pages"] == list(range(1, 22))
    display_text = {}
    scan_dir = OUT / run / "original_scanned_budget"
    with pymupdf.open(scan_dir / "report.pdf") as pdf:
        display_text["pdf"] = "\n".join(page.get_text() for page in pdf)
    with zipfile.ZipFile(scan_dir / "report.docx") as archive:
        xml = ET.fromstring(archive.read("word/document.xml"))
        display_text["docx"] = "".join(xml.itertext())
    for suffix, displayed in display_text.items():
        assert "缺失预算表" not in displayed, (run, suffix)
        assert "结构识别尚未完成" in displayed, (run, suffix)
    mixed = next(s for s in receipt["scenarios"] if s["scenario"] == "original_mixed_final")
    assert float(mixed["correction"]["original_raw_value"]) == 746.62
    assert float(mixed["correction"]["corrected_raw_value"]) == 756.62
    facts = read(OUT / run / "original_mixed_final/fact_context.json")["facts"]
    assert not [fact for fact in facts if "栏次" in (fact.get("subject_name") or "") or "行次" in (fact.get("subject_name") or "")]
    assert any(fact["indicator"] == "total_actual" and float(fact["raw_value"]) == 746.62 for fact in facts)
    checks[run] = {"scenarios_passed": 7, "scan_elapsed_seconds": scan["pipeline_budget"]["elapsed_seconds"],
                   "scan_processed_pages": 21, "scan_formal_issue_total": 0,
                   "mixed_original_raw": mixed["correction"]["original_raw_value"],
                   "mixed_corrected_raw": mixed["correction"]["corrected_raw_value"],
                   "cache_readback_passed": True, "cleanup": receipt["cleanup"]}
    receipts.append(receipt)

def signature(path):
    v = read(path)
    result = v["result"]
    return {
        "status": v["status"],
        "rules": result["meta"]["rule_execution_summary"]["rule_statuses"],
        "issues": [{key: row.get(key) for key in ("rule_id", "severity", "message", "location")}
                   for row in result["issues"]["all"]],
        "formal_issue_total": result["meta"]["evidence_completeness"]["formal_issue_total"],
    }

for scene in ("original_scanned_budget", "original_mixed_final"):
    for suffix in (("status.json",) if scene == "original_scanned_budget" else ("status.json", "corrected/status.json")):
        assert signature(OUT / "services_run1" / scene / suffix) == signature(OUT / "services_run2" / scene / suffix), (scene, suffix)
(OUT / "export_self_check.json").write_text(json.dumps(export_checks, ensure_ascii=False, indent=2) + "\n")
checks["exports"] = {"independently_rechecked_views": len(export_checks), "four_format_agreement": 1.0, "recorded_sha_matched": True}
checks["repeat_original_outcomes"] = "identical_after_excluding_job_revision_and_timestamps"
preparation = read(OUT / "independent_intake_final/preparation_receipt.json")
assert preparation["actual_frozen_documents"] == preparation["actual_human_defect_groups"] == 0
gate = read(OUT / "independent_handoff_gate_final.json")
assert gate["status"] == "incomplete" and gate["passed"] is False
checks["blind_acceptance"] = {"status": "pending_independent_humans_and_materials", "business_metrics": None}
runtime = read(OUT / "ocr_runtime.json")
assert runtime["cleanup"]["status"] == "complete"
assert "No broken requirements found" in (OUT / "ocr_dependency_check.log").read_text()
assert all(row["postgres_removed"] and row["temporary_workspace_removed"] and row["api_and_worker_stopped"] and row["receipt_logs_closed"] for row in runtime["cleanup"]["isolated_service_checks"])
checks["final_cleanup"] = runtime["cleanup"]
checks["ocr_dependencies"] = "passed"
checks["status"] = "passed_for_five_remediations_development_scope"
(OUT / "self_check.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(checks, ensure_ascii=False, indent=2))
