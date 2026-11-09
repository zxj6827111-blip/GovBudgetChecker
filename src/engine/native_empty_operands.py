"""原生财政网格的空白观察项：用于金额核对，不新增或覆盖原始金额事实。"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.schemas.parse_artifact import CellPresence, FiscalFact
from src.services.fiscal_fact_materializer import FiscalFactMaterializer, measure_fund_scope
from .fact_context import _canonical_budget_stage, _column_mappings_for_instance, _fragment_for_instance, _stage_for_fact, _owner_rejection_reason


# 只允许已知决算网格的金额关系使用原件空白；预算材料、OCR和文本拼表不适用。
_TABLES = {'FIN_01_income_expenditure_total', 'FIN_02_income', 'FIN_03_expenditure',
           'FIN_04_fiscal_grant_total', 'FIN_05_general_public_expenditure',
           'FIN_06_basic_expenditure', 'FIN_07_three_public', 'FIN_08_gov_fund', 'FIN_09_state_capital'}


class NativeBlankTerm(FiscalFact):
    """运行时的无发生额项，raw_value 仍为空；不得物化成财政金额事实。"""


def blank_table_terms(doc, table_code):
    """全空金额表也须逐格证明；没有数据行或夹有横线/未知字样则不推零。

    仅补齐本次计算视图，不加入 fact_context，不创建实际金额事实。
    """
    context, artifact = getattr(doc, 'fact_context', None), getattr(doc, 'parse_artifact', None)
    if context is None or artifact is None or any(f.table_code == table_code for f in context.facts):
        return []
    pairs = [(i, _fragment_for_instance(artifact, i)) for i in getattr(artifact, 'logical_tables', []) if i.table_code == table_code]
    if len(pairs) != 1 or pairs[0][1] is None:
        return []
    instance, table = pairs[0]
    mappings = _column_mappings_for_instance(instance, table)
    if not mappings:
        return []
    materializer = FiscalFactMaterializer(None)
    cells = {(c.row_idx,c.col_idx):c for c in table.cells}
    start = min(int(m['source_col_idx']) for m in mappings)
    terms, seen = [], set()
    for mapping in mappings:
        col = int(mapping['source_col_idx'])
        label_cols = set(mapping.get('source_label_cols') or range(start))
        stage = _stage_for_fact(mapping['canonical_measure'],mapping.get('source_col_name',''),'final','决算')
        for row in range(table.header_row_count,len(table.rows)):
            from .native_grid_roles import merged_note_row
            if merged_note_row(table, row):
                continue
            cell = cells.get((row,col))
            if cell is None or cell.is_header:
                continue
            if cell.raw_text.strip() or cell.numeric_value is not None or not cell.native_blank_verified:
                return []
            labels = [cells.get((row,c)) for c in sorted(label_cols)]
            if any(c is None for c in labels):
                return []
            classification = materializer._extract_classification([
                {'raw_text':c.raw_text,'col_idx':c.col_idx} for c in labels])
            if classification is None and table_code == 'FIN_07_three_public' and start == 0:
                classification = {'classification_code':'total','classification_name':'合计'}
            if classification is None:
                if any(c.raw_text.strip() for c in labels):
                    return []
                continue
            term = blank_term(doc,table_code,mapping['canonical_measure'],
                code=classification['classification_code'],
                names=(''.join((classification['classification_name'] or '').split()),),stage=stage)
            if term is None:
                return []
            sid = term.extra_dims['native_blank_observation']['source_cell_id']
            if sid not in seen:
                seen.add(sid);terms.append(term)
    return terms


def cell_id(table, cell):
    return (f'{cell.origin_fragment_uid or table.fragment_uid}:'
            f'r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}'
            f'c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}')


def money_facts(terms):
    return [term for term in terms if not isinstance(term, NativeBlankTerm)]


def blank_observations(terms):
    return [dict(term.extra_dims['native_blank_observation'])
            for term in terms if isinstance(term, NativeBlankTerm)]


def blank_operand_refs(terms, relation):
    """计算角色单列在观察记录外；原格认证记录保持可逐字复验。"""
    return [{'role': 'parent' if index == 0 else 'child', 'relation_id': relation,
             'operand_id': f'{relation}:{index}',
             'observation': dict(term.extra_dims['native_blank_observation'])}
            for index, term in enumerate(terms) if isinstance(term, NativeBlankTerm)]


def blank_term(doc, table_code, indicator, *, code=None, names=(), stage='决算', calculation=True):
    """未识别格保持缺失；只有原页原格、列身份和行身份均完整才返回空白项。"""
    names = tuple(''.join(str(name or '').split()) for name in names)
    context, artifact = getattr(doc, 'fact_context', None), getattr(doc, 'parse_artifact', None)
    if (table_code not in _TABLES or context is None or artifact is None
            or not context.file_hash or not context.parse_run_uid
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
    if not candidates or (len(candidates) > 1 and code is None and not names):
        return None
    source_cells = {(c.row_idx, c.col_idx): c for c in table.cells}
    if len(source_cells) != len(table.cells):
        return None
    start = min(int(m['source_col_idx']) for m in mappings)
    materializer = FiscalFactMaterializer(None)
    matches = []
    for mapping in candidates:
        col = int(mapping['source_col_idx'])
        label_cols = mapping.get('source_label_cols')
        # 经济分类表左右各有一条“编码｜名称｜决算数”车道；同指标多列时
        # 必须由字面列身份提供标签范围，先匹配科目，不能默认拿左车道。
        if len(candidates) > 1 and not label_cols:
            return None
        labels = set(label_cols if label_cols is not None else range(start))
        for row_idx in range(table.header_row_count, len(table.rows)):
            row = [c for c in table.cells if c.row_idx == row_idx]
            classification = materializer._extract_classification(
                [{'raw_text': c.raw_text, 'col_idx': c.col_idx}
                 for c in sorted(row, key=lambda c: c.col_idx) if c.col_idx in labels])
            if classification is None and table_code == 'FIN_07_three_public' and start == 0:
                classification = {'classification_code': 'total', 'classification_name': '合计'}
            if classification is None:
                continue
            subject = classification['classification_code']
            name = ''.join((classification['classification_name'] or '').split())
            if (code is not None and subject != code) or (names and name not in names):
                continue
            if classification.get('classification_conflict'):
                return None
            matches.append((row_idx, subject, name, source_cells.get((row_idx, col)), mapping))
    if len(matches) != 1:
        return None
    row_idx, subject, name, cell, mapping = matches[0]
    col = int(mapping['source_col_idx'])
    if (cell is None or not cell.native_blank_verified or cell.is_header
            or cell.raw_text.strip() or cell.presence != CellPresence.BLANK or cell.numeric_value is not None
            or not cell.bbox or cell.extraction_method not in {'lines', 'lines_strict'}
            or (cell.unit_hint and cell.unit_hint != table.unit_hint)):
        return None
    # 事实或人工校正已占据该字段时不绕过它；低置信/冲突事实也不可用空白兜底。
    sid = cell_id(table, cell)
    for fact in context.facts:
        if (fact.table_code != table_code or fact.instance_uid != instance.instance_uid
                or fact.indicator != indicator
                or _canonical_budget_stage(fact.budget_stage) != _canonical_budget_stage(stage)):
            continue
        bound_ids = {ref.get('source_cell_id') for ref in (fact.extra_dims or {}).get('evidence_refs', [])
                     if ref.get('source_cell_id')}
        # 同一原行的另一条已绑定车道不占用本格；没有原格身份的同排事实仍属冲突。
        if sid in bound_ids or (fact.row_order == row_idx and (
                len(candidates) == 1 or fact.subject_code == subject or not bound_ids)):
            return None
    from .cell_value_policy import interpret_cell_value
    interpreted, _presence, convention = interpret_cell_value(
        cell.raw_text, native_blank_verified=cell.native_blank_verified,
        region=context.org_name, year=context.fiscal_year, table_code=table_code, row=row_idx, column=col)
    narrative_sources = None
    if interpreted is None and not cell.raw_text.strip():
        # 说明里明确写“该项决算为 0”时，为同身份空表格作证；原格仍保持空白。
        from .narrative_zero_evidence import narrative_zero_sources

        narrative_sources = narrative_zero_sources(
            doc, table_code=table_code, indicator=indicator, stage=stage)
        if narrative_sources:
            interpreted, convention = Decimal('0'), 'narrative_explicit_zero'
    if calculation and interpreted is None:
        return None
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
                   'budget_stage': stage, 'basis': 'verified_native_grid_blank_with_scoped_convention',
                   'convention': convention, 'calculation': calculation}
    if narrative_sources:
        observation['narrative_sources'] = list(narrative_sources)
    return NativeBlankTerm(fact_uid='', indicator=indicator, raw_value='', unit=table.unit_hint,
                          normalized_value=Decimal('0') if calculation else None, org_name=context.org_name,
                          org_scope=next((f.org_scope for f in context.facts if f.instance_uid == instance.instance_uid), None),
                          fiscal_year=context.fiscal_year, period='本年', budget_stage=stage,
                          fund_scope=measure_fund_scope(table_code, table.title,mapping.get('source_col_name'))[0], subject_code=subject,
                          subject_name=name, table_code=table_code, instance_uid=instance.instance_uid,
                          row_order=row_idx, recognition_status='observed_blank',
                          extra_dims={'native_blank_observation': observation})


def independent_blank_zero(doc, term):
    """原格约定独立证明零值；不能用待核对的说明金额给自身作证。"""
    if not isinstance(term, NativeBlankTerm) or term.normalized_value != Decimal('0'):
        return False
    observation = term.extra_dims.get('native_blank_observation') or {}
    return bool(isinstance(observation.get('convention'), dict)
                and observation['convention'].get('id')
                and valid_blank_observation(doc.parse_artifact, doc.fact_context, observation,
                                            getattr(doc, 'page_texts', None)))


def verified_blank_zero_by_code(doc, table_code, page, code):
    """旧结构化表（单元格无坐标）上的空白金额格，回到原件网格核实。

    在逻辑表 ``table_code`` 第 ``page`` 页里找科目编码恰为 ``code`` 的唯一一格，
    取同一行“编码｜名称｜金额”版式中名称右侧的金额格（表头须含“决算”）。
    仅当该格经网格认证为空白、且命中空白约定（U-5）时返回观察记录；
    任何一步不唯一或不成立都返回 None，调用方保持未知。
    """
    import re

    context, artifact = getattr(doc, 'fact_context', None), getattr(doc, 'parse_artifact', None)
    code = str(code or '').strip()
    if (context is None or artifact is None or not code or not page
            or context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid
            or not context.org_name or context.fiscal_year is None):
        return None

    def norm(text):
        return re.sub(r'\s+', '', str(text or ''))

    hits = []
    for table in getattr(artifact, 'table_fragments', []) or []:
        if table.table_code != table_code or table.extraction_method not in {'lines', 'lines_strict'}:
            continue
        cells = {(c.row_idx, c.col_idx): c for c in table.cells}
        for cell in table.cells:
            if cell.page_number == page and not cell.is_header and norm(cell.raw_text) == code:
                hits.append((table, cells, cell))
    if len(hits) != 1:
        return None
    table, cells, code_cell = hits[0]
    name_cell = cells.get((code_cell.row_idx, code_cell.col_idx + 1))
    amount_col = code_cell.col_idx + 2
    target = cells.get((code_cell.row_idx, amount_col))
    header = ''.join(norm(row[amount_col]) for row in table.rows[:table.header_row_count]
                     if amount_col < len(row))
    if (name_cell is None or not norm(name_cell.raw_text) or target is None or '决算' not in header
            or target.is_header or target.raw_text.strip() or target.numeric_value is not None
            or not target.native_blank_verified or not target.bbox
            or target.page_number != page):
        return None
    sid = cell_id(table, target)
    physical = [c for fragment in getattr(artifact, 'physical_fragments', [])
                for c in fragment.cells if cell_id(fragment, c) == sid]
    if len(physical) != 1 or not physical[0].native_blank_verified or physical[0].raw_text.strip():
        return None
    from .cell_value_policy import interpret_cell_value
    interpreted, _presence, convention = interpret_cell_value(
        '', native_blank_verified=True, region=context.org_name, year=context.fiscal_year,
        table_code=table_code, row=target.row_idx, column=amount_col)
    if interpreted is None or not convention:
        return None
    return {'source_cell_id': sid, 'file_hash': context.file_hash,
            'parse_run_uid': context.parse_run_uid, 'physical_page': target.page_number,
            'bbox': list(target.bbox), 'presence': 'blank', 'raw_text': '',
            'table_code': table_code, 'subject_code': code, 'subject_name': norm(name_cell.raw_text),
            'budget_stage': '决算', 'basis': 'verified_native_grid_blank_with_scoped_convention',
            'convention': convention.get('id') if isinstance(convention, dict) else convention,
            'calculation': True}


def verified_blank_column(doc, table_code, indicator):
    """某金额列在原件网格中整列都是认证空白、且逐格命中空白约定（U-5）时，
    返回各格观察记录（该列明确为 0）；列身份不唯一、任一格有字或未认证则返回 None。"""
    context, artifact = getattr(doc, 'fact_context', None), getattr(doc, 'parse_artifact', None)
    if (context is None or artifact is None
            or context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid
            or not context.org_name or context.fiscal_year is None):
        return None
    pairs = [(instance, _fragment_for_instance(artifact, instance))
             for instance in getattr(artifact, 'logical_tables', []) if instance.table_code == table_code]
    pairs = [(instance, table) for instance, table in pairs
             if table is not None and table.extraction_method in {'lines', 'lines_strict'}]
    if len(pairs) != 1:
        return None
    instance, table = pairs[0]
    if instance.identity_conflict or len({len(row) for row in table.rows}) != 1:
        return None
    columns = {int(m['source_col_idx']) for m in _column_mappings_for_instance(instance, table)
               if m.get('canonical_measure') == indicator}
    if len(columns) != 1:
        return None
    col = columns.pop()
    if any(f.table_code == table_code and f.instance_uid == instance.instance_uid
           and f.indicator == indicator for f in context.facts):
        return None
    from .cell_value_policy import interpret_cell_value
    observations = []
    for cell in table.cells:
        if cell.col_idx != col or cell.is_header or cell.row_idx < table.header_row_count:
            continue
        if cell.raw_text.strip() or cell.numeric_value is not None:
            return None
        zero, _presence, convention = interpret_cell_value(
            '', native_blank_verified=cell.native_blank_verified, region=context.org_name,
            year=context.fiscal_year, table_code=table_code, row=cell.row_idx, column=col)
        if zero is None or not cell.bbox:
            return None
        observations.append({'source_cell_id': cell_id(table, cell), 'file_hash': context.file_hash,
                             'parse_run_uid': context.parse_run_uid, 'physical_page': cell.page_number,
                             'bbox': list(cell.bbox), 'presence': 'blank', 'raw_text': '',
                             'table_code': table_code, 'instance_uid': instance.instance_uid,
                             'indicator': indicator, 'budget_stage': '决算',
                             'basis': 'verified_native_grid_blank_with_scoped_convention',
                             'convention': convention.get('id') if isinstance(convention, dict) else convention,
                             'calculation': True})
    return observations or None


def valid_blank_observation(artifact, context, observation, page_texts=None):
    term = blank_term(SimpleNamespace(parse_artifact=artifact, fact_context=context,
                                      page_texts=list(page_texts or [])),
                      observation.get('table_code'), observation.get('indicator'),
                      code=observation.get('subject_code'), names=(observation.get('subject_name'),),
                      stage=observation.get('budget_stage'), calculation=observation.get('calculation', True))
    return term is not None and blank_observations([term])[0] == observation
