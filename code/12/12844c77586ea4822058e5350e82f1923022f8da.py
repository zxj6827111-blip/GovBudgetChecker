"""Synthetic legal-contract probes. These are not real blind acceptance scores."""
import copy
import csv
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import fitz
sys.path.append("/Users/zhangxingjin/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages")
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FIX = OUT / "gate_fixture"
FIX.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("zc07_round5_probe", ROOT / "scripts/run_zc07_acceptance.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
from scripts.evaluate_golden_corpus import _consume_truth_annotations
from src.schemas.review_report import ReviewReport

doc_id = "SYNTHETIC-R5-GATE"
source_dir = FIX / "corpus" / doc_id
source_dir.mkdir(parents=True, exist_ok=True)
source = source_dir / "source.pdf"
c = canvas.Canvas(str(source), pagesize=(600, 800))
c.drawString(100, 650, "amount 123.45")
c.save()
with fitz.open(source) as pdf:
    rect = list(pdf[0].search_for("amount 123.45")[0])
    pdf[0].get_pixmap().save(FIX / "source.png")
source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
evidence = {
    "file_hash": source_sha, "physical_page": 1, "source_block_id": "synthetic-block-1",
    "quote": "amount 123.45", "verification_status": "verified", "location_level": "exact_span", "bbox": rect,
}
evidence["evidence_id"] = hashlib.sha1("|".join((source_sha, "1", evidence["source_block_id"], evidence["quote"])).encode()).hexdigest()[:16]
annotation = {"annotation_id": "T1", "truth_id": "T1", "label": "defect", "rule_id": "C-001", "page": 1, "expected_severity": "high", "evidence": "amount 123.45", "truth_region": {"page": 1, "bbox": rect}}
finding = {"id": "F1", "rule": "C-001", "severity": "high", "page": 1, "message": "amount 123.45", "evidence_text": "amount 123.45", "bbox": rect, "location_level": "exact_span", "verification_status": "verified", "evidence_verification": {"status": "verified"}, "evidence": [evidence]}
matched, _, _ = _consume_truth_annotations([annotation], [finding], set())
row = {"doc_id": doc_id, "golden": {"sha256": source_sha, "labels": [annotation]}, "run": {"findings": [finding]}, "report": {"matched": matched}}
results = {"scope": "synthetic_subfunction_contracts_only", "source_sha256": source_sha}
results["official_labels_standard_authenticated_match"] = gate._evaluate_exact_location([row], FIX)
results["authentic_reference_positive"] = gate._evaluate_evidence_authenticity([row], FIX)
for name, change in [
    ("whole_page_region", {"bbox": [0, 0, 600, 800], "location_level": "region", "verification_status": "unverified"}),
    ("wrong_bbox", {"bbox": [400, 400, 500, 500]}),
]:
    changed = copy.deepcopy(row)
    changed["report"]["matched"][0]["finding"].update(change)
    results[name] = gate._evaluate_exact_location([changed], FIX)
changed = copy.deepcopy(row)
changed["run"]["findings"][0]["evidence"][0]["file_hash"] = "a" * 64
results["false_source_hash"] = gate._evaluate_evidence_authenticity([changed], FIX)
results["claimed_verified_without_reference"] = gate._evaluate_evidence_authenticity([{"run": {"findings": [{"evidence_verification": {"status": "verified"}}]}}], FIX)

receipt = {"rule_id": "BUD-107", "sub_check_id": "T1:income_budget:text_vs_table", "operand_fact_ids": ["known-income"], "formula": "narrative == table", "status": "pass", "tolerance": "0.5"}
expected = {"rule_id": receipt["rule_id"], "sub_check_id": receipt["sub_check_id"]}
receipt_row = {"golden": {"applicable_sub_checks": [expected]}, "run": {"meta": {"check_receipts": [receipt]}}}
results["receipt_contract_forms"] = {}
for name, requirements in [("object", [expected]), ("double_colon", [receipt["rule_id"] + "::" + receipt["sub_check_id"]]), ("single_colon", [receipt["rule_id"] + ":" + receipt["sub_check_id"]])]:
    value = copy.deepcopy(receipt_row)
    value["golden"]["applicable_sub_checks"] = requirements
    results["receipt_contract_forms"][name] = gate._evaluate_receipts([value])
results["all_missing_frozen_denominator"] = gate._evaluate_receipts([{**receipt_row, "golden": {}}])
results["one_document_missing_frozen_denominator"] = gate._evaluate_receipts([receipt_row, {**receipt_row, "golden": {}}])
value = copy.deepcopy(receipt_row)
value["golden"]["applicable_sub_checks"] = [{"rule_id": "BUD-107", "sub_check_id": "required-" + str(i)} for i in range(100)]
value["golden"]["applicable_sub_checks"][0] = expected
results["one_of_100_receipts"] = gate._evaluate_receipts([value])
value = copy.deepcopy(receipt_row)
value["run"]["meta"]["check_receipts"].append({**receipt, "status": "execution_error", "incomplete_reason": "injected failure"})
results["conflicting_duplicate_receipts"] = gate._evaluate_receipts([value])
value = copy.deepcopy(row)
value["golden"]["labels"].append({**annotation, "annotation_id": "T2", "truth_id": "T2", "truth_region": None})
results["one_defect_missing_truth_region"] = gate._evaluate_exact_location([value], FIX)

basis = {"source_sha256": source_sha, "configuration_fingerprint": "synthetic-config-1", "version_fingerprint": "synthetic-version-1"}
base = {"repeatability_basis": basis, "legacy": {"findings": [finding], "analysis_conclusion": "issues_found", "meta": {"check_receipts": [receipt]}}}
first, repeat = FIX / "first.json", FIX / "repeat.json"
manifest = {"documents": [{"doc_id": doc_id, "golden_sha256": source_sha, "replay_path": str(first), "repeat_replay_path": str(repeat)}]}
first.write_text(json.dumps(base))
repeat.write_text(json.dumps(base))
results["repeat_identical_nonempty_positive"] = gate._evaluate_repeatability(manifest, FIX, "legacy")
for name, mutation in [("bbox_changed", "bbox"), ("receipt_status_changed", "status"), ("receipt_tolerance_changed", "tolerance"), ("reference_quote_changed", "quote")]:
    changed = copy.deepcopy(base)
    if mutation == "bbox":
        changed["legacy"]["findings"][0]["bbox"] = [400, 400, 500, 500]
    elif mutation == "status":
        changed["legacy"]["meta"]["check_receipts"][0]["status"] = "insufficient_data"
    elif mutation == "tolerance":
        changed["legacy"]["meta"]["check_receipts"][0]["tolerance"] = "99"
    else:
        changed["legacy"]["findings"][0]["evidence"][0]["quote"] = "unrelated quote"
    repeat.write_text(json.dumps(changed))
    results["repeat_" + name] = gate._evaluate_repeatability(manifest, FIX, "legacy")

# Complete four-format bundle with two nonempty issues and real DOCX/PDF pixels.
issues = [
    {"id": "F1", "rule_id": "C-001", "title": "IssueAlpha", "severity": "high", "message": "amount100", "suggestion": "VerifyAlpha", "fact_ids": [], "evidence_ids": []},
    {"id": "F2", "rule_id": "C-002", "title": "IssueBeta", "severity": "medium", "message": "amount200", "suggestion": "VerifyBeta", "fact_ids": [], "evidence_ids": []},
]
report = ReviewReport(schema_version=2, job_id="synthetic-r5-gate", analysis_revision="synthetic-rev-1", issues=issues, conclusion={"analysis_conclusion": "issues_found"}).to_dict()
digest = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
export_json, export_csv, export_docx, export_pdf = (FIX / ("export" + suffix) for suffix in (".json", ".csv", ".docx", ".pdf"))
export_json.write_text(json.dumps({"analysis_revision": report["analysis_revision"], "review_report_sha256": digest, "review_report": report}))
columns = ["record_type", "issue_id", "rule_id", "title", "severity", "page", "bbox", "location_level", "verification_status", "check_relation_id", "fact_ids", "evidence_ids", "human_decision", "human_decision_note", "decision_revision", "decision_actor", "decision_inherited", "analysis_revision", "analysis_conclusion", "decision_identity_fingerprint", "review_report_sha256", "quality_status", "conclusion_scope", "manual_review_type", "check_id", "check_status", "incomplete_reason", "formula", "operand_fact_ids", "issue_refs", "conclusion_json", "message"]
with export_csv.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    writer.writerow({"record_type": "summary", "analysis_revision": report["analysis_revision"], "review_report_sha256": digest, "conclusion_json": json.dumps(report["conclusion"])})
    for issue in issues:
        writer.writerow({"record_type": "issue", "issue_id": issue["id"], **{key: value for key, value in issue.items() if key not in {"id", "suggestion", "fact_ids", "evidence_ids"}}, "fact_ids": "[]", "evidence_ids": "[]"})

def write_visible_exports(swapped=False):
    lines = ["Synthetic acceptance gate fixture", "REVIEW_REPORT_SHA256: " + digest, "CONCLUSION: issues_found"]
    for index, issue in enumerate(issues):
        message = issues[1-index]["message"] if swapped else issue["message"]
        lines.append(" | ".join(["ISSUE_ID|" + issue["id"] + "|", issue["rule_id"], issue["title"], issue["severity"], message, issue["suggestion"]]))
    xml = '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + ''.join('<w:p><w:r><w:t>' + escape(line) + '</w:t></w:r></w:p>' for line in lines) + '</w:body></w:document>'
    with zipfile.ZipFile(export_docx, "w") as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    pdf = canvas.Canvas(str(export_pdf), pagesize=(600, 800))
    pdf.setFont("Helvetica", 8)
    for index, line in enumerate(lines):
        pdf.drawString(30, 750-index*20, line)
    pdf.save()
    with fitz.open(export_pdf) as document:
        document[0].get_pixmap().save(FIX / ("export_swapped.png" if swapped else "export_positive.png"))

export_manifest = {"documents": [{"doc_id": doc_id, "export_paths": [str(p) for p in (export_json, export_csv, export_docx, export_pdf)]}]}
write_visible_exports()
results["four_formats_nonempty_positive"] = gate._evaluate_export_agreement(export_manifest, FIX)
write_visible_exports(swapped=True)
results["docx_pdf_issue_messages_swapped"] = gate._evaluate_export_agreement(export_manifest, FIX)
results["source_code_sha256"] = hashlib.sha256((ROOT / "scripts/run_zc07_acceptance.py").read_bytes()).hexdigest()
(OUT / "root_gate_probes.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps(results, ensure_ascii=False, indent=2))
