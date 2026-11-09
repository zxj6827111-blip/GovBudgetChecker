"""把同一原行的支出三列并排交付；计算列与相邻列不混加。"""
from __future__ import annotations

from decimal import Decimal
import re

from .amount_math import classify_amount_diff
from .fact_context import _column_mappings_for_instance
from .native_empty_operands import NativeBlankTerm, blank_observations
from .native_three_public_ratios import operand_ref


def build_expenditure_context(doc, view):
    instance, table, _, cells, rows, facts = view
    if table.table_code not in {'FIN_03_expenditure', 'FIN_05_general_public_expenditure'}:
        return None
    from .native_hierarchy_checks import _select
    from .native_table_arithmetic import _native_grid_coverage_error

    if _native_grid_coverage_error(doc.parse_artifact, instance, table):
        return None
    indicators = ['total_actual', 'basic_actual', 'project_actual']
    if table.table_code == 'FIN_03_expenditure':
        indicators += ['to_superior_actual', 'operational_actual', 'subsidiary_actual']
    mappings = [m for m in _column_mappings_for_instance(instance, table)
                if m.get('canonical_measure') in indicators]
    if any(sum(m.get('canonical_measure') == field for m in mappings) != 1 for field in indicators):
        return None
    complete = (instance, table, mappings, cells, rows, facts)
    codes = ['total', *sorted(code for code in rows if re.fullmatch(r'\d{3}', code))]
    if 'total' not in rows or len(codes) < 2:
        return None
    data = []
    for code in codes:
        terms, values = [], []
        for indicator in indicators:
            term, error = _select(doc, complete, code, indicator)
            if error or term is None:
                return None
            terms.append(term)
            original = (term.extra_dims or {}).get('evidence_refs') or []
            values.append({'indicator': indicator, 'value': str(term.normalized_value),
                           'operand_ref': None if isinstance(term, NativeBlankTerm) else operand_ref(term, '列间核对'),
                           'blank_observation': blank_observations([term])[0] if isinstance(term, NativeBlankTerm) else None,
                           'page': (term.extra_dims['native_blank_observation']['physical_page']
                                    if isinstance(term, NativeBlankTerm) else original[0].get('physical_page'))})
        total = terms[0].normalized_value
        parts = sum((term.normalized_value for term in terms[1:]), Decimal(0))
        level, diff = classify_amount_diff(total, parts, n_children=len(terms)-1)
        data.append({'code': code, 'name': terms[0].subject_name or code, 'cells': values,
                     'row_difference': str(diff), 'row_status': level})
    columns = []
    for index, indicator in enumerate(indicators):
        actual = Decimal(data[0]['cells'][index]['value'])
        summed = sum((Decimal(row['cells'][index]['value']) for row in data[1:]), Decimal(0))
        level, diff = classify_amount_diff(actual, summed, n_children=len(data)-1)
        columns.append({'indicator': indicator, 'actual': str(actual), 'computed': str(summed),
                        'difference': str(diff), 'status': level})
    return {'table_code': table.table_code, 'file_hash': doc.fact_context.file_hash,
            'fact_version': doc.fact_context.version_token, 'verified': False,
            'rows': data, 'columns': columns}
