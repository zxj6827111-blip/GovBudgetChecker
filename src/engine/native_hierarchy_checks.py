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
from .native_empty_operands import NativeBlankTerm, blank_term, blank_observations, blank_operand_refs, money_facts
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


def _gap(doc, text, *, codes, blockers=(), missing=(), candidates=()):
    """DH-02：登记表/行/列/格级卡点；字段不合法时静默跳过，不影响结论。"""
    from .diagnostic_fields import record_gap

    record_gap(doc, text, reason_codes=list(codes), blocked_by=list(blockers),
               missing_operands=list(missing), candidates=list(candidates))


def _table_blocker(title, reason_code, ref):
    from .diagnostic_fields import blocker

    return blocker(dimension='instance_uid', ref=f'{title}:{ref}', reason_code=reason_code)


def _table_view(doc, table_code, *, include_income_sources=False):
    artifact, context = getattr(doc, 'parse_artifact', None), getattr(doc, 'fact_context', None)
    title = DETAIL_TABLES[table_code]
    if context is None or artifact is None:
        reason = f'{title}缺少当前版本财政事实或解析产物'
        _gap(doc, reason, codes=['table_missing'],
             blockers=[_table_blocker(title, 'table_missing', 'no-artifact')])
        return None, reason
    if context.file_hash != artifact.source_file_hash or context.parse_run_uid != artifact.parse_run_uid:
        reason = f'{title}财政事实与当前解析产物版本不一致'
        _gap(doc, reason, codes=['table_mapping_incomplete'],
             blockers=[_table_blocker(title, 'table_mapping_incomplete', 'parse-version')])
        return None, reason
    instances = [(i, _fragment_for_instance(artifact, i)) for i in artifact.logical_tables
                 if i.table_code == table_code]
    instances = [(i, t) for i, t in instances if t is not None and t.extraction_method != 'page_text_fallback']
    if len(instances) != 1:
        reason = f'{title}未找到唯一完整表实例'
        _gap(doc, reason, codes=['table_missing'],
             blockers=[_table_blocker(title, 'table_missing', f'{len(instances)}个候选实例')])
        return None, reason
    instance, table = instances[0]
    if instance.identity_conflict:
        reason = f'{title}续表身份冲突：{"；".join(instance.conflict_reasons)}'
        _gap(doc, reason, codes=['table_mapping_incomplete'],
             blockers=[_table_blocker(title, 'table_mapping_incomplete', 'identity-conflict')])
        return None, reason
    mappings = _column_mappings_for_instance(instance, table)
    # This rule owns the total/basic/project roles. The remaining income and
    # expenditure components have dedicated horizontal arithmetic rules.
    # Blank optional component columns must not expand this rule's scope.
    roles = {'total_actual'} if table_code == 'FIN_02_income' else {
        'total_actual', 'basic_actual', 'project_actual'}
    if table_code == 'FIN_02_income' and include_income_sources:
        roles.update({'fiscal_allocation', 'superior_subsidy_income', 'business_income',
                      'operational_income', 'subsidiary_income', 'other_income'})
    mappings = [m for m in mappings if m.get('canonical_measure') in roles]
    if not mappings or not any(m['canonical_measure'] == 'total_actual' for m in mappings):
        reason = f'{title}本年合计金额列身份未绑定'
        from .diagnostic_fields import blocker
        _gap(doc, reason, codes=['column_identity_unconfirmed'],
             blockers=[blocker(dimension='column', ref=f'{title}:total_actual', reason_code='column_identity_unconfirmed')])
        return None, reason
    start = min(int(m['source_col_idx']) for m in mappings)
    source_cells = {(c.row_idx, c.col_idx): c for c in table.cells}
    code_rows, issues = {}, []
    row_signatures = {}
    for row_idx, row in enumerate(table.rows):
        if row_idx < table.header_row_count or is_index_header(row):
            continue
        code = _row_code(row, start)
        measure_cells = [source_cells.get((row_idx, int(m['source_col_idx']))) for m in mappings]
        if code is None:
            # Repeated actual headers have no numeric amount cells; unknown
            # amount rows cannot be omitted from a complete sum.
            if any(c is not None and c.numeric_value is not None and not c.is_header for c in measure_cells):
                issue_text = f'{title}第{row_idx + 1}行金额科目编码未唯一识别'
                issues.append(issue_text)
                from .diagnostic_fields import blocker
                _gap(doc, issue_text, codes=['row_level_unresolved'],
                     blockers=[blocker(dimension='row', ref=f'{title}:第{row_idx + 1}行',
                                       reason_code='row_level_unresolved')])
            continue
        signature = tuple(re.sub(r'\s+', '', str(value or '')) for value in row)
        if code in code_rows:
            # 同一科目编码连续重复且整行文字完全相同＝排版重复印了一行
            # （2026-11-01 实样：第 8、10 页把 21211 行印了两遍，各列数字一致）。
            # 这种重复不携带第二个业务项，按一行参与合计；整行不一致的重复仍是
            # 身份歧义，必须转人工，不能挑一行当答案。
            if row_signatures[code] != signature:
                issue_text = f'{title}科目{code}存在重复行身份'
                issues.append(issue_text)
                from .diagnostic_fields import blocker
                _gap(doc, issue_text, codes=['row_identity_duplicate'],
                     blockers=[blocker(dimension='row', ref=f'{title}:科目{code}',
                                       reason_code='row_identity_duplicate')])
            continue
        row_signatures[code] = signature
        code_rows[code] = row_idx
    issues += _unrepresented_source_codes(artifact, table, code_rows, start)
    if not code_rows or 'total' not in code_rows:
        issue_text = f'{title}未找到唯一合计行'
        issues.append(issue_text)
        from .diagnostic_fields import blocker
        _gap(doc, issue_text, codes=['row_code_missing'],
             blockers=[blocker(dimension='row', ref=f'{title}:合计行', reason_code='row_code_missing')])
    if issues:
        reason = '；'.join(issues)
        for issue_text in issues:
            if '未进入完整逻辑表' in issue_text:
                code = issue_text.rsplit('科目', 1)[-1].split('未进入', 1)[0]
                _gap(doc, issue_text, codes=['table_mapping_incomplete'],
                     blockers=[_table_blocker(title, 'table_mapping_incomplete', f'科目{code}')])
        return None, reason
    facts = [f for f in context.facts if f.table_code == table_code and f.instance_uid == instance.instance_uid]
    return (instance, table, mappings, source_cells, code_rows, facts), None


