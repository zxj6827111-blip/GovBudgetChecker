"""原件错码/缺上级不能把项级金额重复放进类级合计。"""
from copy import deepcopy

import pytest

from src.engine.native_hierarchy_checks import check_native_detail_tables
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33005_TableTotalConsistency, R33120_DetailTableCheck
from tests.remediation.test_real_intake_table_gaps import T5, native_doc


def check(doc, rule=None):
    return check_native_detail_tables(rule or R33005_TableTotalConsistency(), doc, table_codes=[T5])


def test_mistyped_item_is_reported_as_orphan_and_never_counted_twice_in_class_total():
    doc = native_doc([('total', '100', 1, '万元'), ('201', '100', 1, '万元'),
                      ('20132', '100', 1, '万元'), ('3013299', '100', 1, '万元')])
    before = deepcopy(doc.fact_context.to_dict())
    issues = check(doc)
    assert len(issues) == 1
    assert issues[0].location['check_type'] == 'orphan_classification'
    assert issues[0].location['orphan_codes'] == ['3013299']
    assert '3013299' in issues[0].message and '2013299' not in issues[0].message
    assert issues[0].location['source_refs'] == [{'page': 1, 'span': '3013299', 'role': '原文孤立款项编码'}]
    assert issues[0].location['operand_refs'][0]['subject_code'] == '3013299'
    total = next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:total:total_actual')
    assert total['status'] == 'pass' and total['operand_fact_ids'] == ['fact-1', 'fact-2']
    assert next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:orphan_classification')['status'] == 'fail'
    assert doc.fact_context.to_dict() == before


def test_real_difference_between_printed_class_totals_is_still_detected():
    doc = native_doc([('total', '90', 1, '万元'), ('201', '100', 1, '万元'),
                      ('3013299', '10', 1, '万元')])
    issues = check(doc)
    assert len(issues) == 2
    mismatch = next(i for i in issues if i.location.get('code') == 'total')
    assert '10.00' in mismatch.message and '110.00' not in mismatch.message
    assert [r['subject_code'] for r in mismatch.location['operand_refs']] == ['total', '201']


def test_no_class_row_does_not_promote_an_item_to_class_or_pass_the_total():
    doc = native_doc([('total', '100', 1, '万元'), ('2013299', '100', 1, '万元')])
    with pytest.raises(RuleDeferred) as result:
        check(doc)
    assert len(result.value.partial_issues) == 1
    assert result.value.partial_issues[0].location['orphan_codes'] == ['2013299']
    total = next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:total:total_actual')
    assert total['status'] == 'insufficient_data'


@pytest.mark.parametrize('codes', [('201', '20132', '2013299'), ('2010000', '2013200', '2013299')])
def test_valid_native_hierarchy_and_proven_padded_class_codes_remain_unchanged(codes):
    doc = native_doc([('total', '100', 1, '万元'), *[(code, '100', 1, '万元') for code in codes]])
    assert check(doc) == []


def test_detail_amount_checker_uses_classes_and_does_not_silently_add_an_orphan():
    doc = native_doc([('total', '100', 1, '万元'), ('201', '100', 1, '万元'),
                      ('3013299', '100', 1, '万元')])
    assert check(doc, R33120_DetailTableCheck()) == []
    total = next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:total:total_actual')
    assert total['operand_fact_ids'] == ['fact-1', 'fact-2']


def orphan_pair():
    from types import SimpleNamespace
    from src.engine.fact_context import FactContext
    from src.engine.native_three_public_ratios import operand_ref
    from src.schemas.parse_artifact import FiscalFact
    from decimal import Decimal
    facts = [FiscalFact(fact_uid=f'orphan-{index}', indicator='total_actual', raw_value='100',
                       normalized_value=Decimal('100'), unit='万元', subject_code='3013299',
                       subject_name='其他组织事务支出', table_code=table, instance_uid=table,
                       org_name='甲局', fiscal_year=2025, budget_stage='决算', period='本年')
             for index, table in enumerate(['FIN_02_income', 'FIN_03_expenditure'])]
    context = FactContext(facts, 'a'*64, 'run', org_name='甲局', fiscal_year=2025)
    issues = [dict(id=f'orphan-{index}', rule_id='V33-005', severity='warn', message='科目层级不一致',
                   check_relation_id=f'V33-005:{fact.table_code}:orphan_classification',
                   location=dict(table=fact.table_code, page=index+1, check_type='orphan_classification',
                                 orphan_codes=['3013299'], operand_refs=[operand_ref(fact, '孤立款项原行')],
                                 source_refs=[dict(page=index+1,span='3013299',role='原文孤立款项编码')],
                                 fact_context_version_token=context.version_token))
              for index, fact in enumerate(facts)]
    return SimpleNamespace(fact_context=context), issues


def test_mirrored_orphan_code_merges_but_keeps_both_source_rows_and_failure_receipts():
    from src.engine.native_issue_dedup import deduplicate_native_issues
    doc, issues = orphan_pair()
    receipts = [dict(rule_id='V33-005', sub_check_id=f'{fact.table_code}:orphan_classification',
                     status='fail', issue_refs=[f'orphan-{index}'])
                for index, fact in enumerate(doc.fact_context.facts)]
    result = deduplicate_native_issues(issues, doc, receipts=receipts)
    assert len(result) == 1 and len(result[0]['location']['operand_refs']) == 2
    assert len(result[0]['location']['source_refs']) == 2
    assert len(result[0]['location']['related_findings']) == 2
    assert 'FIN_02_income' in result[0]['message'] and 'FIN_03_expenditure' in result[0]['message']
    assert all(r['issue_refs'] == [result[0]['id']] for r in receipts)


@pytest.mark.parametrize('fault', ['code','name','year','owner','scope','stale','unbound','no_name'])
def test_equal_money_cannot_merge_unrelated_or_unproven_orphan_rows(fault):
    from src.engine.native_issue_dedup import deduplicate_native_issues
    doc, issues = orphan_pair()
    fact = doc.fact_context.facts[1]
    if fault == 'code': fact.subject_code='3043299'; issues[1]['location']['orphan_codes']=['3043299']
    elif fault == 'name': fact.subject_name='其他事项'
    elif fault == 'year': fact.fiscal_year=2024
    elif fault == 'owner': fact.org_name='乙局'
    elif fault == 'scope': fact.org_scope='本级单位'
    elif fault == 'stale': issues[1]['location']['fact_context_version_token']='old'
    elif fault == 'unbound': issues[1]['location']['operand_refs'][0]['fact_uid']='unknown'
    else: fact.subject_name=None
    assert len(deduplicate_native_issues(issues, doc)) == 2
