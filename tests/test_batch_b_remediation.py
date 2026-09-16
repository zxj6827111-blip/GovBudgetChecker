"""Remediation Batch B Test Suite (T11 ~ T21).

Verifies the six deterministic defect remediation cards (B01 ~ B06):
- B01: Strict Value / Field Extractor (T15, T18)
- B02: V33-220 Field Binding & Column Disambiguation (T11, T12)
- B03: Zero & Narrative Extraction, near_number Year/Amount Disambiguation (T14, T19, T20)
- B04: _extract_t9_values & Missing vs Zero semantics (T15)
- B05: Precondition guards & RuleDeferred on missing data (T12, T13, T20)
- B06: Decimal math & tolerance envelopes (T16, T17, T21)
"""

from decimal import Decimal
import sys
from pathlib import Path
from typing import Any, List
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.field_extractor import (
    parse_strict_cell,
    extract_row_strict,
    find_column_index,
    STATUS_VALID,
    STATUS_ZERO,
    STATUS_BLANK,
    STATUS_INVALID,
    STATUS_MISSING_COLUMN,
    STATUS_OUT_OF_BOUNDS,
)
from src.engine.amount_math import (
    classify_amount_diff,
    compute_dynamic_envelope,
    rounding_envelope,
)
from src.engine.rule_outcome import (
    RuleDeferred,
    RuleExecutionError,
)
from src.engine.rules_v33 import (
    build_document,
    near_number,
    parse_number,
    R33103_Income_vs_Text,
    R33220_Narrative3_T3,
    R33240_Table2_IncomeAdvancedCheck,
    R33241_Table3_ExpenseAdvancedCheck,
    R33245_ThreePublicDirectionContradiction,
    R33246_DomesticReceptionDisclosure,
)
from src.engine.budget_rules import (
    _extract_t9_values,
    _extract_t9_strict_values,
    BUD102_T3TotalFormula,
    BUD103_T8TotalFormula,
    BUD107_TextTableConsistency,
    BUD111_ComparativePercentConsistency,
)


# ===========================================================================
# T11: V33-220 基本60/项目40，说明基本40/项目60 必检出字段错置
# ===========================================================================
def test_t11_v33_220_swapped_basic_project_fields():
    """T11: Directly running V33-220 detects swapped basic/project values."""
    page_texts = [
        "第三部分 2024年度部门决算情况说明\n"
        "二、支出决算情况说明\n"
        "2024年度本年支出合计100.00万元，其中：基本支出40.00万元，占40.00%；项目支出60.00万元，占60.00%。\n",
        "支出决算表\n单位：万元\n",
    ]
    # Table 3 has: basic = 60.00, project = 40.00 (swapped compared to text)
    table_3 = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "合计", "100.00", "60.00", "40.00"],
    ]
    doc = build_document(
        path="test_t11.pdf",
        page_texts=page_texts,
        page_tables=[[], [table_3]],
        filesize=100,
    )

    rule = R33220_Narrative3_T3()
    issues = rule.apply(doc)

    assert len(issues) > 0, "V33-220 must detect swapped basic/project amounts"
    issue_msgs = " ".join(issue.message for issue in issues)
    assert "基本支出" in issue_msgs or "项目支出" in issue_msgs
    assert "60.00" in issue_msgs and "40.00" in issue_msgs


# ===========================================================================
# T12: V33-220 正常金额及一侧未知（缺失列不静默 pass）
# ===========================================================================
def test_t12_v33_220_normal_pass_and_missing_column_deferred():
    """T12: Normal matching yields no issue; missing column raises RuleDeferred."""
    # 1. Normal matching
    page_texts = [
        "第三部分 2024年度部门决算情况说明\n"
        "二、支出决算情况说明\n"
        "2024年度本年支出合计100.00万元，其中：基本支出60.00万元，占60.00%；项目支出40.00万元，占40.00%。\n",
        "支出决算表\n单位：万元\n",
    ]
    table_3_normal = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "合计", "100.00", "60.00", "40.00"],
    ]
    doc_normal = build_document(
        path="test_t12_normal.pdf",
        page_texts=page_texts,
        page_tables=[[], [table_3_normal]],
        filesize=100,
    )
    issues = R33220_Narrative3_T3().apply(doc_normal)
    assert len(issues) == 0, "Normal consistent data must pass without issues"

    # 2. Table missing "项目支出" column
    table_3_missing_col = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出"],
        ["合计", "合计", "100.00", "60.00"],
    ]
    doc_missing = build_document(
        path="test_t12_missing.pdf",
        page_texts=page_texts,
        page_tables=[[], [table_3_missing_col]],
        filesize=100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        R33220_Narrative3_T3().apply(doc_missing)
    assert "未定位" in str(exc_info.value.detail) or "无法定位" in str(exc_info.value.detail)


# ===========================================================================
# T13: 续页列序变化/同名不同列组（跨页列漂移报 RuleDeferred，不得静默跳过）
# ===========================================================================
def test_t13_continuation_column_drift():
    """T13: Cross-page column drift in T2/T3 raises RuleDeferred instead of silent pass."""
    # T2 has 5 columns on page 1, but 7 columns on page 2 (continuation drift)
    t2_rows = [
        ["类", "款", "项", "科目名称", "本年收入合计"],
        ["201", "01", "01", "行政运行", "100.00"],
        ["201", "01", "02", "一般行政管理业务", "50.00", "30.00", "20.00"],  # drifted row length
    ]
    doc = build_document(
        path="test_t13.pdf",
        page_texts=["收入决算表\n2024年度收入决算情况说明"],
        page_tables=[[t2_rows]],
        filesize=100,
    )

    with pytest.raises(RuleDeferred) as exc_info:
        R33240_Table2_IncomeAdvancedCheck().apply(doc)
    assert "漂移" in str(exc_info.value.detail) or "不一致" in str(exc_info.value.detail)

    with pytest.raises(RuleDeferred) as exc_info_t3:
        # Similarly for T3
        doc_t3 = build_document(
            path="test_t13_t3.pdf",
            page_texts=["支出决算表\n2024年度支出决算情况说明"],
            page_tables=[[t2_rows]],
            filesize=100,
        )
        R33241_Table3_ExpenseAdvancedCheck().apply(doc_t3)
    assert "漂移" in str(exc_info_t3.value.detail) or "不一致" in str(exc_info_t3.value.detail)


def test_t13_same_width_swapped_column_headers():
    """T13: Same width (5 cols) but swapped columns across pages raises RuleDeferred."""
    page_1_t2 = [
        ["功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入", "其他收入"],
        ["合计", "合计", "100.00", "80.00", "20.00"],
    ]
    # Page 2 has same width (5 cols), but column 3 and 4 swapped!
    page_2_t2 = [
        ["功能分类科目编码", "科目名称", "本年收入合计", "其他收入", "财政拨款收入"],
        ["2010101", "行政运行", "50.00", "10.00", "40.00"],
    ]
    doc = build_document(
        path="test_t13_same_width.pdf",
        page_texts=["收入决算表\n2024年度收入决算情况说明", "收入决算表续"],
        page_tables=[[page_1_t2], [page_2_t2]],
        filesize=100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        R33240_Table2_IncomeAdvancedCheck().apply(doc)
    assert "换列漂移" in str(exc_info.value.detail) or "漂移" in str(exc_info.value.detail)

    # Similarly for T3 same-width swapped columns
    page_1_t3 = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "合计", "100.00", "60.00", "40.00"],
    ]
    page_2_t3 = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "项目支出", "基本支出"],
        ["2010101", "行政运行", "50.00", "20.00", "30.00"],
    ]
    doc_t3 = build_document(
        path="test_t13_same_width_t3.pdf",
        page_texts=["支出决算表\n2024年度支出决算情况说明", "支出决算表续"],
        page_tables=[[page_1_t3], [page_2_t3]],
        filesize=100,
    )
    with pytest.raises(RuleDeferred) as exc_info_t3:
        R33241_Table3_ExpenseAdvancedCheck().apply(doc_t3)
    assert "换列漂移" in str(exc_info_t3.value.detail) or "漂移" in str(exc_info_t3.value.detail)