def _record_cell_gap(doc, table, cell, candidates, reason, *, code, indicator):
    """科目行的事实缺失/多候选：位置来自原格或候选事实。"""
    from .diagnostic_fields import blocker, table_candidate

    table_code = getattr(table, 'table_code', None)
    if not table_code:
        # 表格对象缺表代码时不登记：位置无法成立，不能编造。
        return
    if len(candidates) > 1:
        locations, seen = [], set()
        for fact in candidates:
            for ref in (fact.extra_dims or {}).get('evidence_refs', []) or []:
                cell_id, page = ref.get('source_cell_id'), ref.get('physical_page')
                if not cell_id or not isinstance(page, int) or page < 1:
                    continue
                key = (fact.table_code, page, cell_id)
                if key in seen:
                    continue
                seen.add(key)
                locations.append(table_candidate(table_code=fact.table_code, page=page,
                                                 cell_id=str(cell_id), value=str(fact.normalized_value)))
        if len(locations) >= 2:
            _gap(doc, reason, codes=['operand_ambiguous', 'cell_unbound'], candidates=locations,
                 blockers=[blocker(dimension='cell', ref=f'{table.title}:{code}:{indicator}:多候选',
                                   reason_code='cell_unbound')])
            return
    from .diagnostic_fields import table_page

    _gap(doc, reason, codes=['operand_missing'], missing=[{
        'source': 'table', 'table_code': table_code, 'role': f'{indicator}/决算操作数',
        'page': cell.page_number if cell is not None else table_page(doc, table_code),
        'row_key': code, 'column': indicator, 'fund_scope': None, 'stage': '决算',
        'year': doc.fact_context.fiscal_year if isinstance(getattr(doc.fact_context, 'fiscal_year', None), int) else None,
    }])


