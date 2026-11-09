"""原件核对发现的假取数不足：零支出、折行主体与同比标签，均保留严格反控。"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33224_Narrative7_T7
from src.engine.native_table_text import check_three_public_totals, check_three_public_details
from src.schemas.parse_artifact import FiscalFact
from tests.remediation.test_native_narrative_checks import run
from tests.test_cover_independent_name import run as check_cover


def zero_document(body, *, nonzero=None):
    doc = build_document('zero.pdf', ['上海市甲中心2025年度单位决算',
        '七、财政拨款“三公”经费支出决算情况说明\n' + body], [], 0)
    facts = []
    for stage in ('年初预算', '决算'):
        for indicator in ('total_actual', 'overseas', 'vehicle_purchase_operation',
                          'vehicle_purchase', 'vehicle_operation', 'reception'):
            value = '1.00' if (stage, indicator) == nonzero else '0.00'
            facts.append(FiscalFact(fact_uid=f'{stage}:{indicator}', indicator=indicator,
                raw_value=value, unit='万元', normalized_value=Decimal(value), org_name='上海市甲中心',
                fiscal_year=2025, period='本年', budget_stage=stage, subject_code='total',
                subject_name='合计', table_code='FIN_07_three_public', instance_uid='table',
                extra_dims={'evidence_refs': [{'physical_page': 1}]}))
    doc.fact_context = FactContext(facts, 'a'*64, 'run', org_name='上海市甲中心', fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash='a'*64, parse_run_uid='run')
    return doc


def test_whole_three_public_no_spending_disclosure_checks_printed_zero_cells():
    body = '上海市甲中心2025年度无“三公”经费财政拨款支出。'
    doc = zero_document(body)
    assert check_three_public_totals(R33224_Narrative7_T7(), doc) == []
    assert check_three_public_details(R33224_Narrative7_T7(), doc) == []
    assert len(doc.fact_context.facts) == 12, '不物化或回写叙述性推导值'
    receipts = [r for r in doc.fact_receipts if 'explicit_absence' in r['sub_check_id']]
    assert len(receipts) >= 5
    assert all(r['status'] == 'pass' for r in receipts)
    assert all(any(body.rstrip('。') in s['span'] for s in r['disclosure_sources']) for r in receipts)


@pytest.mark.parametrize('body', [
    '上海市甲中心2024年度无“三公”经费财政拨款支出。',
    '上海市乙中心2025年度无“三公”经费财政拨款支出。',
    '如果上海市甲中心2025年度无“三公”经费财政拨款支出。',
    '并非上海市甲中心2025年度无“三公”经费财政拨款支出。',
    '以下为模板示例。上海市甲中心2025年度无“三公”经费财政拨款支出。',
    '“上海市甲中心2025年度无三公经费财政拨款支出”。',
    '“上海市甲中心2025年度无三公经费财政拨款支出。”',
    '“上海市甲中心2025年度无“三公”经费财政拨款支出。”',
    '“2025年度说明如下。上海市甲中心2025年度无“三公”经费财政拨款支出。”',
    '上海市甲中心2025年度未能确认本年度无“三公”经费财政拨款支出。',
    '上海市甲中心2025年度未必无“三公”经费财政拨款支出。',
    '2024年度情况如下。无“三公”经费财政拨款支出。',
    '上海市乙中心的决算情况如下。无“三公”经费财政拨款支出。',
    '上海市甲中心2025年度预计无“三公”经费财政拨款支出。',
    '截至2025年6月，上海市甲中心无“三公”经费财政拨款支出。',
    '2025年上半年无“三公”经费财政拨款支出。',
    '上海市甲中心2025年度无“三公”经费财政拨款支出。此表述不属实。',
    '上海市甲中心2025年度无“三公”经费预算支出。',
    '上海市甲中心2025年度暂无“三公”经费财政拨款支出。',
    '上海市甲中心2025年度无“三公”经费财政拨款支出？',
    '上海市甲中心2025年度无“三公”经费财政拨款支出，非财政拨款支出另计。',
    '上海市甲中心2025年度无“三公”经费财政拨款支出。公务接待费支出决算1万元。',
])
def test_no_spending_disclosure_does_not_bypass_assertion_scope_or_conflicts(body):
    doc = zero_document(body)
    with pytest.raises(RuleDeferred):
        check_three_public_details(R33224_Narrative7_T7(), doc)
    assert not any('explicit_absence' in r['sub_check_id'] and r['status'] == 'pass'
                   for r in doc.fact_receipts)


def test_no_spending_against_nonzero_table_remains_a_material_error():
    doc = zero_document('上海市甲中心2025年度无“三公”经费财政拨款支出。',
                        nonzero=('决算', 'reception'))
    issues = check_three_public_details(R33224_Narrative7_T7(), doc)
    assert len(issues) == 1 and '公务接待费' in issues[0].message
    assert '未在原件中补写数字' in issues[0].amounts_provenance


def test_explicit_budget_zero_cannot_replace_missing_actual_spending_statement():
    doc = zero_document('“三公”经费财政拨款支出年初预算为0万元。')
    with pytest.raises(RuleDeferred, match='不能用预算金额替代'):
        check_three_public_totals(R33224_Narrative7_T7(), doc)


def test_yoy_bare_total_marker_uses_literal_same_clause_amount_without_repairing_text():
    body = '甲局2025年度收入支出总8101.41万元。与2024年度相比，收入支出总计减少840.12万元，下降9.40%。'
    issues, unresolved, doc = run(body)
    assert not issues and not unresolved
    receipt = doc.fact_receipts[-1]
    assert receipt['status'] == 'pass'
    assert any('收入支出总8101.41万元' in ref['span'] for ref in receipt['disclosure_sources'])
    assert '收入支出总计8101.41万元' not in doc.page_texts[0]


def test_missing_yoy_total_diagnostic_uses_actual_section_not_procurement():
    _, unresolved, doc = run('与2024年度相比，收入支出减少10万元，下降20%。')
    assert unresolved
    # 直接通过结构化登记集合验证，不能只改页面标题。
    assert '政府采购情况说明' not in str(vars(doc))
    assert '同比说明本年总额与增减额' in str(vars(doc))


def test_wrapped_full_subject_has_independent_original_body_evidence():
    name = '上海市普陀区曹杨新村街道社区事务受理服务中心'
    pages = [f'{name[:12]}\n{name[12:]}\n2025年度决算',
             f'第一部分 {name[:19]}\n{name[19:]}概况\n一、主要职能',
             f'第二部分 {name[:16]}\n{name[16:]}2025年度决算表',
             f'第三部分 {name[:20]}\n{name[20:]}2025年度决算情况说明']
    issues, reasons = check_cover(pages)
    assert not issues and not reasons
