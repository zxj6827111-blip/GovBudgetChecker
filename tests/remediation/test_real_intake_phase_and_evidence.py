"""预算阶段、跨页业务身份与财政总表证据的实质回归。"""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.check_obligations import attach_obligation_ids, build_check_receipts, build_obligation_ledger
from src.engine.fact_context import FactContext, make_fact_receipt
from src.engine.native_summary_checks import check_grant_summary, check_grant_general_vs_function_total
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33202_InterTable_T4_T5, R33242_Table4_ComprehensiveCheck, R33TrendCompletionRate
from src.schemas.parse_artifact import FiscalFact
from tests.remediation.test_native_three_public_ratios import document


def test_generic_budget_conflict_is_unfinished_not_a_false_amount_error():
    doc = document('公务用车购置及运行维护费年初预算100万元，支出决算43.34万元，完成年初预算的43.34%。')
    doc.fact_context.facts[0].extra_dims['source_header'] = '公务用车购置及运行维护费/预算数'
    with pytest.raises(RuleDeferred) as signal:
        R33TrendCompletionRate().apply(doc)
    assert not signal.value.partial_issues
    assert '预算阶段' in signal.value.detail
    assert doc.fact_receipts[0]['status'] == 'insufficient_data'


def test_generic_budget_is_usable_when_explicit_phase_and_amount_agree():
    doc = document('公务用车购置及运行维护费年初预算67.30万元，支出决算43.34万元，完成年初预算的64.40%。')
    doc.fact_context.facts[0].extra_dims['source_header'] = '公务用车购置及运行维护费/预算数'
    assert R33TrendCompletionRate().apply(doc) == []
    assert doc.fact_receipts[0]['status'] == 'pass'


def test_footer_split_reception_does_not_bind_the_preceding_vehicle():
    body = '公务用车购置及运行维护费支出决算43.34万元，完成预算的64.40%。公务接\n2'
    doc = document(body)
    doc.page_texts.append('待费支出决算25.52万元，完成年初预算的74.40%。')
    doc.pages = 3
    for stage, value in [('年初预算', '34.30'), ('决算', '25.52')]:
        f = deepcopy(doc.fact_context.facts[0])
        f.fact_uid = f'reception:{stage}'
        f.indicator = 'reception'
        f.budget_stage = stage
        f.normalized_value = Decimal(value)
        doc.fact_context.facts.append(f)
    assert R33TrendCompletionRate().apply(doc) == []
    assert len([r for r in doc.fact_receipts if r['status'] == 'pass']) == 2


def summary_doc():
    definitions = [('income_actual', '年初财政拨款结转和结余', '10'),
                   ('income_actual', '本年收入合计', '90'), ('income_actual', '总计', '100'),
                   ('expenditure_actual', '本年支出合计', '95'),
                   ('expenditure_actual', '年末财政拨款结转和结余', '5'),
                   ('expenditure_actual', '总计', '100')]
    for indicator in ('general_public_budget', 'government_fund_budget', 'state_capital_budget'):
        for name, value in [('本年支出合计', '95'), ('年末财政拨款结转和结余', '5'), ('总计', '100')]:
            definitions.append((indicator, name, value if indicator == 'general_public_budget' else '0'))
    facts = []
    for index, (indicator, name, value) in enumerate(definitions):
        facts.append(FiscalFact(fact_uid=f'f{index}', indicator=indicator, raw_value=value,
            unit='万元', normalized_value=Decimal(value), org_name='甲局', fiscal_year=2025,
            period='本年', budget_stage='决算', subject_name=name,
            table_code='FIN_04_fiscal_grant_total', instance_uid='t4',
            extra_dims={'evidence_refs': [{'physical_page': 2 if index < 3 else 3,
                         'quote': value, 'source_cell_id': f'cell-{index}'}]}))
    return SimpleNamespace(fact_context=FactContext(facts, 'a'*64, 'run', org_name='甲局', fiscal_year=2025),
                           parse_artifact=SimpleNamespace(source_file_hash='a'*64, parse_run_uid='run'))


def test_summary_uses_original_operands_and_preserves_both_physical_pages():
    doc = summary_doc()
    doc.fact_context.facts[1].normalized_value = Decimal('80')
    findings = check_grant_summary(R33242_Table4_ComprehensiveCheck(), doc)
    assert len(findings) == 2
    assert all(item.fact_ids and item.location['operand_refs'] for item in findings)
    assert any(item.location['pages'] == [2, 3] for item in findings)
    assert all(ref['span'] in ('5', '10', '90', '95', '100')
               for item in findings for ref in item.location['fact_source_refs'])
    assert '80' not in {ref['span'] for item in findings for ref in item.location['fact_source_refs']}


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'wrong_owner', 'wrong_version'])
def test_summary_unknown_operand_never_becomes_zero_or_pass(damage):
    doc = summary_doc()
    if damage == 'missing':
        doc.fact_context.facts.pop(0)
    elif damage == 'duplicate':
        doc.fact_context.facts.append(deepcopy(doc.fact_context.facts[0]))
    elif damage == 'wrong_owner':
        doc.fact_context.facts[0].org_name = '乙局'
    else:
        doc.parse_artifact.parse_run_uid = 'stale'
    with pytest.raises(RuleDeferred):
        check_grant_summary(R33242_Table4_ComprehensiveCheck(), doc)
    if damage != 'wrong_version':
        assert any(r['status'] == 'insufficient_data' for r in doc.fact_receipts)


