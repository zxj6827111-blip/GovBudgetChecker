from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_issue_dedup import deduplicate_native_issues
from src.schemas.parse_artifact import FiscalFact


def document():
    facts = [FiscalFact(fact_uid=uid, table_code=table, indicator=indicator,
        instance_uid=table, subject_code=code, subject_name=name, unit='万元', raw_value=value,
        normalized_value=Decimal(value), org_name='甲局', fiscal_year=2025,
        period='本年', budget_stage='决算') for uid, table, indicator, code, name, value in [
        ('name', 'FIN_05_general_public_expenditure', 'total_actual', '201', '一般公共服务支出', '3'),
        ('annuity', 'FIN_05_general_public_expenditure', 'total_actual', '2080506', '机关事业单位职业年金缴费支出', '257.14'),
        ('reception', 'FIN_07_three_public', 'reception', 'total', '合计', '0'),
    ]]
    context = FactContext(facts, 'a'*64, 'run', org_name='甲局', fiscal_year=2025, report_kind='final')
    return SimpleNamespace(fact_context=context, page_texts=[
        '一般公共服务（类）统计信息事务（款）统计管理（项）。',
        '一般公共服务（类）统战事务（款）一般行政管理事务（项）。',
        '机关事业单位职业年金缴费支出（项）257.14万元。支出决算为245.53万元。',
        '公务接待费支出决算为1.53万元。',
    ])


def operand(doc, uid):
    fact = next(f for f in doc.fact_context.facts if f.fact_uid == uid)
    return dict(fact_uid=uid, table_code=fact.table_code, instance_uid=fact.instance_uid,
                subject_code=fact.subject_code, indicator=fact.indicator,
                budget_stage='决算', value=str(fact.normalized_value))


def names(doc):
    return [dict(id=f'name-{p}', rule_id='V33-227', severity='low', message='名称不一致',
        location=dict(page=p, code='201', code_level='类', expected_name='一般公共服务支出',
                      actual_name='一般公共服务', table_refs=[dict(role='说明5', page=p, span=doc.page_texts[p-1][:-1])]))
        for p in [1, 2]]


def reception(doc):
    return [dict(id=rule, rule_id=rule, severity='error', message='接待费表文不一致',
        location=dict(page=4, fact_context_version_token=doc.fact_context.version_token,
            operand_refs=[operand(doc, 'reception')], table_refs=[dict(page=4, span=doc.page_texts[3][:-1])]))
        for rule in ['V33-224', 'CMM-001']]


def annuity(doc):
    one = dict(id='table-text', rule_id='V33-222', severity='error', message='职业年金表文不一致',
        location=dict(page=3, fact_context_version_token=doc.fact_context.version_token,
            operand_refs=[operand(doc, 'annuity')], table_refs=[dict(page=3, span='支出决算为245.53万元')]))
    two = dict(id='repeat', rule_id='V33-NARRATIVE-INDICATOR-REPEAT', severity='error', message='职业年金文内不一致',
        location=dict(page=3, indicator='机关事业单位职业年金缴费支出', measure='本年支出决算', fiscal_year=2025,
            value_a='257.14', value_b='245.53', table_refs=[
                dict(role='披露A', page=3, span='机关事业单位职业年金缴费支出（项）257.14万元'),
                dict(role='披露B', page=3, span='支出决算为245.53万元')]))
    return [one, two]


def test_three_pairs_become_three_cards_with_all_source_locations_and_member_ids():
    doc = document()
    before = names(doc) + reception(doc) + annuity(doc)
    original = deepcopy(before)
    result = deduplicate_native_issues(before, doc)
    assert len(result) == 3
    assert result[0]['location']['pages'] == [1, 2]
    assert set(result[0]['merged_issue_ids']) >= {'name-1', 'name-2'}
    assert set(result[1]['related_rule_ids']) == {'V33-224', 'CMM-001'}
    assert set(result[2]['related_rule_ids']) == {'V33-222', 'V33-NARRATIVE-INDICATOR-REPEAT'}
    assert len(result[2]['location']['table_refs']) == 3
    assert len(result[2]['location']['related_findings']) == 2
    # 允许增加审计别名，但不改写原始发现的结论、原文或严重度。
    assert [(x['message'], x['severity']) for x in before] == [(x['message'], x['severity']) for x in original]


@pytest.mark.parametrize('change', ['code', 'actual_name', 'wrong_page', 'missing_source', 'year'])
def test_name_grouping_never_merges_other_code_or_unverified_occurrence(change):
    doc = document(); one, two = names(doc)
    if change == 'code': two['location']['code'] = '204'
    elif change == 'actual_name': two['location']['actual_name'] = '一般公共事务'
    elif change == 'wrong_page': two['location']['table_refs'][0]['page'] = 1
    elif change == 'missing_source': two['location']['table_refs'] = []
    else: doc.fact_context.facts[0].fiscal_year = 2024
    assert len(deduplicate_native_issues([one, two], doc)) == 2


