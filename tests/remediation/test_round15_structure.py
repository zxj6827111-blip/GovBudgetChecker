from types import SimpleNamespace
import asyncio

from src.engine.budget_rules import BUD001_StructureAndAnchors, find_budget_anchors
from src.engine.pipeline import build_issues_payload, run_rules_with_outcomes
from src.engine.rules_v33 import build_document
from src.services.evidence_guard import apply_evidence_completeness, count_formal_findings
from src.services.structure_view import build_structure_view
from src.engine.check_obligations import build_obligation_ledger
from src.schemas.issues import JobContext
from src.services.engine_rule_runner import EngineRuleRunner


def _scan_doc():
    doc = build_document("scan_budget.pdf", [""] * 21, [[] for _ in range(21)], 1000)
    artifact = SimpleNamespace(text_blocks=[
        SimpleNamespace(page_number=page, extraction_method="ocr", confidence=.95, text=text)
        for page, text in ((12, "单位收支预算总表"), (13, "单位收入预算总表"), (14, "单位支出预算总表"))
    ])
    doc.structure_view = build_structure_view(doc.page_texts, artifact)
    return doc


def test_scan_titles_exist_without_promoting_unqualified_amounts():
    doc = _scan_doc()
    findings, outcomes = run_rules_with_outcomes(doc, report_kind="budget", rules=[BUD001_StructureAndAnchors])
    assert doc.anchors["BUD_T1"] == [12]
    assert doc.anchors["BUD_T2"] == [13]
    assert doc.anchors["BUD_T3"] == [14]
    assert not any("缺失预算表:" in issue.message for issue in findings)
    assert outcomes[0].status == "insufficient_data"
    assert all(issue.severity == "manual_review" for issue in findings)
    assert all(text == "" for text in doc.page_texts)


def test_scan_structure_receipts_and_formal_count_stay_uncompleted():
    doc = _scan_doc()
    payload = build_issues_payload(doc, use_ai_assist=False, report_kind="budget")
    result = {"issues": payload["issues"]}
    stats = apply_evidence_completeness(result, document_page_count=21, page_texts=doc.page_texts)
    assert count_formal_findings(result) == 0
    assert stats["formal_issue_total"] == 0
    assert payload["rule_execution_summary"]["rule_statuses"]["BUD-001"] == "insufficient_data"
    ledger = build_obligation_ledger(None, report_kind="budget", rule_execution_summary=payload["rule_execution_summary"])
    structures = [item for item in ledger["instances"] if "BUD-001" in item["checkers"]]
    assert structures and all(item["status"] != "completed" for item in structures)


def test_empty_structure_does_not_claim_missing_tables():
    doc = build_document("empty_budget.pdf", [""], [[]], 100)
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="budget", rules=[BUD001_StructureAndAnchors])
    assert outcomes[0].status == "insufficient_data"
    assert not any("缺失预算表" in issue.message for issue in issues)


def test_complete_native_structure_still_detects_real_missing_table():
    doc = build_document("native_budget.pdf", ["2024年单位预算编制说明，无政府性基金及国有资本预算"], [[]], 100)
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="budget", rules=[BUD001_StructureAndAnchors])
    assert outcomes[0].status == "fail"
    assert any("缺失预算表" in issue.message and issue.location["table"] == "BUD_T1" for issue in issues)


def test_unit_title_aliases_empty_and_landscape_pages_are_structural():
    doc = build_document("budget.pdf", ["单位收支预算总表\n本表为空表", "部门收入预算总表", "单位支出预算总表"], [[], [], []], 100)
    anchors = find_budget_anchors(doc)
    assert anchors["BUD_T1"] == [1]
    assert anchors["BUD_T2"] == [2]
    assert anchors["BUD_T3"] == [3]


def test_unread_scan_has_no_conditional_missing_claim():
    doc = _scan_doc()
    doc.structure_view["page_texts"][0] = "政府性基金预算100万元"
    issues, _ = run_rules_with_outcomes(doc, report_kind="budget", rules=[BUD001_StructureAndAnchors])
    assert not any("缺失预算表" in issue.message for issue in issues)


def test_dual_runner_keeps_same_structure_view_and_unknown_state():
    source = _scan_doc()
    context = JobContext(job_id="round15-scan", pdf_path=source.path,
        page_texts=source.page_texts, page_tables=source.page_tables,
        meta={"structure_view": source.structure_view})
    doc = asyncio.run(EngineRuleRunner()._prepare_document(context))
    assert doc.structure_view == source.structure_view
    _, outcomes = run_rules_with_outcomes(doc, report_kind="budget", rules=[BUD001_StructureAndAnchors])
    assert outcomes[0].status == "insufficient_data"


def test_invalid_ocr_score_is_not_a_structure_hint():
    artifact = SimpleNamespace(text_blocks=[SimpleNamespace(page_number=1,
        extraction_method="ocr", confidence=float("nan"), text="单位收支预算总表")])
    view = build_structure_view([""], artifact)
    assert view["ocr_hint_pages"] == []
    assert view["absence_check_ready"] is False
