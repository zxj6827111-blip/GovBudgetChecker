"""并排列金额只消费当前原格证据；不得把展示数据当作财政事实。"""
from copy import deepcopy
from decimal import Decimal


def bind_expenditure_context(value, *, context, artifact, pages, file_hash):
    from src.engine.native_empty_operands import valid_blank_observation
    from src.engine.amount_math import classify_amount_diff
    from .rule_source_binding import _operand_fact, _current_cell_catalog, _current_cell_evidence

    if (not isinstance(value, dict) or value.get('file_hash') != file_hash
            or value.get('fact_version') != context.version_token
            or value.get('table_code') not in {'FIN_03_expenditure', 'FIN_05_general_public_expenditure'}):
        return None
    result = deepcopy(value)
    rows = result.get('rows') or []
    if not rows or rows[0].get('code') != 'total' or len({row.get('code') for row in rows}) != len(rows):
        return None
    expected = ['total_actual', 'basic_actual', 'project_actual']
    if result['table_code'] == 'FIN_03_expenditure':
        expected += ['to_superior_actual', 'operational_actual', 'subsidiary_actual']
    from src.engine.fact_context import _fragment_for_instance, _column_mappings_for_instance, _canonical_budget_stage
    from src.engine.native_hierarchy_checks import _row_code
    instances = [i for i in artifact.logical_tables if i.table_code == result['table_code']]
    if len(instances) != 1 or instances[0].identity_conflict:
        return None
    table = _fragment_for_instance(artifact, instances[0])
    if table is None:
        return None
    mappings = _column_mappings_for_instance(instances[0], table)
    start = min(int(m['source_col_idx']) for m in mappings)
    source_codes = {_row_code(row, start) for row in table.rows[table.header_row_count:]}
    class_codes = {code for code in source_codes if code and (code == 'total' or len(code) == 3)}
    if {row['code'] for row in rows} != class_codes:
        return None
    catalog = _current_cell_catalog(artifact)
    for row in rows:
        if [cell.get('indicator') for cell in row.get('cells', [])] != expected:
            return None
        for cell in row['cells']:
            observation = cell.get('blank_observation')
            ref = cell.get('operand_ref')
            if observation:
                if (observation.get('table_code') != result['table_code']
                        or observation.get('subject_code') != row['code']
                        or observation.get('indicator') != cell['indicator']
                        or observation.get('calculation') is not True
                        or not valid_blank_observation(artifact, context, observation, pages)):
                    return None
                source = {'physical_page': observation['physical_page'], 'bbox': observation['bbox'],
                          'source_cell_id': observation['source_cell_id']}
                actual = Decimal(0)
            else:
                if not isinstance(ref, dict):
                    return None
                fact, error = _operand_fact(ref, context.facts, context)
                if (error or fact.table_code != result['table_code'] or fact.subject_code != row['code']
                        or fact.indicator != cell['indicator'] or fact.fiscal_year != context.fiscal_year
                        or _canonical_budget_stage(fact.budget_stage) != '决算'
                        or fact.period not in (None, '本年')):
                    return None
                sources = _current_cell_evidence([fact], artifact, pages, file_hash, cell_catalog=catalog)
                if len(sources) != 1:
                    return None
                source, actual = sources[0], fact.normalized_value
            if actual != Decimal(str(cell.get('value'))):
                return None
            if cell['indicator'] == 'total_actual':
                row['name'] = observation['subject_name'] if observation else fact.subject_name
            cell.update(page=source['physical_page'], bbox=list(source['bbox']),
                        source_cell_id=source['source_cell_id'], blank_as_zero=bool(observation),
                        key=f"expenditure:{result['table_code']}:{row['code']}:{cell['indicator']}")
        lhs = Decimal(row['cells'][0]['value'])
        rhs = sum((Decimal(cell['value']) for cell in row['cells'][1:]), Decimal(0))
        level, difference = classify_amount_diff(lhs, rhs, n_children=len(expected)-1)
        row.update(row_status=level, row_difference=str(difference))
    columns = []
    for index, indicator in enumerate(expected):
        actual = Decimal(rows[0]['cells'][index]['value'])
        computed = sum((Decimal(row['cells'][index]['value']) for row in rows[1:]), Decimal(0))
        level, difference = classify_amount_diff(actual, computed, n_children=len(rows)-1)
        columns.append(dict(indicator=indicator, actual=str(actual), computed=str(computed),
                            status=level, difference=str(difference)))
    result.update(verified=True, columns=columns)
    return result
