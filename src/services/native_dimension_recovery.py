"""以原生字符流恢复跨横线折行的名称/编码，保留金额格及原始网格抽取。"""
from __future__ import annotations

from dataclasses import replace
import math
import re

from src.services.fiscal_column_identity import canonical_column_mappings, code_dimension_columns, header_paths
from src.services.fiscal_row_roles import is_index_header
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text


def _columns(table, reference=None):
    if reference is not None and table.table_code.startswith('UNKNOWN_'):
        # 调用方已证明续页的原生物理金额列边界一致；沿用字面表头角色，
        # 不凭列数猜收入表/双栏表，也不改变续页原片段身份。
        rule = NINE_TABLE_RULES.get(reference.table_code)
        mappings = canonical_column_mappings(reference, rule) if rule else []
        money = {m['source_col_idx'] for m in mappings}
        labels = {c for m in mappings for c in m.get('source_label_cols', [])}
        return labels - money, money
    rule = NINE_TABLE_RULES.get(table.table_code)
    if rule:
        mappings = canonical_column_mappings(table, rule)
        money = {m['source_col_idx'] for m in mappings}
        labels = {c for m in mappings for c in m.get('source_label_cols', [])}
        return labels - money, money
    # 无表头续页只能恢复有明确账表布局的标签；后续仍须通过逻辑表身份合并。
    widths = {len(row) for row in table.rows}
    if len(widths) != 1:
        return set(), set()
    width = next(iter(widths))
    content = ''.join(str(v or '') for row in table.rows for v in row)
    if width in (4, 7) and any(token in content for token in ('本年收入合计', '年末财政拨款结转', '使用非财政拨款结余')):
        return {0, 2}, {1, *range(3, width)}
    if width == 8 and any(re.fullmatch(r'[\d\s]+', str(row[start] or ''))
                         and row[start + 2] and row[start + 3]
                         for row in table.rows for start in (0, 4)):
        return {0, 1, 2, 4, 5, 6}, {3, 7}
    if width in (3, 5, 7) and any(re.fullmatch(r'\d{3}(?:\d{2}){0,2}', re.sub(r'\s+', '', str(row[0] or '')))
                                 and row[1] for row in table.rows):
        return {0, 1}, set(range(2, width))
    return set(), set()


def _box(char):
    try:
        values = tuple(float(char[k]) for k in ('x0', 'top', 'x1', 'bottom'))
        return values if all(math.isfinite(v) for v in values) and values[2] > values[0] and values[3] > values[1] else None
    except (KeyError, ValueError, TypeError):
        return None


def _continues(previous, current):
    _, p, pb = previous
    _, c, cb = current
    height = max(pb[3] - pb[1], cb[3] - cb[1])
    dy = cb[1] - pb[1]
    if abs(dy) <= height * .6:
        return cb[0] >= pb[0] - .2  # 下一行的重新起笔不能拼到上一行末尾。
    return height * .6 < dy <= height * 1.8 and cb[0] <= pb[2] + 1


def _excluded_header_flow(group, table, cells, band):
    """跨入正文的分类标记必须溯源至表头/合并合计格，不能按名称关键词删字。"""
    literal = ''.join(str(sample[1]['text']) for sample in group)
    if not re.fullmatch(r'(?:合计|类|款|项)+', literal):
        return None
    first = group[0][2]
    x, y = (first[0] + first[2]) / 2, (first[1] + first[3]) / 2
    anchors = [cell for cell in table.cells if cell.bbox
               and cell.bbox[0] <= x < cell.bbox[2] and cell.bbox[1] <= y < cell.bbox[3]
               and (cell.is_header or (normalize_text(cell.raw_text) == '合计'
                    and cell.bbox[0] < band[0] and cell.bbox[2] >= band[2]))]
    if len(anchors) != 1 or not literal.startswith(normalize_text(anchors[0].raw_text)):
        return None
    anchor = anchors[0]
    intruded = [cell for cell in cells if cell.row_idx > anchor.row_idx and any(
        cell.bbox[0] <= (sample[2][0] + sample[2][2]) / 2 < cell.bbox[2]
        and cell.bbox[1] <= (sample[2][1] + sample[2][3]) / 2 < cell.bbox[3]
        for sample in group)]
    if len(intruded) < 2:
        return None
    return {'physical_page': anchor.page_number, 'start_char_index': group[0][0],
            'end_char_index': group[-1][0] + 1, 'text': literal,
            'bbox': [min(s[2][0] for s in group), min(s[2][1] for s in group),
                     max(s[2][2] for s in group), max(s[2][3] for s in group)],
            'method': 'native_header_marker_flow_excluded_from_body',
            'anchor_row_idx': anchor.row_idx, 'anchor_col_idx': anchor.col_idx,
            'anchor_source_cell_id': (f'{anchor.origin_fragment_uid or table.fragment_uid}:'
                f'r{anchor.origin_row_idx if anchor.origin_row_idx is not None else anchor.row_idx}'
                f'c{anchor.origin_col_idx if anchor.origin_col_idx is not None else anchor.col_idx}'),
            'anchor_grid_text': anchor.raw_text, 'anchor_grid_bbox': list(anchor.bbox),
            'affected_rows': [cell.row_idx for cell in intruded]}


