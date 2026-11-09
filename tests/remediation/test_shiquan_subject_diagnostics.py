from types import SimpleNamespace

import pytest

from src.engine.diagnostic_fields import diagnostics_for_texts
from src.engine.native_table_text import context_guard, context_rejection, record_context_rejection
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33102_TotalSheet_vs_Text


OWNER = '上海市普陀区石泉路街道社区事务受理中心'
FOREIGN = '上海市普陀区人民政府石泉路街道社区事务受理中心'


def document(owner=FOREIGN, year=2025):
    doc = build_document('scope.pdf', [f'{OWNER}2025年度单位决算',
        f'一、收入支出决算总体情况说明\n{year}年度{owner}收入支出总计100.00万元。'], [], 0)
    doc.fact_context = SimpleNamespace(org_name=OWNER, fiscal_year=2025)
    return doc


def test_existing_amount_with_conflicting_owner_is_not_reported_as_missing_amount():
    doc = document()
    with pytest.raises(RuleDeferred) as caught:
        R33102_TotalSheet_vs_Text._current_total_claim(doc, ['收入支出总计'])
    assert '金额已披露' in caught.value.detail and '主体' in caught.value.detail
    diagnostic = diagnostics_for_texts(doc, [caught.value.detail])
    assert diagnostic[0]['reason_codes'] == ['subject_conflict']
    assert diagnostic[0]['blocked_by'][0]['dimension'] == 'organization'


def test_same_owner_remains_usable_and_wrong_year_is_not_accepted():
    doc = document(OWNER)
    assert str(R33102_TotalSheet_vs_Text._current_total_claim(doc, ['收入支出总计'])[3]) == '100.00'
    doc = document(OWNER, 2024)
    with pytest.raises(RuleDeferred) as caught:
        R33102_TotalSheet_vs_Text._current_total_claim(doc, ['收入支出总计'])
    assert '其他年度' in caught.value.detail
