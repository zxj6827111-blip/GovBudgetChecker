from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33108_ThreePublic_vs_Text, R33226_Narrative2_T2,
    R33117_BasicExpenseClassification, R33243_Table6_BasicExpenseAdvancedCheck,
    R33244_Table7_ThreePublicAdvancedCheck, R33241_Table3_ExpenseAdvancedCheck,
    _TXT_FUND_ITEM_RE,
)
from src.schemas.parse_artifact import FiscalFact


def document(body, code, values):
    doc = build_document('native.pdf', ['甲局2025年度部门决算', body], [], 0)
    facts = []
    for index, (subject, name, indicator, stage, value) in enumerate(values):
        facts.append(FiscalFact(fact_uid=f'fact:{index}', indicator=indicator,
            raw_value=str(value), unit='万元', normalized_value=Decimal(str(value)),
            org_name='甲局', fiscal_year=2025, period='本年', budget_stage=stage,
            subject_code=subject, subject_name=name, table_code=code, instance_uid='table',
            extra_dims={'evidence_refs': [{'physical_page': 1}]}))
    doc.fact_context = FactContext(facts, 'a'*64, 'run', org_name='甲局', fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash='a'*64, parse_run_uid='run')
    return doc


def three_public(body, actual='4524.78'):
    return document('七、财政拨款“三公”经费支出决算情况说明\n' + body,
                    'FIN_07_three_public', [('total','合计','total_actual','年初预算','6937.99'),
                                           ('total','合计','total_actual','决算',actual)])


def test_budget_and_soft_wrapped_decision_are_bound_to_distinct_stages():
    doc = three_public('“三公”经费年初预算为6937.99万元，支\n出决算为4524.78万元。')
    assert R33108_ThreePublic_vs_Text().apply(doc) == []
    assert len(doc.fact_receipts) == 2
    assert all(r['status'] == 'pass' for r in doc.fact_receipts)


def test_real_decision_mismatch_keeps_business_stage_and_original_quote():
    doc = three_public('“三公”经费年初预算为6937.99万元，支\n出决算为4524.78万元。', actual='4520')
    issue, = R33108_ThreePublic_vs_Text().apply(doc)
    assert '4520.00' in issue.message
    assert issue.location['operand_refs'][0]['budget_stage'] == '决算'
    assert '支\n出决算为4524.78' in issue.evidence_text


def test_missing_decision_is_uncompleted_not_replaced_by_budget():
    with pytest.raises(RuleDeferred):
        R33108_ThreePublic_vs_Text().apply(three_public('“三公”经费年初预算为6937.99万元。'))


def test_part_amount_is_not_three_public_total():
    doc = three_public('“三公”经费支出决算为4524.78万元。公务接待费支出决算为410.77万元。')
    assert R33108_ThreePublic_vs_Text().apply(doc) == []
    assert len(doc.fact_receipts) == 1


def test_cross_page_decision_token_ignores_only_verified_physical_footer():
    doc = three_public('“三公”经费年初预算为6937.99万元，支出\n2')
    doc.page_texts.append('决算为4524.78万元。\n3')
    assert R33108_ThreePublic_vs_Text().apply(doc) == []
    assert len(doc.fact_receipts) == 2


def test_unmatched_terminal_number_is_not_silently_removed():
    doc = three_public('“三公”经费年初预算为6937.99万元，支出\n52')
    doc.page_texts.append('决算为4524.78万元。')
    with pytest.raises(RuleDeferred):
        R33108_ThreePublic_vs_Text().apply(doc)


def test_foreign_year_or_owner_claim_is_not_compared_to_current_total():
    for claim in ('2024年“三公”经费支出决算为4524.78万元。',
                  '乙局“三公”经费支出决算为4524.78万元。'):
        with pytest.raises(RuleDeferred):
            R33108_ThreePublic_vs_Text().apply(three_public(claim))


# 2026-10-22 口径决议①：容差 0.01 万元（含边界），0.50 级差额须报出。
@pytest.mark.parametrize('value, count', [('12686.73', 0), ('12686.72', 1), ('12686.24', 1)])
def test_income_decimal_tolerance_and_indicator_binding(value, count):
    doc = document(f'二、收入决算情况说明\n本年收入合计20000万元，其中经营收入{value}万元，其他收入7313.26万元。',
        'FIN_02_income', [('total','合计','total_actual','决算','20000'),
                          ('total','合计','operational_income','决算','12686.74'),
                          ('total','合计','other_income','决算','7313.26')])
    issues = R33226_Narrative2_T2().apply(doc)
    assert len(issues) == count
    if issues:
        assert issues[0].location['operand_refs'][0]['indicator'] == 'operational_income'


