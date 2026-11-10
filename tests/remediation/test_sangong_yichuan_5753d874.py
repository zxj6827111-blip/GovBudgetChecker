"""宜川任务 5753d874… 暴露的三公审校缺口与反例（2026-11-10 裁定）。

验收点：
1. 认证空白格按既定口径（空白=0）参与金额勾稽；说明中的完成率百分比
   不再连带阻断金额加总、分项齐备与表文一致检查；
2. 正数披露与认证空白表的矛盾仍须阻断，且诊断带具体位置（不再
   rule_no_reason）；
3. 经济分类表无关科目重复（如 31099）不整表阻断：排版重复按一行参与
   合计并单独报告，内容不同的重复只阻断涉及科目；三公跨表核对按
   目标科目要求严格唯一原行；空白未认证不得推零；
4. 重复表文义务 OBL-TXT-T7 与 OBL-SG-TABLE-TEXT 合并为一条待办，
   关联检查项（V33-108/V33-224/CMM-001）完整保留。
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.check_obligations import (
    OBLIGATION_CATALOG_VERSION,
    build_obligation_ledger,
)
from src.engine.diagnostic_fields import diagnostics_for_texts
from src.engine.native_no_activity import require_no_activity_disposition
from src.engine.native_table_arithmetic import (
    _economic_operand,
    _economic_table_view,
    check_three_public_arithmetic,
)
from src.engine.rule_outcome import RuleDeferred, RuleNotApplicable
from src.engine.rules_v33 import (
    R33117_BasicExpenseClassification,
    R33243_Table6_BasicExpenseAdvancedCheck,
)
from tests.remediation.test_putuo_code_repair import empty_three_public
from tests.remediation.test_shiquan_native_empty_rows import grid_document

T6 = 'FIN_06_basic_expenditure'


def economic_rows(right_lane_tail):
    """两车道经济分类网格：左车道固定，右车道按需追加重复科目行。"""
    rows = [
        ['经济分类科目编码', '科目名称', '决算数', '经济分类科目编码', '科目名称', '决算数'],
        ['301', '工资福利支出', '100.00', '302', '商品和服务支出', '30.00'],
        ['30101', '基本工资', '60.00', '30201', '办公费', '10.00'],
        ['30102', '津贴补贴', '40.00', '30202', '印刷费', '20.00'],
        ['303', '对个人和家庭的补助', '20.00', '310', '资本性支出', '0.00'],
        ['30301', '离休费', '20.00', '31001', '房屋建筑物购建', '0.00'],
        *right_lane_tail,
        ['人员经费合计', '', '120.00', '公用经费合计', '', '30.00'],
    ]
    return rows


def economic_view(right_lane_tail):
    doc = grid_document(economic_rows(right_lane_tail))
    facts = [f for f in doc.fact_context.facts if f.table_code == T6]
    view, error = _economic_table_view(doc, facts)
    assert error is None, error
    return doc, view, facts


# ---- 需求1：百分比不阻断；空白=0 口径保留 ----


def test_percent_only_narrative_no_longer_blocks_empty_three_public():
    doc = empty_three_public()
    doc.page_texts[0] += (
        "\n七、财政拨款“三公”经费支出决算情况说明\n"
        "“三公”经费财政拨款支出年初预算为0万元，支出决算为0万元，"
        "完成预算的100%。"
    )
    with pytest.raises(RuleNotApplicable):
        require_no_activity_disposition(SimpleNamespace(code="test"), doc)
    rec = doc.fact_receipts[-1]
    assert rec["status"] == "not_applicable"
    # 十二格认证空白照旧随回执留证（分项齐备+空白=0 的证据面）。
    assert len(rec["cell_observations"]) == 12
    # 完成率百分比交由专项规则独立核对，回执原因须写明这一移交。
    assert "完成率" in rec["incomplete_reason"]


def test_clean_and_percent_cases_share_same_blank_zero_receipt_basis():
    """有无完成率百分比不改变十二格空白=0 的认证回执形态。"""
    clean = empty_three_public()
    with pytest.raises(RuleNotApplicable):
        require_no_activity_disposition(SimpleNamespace(code="test"), clean)
    base = clean.fact_receipts[-1]["cell_observations"]
    percent = empty_three_public()
    percent.page_texts[0] += (
        "\n七、财政拨款“三公”经费支出决算情况说明\n支出决算为0万元，完成预算的100%。"
    )
    with pytest.raises(RuleNotApplicable):
        require_no_activity_disposition(SimpleNamespace(code="test"), percent)
    assert percent.fact_receipts[-1]["cell_observations"] == base


# ---- 需求1/4 反例：正数披露仍阻断且诊断落到具体位置 ----


def test_positive_narrative_still_defers_with_structured_diagnostics():
    doc = empty_three_public()
    doc.page_texts[0] += (
        "\n七、财政拨款“三公”经费支出决算情况说明\n"
        "“三公”经费支出决算为3.20万元。"
    )
    with pytest.raises(RuleDeferred) as exc_info:
        require_no_activity_disposition(SimpleNamespace(code="test"), doc)
    detail = str(exc_info.value.detail)
    assert "正数" in detail
    diags = diagnostics_for_texts(doc, [detail], rule_id="test")
    assert diags, "正数冲突 defer 必须带结构化诊断，不得落入 rule_no_reason"
    assert all("rule_no_reason" not in entry["reason_codes"] for entry in diags)
    assert any("text_binding_failed" in entry["reason_codes"] for entry in diags)
    operands = [operand for entry in diags for operand in entry["missing_operands"]]
    assert operands and all(operand.get("source") == "text" for operand in operands)
    assert any("三公" in str(operand.get("section")) for operand in operands)
    assert all(operand.get("page") for operand in operands), "正数位置必须带页码"


# ---- 需求5 反例：空白未认证不得推零 ----


def test_uncertified_blank_never_becomes_zero_in_arithmetic():
    doc = empty_three_public()
    # 十二格之一失去原生空白认证：无支出说明不能替它背书。
    doc.parse_artifact.table_fragments[0].cells[-1].native_blank_verified = False
    assert require_no_activity_disposition(SimpleNamespace(code="test"), doc) is None
    with pytest.raises(RuleDeferred) as exc_info:
        check_three_public_arithmetic(SimpleNamespace(code="V33-244"), doc)
    # 未认证空白不得推零：表内勾稽按取数不足阻断，而不是 0=0+0 的假通过。
    assert "没有可用金额事实" in str(exc_info.value.detail)
    assert not any("three_public:" in str(rec.get("sub_check_id")) and rec["status"] == "pass"
                   for rec in doc.fact_receipts)


# ---- 需求3：31099 排版重复单独报告，按一行参与合计 ----


def test_identical_duplicate_unrelated_subject_counts_once_and_reports_low():
    doc, view, facts = economic_view([
        ['', '', '', '31002', '办公设备购置', '0.00'],
        ['', '', '', '31099', '其他资本性支出', '0.00'],
        ['', '', '', '31099', '其他资本性支出', '0.00'],
    ])
    assert set(view[6]) == {'31099'} and view[6]['31099']['identical'] is True
    assert [entry[0] for entry in view[4]['31099']] == [7, 8]
    # 严格身份取数（跨表口径）：目标外科目的排版重复不豁免唯一性要求。
    term, error = _economic_operand(doc, view, '31099')
    assert term is None and '重复原行身份' in error
    # 加总口径：排版重复按一行参与合计，310 类合计勾稽照常完成。
    term, error = _economic_operand(doc, view, '31099', allow_identical_duplicate=True)
    assert error is None and term.normalized_value == Decimal('0.00')
    issues = R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    # 表级唯一一次单独报告；金额勾稽不再被全表阻断。
    assert [issue.severity for issue in issues] == ['low']
    assert '31099 其他资本性支出' in issues[0].message
    assert '按一行参与合计' in issues[0].message
    assert issues[0].location['table_code'] == T6
    assert issues[0].location['page'] == 1
    assert R33117_BasicExpenseClassification().apply(doc) == []
    assert any(rec.get('sub_check_id') == 'economic:class:310' and rec['status'] == 'pass'
               for rec in doc.fact_receipts)


# ---- 需求3 反例：目标科目自身重复仍阻断（跨表按目标科目验证唯一身份）----


def test_target_subject_duplicate_blocks_strict_identity_but_not_unrelated_sums():
    doc, view, _facts = economic_view([
        ['', '', '', '31002', '办公设备购置', '0.00'],
        ['', '', '', '30217', '公务接待费', '0.00'],
        ['', '', '', '30217', '公务接待费', '0.00'],
    ])
    assert set(view[6]) == {'30217'} and view[6]['30217']['identical'] is True
    term, error = _economic_operand(doc, view, '30217')
    assert term is None and '重复原行身份' in error
    # 302 类内其余科目不受影响；类合计（含按一行计数的 30217）照常勾稽。
    issues = R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert [issue.severity for issue in issues] == ['low']
    assert any(rec.get('sub_check_id') == 'economic:class:302' and rec['status'] == 'pass'
               for rec in doc.fact_receipts)


# ---- 需求3 反例：内容不同的重复只阻断涉事科目，不成见全表 ----


def test_non_identical_duplicate_blocks_only_its_own_class_sum():
    doc, view, _facts = economic_view([
        ['', '', '', '31002', '办公设备购置', '0.00'],
        ['', '', '', '31099', '其他资本性支出', '0.00'],
        ['', '', '', '31099', '其他资本性支出', '5.00'],
    ])
    assert set(view[6]) == {'31099'} and view[6]['31099']['identical'] is False
    term, error = _economic_operand(doc, view, '31099', allow_identical_duplicate=True)
    assert term is None and '重复原行身份' in error
    # 310 类合计因涉事科目身份歧义而取数不足；302 类合计不受牵连。
    with pytest.raises(RuleDeferred) as exc_info:
        R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert '31099' in str(exc_info.value.detail)
    partial = list(exc_info.value.partial_issues)
    assert [issue.severity for issue in partial] == ['medium']
    assert '金额不一致' in partial[0].message
    assert any(rec.get('sub_check_id') == 'economic:class:310' and rec['status'] == 'insufficient_data'
               for rec in doc.fact_receipts)
    assert any(rec.get('sub_check_id') == 'economic:class:302' and rec['status'] == 'pass'
               for rec in doc.fact_receipts)
    # 人员/公用汇总（V33-117）不涉及 31099，照常完成。
    assert R33117_BasicExpenseClassification().apply(doc) == []


# ---- 需求4：重复表文义务合并，检查项与证据保留 ----


def test_duplicate_table_text_obligation_merged_with_checkers_kept():
    ledger = build_obligation_ledger(
        None, report_kind='final',
        rule_execution_summary={'rule_statuses': {
            'V33-108': 'pass', 'V33-224': 'pass', 'CMM-001': 'insufficient_data'}},
    )
    ids = [item['obligation_id'] for item in ledger['instances']]
    # OBL-TXT-T7 与 OBL-SG-TABLE-TEXT 是同一检查的重复登记：合并后只留一条。
    assert 'OBL-TXT-T7' not in ids
    assert 'OBL-SG-TABLE-TEXT' in ids
    merged = next(item for item in ledger['instances']
                  if item['obligation_id'] == 'OBL-SG-TABLE-TEXT')
    # 关联检查项完整保留在合并后的义务上。
    assert set(merged['checkers']) == {'V33-108', 'V33-224', 'CMM-001'}
    assert merged['reason'] == 'insufficient_data'
    assert OBLIGATION_CATALOG_VERSION == 'obligations-v13-checklist-global-20261110'
