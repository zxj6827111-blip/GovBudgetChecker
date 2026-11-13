from copy import deepcopy
from decimal import Decimal

import pytest

from src.engine.native_expenditure_context import build_expenditure_context
from src.engine.native_hierarchy_checks import _table_view
from src.services.expenditure_context_binding import bind_expenditure_context
from tests.remediation.test_shiquan_native_empty_rows import grid_document


T3 = 'FIN_03_expenditure'


def document():
    rows = [
        ['功能分类科目编码', '科目名称', '本年支出合计', '基本支出', '项目支出', '上缴上级支出', '经营支出', '对附属单位补助支出'],
        ['合计', '', '10.00', '8.00', '3.00', '', '', ''],
        ['201', '一般公共服务支出', '3.00', '', '3.00', '', '', ''],
        ['208', '社会保障和就业支出', '7.00', '7.00', '', '', '', ''],
    ]
    doc = grid_document(rows, T3)
    doc.page_texts = ['甲局2025年度部门决算。' + '\n'.join(' '.join(row) for row in rows)]
    return doc


def bind(doc, value):
    return bind_expenditure_context(value, context=doc.fact_context, artifact=doc.parse_artifact,
                                    pages=doc.page_texts, file_hash=doc.fact_context.file_hash)


def test_adjacent_project_amount_is_retained_and_each_column_has_its_own_result():
    doc = document()
    before = doc.fact_context.to_dict()
    view, error = _table_view(doc, T3)
    assert error is None
    data = build_expenditure_context(doc, view)
    result = bind(doc, data)
    assert result['verified']
    row = next(row for row in result['rows'] if row['code'] == '201')
    values = {cell['indicator']: cell for cell in row['cells']}
    assert Decimal(values['basic_actual']['value']) == 0 and values['basic_actual']['blank_as_zero']
    assert Decimal(values['project_actual']['value']) == 3 and not values['project_actual']['blank_as_zero']
    assert values['project_actual']['bbox'] != values['basic_actual']['bbox']
    columns = {c['indicator']: c for c in result['columns']}
    assert Decimal(columns['total_actual']['difference']) == 0
    assert Decimal(columns['project_actual']['difference']) == 0
    assert Decimal(columns['basic_actual']['difference']) == 1
    assert all(row['row_status'] == 'ok' for row in result['rows'][1:])
    assert doc.fact_context.to_dict() == before


@pytest.mark.parametrize('change', ['dropped_class', 'changed_value', 'fake_blank', 'stale', 'wrong_hash',
                                  'swapped_column', 'missing_cell', 'other_year', 'budget'])
def test_comparison_context_must_not_display_partial_changed_or_incompatible_sources(change):
    doc = document()
    view, error = _table_view(doc, T3)
    assert error is None
    data = build_expenditure_context(doc, view)
    if change == 'dropped_class': data['rows'].pop()
    elif change == 'changed_value': data['rows'][1]['cells'][2]['value'] = '30'
    elif change == 'fake_blank': data['rows'][1]['cells'][1]['blank_observation']['bbox'][0] += 1
    elif change == 'stale': data['fact_version'] = 'old'
    elif change == 'wrong_hash': data['file_hash'] = 'b'*64
    elif change == 'swapped_column': data['rows'][1]['cells'][1]['indicator'] = 'project_actual'
    elif change == 'missing_cell': data['rows'][1]['cells'].pop()
    else:
        fact = next(f for f in doc.fact_context.facts if f.subject_code == '201' and f.indicator == 'project_actual')
        if change == 'other_year': fact.fiscal_year = 2024
        else: fact.budget_stage = '年初预算'
    assert bind(doc, data) is None
