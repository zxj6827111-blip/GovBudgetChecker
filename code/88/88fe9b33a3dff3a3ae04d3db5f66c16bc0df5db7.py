"""Read-only R14 structure-rule replay from saved original service artifacts.

Writes only this directory. No OCR inference, network, AI, DB, or old output writes.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update(PYTHON_DOTENV_DISABLED="1", DATABASE_URL="",
                  GOVBUDGET_TEST_DATABASE_URL="", AI_ENABLED="false",
                  AI_ASSIST_ENABLED="false", OCR_ENABLED="false")
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
dotenv.dotenv_values = lambda *a, **k: {}
import pymupdf

from src.engine.budget_rules import BUD001_StructureAndAnchors, find_budget_anchors
from src.engine.check_obligations import build_check_receipts, build_obligation_ledger
from src.engine.fact_context import FactContext
from src.engine.pipeline import build_issues_payload
from src.engine.rules_v33 import build_document
from src.schemas.document_profile import DocumentProfile
from src.services.evidence_guard import apply_evidence_completeness, count_formal_findings

OUT = Path(__file__).resolve().parent
SERVICE_ROOT = ROOT / "outputs/remediation_20261006_round13/services_final"
CASE = SERVICE_ROOT / "original_scanned_budget"
SOURCE_FILES = ["api/main.py", "src/engine/budget_rules.py", "src/engine/rules_v33.py",
                "src/engine/check_obligations.py", "src/engine/pipeline.py",
                "src/services/evidence_guard.py", "src/services/ocr_fact_promotion.py",
                "src/engine/rule_policy.py", "tests/remediation/test_round11_contracts.py",
                "tests/remediation/test_round8_percentage_binding.py"]

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def fingerprints():
    return {name: sha(ROOT / name) for name in SOURCE_FILES}

before = fingerprints()
service = json.loads((SERVICE_ROOT / "service_receipt.json").read_text())
scenario = next(row for row in service["scenarios"] if row["scenario"] == "original_scanned_budget")
status = json.loads((CASE / "status.json").read_text())
export = json.loads((CASE / "report.json").read_text())
facts = json.loads((CASE / "fact_context.json").read_text())
source = CASE / "source.pdf"
with pymupdf.open(source) as pdf:
    page_texts = [page.get_text() for page in pdf]
    page_dimensions = [list(page.rect) for page in pdf]

# Preserve the native rule view that production uses when OCR eligibility fails.
# BUD-001 reads text anchors; image-only source has no native table cells/text.
doc = build_document(str(source), page_texts, [[] for _ in page_texts], source.stat().st_size)
doc.fact_context = FactContext.from_dict(facts)
doc.document_profile = DocumentProfile.model_validate(status["document_profile"])
payload = build_issues_payload(doc, False, report_kind="budget", rules=[BUD001_StructureAndAnchors()])
ledger = build_obligation_ledger(doc.document_profile, report_kind="budget",
                               rule_execution_summary=payload["rule_execution_summary"])
receipts = build_check_receipts(payload, report_kind="budget",
                               rule_execution_summary=payload["rule_execution_summary"],
                               obligation_coverage=ledger, fact_receipts=doc.fact_receipts)
actual_issues = [row for row in export["issues"] if row["rule_id"] == "BUD-001"]
copy_for_guard = copy.deepcopy(export)
guard_recomputed = apply_evidence_completeness(copy_for_guard, document_page_count=len(page_texts),
                                              page_texts=page_texts, source_file_hash=sha(source),
                                              source_pdf_path=str(source))
core_ledger = next(row for row in ledger["instances"]
                   if row["obligation_id"] == "OBL-STRUCT-BUDGET-TABLES")
actual_core = next(row for row in export["review_report"]["obligation_coverage"]["instances"]
                   if row["obligation_id"] == "OBL-STRUCT-BUDGET-TABLES")

# These titles were read visually from the fresh Poppler renders of this source.
# They are AI observations, NOT an OCR replacement, frozen labels or human truth.
visual_pages = []
for physical_page, printed_page, title, table_key in [
    (12, 11, "表1：2024年单位收支预算总表", "BUD_T1"),
    (13, 12, "表2：2024年单位收入预算总表", "BUD_T2"),
    (14, 13, "表3：2024年单位支出预算总表", "BUD_T3"),
]:
    image_path = OUT / f"original_budget-{physical_page}.png"
    title_view = build_document("visual_transcription_only", [title], [[]], 1)
    anchors = find_budget_anchors(title_view)
    visual_pages.append({"physical_page": physical_page, "printed_page": printed_page,
                         "title_observed": title, "table_key": table_key,
                         "bbox": None, "observation_method": "AI visual review of fresh Poppler render",
                         "independent_human_truth": False, "native_text_length": len(page_texts[physical_page-1]),
                         "image_file": str(image_path), "image_sha256": sha(image_path),
                         "pdf_page_rect": page_dimensions[physical_page-1],
                         "title_only_helper_control": {"not_production_ocr": True, "anchors": anchors}})

export_pdf_mentions = []
with pymupdf.open(CASE / "report.pdf") as pdf:
    for number, page in enumerate(pdf, 1):
        text = page.get_text()
        if "缺失预算表" in text:
            export_pdf_mentions.append({"export_physical_page": number, "text": text})

# Replay old 9 + 37 directly in memory. Do NOT execute an old authoring wrapper.
old = json.loads((ROOT / "outputs/reacceptance_20261005_round10/rules_audit.json").read_text())
scope = {"__file__": str(ROOT / "outputs/reacceptance_20261005_round8/root_business_neighbors.py"),
         "__name__": "__read_only_r14_replay__"}
with contextlib.redirect_stdout(io.StringIO()) as stream:
    exec(compile(old["reproduction"]["original_replay_code"], scope["__file__"], "exec"), scope)
old_nine = json.loads(stream.getvalue())
exec(compile(old["reproduction"]["executable_neighbor_code"], "<r14_old_neighbor_replay>", "exec"), scope)
original_expected = {row["case_id"]: row for row in old["original_9_cases_summary"]}
nine_summary = []
for row in old_nine:
    observed = {item["sub_check_id"]: item["status"] for item in row["final_check_receipts"]
                if "share_of" in item["sub_check_id"]}
    expected = original_expected[row["case_id"]]["percentage_relation_status"]
    nine_summary.append({"case_id": row["case_id"], "expected": expected, "actual": observed,
                         "matches": observed == expected,
                         "percentage_findings": [i for i in row["payload"]["issues"]["all"] if "占比" in i["message"]]})
neighbor_summary = [{"case_id": row["case_id"], "expected": row["expected_relation_status"],
                     "actual": row["actual_relation_status"], "matches": row["contract_matches"],
                     "percentage_findings": row["percentage_issues"]} for row in scope["cases"]]
amount_summary = [{"case_id": row["case_id"], "expected": row["amount_expected"],
                   "actual": row["amount_actual"], "matches": row["amount_contract_matches"]}
                  for row in scope["amount_cases"]]

line_numbers = {
    "api/main.py": [1288,1289,1291,1324,1327,1329,1331,1332,1520,1522,1523,1528,1529],
    "src/engine/budget_rules.py": [120,123,124,125,126,831,835,836,837,850,2355,2361,2373,2375,2377,2396,2397],
    "src/engine/check_obligations.py": [1238,1239,1242,1243],
    "src/services/evidence_guard.py": [461,470,473,485,492,618,622,629,643,644,647,648],
}
rootline = {}
for name, numbers in line_numbers.items():
    lines = (ROOT / name).read_text().splitlines()
    rootline[name] = [{"line": n, "text": lines[n-1]} for n in numbers]
manifest = json.loads((ROOT / "outputs/remediation_20261006_round13/original_material_review_manifest.json").read_text())
original_manifest = next(row for row in manifest["documents"] if row["name"] == "zhejiang_budget")
root_snapshot = json.loads((ROOT / "outputs/reacceptance_20261006_round14/source_snapshot_before.json").read_text())
after = fingerprints()
audit = {
    "scope": {"round": 14, "read_only": True, "real_saved_original_replay": True,
              "no_network_ai_db_model_or_docker": True, "no_private_blind_labels": True,
              "no_new_human_or_frozen_truth": True},
    "source_sha_before": before, "source_sha_after": after, "source_unchanged": before == after,
    "matches_root_before_snapshot": {name: root_snapshot["files"].get(name) == digest for name,digest in before.items()},
    "matches_final_service_source_fingerprint": {name: service["code_fingerprints"].get(name) == digest
              for name,digest in before.items() if name in service["code_fingerprints"]},
    "input_identity": {"source_file": str(source), "source_sha256": sha(source),
                       "service_source_sha256": scenario["source_sha256"],
                       "manifest_source_sha256": original_manifest["sha256"],
                       "original_artifact_sha256": sha(ROOT / original_manifest["file"]),
                       "page_count": len(page_texts), "native_text_lengths": [len(t) for t in page_texts],
                       "official_provenance_record": original_manifest,
                       "provenance_limit": "Recorded download identity matched locally; no fresh web request or independent human authentication performed."},
    "observed_original_pages": visual_pages,
    "final_service_observation": {"service_run_id": service["run_id"], "job_id": scenario["job_id"],
                       "analysis_revision": scenario["analysis_revision"], "terminal_status": scenario["terminal_status"],
                       "service_validation_passed": scenario["service_validation_passed"],
                       "business_accuracy": scenario["business_accuracy"], "ocr": scenario["ocr"],
                       "page_extraction": status["meta"]["page_extraction"],
                       "fact_summary": status["meta"]["fact_context"],
                       "rule_execution_summary": status["meta"]["rule_execution_summary"],
                       "actual_BUD001_issues": actual_issues,
                       "actual_BUD001_check_receipt": [r for r in export["review_report"]["check_receipts"] if r["rule_id"]=="BUD-001"],
                       "actual_core_obligation": actual_core},
    "actual_production_function_replay": {"rule_set": ["BUD-001"], "fact_context_source": str(CASE / "fact_context.json"),
                       "view_basis": "21 actual image-only source pages have no native text; production excludes ineligible OCR from rule view",
                       "anchors": doc.anchors, "payload": payload, "ledger_core": core_ledger,
                       "receipts": [r for r in receipts if r["rule_id"]=="BUD-001"],
                       "exact_final_export_BUD001_message_match": [r["message"] for r in payload["issues"]["all"]] == [r["message"] for r in actual_issues]},
    "evidence_and_count_distinction": {"count_formal_findings_export_call": count_formal_findings(export),
                       "count_formal_findings_review_report_call": count_formal_findings(export["review_report"]),
                       "stored_evidence_guard_summary": status["meta"]["evidence_completeness"],
                       "recomputed_evidence_guard_summary": guard_recomputed,
                       "review_report_issue_count": len(export["review_report"]["issues"]),
                       "review_report_conclusion": {k:export["review_report"]["conclusion"][k] for k in ["status","quality_status","analysis_conclusion","issue_total"]},
                       "classification_note": "formal_issue_total=11 is the current system's counting label: incomplete_rule_warning is included because only degraded_missing_evidence is excluded. It does not mean authenticated findings: verified_count=0, unverified_count=11, rule_warning_count=11. Overall review_required/incomplete remains honest."},
    "final_user_facing_pdf": {"file": str(CASE/"report.pdf"), "sha256": sha(CASE/"report.pdf"),
                       "missing_table_mentions": export_pdf_mentions},
    "confirmed_finding": {"finding_id":"R14-01", "priority":1,
                       "title":"扫描识别未完成被输出为缺表错误，结构义务仍记完成",
                       "document_type":"budget", "year":2024,
                       "organization_visually_observed":"金华市婺城区新狮街道社区卫生服务中心",
                       "root_file":"src/engine/budget_rules.py", "root_lines":[2361,2373,2375,2377],
                       "expected":"未过识别/资格且核心页面未被规则覆盖时，表存在性不能确定，应回执insufficient_data/manual_review并保留风险；不得断言原件缺表。真实缺表需有完整可用结构视图的前提。",
                       "actual":"原件P12/P13/P14有收支/收入/支出表，规则视图全空时产生BUD-001缺失error；最终PDF P22仍呈现缺表问题。BUD-001=fail使CORE_TABLES义务completed。",
                       "cause_chain":["api/main.py:1288 keeps native text for rule view", "api/main.py:1324-1332 gates OCR view inclusion on monetary operand eligibility", "budget_rules.py:835-837 skips blank text pages", "budget_rules.py:2361-2377 treats absence of anchor as absence of table without extraction prerequisite", "check_obligations.py:1238-1243 maps fail to completed; this is execution completion, not material validity"],
                       "neighbor_title_control":"P12 actual visual title 单位收支预算总表 also lacks a BUD_T1 anchor in a title-only control, while P13/P14 match BUD_T2/T3. Treat this as necessary alias neighborhood within the same structural availability fix, not a separate frozen defect.",
                       "evidence_risk":"AI has seen the actual source page images; no certified bbox, independent human scan attestation, or frozen business truth was created.",
                       "suggestion":"Separate trustworthy table/section presence recognition from numerical-operand eligibility, pass recognition coverage/unknown states into structural rules, and defer missing claims where availability is unknown. Preserve high-confidence real absence on complete native views and common unit-title aliases.",
                       "confidence":1.0},
    "old_rule_regressions": {"original9": nine_summary, "neighbors37": neighbor_summary,
                       "amount_boundaries": amount_summary, "old_scripts_written":False,
                       "original9_mismatches":sum(not r["matches"] for r in nine_summary),
                       "neighbors37_mismatches":sum(not r["matches"] for r in neighbor_summary),
                       "amount_mismatches":sum(not r["matches"] for r in amount_summary),
                       "synthetic_scope":"Existing explicitly synthetic production-rule regressions; not independent business truth or accuracy."},
    "rootline": rootline,
    "limitations":["No full pytest or fresh API/Worker/OCR service run; saved service artifacts and actual production rule functions were independently replayed.",
                   "Only P12-P14 existence is independently visually confirmed here; no assertion that all seven missing-table or four missing-section messages are false.",
                   "Title-only control is an AI transcription probe, never supplied to production or used as monetary operands.",
                   "ReviewRequired is preserved; issue label/error and structural completion are the concrete problematic outputs, not a claim that the whole document was marked passed.",
                   "No formal precision, recall, scan accuracy or frozen defect quota was calculated."],
    "reproduction": {"command":"PYTHONDONTWRITEBYTECODE=1 .venv/bin/python outputs/reacceptance_20261006_round14/rules_audit/reproduce.py",
                       "render_command":"pdftoppm -f 12 -l 14 -r 110 -png outputs/remediation_20261006_round13/services_final/original_scanned_budget/source.pdf outputs/reacceptance_20261006_round14/rules_audit/original_budget",
                       "executable_code":Path(__file__).read_text()},
}
(OUT/"rules_audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str)+"\n")
print(json.dumps({"output":str(OUT/"rules_audit.json"), "source_unchanged":before==after,
                  "original_source_sha256":sha(source), "real_replay_exact_message_match":audit["actual_production_function_replay"]["exact_final_export_BUD001_message_match"],
                  "formal_count":count_formal_findings(export), "verified":guard_recomputed["verified_count"],
                  "original9_mismatches":audit["old_rule_regressions"]["original9_mismatches"],
                  "neighbors37_mismatches":audit["old_rule_regressions"]["neighbors37_mismatches"],
                  "amount_mismatches":audit["old_rule_regressions"]["amount_mismatches"]},ensure_ascii=False))
