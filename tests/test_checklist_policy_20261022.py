"""2026-10-22 口径决议①~⑤：普陀预决算检查点清单作为全系统检查指标。

决议记录见 docs/PUTUO_CHECKLIST_GAP_ANALYSIS_20261022.md §2。用例金额取自
清单“常见错误表现”原文（预算 C-16、B-03，决算 A-08、A-09、B-10），其余为
合成 CONTRACT FIXTURE。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List

import pytest

from src.engine.amount_math import classify_amount_diff, classify_display_conversion, wan_scale
from src.engine.budget_rules import (
    BUD001_StructureAndAnchors,
    BUD109_FunctionalClassificationNameConsistency,
    BUD114_FunctionalEntryAmountConsistency,
)
from src.engine.common_rules import CMM004_CodeMirrorConsistency, _close
from src.engine.rule_policy import AMOUNT_TOLERANCE_WAN
from src.engine.rules_v33 import R33SGCompletion, build_document
from src.engine.three_public_counts import count_issues
from src.schemas.parse_artifact import FiscalFact


def _fact(table: str, code: str, indicator: str, value: str, *, raw: str = "", unit: str = "万元",
          precision: int = 2, stage: str = "决算", name: str = "", uid: str = "") -> FiscalFact:
    return FiscalFact(
        fact_uid=uid or f"{table}:{code}:{indicator}:{stage}",
        indicator=indicator,
        raw_value=raw or value,
        unit=unit,
        normalized_value=Decimal(value),
        display_precision=precision,
        period="本年",
        budget_stage=stage,
        subject_code=code,
        subject_name=name,
        table_code=table,
        instance_uid=f"{table}:inst",
    )


def _attach_facts(doc: Any, facts: List[FiscalFact]) -> Any:
    doc.fact_context = SimpleNamespace(
        file_hash="h", parse_run_uid="r", facts=facts, org_name=None, fiscal_year=2026,
        version_token="test-facts-v1",
    )
    doc.parse_artifact = SimpleNamespace(source_file_hash="h", parse_run_uid="r")
    return doc


# ---- 决议①：通用金额容差 0.01 万元 ----------------------------------------


def test_policy_amount_tolerance_is_one_hundredth_wan() -> None:
    assert AMOUNT_TOLERANCE_WAN == Decimal("0.01")
    # 决算 A-09 案例：说明 34.85 万元、表内 34.35 万元，差 0.50——旧 0.5 容差下不报
    assert classify_amount_diff("34.85", "34.35", 1)[0] == "mismatch"
    assert classify_amount_diff("10.00", "10.02", 1)[0] == "mismatch"
    # 两个两位小数显示值之间 0.01 仍在显示舍入包络内
    assert classify_amount_diff("10.00", "10.01", 1)[0] == "rounding_hint"


def test_close_uses_policy_tolerance_and_component_envelope() -> None:
    assert _close(0.30, 0.29)  # 浮点尾差不得把恰好 0.01 判成超限
    assert not _close(1.00, 1.02)
    assert _close(10.00, 10.015, terms=2)
    assert not _close(10.00, 10.40)


# ---- 决议①补充：元→万元换算四舍五入精确比对（清单 C-16） -------------------


@pytest.mark.parametrize(
    ("yuan", "text", "expected"),
    [
        (1022257, "102.22", "102.23"),  # 曹杨新村第三幼儿园 住房公积金（截尾）
        (8809252, "880.92", "880.93"),  # 洵阳中学（截尾）
        (71960, "7.19", "7.20"),  # 北海中学 其他行政事业单位养老支出
        (34050, "3.01", "3.41"),  # 区委社会工作部 其他社会保障和就业支出
    ],
)
def test_display_conversion_flags_checklist_c16_cases(yuan: int, text: str, expected: str) -> None:
    state, rounded = classify_display_conversion(
        Decimal(text), 2, Decimal(yuan) / Decimal(10000), wan_scale(0, "元")
    )
    assert state == "mismatch"
    assert rounded == Decimal(expected)


def test_display_conversion_accepts_half_up_and_skips_same_precision() -> None:
    assert classify_display_conversion(Decimal("102.23"), 2, Decimal("102.2257"), 4)[0] == "ok"
    assert classify_display_conversion(Decimal("508"), 0, Decimal("508.000000"), 6)[0] == "ok"
    # 表内与正文同为两位小数万元：不适用精确比对，回到包络 + 政策容差
    assert classify_display_conversion(Decimal("10.00"), 2, Decimal("10.01"), 2)[0] == "not_applicable"


_MAIN_CONTENT_PAGE = "\n".join(
    [
        "2026年部门预算编制说明",
        "财政拨款支出主要内容如下：",
        "1.“住房保障支出（类）住房改革支出（款）住房公积金（项）”102.22万元，主要用于在职人员公积金支出。",
        "2.“卫生健康支出（类）行政事业单位医疗（款）行政单位医疗（项）”10.99万元，主要用于医疗保险。",
        "3.“社会保障和就业支出（类）其他社会保障和就业支出（款）其他社会保障和就业支出（项）”3.01万 元，主要用于其他支出。",
    ]
)


def _main_content_doc() -> Any:
    doc = build_document(path="unit-2026-budget.pdf", page_texts=[_MAIN_CONTENT_PAGE], page_tables=[[]], filesize=1)
    table = "FIN_05_general_public_expenditure"
    facts = [
        _fact(table, "221", "total_actual", "102.2257", name="住房保障支出", unit="元", raw="1,022,257.00", stage="年初预算"),
        _fact(table, "22102", "total_actual", "102.2257", name="住房改革支出", unit="元", raw="1,022,257.00", stage="年初预算"),
        _fact(table, "2210201", "total_actual", "102.2257", name="住房公积金", unit="元", raw="1,022,257.00", stage="年初预算"),
        _fact(table, "210", "total_actual", "10.9909", name="卫生健康支出", unit="元", raw="109,909.00", stage="年初预算"),
        _fact(table, "21011", "total_actual", "10.9909", name="行政事业单位医疗", unit="元", raw="109,909.00", stage="年初预算"),
        _fact(table, "2101101", "total_actual", "10.9909", name="行政单位医疗", unit="元", raw="109,909.00", stage="年初预算"),
        _fact(table, "208", "total_actual", "3.405", name="社会保障和就业支出", unit="元", raw="34,050.00", stage="年初预算"),
        _fact(table, "20899", "total_actual", "3.405", name="其他社会保障和就业支出", unit="元", raw="34,050.00", stage="年初预算"),
        _fact(table, "2089999", "total_actual", "3.405", name="其他社会保障和就业支出", unit="元", raw="34,050.00", stage="年初预算"),
    ]
    return _attach_facts(doc, facts)


def test_bud114_reports_truncation_and_real_mismatch_separately() -> None:
    issues = BUD114_FunctionalEntryAmountConsistency().apply(_main_content_doc())

    by_code = {issue.location["subject_code"]: issue for issue in issues}
    assert set(by_code) == {"2210201", "2089999"}  # 10.99 是正确的四舍五入，不报
    truncation = by_code["2210201"]
    assert truncation.severity == "warn"
    assert "102.23" in truncation.message and "截尾" in truncation.message
    mismatch = by_code["2089999"]
    assert mismatch.severity == "error"
    assert "3.41" in mismatch.location["expected"]


# ---- 决议②：预算 T6/T7 必备 -------------------------------------------------


def test_bud001_reports_missing_fund_tables_even_without_fund() -> None:
    doc = build_document(path="unit25_budget.pdf", page_texts=["2025年预算报告"], page_tables=[[]], filesize=1)
    issues = BUD001_StructureAndAnchors().apply(doc)
    fund = [issue for issue in issues if "BUD_T6" in issue.message or "BUD_T7" in issue.message]
    assert {issue.severity for issue in fund} == {"error"}
    assert len(fund) == 2


def test_bud001_downgrades_missing_fund_table_when_text_declares_none() -> None:
    text = "2025年预算报告\n政府性基金拨款支出预算0万元；国有资本经营预算拨款支出预算0万元。"
    doc = build_document(path="unit25_budget.pdf", page_texts=[text], page_tables=[[]], filesize=1)
    issues = BUD001_StructureAndAnchors().apply(doc)
    fund = [issue for issue in issues if "BUD_T6" in issue.message or "BUD_T7" in issue.message]
    assert len(fund) == 2
    assert all(issue.severity == "medium" and "空表说明" in issue.message for issue in fund)


# ---- 决议③：科目名称定位宽松、判定严格 ---------------------------------------


def test_bud109_reports_suffix_only_difference_as_low() -> None:
    explanation = "\n".join(
        [
            "2026年部门预算编制说明",
            "财政拨款支出主要内容如下：",
            "1、住房保障支出（221类）住房改革支出（02款）住房公积金支出（01项）102.23万元。",
        ]
    )
    t5_text = "2026年部门一般公共预算支出功能分类预算表\n单位：元"
    t5_table: list[list[Any]] = [
        ["功能分类科目编码", None, None, "功能分类科目名称", "合计", "基本支出", "项目支出"],
        ["类", "款", "项", None, None, None, None],
        ["221", "", "", "住房保障支出", "1022257.00", "1022257.00", "0.00"],
        ["221", "02", "", "住房改革支出", "1022257.00", "1022257.00", "0.00"],
        ["221", "02", "01", "住房公积金", "1022257.00", "1022257.00", "0.00"],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf", page_texts=[explanation, t5_text], page_tables=[[], [t5_table]], filesize=1
    )
    issues = BUD109_FunctionalClassificationNameConsistency().apply(doc)
    assert len(issues) == 1
    assert issues[0].severity == "low"
    assert "名称多/少" in issues[0].message
    assert issues[0].location["expected_name"] == "住房公积金"


# ---- 决议④：三公数量要素金额驱动 ---------------------------------------------

_THREE_PUBLIC_SCOPE = "\n".join(
    [
        "（一）支出决算具体如下：",
        "1、因公出国（境）费支出决算为10.00万元，主要用于业务交流。",
        "2、公务用车购置费支出决算为30.00万元，主要用于车辆更新。",
        "3、公务接待费支出决算为0万元。",
    ]
)


def _three_public_doc(scope: str, extra_page: str = "") -> Any:
    doc = build_document(path="dept-2025-final.pdf", page_texts=[scope, extra_page], page_tables=[[], []], filesize=1)
    table = "FIN_07_three_public"
    return _attach_facts(doc, [
        _fact(table, "total", "overseas", "10.00"),
        _fact(table, "total", "vehicle_purchase", "30.00"),
        _fact(table, "total", "reception", "0"),
        _fact(table, "total", "overseas", "12.00", stage="年初预算"),
    ])


def _counts(doc: Any, scope: str) -> set:
    issues = count_issues(R33SGCompletion(), doc, report_kind="final", scope=scope,
                          section_title="七、“三公”经费支出决算情况说明", locate_page=lambda d, s: 1)
    return {issue.location["count_field"] for issue in issues}


def test_three_public_counts_required_only_for_positive_amounts() -> None:
    missing = _counts(_three_public_doc(_THREE_PUBLIC_SCOPE), _THREE_PUBLIC_SCOPE)
    # 接待费为 0：批次、人数不要求；出国、购置有金额：须披露数量；保有量始终要求
    assert missing == {"overseas_groups", "overseas_people", "purchase_count", "vehicle_stock"}


def test_three_public_counts_satisfied_by_disclosure_and_assets_section() -> None:
    scope = "\n".join(
        [
            "（一）支出决算具体如下：",
            "1、因公出国（境）费支出决算为10.00万元，组织出访团组1个、5人次。",
            "2、公务用车购置费支出决算为30.00万元，购置公务用车1辆。",
            "3、公务接待费支出决算为0万元。",
        ]
    )
    assets = "（五）国有资产占有使用情况\n截至2025年12月31日，本部门共有车辆3辆。"
    assert _counts(_three_public_doc(scope, assets), scope) == set()


# ---- 决议⑤：决算同科目收入≠支出 → 待人工复核 ---------------------------------


def test_cmm004_final_flags_large_income_expense_gap_for_manual_review() -> None:
    doc = build_document(path="dept-2025-final.pdf", page_texts=["2025年度部门决算"], page_tables=[[]], filesize=1)
    doc.report_kind = "final"
    _attach_facts(doc, [
        _fact("FIN_02_income", "201", "total_actual", "37.06"),
        _fact("FIN_02_income", "20133", "total_actual", "37.06"),
        _fact("FIN_03_expenditure", "201", "total_actual", "77.00"),
        _fact("FIN_03_expenditure", "20133", "total_actual", "77.00"),
        # 10% 以内的差额属结转结余正常形态，不提示
        _fact("FIN_02_income", "2080505", "total_actual", "100.00"),
        _fact("FIN_03_expenditure", "2080505", "total_actual", "105.00"),
    ])
    # table_facts 要求单一表实例：两张表各自一个实例
    issues = CMM004_CodeMirrorConsistency().apply(doc)
    assert [issue.location["subject_code"] for issue in issues] == ["20133"]
    assert issues[0].severity == "manual_review"
    assert "结转结余" in issues[0].message


# ---- 容差收紧后在样张回归中暴露的误报（2026-10-22 修复） -----------------------


def test_v33_235_full_year_budget_is_not_the_item_amount() -> None:
    """普陀样张「全年预算为18.12万元，支出决算为17.70万元」是预算对决算，
    不是同一金额前后两处披露；旧 0.5 容差曾掩盖该误报。"""
    from src.engine.rules_v33 import R33235_NarrativeAmountConsistency

    head = "五、一般公共预算财政拨款支出决算情况说明\n（三）一般公共预算财政拨款支出决算具体情况\n"
    budget_vs_actual = head + (
        "8、住房保障支出（类）住房改革支出（款）住房公积金（项）。主要用于：住房公积金支出。"
        "全年预算为18.12万元，支出决算为17.70万元，完成全年预算数的97.68%。\n"
    )
    repeated = head + (
        "8、住房保障支出（类）住房改革支出（款）住房公积金（项）18.12万元。主要用于：住房公积金支出。"
        "年初预算为20.00万元，支出决算为17.70万元。\n"
    )
    rule = R33235_NarrativeAmountConsistency()
    pages = ["2025年度部门决算"]
    assert rule.apply(build_document("dept-2025-final.pdf", [*pages, budget_vs_actual], [[], []], 0)) == []
    assert len(rule.apply(build_document("dept-2025-final.pdf", [*pages, repeated], [[], []], 0))) == 1


def test_reception_batches_accept_measure_word_between_number_and_batch() -> None:
    """上海市民政局样张「全年共接待492个批次、4364人次」属已披露。"""
    scope = "\n".join(
        [
            "（一）支出决算具体如下：",
            "1、因公出国（境）费支出决算为10.00万元，全年安排因公出国（境）团组4个、累计19人次。",
            "2、公务用车购置费支出决算为0万元。公务用车保有量为84辆。",
            "3、公务接待费支出决算为25.52万元。全年共接待492个批次、4364人次。",
        ]
    )
    doc = build_document("dept-2025-final.pdf", [scope], [[]], filesize=1)
    table = "FIN_07_three_public"
    _attach_facts(doc, [
        _fact(table, "total", "overseas", "10.00"),
        _fact(table, "total", "vehicle_purchase", "0"),
        _fact(table, "total", "reception", "25.52"),
    ])
    assert _counts(doc, scope) == set()
