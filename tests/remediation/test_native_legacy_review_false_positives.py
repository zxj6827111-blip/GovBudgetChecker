"""原件重放暴露的旧规则误报：零值不是空表，同比减数不是本年总额。"""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred, RuleNotApplicable
from src.engine.rules_v33 import R33102_TotalSheet_vs_Text, R33230_EmptyZeroHint
from src.schemas.parse_artifact import EvidenceRef, FiscalFact


def total_document(body, *, value='8101.41', current='8000.00', native=True):
    pages = [
        '甲局2025年度部门决算\n收入支出决算总表\n单位：万元\n总计 8101.41',
        '一、收入支出决算总体情况说明\n' + body + '\n二、收入决算情况说明\n',
        '四、财政拨款收入支出决算总体情况说明\n财政拨款收入支出总计999.00万元。',
    ]
    rows = [
        ['收入项目', '决算数', '支出项目', '决算数'],
        ['本年收入合计', current, '本年支出合计', current],
        ['总计', value, '总计', value],
    ]
    doc = build_document('甲局2025年度决算.pdf', pages, [[rows], [], []], 0)
    if not native:
        return doc
    facts = []
    for i, (name, raw) in enumerate([('总计', value), ('本年支出合计', current)]):
        ref = EvidenceRef(file_hash='a'*64, physical_page=1, source_cell_id=f'cell-{i}',
                          quote=raw, location_level='exact_cell').to_dict()
        facts.append(FiscalFact(fact_uid=f'fact-{i}', indicator='expenditure_actual',
            raw_value=raw, unit='万元', normalized_value=Decimal(raw), org_name='甲局',
            fiscal_year=2025, period='本年', budget_stage='决算', subject_code='total',
            subject_name=name, table_code='FIN_01_income_expenditure_total', instance_uid='table',
            extra_dims={'evidence_refs': [ref]}))
    doc.fact_context = FactContext(facts, 'a'*64, 'run', org_name='甲局', fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash='a'*64, parse_run_uid='run')
    return doc


@pytest.mark.parametrize('native', [False, True])
def test_bare_total_reads_literal_amount_and_does_not_borrow_yoy_decrease(native):
    body = ('甲局2025年度收入支出总8101.41万元。与2024年度相比，'
            '收入支出总计减少840.12万元，下降9.40%。')
    doc = total_document(body, native=native)
    assert R33102_TotalSheet_vs_Text().apply(doc) == []
    assert '收入支出总计8101.41万元' not in doc.page_texts[1], '不在原件中补字'
    if native:
        receipt, = doc.fact_receipts
        assert receipt['status'] == 'pass'
        assert any(ref['span'] == '收入支出总8101.41万元' for ref in receipt['disclosure_sources'])


@pytest.mark.parametrize('body', [
    '与2024年度相比，收入支出总计减少840.12万元，下降9.40%。',
    '甲局2025年度收入支出总计。减少840.12万元，下降9.40%。',
    '甲局2025年度收入支出总计8101.41。',
    '甲局2024年度收入支出总计8101.41万元。',
    '甲局2025年度预算收入支出总计8101.41万元。',
    '如果甲局2025年度收入支出总计8101.41万元。',
    '“甲局2025年度收入支出总计8101.41万元”。',
    '“甲局2025年度收入支出总计8101.41万元。”',
    '“2025年度说明。甲局2025年度收入支出总计8101.41万元。”',
    '以下为模板示例。甲局2025年度收入支出总计8101.41万元。',
    '甲局2025年度收入支出总计8101.41万元。收入支出总计8200.00万元。',
])
def test_missing_or_noncurrent_total_is_unfinished_not_a_pass(body):
    with pytest.raises(RuleDeferred):
        R33102_TotalSheet_vs_Text().apply(total_document(body))


def test_foreign_owner_total_is_not_current_operand():
    with pytest.raises(RuleDeferred):
        R33102_TotalSheet_vs_Text().apply(total_document('乙局2025年度收入支出总计8101.41万元。'))


