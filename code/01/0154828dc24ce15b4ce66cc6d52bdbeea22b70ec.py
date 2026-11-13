"""在原页坐标中联合编码起点、金额印迹和名称折行，输出逻辑副本。

PDF 字符数组的顺序只用于证据索引，不用于阅读顺序。没有唯一几何锚点时
保留原候选，交由补读；此处不查科目字典，也不根据金额差额修复原件。
"""

from collections import defaultdict
from dataclasses import replace
from statistics import median
import math
import re

from src.engine.fiscal_lexeme import lex_numeric


def compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def valid_samples(artifact):
    pages = defaultdict(list)
    for source in artifact.source_fragments:
        box = source.get("bbox")
        index = source.get("char_index")
        if (
            source.get("granularity") != "character"
            or not isinstance(index, int)
            or isinstance(index, bool)
            or not box
            or len(box) != 4
            or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
            or box[2] <= box[0]
            or box[3] <= box[1]
            or not str(source.get("text") or "").strip()
            or source.get("upright") is False
        ):
            continue
        pages[source["physical_page"]].append(source)
    return pages


def lines(samples):
    groups = []
    for sample in sorted(samples, key=lambda s: (s["bbox"][1], s["bbox"][0], s["char_index"])):
        box = sample["bbox"]
        if (
            not groups
            or abs(box[1] - median(s["bbox"][1] for s in groups[-1])) > (box[3] - box[1]) * 0.35
        ):
            groups.append([])
        groups[-1].append(sample)
    return [sorted(group, key=lambda s: (s["bbox"][0], s["char_index"])) for group in groups]


def literal(samples):
    return "\n".join("".join(s["text"] for s in line) for line in lines(samples))


def proofs(samples, page):
    """可见顺序与物理索引分开：每个索引区间都能直接回读原字符数组。"""
    groups = []
    for line in lines(samples):
        for sample in line:
            if not groups or sample["char_index"] != groups[-1][-1]["char_index"] + 1:
                groups.append([])
            groups[-1].append(sample)
    return [
        {
            "physical_page": page,
            "start_char_index": group[0]["char_index"],
            "end_char_index": group[-1]["char_index"] + 1,
            "text": "".join(s["text"] for s in group),
            "bbox": [
                min(s["bbox"][0] for s in group),
                min(s["bbox"][1] for s in group),
                max(s["bbox"][2] for s in group),
                max(s["bbox"][3] for s in group),
            ],
            "method": "native_geometry_business_row",
        }
        for group in groups
    ]


def inside(sample, box):
    b = sample["bbox"]
    return box[0] <= (b[0] + b[2]) / 2 < box[2] and box[1] <= (b[1] + b[3]) / 2 < box[3]


def bind_literal_sources(table, pages):
    """原格字面已正确时只绑定原字符，不重新分配行。"""
    cells = []
    for cell in table.cells:
        if cell.extraction_method == "ocr" or cell.native_text_sources or not cell.bbox:
            cells.append(cell)
            continue
        samples = [s for s in pages.get(cell.page_number, []) if inside(s, cell.bbox)]
        if samples and compact(literal(samples)) == compact(cell.raw_text):
            cell = replace(cell, native_text_sources=proofs(samples, cell.page_number))
        cells.append(cell)
    return replace(table, cells=cells)


