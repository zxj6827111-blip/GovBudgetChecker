from decimal import Decimal

import pytest

from src.engine.checklist_final_rules import _clause_declaration_accounting


def accounting(raw='0', note='年初无预算。'):
    text = f'年初预算为{raw}，支出决算为150万元。此项目为2025年追加预算，{note}'
    records = [dict(kind='budget', raw=raw, unit=None, pos=0, value_wan=None,
                    not_included_reason='amount_or_unit_unusable')]
    result = _clause_declaration_accounting(text,
        [dict(start=0, end=len(text), label='减排专项支出')], records, 0)
    return records[0], result


def test_bare_zero_and_same_stage_explicit_absence_are_consistent_without_inventing_unit():
    record, result = accounting()
    assert record['value_wan'] == Decimal('0')
    assert record['raw'] == '0' and record['unit'] is None
    assert record['not_included_reason'] is None
    assert result['exceptions'][0]['consistent_zero']
    assert not result['conflicts']


@pytest.mark.parametrize('raw,note', [('1', '年初无预算。'), ('0', ''), ('0', '本年无支出。')])
def test_nonzero_or_absence_for_another_stage_does_not_supply_missing_budget_unit(raw, note):
    record, _ = accounting(raw, note)
    assert record['value_wan'] is None
    assert record['not_included_reason'] is not None


def test_complete_detail_sum_accepts_explicit_zero_without_mixing_missing_unit_into_unit_sort():
    from src.engine.pipeline import build_document
    from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
    doc = build_document('zero-budget.pdf', ['甲局2025年度部门决算',
        '一般公共预算财政拨款支出决算具体情况。年初预算为10000万元，支出决算为150万元。'
        '1.教育支出（类）普通教育（款）小学教育（项），年初预算为1亿元，支出决算为0万元。'
        '2.节能环保支出（类）污染减排（款）减排专项支出（项），年初预算为0，支出决算为150万元。'
        '此项目为2025年追加预算，年初无预算。'], [], 0)
    issues = R33251_FunctionalDetailCompleteness().apply(doc)
    assert not any('金额未纳入' in issue.message or '合计不一致' in issue.message for issue in issues)
    receipts = [r for r in doc.fact_receipts if r['rule_id'] == 'V33-251']
    assert len(receipts) == 2 and all(r['status'] == 'pass' for r in receipts)