def repair_native_dimensions(page, table, *, reference=None):
    """只在原字符流连续且确实跨格时恢复；不按科目字典猜码，不根据金额补差。"""
    if table.extraction_method not in {'lines', 'lines_strict'}:
        return table
    chars = getattr(page, 'chars', None)
    if not chars:
        return table
    labels, money = _columns(table, reference)
    if not labels or not money:
        return table
    positions = {(c.row_idx, c.col_idx): c for c in table.cells}
    eligible = {r for r, row in enumerate(table.rows) if not is_index_header(row)
                and any(positions.get((r, c)) is not None and positions[r, c].numeric_value is not None for c in money)
                and (r >= table.header_row_count or table.table_code.startswith('UNKNOWN_'))}
    amount_rows = set(eligible)
    # 本行金额明确留空，仍可能拥有溢到下一行的名称尾字。仅排除空金额行
    # 会让尾字失去来源归属并污染下一条总计；纳入标签归属不创造任何零值。
    eligible.update(r for r, row in enumerate(table.rows)
                    if (r >= table.header_row_count or table.table_code.startswith('UNKNOWN_'))
                    and not is_index_header(row)
                    and any(positions.get((r, c)) is not None and positions[r, c].raw_text.strip() for c in labels)
                    and all(positions.get((r, c)) is not None and not positions[r, c].raw_text.strip()
                            and positions[r, c].native_blank_verified for c in money))
    updates = {}
    for col in labels:
        cells = [c for c in table.cells if c.col_idx == col and c.row_idx in eligible and c.bbox]
        if not cells:
            continue
        band = min((c.bbox for c in cells), key=lambda b: b[2] - b[0])
        samples = []
        for index, char in enumerate(chars):
            box = _box(char)
            # 合计行的标签可能横跨类、款、名称三格，不能只采窄编码列而抹掉其自身文字。
            in_label = box is not None and any(
                c.bbox[0] <= (box[0] + box[2]) / 2 < c.bbox[2]
                and c.bbox[1] <= (box[1] + box[3]) / 2 < c.bbox[3]
                for c in cells if c.bbox[2] - c.bbox[0] > band[2] - band[0] + 1)
            if (box is None or not str(char.get('text') or '').strip()
                    or not (band[0] <= (box[0] + box[2]) / 2 < band[2] or in_label)
                    or not table.bbox[1] <= (box[1] + box[3]) / 2 <= table.bbox[3] + (box[3] - box[1])):
                continue
            samples.append((index, char, box))
        independent_starts = set()
        blank_cells = [c for c in cells if c.row_idx not in amount_rows]
        if blank_cells:
            for cell in cells:
                if cell.row_idx not in amount_rows:
                    continue
                own = [s for s in samples if cell.bbox[0] <= (s[2][0] + s[2][2]) / 2 < cell.bbox[2]
                       and cell.bbox[1] <= (s[2][1] + s[2][3]) / 2 < cell.bbox[3]]
                if not own or len(own) < 2:
                    continue
                height = max(s[2][3] - s[2][1] for s in own)
                if normalize_text(''.join(str(s[1]['text']) for s in own)) != normalize_text(cell.raw_text):
                    continue
                if (cell.bbox[1] - .5 <= own[0][2][1] and own[0][2][3] <= cell.bbox[3] + .5
                        and all(b[0] == a[0] + 1 for a, b in zip(own, own[1:], strict=False))):
                    # 本格从首字起就独立完整地呈现（允许多行名称），不能仅因
                    # PDF对象与前行连续就把它当作前行折词，导致正确金额失去标签。
                    independent_starts.add(own[0][0])
                    continue
                if max(s[2][1] for s in own) - min(s[2][1] for s in own) > height * .25:
                    continue
                if normalize_text(cell.raw_text) in {'合计', '总计'}:
                    # 完整位于本格的总计标记有独立身份；金额在同格内偏上/下
                    # 不能成为把该标记吞入前行的理由。“款\n总计”不走此分支。
                    independent_starts.add(own[0][0])
                    continue
                # 连续PDF对象也可以是两个独立打印行。后行完整标签与其金额
                # 同基线时必须重新起组，不能被空金额前行吞掉。
                for money_col in money:
                    amount = positions.get((cell.row_idx, money_col))
                    if amount is None or amount.numeric_value is None or not amount.bbox:
                        continue
                    amount_chars = [char for char in chars if (b := _box(char))
                                    and str(char.get('text') or '').strip()
                                    and amount.bbox[0] <= (b[0] + b[2]) / 2 < amount.bbox[2]
                                    and amount.bbox[1] <= (b[1] + b[3]) / 2 < amount.bbox[3]]
                    if (amount_chars and normalize_text(''.join(str(c['text']) for c in amount_chars)) == normalize_text(amount.raw_text)
                            and abs(own[0][2][1] - min(c['top'] for c in amount_chars)) <= height * .4):
                        independent_starts.add(own[0][0])
                        break
        groups = []
        for sample in samples:
            independent_row = (groups and sample[0] in independent_starts and any(
                c.bbox[1] <= (groups[-1][0][2][1] + groups[-1][0][2][3]) / 2 < c.bbox[3]
                for c in blank_cells))
            if not groups or independent_row or sample[0] != groups[-1][-1][0] + 1 or not _continues(groups[-1][-1], sample):
                groups.append([])
            groups[-1].append(sample)
        owned, owners, affected, excluded = {}, {}, False, []
        for group in groups:
            header_flow = _excluded_header_flow(group, table, cells, band)
            if header_flow is not None:
                excluded.append(header_flow)
                affected = True
                continue
            first = group[0][2]
            x, y = (first[0] + first[2]) / 2, (first[1] + first[3]) / 2
            candidates = [c for c in cells if c.bbox[0] <= x < c.bbox[2] and c.bbox[1] <= y < c.bbox[3]]
            if len(candidates) != 1:
                continue
            cell = candidates[0]
            bbox = (min(s[2][0] for s in group), min(s[2][1] for s in group),
                    max(s[2][2] for s in group), max(s[2][3] for s in group))
            if bbox[3] > cell.bbox[3] + max(s[2][3] - s[2][1] for s in group) * 3:
                return table
            source = {'physical_page': cell.page_number, 'start_char_index': group[0][0],
                      'end_char_index': group[-1][0] + 1, 'text': ''.join(str(s[1]['text']) for s in group),
                      'bbox': list(bbox), 'method': 'native_contiguous_character_flow'}
            owned.setdefault(cell.row_idx, []).append(source)
            owners.update({sample[0]: cell.row_idx for sample in group})
            affected |= bbox[3] > cell.bbox[3] + 1 or bbox[1] < cell.bbox[1] - 1
        if not affected:
            continue
        for cell in cells:
            sources = owned.get(cell.row_idx, [])
            if not sources:
                intruders = [s for s in samples if cell.bbox[0] <= (s[2][0] + s[2][2]) / 2 < cell.bbox[2]
                             and cell.bbox[1] <= (s[2][1] + s[2][3]) / 2 < cell.bbox[3]]
                if not intruders or not all(s[0] in owners and owners[s[0]] != cell.row_idx for s in intruders):
                    continue
                sources = [{'physical_page': cell.page_number, 'start_char_index': s[0], 'end_char_index': s[0] + 1,
                            'text': str(s[1]['text']), 'bbox': list(s[2]), 'method': 'native_character_assigned_to_previous_row'}
                           for s in intruders]
                text = ''
            else:
                text = ''.join(s['text'] for s in sources).strip()
            if normalize_text(text) == normalize_text(cell.raw_text):
                continue
            removed = [source for source in excluded if cell.row_idx in source['affected_rows']]
            if removed:
                # 有效名称的证明与被排除标记的证明一起传入行身份，原网格文本仍保留。
                sources = [{**source, 'excluded_header_sources': removed} for source in sources]
            value = float(text) if re.fullmatch(r'\d+', text) else None
            updates[cell.row_idx, col] = replace(cell, raw_text=text, normalized_text=normalize_text(text),
                 numeric_value=value, normalized_value_wan=None, grid_raw_text=cell.raw_text,
                 native_text_sources=sources, native_blank_verified=False)
    if not updates:
        return table
    rows = [list(row) for row in table.rows]
    for (r, c), cell in updates.items():
        rows[r][c] = cell.raw_text
    return replace(table, rows=rows, cells=[updates.get((c.row_idx, c.col_idx), c) for c in table.cells])


