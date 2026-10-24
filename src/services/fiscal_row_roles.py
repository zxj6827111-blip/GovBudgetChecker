"""Structural fiscal rows must remain outside the amount fact set."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from src.services.fiscal_table_rules import normalize_text


def is_index_header(values: Iterable[object]) -> bool:
    """Recognize explicit row/column index labels, never integer amounts alone."""
    values = list(values)
    if any(
        re.fullmatch(r"(?:栏次|行次)(?:编号)?[:：]?", normalize_text(str(value or "")))
        for value in values
    ):
        return True
    # Scanned forms also mark non-amount columns with **/…/dots before a
    # consecutive 1..N column-number sequence. Require the explicit markers;
    # integer amounts or a real row label alone never satisfy this branch.
    tokens = [str(value or "").strip() for value in values if str(value or "").strip()]
    numbers = [token for token in tokens if token.isdigit()]
    markers = [token for token in tokens if not token.isdigit()]
    return bool(markers and len(numbers) >= 3
                and all(re.fullmatch(r"[*.．。·…⋯]+", marker) for marker in markers)
                and numbers == [str(index) for index in range(1, len(numbers) + 1)])


def native_bilateral_padding_positions(table) -> set[tuple[int, int]]:
    """双栏总表右侧的占位零（两种原件写法的并集）；保留原格，不创造业务事实。"""
    return _closing_grant_padding(table) | _unlabelled_zero_padding(table)


def _unlabelled_zero_padding(table) -> set[tuple[int, int]]:
    """单位版收支总表支出侧多出来的空行：科目格经原页网格证实为空，金额格只印了 0。

    只认原生网格、收支总表，且占位行须在支出侧「总计/支出总计」行之前；整行任何一格
    有非零金额或未证实的空白，都不算占位。财政拨款收支总表仍只认 _closing_grant_padding。
    """
    if (table.table_code != "FIN_01_income_expenditure_total"
            or table.extraction_method not in {"lines", "lines_strict"}):
        return set()
    from src.services.fiscal_column_identity import canonical_column_mappings
    from src.services.fiscal_table_rules import NINE_TABLE_RULES

    mappings = canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])
    sides = {}
    for mapping in mappings:
        if mapping["canonical_measure"] != "income_actual" and mapping.get("source_label_cols"):
            sides.setdefault(mapping["source_label_cols"][-1], set()).add(mapping["source_col_idx"])
    if len(sides) != 1:
        return set()
    (label_col, money_cols), = sides.items()
    total_row = next((index for index, row in enumerate(table.rows)
                      if label_col < len(row) and normalize_text(str(row[label_col] or "")) in {"总计", "支出总计"}),
                     None)
    if total_row is None:
        return set()
    cells = {(cell.row_idx, cell.col_idx): cell for cell in table.cells}
    positions = set()
    for row_idx in range(table.header_row_count, total_row):
        label = cells.get((row_idx, label_col))
        if not label or label.raw_text.strip() or not label.native_blank_verified or not label.bbox:
            continue
        row = [cells.get((row_idx, col)) for col in sorted(money_cols)]
        if any(cell is None or not cell.bbox for cell in row):
            continue
        zeros = []
        for cell in row:
            try:
                zero = Decimal(cell.raw_text.strip()) == 0 and cell.numeric_value == 0
            except InvalidOperation:
                zero = False
            if zero:
                zeros.append(cell)
            elif cell.raw_text.strip() or not cell.native_blank_verified:
                break
        else:
            positions.update((cell.row_idx, cell.col_idx) for cell in zeros)
    return positions


def _closing_grant_padding(table) -> set[tuple[int, int]]:
    """双栏财政总表末尾三行的右侧占位零；保留原格，不创造业务事实。

    只认原生网格、左右两套独立标签、年末结转/总计夹住的三项期初资金行。
    任何非零金额、右侧业务标签、未证实的空白或 OCR 格都不能认定为占位。
    """
    if (table.table_code != "FIN_04_fiscal_grant_total"
            or table.extraction_method not in {"lines", "lines_strict"}):
        return set()
    from src.services.fiscal_column_identity import canonical_column_mappings
    from src.services.fiscal_table_rules import NINE_TABLE_RULES

    mappings = canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])
    right = [m for m in mappings if m.get("source_label_cols") == [2]]
    if {m["source_col_idx"] for m in right} != {3, 4, 5, 6}:
        return set()
    cells = {(cell.row_idx, cell.col_idx): cell for cell in table.cells}
    labels = ["一、一般公共预算财政拨款", "二、政府性基金预算财政拨款", "三、国有资本经营预算财政拨款"]
    for start in range(1, len(table.rows) - 3):
        rows = table.rows[start - 1:start + 4]
        if (any(len(row) != 7 for row in rows)
                or normalize_text(str(rows[0][2] or "")) != "年末财政拨款结转和结余"
                or normalize_text(str(rows[-1][2] or "")) != "总计"
                or [normalize_text(str(row[0] or "")) for row in rows[1:4]] != labels):
            continue
        positions = set()
        for row_idx in range(start, start + 3):
            label = cells.get((row_idx, 2))
            if not label or label.raw_text.strip() or not label.native_blank_verified or not label.bbox:
                break
            for col in range(3, 7):
                cell = cells.get((row_idx, col))
                if not cell or not cell.bbox or cell.extraction_method not in {"lines", "lines_strict"}:
                    break
                blank = not cell.raw_text.strip() and cell.native_blank_verified
                try:
                    zero = Decimal(cell.raw_text.strip()) == 0 and cell.numeric_value == 0
                except InvalidOperation:
                    zero = False
                if not (zero or blank) or cell.bbox[0] < label.bbox[2] - .5:
                    break
                positions.add((row_idx, col))
            else:
                continue
            break
        if len(positions) == 12:
            return positions
    return set()
