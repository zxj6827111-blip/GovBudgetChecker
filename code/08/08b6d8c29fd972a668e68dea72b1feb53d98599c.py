"""Isolated reacceptance probes for ZC07 metrics; no real corpus or services."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("zc07_probe", ROOT / "scripts/run_zc07_acceptance.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
from scripts.evaluate_golden_corpus import _consume_truth_annotations

results = {}
annotation = {
    "annotation_id": "T1", "truth_id": "T1", "label": "defect", "rule_id": "C-001",
    "page": 1, "expected_severity": "high", "evidence": "amount 123.45",
    "truth_region": {"page": 1, "bbox": [10, 10, 30, 30]},
}
correct = {
    "rule": "C-001", "severity": "high", "page": 1, "evidence_text": "amount 123.45",
    "message": "amount 123.45", "bbox": [10, 10, 30, 30],
    "location_level": "exact_cell", "verification_status": "verified",
    "evidence_verification": {"status": "verified"},
}
wrong = {**correct, "page": 9, "bbox": [900, 900, 950, 950]}
results["outside_truth_region"] = gate._evaluate_exact_location([
    {"golden": {"annotations": [annotation]}, "report": {
        "matched": [{"annotation_id": "T1", "finding": wrong}]
    }}
], ROOT)
matched, _, _ = _consume_truth_annotations([annotation], [correct], set())
results["standard_matched_interface"] = {
    "matched_keys": list(matched[0]),
    "metric": gate._evaluate_exact_location([
        {"golden": {"annotations": [annotation]}, "report": {"matched": matched}}
    ], ROOT),
    "legacy_findings_read_count": len(gate._iter_run_issues({"findings": [correct]})),
}
results["missing_verifications_omitted"] = gate._evaluate_evidence_authenticity([
    {"run": {"issues": [correct] + [{"id": f"missing-{i}"} for i in range(99)]}}
])
results["required_checks_omitted"] = gate._evaluate_receipts([
    {"run": {"meta": {
        "required_checks": [f"check-{i}" for i in range(100)],
        "check_receipts": [{"sub_check_id": "check-0", "operand_fact_ids": ["fact-0"],
                            "formula": "x", "status": "pass"}],
    }}}
])
with tempfile.TemporaryDirectory(prefix="govbudget-gate-reacceptance-") as temp:
    root = Path(temp)
    first, repeat = root / "first.json", root / "repeat.json"
    first.write_text(json.dumps({"issues": [correct]}))
    repeat.write_text(first.read_text())
    try:
        results["export_agreement"] = gate._evaluate_export_agreement({"documents": [
            {"export_paths": [str(first), str(repeat)]}
        ]}, root)
    except Exception as error:
        results["export_agreement"] = {"exception": type(error).__name__, "message": str(error)}
    first.write_text(json.dumps({"legacy": {"issues": [], "analysis_conclusion": "incomplete"}}))
    repeat.write_text(json.dumps({"legacy": {"issues": [], "analysis_conclusion": "no_findings"}}))
    results["repeatability_different_conclusions"] = gate._evaluate_repeatability({"documents": [
        {"replay_path": str(first), "repeat_replay_path": str(repeat)}
    ]}, root, "legacy")

print(json.dumps(results, ensure_ascii=False, indent=2))
