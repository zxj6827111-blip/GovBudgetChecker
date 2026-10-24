"""真实复测的表格误报与完整操作数守卫回归。"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext, FactOverlay, apply_fact_overlay
from src.engine.native_hierarchy_checks import (
    check_native_detail_tables, is_native_bilateral_summary_fragment,
    native_grid_table_identity,
)
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33120_DetailTableCheck
from src.schemas.parse_artifact import (
    EvidenceRef, FiscalFact, LogicalTableInstance, ParseArtifact,
    ParsedCell, ParsedTable, ParsedTextBlock,
)


T5 = 'FIN_05_general_public_expenditure'
HASH = 'a' * 64
FROZEN = Path(__file__).resolve().parents[2] / 'outputs/real_intake_review_20261008/native'


def native_doc(entries):
    rows = [['科目编码', '科目名称', '合计']]
    cells, facts, physical = [], [], {}
    for row_idx, (code, raw, page, unit) in enumerate(entries, 1):
        raw = str(raw)
        name = '合计' if code == 'total' else f'科目{code}'
        row = ['' if code == 'total' else code, name, raw]
        rows.append(row)
        fragment = physical.setdefault(page, ParsedTable(
            T5 if page == 1 else 'UNKNOWN', '功能支出表', page, 0,
            (10, 30, 300, 700), 1 if page == 1 else 0, .99, 'lines', unit,
            [rows[0]] if page == 1 else [], [], f'page-{page}'))
        original_row = len(fragment.rows)
        fragment.rows.append(row)
        rowcells = []
        for col, value in enumerate(row):
            numeric = float(raw.replace(',', '')) if col == 2 else (int(value) if value.isdigit() else None)
            cell = ParsedCell(row_idx, col, value, value, numeric, page,
                              (10 + col * 80, 40 + original_row * 20,
                               90 + col * 80, 60 + original_row * 20),
                              False, .99, unit, 'lines',
                              origin_fragment_uid=fragment.fragment_uid,
                              origin_row_idx=original_row)
            cells.append(cell)
            rowcells.append(cell)
            original = deepcopy(cell)
            original.row_idx = original_row
            fragment.cells.append(original)
        value = Decimal(raw.replace(',', '')) * {'万元': Decimal('1'), '元': Decimal('.0001'), '亿元': Decimal('10000')}[unit]
        evidence = EvidenceRef(HASH, page, source_cell_id=f'{fragment.fragment_uid}:r{original_row}c2',
                               quote=raw, bbox=rowcells[2].bbox, location_level='exact_cell')
        facts.append(FiscalFact(fact_uid=f'fact-{row_idx}', indicator='total_actual',
            raw_value=raw, unit=unit, normalized_value=value, display_precision=2,
            org_name='甲单位', fiscal_year=2025, period='本年', budget_stage='决算',
            subject_code=code, subject_name=name, row_order=row_idx,
            table_code=T5, instance_uid='complete', evidence_ids=[evidence.evidence_id],
            extra_dims={'evidence_refs': [evidence.to_dict()]}))
    table = ParsedTable(T5, '一般公共预算财政拨款支出决算表', 1, 0,
                        (10, 30, 300, 700), 1, .99, 'lines', '万元', rows, cells,
                        'complete', [f.fragment_uid for f in physical.values()])
    instance = LogicalTableInstance('complete', T5, table.title,
                                     ['complete'], 1, max(physical), org_name='甲单位', fiscal_year=2025)
    artifact = ParseArtifact(HASH, parse_run_uid='run', table_fragments=[table],
                             physical_fragments=list(physical.values()), logical_tables=[instance])
    context = FactContext(facts, HASH, 'run', org_name='甲单位', fiscal_year=2025,
                          report_kind='final', budget_stage='决算')
    return SimpleNamespace(parse_artifact=artifact, fact_context=context,
                           fact_receipts=[], structured_consumption=[])


def continuation_doc():
    return native_doc([
        ('total', '8925.24', 1, '万元'), ('207', '8925.24', 1, '万元'),
        ('20708', '8925.24', 1, '万元'), ('2070802', '781.22', 1, '万元'),
        ('2070806', '1167.57', 1, '万元'), ('2070807', '102.00', 1, '万元'),
        ('2070899', '6874.45', 2, '万元'),
    ])


def check(doc):
    return check_native_detail_tables(R33120_DetailTableCheck(), doc, table_codes=[T5])


def frozen_doc(number):
    directory = FROZEN / f'document_{number:02}'
    if not directory.exists():
        pytest.skip('真实复测冻结解析产物未随本checkout提供')
    return SimpleNamespace(
        parse_artifact=ParseArtifact.from_dict(json.loads((directory / 'artifact.json').read_text())),
        fact_context=FactContext.from_dict(json.loads((directory / 'fact_context.json').read_text())),
        fact_receipts=[], structured_consumption=[],
    )


def test_complete_continuation_first_row_is_child_and_never_double_counted():
    doc = continuation_doc()
    assert check(doc) == []
    relation = next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:20708:total_actual')
    assert relation['status'] == 'pass'
    assert relation['operand_fact_ids'] == ['fact-3', 'fact-4', 'fact-5', 'fact-6', 'fact-7']
    assert all(r['status'] == 'pass' for r in doc.fact_receipts)


def test_modified_current_continuation_fact_detects_real_gap_and_retains_each_original_cell():
    doc = continuation_doc()
    doc.fact_context = apply_fact_overlay(doc.fact_context, [FactOverlay('fact-7', Decimal('6800'))])
    issue, = check(doc)
    assert issue.location['code'] == '20708'
    assert issue.location['pages'] == [1, 2]
    assert '74.45' in issue.message
    refs = issue.location['operand_refs']
    assert len(refs) == 5 and refs[-1]['value'] == '6800'
    assert refs[-1]['evidence_refs'][0]['quote'] == '6874.45'
    assert refs[-1]['evidence_refs'][0]['source_cell_id'].startswith('page-2:')
    assert all(ref['fact_uid'] and ref['evidence_refs'][0]['bbox'] for ref in refs)
    assert issue.location['fact_context_version_token'] == doc.fact_context.version_token


@pytest.mark.parametrize('fault', ['missing_fact', 'unknown_unit', 'bad_identity', 'wrong_cell'])
def test_missing_or_unqualified_child_defers_instead_of_using_partial_sum(fault):
    doc = continuation_doc()
    if fault == 'missing_fact':
        doc.fact_context.facts.pop()
    elif fault == 'unknown_unit':
        doc.fact_context.facts[-1].unit = None
    elif fault == 'bad_identity':
        doc.fact_context.facts[-1].subject_code = None
    else:
        doc.fact_context.facts[-1].extra_dims['evidence_refs'][0]['source_cell_id'] = 'old-cell'
    with pytest.raises(RuleDeferred) as result:
        check(doc)
    assert not result.value.partial_issues
    assert next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:20708:total_actual')['status'] == 'insufficient_data'


def test_units_are_compared_after_normalization():
    doc = native_doc([('total', '100', 1, '万元'), ('201', '100', 1, '万元'),
                      ('20101', '1000000', 2, '元')])
    assert check(doc) == []


def test_unrepresented_original_code_stops_truncated_grid_comparison():
    doc = continuation_doc()
    # A source code in the code-column alignment proves an omitted original
    # row; integer amounts in the amount column do not constitute a code.
    doc.parse_artifact.text_blocks = [
        ParsedTextBlock('code', 2, '2070898', (20, 30, 70, 40)),
        ParsedTextBlock('amount', 2, '2070897', (170, 30, 220, 40)),
    ]
    with pytest.raises(RuleDeferred) as result:
        check(doc)
    assert not result.value.partial_issues
    assert '2070898' in result.value.detail and '2070897' not in result.value.detail


def test_missing_intermediate_level_defers_class_instead_of_summing_leaves():
    doc = native_doc([('total', '100', 1, '万元'), ('201', '100', 1, '万元'),
                      ('2010101', '50', 1, '万元')])
    with pytest.raises(RuleDeferred) as result:
        check(doc)
    assert result.value.partial_issues == []
    assert '直接层级明细不完整' in result.value.detail


def test_frozen_culture_missing_native_first_row_is_uncompleted_not_false_positive():
    doc = frozen_doc(5)
    with pytest.raises(RuleDeferred) as result:
        check_native_detail_tables(R33120_DetailTableCheck(), doc)
    assert '2070899' in result.value.detail
    assert not any('层级校验失败(20708)' in i.message for i in result.value.partial_issues)


def test_frozen_civil_affairs_three_real_hierarchy_gaps_remain_detected():
    doc = frozen_doc(6)
    issues = check_native_detail_tables(R33120_DetailTableCheck(), doc)
    expected = {'FIN_02_income': '7274.70', 'FIN_03_expenditure': '7051.21', T5: '5131.83'}
    for code, difference in expected.items():
        issue, = [i for i in issues if i.location.get('table_code') == code
                  and i.location.get('code') == '208' and i.location.get('column') == 'total_actual']
        assert difference in issue.message
        assert len(issue.location['operand_refs']) == 7
        assert all(r['fact_uid'] and r['evidence_refs'][0]['source_cell_id']
                   for r in issue.location['operand_refs'])


def test_true_fiscal_summary_continuation_is_routed_without_disabling_unknown_tables():
    doc = frozen_doc(5)
    assert native_grid_table_identity(doc, 18, 0) == ('FIN_04_fiscal_grant_total', 'lines')
    assert is_native_bilateral_summary_fragment(doc, 18, 0)
    assert native_grid_table_identity(doc, 6, 0) == ('UNKNOWN_P6_T0', 'lines')
    assert not is_native_bilateral_summary_fragment(doc, 6, 0)
    assert native_grid_table_identity(doc, 2, 0) == (None, None)


def test_same_page_independent_unknown_grid_keeps_its_own_identity():
    doc = continuation_doc()
    doc.parse_artifact.physical_fragments.append(ParsedTable(
        'UNKNOWN', '附表', 2, 1, (400, 30, 550, 500), 1, .99,
        'lines', '万元', [['项目', '金额'], ['甲', '10'], ['合计', '20']], [], 'separate'))
    assert native_grid_table_identity(doc, 2, 0) == (T5, 'lines')
    assert native_grid_table_identity(doc, 2, 1) == (None, 'lines')
    assert not is_native_bilateral_summary_fragment(doc, 2, 1)
