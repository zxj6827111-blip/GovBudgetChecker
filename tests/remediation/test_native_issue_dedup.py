from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_issue_dedup import deduplicate_native_issues, link_merged_issue_receipts
from src.engine.native_hierarchy_checks import check_native_detail_tables
from src.engine.rules_v33 import R33120_DetailTableCheck, R33241_Table3_ExpenseAdvancedCheck
from src.schemas.issues import IssueItem
from src.schemas.parse_artifact import FiscalFact, ParseArtifact


T3 = 'FIN_03_expenditure'
T7 = 'FIN_07_three_public'
FROZEN = Path(__file__).resolve().parents[2] / 'outputs/real_intake_review_20261008/native'


def context():
    facts = []
    for indicator in ('total_actual', 'project_actual'):
        for index, (code, amount) in enumerate([('208', '100'), ('20802', '40'), ('20805', '50')]):
            facts.append(FiscalFact(
                fact_uid=f'{indicator}-{index}', indicator=indicator, raw_value=amount,
                normalized_value=Decimal(amount), unit='万元', subject_code=code,
                subject_name=code, table_code=T3, instance_uid='one', org_name='甲局',
                fiscal_year=2025, budget_stage='决算', period='本年'))
    return FactContext(facts, 'a' * 64, 'run', org_name='甲局', fiscal_year=2025)


def sum_issue(rule, indicator='total_actual', *, exact=False):
    c = context()
    refs = [{'fact_uid': f'{indicator}-{i}', 'table_code': T3, 'instance_uid': 'one',
             'indicator': indicator, 'subject_code': code, 'budget_stage': '决算',
             'value': amount, 'value_unit': '万元',
             'role': ('parent' if i == 0 else 'child') if rule == 'V33-120' else f'vertical:{i}',
             'relation_id': f'relation-{rule}'}
            for i, (code, amount) in enumerate([('208', '100'), ('20802', '40'), ('20805', '50')])]
    location = {'page': 2, 'pages': [2, 3], 'operand_refs': refs,
                'fact_context_version_token': c.version_token}
    if exact:
        location.update(bbox=[10, 20, 50, 40], location_level='exact_cell', verification_status='verified')
    return {'id': f'issue-{rule}-{indicator}', 'rule_id': rule, 'severity': 'warn' if rule == 'V33-120' else 'error',
            'message': f'{rule} sum error', 'location': location,
            'check_relation_id': f'{rule}:sum:{indicator}',
            'fact_ids': [ref['fact_uid'] for ref in refs], 'evidence_ids': [f'evidence-{rule}'],
            'evidence': [{'page': 2, 'source_cell_id': f'cell-{rule}', 'quote': '100.00'}]}


def test_same_sum_merge_keeps_primary_original_location_evidence_and_receipt_links():
    one, two = sum_issue('V33-120'), sum_issue('V33-241', exact=True)
    receipts = [{'rule_id': rule, 'sub_check_id': 'sum:total_actual', 'status': 'fail',
                 'issue_refs': [f'issue-{rule}-total_actual']} for rule in ('V33-120', 'V33-241')]
    doc = SimpleNamespace(fact_context=context(), fact_receipts=receipts)
    result = deduplicate_native_issues([one, two], doc)
    assert len(result) == 1
    merged = result[0]
    assert merged['id'] == two['id']
    assert merged['severity'] == 'error'
    assert merged['location']['bbox'] == [10, 20, 50, 40]
    assert merged['related_rule_ids'] == ['V33-120', 'V33-241']
    assert merged['check_relation_ids'] == ['V33-120:sum:total_actual', 'V33-241:sum:total_actual']
    assert len(merged['evidence']) == 2 and len(merged['evidence_ids']) == 2
    assert all(receipt['issue_refs'] == [two['id']] for receipt in receipts)
    assert one.get('related_rule_ids') is None  # input finding was not rewritten


def test_equal_total_and_project_gaps_remain_separate():
    issues = [sum_issue('V33-120'), sum_issue('V33-241'),
              sum_issue('V33-120', 'project_actual'), sum_issue('V33-241', 'project_actual')]
    result = deduplicate_native_issues(issues, SimpleNamespace(fact_context=context()))
    assert len(result) == 2
    assert {ref['indicator'] for ref in result[0]['location']['operand_refs']} == {'total_actual'}
    assert {ref['indicator'] for ref in result[1]['location']['operand_refs']} == {'project_actual'}