def test_cross_table_difference_has_current_fact_ids_and_original_pages():
    doc = summary_doc()
    fact = deepcopy(doc.fact_context.facts[3])
    fact.fact_uid, fact.table_code, fact.instance_uid = 't5-total', 'FIN_05_general_public_expenditure', 't5'
    fact.indicator, fact.subject_name = 'total_actual', '合计'
    fact.normalized_value, fact.raw_value = Decimal('90'), '90'
    fact.extra_dims = {'evidence_refs': [{'physical_page': 4, 'quote': '90', 'source_cell_id': 't5-c'}]}
    doc.fact_context.facts.append(fact)
    item, = check_grant_general_vs_function_total(R33202_InterTable_T4_T5(), doc)
    assert item.location['pages'] == [3, 4]
    assert item.fact_ids == ['f6', 't5-total']
    assert item.location['difference_wan'] == '5'


def test_common_three_public_incomplete_affects_selected_final_obligations():
    summary = {'rule_statuses': {'V33-108': 'pass', 'V33-224': 'pass', 'CMM-001': 'insufficient_data'}}
    coverage = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summary)
    selected = [x for x in coverage['instances'] if x['obligation_id'] in ('OBL-TXT-T7', 'OBL-SG-TABLE-TEXT')]
    assert len(selected) == 2
    assert all(x['reason'] == 'insufficient_data' for x in selected)
    receipts = build_check_receipts({}, report_kind='final', rule_execution_summary=summary,
        obligation_coverage=coverage, fact_receipts=[make_fact_receipt('CMM-001', 'detail', status='insufficient_data', incomplete_reason='字段未识别')])
    relation = [r for r in receipts if r['sub_check_id'] == 'detail']
    assert {r['obligation_id'] for r in relation} == {'OBL-TXT-T7', 'OBL-SG-TABLE-TEXT'}
    assert all('BUDGET' not in r['obligation_id'] for r in relation)


def test_explicit_total_confirms_same_matrix_component_budget_phase():
    doc = document('“三公”经费支出年初预算100万元，公务用车购置及运行维护费支出决算43.34万元，完成预算的64.40%。')
    budget = doc.fact_context.facts[0]
    budget.extra_dims['source_header'] = '公务用车购置及运行维护费/预算数'
    total = deepcopy(budget)
    total.fact_uid, total.indicator = 'total-budget', 'total_actual'
    total.normalized_value = Decimal('100')
    total.extra_dims['source_header'] = '合计/预算数'
    doc.fact_context.facts.append(total)
    assert R33TrendCompletionRate().apply(doc) == []
    total.normalized_value = Decimal('90')
    with pytest.raises(RuleDeferred):
        R33TrendCompletionRate().apply(doc)


@pytest.mark.parametrize('nested', [False, True])
def test_merged_legacy_rule_receipt_links_to_retained_issue_and_own_obligation(nested):
    finding = {'id': 'retained', 'source': 'rule', 'rule_id': 'V33-234'}
    owner = finding.setdefault('location', {}) if nested else finding
    owner['related_rule_ids'] = ['V33-234', 'CMM-007']
    result = {'issues': [finding]}
    assert attach_obligation_ids(result) == 1
    assert set(finding['obligation_ids']) == {'OBL-NARRATIVE-AMOUNT', 'OBL-TREND-ZERO-BASE'}
    summary = {'rule_statuses': {'V33-234': 'fail', 'CMM-007': 'fail'}}
    coverage = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summary)
    receipts = build_check_receipts(result, report_kind='final',
        rule_execution_summary=summary, obligation_coverage=coverage)
    relevant = [r for r in receipts if r['rule_id'] in ('V33-234', 'CMM-007')]
    assert len(relevant) == 2
    assert all(r['issue_refs'] == ['retained'] for r in relevant)
    assert {r['rule_id']: r['obligation_id'] for r in relevant} == {
        'V33-234': 'OBL-NARRATIVE-AMOUNT', 'CMM-007': 'OBL-TREND-ZERO-BASE'}


def test_year_evidence_skips_ambiguous_contents_and_uses_unique_body_title():
    from src.engine.native_narrative_checks import _metadata, _Text
    from src.services.rule_source_binding import _native_span
    pages = ['目录\n2025年度部门决算表\n2025年度部门决算情况说明',
             '第三部分 甲局2025年度部门决算情况说明']
    _, _, refs = _metadata(SimpleNamespace(fiscal_year=2025, page_texts=pages), _Text.from_pages(pages))
    assert refs[0]['page'] == 2
    assert _native_span(refs[0]['span'], refs[0]['page'], pages, 'a'*64)


def test_repeated_functional_name_requires_unique_original_code_and_name():
    from src.engine.native_name_evidence import _qualified_name_span
    from src.services.rule_source_binding import _native_span
    page = '其他科目 12.00\n210 卫生健康支出 10759.21\n21099 其他卫生健康支出 2.40\n2109999 其他卫生健康支出 2.40'
    span = _qualified_name_span(page, '210', '卫生健康支出')
    assert span == '210 卫生健康支出'
    assert _native_span(span, 1, [page], 'a'*64)
    assert _qualified_name_span(page + '\n210 卫生健康支出 10759.21', '210', '卫生健康支出') is None
    assert _qualified_name_span('1210 卫生健康支出', '210', '卫生健康支出') is None
