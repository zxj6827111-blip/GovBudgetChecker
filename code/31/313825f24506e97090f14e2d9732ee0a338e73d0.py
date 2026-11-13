import pytest

from src.engine.check_obligations import build_obligation_ledger
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.engine.rules_v33 import build_document


def analyze(statement, budget='100.00', final='90.00'):
    text = (
        '（三）一般公共预算财政拨款支出决算具体情况\n'
        f'一般公共预算财政拨款支出年初预算为{budget}万元，支出决算为{final}万元。其中：\n'
        '1、教育支出（类）普通教育（款）小学教育（项）。'
        '年初预算100.00万元，支出决算为90.00万元。\n'
        '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。'
        f'{statement}\n六、一般公共预算财政拨款基本支出决算情况说明'
    )
    doc = build_document('/tmp/v33-251-independent-self-audit.pdf', [text], [[]], 1000)
    doc.report_kind = 'final'
    issues, outcomes = run_rules_with_outcomes(doc, report_kind='final', rules=[R33251_FunctionalDetailCompleteness()])
    ledger = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summarize_rule_outcomes(outcomes))
    return doc, issues, outcomes[0], ledger


def assert_deferred(outcome, ledger):
    assert outcome.status == 'insufficient_data', outcome.to_dict()
    target = next(x for x in ledger['instances'] if x['obligation_id'] == 'OBL-TXT-FINAL-DETAIL')
    assert target['status'] == 'insufficient_data'
    assert 'OBL-TXT-FINAL-DETAIL' in ledger['blocking_obligation_ids']


def test_ambiguous_withholding_and_nonzero_values_do_not_pass():
    doc, issues, outcome, ledger = analyze(
        '年初预算或支出决算依法不予公开，年初预算为60.00万元，支出决算为50.00万元。',
        budget='160.00', final='140.00',
    )
    assert_deferred(outcome, ledger)


@pytest.mark.parametrize('statement', [
    '年初预算和预算数依法不予公开支出决算未提供。',
    '支出决算和决算数依法不予公开年初预算未提供。',
], ids=['budget_twice', 'final_twice'])
def test_two_names_for_same_stage_do_not_prove_joint_scope(statement):
    doc, issues, outcome, ledger = analyze(statement)
    assert_deferred(outcome, ledger)


@pytest.mark.parametrize('statement', [
    '年初预算并非无此项，预算金额未提供，支出决算为0.00万元。',
    '支出决算并非无此项，决算金额未提供，年初预算为0.00万元。',
], ids=['budget_negation', 'final_negation'])
def test_negated_zero_exception_must_not_synthesize_zero(statement):
    doc, issues, outcome, ledger = analyze(statement)
    assert_deferred(outcome, ledger)
