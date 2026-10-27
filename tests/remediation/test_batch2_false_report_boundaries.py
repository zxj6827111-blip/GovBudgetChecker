from types import SimpleNamespace

import pytest

from src.engine.common_rules import CMM006_IncomeExpenseTrendConsistency
from src.engine.rule_identity import rule_identity_requirements
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33236_DocumentScopeTerminology


def doc(text, *, scope=None, year=2024, owner='上海市甲局'):
    return SimpleNamespace(page_texts=[text], fact_context=SimpleNamespace(
        org_name=owner, fiscal_year=year, org_scope=scope,
        facts=[SimpleNamespace(extra_dims={'org_scope_pending':scope is None})]))


@pytest.mark.parametrize('text', [
    '上海市甲局2024年收入预算100万元，比2023年预算增加10万元。上海市乙局2024年支出预算90万元，比2023年预算减少10万元。',
    '上海市甲局2024年收入预算100万元，比2023年预算增加10万元。上海市甲局2023年支出预算90万元，比2022年预算减少10万元。',
    '2024年一般公共预算收入预算100万元，比2023年预算增加10万元。2024年政府性基金支出预算90万元，比2023年预算减少10万元。',
    '2024年收入预算100万元，比2023年预算增加10万元。2024年支出预算90万元，比2022年预算减少10万元。',
    '2024年收入预算100万元，其中项目预算20万元，比2023年预算增加10万元。2024年支出预算90万元，比2023年预算减少10万元。',
    '2024年收入预算100万元，比2023年预算增加10万元。2024年收入预算90万元，比2023年预算减少5万元。2024年支出预算90万元，比2023年预算减少10万元。',
    '2024年收入预算100万元，比2023年预算减少10万元。2024年支出预算120万元，比2023年预算增加10万元。本年另有上年结转20万元。',
])
def test_noncomparable_or_ambiguous_trends_remain_unfinished_instead_of_false_findings(text):
    material=doc(text)
    with pytest.raises(RuleDeferred) as error:
        with rule_identity_requirements(material,'CMM-006'):
            CMM006_IncomeExpenseTrendConsistency().apply(material)
    assert error.value.partial_issues == []
    assert error.value.reason_codes == ['comparison_identity_unresolved']


def test_same_context_real_contradictions_are_not_lost_when_scope_is_unknown():
    material=doc('上海市甲局2024年财政拨款收入预算100万元，比2023年预算增加10万元。'
                 '上海市甲局2024年财政拨款支出预算100万元，比2023年预算减少10万元。'
                 '财政拨款收入支出减少的主要原因是项目调整。')
    with rule_identity_requirements(material,'CMM-006'):
        issues=CMM006_IncomeExpenseTrendConsistency().apply(material)
    assert any('收入/支出同比方向矛盾' in issue.message for issue in issues)
    assert any('口径描述矛盾' in issue.message for issue in issues)


def test_unknown_period_does_not_become_a_clean_check():
    material=doc('收入预算100万元，比2023年预算增加10万元。支出预算90万元，比2023年预算减少10万元。', year=None)
    with pytest.raises(RuleDeferred): CMM006_IncomeExpenseTrendConsistency().apply(material)


def test_summary_in_another_scope_is_unfinished_and_keeps_proven_partial_finding():
    material=doc('2024年收入预算100万元，比2023年预算增加10万元。'
                 '2024年支出预算100万元，比2023年预算减少10万元。财政拨款收入支出增加。')
    with pytest.raises(RuleDeferred) as error: CMM006_IncomeExpenseTrendConsistency().apply(material)
    assert len(error.value.partial_issues)==1
    assert '收入/支出同比方向矛盾' in error.value.partial_issues[0].message


@pytest.mark.parametrize('phrase',['部门决算\n安排','部 门 决 算 安 排','部门\r\n决算安排'])
def test_wrapped_terminology_is_detected_and_bound_to_unchanged_original_span(phrase):
    text='其他相关情况：'+phrase+'如下。'
    material=doc(text,scope='unit')
    with rule_identity_requirements(material,'V33-236'):
        issues=R33236_DocumentScopeTerminology().apply(material)
    assert len(issues)==1
    location=issues[0].location
    assert text[location['pos']:location['end_pos']]==phrase
    assert location['table_refs'][0]['span']==phrase


def test_wrapped_expression_with_unknown_scope_is_still_blocked():
    material=doc('部门决算\n安排如下。')
    with pytest.raises(RuleDeferred):
        with rule_identity_requirements(material,'V33-236'):
            R33236_DocumentScopeTerminology().apply(material)


def test_page_edges_are_neither_joined_into_a_formal_problem_nor_silently_passed():
    material=doc('不含相关表述',scope='unit')
    material.page_texts=['本年部门决算','安排下年度工作。']
    with pytest.raises(RuleDeferred) as error:
        R33236_DocumentScopeTerminology().apply(material)
    assert error.value.partial_issues==[]
    assert '跨页断裂' in error.value.detail


def test_physical_page_footers_do_not_hide_a_cross_page_expression():
    material=doc('不含相关表述',scope='unit')
    material.page_texts=['本年部门决算\n1','安排如下。\n2']
    with pytest.raises(RuleDeferred): R33236_DocumentScopeTerminology().apply(material)


def test_multiple_summary_candidates_do_not_silently_use_the_first_one():
    material=doc('2024年财政拨款收入预算100万元，比2023年预算增加10万元。'
                 '2024年财政拨款支出预算100万元，比2023年预算增加10万元。'
                 '财政拨款收入支出增加。财政拨款收入支出减少。')
    with pytest.raises(RuleDeferred) as error: CMM006_IncomeExpenseTrendConsistency().apply(material)
    assert error.value.partial_issues==[]


def test_real_runner_retains_partial_finding_and_obligation_remains_blocked():
    from src.engine.check_obligations import build_obligation_ledger
    from src.engine.pipeline import run_rules_with_outcomes
    from src.engine.rules_v33 import build_document
    from src.services.document_profile_resolver import resolve_document_profile

    text = ('2024年收入预算100万元，比2023年预算增加10万元。'
            '2024年支出预算100万元，比2023年预算减少10万元。财政拨款收入支出增加。')
    material = build_document('2024预算.pdf', [text], [[]], 0)
    material.fact_context = doc(text).fact_context
    issues, outcomes = run_rules_with_outcomes(material, report_kind='budget',
                                             rules=[CMM006_IncomeExpenseTrendConsistency()])
    assert len(issues) == 1
    assert outcomes[0].status == 'insufficient_data'
    assert outcomes[0].partial_findings_count == 1
    profile = resolve_document_profile(filename='2024预算.pdf', page_texts=['2024年单位预算'])
    ledger = build_obligation_ledger(profile, report_kind='budget', rule_execution_summary={
        'rule_statuses': {'CMM-006': outcomes[0].status, 'V33-245': 'pass'}})
    direction = next(item for item in ledger['instances'] if item['obligation_id'] == 'OBL-TREND-DIRECTION')
    assert direction['blocks_gate'] is True
    assert 'OBL-TREND-DIRECTION' in ledger['blocking_obligation_ids']
