import pytest
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.rules_v33 import build_document
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.engine.check_obligations import build_obligation_ledger

HEAD = ('（三）一般公共预算财政拨款支出决算具体情况\n'
        '一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n')
TAIL = '六、一般公共预算财政拨款基本支出决算情况说明'

@pytest.mark.parametrize('body', [
    '1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算未提供。\n'
    '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算未提供。\n',
    '1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为90.00万元。\n'
    '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。主要用于职工住房公积金缴费，年初预算40.00万元。\n',
], ids=['all_item_final_amounts_missing', 'one_item_missing_other_items_equal_total'])
def test_missing_item_amount_does_not_complete_obligation(body):
    doc = build_document('/tmp/independent-missing-items.pdf', [HEAD + body + TAIL], [[]], 1000)
    doc.report_kind = 'final'
    issues, outcomes = run_rules_with_outcomes(doc, report_kind='final', rules=[R33251_FunctionalDetailCompleteness()])
    assert outcomes[0].status == 'insufficient_data', outcomes[0].to_dict()
    ledger = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summarize_rule_outcomes(outcomes))
    target = next(x for x in ledger['instances'] if x['obligation_id'] == 'OBL-TXT-FINAL-DETAIL')
    assert target['status'] == 'insufficient_data'
    assert target['blocks_gate'] is True
