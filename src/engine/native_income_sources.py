"""收入表横向关系从有效事实取数，认证空白仅作为本关系的观察项。"""
from .native_hierarchy_checks import (
    _table_view, _compare, _cell_id, _row_missing_operand, _table_page,
    check_native_detail_tables,
)
from .native_table_arithmetic import select_diagnostic, receipt, finish, cluster_rounding
from .diagnostics import blocker, make_diagnosis, merge_diagnoses
from .fact_context import _column_mappings_for_instance
from .rule_outcome import RuleDeferred

CODE = 'FIN_02_income'
SOURCES = ('fiscal_allocation', 'superior_subsidy_income', 'business_income',
           'operational_income', 'subsidiary_income', 'other_income')


def missing_header_findings(rule, doc):
    """只报告有原格空白证明的金额列表头缺失，不按勾稽反推缺失的列名。"""
    artifact = getattr(doc, 'parse_artifact', None)
    if artifact is None:
        return []
    issues = []
    for table in artifact.table_fragments:
        if table.table_code != CODE:
            continue
        from src.services.fiscal_column_identity import canonical_column_mappings
        from src.services.fiscal_table_rules import NINE_TABLE_RULES
        mappings = canonical_column_mappings(table, NINE_TABLE_RULES[CODE])
        mapped = {item['source_col_idx'] for item in mappings}
        labels = {col for item in mappings for col in item.get('source_label_cols', [])}
        numeric = {cell.col_idx for cell in table.cells if cell.row_idx >= table.header_row_count and cell.numeric_value is not None}
        for col in sorted(numeric - mapped - labels):
            blanks = [cell for cell in table.cells if cell.col_idx == col and cell.row_idx < table.header_row_count
                      and cell.bbox and cell.native_blank_verified and not cell.raw_text.strip()]
            if len(blanks) != 1:
                continue
            cell = blanks[0]
            message = f'收入决算表第{col + 1}列表头在原件中为空白，相关金额列身份未确认；未代填列名，请人工复核。'
            issues.append(rule._issue(message, {'table_code': CODE, 'table': table.title,
                'page': cell.page_number, 'bbox': list(cell.bbox), 'header_observations': [{
                    'source_cell_id': f'{table.fragment_uid}:r{cell.row_idx}c{cell.col_idx}',
                    'file_hash': artifact.source_file_hash, 'page': cell.page_number,
                    'bbox': list(cell.bbox), 'raw_text': '', 'native_blank_verified': True}]},
                'manual_review', evidence_text=message))
    return issues


