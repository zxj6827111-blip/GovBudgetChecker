"""原生财政网格的空白观察项：仅用于加法，不新增或覆盖原始金额事实。"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.schemas.parse_artifact import CellPresence, FiscalFact
from src.services.fiscal_fact_materializer import FiscalFactMaterializer, measure_fund_scope
from .fact_context import _canonical_budget_stage, _column_mappings_for_instance, _fragment_for_instance, _stage_for_fact, _owner_rejection_reason


# 只允许已知决算网格的加法关系使用原件空白；预算、OCR和文本拼表不适用。
_TABLES = {'FIN_01_income_expenditure_total', 'FIN_02_income', 'FIN_03_expenditure',
           'FIN_04_fiscal_grant_total', 'FIN_05_general_public_expenditure', 'FIN_07_three_public'}


class NativeBlankTerm(FiscalFact):
    """运行时的无发生额项，raw_value 仍为空；不得物化成财政金额事实。"""


def cell_id(table, cell):
    return (f'{cell.origin_fragment_uid or table.fragment_uid}:'
            f'r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}'
            f'c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}')


def money_facts(terms):
    return [term for term in terms if not isinstance(term, NativeBlankTerm)]


def blank_observations(terms):
    return [dict(term.extra_dims['native_blank_observation'])
            for term in terms if isinstance(term, NativeBlankTerm)]


def blank_term(doc, table_code, indicator, *, code=None, names=(), stage='决算'):
    """未识别格保持缺失；只有原页原格、列身份和行身份均完整才返回空白项。"""
    context, artifact = getattr(doc, 'fact_context', None), getattr(doc, 'parse_artifact', None)
    if (table_code not in _TABLES or context is None or artifact is None
            or context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid
            or not context.org_name or context.fiscal_year is None or context.report_kind != 'final'):
        return None
    pairs = [(instance, _fragment_for_instance(artifact, instance))
             for instance in getattr(artifact, 'logical_tables', []) if instance.table_code == table_code]
    pairs = [(instance, table) for instance, table in pairs
             if table is not None and table.extraction_method in {'lines', 'lines_strict'}]
    if len(pairs) != 1:
        return None
    instance, table = pairs[0]
    if (instance.identity_conflict or table.confidence < .8
            or (instance.fiscal_year is not None and instance.fiscal_year != context.fiscal_year)
            or len({len(row) for row in table.rows}) != 1
            or table.unit_hint not in {'元', '万元', '亿元', '千元', '百万元'}
            or _owner_rejection_reason(SimpleNamespace(org_name=instance.org_name,
                 extra_dims={'source_table_title': table.title}), context.org_name)):
        return None
    mappings = _column_mappings_for_instance(instance, table)
    candidates = [mapping for mapping in mappings if mapping.get('canonical_measure') == indicator
                  and _canonical_budget_stage(_stage_for_fact(indicator, mapping.get('source_col_name', ''),
                                               'final', '决算')) == _canonical_budget_stage(stage)]
    if len(candidates) != 1:
        return None
    mapping = candidates[0]
    col = int(mapping['source_col_idx'])
    source_cells = {(c.row_idx, c.col_idx): c for c in table.cells}
    if len(source_cells) != len(table.cells):
        return None
    start = min(int(m['source_col_idx']) for m in mappings)
    label_cols = mapping.get('source_label_cols')
    labels = set(label_cols if label_cols is not None else range(start))
    materializer = FiscalFactMaterializer(None)
    matches = []
    for row_idx in range(table.header_row_count, len(table.rows)):
        row = [c for c in table.cells if c.row_idx == row_idx]
        classification = materializer._extract_classification(
            [{'raw_text': c.raw_text} for c in sorted(row, key=lambda c: c.col_idx) if c.col_idx in labels])
        if classification is None and table_code == 'FIN_07_three_public' and start == 0:
            classification = {'classification_code': 'total', 'classification_name': '合计'}
        if classification is None:
            continue
        subject = classification['classification_code']
        name = ''.join((classification['classification_name'] or '').split())
        if (code is not None and subject != code) or (names and name not in names):
            continue
        matches.append((row_idx, subject, name, source_cells.get((row_idx, col))))
    if len(matches) != 1:
        return None
    row_idx, subject, name, cell = matches[0]
    if (cell is None or not cell.native_blank_verified or cell.is_header
            or cell.raw_text.strip() or cell.presence != CellPresence.BLANK or cell.numeric_value is not None
            or not cell.bbox or cell.extraction_method not in {'lines', 'lines_strict'}
            or (cell.unit_hint and cell.unit_hint != table.unit_hint)):
        return None
    # 事实或人工校正已占据该字段时不绕过它；低置信/冲突事实也不可用空白兜底。
    if any(f.table_code == table_code and f.instance_uid == instance.instance_uid
           and f.row_order == row_idx and f.indicator == indicator
           and _canonical_budget_stage(f.budget_stage) == _canonical_budget_stage(stage) for f in context.facts):
        return None
    sid = cell_id(table, cell)
    physical = [(fragment, c) for fragment in getattr(artifact, 'physical_fragments', [])
                for c in fragment.cells if cell_id(fragment, c) == sid]
    if len(physical) != 1:
        return None
    _, original = physical[0]
    if (not original.native_blank_verified or original.raw_text.strip()
            or original.bbox != cell.bbox or original.page_number != cell.page_number):
        return None
    observation = {'source_cell_id': sid, 'file_hash': context.file_hash,
                   'parse_run_uid': context.parse_run_uid, 'physical_page': cell.page_number,
                   'bbox': list(cell.bbox), 'presence': 'blank', 'raw_text': '',
                   'table_code': table_code, 'instance_uid': instance.instance_uid,
                   'indicator': indicator, 'subject_code': subject, 'subject_name': name,
                   'budget_stage': stage, 'basis': 'verified_native_grid_blank_no_recorded_amount'}
    return NativeBlankTerm(fact_uid='', indicator=indicator, raw_value='', unit=table.unit_hint,
                          normalized_value=Decimal('0'), org_name=context.org_name,
                          org_scope=next((f.org_scope for f in context.facts if f.instance_uid == instance.instance_uid), None),
                          fiscal_year=context.fiscal_year, period='本年', budget_stage=stage,
                          fund_scope=measure_fund_scope(table_code, table.title,mapping.get('source_col_name'))[0], subject_code=subject,
                          subject_name=name, table_code=table_code, instance_uid=instance.instance_uid,
                          row_order=row_idx, recognition_status='observed_blank',
                          extra_dims={'native_blank_observation': observation})


def valid_blank_observation(artifact, context, observation):
    term = blank_term(SimpleNamespace(parse_artifact=artifact, fact_context=context),
                      observation.get('table_code'), observation.get('indicator'),
                      code=observation.get('subject_code'), names=(observation.get('subject_name'),),
                      stage=observation.get('budget_stage'))
    return term is not None and blank_observations([term])[0] == observation
