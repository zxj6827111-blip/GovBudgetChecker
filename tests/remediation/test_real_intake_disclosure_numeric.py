"""原件漏报回归和反例：单位/期间/分母/绩效范围必须显式绑定。"""
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext, build_fact_context_from_artifact
from src.engine.native_final_disclosure import (
    GENERAL, FUND, check_function_disclosures, check_fund_totals,
    check_final_expenditure_shares, check_performance_counts,
)
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import Rule
from src.schemas.parse_artifact import FiscalFact
from src.services.document_profile_resolver import resolve_document_profile
from src.services.pdf_parser import PDFParser
from src.services.evidence_guard import evaluate_finding_evidence
from src.services.rule_source_binding import bind_rule_sources


class DisclosureRule(Rule):
    code, severity = 'TEST-DISCLOSURE', 'error'


def document(body, values, *, heading='五、一般公共预算财政拨款支出决算情况说明'):
    doc = build_document('final.pdf', ['甲局\n2025年度部门决算', heading + '\n' + body], [], 0)
    facts = [FiscalFact(fact_uid=f'fact:{i}', indicator=indicator, raw_value=str(amount),
                        unit='万元', normalized_value=Decimal(str(amount)), org_name='甲局',
                        fiscal_year=2025, period='本年', budget_stage='决算',
                        subject_code=code, subject_name=name, table_code=table,
                        instance_uid=table, fund_scope='general_public_budget' if table == GENERAL else
                        ('government_fund' if table == FUND else None),
                        extra_dims={'evidence_refs': [{'physical_page': 1, 'quote': str(amount),
                            'bbox': [1, 1, 5, 5], 'source_cell_id': f'cell:{i}'}]})
             for i, (table, code, name, indicator, amount) in enumerate(values)]
    doc.fact_context = FactContext(facts, 'a' * 64, 'run', org_name='甲局', fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash='a' * 64, parse_run_uid='run')
    return doc


def medical(body, *, table_amount='544.49'):
    return document(f'一般公共预算财政拨款支出{table_amount}万元。\n' + body,
                    [(GENERAL, '210', '卫生健康支出', 'total_actual', table_amount),
                          (GENERAL, '21011', '行政事业单位医疗', 'total_actual', table_amount),
                          (GENERAL, '2101102', '事业单位医疗', 'total_actual', table_amount),
                          (GENERAL, 'total', '合计', 'total_actual', table_amount)])


ITEM = '1、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。'


def test_same_numeric_value_with_wrong_unit_is_normalized_and_detected():
    doc = medical(ITEM + '主要用于按政策缴纳医疗保险。年初预算579.95万元，支出\n决算为544.49元。')
    issue, = check_function_disclosures(DisclosureRule(), doc)
    assert issue.severity == 'error'
    assert issue.location['portable_rule_id'] == 'R001'
    assert issue.location['declared_amount_wan'] == '0.054449'
    assert issue.location['table_amount_wan'] == '544.49'
    assert issue.location['page'] == 2 and issue.location['pages'] == [1, 2]
    assert issue.fact_ids == ['fact:2']
    assert '支出\n决算为544.49元' in issue.evidence_text
    assert '544.49' == issue.location['fact_source_refs'][0]['span']


# 2026-10-22 口径决议①：容差 0.01 万元（含边界）。
@pytest.mark.parametrize(('claim', 'amount'), [('5444900元', '544.49'),
                                            ('0.054449亿元', '544.49'),
                                            ('544.50万元', '544.49')])
def test_equivalent_units_and_inclusive_amount_tolerance_do_not_raise(claim, amount):
    doc = medical(ITEM + f'年初预算600万元，支出决算为{claim}。', table_amount=amount)
    assert check_function_disclosures(DisclosureRule(), doc) == []
    assert doc.fact_receipts[-1]['status'] == 'pass'


def test_original_inverted_unit_word_order_remains_an_explicit_decision_amount():
    doc = medical(ITEM + '年初预算为600万元，支出决算544.49为万元。')
    assert check_function_disclosures(DisclosureRule(), doc) == []
    assert doc.fact_receipts[-1]['status'] == 'pass'
    assert any('544.49为万元' in ref['span'] for ref in doc.fact_receipts[-1]['disclosure_sources'])


