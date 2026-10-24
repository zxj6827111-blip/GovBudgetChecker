"""完整逻辑表上的功能分类勾稽及通用表检查的物理片段路由。

金额只取当前 FiscalFact，明细集合取当前逻辑表的行身份。跨页第一页数据
不得当成表头丢弃；无法找到一项的当前事实时，不能用剩余项报确定不平。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .amount_math import classify_amount_diff, compute_dynamic_envelope
from .fact_context import (
    _canonical_budget_stage, _column_mappings_for_instance, _fragment_for_instance,
    operand_rejection_reason,
)
from .native_table_arithmetic import cluster_rounding, finish, receipt
from .native_three_public_ratios import operand_ref
from src.services.fiscal_row_roles import is_index_header
from .native_empty_operands import NativeBlankTerm, blank_term, blank_observations, money_facts
from src.services.fiscal_code_hierarchy import hierarchy_paths, leaf_codes


DETAIL_TABLES = {
    'FIN_02_income': '收入决算表',
    'FIN_03_expenditure': '支出决算表',
    'FIN_05_general_public_expenditure': '一般公共预算财政拨款支出决算表',
}
_CODE = re.compile(r'\d{3}(?:\d{2}){0,2}')
_BILATERAL = {'FIN_01_income_expenditure_total', 'FIN_04_fiscal_grant_total'}
_UNITS = {'元', '万元', '亿元', '千元', '百万元'}


def native_grid_table_identity(doc, page, table_index):
    """恢复物理网格的真实表种；不以页码锚点猜测其他独立表的身份。

    原生 page_tables 按 physical_fragments 的原顺序排列，保留不同抽取方法
    的候选。索引先对应这个排列，再用 fragment_uid 追到完整逻辑实例。
    返回 (table_code, extraction_method)，无法唯一映射则 code=None。
    """
    artifact = getattr(doc, 'parse_artifact', None)
    if artifact is None:
        return None, None
    physical = [t for t in artifact.physical_fragments if t.page_number == page
                and t.extraction_method != 'page_text_fallback']
    if not 0 <= table_index < len(physical):
        return None, None
    fragment = physical[table_index]
    codes = set()
    for instance in artifact.logical_tables:
        merged = _fragment_for_instance(artifact, instance)
        if merged is None or merged.extraction_method == 'page_text_fallback':
            continue
        source_uids = set(instance.fragment_uids) | set(merged.source_fragment_uids)
        source_uids.add(merged.fragment_uid)
        if fragment.fragment_uid in source_uids:
            codes.add(instance.table_code)
    if not codes and fragment.table_code in _BILATERAL | set(DETAIL_TABLES):
        codes.add(fragment.table_code)
    return (next(iter(codes)) if len(codes) == 1 else None), fragment.extraction_method


def is_native_bilateral_summary_fragment(doc, page, table_index):
    """双侧财政总表由专门规则检查，不对其无头续页做通用分项累加。"""
    code, method = native_grid_table_identity(doc, page, table_index)
    return code in _BILATERAL and method not in (None, 'page_text_fallback')


def _cell_id(table, cell):
    return (f'{cell.origin_fragment_uid or table.fragment_uid}:'
            f'r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}'
            f'c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}')


def _row_code(row, measure_start):
    labels = [re.sub(r'\s+', '', str(v or '')) for v in row[:measure_start]]
    if any(v in ('合计', '总计', '本年收入合计', '本年支出合计') for v in labels):
        return 'total'
    numeric_labels = [v for v in labels if v.isdigit()]
    combined = ''.join(numeric_labels)
    return combined if _CODE.fullmatch(combined) else None


def _unrepresented_source_codes(artifact, table, row_codes, measure_start):
    """源文字科目列还有未进入网格的编码时，暴露截行而不拼造金额事实。

    只检查与当前真实编码单元格横坐标一致的、单独完整编码文字块。整数
    金额列、标题年份、脚注不参与判定。可识别遗漏则整表勾稽转人工。
    """
    ranges, code_cells = {}, {}
    for cell in table.cells:
        if (cell.col_idx < measure_start and cell.bbox
                and _CODE.fullmatch(re.sub(r'\s+', '', cell.raw_text or ''))):
            ranges.setdefault(cell.page_number, []).append((cell.bbox[0], cell.bbox[2]))
            code_cells.setdefault(cell.page_number, []).append(cell)
    missing = []
    for block in artifact.text_blocks:
        code = re.sub(r'\s+', '', block.text or '')
        if (not block.bbox or block.page_number not in ranges
                or not _CODE.fullmatch(code) or code in row_codes):
            continue
        center = (block.bbox[0] + block.bbox[2]) / 2
        middle = (block.bbox[1] + block.bbox[3]) / 2
        # 7 位编码在窄格内折成两行（“20701”/“00”），每行是一个文字块；块是某个
        # 已入表编码格的其中一行时，它就是那个完整编码，不是被截掉的科目。
        if any(c.bbox[0] - 1 <= center <= c.bbox[2] + 1 and c.bbox[1] - 1 <= middle <= c.bbox[3] + 1
               and code in c.raw_text.split() and re.sub(r'\s+', '', c.raw_text) in row_codes
               for c in code_cells[block.page_number]):
            continue
        if any(left <= center <= right for left, right in ranges[block.page_number]):
            missing.append(f'第{block.page_number}页原文科目{code}未进入完整逻辑表')
    return list(dict.fromkeys(missing))


def _table_view(doc, table_code):
    artifact, context = getattr(doc, 'parse_artifact', None), getattr(doc, 'fact_context', None)
    title = DETAIL_TABLES[table_code]
    if context is None or artifact is None:
        return None, f'{title}缺少当前版本财政事实或解析产物'
    if context.file_hash != artifact.source_file_hash or context.parse_run_uid != artifact.parse_run_uid:
        return None, f'{title}财政事实与当前解析产物版本不一致'
    instances = [(i, _fragment_for_instance(artifact, i)) for i in artifact.logical_tables
                 if i.table_code == table_code]
    instances = [(i, t) for i, t in instances if t is not None and t.extraction_method != 'page_text_fallback']
    if len(instances) != 1:
        return None, f'{title}未找到唯一完整表实例'
    instance, table = instances[0]
    if instance.identity_conflict:
        return None, f'{title}续表身份冲突：{"；".join(instance.conflict_reasons)}'
    mappings = _column_mappings_for_instance(instance, table)
    # This rule owns the total/basic/project roles. The remaining income and
    # expenditure components have dedicated horizontal arithmetic rules.
    # Blank optional component columns must not expand this rule's scope.
    roles = {'total_actual'} if table_code == 'FIN_02_income' else {
        'total_actual', 'basic_actual', 'project_actual'}
    mappings = [m for m in mappings if m.get('canonical_measure') in roles]
    if not mappings or not any(m['canonical_measure'] == 'total_actual' for m in mappings):
        return None, f'{title}本年合计金额列身份未绑定'
    start = min(int(m['source_col_idx']) for m in mappings)
    source_cells = {(c.row_idx, c.col_idx): c for c in table.cells}
    code_rows, issues = {}, []
    for row_idx, row in enumerate(table.rows):
        if row_idx < table.header_row_count or is_index_header(row):
            continue
        code = _row_code(row, start)
        measure_cells = [source_cells.get((row_idx, int(m['source_col_idx']))) for m in mappings]
        if code is None:
            # Repeated actual headers have no numeric amount cells; unknown
            # amount rows cannot be omitted from a complete sum.
            if any(c is not None and c.numeric_value is not None and not c.is_header for c in measure_cells):
                issues.append(f'{title}第{row_idx + 1}行金额科目编码未唯一识别')
            continue
        if code in code_rows:
            issues.append(f'{title}科目{code}存在重复行身份')
        code_rows[code] = row_idx
    issues += _unrepresented_source_codes(artifact, table, code_rows, start)
    if not code_rows or 'total' not in code_rows:
        issues.append(f'{title}未找到唯一合计行')
    if issues:
        return None, '；'.join(issues)
    facts = [f for f in context.facts if f.table_code == table_code and f.instance_uid == instance.instance_uid]
    return (instance, table, mappings, source_cells, code_rows, facts), None


def _select(doc, view, code, indicator):
    instance, table, mappings, source_cells, code_rows, facts = view
    mapping = next(m for m in mappings if m['canonical_measure'] == indicator)
    cell = source_cells.get((code_rows[code], int(mapping['source_col_idx'])))
    candidates = [f for f in facts if f.subject_code == code and f.indicator == indicator]
    if not candidates and cell is not None and cell.native_blank_verified:
        term = blank_term(doc, table.table_code, indicator, code=code)
        if term is not None:
            return term, None
    if cell is None or not str(cell.raw_text or '').strip() or len(candidates) != 1:
        return None, f'{table.title}科目{code}/{indicator}原始单元格或当前事实缺失、不唯一'
    fact = candidates[0]
    reason = operand_rejection_reason(fact, doc.fact_context.org_name)
    if (fact.unit not in _UNITS or doc.fact_context.fiscal_year is None
            or fact.fiscal_year != doc.fact_context.fiscal_year
            or _canonical_budget_stage(fact.budget_stage) != '决算'
            or fact.period not in (None, '本年')):
        reason = reason or '金额单位、年度、期间或决算阶段未确认'
    matching_refs = [ref for ref in fact.extra_dims.get('evidence_refs', [])
                     if ref.get('source_cell_id') == _cell_id(table, cell)
                     and ref.get('physical_page') == cell.page_number
                     and ref.get('file_hash') == doc.fact_context.file_hash]
    if len(matching_refs) != 1:
        reason = reason or '当前事实未绑定到唯一原页原格'
    return (None, f'{table.title}科目{code}/{indicator}：{reason}') if reason else (fact, None)


def _compare(rule, doc, title, key, code, indicator, parent, children, *, column_total=False):
    facts = [parent] + children
    scope = ('instance_uid', 'org_scope', 'fund_scope', 'fiscal_year', 'budget_stage')
    if any(any(getattr(f, k) != getattr(parent, k) for k in scope) for f in children):
        reason = f'{title}科目{code}/{indicator}操作数口径不一致'
        receipt(rule, doc, key, facts, status='insufficient_data', formula='parent = sum(children)', reason=reason)
        return [], [reason]
    total = sum((f.normalized_value for f in children), Decimal('0'))
    if isinstance(parent, NativeBlankTerm) and total != 0:
        reason = f'{title}科目{code}/{indicator}合计格原件留空而明细有金额'
        receipt(rule, doc, key, facts, status='insufficient_data', formula='parent = sum(children)', reason=reason)
        return [], [reason]
    if all(isinstance(f, NativeBlankTerm) for f in facts):
        receipt(rule, doc, key, facts, status='not_applicable', formula='原件完整网格各项均为空白，无发生额关系')
        return [], []
    envelope = compute_dynamic_envelope([(f.display_precision, f.unit) for f in money_facts(facts)])
    level, diff = classify_amount_diff(parent.normalized_value, total, len(children), envelope=envelope)
    ids, evidence_ids = receipt(rule, doc, key, facts, status='fail' if level == 'mismatch' else 'pass',
                                formula='parent = sum(children) (万元)')
    if level not in ('mismatch', 'rounding_hint'):
        return [], []
    refs = []
    for i, fact in enumerate(facts):
        if isinstance(fact, NativeBlankTerm):
            continue
        ref = operand_ref(fact, 'parent' if i == 0 else 'child')
        ref.update(operand_id=f'{key}:{i}', relation_id=key,
                   evidence_refs=list(fact.extra_dims.get('evidence_refs', [])))
        refs.append(ref)
    pages = sorted({r['physical_page'] for f in facts for r in f.extra_dims.get('evidence_refs', [])
                    if r.get('physical_page')})
    if level == 'rounding_hint':
        message = f'{title}科目{code}/{indicator}与其明细之和差额{diff:.2f}万元，可能为取整误差。'
    elif column_total:
        message = (f'{title}合计行列校验失败({indicator})：合计({parent.normalized_value:.2f}) '
                   f'!= 明细之和({total:.2f})，差额{diff:.2f}万元。')
    else:
        suffix = '' if indicator == 'total_actual' else f'/{indicator}'
        message = (f'层级校验失败({code}{suffix}): 父级金额({parent.normalized_value:.2f}) '
                   f'!= 子级之和({total:.2f})，差额{diff:.2f}万元。')
    location = {'table': title, 'table_code': parent.table_code, 'code': code, 'column': indicator,
                'page': pages[0] if pages else None, 'pages': pages, 'operand_refs': refs,
                'native_blank_sources': blank_observations(facts),
                'fact_context_version_token': doc.fact_context.version_token}
    return [rule._issue(message, location, 'warn' if level == 'mismatch' else 'info',
                        evidence_text=message, check_relation_id=f'{rule.code}:{key}',
                        fact_ids=ids, evidence_ids=evidence_ids)], []


def check_native_detail_tables(rule, doc, *, table_codes=None):
    """V33-120 的原生入口；legacy Document 的原规则实现仍保留。

    每一金额列按完整行集合检查直接父子和总计；总计只累计根科目一次，
    不重复累计其子级。一个操作数不完整只阻断该关系，并留下不足回执。
    """
    issues, unresolved = [], []
    present = {item.table_code for item in getattr(getattr(doc, "parse_artifact", None), "logical_tables", [])}
    targets = list(table_codes) if table_codes is not None else [code for code in DETAIL_TABLES if code in present]
    if not targets:
        return finish(rule, [], ["未找到任何可检查的收入、支出或一般公共预算完整表实例"])
    # Missing-table completeness belongs to the structural checker. Every
    # physically present table still requires its own complete operands.
    for table_code in targets:
        title = DETAIL_TABLES[table_code]
        view, error = _table_view(doc, table_code)
        if error:
            unresolved.append(error)
            receipt(rule, doc, f'{table_code}:complete_table', [], status='insufficient_data',
                    formula='complete logical table', reason=error)
            continue
        _, _, mappings, _, rows, _ = view
        codes = sorted(code for code in rows if code != 'total')
        paths = hierarchy_paths(codes)
        if len(set(paths.values())) != len(paths):
            unresolved.append(f'{title}科目层级存在重复身份')
            continue
        roots = [code for code in codes if not any(paths[code].startswith(paths[p]) and code != p for p in codes)]
        relations = [('total', roots)] + [(code, [c for c in codes if paths[c].startswith(paths[code])
                                                and len(paths[c]) == len(paths[code]) + 2])
                                        for code in codes if len(paths[code]) in (3, 5)]
        for parent_code, child_codes in relations:
            descendants = [c for c in codes if c != parent_code and paths[c].startswith(paths.get(parent_code, 'total'))]
            gap = bool(descendants and any(not any(c == direct or paths[c].startswith(paths[direct]) for direct in child_codes)
                                           for c in descendants))
            if parent_code != 'total' and not child_codes and not gap:
                continue
            for mapping in mappings:
                indicator = mapping['canonical_measure']
                key = f'{table_code}:{parent_code}:{indicator}'
                operands, errors = [], []
                for code in [parent_code] + child_codes:
                    fact, reason = _select(doc, view, code, indicator)
                    if fact is not None:
                        operands.append(fact)
                    if reason:
                        errors.append(reason)
                if gap or not child_codes:
                    errors.append(f'{title}科目{parent_code}/{indicator}直接层级明细不完整')
                if errors:
                    unresolved.extend(errors)
                    receipt(rule, doc, key, operands, status='insufficient_data',
                            formula='parent = sum(complete children)', reason='；'.join(errors))
                    continue
                found, missing = _compare(rule, doc, title, key, parent_code, indicator,
                                          operands[0], operands[1:], column_total=parent_code == 'total')
                issues.extend(found)
                unresolved.extend(missing)
    return finish(rule, cluster_rounding(rule, issues), unresolved)
