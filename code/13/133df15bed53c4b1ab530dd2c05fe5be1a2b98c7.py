"""保留历史账本，登记本轮只读复核；不改生产代码或原验收材料。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ACCEPTANCE = ROOT / "outputs/pdf_acceptance_shanghai2022_20261021"
LEDGER = ROOT / "outputs/pdf_trusted_ingest_20261020/ledger.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


findings = json.loads((OUT / "audit_findings.json").read_text())
saved = json.loads((OUT / "saved_evidence_review.json").read_text())
fresh = json.loads((OUT / "fresh_native_reproduction.json").read_text())
baseline = json.loads((ACCEPTANCE / "baseline/code_fingerprints.json").read_text())
assert not fresh["changed_code_files"]
changed = [name for name, sha in baseline.items()
           if not (ROOT / name).is_file() or digest(ROOT / name) != sha]
assert not changed, changed
assert saved["original_hashes_verified"] == 48
assert saved["saved_database_rows_verified"] == 16215
assert len(fresh["results"]) == 4
assert all(x["fresh_source_render_tile_plan_reproduced"]
           for x in saved["ocr_geometry_review"])

old_bytes = LEDGER.read_bytes()
old_sha = hashlib.sha256(old_bytes).hexdigest()
history = LEDGER.parent / "ledger_revisions" / f"{old_sha}.json"
history.parent.mkdir(exist_ok=True)
if history.exists():
    assert history.read_bytes() == old_bytes
else:
    with history.open("xb") as handle:
        handle.write(old_bytes)

ledger = json.loads(old_bytes)
now = datetime.now(timezone.utc).isoformat()
review_prefix = "../pdf_acceptance_review_20261021/"
report_ref = review_prefix + "中文复核报告.txt"
findings_ref = review_prefix + "audit_findings.json"
review_id = "shanghai2022_budget_acceptance_review_20261021"
reopened = {
    "M2": ["REVIEW-CROSSPAGE", "REVIEW-DUPLICATE", "REVIEW-FIN04", "REVIEW-SG",
           "REVIEW-NAME", "REVIEW-OWNER"],
    "M3": ["REVIEW-FIN04", "REVIEW-SG", "REVIEW-NAME", "REVIEW-YEAR"],
    "M4": ["REVIEW-OCR-STRIDE", "REVIEW-OCR-PRIORITY", "REVIEW-OCR-ORIENTATION"],
    "M5": ["REVIEW-RATE"],
}
for name, issue_ids in reopened.items():
    milestone = ledger["milestones"][name]
    if milestone.get("current_review_id") != review_id:
        milestone["prior_review_state"] = dict(milestone)
    milestone.update({
        "status": "reopened_confirmed_software_gaps",
        "implementation": False,
        "tests": False,
        "real_source_acceptance": False,
        "runtime_acceptance": False,
        "independent_material_acceptance": False,
        "current_review_id": review_id,
        "open_review_issues": issue_ids,
        "current_gate_note": "历史通过结果保存在prior_review_state及账本归档；当前新增反例尚未修复，新增核销测试和使用链路验证尚未通过。",
        "note": "上轮全部代码完成的判断已撤回；当前开放缺陷、原因和核销标准见本轮复核报告。",
    })
    for ref in (report_ref, findings_ref):
        if ref not in milestone.setdefault("evidence", []):
            milestone["evidence"].append(ref)

ledger["milestones"]["M1"]["current_review"] = {
    "id": review_id,
    "status": "saved_47_native_records_consistency_verified_only",
    "raw_fact_rows_compared": 16215,
    "scope": "已生成事实的保存记录一致，不代表源格完整性或当前实时服务再验通过。",
    "evidence": review_prefix + "saved_evidence_review.json",
}
ledger["milestones"]["M0"]["current_review"] = {
    "id": review_id,
    "new_materials": 48,
    "material_kind": "budget_only",
    "independent_human_cell_truth": False,
    "scope": "本批经诊断后作为回归集；正式验收材料类型、地区、年份及困难页配额仍未核销。",
}
existing_ids = {issue["id"] for issue in ledger["issues"]}
for finding in findings["findings"]:
    if finding["id"] in existing_ids:
        continue
    ledger["issues"].append({
        "id": finding["id"],
        "source_issue": finding["source_defects"],
        "title": finding["finding"],
        "status": "open_confirmed_not_fixed",
        "priority": finding["priority"],
        "code_implemented": False,
        "automated_tests_passed": None,
        "original_evidence_review": finding["state"],
        "independent_material_acceptance": False,
        "runtime_acceptance": False,
        "closure_condition": finding["closure"],
        "acceptance_evidence": [report_ref, findings_ref],
    })
ledger["current_review"] = {
    "id": review_id,
    "conclusion": "not_accepted_software_remediation_reopened",
    "prior_claim_correction": findings["prior_claim_correction"],
    "reopened_milestones": list(reopened),
    "report": report_ref,
    "findings": findings_ref,
    "pre_review_ledger_sha256": old_sha,
    "pre_review_ledger_archive": str(history.relative_to(LEDGER.parent)),
    "production_code_changed_by_review": False,
}
ledger["updated_at"] = now
temp = LEDGER.with_suffix(".review.tmp")
with temp.open("x", encoding="utf-8") as handle:
    handle.write(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
temp.replace(LEDGER)
assert json.loads(LEDGER.read_text())["current_review"]["id"] == review_id
assert digest(history) == old_sha

artifact_names = ["中文复核报告.txt", "audit_findings.json",
                  "fresh_native_reproduction.json", "saved_evidence_review.json",
                  "reproduce_native.py", "review_saved_evidence.py", "finalize_review.py"]
receipt = {
    "schema_version": "gbc.acceptance-review-receipt.v1",
    "created_at": now,
    "conclusion": "not_accepted_confirmed",
    "production_code_files_compared": len(baseline),
    "changed_production_code_files": changed,
    "saved_database_rows_compared": 16215,
    "fresh_native_reparsed_originals": 4,
    "new_ocr_requests": 0,
    "live_database_connections": 0,
    "browser_suite_rerun": False,
    "ledger_prior_sha256": old_sha,
    "ledger_current_sha256": digest(LEDGER),
    "ledger_prior_archive": str(history),
    "artifacts": {name: digest(OUT / name) for name in artifact_names},
    "source_acceptance_evidence": {
        name: digest(ACCEPTANCE / name)
        for name in ["中文验收报告.txt", "defects.csv", "逐份执行结果.csv",
                     "source_manifest.json", "baseline/code_fingerprints.json"]
    },
}
(OUT / "review_receipt.json").write_text(
    json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"conclusion": receipt["conclusion"],
                  "code_files_checked": len(baseline),
                  "changed_code_files": changed,
                  "review_findings": len(findings["findings"]),
                  "reopened_milestones": list(reopened),
                  "report": str(OUT / "中文复核报告.txt"),
                  "historical_ledger": str(history)}, ensure_ascii=False))