@pytest.mark.parametrize('difference', ['child_identity', 'parent', 'instance', 'version', 'manual', 'pooled', 'values_only'])
def test_equal_values_and_page_never_hide_a_different_or_unresolved_relation(difference):
    one, two = sum_issue('V33-120'), sum_issue('V33-241')
    if difference == 'child_identity':
        two['location']['operand_refs'][-1]['subject_code'] = '20806'
    elif difference == 'parent':
        two['location']['operand_refs'][0]['role'] = 'child'
        two['location']['operand_refs'][1]['role'] = 'parent'
    elif difference == 'instance':
        two['location']['operand_refs'][0]['instance_uid'] = 'two'
    elif difference == 'version':
        two['location']['fact_context_version_token'] = 'older'
    elif difference == 'manual':
        two['severity'] = 'manual_review'
    elif difference == 'pooled':
        two['location']['operand_refs'][-1]['relation_id'] = 'different'
    else:
        two['location']['operand_refs'] = [{'value': '100'}, {'value': '40'}, {'value': '50'}]
    assert len(deduplicate_native_issues([one, two], SimpleNamespace(fact_context=context()))) == 2


def test_arithmetic_child_order_is_commutative_but_parent_role_is_not():
    one, two = sum_issue('V33-120'), sum_issue('V33-241')
    two['location']['operand_refs'][1:] = list(reversed(two['location']['operand_refs'][1:]))
    assert len(deduplicate_native_issues([one, two], SimpleNamespace(fact_context=context()))) == 1


def test_single_rounding_relation_may_merge_without_dropping_or_promoting_it():
    one, two = sum_issue('V33-120'), sum_issue('V33-241')
    one['severity'] = two['severity'] = 'info'
    result = deduplicate_native_issues([one, two], SimpleNamespace(fact_context=context()))
    assert len(result) == 1 and result[0]['severity'] == 'info'


def yoy_issues():
    source = '一般公共预算财政拨款支出139,407.47万元。与2024年度相比，一般公共预算财政拨款支出增加16,819.41万元，增长13.14%'
    first = {'id': 'v', 'rule_id': 'V33-234', 'severity': 'error',
             'message': 'yoy', 'check_relation_id': 'V33-234:change:1',
             'location': {'page': 1, 'table_refs': [{'page': 1, 'span': source}],
                          'current_year': 2025, 'current_amount_wan': '139407.47',
                          'change_amount_wan': '16819.41', 'declared_percent': '13.14', 'direction': '增加'}}
    second = {'id': 'c', 'rule_id': 'CMM-007', 'severity': 'error', 'message': 'same yoy',
              'location': {'page': 1, 'indicator': '一般公共预算财政拨款', 'fiscal_year': 2025,
                           'current_amount': '139407.47', 'delta_amount': '16819.41', 'unit': '万元',
                           'delta_unit': '万元', 'declared_percent': '13.14', 'direction': '增加',
                           'table_refs': [{'page': 1, 'span': '一般公共预算财政拨款支出增加16,819.41万元，增长13.14%'}]}}
    return source, first, second


def test_same_original_yoy_claim_merges_despite_different_section_and_phrase_lengths():
    source, one, two = yoy_issues()
    result = deduplicate_native_issues([one, two], SimpleNamespace(page_texts=[source]))
    assert len(result) == 1
    assert result[0]['related_rule_ids'] == ['V33-234', 'CMM-007']
    assert len(result[0]['location']['table_refs']) == 2


@pytest.mark.parametrize('difference', ['business', 'repeated_source', 'missing_source', 'other_year', 'other_amount'])
def test_yoy_requires_same_business_year_and_unique_original_source(difference):
    source, one, two = yoy_issues()
    if difference == 'business':
        two['location']['indicator'] = '财政拨款收入支出总计'
    elif difference == 'repeated_source':
        source = source + '\n' + source
    elif difference == 'missing_source':
        source = '仅有相同数字139407.47和16819.41，不是原文声明'
    elif difference == 'other_year':
        two['location']['fiscal_year'] = 2024
    else:
        two['location']['current_amount'] = '150000'
    assert len(deduplicate_native_issues([one, two], SimpleNamespace(page_texts=[source]))) == 2


def test_three_public_same_total_declaration_and_stage_share_one_issue():
    quote = '“三公”经费财政拨款支出年初预算为100万元'
    fact = FiscalFact(fact_uid='total-budget', indicator='total_actual', raw_value='90',
                      normalized_value=Decimal('90'), unit='万元', table_code=T7,
                      instance_uid='t7', subject_code='total', fiscal_year=2025,
                      org_name='甲局', budget_stage='年初预算')
    doc = SimpleNamespace(page_texts=[quote], fact_context=FactContext([fact], 'a'*64, 'run',
                           org_name='甲局', fiscal_year=2025))
    issues = []
    for rule in ('CMM-001', 'V33-108'):
        issues.append({'id': rule, 'rule_id': rule, 'severity': 'warn', 'message': 'total mismatch',
                       'check_relation_id': f'{rule}:total-budget',
                       'location': {'page': 1, 'table_refs': [{'page': 1, 'span': quote}],
                                    'operand_refs': [{'fact_uid': fact.fact_uid, 'table_code': T7,
                                                      'instance_uid': 't7', 'indicator': 'total_actual',
                                                      'subject_code': 'total', 'budget_stage': '年初预算', 'value': '90'}]}})
    assert len(deduplicate_native_issues(issues, doc)) == 1
    other = deepcopy(issues[1])
    other['location']['operand_refs'][0]['budget_stage'] = '决算'
    assert len(deduplicate_native_issues([issues[0], other], doc)) == 2