def repair_native_code_baselines(page, table):
    """完整编码与同车道金额的基线共指；相邻行溢出的残字不能污染本行编码。"""
    if table.extraction_method not in {'lines', 'lines_strict'}:
        return table
    paths = header_paths(table)
    codes = code_dimension_columns(table, paths)
    _, money = _columns(table)
    chars = getattr(page, 'chars', None) or []
    updates = {}
    for cell in table.cells:
        if cell.is_header or cell.col_idx not in codes or not cell.bbox:
            continue
        # 类、款分列的布局仍由连续字符恢复处理，此处只处理一格中的完整编码。
        next_code = min((col for col in codes if col > cell.col_idx), default=len(paths))
        columns = [col for col in money if cell.col_idx < col < next_code]
        anchors = [c for c in table.cells if c.row_idx == cell.row_idx and c.col_idx in columns
                   and c.numeric_value is not None and c.bbox]
        if len(anchors) != 1:
            continue
        anchor = anchors[0]
        amount_chars = [c for c in chars if str(c.get('text', '')).strip() and _box(c)
                        and anchor.bbox[0] <= (c['x0'] + c['x1']) / 2 < anchor.bbox[2]
                        and anchor.bbox[1] <= c['top'] < anchor.bbox[3]]
        if not amount_chars:
            continue
        baseline = min(c['top'] for c in amount_chars)
        samples = [(i, c, _box(c)) for i, c in enumerate(chars)
                   if str(c.get('text', '')).strip() and _box(c)
                   and cell.bbox[0] <= (c['x0'] + c['x1']) / 2 < cell.bbox[2]
                   and abs(c['top'] - baseline) <= (c['bottom'] - c['top']) * .2]
        if not samples or any(b[0] != a[0] + 1 for a, b in zip(samples, samples[1:], strict=False)):
            continue
        literal = ''.join(c['text'] for _, c, _ in samples)
        if not re.fullmatch(r'\d{3}(?:\d{2}){0,2}', literal):
            continue  # 原件的 302041 等异常编码保持原样，不能按字典截掉数字。
        if normalize_text(cell.raw_text) == literal:
            continue
        box = [min(s[2][0] for s in samples), min(s[2][1] for s in samples),
               max(s[2][2] for s in samples), max(s[2][3] for s in samples)]
        source = {'physical_page': cell.page_number, 'start_char_index': samples[0][0],
                  'end_char_index': samples[-1][0] + 1, 'text': literal, 'bbox': box,
                  'method': 'native_contiguous_character_flow'}
        updates[cell.row_idx, cell.col_idx] = replace(cell, raw_text=literal, normalized_text=literal,
            numeric_value=float(literal), grid_raw_text=cell.grid_raw_text if cell.grid_raw_text is not None else cell.raw_text,
            native_text_sources=[source], native_blank_verified=False)
    if not updates:
        return table
    rows = [list(row) for row in table.rows]
    for (r, c), cell in updates.items():
        rows[r][c] = cell.raw_text
    return replace(table, rows=rows, cells=[updates.get((c.row_idx, c.col_idx), c) for c in table.cells])