def _select(doc, view, code, indicator):
    instance, table, mappings, source_cells, code_rows, facts = view
    mapping = next(m for m in mappings if m['canonical_measure'] == indicator)
    cell = source_cells.get((code_rows[code], int(mapping['source_col_idx'])))
    candidates = [f for f in facts if f.subject_code == code and f.indicator == indicator]
    if cell is not None and cell.native_blank_verified:
        # 全空表的运行时观察项也要按原格配对，左右合计行都叫 total 时不能按编码挑选。
        from .native_empty_operands import valid_blank_observation
        observed = [f for f in candidates if isinstance(f, NativeBlankTerm)
                    and f.extra_dims['native_blank_observation']['source_cell_id'] == _cell_id(table, cell)]
        if len(observed) == 1 and valid_blank_observation(doc.parse_artifact, doc.fact_context,
                observed[0].extra_dims['native_blank_observation'], getattr(doc, 'page_texts', None)):
            return observed[0], None
    if not candidates and cell is not None and cell.native_blank_verified:
        term = blank_term(doc, table.table_code, indicator, code=code)
        if term is not None:
            return term, None
    if len(candidates) > 1 and cell is not None:
        # 同一原行被排版重复时会有多条事实；只认绑定到本行这一格的唯一事实，
        # 不按顺序挑第一条。
        cell_id = _cell_id(table, cell)
        bound = [f for f in candidates
                 if any(ref.get('source_cell_id') == cell_id
                        for ref in (f.extra_dims or {}).get('evidence_refs', []))]
        if len(bound) == 1:
            candidates = bound
    if cell is None or not str(cell.raw_text or '').strip() or len(candidates) != 1:
        reason = f'{table.title}科目{code}/{indicator}原始单元格或当前事实缺失、不唯一'
        _record_cell_gap(doc, table, cell, candidates, reason, code=code, indicator=indicator)
        return None, reason
    fact = candidates[0]
    reason = operand_rejection_reason(fact, doc.fact_context.org_name)
    if (fact.unit not in _UNITS or doc.fact_context.fiscal_year is None
            or fact.fiscal_year != doc.fact_context.fiscal_year
            or _canonical_budget_stage(fact.budget_stage) != '决算'
            or fact.period not in (None, '本年')):
        reason = reason or '金额单位、年度、期间或决算阶段未确认'
        from .native_table_arithmetic import record_operand_rejection
        record_operand_rejection(doc, fact, reason, indicator=indicator, stage='决算')
    matching_refs = [ref for ref in fact.extra_dims.get('evidence_refs', [])
                     if ref.get('source_cell_id') == _cell_id(table, cell)
                     and ref.get('physical_page') == cell.page_number
                     and ref.get('file_hash') == doc.fact_context.file_hash]
    if len(matching_refs) != 1:
        reason = reason or '当前事实未绑定到唯一原页原格'
        from .diagnostic_fields import blocker
        _gap(doc, f'{table.title}科目{code}/{indicator}：{reason}',
             codes=['cell_unbound', 'operand_missing'],
             blockers=[blocker(dimension='cell', ref=_cell_id(table, cell) if cell is not None else f'{table.title}:{code}',
                               reason_code='cell_unbound')],
             missing=[{
                 'source': 'table', 'table_code': table.table_code,
                 'role': f'{indicator}/决算操作数',
                 'page': cell.page_number if cell is not None else None,
                 'row_key': code, 'column': indicator,
                 'fund_scope': None, 'stage': '决算',
                 'year': doc.fact_context.fiscal_year if isinstance(getattr(doc.fact_context, 'fiscal_year', None), int) else None,
             }])
    return (None, f'{table.title}科目{code}/{indicator}：{reason}') if reason else (fact, None)


