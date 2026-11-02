"""政府性基金决算表合计行被支出子表头占位时的总额取数（V33-248）。

真值材料：上海市建设用地和土地整理事务中心 2025 年度决算第 16 页——合计行的
「本年支出/基本支出/项目支出」三格印的是子表头文字（合计/基本支出/项目支出），
PDF 里没有数；修复前 V33-248 报「total 的 total_actual 操作数缺失」，把整条
补核义务（表文逐项一致）一起阻塞。修复口径：类级行齐全可读时按「合计 = 各类
之和」核算，并在回执里写明推导依据；类级行读不出数时仍然取数不足。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.engine.native_final_disclosure import check_fund_totals
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33248_FundTotals
from src.schemas.parse_artifact import FiscalFact

ORG = "上海市某某事务中心"
YEAR = 2025


def _fact(code, name, indicator, raw, *, unit="万元", stage="决算", period="本年"):
    normalized = Decimal(raw.replace(",", "")) if raw else None
    return FiscalFact(
        fact_uid=f"fact-{code}-{indicator}",
        indicator=indicator,
        raw_value=raw,
        unit=unit,
        normalized_value=normalized,
        org_name=ORG,
        org_scope="单位",
        fiscal_year=YEAR,
        period=period,
        fund_scope="government_fund",
        budget_stage=stage,
        subject_code=code,
        subject_name=name,
        table_code="FIN_08_gov_fund",
        instance_uid="instance-1",
    )


def _doc(facts, narrative_total="7,402.47", *, income_total="7,402.47"):
    texts = [
        "上海市某某事务中心\n2025 年度单位决算\n",
        "政府性基金预算财政拨款收入支出决算表\n单位：万元\n",
        "八、政府性基金预算财政拨款收入支出决算情况说明\n"
        f"政府性基金预算财政拨款收入 {income_total} 万元，支出 {narrative_total} 万元，"
        "支出具体情况如下：\n"
        "1、城乡社区支出-国有土地使用出让收入安排的支出-土地出让业务支出。"
        f"支出决算为 {narrative_total} 万元。\n",
    ]
    doc = build_document("fund.pdf", texts, [[], [], []], 0)

    class _Artifact:
        source_file_hash = "a" * 64
        parse_run_uid = "run"

    class _Context:
        file_hash = "a" * 64
        parse_run_uid = "run"
        org_name = ORG
        fiscal_year = YEAR
        version_token = "v1"

    _Context.facts = facts
    doc.parse_artifact = _Artifact()
    doc.fact_context = _Context()
    doc.fact_receipts = []
    return doc


def _rule():
    return R33248_FundTotals()


def _class_rows(amount="7,402.47", *, total_readable=True):
    """合计行只有收入事实（本年支出格被表头占位），类级行有收入+支出事实。"""
    return [
        _fact("total", "合计", "income_actual", amount),
        _fact("212", "城乡社区支出", "income_actual", amount),
        _fact("212", "城乡社区支出", "total_actual", amount if total_readable else ""),
    ]


def test_total_row_missing_uses_class_row_sum_without_issue():
    """合计行无本年支出数 + 类级行可读 → 按类级行合计核对，通过且留推导依据。"""
    doc = _doc(_class_rows())
    issues = check_fund_totals(_rule(), doc)

    assert issues == []
    receipts = [r for r in doc.fact_receipts if r.get("status") == "pass"]
    assert any(
        r.get("formula") == "explicit narrative amount == sum of class rows (total cell not printed)"
        for r in receipts
    ), receipts


def test_derived_total_still_reports_a_real_difference():
    """推导总额与说明不一致 → 仍然是 error，证据里写明按类级行合计。"""
    doc = _doc(_class_rows(), narrative_total="8,000.00")
    issues = check_fund_totals(_rule(), doc)

    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "类级行合计" in issues[0].message
    assert "7402.47" in issues[0].message and "597.53" in issues[0].message
    assert issues[0].location["derived_total_from"] == "class_rows"


def test_unreadable_class_row_keeps_the_check_unresolved():
    """类级行读不出金额时不得推导 → 取数不足（空白不按 0 处理）。"""
    facts = _class_rows(total_readable=False)
    with pytest.raises(RuleDeferred) as excinfo:
        check_fund_totals(_rule(), _doc(facts))
    assert excinfo.value.status == "insufficient_data"
