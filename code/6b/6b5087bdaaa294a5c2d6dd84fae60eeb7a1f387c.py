import pytest
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.rules_v33 import build_document
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.engine.check_obligations import build_obligation_ledger

HEAD = ('（三）一般公共预算财政拨款支出决算具体情况\n'
        '一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n')
TAIL = '六、一般公共预算财政拨款基本支出决算情况说明'
KNOWN = '1、教育支出（类）普通教育（款）小学教育（项）。年初预算100.00万元，支出决算为90.00万元。\n'


def run(body):
    doc = build_document('/tmp/scoped-exceptions.pdf', [HEAD + body + TAIL], [[]], 1000)
    doc.report_kind = 'final'
    issues, outcomes = run_rules_with_outcomes(doc, report_kind='final', rules=[R33251_FunctionalDetailCompleteness()])
    return doc, issues, outcomes


@pytest.mark.parametrize('exception', ['年初无此项预算', '年初预算涉密依法不予公开'], ids=['budget_zero_does_not_prove_final_zero', 'budget_withholding_does_not_exempt_final'])
def test_budget_exception_does_not_exempt_missing_final(exception):
    doc, issues, outcomes = run(KNOWN + '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。' + exception + '。支出决算未提供。\n')
    assert outcomes[0].status == 'insufficient_data', outcomes[0].to_dict()
    ledger = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summarize_rule_outcomes(outcomes))
    target = next(x for x in ledger['instances'] if x['obligation_id'] == 'OBL-TXT-FINAL-DETAIL')
    assert target['status'] == 'insufficient_data'
    assert target['blocks_gate'] is True


def test_withheld_contribution_is_not_a_complete_sum_mismatch():
    doc, issues, outcomes = run(
        '1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n'
        '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。该科目年初预算和支出决算涉密，依法不予公开。\n')
    assert not any(x.location.get('completeness') == 'complete' and '合计' in x.message and '不一致' in x.message for x in issues), [x.message for x in issues]
    assert not any(x['status'] == 'fail' and x.get('sub_check_id', '').endswith(':item_sum') for x in doc.fact_receipts), doc.fact_receipts