@pytest.mark.parametrize('conflict', ['year', 'owner', 'version', 'recognition', 'instance', 'identity'])
def test_function_disclosure_rejects_ambiguous_or_incompatible_operands(conflict):
    doc = medical(ITEM + '支出决算为544.49元。')
    fact = doc.fact_context.facts[2]
    if conflict == 'year': fact.fiscal_year = 2024
    elif conflict == 'owner': fact.org_name = '乙局'
    elif conflict == 'version': doc.parse_artifact.parse_run_uid = 'old'
    elif conflict == 'recognition': fact.recognition_status = 'ambiguous'
    elif conflict == 'identity': doc.fact_context.facts[1].subject_name = '不一致科目'
    else:
        another = deepcopy(fact)
        another.instance_uid, another.fact_uid = 'other', 'other'
        doc.fact_context.facts.append(another)
    with pytest.raises(RuleDeferred) as signal:
        check_function_disclosures(DisclosureRule(), doc)
    assert not signal.value.partial_issues


def test_purpose_naming_subordinate_unit_does_not_change_current_subject():
    doc = medical(ITEM + '主要为市储备局缴纳基本医疗保险。年初预算600万元，支出决算为544.49万元。')
    assert check_function_disclosures(DisclosureRule(), doc) == []


def test_museum_and_library_fiscal_subjects_are_not_foreign_publishers():
    for item_name in ('博物馆', '图书馆'):
        doc = document(f'一般公共预算财政拨款支出100万元。\n'
                       f'1、文化旅游体育与传媒支出（类）文物（款）{item_name}（项）。'
                       '年初预算100万元，支出决算为100万元。',
                       [(GENERAL, '207', '文化旅游体育与传媒支出', 'total_actual', '100'),
                        (GENERAL, '20702', '文物', 'total_actual', '100'),
                        (GENERAL, '2070205', item_name, 'total_actual', '100'),
                        (GENERAL, 'total', '合计', 'total_actual', '100')])
        assert check_function_disclosures(DisclosureRule(), doc) == []


def test_missing_decision_and_next_item_cannot_supply_zero_or_neighbor_amount():
    doc = medical(ITEM + '年初预算579.95万元。\n2、其他未识别科目。支出决算为544.49元。')
    with pytest.raises(RuleDeferred) as signal:
        check_function_disclosures(DisclosureRule(), doc)
    assert not signal.value.partial_issues
    assert '不能用预算金额替代' in str(signal.value)


def test_function_scope_without_any_current_amount_is_uncompleted():
    doc = document('本部门一般公共预算财政拨款支出情况如下。',
                   [(GENERAL, 'total', '合计', 'total_actual', '100')])
    with pytest.raises(RuleDeferred) as signal:
        check_function_disclosures(DisclosureRule(), doc)
    assert not signal.value.partial_issues
    assert doc.fact_receipts[-1]['status'] == 'insufficient_data'


def fund(body, values=(('income_actual', '234955.97'), ('total_actual', '234955.97'))):
    return document(body, [(FUND, 'total', '合计', indicator, amount) for indicator, amount in values],
                    heading='八、政府性基金预算财政拨款收入支出决算情况说明')


def test_fund_income_uses_current_income_not_expenditure_or_opening_balance():
    doc = fund('政府性基金预算财政拨款收入78,656.53万元，支出234,955.97万元。'
               '年初结转100万元。教育（类）普通教育（款）高等教育（项）。支出决算100万元。')
    issue, = check_fund_totals(DisclosureRule(), doc)
    assert issue.location['indicator'] == 'income_actual'
    assert issue.location['table_amount_wan'] == '234955.97'
    assert issue.location['declared_amount_wan'] == '78656.53'
    assert issue.fact_ids == ['fact:0']
    assert [row['status'] for row in doc.fact_receipts] == ['fail', 'pass']


def test_fund_income_missing_stays_deferred_while_valid_expenditure_is_kept():
    doc = fund('政府性基金预算财政拨款收入78,656.53万元，支出234,955.97万元。',
               values=(('total_actual', '234955.97'),))
    with pytest.raises(RuleDeferred) as signal:
        check_fund_totals(DisclosureRule(), doc)
    assert not signal.value.partial_issues
    assert [row['status'] for row in doc.fact_receipts] == ['insufficient_data', 'pass']


