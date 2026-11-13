"""Resolve semantic columns from literal headers and physical merged cells."""
from __future__ import annotations

from src.services.fiscal_table_rules import normalize_text


def header_paths(fragment):
    count = min(int(fragment.header_row_count or 1), len(fragment.rows), 8)
    rows = fragment.rows[:count]
    width = max((len(row) for row in rows), default=0)
    cells = {(cell.row_idx, cell.col_idx): cell for cell in fragment.cells}
    paths = []
    for col in range(width):
        boxes = [cell.bbox for cell in fragment.cells if cell.col_idx == col and cell.bbox]
        narrow = min(boxes, key=lambda b: b[2] - b[0]) if boxes else None
        center = (narrow[0] + narrow[2]) / 2 if narrow else None
        path = []
        for row_idx, row in enumerate(rows):
            text = str(row[col] or "") if col < len(row) else ""
            own = cells.get((row_idx, col))
            if not text and center is not None and (own is None or own.bbox is None):
                spanning = [cell for cell in fragment.cells
                            if cell.row_idx == row_idx and cell.bbox and cell.raw_text
                            and cell.bbox[0] < center < cell.bbox[2]]
                if len(spanning) == 1:
                    text = spanning[0].raw_text
            path.append(normalize_text(text))
        paths.append(path)
    # 基金/国资表可能把支出子表头与收入合计数印在同一行。
    # 仅继承“本年支出”物理父表头下的三个明确叶表头，不把该行收入金额当表头丢弃。
    for row_idx, col in mixed_expenditure_header_cells(fragment, paths):
        paths[col].append(normalize_text(str(fragment.rows[row_idx][col] or '')))
    return paths


def mixed_expenditure_header_cells(fragment, paths=None):
    """同一原行内只有支出叶表头是表头，收入合计仍是金额格。"""
    count = min(int(fragment.header_row_count or 1), len(fragment.rows), 8)
    if paths is None:
        # header_paths 传入已有路径，避免递归；外部调用使用同一列身份判据。
        paths = header_paths(fragment)
    cells = {(cell.row_idx, cell.col_idx): cell for cell in fragment.cells}
    if fragment.table_code in {'FIN_08_gov_fund', 'FIN_09_state_capital'} and count < len(fragment.rows):
        mixed = fragment.rows[count]
        if mixed and normalize_text(str(mixed[0] or '')) == '合计':
            leaves = [(col, normalize_text(str(value or ''))) for col, value in enumerate(mixed)
                      if col < len(paths) and '本年支出' in paths[col]
                      and normalize_text(str(value or '')) in {'合计', '基本支出', '项目支出'}
                      and cells.get((count, col)) is not None and cells[count, col].bbox]
            if len(leaves) == 3 and {value for _, value in leaves} == {'合计', '基本支出', '项目支出'}:
                return {(count, col) for col, _ in leaves}
    return set()


def canonical_column_mappings(fragment, rule):
    if fragment.extraction_method == "page_text_fallback":
        return []
    paths = header_paths(fragment)
    if not paths:
        return []
    dimension_tokens = {"项目", "科目编码", "功能分类科目编码", "经济分类科目编码", "科目名称", "行次", "栏次", "类", "款", "项"}
    dimensions = {col for col, path in enumerate(paths) if any(part in dimension_tokens for part in path)}

    def labels(col):
        preceding = sorted(d for d in dimensions if d < col)
        if not preceding:
            return []
        result = [preceding[-1]]
        for d in reversed(preceding[:-1]):
            if d == result[0] - 1:
                result.insert(0, d)
            else:
                break
        return result

    def mapping(col, measure):
        return {"source_col_idx": col, "canonical_measure": measure,
                "source_col_name": "/".join(part for part in paths[col] if part),
                "source_label_cols": labels(col), "confidence": .9}

    # Paired summary ledgers have distinct literal income/expenditure sides.
    if rule.code in {"FIN_01_income_expenditure_total", "FIN_04_fiscal_grant_total"}:
        result = []
        for col, path in enumerate(paths):
            if col in dimensions:
                continue
            if any(part.startswith("收入") for part in path) and any("决算数" in part for part in path):
                result.append(mapping(col, "income_actual"))
            elif any(part.startswith("支出") for part in path) and "合计" in path:
                result.append(mapping(col, "expenditure_actual"))
            elif any(part.startswith("支出") for part in path) and any(part == "决算数" for part in path):
                result.append(mapping(col, "expenditure_actual"))
        if rule.code == "FIN_01_income_expenditure_total" and result:
            return result
    else:
        result = []

    # A horizontal three-public matrix explicitly states budget/actual in its
    # leaf header. Preserve every indicator/stage column, never copy neighbours.
    stages = {"预算数", "年初预算数", "决算数", "支出决算数"}
    matrix = rule.code == "FIN_07_three_public" and not dimensions and sum(any(p in stages for p in path) for path in paths) >= 4
    if matrix:
        result = []
        for col, path in enumerate(paths):
            if not any(part in stages for part in path):
                continue
            candidates = []
            for depth, part in enumerate(path):
                for measure, aliases in rule.measure_aliases.items():
                    if measure in {"budget", "actual"}:
                        continue
                    for alias in aliases:
                        alias = normalize_text(alias)
                        if alias and alias in part:
                            candidates.append((depth, len(alias), measure))
            if not candidates:
                continue
            best = max((depth, length) for depth, length, _ in candidates)
            winners = {measure for depth, length, measure in candidates if (depth, length) == best}
            if len(winners) == 1:
                result.append(mapping(col, winners.pop()))
        return result

    for measure, aliases in rule.measure_aliases.items():
        candidates = []
        for col, path in enumerate(paths):
            if col in dimensions:
                continue
            header = "".join(path)
            aliases_norm = [normalize_text(alias) for alias in aliases]
            scores = []
            for alias in aliases_norm:
                if not alias or alias not in header:
                    continue
                generic_stage = alias in {'决算数', '预算数', '支出决算数'}
                # An explicit business header beats a shared stage wrapper.
                # e.g. 合计/基本支出/项目支出 all sit under 支出决算数.
                score = (1 if generic_stage else 3) if alias in path else (0 if generic_stage else 2)
                scores.append(score)
            if scores:
                candidates.append((max(scores), col))
        if not candidates:
            continue
        best = max(score for score, _ in candidates)
        columns = [col for score, col in candidates if score == best]
        if len(columns) == 1:
            result.append(mapping(columns[0], measure))
        elif all(labels(col) for col in columns) and len({tuple(labels(col)) for col in columns}) == len(columns):
            result.extend(mapping(col, measure) for col in columns)
        # Otherwise duplicate columns have no distinct literal identity.
    by_col = {}
    for item in result:
        col = item['source_col_idx']
        previous = by_col.get(col)
        if previous is None or item['canonical_measure'] in {"income_actual", "expenditure_actual"}:
            by_col[col] = item
    return list(by_col.values())
