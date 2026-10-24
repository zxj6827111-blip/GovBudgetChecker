"""Real-intake semantic gaps with independent scope/negation counterexamples."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.native_disclosure_semantics import check_disclosure_semantics
from src.engine.native_narrative_checks import check_narrative_relations
from src.engine.native_narrative_quality import check_quality_relations
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33234_NarrativePercentConsistency


RULE = R33234_NarrativePercentConsistency


def document(body, heading="五、一般公共预算财政拨款支出决算情况说明", *, year=2027):
    return SimpleNamespace(page_texts=[f"甲局\n{year}年度部门决算", heading + "\n" + body],
                           report_kind="final", fiscal_year=year, organization_name="甲局")


def assert_original_refs(issues, doc):
    for issue in issues:
        refs = issue.location["table_refs"]
        assert refs and all(ref["span"] in doc.page_texts[ref["page"] - 1] for ref in refs)


def test_budget_restated_cause_requires_same_item_nonzero_difference_and_keeps_original():
    doc = document("10、甲项。年初预算为28万元，支出决算为14万元。"
                   "决算数小于预算数的主要原因：该项实际支出小于预算数。\n"
                   "11、乙项。年初预算为33万元，支出决算为17万元。主要原因：该项实际支出小于预算数。")
    issues, unresolved = check_quality_relations(RULE(), doc)
    assert not unresolved and len(issues) == 2
    assert all(issue.severity == "medium" for issue in issues)
    assert [issue.location["budget_amount_wan"] for issue in issues] == ["28", "33"]
    assert_original_refs(issues, doc)


@pytest.mark.parametrize("cause", [
    "据实结算", "发生死亡抚恤金据实支出", "人员经费清算", "人员减少",
    "实际支出小于预算数，因人员退休减少支出", "厉行节约减少支出",
])
def test_short_but_specific_causes_and_result_followed_by_business_reason_are_allowed(cause):
    issues, unresolved = check_quality_relations(RULE(), document(
        f"年初预算为90万元，支出决算为60万元。主要原因是{cause}。"))
    assert not issues and not unresolved


@pytest.mark.parametrize("body", [
    # 2026-10-22 口径决议①：差额在 0.01 万元容差内才算“无实质差异”。
    "年初预算为90万元，支出决算为89.99万元。主要原因是决算数与预算数基本持平。",
    "年初预算为900000元，支出决算为900001元。主要原因是决算数与预算数基本持平。",
    "机关运行经费支出90万元，比2026年度减少0万元，下降0%。主要原因是机关运行支出同比略有增多。",
])
def test_equal_rounding_and_zero_yoy_changes_do_not_supply_a_material_difference(body):
    heading = "十一、其他重要事项的情况说明" if "机关运行" in body else "五、一般公共预算财政拨款支出决算情况说明"
    issues, unresolved = check_quality_relations(RULE(), document(body, heading))
    assert not issues and not unresolved


@pytest.mark.parametrize("body", [
    "1、甲项。年初预算为90万元，支出决算为60万元。\n2、乙项。主要原因是该项实际支出小于预算数。",
    "年初预算为90万元，支出决算为60万元，其中项目支出30万元。主要原因是该项实际支出小于预算数。",
    "按可比口径统计，年初预算为90万元，支出决算为60万元。主要原因是该项实际支出小于预算数。",
    "机关运行经费支出90万元，比2025年度减少10万元，下降10%。主要原因是机关运行支出同比略有增多。",
])
def test_unbound_or_adjusted_cause_is_unfinished_not_a_confirmed_finding(body):
    heading = "十一、其他重要事项的情况说明" if "机关运行" in body else "五、一般公共预算财政拨款支出决算情况说明"
    doc = document(body, heading)
    issues, unresolved = check_quality_relations(RULE(), doc)
    assert not issues and unresolved
    assert doc.fact_receipts[-1]["status"] == "insufficient_data"


def test_operating_cause_is_same_clause_and_cross_page_original_quotes_survive():
    doc = document("（一）机关运行经费支出情况\n机关运行经费支出110万元，比2026年度增加10万元，")
    doc.page_texts[1] = "十一、其他重要事项的情况说明\n" + doc.page_texts[1]
    doc.page_texts.append("增长10%。主要原因是机关运行支出同比略有增多。\n（二）政府采购支出情况\n其他内容。")
    issues, unresolved = check_quality_relations(RULE(), doc)
    assert len(issues) == 1 and not unresolved
    assert issues[0].location["cause_kind"] == "year_on_year"
    assert issues[0].location["pages"] == [2, 3]
    assert_original_refs(issues, doc)


def test_operating_nonzero_change_supplies_cause_evidence_without_a_percentage():
    doc = document("机关运行经费支出110万元，比2026年度增加10万元。"
                   "主要原因是机关运行支出同比略有增多。", "十一、其他重要事项的情况说明")
    issues, unresolved = check_quality_relations(RULE(), doc)
    assert len(issues) == 1 and not unresolved
    assert issues[0].location["change_amount_wan"] == "10"


def test_operating_yoy_uses_previous_base_and_valid_yoy_has_no_warning():
    doc = document("机关运行经费支出90万元，比2026年度减少10万元，下降11.11%。",
                   "（一）机关运行经费支出情况")
    issues, unresolved = check_narrative_relations(RULE(), doc)
    assert len(issues) == 1 and not unresolved
    assert issues[0].location["previous_amount_wan"] == "100"
    assert Decimal(issues[0].location["computed_percent"]) == Decimal("10")
    correct = document("机关运行经费支出90万元，比2026年度减少10万元，下降10%。",
                       "（一）机关运行经费支出情况")
    issues, unresolved = check_narrative_relations(RULE(), correct)
    assert not issues and not unresolved


def vehicle_document(body):
    return document(body, "七、财政拨款“三公”经费支出决算情况说明")


def test_zero_vehicle_spend_and_positive_purchase_use_is_wording_conflict():
    doc = vehicle_document("2027年无公务车辆更新。\n公务用车购置支出为0万元。主要用于一辆公务用车购置。"
                           "公务用车运行维护支出19万元。")
    issues, unresolved = check_disclosure_semantics(RULE(), doc)
    assert len(issues) == 1 and not unresolved
    assert issues[0].severity == "medium"
    assert issues[0].location["purchase_spend_wan"] == "0"
    assert len(issues[0].location["table_refs"]) == 2
    assert_original_refs(issues, doc)


@pytest.mark.parametrize("body", [
    "公务用车购置支出为0万元。未购置公务用车。",
    "公务用车购置支出为0万元。未购置公务用车1辆。",
    "公务用车购置支出为0万元。拟购置公务用车1辆。",
    "公务用车购置支出为0万元。主要用于计划购买公务用车1辆。",
    "公务用车购置支出为0万元。公务用车运行维护支出19万元。另一个部门购置公务用车1辆。",
    "公务用车购置支出为14万元。主要用于一辆公务用车购置。",
    "公务用车购置支出年初预算为0万元。年中调整预算，购置公务用车1辆。",
])
def test_vehicle_negative_planned_budget_and_other_item_purchases_are_not_confirmed(body):
    issues, unresolved = check_disclosure_semantics(RULE(), vehicle_document(body))
    assert not issues and not unresolved


@pytest.mark.parametrize("qualification", [
    "该车辆尚未付款", "该车辆未支付购置款", "该车辆由其他部门支付购置款",
    "车辆通过无偿调拨取得", "车辆购置款跨年度支付",
])
def test_vehicle_payment_or_transfer_scope_is_manual_and_never_pass(qualification):
    doc = vehicle_document(f"公务用车购置支出为0万元。主要用于一辆公务用车购置，{qualification}。")
    issues, unresolved = check_disclosure_semantics(RULE(), doc)
    assert len(issues) == 1 and issues[0].severity == "manual_review" and unresolved
    assert doc.fact_receipts[-1]["status"] == "insufficient_data"
    assert qualification in issues[0].evidence_text
    assert_original_refs(issues, doc)


def test_condition_table_instruction_is_one_group_and_does_not_reject_valid_empty_note():
    doc = document("无政府性基金预算财政拨款收入支出的，仍保留该表格式，并在本表下方作下述说明：\n"
                   "说明：甲局本年度没有政府性基金预算财政拨款收入和支出，故本表无数据。", "政府性基金预算财政拨款收入支出决算表")
    doc.page_texts.append("国有资本经营预算财政拨款收入支出决算表\n"
                          "无国有资本经营预算财政拨款收入支出的，仍保留该表格式，并在本表下方作下述说明：\n"
                          "说明：甲局本年度没有国有资本经营预算财政拨款收入和支出，故本表无数据。")
    issues, unresolved = check_disclosure_semantics(RULE(), doc)
    assert len(issues) == 1 and not unresolved
    assert issues[0].severity == "low"
    assert issues[0].location["pages"] == [2, 3]
    assert "不表示缺表或缺少空表说明" in issues[0].message
    assert_original_refs(issues, doc)
    doc.page_texts = [part.replace("仍保留该表格式，并在本表下方作下述说明：", "") for part in doc.page_texts]
    assert check_disclosure_semantics(RULE(), doc) == ([], [])


def test_rule_entry_preserves_manual_vehicle_finding_and_incomplete_scope():
    doc = vehicle_document("1、公务用车购置决算情况。\n公务用车购置支出为0万元。"
                           "主要用于一辆公务用车购置，车辆尚未付款。")
    with pytest.raises(RuleDeferred) as caught:
        RULE().apply(doc)
    assert any("付款" in reason for reason in caught.value.unresolved_reasons)
    issues = caught.value.partial_issues
    assert len(issues) == 1 and issues[0].severity == "manual_review"
    assert_original_refs(issues, doc)


def test_rule_entry_preserves_unbound_quality_scope_without_claiming_pass():
    doc = document("1、甲项预算情况。主要原因是该项实际支出小于预算数。")
    with pytest.raises(RuleDeferred) as caught:
        RULE().apply(doc)
    assert not caught.value.partial_issues
    assert any("原因质量关系未完成" in reason for reason in caught.value.unresolved_reasons)
    assert doc.fact_receipts[-1]["status"] == "insufficient_data"


@pytest.mark.parametrize(("filename", "kind", "expected_count", "expected_pages"), [
    ("上海市商务委员会.pdf", "reason_restates_result", 3, [26, 26, 32]),
    ("上海市商务委员会.pdf", "zero_vehicle_purchase_semantics", 1, [30]),
    ("上海市监狱管理局.pdf", "conditional_template_instruction_residue", 1, [18]),
    ("上海市交通委员会.pdf", "narrative_yoy_delta", 1, [43]),
])
def test_real_originals_replay_semantic_and_operating_gaps(filename, kind, expected_count, expected_pages):
    path = Path("/Users/zhangxingjin/Desktop/决算测试上海市") / filename
    if not path.exists():
        pytest.skip("真实原件未在该电脑提供；合成边界测试仍执行")
    pdfplumber = pytest.importorskip("pdfplumber")
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    doc = SimpleNamespace(page_texts=pages, report_kind="final")
    if kind == "reason_restates_result":
        issues, _ = check_quality_relations(RULE(), doc)
    elif kind == "narrative_yoy_delta":
        issues, _ = check_narrative_relations(RULE(), doc)
        issues = [issue for issue in issues if "机关运行" in issue.location["section"]]
    else:
        issues, _ = check_disclosure_semantics(RULE(), doc)
    found = [issue for issue in issues if issue.location["relation_type"] == kind]
    assert len(found) == expected_count
    assert [issue.location["page"] for issue in found] == expected_pages
    assert_original_refs(found, doc)
    if kind == "conditional_template_instruction_residue":
        assert found[0].location["pages"] == [18, 19]
    if kind == "narrative_yoy_delta":
        assert found[0].location["previous_amount_wan"] == "7015.13"
        assert Decimal(found[0].location["computed_percent"]).quantize(Decimal(".01")) == Decimal("4.78")