def test_fund_does_not_borrow_a_project_expenditure_after_intervening_clause():
    doc = fund('政府性基金预算财政拨款收入234,955.97万元，年初结转100万元。'
               '其中项目支出100万元。')
    assert check_fund_totals(DisclosureRule(), doc) == []
    assert len(doc.fact_receipts) == 1


@pytest.mark.parametrize('period', ['上年', '去年', '年初预算'])
def test_prior_period_fund_amount_cannot_be_compared_as_current_income(period):
    doc = fund(f'{period}政府性基金预算财政拨款收入10万元。')
    with pytest.raises(RuleDeferred) as signal:
        check_fund_totals(DisclosureRule(), doc)
    assert not signal.value.partial_issues


def test_fund_explicit_zero_is_a_number_but_absence_is_not_zero():
    doc = fund('政府性基金预算财政拨款本年收入0万元，支出0万元。',
               values=(('income_actual', '0'), ('total_actual', '0')))
    assert check_fund_totals(DisclosureRule(), doc) == []
    doc = fund('本部门无政府性基金预算财政拨款收入和支出。', values=())
    assert check_fund_totals(DisclosureRule(), doc) == []
    assert doc.fact_receipts[-1]['status'] == 'not_applicable'


def shares(body, total='2443579.41', general='2196397.98'):
    return document(body, [(GENERAL, 'total', '合计', 'total_actual', general),
                          ('FIN_03_expenditure', 'total', '合计', 'total_actual', total),
                          (GENERAL, '208', '社会保障和就业支出', 'total_actual', '18553.22')])


def test_general_share_is_recomputed_from_whole_expenditure_table():
    doc = shares('一般公共预算财政拨款支出2,196,397.98万元，占本年支出合计的90.34%。'
                 '社会保障和就业支出18,553.22万元，占0.84%。')
    issue, = check_final_expenditure_shares(DisclosureRule(), doc)
    assert issue.location['denominator_table_code'] == 'FIN_03_expenditure'
    assert abs(Decimal(issue.location['computed_percent']) - Decimal('89.8844527422')) < Decimal('.0000000001')
    assert issue.fact_ids == ['fact:0', 'fact:1']
    assert [row['status'] for row in doc.fact_receipts] == ['fail', 'pass']


def test_class_share_uses_general_scope_and_reports_bad_percentage():
    doc = shares('社会保障和就业支出18,553.22万元，占1.84%。')
    issue, = check_final_expenditure_shares(DisclosureRule(), doc)
    assert issue.location['denominator_table_code'] == GENERAL
    assert issue.location['denominator_subject_code'] == 'total'
    assert issue.fact_ids == ['fact:2', 'fact:0']


@pytest.mark.parametrize('conflict', ['missing', 'zero', 'foreign_scope', 'foreign_owner'])
def test_share_denominator_absence_or_scope_conflict_is_deferred(conflict):
    doc = shares('一般公共预算财政拨款支出2,196,397.98万元，占本年支出合计的90.34%。')
    if conflict == 'missing': doc.fact_context.facts.pop(1)
    elif conflict == 'zero': doc.fact_context.facts[1].normalized_value = Decimal('0')
    elif conflict == 'foreign_scope': doc.fact_context.facts[1].fund_scope = 'government_fund'
    else: doc.fact_context.facts[1].org_name = '乙局'
    with pytest.raises(RuleDeferred) as signal:
        check_final_expenditure_shares(DisclosureRule(), doc)
    assert not signal.value.partial_issues


RATINGS = ('绩效评级为“优”的项目105个；绩效评级为“良”的项目21个；'
           '绩效评级为“合格”的项目0个；绩效评级为“不合格”的项目1个。')


