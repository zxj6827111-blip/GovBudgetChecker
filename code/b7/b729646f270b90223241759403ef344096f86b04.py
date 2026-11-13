"""Synthetic subfunction checks; no corpus, services or real acceptance scores."""
import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("zc07_current_probe", ROOT / "scripts/run_zc07_acceptance.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
from scripts.evaluate_golden_corpus import _consume_truth_annotations

annotation = {
    "annotation_id": "T1", "truth_id": "T1", "label": "defect", "rule_id": "C-001",
    "page": 1, "expected_severity": "high", "evidence": "amount 123.45",
    "truth_region": {"page": 1, "bbox": [10, 10, 30, 30]},
}
finding = {
    "id": "F1", "rule": "C-001", "severity": "high", "page": 1,
    "message": "amount 123.45", "evidence_text": "amount 123.45",
    "bbox": [10, 10, 30, 30], "location_level": "exact_cell",
    "verification_status": "verified", "evidence_verification": {"status": "verified"},
}
results = {}
matched, _, _ = _consume_truth_annotations([annotation], [finding], set())
results["official_labels_with_standard_match"] = gate._evaluate_exact_location([
    {"golden": {"labels": [annotation]}, "report": {"matched": matched}}
], ROOT)
region = {**finding, "bbox": [0, 0, 600, 800], "location_level": "region", "verification_status": "unverified"}
results["unverified_whole_page_region_counts_exact"] = gate._evaluate_exact_location([
    {"golden": {"annotations": [annotation]}, "report": {"matched": [{"annotation_id": "T1", "finding": region}]}}
], ROOT)
receipt = {
    "rule_id": "BUD-107", "sub_check_id": "T1:income_budget:text_vs_table",
    "operand_fact_ids": ["known-income"], "formula": "narrative == table", "status": "pass",
}
results["receipt_key_forms"] = {}
for name, expected in [
    ("actual_sub_check_id", [receipt["sub_check_id"]]),
    ("rule_prefixed_sub_check", [receipt["rule_id"] + ":" + receipt["sub_check_id"]]),
]:
    results["receipt_key_forms"][name] = gate._evaluate_receipts([
        {"golden": {"applicable_sub_checks": expected}, "run": {"meta": {"check_receipts": [receipt]}}}
    ])
results["receipt_without_frozen_required_set"] = gate._evaluate_receipts([
    {"golden": {}, "run": {"meta": {"check_receipts": [receipt]}}}
])
results["authenticity_ignores_structure_and_reference_validation"] = gate._evaluate_evidence_authenticity([
    {"run": {"findings": [{"id": "missing-reference", "evidence_status": "incomplete", "evidence_verification": {"status": "verified"}}]}}
])
with tempfile.TemporaryDirectory(prefix="govbudget-gate-third-") as tmp:
    root = Path(tmp)
    first, second = root / "first.json", root / "second.json"
    base = {"legacy": {"findings": [finding], "analysis_conclusion": "issues_found", "meta": {"check_receipts": [receipt]}}}
    changed = copy.deepcopy(base)
    changed["legacy"]["findings"][0].update({"bbox": [200, 200, 240, 240], "fact_ids": ["different-source"], "evidence_ids": ["different-ev"]})
    changed["legacy"]["meta"]["check_receipts"][0]["status"] = "insufficient_data"
    first.write_text(json.dumps(base)); second.write_text(json.dumps(changed))
    results["repeatability_changes_bbox_identity_and_completion"] = gate._evaluate_repeatability(
        {"documents": [{"replay_path": str(first), "repeat_replay_path": str(second)}]}, root, "legacy"
    )
    export_a = {"issues": [finding], "review_report": {"analysis_revision": "rev-fixed", "conclusion": {"analysis_conclusion": "issues_found"}, "human_decisions": []}}
    export_b = copy.deepcopy(export_a)
    export_b["review_report"]["conclusion"]["analysis_conclusion"] = "incomplete"
    export_b["review_report"]["human_decisions"] = [{"issue_id": "F1", "status": "no_issue"}]
    first.write_text(json.dumps(export_a)); second.write_text(json.dumps(export_b))
    results["export_ignores_conclusion_and_human_decision"] = gate._evaluate_export_agreement(
        {"documents": [{"export_paths": [str(first), str(second)]}]}, root
    )
    unsupported = {"items": [finding]}
    first.write_text(json.dumps(unsupported)); second.write_text(json.dumps(unsupported))
    results["unsupported_json_schema_counts_empty_consistent"] = gate._evaluate_export_agreement(
        {"documents": [{"export_paths": [str(first), str(second)]}]}, root
    )

print(json.dumps(results, ensure_ascii=False, indent=2))
