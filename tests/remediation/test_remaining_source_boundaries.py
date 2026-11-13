from decimal import Decimal

import pytest

from src.engine.diagnostic_fields import fields_for_text
from src.engine.rules_v33 import R33104_Expense_vs_Text, R33117_BasicExpenseClassification
from src.engine.rule_outcome import RuleDeferred
from tests.remediation.test_shiquan_native_empty_rows import grid_document, economics
from tests.test_material_writing_rules import document, resolution
from src.services.template_scope import HumanDecision
from src.engine.checklist_identity_rules import CMM019_CoverElements


def expense(body):
    doc=grid_document([
        ['功能分类科目编码','科目名称','本年支出合计','基本支出','项目支出','上缴上级支出','经营支出','对附属单位补助支出'],
        ['合计','','10.00','','10.00','','',''],
        ['201','一般公共服务支出','10.00','','10.00','','','']], 'FIN_03_expenditure')
    doc.page_texts=['甲局2025年度部门决算\n支出决算表','三、支出决算情况说明\n'+body]
    return doc


def test_verified_blank_summary_is_zero_but_missing_text_stays_incomplete():
    doc=expense('本年支出合计10.00万元，其中：项目支出10.00万元。')
    with pytest.raises(RuleDeferred) as caught:R33104_Expense_vs_Text().apply(doc)
    assert '说明文本中未提取到基本支出' in caught.value.detail
    assert '缺少关键行: 基本支出' not in caught.value.detail
    r=next(r for r in doc.fact_receipts if r['sub_check_id']=='T3:basic_actual:text_vs_table')
    assert r['status']=='insufficient_data' and r['cell_observations']


def test_verified_blank_summary_and_explicit_text_are_really_compared():
    doc=expense('本年支出合计10.00万元，其中：基本支出0.00万元；项目支出10.00万元。')
    assert not R33104_Expense_vs_Text().apply(doc)
    r=next(r for r in doc.fact_receipts if r['sub_check_id']=='T3:basic_actual:text_vs_table')
    assert r['status']=='pass' and r['cell_observations']
    doc=expense('本年支出合计10.00万元，其中：基本支出1.00万元；项目支出10.00万元。')
    assert any('基本支出：表0.0' in i.message for i in R33104_Expense_vs_Text().apply(doc))


def test_summary_discrepancy_is_not_excused_by_special_point_zero_five_tolerance():
    doc=economics()
    fact=next(f for f in doc.fact_context.facts if f.subject_name=='人员经费合计')
    fact.normalized_value=Decimal('120.04')
    fact.raw_value='120.04'
    assert any(i.severity=='error' and '0.04' in i.message for i in R33117_BasicExpenseClassification().apply(doc))


def test_conflicting_body_names_are_material_identity_diagnostics_not_generic_technical_failure():
    pages=['上海市甲办公室2025年度单位决算',
           '第一部分上海市甲办公室概况\n第一部分上海市甲办公室分部概况',
           '上海市甲办公室2025年度单位决算表\n上海市甲办公室分部2025年度单位决算表']
    doc=document(pages,resolution(pages,HumanDecision('unit','test',True)))
    with pytest.raises(RuleDeferred) as caught:CMM019_CoverElements().apply(doc)
    reason='封面单位名称缺少正文独立依据'
    assert reason in caught.value.unresolved_reasons
    diag=fields_for_text(doc,reason,rule_id='CMM-019')
    assert diag['reason_codes']==['subject_conflict']
    assert diag['next_action']=='human_confirm'