def performance(body, *, pages=None):
    return SimpleNamespace(page_texts=pages or ['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + body],
                           report_kind='final', fiscal_year=2025, organization_name='甲局')


def test_performance_rating_counts_have_no_currency_or_rounding_tolerance():
    doc = performance('编报绩效目标的2025年度项目108个；绩效跟踪评价的2025年度项目108个；'
                      '绩效自评的2025年度项目108个，平均得分96.49分（其中，' + RATINGS + '）')
    issue, = check_performance_counts(DisclosureRule(), doc)
    assert issue.location['declared_count'] == 108
    assert issue.location['computed_count'] == 127
    assert issue.location['rating_counts'] == {'优': 105, '良': 21, '合格': 0, '不合格': 1}
    assert issue.location['difference_count'] == -19


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'previous_year', 'foreign_owner'])
def test_incomplete_or_foreign_rating_scope_is_not_a_confirmed_error(change):
    body = '绩效自评的2025年度项目108个，' + RATINGS
    if change == 'missing':
        # 未披露类别仍可能解释差额；已披露合计超过总数则已构成确定矛盾。
        body = body.replace('绩效评级为“不合格”的项目1个。', '').replace('项目108个', '项目128个')
    elif change == 'duplicate': body += '绩效评级为“良”的项目1个。'
    elif change == 'previous_year': body = body.replace('2025年度', '2024年度')
    else: body = '乙局' + body
    with pytest.raises(RuleDeferred) as signal:
        check_performance_counts(DisclosureRule(), performance(body))
    assert not signal.value.partial_issues


def test_distinct_evaluation_scope_is_not_added_to_self_evaluation():
    doc = performance('绩效自评的2025年度项目108个。部门绩效评价项目127个，' + RATINGS)
    assert check_performance_counts(DisclosureRule(), doc) == []


def test_rating_details_cannot_take_total_from_a_different_performance_task():
    doc = performance('编报绩效目标的2025年度项目108个；绩效跟踪评价的2025年度项目108个。' + RATINGS)
    with pytest.raises(RuleDeferred) as signal:
        check_performance_counts(DisclosureRule(), doc)
    assert not signal.value.partial_issues
    assert '不能借用绩效目标' in str(signal.value)


def test_performance_soft_line_break_across_pages_preserves_original_quotes():
    doc = performance('', pages=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n绩效自',
                                 '评的2025年度项目108个。' + RATINGS])
    issue, = check_performance_counts(DisclosureRule(), doc)
    assert issue.location['pages'] == [1, 2]
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in issue.location['table_refs'])


GRAIN_ITEM = '26、粮油物资储备支出（类）粮油物资事务（款）储备粮油补贴（项）。'


def grain(body):
    return document('一般公共预算财政拨款支出19933.27万元。\n' + body,
                    [(GENERAL, '222', '粮油物资储备支出', 'total_actual', '19933.27'),
                     (GENERAL, '22201', '粮油物资事务', 'total_actual', '0'),
                     (GENERAL, '22204', '粮油储备', 'total_actual', '19933.27'),
                     (GENERAL, '2220401', '储备粮油补贴', 'total_actual', '19933.27'),
                     (GENERAL, 'total', '合计', 'total_actual', '19933.27')])


@pytest.mark.parametrize('amount', ['19933.27', '19934.27'])
def test_known_wrong_parent_name_does_not_block_independent_unique_item_amount(amount):
    doc = grain(GRAIN_ITEM + f'年初预算0万元，支出决算{amount}万元。')
    issues = check_function_disclosures(DisclosureRule(), doc)
    assert len(issues) == (amount != '19933.27')
    receipt = doc.fact_receipts[-1]
    assert receipt['operand_fact_ids'] == ['fact:3']
    assert receipt['status'] == ('pass' if amount == '19933.27' else 'fail')
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1]
               for ref in receipt['disclosure_sources'])
    if issues:
        assert issues[0].location['subject_code'] == '2220401'
        assert issues[0].location['difference_wan'] == '1.00'


@pytest.mark.parametrize('conflict', ['class', 'declared_parent', 'actual_parent', 'leaf_name',
                                     'ambiguous_leaf', 'leaf_year', 'parent_year', 'parent_owner',
                                     'parent_recognition', 'leaf_recognition', 'leaf_instance',
                                     'cross_class', 'version'])