def _compare(rule, doc, title, key, code, indicator, parent, children, *, column_total=False, table_view=None):
    facts = [parent] + children
    scope = ('instance_uid', 'org_scope', 'fund_scope', 'fiscal_year', 'budget_stage')
    if any(any(getattr(f, k) != getattr(parent, k) for k in scope) for f in children):
        reason = f'{title}科目{code}/{indicator}操作数口径不一致'
        receipt(rule, doc, key, facts, status='insufficient_data', formula='parent = sum(children)', reason=reason)
        return [], [reason]
    total = sum((f.normalized_value for f in children), Decimal('0'))
    # 父格已通过原格认证与 U-5 取零，完整子项仍须按同一容差勾稽。
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
    if column_total and indicator in {'basic_actual', 'project_actual'}:
        # 展示全部类级原行，包括认证空白零，避免把另一金额列的有数误解为本列漏算。
        column_name = '基本支出' if indicator == 'basic_actual' else '项目支出'
        message += f'本列（{column_name}）逐类复算：' + ' + '.join(
            f'{f.subject_code}={f.normalized_value:.2f}' for f in children) + f' = {total:.2f}万元。'
    location = {'table': title, 'table_code': parent.table_code, 'code': code, 'column': indicator,
                'page': pages[0] if pages else None, 'pages': pages, 'operand_refs': refs,
                'native_blank_sources': blank_observations(facts),
                'native_blank_operands': blank_operand_refs(facts, key),
                'fact_context_version_token': doc.fact_context.version_token}
    if column_total and table_view is not None:
        from .native_expenditure_context import build_expenditure_context
        cache = getattr(doc, '_expenditure_context_cache', None)
        if cache is None:
            cache = doc._expenditure_context_cache = {}
        cache_key = (parent.table_code, doc.fact_context.version_token)
        if cache_key not in cache:
            cache[cache_key] = build_expenditure_context(doc, table_view)
        if cache[cache_key] is not None:
            location['expenditure_context'] = {**cache[cache_key], 'checked_indicator': indicator}
            if level == 'mismatch':
                label = {'total_actual': '本年支出合计', 'basic_actual': '基本支出',
                         'project_actual': '项目支出'}.get(indicator, indicator)
                message = (f'{title}{label}列合计不一致：表内总计{parent.normalized_value:.2f}万元，'
                           f'各类科目合计{total:.2f}万元，差额{diff:.2f}万元。')
    return [rule._issue(message, location, 'warn' if level == 'mismatch' else 'info',
                        evidence_text=message, check_relation_id=f'{rule.code}:{key}',
                        fact_ids=ids, evidence_ids=evidence_ids)], []


def _exact_duplicate_rows(table, start, source_cells):
    """整行完全相同的重复科目行（同一编码在各列金额完全一致地印了两遍）。

    这类重复按一行参与合计（见 :func:`_table_view`），但它本身是原件缺陷：
    实样里第二行本应是项级行（2121100），却印成了款级编码（21211），
    项级科目因此整行缺失。内容不同的重复行不在这里报，由 _table_view 判身份歧义。
    """
    seen, duplicates = {}, []
    for row_idx, row in enumerate(table.rows):
        if row_idx < table.header_row_count or is_index_header(row):
            continue
        code = _row_code(row, start)
        if code is None:
            continue
        signature = tuple(re.sub(r'\s+', '', str(value or '')) for value in row)
        first = seen.get(code)
        if first is None:
            seen[code] = (row_idx, signature)
            continue
        if first[1] != signature:
            continue
        cells = [source_cells.get((row_idx, col)) for col in range(len(row))]
        page = next((int(cell.page_number) for cell in cells
                     if cell is not None and cell.page_number), None)
        name = ''
        for value in list(row)[1:start]:
            text = re.sub(r'\s+', '', str(value or ''))
            if text and not text.replace(',', '').replace('.', '').isdigit():
                name = text
                break
        duplicates.append({'code': code, 'name': name, 'row_idx': row_idx,
                           'first_row_idx': first[0], 'page': page})
    return duplicates


