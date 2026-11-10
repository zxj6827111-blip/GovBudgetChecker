"""D0 RC-5a 回归：三公数量要素逐项显式记账，消灭"零问题零回执"静默跳过。

控制试验（计划第二节）：金额事实缺失时数量检查直接跳过——零问题、零回执、
零未决。修复后：金额缺失记 insufficient_data 回执、金额为0/未发生记
not_applicable、要素齐备记 pass、缺失要素记 fail——检查诚实性可对账。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.three_public_counts import count_issues  # noqa: E402


class _Rule:
    code, severity = "TEST-QUANTITY", "medium"

    def _issue(self, message, location, **kwargs):  # 最小桩
        return SimpleNamespace(message=message, location=location, **kwargs)


def _doc(facts, body):
    doc = SimpleNamespace(
        fact_context=SimpleNamespace(org_name="甲局", fiscal_year=2025, facts=facts),
        page_texts=["甲局2025年度部门决算\n七、财政拨款“三公”经费支出决算情况说明\n" + body],
    )
    return doc


def _fact(indicator, value):
    from decimal import Decimal

    return SimpleNamespace(
        table_code="FIN_07_three_public", instance_uid="i1", indicator=indicator,
        subject_code="total", normalized_value=Decimal(value), budget_stage="决算",
        org_name="甲局", extra_dims={}, recognition_status="verified",
    )


def _run(doc):
    return count_issues(
        _Rule(), doc, report_kind="final",
        scope=doc.page_texts[0], section_title="三公", locate_page=lambda d, s: 1,
    )


def _receipts(doc):
    return [r for r in getattr(doc, "fact_receipts", [])
            if str(r.get("sub_check_id", "")).startswith("quantity:")]


def test_missing_amount_fact_records_insufficient_receipt():
    doc = _doc([], "公务用车购置及运行维护费支出决算 4.06 万元。现有公务用车5辆。")
    issues = _run(doc)
    assert issues == []  # 金额缺失不报问题（宁漏报不误报），但必须留回执
    receipts = _receipts(doc)
    assert receipts, "取数不足必须留回执"
    assert any(r["status"] == "insufficient_data" for r in receipts)


def test_zero_amount_records_not_applicable_receipt():
    doc = _doc([_fact("vehicle_purchase", "0.00")], "公务用车购置及运行维护费支出决算 0 万元。现有公务用车5辆。")
    issues = _run(doc)
    assert issues == []
    receipts = _receipts(doc)
    assert any(r["sub_check_id"] == "quantity:vehicle_purchase"
               and r["status"] == "not_applicable" for r in receipts)


def test_pass_and_fail_are_both_recorded():
    # 要素齐备 → pass 回执；保有量全文未披露 → fail 回执
    doc = _doc(
        [_fact("vehicle_purchase", "2.00")],
        "公务用车购置费支出决算2万元，购置公务用车1辆。全年公务接待0批次。",
    )
    issues = _run(doc)
    receipts = {r["sub_check_id"]: r["status"] for r in _receipts(doc)}
    assert receipts.get("quantity:vehicle_purchase") == "pass"
    assert receipts.get("quantity:vehicle_stock") == "fail"
    assert any("保有量" in str(i.message) for i in issues)


def test_multi_instance_table_records_gap():
    facts = [_fact("vehicle_purchase", "1.00"), _fact("vehicle_purchase", "2.00")]
    facts[1].instance_uid = "i2"
    doc = _doc(facts, "公务用车购置费支出决算1万元，购置1辆。现有公务用车5辆。")
    issues = _run(doc)
    assert [i for i in issues if "保有量" not in str(i.message)] == []
    receipts = _receipts(doc)
    assert any(r["status"] == "insufficient_data"
               and "实例" in str(r.get("incomplete_reason")) for r in receipts)