def test_parent_conflict_resolution_never_guesses_missing_or_ambiguous_identity(conflict):
    doc = grain(GRAIN_ITEM + '支出决算19933.27万元。')
    facts = doc.fact_context.facts
    if conflict == 'class': facts[0].subject_name = '其他支出'
    elif conflict == 'declared_parent': facts[1].subject_name = '无法确认的款'
    elif conflict == 'actual_parent': facts[2].subject_code = '22205'
    elif conflict == 'leaf_name': facts[3].subject_name = '其他补贴'
    elif conflict == 'ambiguous_leaf':
        another = deepcopy(facts[3])
        another.subject_code, another.fact_uid = '2220501', 'another-leaf'
        facts.append(another)
    elif conflict == 'leaf_year': facts[3].fiscal_year = 2024
    elif conflict == 'parent_year': facts[2].fiscal_year = 2024
    elif conflict == 'parent_owner': facts[2].org_name = '乙局'
    elif conflict == 'parent_recognition': facts[2].recognition_status = 'ambiguous'
    elif conflict == 'leaf_recognition': facts[3].recognition_status = 'ambiguous'
    elif conflict == 'leaf_instance':
        another = deepcopy(facts[3])
        another.instance_uid, another.fact_uid = 'another', 'another-leaf'
        facts.append(another)
    elif conflict == 'cross_class': facts[3].subject_code = '2230401'
    else: doc.parse_artifact.parse_run_uid = 'old'
    with pytest.raises(RuleDeferred) as signal:
        check_function_disclosures(DisclosureRule(), doc)
    assert not signal.value.partial_issues
    if doc.fact_receipts:
        assert doc.fact_receipts[-1]['status'] == 'insufficient_data'
    else:
        assert conflict in {'leaf_instance', 'version'}


def rating_body(total, counts):
    return (f'绩效自评的2025年度项目{total}个，平均得分98.05分（其中，'
            + '；'.join(f'绩效评级为“{label}”的项目{count}个' for label, count in counts) + '）。')


@pytest.mark.parametrize(('total', 'counts'), [
    (78, [('优', 76), ('良', 2)]), (78, [('优', 78)]),
    (0, [('优', 0), ('良', 0)]), (78, [('良', 2), ('优', 76)]),
])
def test_disclosed_distinct_ratings_can_close_without_inventing_missing_zeros(total, counts):
    doc = performance(rating_body(total, counts))
    assert check_performance_counts(DisclosureRule(), doc) == []
    assert doc.fact_receipts[-1]['status'] == 'pass'
    assert doc.fact_receipts[-1]['disclosure_sources']
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1]
               for ref in doc.fact_receipts[-1]['disclosure_sources'])


def test_partial_ratings_below_total_remain_uncompleted_without_fabricated_zeros():
    doc = performance(rating_body(78, [('优', 75), ('良', 2)]))
    with pytest.raises(RuleDeferred) as signal:
        check_performance_counts(DisclosureRule(), doc)
    assert not signal.value.partial_issues
    assert '不能补零' in str(signal.value)


def test_partial_ratings_above_total_are_a_provable_lower_bound_error():
    doc = performance(rating_body(78, [('优', 77), ('良', 2)]))
    issue, = check_performance_counts(DisclosureRule(), doc)
    assert issue.location['rating_counts'] == {'优': 77, '良': 2}
    assert issue.location['comparison_basis'] == 'disclosed_lower_bound'
    assert issue.location['computed_count'] == 79
    assert '合格' not in issue.message


def test_complete_four_ratings_below_total_remain_a_confirmed_error():
    issue, = check_performance_counts(DisclosureRule(), performance(
        rating_body(78, [('优', 75), ('良', 2), ('合格', 0), ('不合格', 0)])))
    assert issue.location['comparison_basis'] == 'complete_sum'
    assert issue.location['computed_count'] == 77


@pytest.mark.parametrize('conflict', ['duplicate_zero', 'duplicate_positive', 'unknown_rating',
                                     'rating_year', 'rating_previous', 'rating_owner', 'rating_scope'])
def test_equal_rating_sum_cannot_override_identity_or_scope_conflict(conflict):
    body = rating_body(78, [('优', 76), ('良', 2)])
    if conflict == 'duplicate_zero': body += '绩效评级为“优”的项目0个。'
    elif conflict == 'duplicate_positive': body = rating_body(78, [('优', 75), ('优', 1), ('良', 2)])
    elif conflict == 'unknown_rating': body += '绩效评级为“中”的项目0个。'
    else:
        prefix = {'rating_year': '2024年度', 'rating_previous': '上年',
                  'rating_owner': '乙局', 'rating_scope': '调整统计范围后'}[conflict]
        body = body.replace('其中，', f'其中，{prefix}')
    with pytest.raises(RuleDeferred) as signal:
        check_performance_counts(DisclosureRule(), performance(body))
    assert not signal.value.partial_issues