# ===========================================================================
# T14: 表100、说明0.00；表0、说明0（真实0比较不误报，不符必检出）
# ===========================================================================
def test_t14_zero_values_comparison():
    """T14: Table 100 vs narrative 0.00 detected; Table 0 vs narrative 0 matches."""
    # 1. Table 100 vs Narrative 0.00
    page_mismatch = [
        "第三部分 2024年度部门决算情况说明\n"
        "二、支出决算情况说明\n"
        "2024年度本年支出合计100.00万元，其中：基本支出100.00万元，项目支出0.00万元。\n",
        "支出决算表\n单位：万元\n",
    ]
    table_mismatch = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "合计", "100.00", "100.00", "50.00"],  # Table has 50.00 for project
    ]
    doc_mismatch = build_document(
        path="test_t14_mismatch.pdf",
        page_texts=page_mismatch,
        page_tables=[[], [table_mismatch]],
        filesize=100,
    )
    issues = R33220_Narrative3_T3().apply(doc_mismatch)
    assert any("项目支出" in i.message for i in issues)

    # 2. Table 0 vs Narrative 0.00
    page_zero = [
        "第三部分 2024年度部门决算情况说明\n"
        "二、支出决算情况说明\n"
        "2024年度本年支出合计100.00万元，其中：基本支出100.00万元，项目支出0.00万元。\n",
        "支出决算表\n单位：万元\n",
    ]
    table_zero = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "合计", "100.00", "100.00", "0.00"],  # Table has 0.00 for project
    ]
    doc_zero = build_document(
        path="test_t14_zero.pdf",
        page_texts=page_zero,
        page_tables=[[], [table_zero]],
        filesize=100,
    )
    issues_zero = R33220_Narrative3_T3().apply(doc_zero)
    assert len(issues_zero) == 0, "Table 0.00 matching narrative 0.00 must not report issue"


# ===========================================================================
# T15: 三公缺列、越界、空白、无效文本、明确0
# ===========================================================================
def test_t15_extract_t9_strict_differentiation():
    """T15: _extract_t9_values returns None for missing/blank, and 0.0 for real zero."""
    # Row with valid 0.00 for abroad, but missing reception column
    header = ["合计", "因公出国", "公务用车购置及运行费", "公务用车购置", "公务用车运行"]
    data_row = ["10.00", "0.00", "10.00", "0.00", "10.00"]
    t9 = _extract_t9_values([header, data_row])

    # abroad is real zero
    assert t9["abroad"] == 0.0
    # reception was missing in header/row -> must be None, NOT 0.0!
    assert t9["reception"] is None

    # Strict extraction inspection
    strict_t9 = _extract_t9_strict_values([header, data_row])
    assert strict_t9["abroad"].status == STATUS_ZERO
    assert strict_t9["abroad"].decimal_val == Decimal("0.00")
    assert strict_t9["reception"].status in (STATUS_MISSING_COLUMN, STATUS_OUT_OF_BOUNDS)
    assert strict_t9["reception"].is_missing is True


# ===========================================================================
# T16: 10000.00 对 6000.00+3995.50 超舍入差额必检出
# ===========================================================================
def test_t16_large_amount_subtle_diff_must_be_detected():
    """T16: 10000.00 vs 6000.00 + 3995.50 (diff=4.50) must be mismatch, not ignored."""
    total = 10000.00
    sum_parts = 6000.00 + 3995.50  # 9995.50
    diff_label, diff_val = classify_amount_diff(total, sum_parts, 2)

    assert diff_label == "mismatch", "Difference of 4.50万元 exceeds rounding and must be mismatch"
    assert pytest.approx(float(diff_val), 0.001) == 4.50


# ===========================================================================
# T17: 显示舍入内 / 边界 / 刚超边界
# ===========================================================================
def test_t17_rounding_envelope_boundaries():
    """T17: Exact assertions for within envelope, boundary, and exceeding envelope."""
    # For n=3 terms, tolerance = (3 + 1) * 0.005 = 0.020 万元
    # 1. Exact -> "ok"
    l1, _ = classify_amount_diff(100.000, 100.000, 3)
    assert l1 == "ok"

    # 2. Within rounding envelope (0 < diff <= 0.020) -> "rounding_hint"
    l2, v2 = classify_amount_diff(100.00, 100.018, 3)
    assert l2 == "rounding_hint"
    assert pytest.approx(float(v2), 0.001) == 0.018

    # 3. Exceeds envelope (> 0.020) -> "mismatch"
    l3, v3 = classify_amount_diff(100.00, 100.025, 3)
    assert l3 == "mismatch"
    assert pytest.approx(float(v3), 0.001) == 0.025


# ===========================================================================
# T18: 原文100.00与100.0、元与万元（保留精度与单位）
# ===========================================================================
def test_t18_precision_and_units():
    """T18: Retain raw scale and unit during extraction and verify real dynamic comparison."""
    sv1 = parse_strict_cell("100.00")
    assert sv1.scale_digits == 2
    assert sv1.decimal_val == Decimal("100.00")

    sv2 = parse_strict_cell("100.0")
    assert sv2.scale_digits == 1
    assert sv2.decimal_val == Decimal("100.0")

    sv_yuan = parse_strict_cell("50000元")
    assert sv_yuan.unit == "元"
    assert sv_yuan.decimal_val == Decimal("5.0")  # 5万元

    # Real comparison with dynamic envelope
    sv_a = parse_strict_cell("100.0")   # scale 1 -> 0.05
    sv_b = parse_strict_cell("60.00")   # scale 2 -> 0.005
    sv_c = parse_strict_cell("40.000")  # scale 3 -> 0.0005
    env = compute_dynamic_envelope([sv_a, sv_b, sv_c])
    assert env > Decimal("0.05")
    # Small difference within dynamic envelope is accepted
    status_hint, _ = classify_amount_diff(Decimal("100.0"), Decimal("100.04"), envelope=env)
    assert status_hint == "rounding_hint"
    # Large difference exceeding dynamic envelope is flagged as mismatch
    status_mismatch, _ = classify_amount_diff(Decimal("100.0"), Decimal("100.08"), envelope=env)
    assert status_mismatch == "mismatch"


# ===========================================================================
# T19: 同句2024年90万元、2025年100万元（目标期间取数）
# ===========================================================================
def test_t19_multi_year_tokens_disambiguation():
    """T19: In sentence with 2024 and 2025, target_year retrieves the correct figure."""
    text = "2024年度支出决算为90.00万元，2025年度预算安排为100.00万元。"
    
    # Targeting 2025
    val_2025 = near_number(text, ["预算安排", "支出"], target_year=2025)
    assert val_2025 == 100.00

    # Targeting 2024
    val_2024 = near_number(text, ["决算", "支出"], target_year=2024)
    assert val_2024 == 90.00


# ===========================================================================
# T20: 实际金额2024万元、一般公共预算名称、章节缺失不跨章替代
# ===========================================================================
def test_t20_amount_named_2024_and_no_cross_section_fallback():
    """T20: Amount of 2024.00万元 is not dropped as year; missing section raises RuleDeferred."""
    # 1. Amount of 2024.00 is extracted as amount, not stripped
    text = "一般公共预算财政拨款支出为2024.00万元。"
    val = near_number(text, ["一般公共预算财政拨款支出", "支出"])
    assert val == 2024.00

    # 2. Missing Three-Public section in doc does NOT fall back to other sections
    doc_no_san_gong = build_document(
        path="test_t20_no_sg.pdf",
        page_texts=["第一部分 概况\n十一、其他重要事项说明：公务接待费0批次。"],
        page_tables=[[]],
        filesize=50,
    )
    with pytest.raises(RuleDeferred) as exc_v245:
        R33245_ThreePublicDirectionContradiction().apply(doc_no_san_gong)
    assert "未找到三公经费说明章节" in str(exc_v245.value.detail)

    with pytest.raises(RuleDeferred) as exc_v246:
        R33246_DomesticReceptionDisclosure().apply(doc_no_san_gong)
    assert "未找到三公经费说明章节" in str(exc_v246.value.detail)


