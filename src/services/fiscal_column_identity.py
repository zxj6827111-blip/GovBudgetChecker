"""Resolve semantic columns from literal headers and physical merged cells."""
from __future__ import annotations

from src.services.fiscal_table_rules import is_table_preamble_row, normalize_text

#: 单位版收支总表把「本年支出」拆成 合计 / 基本支出（人员经费、公用经费）/ 项目支出，按叶表头定列。
SPLIT_EXPENDITURE_LEAVES = {"合计": "expenditure_actual", "基本支出": "expenditure_basic",
                            "人员经费": "expenditure_personnel", "公用经费": "expenditure_public",
                            "项目支出": "expenditure_project"}


def header_paths(fragment):
    count = min(int(fragment.header_row_count or 1), len(fragment.rows), 8)
    rows = fragment.rows[:count]
    width = max((len(row) for row in rows), default=0)
    cells = {(cell.row_idx, cell.col_idx): cell for cell in fragment.cells}
    # 列几何只取首页的满宽行：合并视图里续页的合计脚注（合计横跨三格、只剩
    # 4 列）或误并入的异表，同一列号对应的是另一条物理列，取它的最窄框会让
    # 列中心落进相邻表头，名称列因此丢掉「项目」父表头而退出维度列。
    full_width_rows = {index for index, row in enumerate(fragment.rows) if len(row) == width}
    geometry_cells = [cell for cell in fragment.cells
                      if cell.row_idx in full_width_rows and cell.page_number == fragment.page_number]
    # 框进表格的表题、编制单位行横跨全表，不能当作每一列的父表头。
    preamble = ({index for index, row in enumerate(rows) if is_table_preamble_row(row)}
                if fragment.extraction_method in {"lines", "lines_strict"} else set())
    # 行分组锚：非空格按几何跨度（bbox 覆盖到的列中心，即合并格/跨列文本的
    # 记录跨度）圈定覆盖区间。用途有二：
    # 1) 上一行的分组边界——行内空占位格向左延续父级文本时不得跨组
    #    （三公矩阵「公务接待费」分组下的空行不得继承左邻「运行维护费」）；
    # 2) 候选父格自身的记录跨度——bbox 覆盖 ≥2 列中心说明物理跨度仍在，
    #    外推不得越出该跨度；只有单列覆盖（父行画竖线、跨度被切碎）才允许
    #    向左自由延续（过宽总表把「财政拨款支出」挤到下页时，首页的
    #    「财政拨款收入」不能越过自身跨度去覆盖支出侧空列）。
    column_centers = []
    for col in range(width):
        boxes = ([cell.bbox for cell in geometry_cells if cell.col_idx == col and cell.bbox]
                 or [cell.bbox for cell in fragment.cells if cell.col_idx == col and cell.bbox])
        narrow = min(boxes, key=lambda b: b[2] - b[0]) if boxes else None
        column_centers.append((narrow[0] + narrow[2]) / 2 if narrow else None)
    row_anchors, row_anchor_spans = [], []
    for row_idx, row in enumerate(rows):
        anchors, spans = {}, {}
        for anchor_col, value in enumerate(row):
            if not str(value or ""):
                continue
            cell = cells.get((row_idx, anchor_col))
            box = getattr(cell, "bbox", None) if cell is not None else None
            covered = set()
            if box:
                for index, center in enumerate(column_centers):
                    if center is not None and box[0] < center < box[2]:
                        covered.add(index)
            if anchor_col not in covered:
                covered.add(anchor_col)
            spans[anchor_col] = covered
            for index in covered:
                anchors.setdefault(index, anchor_col)
        row_anchors.append(anchors)
        row_anchor_spans.append(spans)
    paths = []
    for col in range(width):
        center = column_centers[col]
        path = []
        leaf_row = rows[-1] if rows else []
        leaf_label = str(leaf_row[col] or "") if col < len(leaf_row) else ""
        for row_idx, row in enumerate(rows):
            if row_idx in preamble:
                path.append("")
                continue
            text = str(row[col] or "") if col < len(row) else ""
            own = cells.get((row_idx, col))
            if not text and center is not None and (
                own is None or own.bbox is None or not str(own.raw_text or "").strip()
            ):
                spanning = [cell for cell in fragment.cells
                            if cell.row_idx == row_idx and cell.bbox and cell.raw_text
                            and cell.bbox[0] < center < cell.bbox[2]]
                if len(spanning) == 1:
                    text = spanning[0].raw_text
            if not text and row_idx + 1 < len(rows) and leaf_label:
                # 合并父表头在物理网格里只落首子列：父行画竖线时（如财政拨款
                # 收入支出决算总表的「收入｜支出」父行），其余子列是带边框的
                # 空占位格，几何跨度恢复无从下手。此时父级身份按同行左侧最近
                # 非空格延续——该列在叶表头行有自己的非空标签，说明它是同一
                # 父组下的真实数据列；叶表头行不外推，叶子是该列自己的标签，
                # 空叶不得继承左邻叶子文本。
                upper = row_anchors[row_idx - 1] if row_idx > 0 else None
                group = upper.get(col) if upper is not None else None
                spans_here = row_anchor_spans[row_idx]
                for left in range(col - 1, -1, -1):
                    left_text = str(row[left] or "") if left < len(row) else ""
                    if not left_text:
                        continue
                    if upper is not None and upper.get(left) != group:
                        # 左候选格属上一行的另一分组：本列与它不同组，不得借用。
                        break
                    recorded = spans_here.get(left)
                    if recorded and len(recorded) >= 2 and col not in recorded:
                        # 候选父格的物理跨度已被网格记录且未覆盖本列：
                        # 空列属于缺失的父表头（如被挤到下页），不得越界借用。
                        break
                    text = left_text
                    break
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