def repair_native_cross_page_name(previous_page, page, previous, current, physical):
    """同网格续页顶端的孤立名称尾部，只能接回上一页最后一个名称格。"""
    if (previous.table_code != 'FIN_05_general_public_expenditure'
            or previous.extraction_method not in {'lines', 'lines_strict'}
            or not previous.cells
            or current.page_number - max(c.page_number for c in previous.cells) != 1
            or current.extraction_method not in {'lines', 'lines_strict'}
            or not current.table_code.startswith('UNKNOWN_')
            or {len(row) for row in previous.rows} != {5}
            or {len(row) for row in current.rows} != {5}):
        return physical
    last = len(previous.rows) - 1
    names = [c for c in previous.cells if c.row_idx == last and c.col_idx == 1 and c.bbox]
    if len(names) != 1 or not re.fullmatch(r'\d{3}(?:\d{2}){0,2}', str(previous.rows[last][0] or '')):
        return physical
    name = names[0]
    current_names = [c for c in current.cells if c.col_idx == 1 and c.bbox]
    if not current_names or any(abs(min(current_names, key=lambda c: c.bbox[2] - c.bbox[0]).bbox[i]
                                   - name.bbox[i]) > 1 for i in (0, 2)):
        return physical
    height = name.bbox[3] - name.bbox[1]
    if (not name.raw_text or normalize_text(name.raw_text).endswith('支出')
            or name.bbox[3] < float(previous_page.height) * .8):
        return physical
    top, bottom = current.bbox[1] - height, current.bbox[1]
    first = [str(value or '').strip() for value in current.rows[0]]
    if first[1] and not any(value for col, value in enumerate(first) if col != 1):
        orphan = [c for c in current.cells if c.row_idx == 0 and c.col_idx == 1 and c.bbox]
        if len(orphan) != 1:
            return physical
        top, bottom = orphan[0].bbox[1], orphan[0].bbox[3]
    samples = [(i, c, _box(c)) for i, c in enumerate(page.chars or [])
               if _box(c) and str(c.get('text') or '').strip()
               and current.bbox[0] <= (_box(c)[0] + _box(c)[2]) / 2 < current.bbox[2]
               and top <= _box(c)[1] < _box(c)[3] <= bottom]
    if (not samples or any(b[0] != a[0] + 1 for a, b in zip(samples, samples[1:], strict=False))
            or any(not name.bbox[0] <= (s[2][0] + s[2][2]) / 2 < name.bbox[2] for s in samples)
            or max(s[2][1] for s in samples) - min(s[2][1] for s in samples) > 3):
        return physical
    tail = ''.join(s[1]['text'] for s in samples)
    if not re.fullmatch(r'[\u4e00-\u9fff]{2,12}', tail) or any(t in tail for t in ('表', '单位', '科目', '说明')):
        return physical
    source_uid = name.origin_fragment_uid or previous.fragment_uid
    source_row = name.origin_row_idx if name.origin_row_idx is not None else name.row_idx
    source_col = name.origin_col_idx if name.origin_col_idx is not None else name.col_idx
    originals = [t for t in physical if t.fragment_uid == source_uid]
    if len(originals) != 1:
        return physical
    original = originals[0]
    cells = [c for c in original.cells if (c.row_idx, c.col_idx) == (source_row, source_col)]
    if len(cells) != 1 or cells[0].native_text_sources:
        return physical  # 不叠加未经证明的跨格修复。
    cell = cells[0]
    head = [(i, c, _box(c)) for i, c in enumerate(previous_page.chars or [])
            if _box(c) and str(c.get('text') or '').strip()
            and cell.bbox[0] <= (_box(c)[0] + _box(c)[2]) / 2 < cell.bbox[2]
            and cell.bbox[1] <= (_box(c)[1] + _box(c)[3]) / 2 < cell.bbox[3]]
    if (not head or any(b[0] != a[0] + 1 for a, b in zip(head, head[1:], strict=False))
            or normalize_text(''.join(s[1]['text'] for s in head)) != normalize_text(cell.raw_text)):
        return physical
    proofs = []
    for part, number in ((head, cell.page_number), (samples, current.page_number)):
        proofs.append({'physical_page': number, 'start_char_index': part[0][0],
                       'end_char_index': part[-1][0] + 1, 'text': ''.join(s[1]['text'] for s in part),
                       'bbox': [min(s[2][0] for s in part), min(s[2][1] for s in part),
                                max(s[2][2] for s in part), max(s[2][3] for s in part)],
                       'method': 'native_contiguous_character_flow'})
    literal = ''.join(p['text'] for p in proofs)
    replacement = replace(cell, raw_text=literal, normalized_text=normalize_text(literal),
                          grid_raw_text=cell.raw_text, native_text_sources=proofs, native_blank_verified=False)
    rows = [list(row) for row in original.rows]
    rows[source_row][source_col] = literal
    fixed = replace(original, rows=rows, cells=[replacement if c is cell else c for c in original.cells])
    return [fixed if t is original else t for t in physical]