def test_fixed_schema_issueitem_preserves_merge_metadata_in_location():
    one, two = sum_issue('V33-120'), sum_issue('V33-241')
    for issue in (one, two):
        issue.update(source='rule', title='层级不平', severity='high')
    items = [IssueItem(**one), IssueItem(**two)]
    result = deduplicate_native_issues(items, SimpleNamespace(fact_context=context()))
    assert len(result) == 1
    assert result[0].location['related_rule_ids'] == ['V33-120', 'V33-241']


def test_raw_issue_then_id_assignment_keeps_both_receipt_relationships():
    from src.engine.rules_v33 import Issue
    one, two = sum_issue('V33-120'), sum_issue('V33-241')
    raw = [Issue(item['rule_id'], item['severity'], item['message'], location=item['location'],
                 check_relation_id=item['check_relation_id'], fact_ids=item['fact_ids']) for item in (one, two)]
    merged, = deduplicate_native_issues(raw, SimpleNamespace(fact_context=context()))
    serialized = {'id': 'retained', 'check_relation_id': merged.check_relation_id, 'location': merged.location}
    receipts = [{'rule_id': rule, 'sub_check_id': 'sum:total_actual', 'status': 'fail', 'issue_refs': []}
                for rule in ('V33-120', 'V33-241')]
    link_merged_issue_receipts([serialized], receipts)
    assert all(r['issue_refs'] == ['retained'] for r in receipts)


def frozen_doc(number):
    path = FROZEN / f'document_{number:02}'
    if not path.exists():
        pytest.skip('冻结原件复测产物未提供')
    artifact = ParseArtifact.from_dict(json.loads((path/'artifact.json').read_text()))
    context = FactContext.from_dict(json.loads((path/'fact_context.json').read_text()))
    return path, SimpleNamespace(parse_artifact=artifact, fact_context=context,
                                 fact_receipts=[], structured_consumption=[])


def test_frozen_economic_duplicate_rules_merge_by_current_original_facts():
    path, doc = frozen_doc(5)
    issues = json.loads((path/'issues.json').read_text())
    selected = [i for i in issues if i['rule'] in ('V33-117', 'V33-243') and i['severity'] != 'info']
    assert len(selected) == 6
    result = deduplicate_native_issues(selected, doc)
    assert len(result) == 3
    assert all(i['location']['related_rule_ids'] == ['V33-117', 'V33-243'] for i in result)


def test_new_native_hierarchy_and_advanced_arithmetic_share_only_same_columns():
    _, doc = frozen_doc(6)
    hierarchy = check_native_detail_tables(R33120_DetailTableCheck(), doc)
    advanced = R33241_Table3_ExpenseAdvancedCheck().apply(doc)
    target = [i for i in hierarchy + advanced if i.severity != 'info'
              and i.location.get('table_code') == T3]
    assert len(target) == 4
    result = deduplicate_native_issues(target, doc)
    assert len(result) == 2
    assert {i.location['column'] for i in result if 'column' in i.location} == {'total_actual', 'project_actual'}
    assert all(i.location['related_rule_ids'] == ['V33-120', 'V33-241'] for i in result)


def test_merged_cross_page_claim_refs_are_literal_on_each_original_page():
    from src.engine.native_issue_dedup import _repair_literal_refs
    doc = SimpleNamespace(page_texts=['一般公共预算支出增\n1', '加16.8万元，增长13.1%。'])
    refs = _repair_literal_refs([{'page': 1, 'span': '一般公共预算支出增加16.8万元，增长13.1%', 'role': '同比声明'}], doc)
    assert [ref['page'] for ref in refs] == [1, 2]
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in refs)
    assert 'operand_refs' not in _merged_text_relation_for_test()


def _merged_text_relation_for_test():
    from src.engine.native_issue_dedup import _merged
    base = {'rule_id': 'V33-234', 'severity': 'warn', 'message': '同比', 'location': {'page': 1}}
    return _merged([base, {**base, 'rule_id': 'CMM-007'}])['location']