def code_dimension_columns(fragment, paths=None):
    """编码维度须由字面表头确认，允许经济分类表的规范缩写。"""
    paths = header_paths(fragment) if paths is None else paths
    columns = {col for col, path in enumerate(paths) if any('编码' in part for part in path)}
    if fragment.table_code == 'FIN_06_basic_expenditure':
        for col, path in enumerate(paths[:-2]):
            # 有些原表只印“经济分类科目”；相邻名称及决算/预算金额列共同确定其角色。
            # 保留原字面表头，不把任意含“科目”的业务名称当成编码列。
            if ('经济分类科目' in path and '科目名称' in paths[col + 1]
                    and any(part in {'决算数', '支出决算数', '预算数', '年初预算数'} for part in paths[col + 2])):
                columns.add(col)
    return columns


def split_expenditure_ledger(paths, dimensions, mapping):
    """单位版收支总表：收入侧一列预算数，支出侧按叶表头分列；每个角色须唯一且有合计与项目支出。"""
    income, spend = [], {}
    for col, path in enumerate(paths):
        parts = [part for part in path if part]
        if col in dimensions or not parts:
            continue
        if "本年收入" in parts and parts[-1] in {"预算数", "年初预算数"}:
            income.append(col)
        elif "本年支出" in parts and parts[-1] in SPLIT_EXPENDITURE_LEAVES:
            spend.setdefault(SPLIT_EXPENDITURE_LEAVES[parts[-1]], []).append(col)
    if (len(income) != 1 or any(len(cols) != 1 for cols in spend.values())
            or not {"expenditure_actual", "expenditure_project"} <= spend.keys()):
        return []
    return [mapping(income[0], "income_actual"), *(mapping(cols[0], measure) for measure, cols in spend.items())]


