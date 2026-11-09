"""根据原生格结构区分表内注释与财政数据行。"""
from __future__ import annotations

import re


def labels_covered_by_native_cells(table, row_index, columns):
    """合并标签格可以覆盖多个标签列，不能越过金额列补造不存在的格。"""
    columns = set(columns)
    cells = [cell for cell in table.cells if cell.row_idx == row_index]
    direct = all(any(cell.col_idx == col or (
        cell.bbox and cell.extraction_method in {'lines', 'lines_strict'}
        and not cell.ocr_provenance and cell.col_idx <= col < cell.col_idx + cell.col_span
        and set(range(cell.col_idx, cell.col_idx + cell.col_span)) <= columns
    ) for cell in cells) for col in columns)
    if direct:
        return True
    # 续页只有“合计标签｜金额”的四格版式，拼入六/八列时 col_span 可能仍为 1。
    # 要求至少两条完整原生数据行的标签边界共同证明该合计格覆盖同一标签车道。
    ordered = sorted(columns)
    if ordered != list(range(min(columns), max(columns) + 1)):
        return False
    enclosing = [cell for cell in cells if cell.col_idx == ordered[0] and cell.bbox
                 and cell.extraction_method in {'lines', 'lines_strict'} and not cell.ocr_provenance]
    if len(enclosing) != 1:
        return False
    proofs = set()
    for index in range(table.header_row_count, len(table.rows)):
        if index == row_index:
            continue
        source = [next((cell for cell in table.cells if cell.row_idx == index and cell.col_idx == col), None)
                  for col in ordered]
        if any(cell is None or not cell.bbox or not cell.raw_text.strip() or cell.is_header
               or cell.extraction_method not in {'lines', 'lines_strict'} or cell.ocr_provenance
               or cell.col_span != 1 for cell in source):
            continue
        if (all(abs(left.bbox[2] - right.bbox[0]) <= .5 for left, right in zip(source, source[1:]))
                and abs(enclosing[0].bbox[0] - source[0].bbox[0]) <= .5
                and abs(enclosing[0].bbox[2] - source[-1].bbox[2]) <= .5):
            proofs.add(index)
    return len(proofs) >= 2


def merged_note_row(table, row_index):
    """整行只有一个横跨全宽的注释格；普通标签行或金额格不能借此消失。"""
    row = table.rows[row_index]
    cells = [cell for cell in table.cells if cell.row_idx == row_index]
    real = [cell for cell in cells if cell.bbox]
    if len(real) != 1:
        return False
    note = real[0]
    return bool(
        note.col_idx == 0 and note.col_span == len(row) and len(row) > 1
        and note.extraction_method in {'lines', 'lines_strict'} and not note.ocr_provenance
        and not note.is_header and note.numeric_value is None
        and re.match(r'^\s*(?:注|说明)\s*[:：]', note.raw_text or '')
        and all(not str(value or '').strip() for value in row[1:])
        and all(cell is note or (not cell.raw_text.strip() and cell.numeric_value is None
                                and not cell.bbox) for cell in cells)
    )