def rebuild_business_rows(table, mappings, pages):
    """有编码起点的纵向科目使用统一起点分区；双边表分别求解。

    横线不截断名称。金额为空的编码行仍建立起点，且不会生成零金额。
    未能唯一识别的无编码起点继续保留原候选，不能强接到前项。
    """
    from src.services.fiscal_column_identity import code_dimension_columns

    table = bind_literal_sources(table, pages)
    if table.extraction_method not in {"lines", "lines_strict"}:
        return table, []
    code_cols = code_dimension_columns(table)
    updates, diagnostics = {}, []
    sides = {tuple(m.get("source_label_cols") or []) for m in mappings}
    for label_cols in sorted(sides):
        codes = set(label_cols).intersection(code_cols)
        names = set(label_cols) - codes
        if not codes or len(names) != 1:
            continue
        name_col = next(iter(names))
        amount_cols = {
            m["source_col_idx"]
            for m in mappings
            if tuple(m.get("source_label_cols") or []) == label_cols
        }
        for page in sorted({c.page_number for c in table.cells}):
            source = pages.get(page, [])
            names_by_row = {
                c.row_idx: c
                for c in table.cells
                if c.page_number == page and c.col_idx == name_col and not c.is_header and c.bbox
            }
            if len(names_by_row) < 2:
                continue
            # 跨页孤立尾字已有独立证明；本页求解不得吞掉另一页的证据。
            if any(
                s.get("physical_page", page) != page
                for c in names_by_row.values()
                for s in c.native_text_sources
            ):
                continue
            band = min((c.bbox for c in names_by_row.values()), key=lambda b: b[2] - b[0])
            left, right = band[0], band[2]
            top = min(c.bbox[1] for c in names_by_row.values())
            bottom = max(c.bbox[3] for c in names_by_row.values())
            excluded = {
                i
                for c in table.cells
                for proof in c.native_text_sources
                for item in proof.get("excluded_header_sources", [])
                if item.get("physical_page") == page
                for i in range(item.get("start_char_index", 0), item.get("end_char_index", 0))
            }
            excluded.update(
                i
                for c in table.cells
                if c.is_header
                for proof in c.native_text_sources
                if proof.get("physical_page") == page
                for i in range(proof.get("start_char_index", 0), proof.get("end_char_index", 0))
            )
            samples = [
                s
                for s in source
                if s["char_index"] not in excluded
                and left <= (s["bbox"][0] + s["bbox"][2]) / 2 < right
                and top <= (s["bbox"][1] + s["bbox"][3]) / 2 < bottom
            ]
            name_lines = lines(samples)
            anchors = {}
            proven_rows = set()
            proven_owners = {}
            source_by_index = {s["char_index"]: s for s in source}
            for row, name in sorted(names_by_row.items()):
                # 已有修复的完整字面是起笔候选，先逐索引核验，不能把居中的编码
                # 基线当作多行名称首行，误吞下一科目的上半行。
                witnessed = []
                for proof in name.native_text_sources:
                    indexes = range(
                        proof.get("start_char_index", 0), proof.get("end_char_index", 0)
                    )
                    selected = [source_by_index[i] for i in indexes if i in source_by_index]
                    if selected and "".join(s["text"] for s in selected) == proof.get("text"):
                        witnessed.extend(selected)
                if witnessed and compact("".join(s["text"] for s in witnessed)) == compact(
                    name.raw_text
                ):
                    anchors[row] = min(s["bbox"][1] for s in witnessed)
                    if any(
                        p.get("method") == "native_contiguous_character_flow"
                        for p in name.native_text_sources
                    ):
                        proven_rows.add(row)
                        proven_owners.update({s["char_index"]: row for s in witnessed})
                    continue
                row_cells = [c for c in table.cells if c.page_number == page and c.row_idx == row]
                code_cells = [c for c in row_cells if c.col_idx in codes and c.bbox]
                code_samples = [s for s in source if any(inside(s, c.bbox) for c in code_cells)]
                code_lines = [g for g in lines(code_samples) if compact(literal(g)).isdigit()]
                amounts = [
                    s
                    for c in row_cells
                    if c.col_idx in amount_cols
                    and c.bbox
                    and lex_numeric(c.raw_text).kind in {"number", "amount"}
                    for s in source
                    if inside(s, c.bbox)
                ]
                if code_lines:
                    if len(code_lines) != 1:
                        if not amounts:
                            continue
                        y = median(s["bbox"][1] for s in amounts)
                        ranked = sorted(
                            code_lines, key=lambda g: abs(median(s["bbox"][1] for s in g) - y)
                        )
                        if (
                            len(ranked) > 1
                            and abs(
                                abs(ranked[0][0]["bbox"][1] - y) - abs(ranked[1][0]["bbox"][1] - y)
                            )
                            < 0.5
                        ):
                            continue
                        code_lines = ranked[:1]
                    anchors[row] = median(s["bbox"][1] for s in code_lines[0])
                else:
                    own = [g for g in name_lines if inside(g[0], name.bbox)]
                    # 总计和无编码业务行是独立起点；来源为空时不能继承前行。
                    if own and compact(literal([s for g in own for s in g])) == compact(
                        name.raw_text
                    ):
                        anchors[row] = median(s["bbox"][1] for s in own[0])
            if set(anchors) != set(names_by_row):
                diagnostics.append(
                    {
                        "page": page,
                        "name_column": name_col,
                        "status": "row_start_requires_regional_read",
                    }
                )
                continue
            ordered = sorted(anchors, key=anchors.get)
            if any(anchors[b] - anchors[a] < 1 for a, b in zip(ordered, ordered[1:], strict=False)):
                continue
            owned = defaultdict(list)
            for group in name_lines:
                for sample in group:
                    if sample["char_index"] in proven_owners:
                        owned[proven_owners[sample["char_index"]]].append(sample)
                group = [s for s in group if s["char_index"] not in proven_owners]
                if not group:
                    continue
                y = median(s["bbox"][1] for s in group)
                height = median(s["bbox"][3] - s["bbox"][1] for s in group)
                aligned = [row for row in ordered if abs(anchors[row] - y) <= height * 0.4]
                if len(aligned) == 1:
                    row = aligned[0]
                elif aligned:
                    break
                else:
                    previous = [row for row in ordered if anchors[row] < y]
                    if not previous:
                        break
                    row = previous[-1]
                owned[row].extend(group)
            else:
                for row, cell in names_by_row.items():
                    if row in proven_rows:
                        continue
                    selected = owned[row]
                    if not selected:
                        continue
                    text = literal(selected)
                    if compact(text) == compact(cell.raw_text):
                        continue
                    updates[row, name_col] = replace(
                        cell,
                        raw_text=text,
                        normalized_text=compact(text),
                        numeric_value=None,
                        normalized_value_wan=None,
                        grid_raw_text=cell.grid_raw_text
                        if cell.grid_raw_text is not None
                        else cell.raw_text,
                        native_text_sources=proofs(selected, page),
                        native_blank_verified=False,
                    )
                    diagnostics.append(
                        {
                            "page": page,
                            "row": row,
                            "column": name_col,
                            "before": cell.raw_text,
                            "after": text,
                            "status": "geometry_row_rebuilt",
                        }
                    )
    rows = [list(row) for row in table.rows]
    for (row, col), cell in updates.items():
        rows[row][col] = cell.raw_text
    view = replace(
        table, rows=rows, cells=[updates.get((c.row_idx, c.col_idx), c) for c in table.cells]
    )
    return bind_literal_sources(view, pages), diagnostics
