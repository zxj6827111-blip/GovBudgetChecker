import pytest
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.rules_v33 import build_document
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.engine.check_obligations import build_obligation_ledger

HEAD = ('（三）一般公共预算财政拨款支出决算具体情况\n'
        '一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n')
KNOWN = '1、教育支出（类）普通教育（款）小学教育（项）。年初预算100.00万元，支出决算为90.00万元。\n'
TAIL = '六、一般公共预算财政拨款基本支出决算情况说明'


def run(text):
    body = KNOWN + '2、住房保障支出（类）住房改革支出（款）住房公积金（项）。' + text
    doc = build_document('/tmp/exception-admission.pdf', [HEAD + body + TAIL], [[]], 1000)
    doc.report_kind = 'final'
    issues, outcomes = run_rules_with_outcomes(doc, report_kind='final', rules=[R33251_FunctionalDetailCompleteness()])
    return doc, issues, outcomes


@pytest.mark.parametrize('text', [
    '年初预算依法不予公开但支出决算未提供。',
    '支出决算依法不予公开但年初预算未提供。',
    '年初预算无此项但支出决算未提供。',
    '支出决算无此项但年初预算未提供。',
], ids=['budget_withheld','final_withheld','budget_zero','final_zero'])
def test_nearby_other_stage_is_not_joint_exception_scope(text):
    doc, issues, outcomes = run(text)
    assert outcomes[0].status == 'insufficient_data', outcomes[0].to_dict()
    ledger = build_obligation_ledger(None, report_kind='final', rule_execution_summary=summarize_rule_outcomes(outcomes))
    target = next(x for x in ledger['instances'] if x['obligation_id'] == 'OBL-TXT-FINAL-DETAIL')
    assert target['status'] == 'insufficient_data'
    assert target['blocks_gate'] is True


def test_rejected_exception_conflict_is_not_included_in_known_sum():
    import json

    doc, issues, outcomes = run('年初无预算，年初预算为60.00万元，支出决算为0.00万元。')
    assert outcomes[0].status == 'insufficient_data'
    assert issues
    for issue in issues:
        location = issue.location
        json.dumps(location, ensure_ascii=False)
        included = location.get('included_items') or []
        rejected = location.get('not_included_items') or []
        accepted_positions = {(x.get('kind'), x.get('pos')) for x in included}
        rejected_positions = {(x.get('kind'), x.get('pos')) for x in rejected}
        assert not (accepted_positions & rejected_positions), location
        assert all(not x.get('not_included_reason') for x in included), included
    assert not any('已超过' in issue.message for issue in issues), [x.message for x in issues]


def test_known_excess_with_conflicting_operand_has_json_safe_evidence(monkeypatch):
    import json
    import sys

    module = sys.modules[__name__]
    monkeypatch.setattr(module, 'HEAD', HEAD.replace('预算为100.00', '预算为80.00'))
    _doc, issues, outcomes = run('年初无预算，年初预算为60.00万元，支出决算为0.00万元。')
    assert outcomes[0].status == 'insufficient_data'
    excess = [issue for issue in issues if '已超过' in issue.message]
    assert excess
    location = excess[0].location
    assert location['included_subtotal_wan'] == '100.00'
    assert any(item.get('value_wan') == '60.00' for item in location['not_included_items'])
    json.dumps(location, ensure_ascii=False)


@pytest.mark.parametrize('connector', ['但', '而', '不过', '然而', '却'])
@pytest.mark.parametrize('comma', ['', '，'])
@pytest.mark.parametrize('exception,missing', [
    ('年初预算依法不予公开', '支出决算'),
    ('支出决算依法不予公开', '年初预算'),
    ('年初预算无此项', '支出决算'),
    ('支出决算无此项', '年初预算'),
])
def test_adversative_does_not_extend_exception(exception, missing, connector, comma):
    _doc, _issues, outcomes = run(f'{exception}{comma}{connector}{missing}未提供。')
    assert outcomes[0].status == 'insufficient_data', outcomes[0].to_dict()


@pytest.mark.parametrize('text', [
    '本项无此项但支出决算未提供。',
    '依法不予公开不过支出决算未提供。',
    '年初预算未提供而本项无此项。',
    '年初预算依法不予公开支出决算未提供。',
])
def test_uncertain_common_scope_does_not_pass(text):
    _doc, _issues, outcomes = run(text)
    assert outcomes[0].status == 'insufficient_data', outcomes[0].to_dict()


@pytest.mark.parametrize('text', [
    '此款下各“项”中年初预算依法不予公开但支出决算未提供。',
    '此款下各“项”中支出决算依法不予公开但年初预算未提供。',
])
def test_subitem_scope_does_not_expand_stage_scope(text):
    _doc, _issues, outcomes = run(text)
    assert outcomes[0].status == 'insufficient_data', outcomes[0].to_dict()


@pytest.mark.parametrize('reason', [
    'candidate_amount_conflict', 'exception_amount_conflict',
    'amount_or_unit_unusable', 'future_rejection_reason',
])
def test_any_rejection_reason_blocks_operand(reason):
    from decimal import Decimal
    from src.engine.checklist_final_rules import _operand_admitted

    record = {'pos': 12, 'value_wan': Decimal('60'), 'not_included_reason': reason}
    assert not _operand_admitted(record, set())
    record['not_included_reason'] = None
    assert _operand_admitted(record, set())
    assert not _operand_admitted(record, {12})
    record['value_wan'] = None
    assert not _operand_admitted(record, set())
