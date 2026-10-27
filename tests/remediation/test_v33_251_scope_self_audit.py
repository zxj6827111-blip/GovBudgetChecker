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


@pytest.mark.parametrize('exception', ['依法不予公开', '无此项'])
@pytest.mark.parametrize('budget,final', [('60.00', '50.00'), ('0.00', '0.00')])
def test_ambiguous_exception_with_numeric_values_is_still_unresolved(exception, budget, final):
    from decimal import Decimal

    statement = (f'年初预算或支出决算{exception}，年初预算为{budget}万元，'
                 f'支出决算为{final}万元。')
    doc, issues, outcome, ledger = analyze(
        statement,
        budget=f'{Decimal("100.00") + Decimal(budget):.2f}',
        final=f'{Decimal("90.00") + Decimal(final):.2f}',
    )
    assert_deferred(outcome, ledger)
    assert 'exception_scope_ambiguous' in outcome.reason_codes
    assert not any('已超过' in item.message for item in issues)
    for issue in issues:
        included = {(x.get('kind'), x.get('pos')) for x in issue.location.get('included_items') or []}
        excluded = {(x.get('kind'), x.get('pos')) for x in issue.location.get('not_included_items') or []}
        assert included.isdisjoint(excluded)


@pytest.mark.parametrize('negation', [
    '并非', '并不是', '不属于', '未必', '不能视为', '未证实', '尚未确认', '可能', '疑似',
])
@pytest.mark.parametrize('stage,other', [
    ('年初预算', '支出决算'), ('支出决算', '年初预算'),
])
def test_negation_forms_do_not_supply_a_zero(negation, stage, other):
    doc, issues, outcome, ledger = analyze(
        f'{stage}{negation}无此项，金额未提供，{other}为0.00万元。'
    )
    assert_deferred(outcome, ledger)
    evidence = [source for receipt in doc.fact_receipts
                for source in receipt.get('disclosure_sources') or []]
    assert any(negation in source.get('text', '') and source['scope'] == 'ambiguous'
               for source in evidence)


@pytest.mark.parametrize('suffix', ['待确认', '待核实', '尚未确认'])
def test_unconfirmed_zero_is_not_synthesized(suffix):
    doc, issues, outcome, ledger = analyze(
        f'年初预算无此项（{suffix}），预算金额未提供，支出决算为0.00万元。'
    )
    assert_deferred(outcome, ledger)
    evidence = [source for receipt in doc.fact_receipts
                for source in receipt.get('disclosure_sources') or []]
    assert any(suffix in source.get('text', '') and source['scope'] == 'ambiguous'
               for source in evidence)


@pytest.mark.parametrize('suffix', ['，待确认', '（待复核）', '的说法尚待确认'])
def test_following_qualification_blocks_zero(suffix):
    doc, issues, outcome, ledger = analyze(
        f'年初预算无此项{suffix}，支出决算为0.00万元。'
    )
    assert_deferred(outcome, ledger)


def test_negated_budget_exception_does_not_discard_valid_final_amounts():
    doc, issues, outcome, ledger = analyze(
        '年初预算并非无此项，预算金额未提供，支出决算为0.00万元。',
        final='80.00',
    )
    assert_deferred(outcome, ledger)
    final = next(item for item in doc.fact_receipts
                 if item.get('sub_check_id') == 'V33-251:final:item_sum')
    assert final['status'] == 'fail'
    assert any('支出决算' in item.message and '不一致' in item.message for item in issues)


@pytest.mark.parametrize('first,second,other', [
    ('年初预算', '预算数', '支出决算'),
    ('预算数', '预算', '支出决算'),
    ('支出决算', '决算数', '年初预算'),
    ('决算数', '决算', '年初预算'),
])
def test_joint_tokens_must_name_different_stages(first, second, other):
    doc, issues, outcome, ledger = analyze(f'{first}和{second}依法不予公开{other}未提供。')
    assert_deferred(outcome, ledger)
    evidence = [source for receipt in doc.fact_receipts
                for source in receipt.get('disclosure_sources') or []]
    assert all(set(source['stages']) != {'budget', 'final'} for source in evidence)


@pytest.mark.parametrize('pair', ['年初预算和支出决算', '支出决算与年初预算'])
def test_explicit_joint_withholding_remains_valid(pair):
    doc, issues, outcome, ledger = analyze(f'{pair}涉密，依法不予公开。')
    assert outcome.status == 'pass', outcome.to_dict()
    assert 'OBL-TXT-FINAL-DETAIL' not in ledger['blocking_obligation_ids']
