"""R1–R4 回归：差额报告可序列化、显式单位不覆盖、非法数字不取数。

这些用例只覆盖规则层；真实上传与发布在隔离环境验收（见本轮输出目录）。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine import checklist_final_rules
from src.engine.checklist_final_rules import (
    R33251_FunctionalDetailCompleteness,
    _dec,
)
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import build_document
from src.schemas.parse_artifact import FiscalFact
from src.services.json_canonical import dumps_canonical

HEAD = ("（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为{total_budget}万元，支出决算为{total_final}万元。其中：\n")
TAIL = "六、一般公共预算财政拨款基本支出决算情况说明"


def _run(page: str):
    doc = build_document("/tmp/rework-r1-r4.pdf", [page], [[]], 1000)
    doc.report_kind = "final"
    doc.fact_context = SimpleNamespace(
        version_token="test-version",
        file_hash="a" * 64,
        parse_run_uid="test-run",
        org_name="甲单位",
        fiscal_year=2023,
    )
    rule = R33251_FunctionalDetailCompleteness()
    try:
        return rule.apply(doc), doc, None
    except RuleOutcomeSignal as signal:
        return list(getattr(signal, "partial_issues", []) or []), doc, signal


def _basis_fact(unit: str = "万元") -> FiscalFact:
    return FiscalFact(
        fact_uid="fact:2160250:total_actual",
        indicator="total_actual",
        raw_value="5,465.41",
        unit=unit,
        normalized_value=Decimal("5465.41"),
        org_name="甲单位",
        fiscal_year=2023,
        period="本年",
        fund_scope="general_public_budget",
        budget_stage="决算",
        subject_code="2160250",
        subject_name="事业运行",
        table_code="FIN_05_general_public_expenditure",
        extra_dims={"evidence_refs": [{
            "physical_page": 16,
            "source_cell_id": "p16:r32c4",
            "quote": "5,465.41",
            "bbox": [10, 20, 30, 40],
            "file_hash": "a" * 64,
        }]},
    )


@pytest.fixture
def table_unit(monkeypatch):
    """让规则看到“表头单位为万元”，同时让单位依据回退到可认证的同科目行。"""
    facts = [SimpleNamespace(
        indicator="total_actual", unit="万元", subject_code="total",
        subject_name="合计", budget_stage="决算", normalized_value=Decimal("90.00"),
    )]
    monkeypatch.setattr(checklist_final_rules, "table_facts", lambda doc, code: (facts, None))
    monkeypatch.setattr(
        "src.engine.native_final_disclosure.detail_decision_unit_from_table",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.engine.native_final_disclosure.detail_unit_basis_from_table",
        lambda *args, **kwargs: _basis_fact(),
    )


def _final_issue(issues):
    return next(issue for issue in issues if "支出决算合计" in issue.message)


def test_fail_branch_operand_evidence_is_serializable(table_unit):
    """R1：完整合计不等时，included_items 里的单位依据必须是可入库摘要。"""
    page = (
        HEAD.format(total_budget="90.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算40.00万元，支出决算为40。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算30.00万元，支出决算为30.00万元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为10.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run(page)
    assert signal is None
    issue = _final_issue(issues)
    included = issue.location["included_items"]
    bases = [item.get("table_unit_basis") for item in included if item.get("table_unit_basis")]
    assert bases and all(isinstance(item, dict) for item in bases)
    assert bases[0]["fact_uid"] == "fact:2160250:total_actual"
    assert bases[0]["page"] == 16
    assert bases[0]["quote"] == "5,465.41"
    # 旧代码在这里抛 UnsupportedJsonValueError（FiscalFact 对象）。
    import dataclasses

    dumps_canonical([dataclasses.asdict(issue) for issue in issues])


def test_explicit_yuan_is_not_overwritten_by_table_unit(table_unit):
    """R2：正文写明“元”时按元换算，表头“万元”不能覆盖。"""
    page = (
        HEAD.format(total_budget="5,465.41", total_final="5,465.41")
        + "1、社会保障和就业支出（类）行政事业单位养老支出（款）事业运行（项）。"
          "年初预算5,465.41万元，支出决算为54,654,100元。\n"
        + TAIL
    )
    issues, doc, signal = _run(page)
    assert signal is None
    assert not any("不一致" in issue.message and "支出决算合计" in issue.message for issue in issues)
    receipt = next(item for item in doc.fact_receipts
                   if item.get("sub_check_id") == "V33-251:final:item_sum")
    assert receipt["status"] == "pass"
    item = receipt["calculation_evidence"]["included"][0]
    assert item["raw"] == "54,654,100"
    assert item["unit_original"] == "元"
    assert item["unit_accounting"] == "元"
    assert Decimal(item["value_wan"]) == Decimal("5465.41")


def test_explicit_yuan_with_real_difference_is_kept(table_unit):
    page = (
        HEAD.format(total_budget="5,465.41", total_final="5,465.41")
        + "1、社会保障和就业支出（类）行政事业单位养老支出（款）事业运行（项）。"
          "年初预算5,465.41万元，支出决算为5,465.41元。\n"
        + TAIL
    )
    issues, doc, signal = _run(page)
    assert signal is None
    # 差额被同源单位错精确解释时不重复出正式问题（2A），但失败回执与
    # 逐项有效读数必须保留，不能被表头单位覆盖。
    receipt = next(item for item in doc.fact_receipts
                   if item.get("sub_check_id") == "V33-251:final:item_sum")
    assert receipt["status"] == "fail"
    evidence = receipt["calculation_evidence"]
    assert evidence["difference_wan"] == "-5464.863459"
    assert Decimal(evidence["included"][0]["value_wan"]) == Decimal("0.546541")
    assert receipt.get("explained_by") == "unit_error"


@pytest.mark.parametrize("raw", ["54,65.41", "5,465.41.00"])
def test_malformed_amount_keeps_literal_and_cannot_pass(raw):
    """R3：非法数字不取数、不修补、不按总额凑平；其他项照常加总。"""
    assert _dec(raw) is None
    page = (
        HEAD.format(total_budget="5,465.41", total_final="5,465.41")
        + f"1、社会保障和就业支出（类）行政事业单位养老支出（款）事业运行（项）。"
          f"年初预算5,465.41万元，支出决算为{raw}。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。"
          "年初预算5,465.41万元，支出决算为5,465.41万元。\n"
        + TAIL
    )
    issues, doc, signal = _run(page)
    receipt = next(item for item in doc.fact_receipts
                   if item.get("sub_check_id") == "V33-251:final:item_sum")
    assert receipt["status"] != "pass"
    assert receipt["calculation_evidence"]["subtotal_wan"] == "5465.41"
    assert receipt["calculation_evidence"]["included_count"] == 1
    malformed = receipt["calculation_evidence"]["not_included"][0]
    assert malformed["raw"] == raw
    assert malformed["malformed_amount"] is True
    assert malformed.get("value_wan") is None
    assert any("金额格式异常" in issue.message for issue in issues)