def repaired_native_grid_shadow(first, second):
    """续页折行已获原字符证明时，同位置同金额的局部文本抽取不能覆盖完整网格。"""
    text = first if first.extraction_method == 'text' else second
    grid = second if text is first else first
    if (text.extraction_method != 'text' or grid.extraction_method not in {'lines', 'lines_strict'}
            or not grid.table_code.startswith('UNKNOWN_') or not text.table_code.startswith('UNKNOWN_')
            or not any(c.native_text_sources for c in grid.cells)):
        return False
    _, money = _columns(grid)
    amounts = [c for c in grid.cells if c.col_idx in money and c.numeric_value is not None and c.bbox]
    matched = False
    for cell in text.cells:
        if cell.numeric_value is None or not cell.bbox:
            continue
        x, y = (cell.bbox[0] + cell.bbox[2]) / 2, (cell.bbox[1] + cell.bbox[3]) / 2
        candidates = [c for c in amounts if c.bbox[0] <= x < c.bbox[2] and c.bbox[1] <= y < c.bbox[3]]
        if not candidates:
            continue  # 编码字符仍由标签证据保留，不能当作金额。
        if len(candidates) != 1 or normalize_text(candidates[0].raw_text) != normalize_text(cell.raw_text):
            return False
        matched = True
    return matched