@pytest.mark.parametrize('conflict', ['owner','year','version','multiple','risk'])
def test_native_table_text_rejects_incompatible_or_ambiguous_current_facts(conflict):
    doc = three_public('“三公”经费支出决算为4524.78万元。')
    fact = doc.fact_context.facts[-1]
    if conflict == 'owner': fact.org_name = '乙局'
    elif conflict == 'year': fact.fiscal_year = 2024
    elif conflict == 'version': doc.parse_artifact.parse_run_uid = 'old'
    elif conflict == 'risk': fact.recognition_status = 'ambiguous'
    else:
        other = deepcopy(fact)
        other.fact_uid, other.instance_uid = 'other', 'other'
        doc.fact_context.facts.append(other)
    with pytest.raises(RuleDeferred):
        R33108_ThreePublic_vs_Text().apply(doc)


def economic(values):
    return document('', 'FIN_06_basic_expenditure',
        [(code, name, 'total_actual', '决算', amount) for code,name,amount in values])


def test_unidentified_amount_row_never_generates_a_partial_sum_error():
    doc = economic([('301','工资福利','100'),('303','补助','0'),('302','商品服务','830.60'),
        ('310','资本','0'),('30201','办公','539.25'),(None,'11\n30212 维修','291.36'),
        ('total','人员经费合计','100'),('total','公用经费合计','830.60')])
    with pytest.raises(RuleDeferred) as signal:
        R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert signal.value.partial_issues == []
    assert any('科目编码' in reason for reason in signal.value.unresolved_reasons)


def test_complete_economic_children_detect_real_gap_and_keep_all_operands():
    doc = economic([('301','工资福利','100'),('303','补助','0'),('302','商品服务','830.60'),
        ('310','资本','0'),('30201','办公','539.25'),('30212','维修','280'),
        ('total','人员经费合计','100'),('total','公用经费合计','830.60')])
    issue, = R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert issue.severity == 'error'
    assert len(issue.location['operand_refs']) == 3


def test_economic_rounding_hint_preserves_complete_children_and_summary():
    doc = economic([('301','工资福利','100'),('303','补助','0'),('302','商品服务','830.60'),
        ('310','资本','0'),('30201','办公','539.25'),('30212','维修','291.36'),
        ('total','人员经费合计','100'),('total','公用经费合计','830.60')])
    # V33-117 检查显式经费合计与类级金额；类级与明细由 V33-243 负责。
    issue, = R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert issue.severity == 'info' and len(issue.location['operand_refs']) == 3


def test_three_public_blank_component_is_not_assumed_zero():
    doc = three_public('')
    with pytest.raises(RuleDeferred) as signal:
        R33244_Table7_ThreePublicAdvancedCheck().apply(doc)
    assert signal.value.partial_issues == []
    assert all(r['status'] == 'insufficient_data' for r in doc.fact_receipts)


def test_expenditure_includes_all_explicit_extra_components():
    values = [('total','合计',key,'决算',value) for key,value in
              [('total_actual','100'),('basic_actual','50'),('project_actual','30'),
               ('to_superior_actual','0'),('operational_actual','20'),('subsidiary_actual','0')]]
    doc = document('', 'FIN_03_expenditure', values)
    assert R33241_Table3_ExpenseAdvancedCheck().apply(doc) == []
    doc.fact_context.facts[4].normalized_value = Decimal('10')
    issue, = R33241_Table3_ExpenseAdvancedCheck().apply(doc)
    assert len(issue.location['operand_refs']) == 6
    assert '90.00' in issue.message


def test_fund_item_after_purpose_uses_decision_not_zero_budget():
    body = '教育（类）超长期特别国债安排的支出（款）高等教育（项）。主要用于项目建设。年初预算0万元，支出决算为15920.47万元。'
    match = _TXT_FUND_ITEM_RE.search(body)
    assert match and match.group('amount') == '15920.47'
    assert not _TXT_FUND_ITEM_RE.search(body.replace('支出决算为', '项目金额'))


def test_fund_item_never_crosses_next_subject_to_borrow_decision():
    body = '教育（类）普通教育（款）高等教育（项）。年初预算0万元。\n2.科学技术（类）科技（款）研发（项）。支出决算为100万元。'
    matches = list(_TXT_FUND_ITEM_RE.finditer(body))
    assert len(matches) == 1 and matches[0].group('xiang') == '研发'