# ===========================================================================
# T21: 比例正常 / 错误 / 分母0（调用 BUD111_ComparativePercentConsistency）
# ===========================================================================
def test_t21_percentage_calculation_and_zero_division():
    """T21: Real BUD111_ComparativePercentConsistency execution (normal, mismatch, zero denom)."""
    # 1. Normal consistent percentage -> passes with []
    text_normal = (
        "2026年部门预算编制说明\n"
        "1、公共安全支出。2025年当年预算执行数为100.00万元，"
        "2026年预算安排120.00万元，比2025年当年预算执行数增加20.00%。\n"
    )
    doc_normal = build_document("t21_normal.pdf", [text_normal], [[]], 100)
    issues_normal = BUD111_ComparativePercentConsistency().apply(doc_normal)
    assert issues_normal == []

    # 2. Percentage direction mismatch -> error
    text_mismatch = (
        "2026年部门预算编制说明\n"
        "1、公共安全支出。2025年当年预算执行数为100.00万元，"
        "2026年预算安排120.00万元，比2025年当年预算执行数减少20.00%。\n"
    )
    doc_mismatch = build_document("t21_mismatch.pdf", [text_mismatch], [[]], 100)
    issues_mismatch = BUD111_ComparativePercentConsistency().apply(doc_mismatch)
    assert any(i.severity == "error" and "增加20.00%" in i.message for i in issues_mismatch)

    # 3. Base year is zero -> raises RuleDeferred (基期为0)
    text_zero_base = (
        "2026年部门预算编制说明\n"
        "1、公共安全支出。2025年当年预算执行数为0.00万元，"
        "2026年预算安排100.00万元，比2025年当年预算执行数增加100.00%。\n"
    )
    doc_zero = build_document("t21_zero.pdf", [text_zero_base], [[]], 100)
    with pytest.raises(RuleDeferred) as exc_zero:
        BUD111_ComparativePercentConsistency().apply(doc_zero)
    assert "基期为0" in str(exc_zero.value.detail)


# ===========================================================================
# P1-1: 严格取数（占位符空白、负数换算、有限数校验、歧义列抑制）
# ===========================================================================
def test_remediation_p1_1_strict_value_parsing():
    """P1-1: Placeholder symbols as blank, negative unit conversion, finite check, ambiguous columns."""
    # 1. Placeholders /, 一, - are STATUS_BLANK with decimal_val=None
    for sym in ["/", "一", "-", "—", "–"]:
        sv = parse_strict_cell(sym)
        assert sv.status == STATUS_BLANK, f"Symbol {sym} must be STATUS_BLANK"
        assert sv.decimal_val is None
        assert sv.is_missing is True

    # 2. Negative unit conversion: -1000元 -> -0.1万元
    sv_neg = parse_strict_cell("-1000元")
    assert sv_neg.status == STATUS_VALID
    assert sv_neg.decimal_val == Decimal("-0.1")

    # 3. Non-finite values: NaN and Infinity are STATUS_INVALID
    for non_finite in ["NaN", "Infinity", "-Infinity", "nan", "inf"]:
        sv_nf = parse_strict_cell(non_finite)
        assert sv_nf.status == STATUS_INVALID
        assert sv_nf.decimal_val is None

    # 4. Ambiguous columns return None
    assert find_column_index(["基本支出", "基本支出"], ["基本支出"]) is None


# ===========================================================================
# P1-2: V33-220 单位转换与动态精度对比
# ===========================================================================
def test_remediation_p1_2_v33_220_unit_conversion():
    """P1-2: Table in 元 vs narrative in 万元, unknown unit deferral."""
    # 1. Table in 元 (600000元 = 60万元, 400000元 = 40万元) vs narrative in 万元
    page_texts_yuan = [
        "第三部分 2024年度部门决算情况说明\n"
        "二、支出决算情况说明\n"
        "2024年度本年支出合计100.00万元，其中：基本支出60.00万元，占60.00%；项目支出40.00万元，占40.00%。\n",
        "支出决算表\n单位：元\n",
    ]
    table_yuan = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "合计", "1000000", "600000", "400000"],
    ]
    doc_yuan = build_document("t_yuan.pdf", page_texts_yuan, [[], [table_yuan]], 100)
    issues_yuan = R33220_Narrative3_T3().apply(doc_yuan)
    assert issues_yuan == [], "600000元 must match 60.00万元 without false positive"

    # 2. Unknown unit raises RuleDeferred
    page_texts_no_unit = [
        "第三部分 2024年度部门决算情况说明\n"
        "二、支出决算情况说明\n"
        "2024年度本年支出合计100.00万元，其中：基本支出60.00万元，占60.00%；项目支出40.00万元，占40.00%。\n",
        "支出决算表\n",  # No unit specified on table page
    ]
    doc_no_unit = build_document("t_no_unit.pdf", page_texts_no_unit, [[], [table_yuan]], 100)
    with pytest.raises(RuleDeferred) as exc_info:
        R33220_Narrative3_T3().apply(doc_no_unit)
    assert "金额单位未知" in str(exc_info.value.detail)