def _verified_native_chain(doc, view, facts):
    """跨表层级证明比单表取数更窄：只接纳已验原生字符的原格。"""
    _, table, mappings, cells, rows, _ = view
    if not table.extraction_method or any(token in table.extraction_method.lower() for token in ('ocr','fallback')):
        return False
    start = min(int(mapping['source_col_idx']) for mapping in mappings)
    for fact in facts:
        refs = fact.extra_dims.get('evidence_refs') or []
        if not refs or any(ref.get('source_method') != 'native_text' for ref in refs):
            return False
        labels = [cell for (row,col),cell in cells.items() if row == rows.get(fact.subject_code) and col < start and str(cell.raw_text or '').strip()]
        if not labels or any(not cell.extraction_method or any(token in cell.extraction_method.lower() for token in ('ocr','fallback')) for cell in labels):
            return False
    # 规则执行早于报告的证据装订，不能依赖稍后才回填的 verified 标签；
    # 直接复用同一原格认证器核验原文、页码、格坐标、来源哈希和证据ID。
    from src.services.rule_source_binding import _current_cell_evidence
    return len(_current_cell_evidence(facts, doc.parse_artifact, getattr(doc,'page_texts',[]),
                                     doc.fact_context.file_hash)) == len(facts)


def _native_mirror_covered_roots(doc, view, roots, paths):
    """只用同原件另一完整原生表的精确名称链确认错前缀，不猜编码/金额。

    混层披露本身合法，不能按编码长度删掉无上级明细。只有另一表实际印有
    同名项及完整类/款链、当前表也印有相同类/款，且错码紧跟该款时，才证明
    该项已属于当前汇总链。事实编码保持原值，单独交付原件镜像冲突。
    """
    _, table, _, _, rows, _ = view
    context = doc.fact_context
    compact = lambda value: re.sub(r'\s+', '', str(value or ''))
    ordered = sorted(rows, key=rows.get)
    proofs = {}
    for code in roots:
        if len(paths[code]) != 7 or code != paths[code]:
            continue
        leaf, error = _select(doc, view, code, 'total_actual')
        if (error or leaf is None or isinstance(leaf, NativeBlankTerm) or not leaf.subject_name
                or not _verified_native_chain(doc, view, [leaf])):
            continue
        candidates = [fact for fact in context.facts
                      if fact.table_code in DETAIL_TABLES and fact.table_code != table.table_code
                      and fact.indicator == 'total_actual' and fact.subject_code
                      and re.fullmatch(r'\d{7}', fact.subject_code)
                      and fact.subject_code != code and fact.subject_code[3:] == code[3:]
                      and compact(fact.subject_name) == compact(leaf.subject_name)
                      and not operand_rejection_reason(fact, context.org_name)
                      and fact.fiscal_year == context.fiscal_year
                      and _canonical_budget_stage(fact.budget_stage) == '决算']
        mirror_codes = {fact.subject_code for fact in candidates}
        if len(mirror_codes) != 1:
            continue
        mirror_code = mirror_codes.pop(); parent_code, class_code = mirror_code[:5], mirror_code[:3]
        position = ordered.index(code)
        if (parent_code not in rows or class_code not in rows or position == 0
                or ordered[position-1] != parent_code or rows[code] != rows[parent_code]+1
                or not rows[class_code] < rows[parent_code]):
            continue
        parent, parent_error = _select(doc, view, parent_code, 'total_actual')
        klass, class_error = _select(doc, view, class_code, 'total_actual')
        if (parent_error or class_error or parent is None or klass is None
                or isinstance(parent, NativeBlankTerm) or isinstance(klass, NativeBlankTerm)
                or not _verified_native_chain(doc, view, [klass,parent,leaf])):
            continue
        verified = []
        for mirror_table in sorted({fact.table_code for fact in candidates}):
            mirror_view, mirror_error = _table_view(doc, mirror_table)
            if mirror_error or any(part not in mirror_view[4] for part in (class_code,parent_code,mirror_code)):
                continue
            chain = [_select(doc, mirror_view, part, 'total_actual') for part in (class_code,parent_code,mirror_code)]
            if any(reason or fact is None or isinstance(fact, NativeBlankTerm) for fact,reason in chain):
                continue
            chain = [fact for fact,reason in chain]
            if not _verified_native_chain(doc, mirror_view, chain):
                continue
            if ([compact(fact.subject_name) for fact in chain]
                    != [compact(fact.subject_name) for fact in (klass,parent,leaf)]):
                continue
            mirror_rows = mirror_view[4]
            if not mirror_rows[class_code] < mirror_rows[parent_code] < mirror_rows[mirror_code]:
                continue
            verified.append(chain)
        if verified:
            proofs[code] = {'mirror_code': mirror_code, 'class_code': class_code,
                            'parent_code': parent_code, 'facts': [klass,parent,leaf],
                            'mirror_facts': [fact for chain in verified for fact in chain]}
    return proofs