def test_partial_ratings_close_across_pages_using_unchanged_original_quotes():
    doc = performance('', pages=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n绩效自评的2025年度项目78个，'
                                 '绩效评级为“优”的项目76个；', '绩效评级为“良”的项目2个。'])
    assert check_performance_counts(DisclosureRule(), doc) == []
    refs = doc.fact_receipts[-1]['disclosure_sources']
    assert [ref['page'] for ref in refs] == [1, 2]
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in refs)


def cancelled_body(statement='年内调整取消的项目2个', total=275):
    return rating_body(total, [('优', 263), ('良', 8), ('合格', 2)]).replace('）。', f'；{statement}）。')


def test_explicit_same_scope_cancelled_projects_close_without_inventing_rating_zero():
    doc = performance(cancelled_body())
    assert check_performance_counts(DisclosureRule(), doc) == []
    receipt = doc.fact_receipts[-1]
    assert receipt['status'] == 'pass'
    assert 'cancelled projects' in receipt['formula']
    assert any('年内调整取消的项目2个' in ref['span'] for ref in receipt['disclosure_sources'])
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in receipt['disclosure_sources'])


def test_cancellation_parenthesis_can_close_after_qualitative_self_evaluation_comment():
    body = cancelled_body().replace('）。', '。绩效自评中发现的问题均在整改过程中）。')
    doc = performance(body)
    assert check_performance_counts(DisclosureRule(), doc) == []
    assert doc.fact_receipts[-1]['status'] == 'pass'


@pytest.mark.parametrize('statement', [
    '另有年内调整取消的项目2个', '不含年内调整取消的项目2个',
    '2024年度年内调整取消的项目2个', '乙局年内调整取消的项目2个',
    '上年年内调整取消的项目2个',
    '调整统计范围后年内调整取消的项目2个',
    '年内调整取消的项目1个；年内调整取消的项目1个',
])
def test_cancelled_project_count_cannot_close_foreign_ambiguous_or_excluded_scope(statement):
    with pytest.raises(RuleDeferred) as signal:
        check_performance_counts(DisclosureRule(), performance(cancelled_body(statement)))
    assert not signal.value.partial_issues


def test_cancellations_outside_the_declared_rating_scope_cannot_supply_missing_counts():
    body = rating_body(275, [('优', 263), ('良', 8), ('合格', 2)]) + '另有年内调整取消的项目2个。'
    with pytest.raises(RuleDeferred):
        check_performance_counts(DisclosureRule(), performance(body))


@pytest.mark.parametrize('body', [
    cancelled_body().replace('）。', '。'),
    rating_body(275, [('优', 263), ('良', 8), ('合格', 2)]) + '（其中，年内调整取消的项目2个）。',
])
def test_cancellation_requires_closed_parenthesis_containing_the_same_ratings(body):
    with pytest.raises(RuleDeferred):
        check_performance_counts(DisclosureRule(), performance(body))


def test_cancelled_projects_are_not_used_to_force_an_equal_total():
    issue, = check_performance_counts(DisclosureRule(), performance(cancelled_body(total=274)))
    assert issue.location['computed_count'] == 275
    assert issue.location['cancelled_project_count'] == 2
    assert issue.location['rating_counts'] == {'优': 263, '良': 8, '合格': 2}
    with pytest.raises(RuleDeferred):
        check_performance_counts(DisclosureRule(), performance(cancelled_body(total=276)))


@pytest.fixture(scope='module')
def original_documents():
    """Reparse actual PDFs; no pre-existing output artifact is used as truth."""
    pdfplumber = pytest.importorskip('pdfplumber')
    root = Path('/Users/zhangxingjin/Desktop/决算测试上海市')
    cache = {}

    def load(filename):
        if filename in cache:
            return cache[filename]
        path = root / filename
        if not path.exists():
            pytest.skip('该电脑未提供真实原件；合成边界用例仍执行')
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with pdfplumber.open(path) as pdf:
            texts = [page.extract_text() or '' for page in pdf.pages]
            artifact = PDFParser(None).build_parse_artifact_from_handle(pdf, file_hash=before)
        profile = resolve_document_profile(filename=filename, page_texts=texts)
        doc = build_document(str(path), texts, [], path.stat().st_size)
        doc.parse_artifact, doc.document_profile = artifact, profile
        doc.fact_context = build_fact_context_from_artifact(
            artifact, org_name=profile.organization_name.value,
            fiscal_year=profile.year, report_kind=profile.kind, doc_type='dept_final')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        cache[filename] = doc
        return doc
    return load


