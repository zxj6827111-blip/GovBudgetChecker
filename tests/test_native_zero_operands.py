"""零额项/空白残差两类“0 已由表格证明”的通用规则测试。"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import _zero_item_parent_reconciles
from src.engine.native_summary_checks import check_total_expenditure_balance
from src.engine.rules_v33 import R33101_TotalSheet_Identity
from src.engine.rule_outcome import RuleDeferred
from src.schemas.parse_artifact import FiscalFact


def _fact(code: str, name: str, value: str, table_code: str, *, indicator: str = "total_actual") -> FiscalFact:
    return FiscalFact(
        fact_uid=f"f-{table_code}-{code}",
        indicator=indicator,
        raw_value=value,
        unit="万元",
        normalized_value=Decimal(value),
        org_name="甲中心",
        org_scope="unit",
        fiscal_year=2025,
        period="本年",
        fund_scope="general_public_budget",
        budget_stage="决算",
        subject_code=code,
        subject_name=name,
        table_code=table_code,
        instance_uid="i",
        row_order=1,
        recognition_status="unverified",
        extra_dims={"evidence_refs": [{"physical_page": 1, "quote": value}]},
    )


def _doc(facts: list[FiscalFact]) -> SimpleNamespace:
    return SimpleNamespace(
        fact_context=FactContext(
            facts=facts,
            org_name="甲中心",
            fiscal_year=2025,
            report_kind="final",
            file_hash="a" * 64,
            parse_run_uid="run",
        ),
        parse_artifact=SimpleNamespace(
            source_file_hash="a" * 64,
            parse_run_uid="run",
            logical_tables=[],
        ),
    )


def _t5_facts() -> list[FiscalFact]:
    table = "FIN_05_general_public_expenditure"
    return [
        _fact("208", "社会保障和就业支出", "272.37", table),
        _fact("20805", "行政事业单位养老支出", "272.37", table),
        _fact("2080501", "行政单位离退休", "16.58", table),
        _fact("2080502", "事业单位离退休", "103.36", table),
        _fact("2080505", "机关事业单位基本养老保险缴费支出", "101.62", table),
        _fact("2080506", "机关事业单位职业年金缴费支出", "50.81", table),
    ]


def test_zero_item_is_allowed_when_parent_total_reconciles():
    item = {
        "klass": "社会保障和就业支出",
        "kuan": "行政事业单位养老支出",
        "xiang": "其他行政事业单位养老支出",
    }
    assert _zero_item_parent_reconciles(_doc(_t5_facts()), _t5_facts(), item) is True


def test_zero_item_is_rejected_when_parent_total_does_not_reconcile():
    item = {
        "klass": "社会保障和就业支出",
        "kuan": "行政事业单位养老支出",
        "xiang": "其他行政事业单位养老支出",
    }
    facts = _t5_facts()
    facts[2] = _fact("2080501", "行政单位离退休", "10.00", "FIN_05_general_public_expenditure")
    assert _zero_item_parent_reconciles(_doc(facts), facts, item) is False


def _fin01_facts(total: str, expense: str, closing: str) -> list[FiscalFact]:
    table = "FIN_01_income_expenditure_total"
    return [
        _fact("total", "总计", total, table, indicator="expenditure_actual"),
        _fact("expense", "本年支出合计", expense, table, indicator="expenditure_actual"),
        _fact("closing", "年末结转和结余", closing, table, indicator="expenditure_actual"),
    ]


def test_missing_child_zero_is_inferred_from_total_equation():
    facts = _fin01_facts("795104.99", "542651.78", "252453.21")
    assert check_total_expenditure_balance(R33101_TotalSheet_Identity(), _doc(facts)) == []


def test_nonzero_residual_does_not_fill_the_missing_child():
    facts = _fin01_facts("795104.99", "542651.78", "252000.00")
    with pytest.raises(RuleDeferred):
        check_total_expenditure_balance(R33101_TotalSheet_Identity(), _doc(facts))