def test_real_total_difference_keeps_original_page_quote_and_fact():
    doc = total_document('甲局2025年度收入支出总计8102.41万元。')
    issue, = R33102_TotalSheet_vs_Text().apply(doc)
    assert '8102.41' in issue.message and '8101.41' in issue.message
    assert issue.location['page'] == 2 and issue.location['pages'] == [1, 2]
    assert issue.location['table_refs'][0]['span'] == '收入支出总计8102.41万元'
    assert issue.location['operand_refs'][0]['fact_uid'] == 'fact-0'
    assert issue.fact_ids == ['fact-0']


def test_total_and_current_expenditure_are_distinct_denominators():
    doc = total_document('甲局2025年度收入支出总计8101.41万元。', current='8000.00')
    assert R33102_TotalSheet_vs_Text().apply(doc) == []
    assert doc.fact_receipts[0]['status'] == 'pass'


@pytest.mark.parametrize('conflict', ['year', 'owner', 'version', 'duplicate', 'risk'])
def test_current_table_identity_is_still_strict(conflict):
    doc = total_document('甲局2025年度收入支出总计8101.41万元。')
    fact = doc.fact_context.facts[0]
    if conflict == 'year': fact.fiscal_year = 2024
    elif conflict == 'owner': fact.org_name = '乙局'
    elif conflict == 'version': doc.parse_artifact.parse_run_uid = 'old'
    elif conflict == 'risk': fact.recognition_status = 'ambiguous'
    else:
        other = deepcopy(fact)
        other.fact_uid = 'duplicate'
        doc.fact_context.facts.append(other)
    with pytest.raises(RuleDeferred):
        R33102_TotalSheet_vs_Text().apply(doc)


def test_explicit_amount_unit_is_normalized_before_comparison():
    doc = total_document('甲局2025年度收入支出总计81014100元。')
    assert R33102_TotalSheet_vs_Text().apply(doc) == []


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('amount', ['0.00', '-1.00'])
def test_printed_zero_or_negative_amount_is_never_reported_as_empty(native, amount):
    rows = [['预算数']*6 + ['决算数']*6, [amount]*12]
    doc = build_document('甲局2025年度决算.pdf',
        ['财政拨款“三公”经费支出决算表\n预算数 决算数\n' + ' '.join([amount]*12),
         '七、财政拨款“三公”经费支出决算情况说明\n本年三公经费支出为0万元。'],
        [[rows], []], 0)
    if native: doc.parse_artifact = SimpleNamespace()
    with pytest.raises(RuleNotApplicable, match='空格补0建议不适用'):
        R33230_EmptyZeroHint().apply(doc)


@pytest.mark.parametrize('statistics,ai_degraded,rule_reason', [
    ({'incomplete':1,'rule_warning_count':0,'degraded_count':1},1,False),
    ({'incomplete':2,'rule_warning_count':1,'degraded_count':1},1,True),
    ({'incomplete':1},0,True),
    ({'incomplete':1,'degraded_count':1},1,False),
    ({'incomplete':1,'rule_warning_count':0,'degraded_count':1},0,True),
])
def test_ai_evidence_gap_is_not_duplicated_as_rule_gap(statistics, ai_degraded, rule_reason):
    from api.main import _evaluate_quality_gate
    from tests.test_audit_quality_p0 import _base_gate_kwargs

    gate = _evaluate_quality_gate(**_base_gate_kwargs(
        evidence_completeness=statistics, evidence_degraded_count=ai_degraded))
    codes = [r['code'] for r in gate['review_reasons']]
    assert gate['status'] == 'review_required', '消除重复提示不能清除真实证据缺口'
    assert ('rule_evidence_incomplete' in codes) is rule_reason
    assert ('evidence_incomplete_findings' in codes) is bool(ai_degraded)
