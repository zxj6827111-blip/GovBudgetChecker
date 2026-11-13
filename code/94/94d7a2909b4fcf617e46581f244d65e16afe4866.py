"""以原生字符流恢复跨横线折行的名称/编码，保留金额格及原始网格抽取。"""
from __future__ import annotations

from dataclasses import replace
import math
import re

from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_row_roles import is_index_header
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text


def _columns(table):
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


def repair_native_dimensions(page, table):
    """只在原字符流连续且确实跨格时恢复；不按科目字典猜码，不根据金额补差。"""
    if table.extraction_method not in {'lines', 'lines_strict'}:
        return table
    chars = getattr(page, 'chars', None)
    if not chars:
        return table
    labels, money = _columns(table)
    if not labels or not money:
        return table
    positions = {(c.row_idx, c.col_idx): c for c in table.cells}
    eligible = {r for r, row in enumerate(table.rows) if not is_index_header(row)
                and any(positions.get((r, c)) is not None and positions[r, c].numeric_value is not None for c in money)
                and (r >= table.header_row_count or table.table_code.startswith('UNKNOWN_'))}
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
        groups = []
        for sample in samples:
            if not groups or sample[0] != groups[-1][-1][0] + 1 or not _continues(groups[-1][-1], sample):
                groups.append([])
            groups[-1].append(sample)
        owned, owners, affected = {}, {}, False
        for group in groups:
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