@pytest.mark.parametrize('change', ['other_occurrence', 'wrong_page', 'budget', 'stale', 'another_business'])
def test_equal_amounts_do_not_merge_other_statement_stage_or_fact(change):
    doc = document(); one, two = reception(doc)
    if change == 'other_occurrence': doc.page_texts.append(doc.page_texts[3]); two['location']['table_refs'][0]['page'] = 5
    elif change == 'wrong_page': two['location']['table_refs'][0]['page'] = 2
    elif change == 'budget': two['location']['operand_refs'][0]['budget_stage'] = '年初预算'
    elif change == 'stale': two['location']['fact_context_version_token'] = 'old'
    else: two['location']['operand_refs'] = [operand(doc, 'annuity')]
    assert len(deduplicate_native_issues([one, two], doc)) == 2


@pytest.mark.parametrize('change', ['three_values', 'wrong_business', 'wrong_year', 'missing_ref', 'missing_fact', 'ambiguous_fact'])
def test_narrative_bridge_requires_same_business_two_claims_and_unique_current_table_fact(change):
    doc = document(); one, two = annuity(doc)
    if change == 'three_values': two['location']['value_a'] = '260'
    elif change == 'wrong_business': two['location']['indicator'] = '基本养老保险缴费支出'
    elif change == 'wrong_year': two['location']['fiscal_year'] = 2024
    elif change == 'missing_ref': two['location']['table_refs'].pop()
    elif change == 'missing_fact': doc.fact_context.facts = [f for f in doc.fact_context.facts if f.fact_uid != 'annuity']
    else: doc.fact_context.facts.append(deepcopy(doc.fact_context.facts[1]))
    assert len(deduplicate_native_issues([one, two], doc)) == 2


def detail_sums(doc):
    doc.page_texts.append('一般公共预算支出具体情况：年初预算为20万元，支出决算为15万元。')
    return [dict(id=kind, rule_id='V33-251', severity='warn', message=f'{kind}分项合计不一致',
        location=dict(page=5, checklist_item=checklist, completeness='complete',
            check_relation_id=f'V33-251:V33-251:{kind}:item_sum',
            detail_section_anchor=dict(page=5, span='年初预算为20万元'),
            included_items=[dict(kind=kind, value_wan='10')], not_included_items=[],
            stated_total_wan=total, included_subtotal_wan='10', difference_wan=difference))
        for kind, checklist, total, difference in [('budget', 'C-11', '20', '-10'), ('final', 'C-12', '15', '-5')]]


def test_same_section_budget_and_actual_have_one_card_two_formulas_and_two_failed_receipts():
    doc = document(); issues = detail_sums(doc)
    receipts = [dict(rule_id='V33-251', sub_check_id=f'V33-251:{kind}:item_sum', status='fail')
                for kind in ['budget', 'final']]
    grouped = deduplicate_native_issues(issues, doc)
    assert len(grouped) == 1
    item = grouped[0]; item['id'] = 'group'
    deduplicate_native_issues(grouped, doc, receipts=receipts)
    assert len(item['location']['detail_sum_checks']) == 2
    assert len(item['location']['related_findings']) == 2
    assert set(item['merged_issue_ids']) >= {'budget', 'final'}
    assert all(receipt['status'] == 'fail' and receipt['issue_refs'] == ['group'] for receipt in receipts)
    assert 'budget分项合计不一致' in item['message'] and 'final分项合计不一致' in item['message']


@pytest.mark.parametrize('change', ['other_section', 'wrong_page', 'partial', 'wrong_kind', 'wrong_sum', 'stale'])
def test_detail_sums_do_not_merge_other_section_or_unverified_computation(change):
    doc = document(); one, two = detail_sums(doc)
    if change == 'other_section':
        doc.page_texts[4] += '另一节年初预算为30万元。'
        two['location']['detail_section_anchor']['span'] = '年初预算为30万元'
    elif change == 'wrong_page': two['location']['detail_section_anchor']['page'] = 4
    elif change == 'partial': two['location']['completeness'] = 'partial'
    elif change == 'wrong_kind': two['location']['included_items'][0]['kind'] = 'budget'
    elif change == 'wrong_sum': two['location']['included_subtotal_wan'] = '11'
    else: two['location']['fact_context_version_token'] = 'old'
    assert len(deduplicate_native_issues([one, two], doc)) == 2