def check_native_detail_tables(rule, doc, *, table_codes=None, include_income_sources=False):
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
        view, error = _table_view(doc, table_code, include_income_sources=include_income_sources)
        if error:
            unresolved.append(error)
            receipt(rule, doc, f'{table_code}:complete_table', [], status='insufficient_data',
                    formula='complete logical table', reason=error)
            continue
        _, _, mappings, _, rows, _ = view
        # 整行排版重复只在表格级规则（V33-005）报一次，避免同一缺陷在收入/支出
        # 各自的专项规则里重复出现。重复行本身已按一行参与合计，不影响勾稽。
        if rule.code == 'V33-005':
            measure_start = min(int(m['source_col_idx']) for m in mappings)
            for duplicate in _exact_duplicate_rows(view[1], measure_start, view[3]):
                label = f"{duplicate['code']} {duplicate['name']}".strip()
                page_text = f"第{duplicate['page']}页" if duplicate['page'] else "原件"
                message = (
                    f"{title}{page_text}科目{label}整行重复印了两遍（各列金额完全相同），"
                    "本次已按一行参与合计；若该行本应是项级科目行，请核对编码是否"
                    "误植为上级款级编码（项级科目将因此整行缺失）。"
                )
                location = {
                    'table': title, 'table_code': table_code, 'code': duplicate['code'],
                    'page': duplicate['page'],
                    'pages': [duplicate['page']] if duplicate['page'] else [],
                    'row_index': duplicate['row_idx'] + 1,
                }
                # 证据走当前事实的原格身份：单行原文在版面上出现两次，无法作为
                # 唯一原文片段；该行的金额格仍是可回溯到原页原格的唯一事实。
                row_facts = [fact for fact in view[5]
                             if fact.subject_code == duplicate['code']
                             and fact.indicator == 'total_actual']
                # 两行重复会各产生一条事实；金额完全一致时它们指向同一数值，
                # 取第一条作证据锚（内容不同的重复行不会走到这里）。
                if len(row_facts) > 1 and len({
                        (str(fact.raw_value), str(fact.normalized_value), fact.unit)
                        for fact in row_facts}) == 1:
                    row_facts = row_facts[:1]
                if len(row_facts) == 1:
                    from .native_table_arithmetic import journal
                    journal(rule, doc, row_facts)
                    location['operand_refs'] = [operand_ref(row_facts[0], 'duplicate_row')]
                    location['fact_context_version_token'] = doc.fact_context.version_token
                issues.append(rule._issue(
                    message, location, 'low', evidence_text=message,
                    check_relation_id=(
                        f"{rule.code}:{table_code}:duplicate_row:{duplicate['code']}")))
        codes = sorted(code for code in rows if code != 'total')
        paths = hierarchy_paths(codes)
        if len(set(paths.values())) != len(paths):
            unresolved.append(f'{title}科目层级存在重复身份')
            continue
        roots = [code for code in codes if not any(paths[code].startswith(paths[p]) and code != p for p in codes)]
        # 不限制原件的披露深度；只排除有同原件精确完整镜像链证明的错前缀项。
        mirror_proofs = _native_mirror_covered_roots(doc, view, roots, paths)
        orphans = sorted(mirror_proofs)
        if orphans and rule.code == 'V33-005':
            orphan_facts, source_refs = [], []
            for code in orphans:
                proof = mirror_proofs[code]
                orphan_facts.extend([*proof['facts'], *proof['mirror_facts']])
                row_idx = rows[code]
                code_cells = [cell for (row, col), cell in view[3].items()
                              if row == row_idx and col < min(int(m['source_col_idx']) for m in mappings)
                              and str(cell.raw_text or '').strip()]
                if code_cells:
                    page = code_cells[0].page_number
                    source_refs.append({'page': page, 'span': code, 'role': '原文孤立款项编码'})
                for fact in proof['mirror_facts']:
                    if fact.subject_code == proof['mirror_code']:
                        for ref in fact.extra_dims.get('evidence_refs', []):
                            source_refs.append({'page': ref['physical_page'], 'span': fact.subject_code,
                                                'role': '同原件完整原生表的同名项编码'})
            orphan_facts = list({fact.fact_uid:fact for fact in orphan_facts}.values())
            mirrors = [{'raw_code':code, 'mirror_code':mirror_proofs[code]['mirror_code'],
                        'class_code':mirror_proofs[code]['class_code'], 'parent_code':mirror_proofs[code]['parent_code']}
                       for code in orphans]
            reason = (f'{title}科目编码层级不一致：' + '、'.join(f"{p['raw_code']} / {p['mirror_code']}" for p in mirrors)
                      + '。同名科目在另一完整原生表印有一致的类/款/项名称链，当前表的该项紧跟相同款级汇总，'
                        '但原印编码前缀不一致；不能将该项再作为独立根重复累加。原编码与金额保留，请核实表间编码。')
            ids, evidence_ids = receipt(rule, doc, f'{table_code}:orphan_classification', orphan_facts,
                                        status='fail', formula='同原件完整类款项名称链的编码镜像一致',
                                        reason=reason, disclosure_sources=source_refs)
            orphan_refs = []
            for fact in money_facts(orphan_facts):
                role = '孤立款项原行' if fact.subject_code in orphans else '完整原生类款项链'
                ref = operand_ref(fact, role)
                ref.update(operand_id=f'{fact.fact_uid}:classification:{role}',
                           relation_id=f'{table_code}:orphan_classification')
                orphan_refs.append(ref)
            issues.append(rule._issue(reason, {
                'table': title, 'table_code': table_code, 'check_type': 'orphan_classification',
                'orphan_codes': orphans, 'classification_mirrors': mirrors, 'source_refs': source_refs,
                'page': source_refs[0]['page'] if source_refs else None,
                'pages': sorted({ref['page'] for ref in source_refs}),
                'operand_refs': orphan_refs,
                'fact_context_version_token': doc.fact_context.version_token,
            }, 'warn', evidence_text='；'.join(orphans),
                check_relation_id=f'{rule.code}:{table_code}:orphan_classification',
                fact_ids=ids, evidence_ids=evidence_ids))
        roots = [code for code in roots if code not in mirror_proofs]
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
                                          operands[0], operands[1:], column_total=parent_code == 'total', table_view=view)
                issues.extend(found)
                unresolved.extend(missing)
    return finish(rule, cluster_rounding(rule, issues), unresolved)