# ===========================================================================
# P1-3: 年度与指标隔离，禁止跨句与跨章节
# ===========================================================================
def test_remediation_p1_3_cross_sentence_and_year_scoping():
    """P1-3: near_number strictly respects sentence boundary and target_year, no cross-section fallback."""
    # 1. Strict sentence boundary: cannot match "支出" from previous sentence
    text_cross = "2024年度支出决算为90.00万元。2025年度收入安排为100.00万元。"
    val = near_number(text_cross, ["支出"], target_year=2025)
    assert val is None, "near_number must not bridge across sentence boundary"

    # 2. No conflicting year fallback: searching for 2025 does NOT return 2024
    text_only_2024 = "2024年度支出决算为90.00万元。"
    val_none = near_number(text_only_2024, ["支出决算"], target_year=2025)
    assert val_none is None, "Must not fall back to 2024 when target_year is 2025"

    # 3. Section scoping: missing section raises RuleDeferred instead of cross-section search
    doc_no_income = build_document(
        "no_income.pdf",
        ["收入决算表\n单位：万元\n", "第一部分 概况\n三、支出决算情况说明：支出合计100万元。"],
        [[[["合计", "100.00"]]], []],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_inc:
        R33103_Income_vs_Text().apply(doc_no_income)
    assert "未找到收入决算情况说明章节" in str(exc_inc.value.detail)


# ===========================================================================
# P1-4: 部分完成状态保持与两类顺序
# ===========================================================================
def test_remediation_p1_4_partial_issue_preservation_and_bud107_orderings():
    """P1-4: Preserve partial findings on deferred rules and verify BUD-107 both orderings."""
    # 1. V33-103 preserves partial total issue even when grant row is missing
    page_texts_v103 = [
        "二、收入决算情况说明\n本年收入合计90.00万元，财政拨款收入80.00万元。\n",
        "收入决算表\n单位：万元\n",
    ]
    # Table has total row (100.00, mismatching 90.00), but missing grant row
    table_v103 = [
        ["科目编码", "科目名称", "本年收入合计"],
        ["合计", "合计", "100.00"],
    ]
    doc_v103 = build_document("v103_part.pdf", page_texts_v103, [[], [table_v103]], 100)
    with pytest.raises(RuleDeferred) as exc_v103:
        R33103_Income_vs_Text().apply(doc_v103)
    assert any("收入合计" in str(i.message) for i in exc_v103.value.partial_issues), (
        "V33-103 must preserve partial finding for total mismatch"
    )

    # 2. BUD-107 Order A: Missing before Error (T1 missing, T4 mismatch)
    narrative_text = (
        "一、收支总体情况说明\n"
        "预算收支总计100.00万元。\n"
        "财政拨款支出预算100.00万元。\n"
    )
    # T1 missing, T4 present with mismatch (80.00 != 100.00)
    table_t4_mismatch = [
        ["项目", "预算数", "项目", "预算数"],
        ["收入总计", "80.00", "支出总计", "80.00"],
    ]
    doc_bud107_a = build_document(
        "bud107_a.pdf",
        [narrative_text, "财政拨款收支预算总表\n单位：万元\n"],
        [[], [table_t4_mismatch]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_a:
        BUD107_TextTableConsistency().apply(doc_bud107_a)
    assert any("T4" in str(i.message) for i in exc_a.value.partial_issues), (
        "BUD-107 must preserve T4 mismatch finding when T1 is missing"
    )

    # 3. BUD-107 Order B: Error before Missing (T1 mismatch, T4 missing)
    table_t1_mismatch = [
        ["项目", "预算数", "项目", "预算数"],
        ["收入总计", "80.00", "支出总计", "80.00"],
    ]
    doc_bud107_b = build_document(
        "bud107_b.pdf",
        [narrative_text, "财务收支预算总表\n单位：万元\n"],
        [[], [table_t1_mismatch]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_b:
        BUD107_TextTableConsistency().apply(doc_bud107_b)
    assert any("T1" in str(i.message) for i in exc_b.value.partial_issues), (
        "BUD-107 must preserve T1 mismatch finding when T4 is missing"
    )

    # 4. BUD-107 Case C: Table present but no narrative raises RuleDeferred
    doc_bud107_c = build_document(
        "bud107_c.pdf",
        ["概况\n无说明内容\n", "财务收支预算总表\n单位：万元\n"],
        [[], [table_t1_mismatch]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_c:
        BUD107_TextTableConsistency().apply(doc_bud107_c)
    assert any("T1" in r for r in exc_c.value.unresolved_reasons)

    # 5. BUD-107 Case D: All normal returns []
    narrative_all = (
        "一、收支总体情况说明\n"
        "收入预算100.00万元，支出预算100.00万元。\n"
        "财政拨款支出预算100.00万元。\n"
        "三公经费预算数为15.00万元，其中因公出国境费5.00万元，公务接待费10.00万元。机关运行经费预算为20.00万元。\n"
    )
    table_t1_ok = [
        ["项目", "预算数", "项目", "预算数"],
        ["收入总计", "100.00", "支出总计", "100.00"],
    ]
    table_t4_ok = [
        ["项目", "预算数", "项目", "预算数"],
        ["收入总计", "100.00", "支出总计", "100.00"],
    ]
    table_t9_ok = [
        ["合计", "因公出国", "公务接待费", "机关运行经费"],
        ["15.00", "5.00", "10.00", "20.00"],
    ]
    doc_bud107_d = build_document(
        "bud107_d.pdf",
        [
            narrative_all,
            "财务收支预算总表\n单位：万元\n",
            "财政拨款收支预算总表\n单位：万元\n",
            "三公经费和机关运行经费预算表\n单位：万元\n",
        ],
        [[], [table_t1_ok], [table_t4_ok], [table_t9_ok]],
        100,
    )
    assert BUD107_TextTableConsistency().apply(doc_bud107_d) == []


# ===========================================================================
# P1-5: BUD-102 动态精度包络（1位小数不误报、3位小数超差必报）
# ===========================================================================
def test_remediation_p1_5_bud102_dynamic_envelope():
    """P1-5: BUD-102 dynamic envelope prevents 1-decimal false positive and detects 3-decimal mismatch."""
    # 1. 1-decimal numbers within envelope (100.0 = 60.0 + 40.0) -> returns []
    table_1_decimal = [
        ["科目", "合计", "基本支出", "项目支出"],
        ["合计", "100.0", "60.0", "40.0"],
    ]
    doc_1dec = build_document(
        "b102_1dec.pdf",
        ["支出预算总表\n单位：万元\n"],
        [[table_1_decimal]],
        100,
    )
    assert BUD102_T3TotalFormula().apply(doc_1dec) == []

    # 2. 3-decimal numbers exceeding envelope (100.000 vs 60.000 + 40.010, diff=0.010 > envelope 0.0015)
    table_3_decimal = [
        ["科目", "合计", "基本支出", "项目支出"],
        ["合计", "100.000", "60.000", "40.010"],
    ]
    doc_3dec = build_document(
        "b102_3dec.pdf",
        ["支出预算总表\n单位：万元\n"],
        [[table_3_decimal]],
        100,
    )
    issues_3dec = BUD102_T3TotalFormula().apply(doc_3dec)
    assert len(issues_3dec) == 1
    assert "T3勾稽错误" in issues_3dec[0].message
    assert "0.01" in issues_3dec[0].message

    # 3. Large error: 10000.00 vs 6000.00 + 3995.50 (diff=4.50)
    table_large_err = [
        ["科目", "合计", "基本支出", "项目支出"],
        ["合计", "10000.00", "6000.00", "3995.50"],
    ]
    doc_large = build_document(
        "b102_large.pdf",
        ["支出预算总表\n单位：万元\n"],
        [[table_large_err]],
        100,
    )
    issues_large = BUD102_T3TotalFormula().apply(doc_large)
    assert len(issues_large) == 1
    assert "T3勾稽错误" in issues_large[0].message
    assert "4.50" in issues_large[0].message


# ===========================================================================
# Round 2 Remediation Tests (R2-P1-1, R2-P1-2, R2-P1-3, R2-P2-1)
# ===========================================================================
def test_remediation_r2_bud102_missing_project_column_deferred():
    """R2-P1-1: BUD-102 must NOT guess project column using '其他支出' or blind fallback."""
    table_other = [
        ["科目名称", "合计", "基本支出", "其他支出"],
        ["合计", "100.00", "60.00", "40.00"],
    ]
    doc = build_document(
        "bud102_missing_project.pdf",
        ["2025年部门预算\n支出预算总表\n单位：万元\n"],
        [[table_other]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        BUD102_T3TotalFormula().apply(doc)
    assert "T3未找到项目支出数值" in str(exc_info.value)


def test_remediation_r2_near_number_year_backtracking_and_conflicting_years():
    """R2-P1-2: near_number must not truncate years to numbers or treat amounts with '万元' as conflicting years."""
    # Year suffix backtracking: '收入2025年为100万元。' must extract 100.0, not 202.0
    val1 = near_number("收入2025年为100万元。", ["收入"], target_year=2025)
    assert val1 == 100.0

    # Conflicting year: '本年收入合计2024万元。' has amount 2024, not year 2024
    val2 = near_number("本年收入合计2024万元。", ["本年收入合计"], target_year=2025)
    assert val2 == 2024.0


def test_remediation_r2_settlement_vs_budget_scope_isolation():
    """R2-P1-2: Settlement rules (V33-103/104/105) must not bind budget explanation chapters."""
    doc = build_document(
        "settlement_wrong_scope.pdf",
        [
            "一、收入预算情况说明\n本年收入合计100.00万元，其中一般公共预算收入90.00万元。",
            "收入决算表\n单位：万元\n",
        ],
        [
            [],
            [[
                ["科目", "合计", "财政拨款收入"],
                ["本年收入合计", "100.00", "90.00"],
            ]],
        ],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        R33103_Income_vs_Text().apply(doc)
    assert "未找到收入决算情况说明章节" in str(exc_info.value)


def test_remediation_r2_v220_fine_and_coarse_precision():
    """R2-P1-3: V33-220 dynamic precision handles fine mismatches and coarse tolerance."""
    narrative_template = (
        "三、支出决算情况说明\n"
        "2025年度支出合计100.00万元，其中：基本支出{basic}万元，占60.00%；项目支出40.00万元，占40.00%。"
    )
    headers = ["功能分类科目编码", "功能分类科目名称", "本年支出合计", "基本支出", "项目支出"]

    # Fine precision: table basic 60.000, narrative basic 60.004 -> diff 0.004 > envelope 0.0010 -> mismatch
    text_fine = narrative_template.format(basic="60.004")
    table_fine = [headers, ["合计", "合计", "100.00", "60.000", "40.00"]]
    doc_fine = build_document(
        "v220_fine.pdf",
        [text_fine, "支出决算表\n单位：万元\n"],
        [[], [table_fine]],
        100,
    )
    issues_fine = R33220_Narrative3_T3().apply(doc_fine)
    assert len(issues_fine) == 1
    assert "说明3↔T3基本支出不一致" in issues_fine[0].message
    assert "60.004" in issues_fine[0].message
    assert "60.000" in issues_fine[0].message
    assert "0.004" in issues_fine[0].message

    # Coarse precision: table basic 60.04, narrative basic 60.0 -> diff 0.04 <= envelope 0.055 -> match
    text_coarse = narrative_template.format(basic="60.0")
    table_coarse = [headers, ["合计", "合计", "100.00", "60.04", "40.00"]]
    doc_coarse = build_document(
        "v220_coarse.pdf",
        [text_coarse, "支出决算表\n单位：万元\n"],
        [[], [table_coarse]],
        100,
    )
    issues_coarse = R33220_Narrative3_T3().apply(doc_coarse)
    assert issues_coarse == []


def test_remediation_r2_bud103_three_decimals_dynamic_envelope():
    """R2-P1-3: BUD-103 dynamic envelope flags 3-decimal mismatch exceeding tolerance."""
    table_t8 = [
        ["科目名称", "合计", "人员经费", "公用经费"],
        ["合计", "100.000", "60.000", "39.995"],
    ]
    doc = build_document(
        "bud103_3dec.pdf",
        ["2025年部门预算\n一般公共预算基本支出部门预算经济分类预算表\n单位：万元\n"],
        [[table_t8]],
        100,
    )
    issues = BUD103_T8TotalFormula().apply(doc)
    assert len(issues) == 1
    assert "T8勾稽错误" in issues[0].message
    assert "100.000" in issues[0].message
    assert "99.995" in issues[0].message
    assert "0.005" in issues[0].message


def test_remediation_r2_log_scanner_broad_keyword_exemption():
    """R2-P2-1: AST log scanner only exempts RuleOutcomeSignal family, flags PayloadError."""
    from scripts.check_log_message_safety import check_source

    leaky_ordinary = """
class PayloadError(Exception):
    def __init__(self, message, partial_issues=None):
        super().__init__(message)
        self.partial_issues = partial_issues

issues = ["audit_secret_marker"]
try:
    raise PayloadError("leaked", partial_issues=issues)
except PayloadError as exc:
    rendered_message = str(exc)
"""
    violations = check_source(leaky_ordinary)
    assert len(violations) == 1
    assert violations[0].expression == "issues"

    benign_deferred = """
from src.engine.rule_outcome import RuleDeferred
issues = ["safe_issue"]
raise RuleDeferred(reasons=["insufficient_data"], partial_issues=issues)
"""
    assert len(check_source(benign_deferred)) == 0

# ===========================================================================
# R3 REGRESSION TESTS (P1-1, P1-2a/b, P2-1)
# ===========================================================================

from src.engine.rules_v33 import (
    _extract_amount_from_segment,  # noqa: F811
    near_strict_number,
)
from src.engine.budget_rules import (
    _extract_t3_strict,
    _extract_t8_strict,
    _dynamic_half_unit_tol,
    BUD105_CrossTableChecks,
)


# ---------------------------------------------------------------------------
# P1-1: no cross-clause borrowing in _extract_amount_from_segment
# ---------------------------------------------------------------------------

def test_remediation_r3_p1_1_no_cross_clause_borrow_forward():
    """\u5173键词后的数字应优先于同一逗号子句内的数字；开头的60不应害等。"""
    # "\u9879目支出"(item expense) keyword: correct answer is 40, NOT 60
    result = _extract_amount_from_segment(
        "基本支出60万元，项目支出为40万元。",
        ["项目支出"],
    )
    assert result is not None, "Should extract amount"
    val, scale, unit = result
    assert float(val) == 40.0, f"Expected 40.0 (not 60.0), got {float(val)}"


def test_remediation_r3_p1_1_no_cross_clause_borrow_reverse():
    """\u5173键词前的数字，同句无其他数字时仍可识别。"""
    result = _extract_amount_from_segment(
        "财政拨款60万元。",
        ["财政拨款"],
    )
    assert result is not None
    val, _, _ = result
    assert float(val) == 60.0, f"Expected 60.0, got {float(val)}"


def test_remediation_r3_p1_1_v33_103_no_spurious_finding():
    """V33-103\u6b63常说明: 收入合计100、财政拨款60 \u2014— 不应误报财政拨款\u53d6到60​/100​。"""
    text = "本年收入合计100万元，财政拨款收入为60万元。"
    # total: keyword ["收入合计"] -> should get 100
    res_total = near_strict_number(text, ["本年收入合计", "收入合计", "合计"])
    assert res_total is not None and float(res_total.decimal_val) == 100.0, (
        f"total should be 100, got {res_total}"
    )
    # fiscal: keyword ["财政拨款收入"] -> should get 60, not 100
    res_fp = near_strict_number(text, ["财政拨款收入"])
    assert res_fp is not None and float(res_fp.decimal_val) == 60.0, (
        f"fiscal should be 60, got {res_fp}"
    )


# ---------------------------------------------------------------------------
# P2-1: integer amounts in aggregate row must NOT be skipped as subject codes
# ---------------------------------------------------------------------------

def test_remediation_r3_p2_1_integer_amount_not_skipped_t3():
    """\u5408计行["\u5408计","100","60","40"](万元)应正常提取100/60/40，不误判为编码行跳过。"""
    header = ["科目名称", "合计", "基本支出", "项目支出"]
    total_row = ["合计", "100", "60", "40"]
    rows = [header, total_row]
    v_tot, v_bas, v_prj = _extract_t3_strict(rows, default_unit="万元")
    assert v_tot is not None and v_tot.is_numeric, f"total should be found, got {v_tot}"
    assert v_bas is not None and v_bas.is_numeric, f"basic should be found, got {v_bas}"
    assert v_prj is not None and v_prj.is_numeric, f"project should be found, got {v_prj}"
    assert float(v_tot.decimal_val) == 100.0
    assert float(v_bas.decimal_val) == 60.0
    assert float(v_prj.decimal_val) == 40.0


def test_remediation_r3_p2_1_real_code_row_still_skipped():
    """\u771f实编码行（第一格为3位整数201）应仍被跳过。"""
    header = ["科目编码", "合计", "基本支出", "项目支出"]
    code_row = ["201", "100", "60", "40"]   # column 0 is a 3-digit code
    total_row = ["合计", "200", "120", "80"]
    rows = [header, code_row, total_row]
    v_tot, v_bas, v_prj = _extract_t3_strict(rows, default_unit="万元")
    assert v_tot is not None and float(v_tot.decimal_val) == 200.0, (
        f"Should pick the real total row (200), got {v_tot}"
    )


def test_remediation_r3_p2_1_integer_amount_not_skipped_t8():
    """T8同型: 合计行["合计","100","70","30"]应正常提取。"""
    header = ["科目名称", "合计", "人员经费", "公用经费"]
    total_row = ["合计", "100", "70", "30"]
    rows = [header, total_row]
    v_tot, v_per, v_pub = _extract_t8_strict(rows, default_unit="万元")
    assert v_tot is not None and v_tot.is_numeric, f"total should be found, got {v_tot}"
    assert float(v_tot.decimal_val) == 100.0
    assert v_per is not None and float(v_per.decimal_val) == 70.0
    assert v_pub is not None and float(v_pub.decimal_val) == 30.0


# ---------------------------------------------------------------------------
# P1-2a: BUD-105 unit normalisation and cross-table check
# ---------------------------------------------------------------------------

def test_remediation_r3_p1_2a_bud105_unit_normalisation():
    """T1单位'元'(1000000.00)与T4单位'万元'(100.00)等价，BUD-105不应误报不一致。"""
    t1_table = [
        ["收入总计", "1000000.00", "支出总计", "1000000.00"],
    ]
    t4_table = [
        ["收入总计", "100.00", "支出总计", "100.00"],
    ]
    doc = build_document(
        "bud105_norm.pdf",
        [
            "表1 部门财务收支预算总表\n单位：元",
            "表4 财政拨款收支预算总表\n单位：万元",
        ],
        [[t1_table], [t4_table]],
        100,
    )
    doc.units_per_page = ["元", "万元"]

    rule = BUD105_CrossTableChecks()
    try:
        issues = rule.apply(doc)
    except RuleDeferred as exc:
        # T3, T5, T8 missing are deferred reasons, but partial_issues should NOT contain T1/T4 errors!
        issues = exc.partial_issues

    t1_t4_issues = [iss for iss in issues if "T1与T4" in iss.message]
    assert len(t1_t4_issues) == 0, f"Expected 0 mismatch issues for equivalent units, got {t1_t4_issues}"


def test_remediation_r3_p1_2a_bud105_unit_mismatch_detected():
    """T1单位'元'(2000000.00即200万)与T4单位'万元'(100.00)真正差额100万，应准确检出。"""
    t1_table = [
        ["收入总计", "2000000.00", "支出总计", "2000000.00"],
    ]
    t4_table = [
        ["收入总计", "100.00", "支出总计", "100.00"],
    ]
    doc = build_document(
        "bud105_mismatch.pdf",
        [
            "表1 部门财务收支预算总表\n单位：元",
            "表4 财政拨款收支预算总表\n单位：万元",
        ],
        [[t1_table], [t4_table]],
        100,
    )
    doc.units_per_page = ["元", "万元"]

    rule = BUD105_CrossTableChecks()
    try:
        issues = rule.apply(doc)
    except RuleDeferred as exc:
        issues = exc.partial_issues

    t1_t4_issues = [iss for iss in issues if "T1与T4" in iss.message]
    assert len(t1_t4_issues) == 2, f"Expected 2 issues (income + expense mismatch), got {t1_t4_issues}"
    assert any("差额=100.00" in iss.message for iss in t1_t4_issues)


# ---------------------------------------------------------------------------
# P1-2b: BUD-107 dynamic precision tolerance (3 decimal places diff detected)
# ---------------------------------------------------------------------------

def test_remediation_r3_p1_2b_bud107_three_decimal_mismatch_detected():
    """T1收入100.000万元，文本100.004万元：差额0.004超过两侧三位精度动态包络0.001，必须检出！"""
    t1_table = [
        ["收入总计", "100.000", "支出总计", "100.000"],
    ]
    doc = build_document(
        "bud107_3dp.pdf",
        [
            "表1 部门财务收支预算总表\n单位：万元\n说明：本年收入预算100.004万元，支出预算100.000万元。",
        ],
        [[t1_table]],
        100,
    )
    doc.units_per_page = ["万元"]

    rule = BUD107_TextTableConsistency()
    try:
        issues = rule.apply(doc)
    except RuleDeferred as exc:
        issues = exc.partial_issues

    inc_issues = [iss for iss in issues if "文本收入预算与T1不一致" in iss.message]
    assert len(inc_issues) == 1, f"Expected 1 income mismatch issue, got {inc_issues}"
    assert "文本=100.004万元" in inc_issues[0].message
    assert "表=100.000万元" in inc_issues[0].message


# ===========================================================================
# R4 REGRESSION TESTS (P1-1, P1-2)
# ===========================================================================

from src.engine.budget_rules import BUD101_T1Balance  # noqa: E402


def test_remediation_r4_p1_1_undisclosed_item_not_borrow_cross_clause():
    """R4-P1-1: 指标未披露时不跨子句借用相邻指标金额，V33-103正确触发RuleDeferred。"""
    # 1. near_number 直接验证
    res = near_number("基本支出60万元，项目支出未披露。", ["项目支出"])
    assert res is None, f"Expected None for undisclosed item, got {res}"

    # 2. V33-103 实际规则验证
    table = [[["项目", "金额"], ["本年收入合计", "100.00"], ["财政拨款收入", "100.00"]]]
    text = "收入决算表\n单位：万元\n一、收入决算情况说明\n本年收入合计100万元，财政拨款收入未披露。"
    doc = build_document("r4_v103.pdf", [text], [table], 100)
    rule = R33103_Income_vs_Text()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)
    assert any("财政拨款收入" in r for r in exc_info.value.unresolved_reasons)
    assert exc_info.value.partial_issues == []


def test_remediation_r4_p1_2_bud101_blank_income_not_take_expense():
    """R4-P1-2: 收入为空时绝不越过'支出总计'标签偷支出金额产生假平衡。"""
    row = ["收入总计", "", "支出总计", "100.00"]
    doc = build_document(
        "r4_bud101.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[["项目", "预算数", "项目", "预算数"], row]]],
        100,
    )
    rule = BUD101_T1Balance()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)
    assert any("收入总计" in r for r in exc_info.value.unresolved_reasons)
    assert exc_info.value.partial_issues == []


def test_remediation_r4_p1_2_bud101_blank_expense_not_take_other():
    """R4-P1-2: 支出为空时同理正确抛出RuleDeferred。"""
    row = ["收入总计", "100.00", "支出总计", ""]
    doc = build_document(
        "r4_bud101_exp.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[["项目", "预算数", "项目", "预算数"], row]]],
        100,
    )
    rule = BUD101_T1Balance()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)
    assert any("支出总计" in r for r in exc_info.value.unresolved_reasons)
    assert exc_info.value.partial_issues == []


# ===========================================================================
# R5 REGRESSION TESTS (P1-1, P1-2)
# ===========================================================================

def test_remediation_r5_p1_1_blank_current_budget_with_prior_column_deferred():
    """R5-P1-1: 本年收入空白、上年收入100时，BUD-101不借用上年数值，正确抛出RuleDeferred。"""
    headers = ["项目", "本年预算数", "上年预算数", "项目", "本年预算数", "上年预算数"]
    row_income_blank = ["收入总计", "", "100.00", "支出总计", "100.00", "100.00"]
    doc_blank_income = build_document(
        "r5_bud101_blank_inc.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_income_blank]]],
        100,
    )
    rule = BUD101_T1Balance()
    with pytest.raises(RuleDeferred) as exc_inc:
        rule.apply(doc_blank_income)
    assert any("收入总计" in r for r in exc_inc.value.unresolved_reasons)
    assert exc_inc.value.partial_issues == []

    row_expense_blank = ["收入总计", "100.00", "100.00", "支出总计", "", "100.00"]
    doc_blank_exp = build_document(
        "r5_bud101_blank_exp.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_expense_blank]]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_exp:
        rule.apply(doc_blank_exp)
    assert any("支出总计" in r for r in exc_exp.value.unresolved_reasons)
    assert exc_exp.value.partial_issues == []


def test_remediation_r5_p1_1_dual_year_valid_and_zero_budget():
    """R5-P1-1: 双年度表格本年数值正常匹配或本年为0时，BUD-101正常通过。"""
    headers = ["项目", "本年预算数", "上年预算数", "项目", "本年预算数", "上年预算数"]
    rule = BUD101_T1Balance()

    row_valid = ["收入总计", "100.00", "90.00", "支出总计", "100.00", "90.00"]
    doc_valid = build_document(
        "r5_bud101_valid.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_valid]]],
        100,
    )
    assert rule.apply(doc_valid) == []

    row_zero = ["收入总计", "0.00", "100.00", "支出总计", "0.00", "100.00"]
    doc_zero = build_document(
        "r5_bud101_zero.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_zero]]],
        100,
    )
    assert rule.apply(doc_zero) == []


def test_remediation_r5_p1_2_bud105_missing_project_column_retains_total_mismatch():
    """R5-P1-2: BUD-105在T3仅缺失项目支出列时，仍正确保留合计差异10.00，reasons仅记录项目列缺失。"""
    t3 = [
        ["科目名称", "合计", "基本支出"],
        ["合计", "100.00", "60.00"],
    ]
    t5 = [
        ["科目名称", "合计", "基本支出", "项目支出"],
        ["合计", "90.00", "60.00", "30.00"],
    ]
    doc = build_document(
        "r5_bud105_partial.pdf",
        [
            "支出预算总表\n单位：万元",
            "一般公共预算支出功能分类预算表\n单位：万元",
        ],
        [[t3], [t5]],
        100,
    )
    rule = BUD105_CrossTableChecks()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)

    issues = exc_info.value.partial_issues
    assert len(issues) == 1
    assert "T3与T5合计不一致: T3=100.00, T5=90.00 (差额=10.00)" in issues[0].message

    reasons = exc_info.value.unresolved_reasons
    assert any("T3与T5项目支出数值缺失" in r for r in reasons)
    assert not any("T3与T5合计数值缺失" in r for r in reasons)
    assert not any("T3与T5基本支出数值缺失" in r for r in reasons)


# ===========================================================================
# R6 REGRESSION TESTS (P1-1, P1-2)
# ===========================================================================

def test_remediation_r6_pure_year_header_blank_deferred():
    """R6-P1-1: 纯年份表头（2025年预算数/2024年决算数）本年空白时，BUD-101不借用上年数，正确报RuleDeferred。"""
    headers = ["项目", "2025年预算数", "2024年决算数", "项目", "2025年预算数", "2024年决算数"]
    row_blank = ["收入总计", "", "100.00", "支出总计", "100.00", "100.00"]
    doc = build_document(
        "r6_bud101_pure_year_blank.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_blank]]],
        100,
    )
    rule = BUD101_T1Balance()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)
    assert any("收入总计" in r for r in exc_info.value.unresolved_reasons)
    assert exc_info.value.partial_issues == []


def test_remediation_r6_pure_year_header_complete_normal():
    """R6-P1-1: 纯年份表头双年度完整有值时，BUD-101准确提取本年列比较并正常通过。"""
    headers = ["项目", "2025年预算数", "2024年决算数", "项目", "2025年预算数", "2024年决算数"]
    row_comp = ["收入总计", "100.00", "90.00", "支出总计", "100.00", "95.00"]
    doc = build_document(
        "r6_bud101_pure_year_comp.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_comp]]],
        100,
    )
    rule = BUD101_T1Balance()
    assert rule.apply(doc) == []


def test_remediation_r6_bud105_t4_pure_year_blank_retains_unresolved():
    """R6-P1-1: T4采用纯年份表头且本年空白时，BUD-105不借用上年数值伪造平衡，unresolved包含T1与T4收入总计缺失。"""
    t1_headers = ["项目", "预算数", "项目", "预算数"]
    t1_row = ["收入总计", "100.00", "支出总计", "100.00"]
    t4_headers = ["项目", "2025年预算数", "2024年决算数", "项目", "2025年预算数", "2024年决算数"]
    t4_row = ["收入总计", "", "100.00", "支出总计", "100.00", "100.00"]

    doc = build_document(
        "r6_bud105_t4_pure_year_blank.pdf",
        [
            "部门财务收支预算总表\n单位：万元",
            "部门财政拨款收支预算总表\n单位：万元",
        ],
        [[[t1_headers, t1_row]], [[t4_headers, t4_row]]],
        100,
    )
    rule = BUD105_CrossTableChecks()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)
    reasons = exc_info.value.unresolved_reasons
    assert any("T1与T4收入总计数值缺失" in r for r in reasons)


def test_remediation_r6_swapped_columns_complete_and_blank():
    """R6-P1-1: 列组换序布局（上年在左、本年在右），完整时正常通过，空白时正确报RuleDeferred。"""
    headers = ["项目", "上年预算数", "本年预算数", "项目", "上年预算数", "本年预算数"]
    rule = BUD101_T1Balance()

    row_comp = ["收入总计", "90.00", "100.00", "支出总计", "95.00", "100.00"]
    doc_comp = build_document(
        "r6_bud101_swapped_comp.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_comp]]],
        100,
    )
    assert rule.apply(doc_comp) == []

    row_blank = ["收入总计", "100.00", "", "支出总计", "95.00", "100.00"]
    doc_blank = build_document(
        "r6_bud101_swapped_blank.pdf",
        ["部门财务收支预算总表\n单位：万元"],
        [[[headers, row_blank]]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc_blank)
    assert any("收入总计" in r for r in exc_info.value.unresolved_reasons)


def test_remediation_r6_bud105_missing_basic_column():
    """R6-P1-2: BUD-105在T3缺失基本支出列时，仍正确保留合计差10与项目差10两条finding，unresolved只含基本支出缺失。"""
    t3 = [
        ["科目名称", "合计", "项目支出"],
        ["合计", "100.00", "40.00"],
    ]
    t5 = [
        ["科目名称", "合计", "基本支出", "项目支出"],
        ["合计", "90.00", "60.00", "30.00"],
    ]
    doc = build_document(
        "r6_bud105_missing_basic.pdf",
        [
            "支出预算总表\n单位：万元",
            "一般公共预算支出功能分类预算表\n单位：万元",
        ],
        [[t3], [t5]],
        100,
    )
    rule = BUD105_CrossTableChecks()
    with pytest.raises(RuleDeferred) as exc_info:
        rule.apply(doc)

    issues = exc_info.value.partial_issues
    assert len(issues) == 2
    assert any("T3与T5合计不一致: T3=100.00, T5=90.00 (差额=10.00)" in i.message for i in issues)
    assert any("T3与T5项目支出不一致: T3=40.00, T5=30.00 (差额=10.00)" in i.message for i in issues)

    reasons = exc_info.value.unresolved_reasons
    assert any("T3与T5基本支出数值缺失" in r for r in reasons)
    assert not any("T3与T5合计数值缺失" in r for r in reasons)
    assert not any("T3与T5项目支出数值缺失" in r for r in reasons)






# ===========================================================================
# R8 REGRESSION TESTS (数据金额污染期间识别)
# ===========================================================================

@pytest.mark.parametrize("prior_amount", ["1000.00", "2024.00", "2050.00", "20250.00", "12024.00"])
def test_remediation_r8_amount_not_pollute_year_detection(prior_amount):
    """R8-P1: 数据行金额（含四位年份片段）不得参与年度推断；本年100≠90的差异必须保持。"""
    headers = ["项目", "2025年预算数", "2024年预算数", "项目", "2025年预算数", "2024年预算数"]
    row = ["收入总计", "100.00", prior_amount, "支出总计", "90.00", prior_amount]
    doc = build_document(
        f"r8_bud101_pollution_{prior_amount}.pdf",
        ["2025年部门财务收支预算总表\n单位：万元"],
        [[[headers, row]]],
        100,
    )
    issues = BUD101_T1Balance().apply(doc)
    assert len(issues) == 1
    assert "差额=10.00" in issues[0].message


@pytest.mark.parametrize("prior_amount", ["2050.00", "20250.00"])
def test_remediation_r8_t4_consumer_amount_not_pollute_year(prior_amount):
    """R8-P1: T4消费者路径不受数据金额污染，取本年列时T1↔T4一致、无差异finding。"""
    t1_headers = ["项目", "预算数", "项目", "预算数"]
    t1_row = ["收入总计", "100.00", "支出总计", "90.00"]
    t4_headers = ["项目", "2025年预算数", "2024年预算数", "项目", "2025年预算数", "2024年预算数"]
    t4_row = ["收入总计", "100.00", prior_amount, "支出总计", "90.00", prior_amount]
    doc = build_document(
        f"r8_bud105_t4_pollution_{prior_amount}.pdf",
        ["部门财务收支预算总表\n单位：万元", "部门财政拨款收支预算总表\n单位：万元"],
        [[[t1_headers, t1_row]], [[t4_headers, t4_row]]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        BUD105_CrossTableChecks().apply(doc)
    # T3/T5/T8缺失导致deferred，但T1↔T4取本年列一致：无差异finding、无T1↔T4缺失原因
    assert exc_info.value.partial_issues == []
    reasons = exc_info.value.unresolved_reasons
    assert not any("T1与T4" in r for r in reasons)


def test_remediation_r8_t4_consumer_mismatch_uses_current_column():
    """R8-P1: T4本年95、上年列为2050.00时，必须报本年口径差额5（借上年则差1950）。"""
    t1_headers = ["项目", "预算数", "项目", "预算数"]
    t1_row = ["收入总计", "100.00", "支出总计", "90.00"]
    t4_headers = ["项目", "2025年预算数", "2024年预算数", "项目", "2025年预算数", "2024年预算数"]
    t4_row = ["收入总计", "95.00", "2050.00", "支出总计", "90.00", "2050.00"]
    doc = build_document(
        "r8_bud105_t4_current_col.pdf",
        ["部门财务收支预算总表\n单位：万元", "部门财政拨款收支预算总表\n单位：万元"],
        [[[t1_headers, t1_row]], [[t4_headers, t4_row]]],
        100,
    )
    with pytest.raises(RuleDeferred) as exc_info:
        BUD105_CrossTableChecks().apply(doc)
    msgs = [i.message for i in exc_info.value.partial_issues]
    assert any("T1与T4收入总计不一致: T1=100.00, T4=95.00 (差额=5.00)" in m for m in msgs)


# ===========================================================================
# R9 REGRESSION TESTS (无“年”字表头期间歧义：fallback 不得猜列通过)
# ===========================================================================

def _r9_doc(name, t1_table, t4_table=None):
    texts = ["2025年部门财务收支预算总表\n单位：万元"]
    tables = [[t1_table]]
    if t4_table is not None:
        texts.append("部门财政拨款收支预算总表\n单位：万元")
        tables.append([t4_table])
    return build_document(name, texts, tables, 100)


# 无年字表头两种列序：上年在左 ["2024 预算数","2025 预算数"]；上年在右 ["2025 预算数","2024 预算数"]
R9_HDR_LEFT_PRIOR = ["项目", "2024 预算数", "2025 预算数", "项目", "2024 预算数", "2025 预算数"]
R9_HDR_RIGHT_PRIOR = ["项目", "2025 预算数", "2024 预算数", "项目", "2025 预算数", "2024 预算数"]


@pytest.mark.parametrize(
    "headers,row,expected_msg",
    [
        # 上年在左：本年正常 -> 通过；本年错误 -> 差额10；本年为0 -> 差额90（取本年0而非上年95）
        (R9_HDR_LEFT_PRIOR, ["收入总计", "95.00", "100.00", "支出总计", "90.00", "100.00"], None),
        (R9_HDR_LEFT_PRIOR, ["收入总计", "95.00", "100.00", "支出总计", "100.00", "90.00"], "差额=10.00"),
        (R9_HDR_LEFT_PRIOR, ["收入总计", "95.00", "0.00", "支出总计", "100.00", "90.00"], "收入=0.00, 支出=90.00"),
        # 上年在右：同三态
        (R9_HDR_RIGHT_PRIOR, ["收入总计", "100.00", "95.00", "支出总计", "100.00", "90.00"], None),
        (R9_HDR_RIGHT_PRIOR, ["收入总计", "100.00", "95.00", "支出总计", "90.00", "100.00"], "差额=10.00"),
        (R9_HDR_RIGHT_PRIOR, ["收入总计", "0.00", "95.00", "支出总计", "90.00", "100.00"], "收入=0.00, 支出=90.00"),
    ],
)
def test_remediation_r9_no_nian_header_period_binding(headers, row, expected_msg):
    """R9-P1: 无“年”字但含期间词（2025 预算数）的表头必须可靠识别期间，按本年列核验。"""
    doc = _r9_doc("r9_no_nian.pdf", [headers, row])
    issues = BUD101_T1Balance().apply(doc)
    if expected_msg is None:
        assert issues == []
    else:
        assert len(issues) == 1
        assert expected_msg in issues[0].message


@pytest.mark.parametrize(
    "headers,row",
    [
        # 本年收入空白：两种列序都必须 deferred，不得取上年数假平衡
        (R9_HDR_LEFT_PRIOR, ["收入总计", "95.00", "", "支出总计", "100.00", "90.00"]),
        (R9_HDR_RIGHT_PRIOR, ["收入总计", "", "95.00", "支出总计", "90.00", "100.00"]),
    ],
)
def test_remediation_r9_no_nian_header_blank_current_deferred(headers, row):
    """R9-P1: 无“年”字表头本年空白时必须报缺输入，不得借上年列通过。"""
    doc = _r9_doc("r9_no_nian_blank.pdf", [headers, row])
    with pytest.raises(RuleDeferred) as exc_info:
        BUD101_T1Balance().apply(doc)
    assert any("收入总计" in r for r in exc_info.value.unresolved_reasons)
    assert exc_info.value.partial_issues == []


def test_remediation_r9_ambiguous_multi_value_columns_deferred():
    """R9-P1: 完全无期间信息（重名“预算数”列）且候选含多个数值列时，必须 deferred 而非猜首列。"""
    headers = ["项目", "预算数", "预算数", "项目", "预算数", "预算数"]
    row = ["收入总计", "100.00", "100.00", "支出总计", "100.00", "90.00"]
    doc = _r9_doc("r9_ambiguous.pdf", [headers, row])
    with pytest.raises(RuleDeferred) as exc_info:
        BUD101_T1Balance().apply(doc)
    assert any("支出总计" in r for r in exc_info.value.unresolved_reasons)


def test_remediation_r9_t4_consumer_no_nian_header():
    """R9-P1: T4 无“年”字表头（上年在左）时，消费者必须按本年列比对：一致无finding、差异报本年口径、空白记缺失。"""
    t1 = [["项目", "预算数", "项目", "预算数"], ["收入总计", "100.00", "支出总计", "90.00"]]
    # 一致：T4 本年 100/90（上年 95/85）-> 无 T1↔T4 finding
    t4_ok = [R9_HDR_LEFT_PRIOR, ["收入总计", "95.00", "100.00", "支出总计", "85.00", "90.00"]]
    doc_ok = _r9_doc("r9_t4_ok.pdf", t1, t4_ok)
    with pytest.raises(RuleDeferred) as exc_ok:
        BUD105_CrossTableChecks().apply(doc_ok)
    assert exc_ok.value.partial_issues == []
    assert not any("T1与T4" in r for r in exc_ok.value.unresolved_reasons)
    # 差异：T4 本年收入 95 -> 报差额 5（取本年列；若借上年95则与T1假平衡）
    t4_bad = [R9_HDR_LEFT_PRIOR, ["收入总计", "96.00", "95.00", "支出总计", "85.00", "90.00"]]
    doc_bad = _r9_doc("r9_t4_bad.pdf", t1, t4_bad)
    with pytest.raises(RuleDeferred) as exc_bad:
        BUD105_CrossTableChecks().apply(doc_bad)
    msgs = [i.message for i in exc_bad.value.partial_issues]
    assert any("T1与T4收入总计不一致: T1=100.00, T4=95.00 (差额=5.00)" in m for m in msgs)
    # 空白：T4 本年收入空白 -> unresolved 记收入总计缺失（上年96不得借用）
    t4_blank = [R9_HDR_LEFT_PRIOR, ["收入总计", "96.00", "", "支出总计", "85.00", "90.00"]]
    doc_blank = _r9_doc("r9_t4_blank.pdf", t1, t4_blank)
    with pytest.raises(RuleDeferred) as exc_blank:
        BUD105_CrossTableChecks().apply(doc_blank)
    assert any("T1与T4收入总计数值缺失" in r for r in exc_blank.value.unresolved_reasons)


# ===========================================================================
# R10 REGRESSION TESTS (列身份必须由结构证明，不得由单元格是否有值决定)
# ===========================================================================

R10_AMBIGUOUS_HEADERS = [
    pytest.param(
        ["项目", "2024", "2025", "项目", "2024", "2025"],
        id="bare-year-columns",
    ),
    pytest.param(
        ["项目", "本年预算数", "本年预算数", "项目", "本年预算数", "本年预算数"],
        id="duplicate-current-columns",
    ),
]

R10_VALUE_MATRIX = [
    pytest.param(("100.00", "110.00"), ("100.00", "90.00"), id="both-values"),
    pytest.param(("", "110.00"), ("", "90.00"), id="left-blank"),
    pytest.param(("100.00", ""), ("100.00", ""), id="right-blank"),
    pytest.param(("", ""), ("", ""), id="all-blank"),
]


@pytest.mark.parametrize("headers", R10_AMBIGUOUS_HEADERS)
@pytest.mark.parametrize("income_values,expense_values", R10_VALUE_MATRIX)
def test_remediation_r10_bud101_ambiguous_column_matrix_deferred(
    headers, income_values, expense_values
):
    """BUD-101：歧义列无论哪些单元格有值都必须 deferred，不能由值分布反推列身份。"""
    row = ["收入总计", *income_values, "支出总计", *expense_values]
    doc = _r9_doc("r10_bud101_ambiguous_matrix.pdf", [headers, row])

    with pytest.raises(RuleDeferred) as exc_info:
        BUD101_T1Balance().apply(doc)

    reasons = exc_info.value.unresolved_reasons
    assert any("收入总计" in reason for reason in reasons)
    assert any("支出总计" in reason for reason in reasons)
    assert exc_info.value.partial_issues == []


@pytest.mark.parametrize("headers", R10_AMBIGUOUS_HEADERS)
@pytest.mark.parametrize("income_values,expense_values", R10_VALUE_MATRIX)
def test_remediation_r10_bud105_t4_ambiguous_column_matrix_deferred(
    headers, income_values, expense_values
):
    """BUD-105：T4 消费同一列定位契约，歧义时保留未完成状态且不产生伪 finding。"""
    t1 = [
        ["项目", "预算数", "项目", "预算数"],
        ["收入总计", "100.00", "支出总计", "100.00"],
    ]
    t4_row = ["收入总计", *income_values, "支出总计", *expense_values]
    doc = _r9_doc("r10_bud105_t4_ambiguous_matrix.pdf", t1, [headers, t4_row])

    with pytest.raises(RuleDeferred) as exc_info:
        BUD105_CrossTableChecks().apply(doc)

    reasons = exc_info.value.unresolved_reasons
    assert any("T1与T4收入总计数值缺失" in reason for reason in reasons)
    assert any("T1与T4支出总计数值缺失" in reason for reason in reasons)
    assert exc_info.value.partial_issues == []