@pytest.mark.parametrize(('filename', 'helper', 'page', 'field', 'expected'), [
    ('上海市发展和改革委员会.pdf', check_function_disclosures, 30, 'declared_amount_wan', '0.054449'),
    ('上海市交通委员会.pdf', check_fund_totals, 40, 'declared_amount_wan', '78656.53'),
    ('上海市交通委员会.pdf', check_final_expenditure_shares, 31, 'declared_percent', '90.34'),
    ('上海市文化和旅游局.pdf', check_performance_counts, 38, 'computed_count', 127),
])
def test_frozen_real_pdf_leaks_are_detected_with_actual_page_quotes(original_documents, filename, helper, page, field, expected):
    doc = original_documents(filename)
    try:
        issues = helper(DisclosureRule(), doc)
    except RuleDeferred as signal:
        issues = signal.partial_issues
    matches = [issue for issue in issues if issue.location['page'] == page and issue.location.get(field) == expected]
    assert len(matches) == 1
    issue = matches[0]
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in issue.location['table_refs'])
    if helper is not check_performance_counts:
        assert issue.fact_ids and issue.evidence_ids
        assert len(issue.location['pages']) >= 2
    item = {'id': 'probe', 'source': 'rule', 'rule_id': issue.rule, 'severity': 'high',
            'location': issue.location, 'evidence': [{'page': page, 'text': issue.evidence_text}]}
    binding = bind_rule_sources({'rule_findings': [item]}, page_texts=doc.page_texts,
                                 source_file_hash=doc.fact_context.file_hash,
                                 artifact=doc.parse_artifact, fact_context=doc.fact_context,
                                 consumption=getattr(doc, 'structured_consumption', []))
    assert binding['bound_rule_findings'] == 1, item.get('source_binding_errors')
    assert item['severity'] == 'high'
    complete, reasons, _ = evaluate_finding_evidence(item, page_texts=doc.page_texts,
                            document_page_count=len(doc.page_texts),
                            source_file_hash=doc.fact_context.file_hash)
    assert complete, reasons


def test_fagai_real_pdf_amount_check_completes_and_name_error_is_preserved(original_documents):
    from src.engine.rules_v33 import R33227_Narrative5_T5_NameConsistency

    doc = original_documents('上海市发展和改革委员会.pdf')
    issues = check_function_disclosures(DisclosureRule(), doc)
    assert any(issue.location.get('portable_rule_id') == 'R001' for issue in issues)
    item_receipts = [r for r in doc.fact_receipts if r['sub_check_id'].startswith('general:text:item:')]
    assert item_receipts and all(r['status'] != 'insufficient_data' for r in item_receipts)
    names = R33227_Narrative5_T5_NameConsistency().apply(doc)
    conflict, = [issue for issue in names if issue.location.get('code') == '22204']
    assert conflict.location['expected_name'] == '粮油储备'
    assert conflict.location['actual_name'] == '粮油物资事务'
    assert conflict.location['pages'] == [20, 33]


@pytest.mark.parametrize('filename', ['上海市发展和改革委员会.pdf', '上海市人民政府办公厅.pdf',
                                    '上海市司法局.pdf', '上海市民政局.pdf', '上海市药品监督管理局.pdf'])
def test_other_department_real_partial_rating_counts_close_without_zeros(original_documents, filename):
    doc = original_documents(filename)
    assert check_performance_counts(DisclosureRule(), doc) == []
    receipts = [r for r in doc.fact_receipts if r['sub_check_id'].startswith('performance:self_evaluation_ratings:')]
    assert receipts and all(r['status'] == 'pass' for r in receipts)
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1]
               for r in receipts for ref in r['disclosure_sources'])