def check_income_sources(rule, doc):
    header_issues = missing_header_findings(rule, doc)
    view, error, view_diagnosis = _table_view(doc, CODE)
    if error:
        return finish(rule, header_issues, [error], [view_diagnosis] if view_diagnosis else [])
    instance, table, _, _, rows, facts = view
    mappings = _column_mappings_for_instance(instance, table)
    roles = [m['canonical_measure'] for m in mappings]
    if any(roles.count(role) != 1 for role in ('total_actual', *SOURCES)):
        diagnosis = make_diagnosis(
            reason_codes=['column_identity_unconfirmed'],
            blocked_by=[blocker('column', '收入表来源列', 'column_identity_unconfirmed')],
            missing_operands=[_row_missing_operand(
                CODE, table.title, _table_page(table), 'income_sources',
                role='column_identity', year=getattr(doc.fact_context, 'fiscal_year', None))],
        )
        return finish(rule, header_issues, ['收入表来源列未绑定至唯一已确认模板'], [diagnosis])
    from .native_column_shift import (
        INCOME_LABELS, column_shift_hint, column_shift_issue, detect_column_shift,
    )
    # 明细行整列错位（源文件漏列/错列）的根因判定：列合计证据一旦成立，
    # 输出一条高危根因问题，并在补核原因里给出材料视角提示。
    columns = [
        (mapping['canonical_measure'],
         mapping.get('source_col_name') or INCOME_LABELS.get(mapping['canonical_measure'],
                                                             mapping['canonical_measure']))
        for mapping in sorted(mappings, key=lambda item: int(item['source_col_idx']))
    ]
    shift = detect_column_shift(columns, facts, org_name=doc.fact_context.org_name)
    shift_note = ('经列合计比对，本表明细行疑似整体错位一列（各列明细之和逐列等于合计行相邻列），'
                  '请优先核对原件列位。' if shift else '')
    issues, unresolved, diagnostics = [], [], []
    for code in rows:
        terms, errors, unknown_cells, relation_diags = [], [], [], []
        for role in ('total_actual', *SOURCES):
            term, error, diagnosis = select_diagnostic(
                doc, facts, role, code=code, allow_native_blank=role in SOURCES
            )
            if error:
                errors.append(error)
                mapping = next(item for item in mappings if item['canonical_measure'] == role)
                cell = next((c for c in table.cells if c.row_idx == rows[code]
                             and c.col_idx == mapping['source_col_idx']), None)
                if diagnosis and 'operand_ambiguous' not in (diagnosis.get('reason_codes') or []):
                    if cell is None:
                        cell_code, dimension, ref = (
                            'column_identity_unconfirmed', 'column',
                            mapping.get('source_col_name') or role,
                        )
                    elif not str(cell.raw_text or '').strip() and not cell.native_blank_verified:
                        cell_code, dimension, ref = (
                            'cell_blank_semantics_unknown', 'cell', _cell_id(table, cell),
                        )
                    else:
                        cell_code, dimension, ref = ('cell_unbound', 'cell', _cell_id(table, cell))
                    diagnosis = merge_diagnoses([diagnosis, make_diagnosis(
                        reason_codes=[cell_code],
                        blocked_by=[blocker(dimension, ref, cell_code)],
                        missing_operands=(diagnosis or {}).get('missing_operands') or [],
                    )])
                if diagnosis:
                    relation_diags.append(diagnosis)
                unknown_cells.append({'indicator': role, 'column_name': mapping.get('source_col_name'),
                    'physical_page': cell.page_number if cell else None,
                    'bbox': list(cell.bbox) if cell and cell.bbox else None,
                    'source_cell_id': _cell_id(table, cell) if cell else None,
                    'raw_text': cell.raw_text if cell else None, 'normalized_value': None,
                    'file_hash': doc.fact_context.file_hash,
                    'native_blank_verified': bool(cell and cell.native_blank_verified), 'reason': error})
            if term is not None:
                terms.append(term)
        key = f'{CODE}:{code}:income_sources'
        if errors:
            unresolved.extend(errors)
            diag = merge_diagnoses(relation_diags)
            if diag:
                diagnostics.append(diag)
            receipt(rule, doc, key, terms, status='insufficient_data',
                    formula='income = sum(sources)', reason='；'.join(errors), diagnosis=diag)
            parent = next((term for term in terms if term.indicator == 'total_actual'), None)
            known = [term for term in terms if term.indicator != 'total_actual']
            from .amount_math import classify_amount_diff
            known_mismatch = parent is not None and known and classify_amount_diff(
                parent.normalized_value, sum(term.normalized_value for term in known), len(known)
            )[0] == 'mismatch'
            if known_mismatch:
                from .native_table_arithmetic import journal
                from .native_three_public_ratios import operand_ref
                ids, evidence_ids = journal(rule, doc, terms)
                message = (f'收入决算表科目{code}：原件合计{parent.normalized_value:.2f}万元，'
                           f'已确认分项合计{sum(term.normalized_value for term in known):.2f}万元；'
                           f'已知数差额{parent.normalized_value - sum(term.normalized_value for term in known):.2f}万元。'
                           '差额可能由空白或未知格承担，请核对原件；本次不作完整勾稽错误认定。'
                           + shift_note + '；'.join(errors))
                issues.append(rule._issue(message, {'table_code': CODE, 'code': code,
                    'unknown_operand_cells': unknown_cells,
                    'operand_refs': [operand_ref(term, 'known') for term in terms]}, 'manual_review',
                    evidence_text=message, fact_ids=ids, evidence_ids=evidence_ids,
                    check_relation_id=f'{rule.code}:{key}:incomplete_clue'))
            continue
        found, missing = _compare(rule, doc, '收入决算表', key, code, 'total_actual',
                                  terms[0], terms[1:])
        issues.extend(found)
        unresolved.extend(missing)
    if shift:
        issues.append(column_shift_issue(rule, doc, table.title, CODE, shift))
        if unresolved:
            unresolved.append(column_shift_hint(table.title, rule.code))
    try:
        # 收入来源列同样需要纵向核对；不能用合计列已通过替代其他列的实际检查。
        issues.extend(check_native_detail_tables(rule, doc, table_codes=(CODE,), include_income_sources=True))
    except RuleDeferred as signal:
        issues.extend(signal.partial_issues)
        unresolved.extend(signal.unresolved_reasons or [signal.detail])
        if getattr(signal, 'reason_codes', None) or getattr(signal, 'blocked_by', None):
            diagnostics.append(make_diagnosis(
                reason_codes=list(getattr(signal, 'reason_codes', []) or []) or ['rule_no_reason'],
                blocked_by=getattr(signal, 'blocked_by', []) or (),
                missing_operands=getattr(signal, 'missing_operands', []) or (),
                candidates=getattr(signal, 'candidates', []) or (),
                next_action=getattr(signal, 'next_action', None),
                root_cause_key=getattr(signal, 'root_cause_key', None),
            ))
    return finish(rule, cluster_rounding(rule, issues), unresolved, diagnostics)
