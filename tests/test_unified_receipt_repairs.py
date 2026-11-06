"""规则部分完成不得绿化，完成的兄弟规则不得借用其它规则诊断。"""
import pytest

from src.engine.check_obligations import build_check_receipts
from src.services.issue_relation_refs import unresolved_issue_relations


@pytest.mark.parametrize("fallback", ["insufficient_data", "parse_error", "execution_error"])
def test_rule_gap_survives_successful_partial_relations(fallback):
    rows = build_check_receipts({}, report_kind="final",
        rule_execution_summary={"rule_statuses": {"V33-TREND-COMPLETION-RATE": fallback}},
        obligation_coverage={"instances": [{"obligation_id": "OBL-TREND-COMPLETION-RATE",
            "status": "insufficient_data", "checkers": ["V33-TREND-COMPLETION-RATE"]}]},
        fact_receipts=[{"rule_id": "V33-TREND-COMPLETION-RATE", "sub_check_id": "partial", "status": "pass"}])
    parent = next(r for r in rows if r["sub_check_id"].startswith("OBL-"))
    assert parent["status"] == fallback
    assert parent["reason_codes"] and parent["next_action"]
    child = next(r for r in rows if r["sub_check_id"] == "partial")
    assert child["status"] == "pass"


def test_passed_sibling_does_not_inherit_unrelated_gap():
    rows = build_check_receipts({}, report_kind="final",
        rule_execution_summary={"rule_statuses": {"V33-103": "pass", "V33-226": "insufficient_data"}},
        obligation_coverage={"instances": [{"obligation_id": "OBL-TXT-T2", "status": "insufficient_data",
            "checkers": ["V33-103", "V33-226"], "reason_codes": ["operand_missing"],
            "next_action": "system_recheck", "missing_operands": [{"source": "table",
                "table_code": "FIN_02_income", "role": "other_income", "page": 6}]}]})
    parent = next(r for r in rows if r["rule_id"] == "V33-103")
    assert parent["status"] == "pass"
    assert parent["reason_codes"] == [] and parent["next_action"] is None
    assert next(r for r in rows if r["rule_id"] == "V33-226")["status"] == "insufficient_data"


def test_source_diagnostic_group_must_resolve_every_member():
    issue = {"rule_id": "R", "check_relation_id": "R:source_errors", "location": {
        "reference_kind": "source_diagnostic_group", "relation_ids": ["R:a", "R:b"],
        "relations": [{"relation_id": "R:a"}, {"relation_id": "R:b"}]}}
    receipts = [{"rule_id": "R", "sub_check_id": "a"}, {"rule_id": "R", "sub_check_id": "b"}]
    assert unresolved_issue_relations(issue, receipts) == []
    assert unresolved_issue_relations(issue, receipts[:1]) == ["R:b"]
    issue["location"]["relations"].pop()
    assert unresolved_issue_relations(issue, receipts) == ["R:source_errors"]


def test_real_farm_blank_has_known_column_but_unknown_amount():
    from pathlib import Path
    from src.services.pdf_parser import PDFParser
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.native_table_arithmetic import select
    from src.engine.diagnostic_fields import fields_for_text
    from src.engine.rules_v33 import build_document

    path = Path(__file__).parent / "fixtures/native_trusted_v27/farm23.pdf"
    artifact = PDFParser(None).build_parse_artifact(str(path))
    context = build_fact_context_from_artifact(artifact, org_name="上海市农场管理局", fiscal_year=2023,
                                              report_kind="final", doc_type="dept_final")
    doc = build_document(str(path), [], [], path.stat().st_size)
    doc.parse_artifact, doc.fact_context = artifact, context
    facts = [f for f in context.facts if f.table_code == "FIN_03_expenditure"]
    before = context.to_dict()
    value, reason = select(doc, facts, "basic_actual", code="total", allow_native_blank=True)
    assert value is None and "原件单元格为空白" in reason
    diagnostic = fields_for_text(doc, reason, rule_id="V33-241", sub_check_id="expenditure:horizontal:total")
    assert diagnostic["reason_codes"] == ["cell_blank_semantics_unknown"]
    assert diagnostic["next_action"] == "human_confirm"
    assert diagnostic["missing_operands"][0]["page"] == 7
    assert context.to_dict() == before  # 不写入任何零事实。


def test_compound_blanks_keep_the_source_driven_action():
    from types import SimpleNamespace
    from src.engine.diagnostic_fields import record_gap, fields_for_text
    doc = SimpleNamespace()
    for name in ("a", "b"):
        record_gap(doc, f"blank-{name}", reason_codes=["cell_blank_semantics_unknown"],
                   blocked_by=[{"dimension": "cell", "ref": name, "reason_code": "cell_blank_semantics_unknown"}],
                   preferred_action="human_confirm")
    result = fields_for_text(doc, "blank-a；blank-b", rule_id="V33-241", sub_check_id="sum")
    assert result["next_action"] == "human_confirm"


def test_literal_fiscal_column_does_not_stand_for_missing_income_total():
    from tests.remediation.test_native_pdf_core import grid, context_for
    from src.engine.native_hierarchy_checks import _table_view
    from src.engine.diagnostic_fields import fields_for_text
    table = grid([["科目编码", "科目名称", "财政拨款收入"],
                  ["合计", "", "100.00"], ["208", "社会保障", "100.00"]],
                 [0, 40, 100, 160], uid="income", page=1, code="FIN_02_income", confidence=.99, unit="万元")
    doc = context_for([table], [table])
    _, reason = _table_view(doc, "FIN_02_income")
    assert "不能用财政拨款收入替代" in reason
    diagnostic = fields_for_text(doc, reason, rule_id="V33-240", sub_check_id="complete")
    assert diagnostic["reason_codes"] == ["operand_missing"]
    assert diagnostic["next_action"] == "human_confirm"