def canonical_column_mappings(fragment, rule):
    if fragment.extraction_method == "page_text_fallback":
        return []
    paths = header_paths(fragment)
    if not paths:
        return []
    dimension_tokens = {"项目", "科目编码", "功能分类科目编码", "经济分类科目编码", "科目名称", "行次", "栏次", "类", "款", "项"}
    # 「功能分类科目名称 / 经济分类科目名称」都是名称维度，不能只靠父级「项目」兜底。
    dimensions = ({col for col, path in enumerate(paths)
                   if any(part in dimension_tokens or part.endswith("科目名称") for part in path)}
                  | code_dimension_columns(fragment, paths))

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

    if rule.code == "FIN_01_income_expenditure_total":
        split = split_expenditure_ledger(paths, dimensions, mapping)
        if split:
            return split

    # Paired summary ledgers have distinct literal income/expenditure sides.
    if rule.code in {"FIN_01_income_expenditure_total", "FIN_04_fiscal_grant_total"}:
        # 并列收支总表的顶层合并表头（如“收入”/“支出”、“财政拨款收入”/“财政拨款支出”）
        # 在 pdfplumber 切分单元格后常只落在左侧第一列；沿顶行向右侧非维度列传递，碰到下一组表头或维度列即停。
        paired_paths = [list(path) for path in paths]
        current_group = ""
        for col, path in enumerate(paired_paths):
            top = path[0] if path else ""
            if col in dimensions:
                current_group = ""
                continue
            if top.startswith(("收入", "支出", "财政拨款收入", "财政拨款支出", "本年收入", "本年支出")):
                current_group = top
            elif top:
                current_group = ""
            elif current_group and path and any(path):
                path[0] = current_group

        result = []
        first_expenditure_col = next(
            (
                col
                for col, path in enumerate(paired_paths)
                if col not in dimensions
                and (any(part.startswith("支出") or "财政拨款支出" in part for part in path) or "合计" in path)
            ),
            len(paired_paths),
        )
        for col, path in enumerate(paired_paths):
            if col in dimensions:
                continue
            if any(part.startswith("收入") or "财政拨款收入" in part for part in path) and any("决算数" in part for part in path):
                result.append(mapping(col, "income_actual"))
            elif any(part.startswith("支出") or "财政拨款支出" in part for part in path) and "合计" in path:
                result.append(mapping(col, "expenditure_actual"))
            elif any(part.startswith("支出") or "财政拨款支出" in part for part in path) and any(part == "决算数" for part in path):
                result.append(mapping(col, "expenditure_actual"))
            # 预算版财政拨款收支总表：父表头是「财政拨款收入 / 财政拨款支出」，
            # 收入侧阶段词是「预算数」。只认这两个完整父表头，不放宽决算版判据。
            elif "财政拨款收入" in path and any(part in {"预算数", "年初预算数"} for part in path):
                result.append(mapping(col, "income_actual"))
            elif "财政拨款支出" in path and "合计" in path:
                result.append(mapping(col, "expenditure_actual"))
            elif (
                rule.code == "FIN_04_fiscal_grant_total"
                and col < first_expenditure_col
                and any(part == "决算数" for part in path)
                and not any("支出" in part for part in path)
            ):
                result.append(mapping(col, "income_actual"))
            elif (rule.code == "FIN_04_fiscal_grant_total" and path[-1:] == ["合计"]
                  and not any("收入" in part for part in path)):
                # 横向过宽的总表会把「财政拨款支出」父表头挤到下一页，
                # 本页只剩叶子「合计」；收入侧没有合计列，它只能是支出合计。
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

    # 父级同时覆盖多列时，叶子表头才是各列身份：「公务用车购置及运行费」
    # 下的 小计/购置费/运行费 各是一个指标，父级别名不能替它们做决定。
    leaf_bound = set()
    for measure, pairs in rule.leaf_aliases.items():
        for col, path in enumerate(paths):
            if col in dimensions or col in leaf_bound:
                continue
            parts = [part for part in path if part]
            if len(parts) < 2:
                continue
            leaf, ancestors = parts[-1], parts[:-1]
            if any(leaf == normalize_text(leaf_alias) and any(normalize_text(parent) in part for part in ancestors)
                   for parent, leaf_alias in pairs):
                result.append(mapping(col, measure))
                leaf_bound.add(col)

    for measure, aliases in rule.measure_aliases.items():
        candidates = []
        for col, path in enumerate(paths):
            if col in dimensions or col in leaf_bound:
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
        elif all(any(stage in ''.join(paths[col])for stage in ('年初预算','调整后预算','调整预算','全年预算','决算'))
                 for col in columns) and len({tuple(paths[col])for col in columns})==len(columns):
            # 同指标的不同预算阶段是不同操作数，保留各自来源列和原表头。
            result.extend(mapping(col,measure)for col in columns)
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
