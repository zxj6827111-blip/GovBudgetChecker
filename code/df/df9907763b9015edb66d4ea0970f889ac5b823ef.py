"""从原生字符位置恢复缺线格、金额续行和同格多行，不根据勾稽关系补数。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import math
import re

from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text


def _number(text):
    text = str(text or "").strip()
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        return None
    return Decimal(text.replace(",", ""))


def _box(char):
    try:
        box = tuple(float(char[k]) for k in ("x0", "top", "x1", "bottom"))
        return (
            box
            if all(math.isfinite(v) for v in box) and box[0] < box[2] and box[1] < box[3]
            else None
        )
    except (KeyError, TypeError, ValueError):
        return None


def _union(samples):
    return (
        min(s[2][0] for s in samples),
        min(s[2][1] for s in samples),
        max(s[2][2] for s in samples),
        max(s[2][3] for s in samples),
    )


def _samples(page, box):
    if not box:
        return []
    if any(
        (b := _box(image))
        and min(b[2], box[2]) > max(b[0], box[0])
        and min(b[3], box[3]) > max(b[1], box[1])
        for image in getattr(page, "images", [])
    ):
        return []
    return [
        (i, c, b)
        for i, c in enumerate(page.chars)
        if (b := _box(c))
        and c.get("upright", True)
        and str(c.get("text") or "").strip()
        and box[0] <= (b[0] + b[2]) / 2 < box[2]
        and box[1] <= (b[1] + b[3]) / 2 < box[3]
    ]


def _lines(samples):
    lines = []
    for sample in sorted(samples, key=lambda s: (s[2][1], s[2][0])):
        if (
            not lines
            or abs(sample[2][1] - lines[-1][0][2][1])
            > min(sample[2][3] - sample[2][1], lines[-1][0][2][3] - lines[-1][0][2][1]) * 0.25
        ):
            lines.append([])
        lines[-1].append(sample)
    return [sorted(line, key=lambda s: s[2][0]) for line in lines]


def _text(samples):
    return "\n".join("".join(s[1]["text"] for s in line) for line in _lines(samples))


def _one_numeric_run(samples):
    """相邻两个整数不能因为去掉空格/布局间距就变成另一个金额。"""
    if not samples or len(_lines(samples)) != 1:
        return False
    by_index = sorted(samples, key=lambda s: s[0])
    if any(b[0] != a[0] + 1 for a, b in zip(by_index, by_index[1:], strict=False)):
        return False
    by_x = sorted(samples, key=lambda s: s[2][0])
    return all(
        b[2][0] - a[2][2] <= max(0.5, (a[2][2] - a[2][0]) * 0.9)
        for a, b in zip(by_x, by_x[1:], strict=False)
    )


def _proofs(samples, page_number, kind):
    """每条证明都是原页字符数组中的连续片段，可独立复核，坐标不推测。"""
    groups = []
    for sample in sorted(samples, key=lambda s: s[0]):
        if not groups or sample[0] != groups[-1][-1][0] + 1:
            groups.append([])
        groups[-1].append(sample)
    return [
        {
            "physical_page": page_number,
            "start_char_index": g[0][0],
            "end_char_index": g[-1][0] + 1,
            "text": "".join(s[1]["text"] for s in g),
            "bbox": list(_union(g)),
            "method": "native_contiguous_character_flow",
            "recovery_kind": kind,
        }
        for g in groups
    ]


def _cell(template, samples, row, kind, *, raw=None):
    text = _text(samples) if raw is None else raw
    proofs = _proofs(samples, template.page_number, kind)
    if normalize_text("".join(p["text"] for p in proofs)) != normalize_text(text):
        return None
    numeric = _number(text)
    if numeric is not None and kind != "decimal_wrap" and not _one_numeric_run(samples):
        return None
    return replace(
        template,
        row_idx=row,
        raw_text=text,
        normalized_text=normalize_text(text),
        numeric_value=float(numeric) if numeric is not None else None,
        normalized_value_wan=None,
        bbox=_union(samples) if samples else None,
        row_span=1,
        col_span=1,
        # 新尾行原来根本没有网格单元格，上一行只提供列结构，不能冒充其原文。
        grid_raw_text=None if kind == "open_grid_tail" else template.raw_text or None,
        native_text_sources=proofs,
        native_blank_verified=False,
        origin_fragment_uid=None,
        origin_row_idx=None,
        origin_col_idx=None,
    )


def _bands(table):
    bands = {}
    for col in range(max(map(len, table.rows), default=0)):
        counts = Counter(
            (round(c.bbox[0], 3), round(c.bbox[2], 3))
            for c in table.cells
            if c.col_idx == col and not c.is_header and c.bbox and c.col_span == 1
        )
        if counts:
            (left, right), count = counts.most_common(1)[0]
            if count >= 2:
                bands[col] = (left, right)
    return bands


def _wrapped_amounts(page, table, mappings):
    labels_by_col = {m["source_col_idx"]: m.get("source_label_cols", []) for m in mappings}
    money = set(labels_by_col)
    updates = {}
    for cell in table.cells:
        if (
            cell.is_header
            or cell.col_idx not in money
            or cell.numeric_value is not None
            or not cell.bbox
        ):
            continue
        parts = cell.raw_text.strip().splitlines()
        # 仅恢复小数尾数被折到下一行的单个金额；两笔完整金额永远不能拼接。
        if (
            len(parts) != 2
            or not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d?", parts[0])
            or not re.fullmatch(r"\d{1,2}", parts[1])
        ):
            continue
        precision = len(parts[0].split(".")[1]) + len(parts[1])
        if precision != 2:
            continue
        samples = _samples(page, cell.bbox)
        lines = _lines(samples)
        if len(lines) != 2 or ["".join(s[1]["text"] for s in line) for line in lines] != parts:
            continue
        first, second = map(_union, lines)
        height = max(s[2][3] - s[2][1] for s in samples)
        labels = [
            c
            for c in table.cells
            if c.row_idx == cell.row_idx
            and c.col_idx in labels_by_col[cell.col_idx]
            and c.raw_text.strip()
        ]
        # 两笔独立数也可能恰好是“一位小数 + 整数”。必须有同一行、居中的
        # 单行科目标签支持续行解释；多行科目或仅对齐前半个数时不能拼金额。
        center = (first[1] + second[3]) / 2
        if not labels:
            continue
        label_samples = [_samples(page, label.bbox) for label in labels]
        if any(
            not s
            or len(_lines(s)) != 1
            or normalize_text(_text(s)) != normalize_text(label.raw_text)
            or abs((_union(s)[1] + _union(s)[3]) / 2 - center) > height * 0.4
            for label, s in zip(labels, label_samples, strict=True)
        ):
            continue
        # 同一格内右对齐的下一行尾数，且原始字符顺序连续。
        if (
            abs(first[2] - second[2]) > height * 0.3
            or not height * 0.7 <= second[1] - first[1] <= height * 1.8
        ):
            continue
        if any(
            b[0] != a[0] + 1 for a, b in zip(sorted(samples), sorted(samples)[1:], strict=False)
        ):
            continue
        fixed = _cell(cell, samples, cell.row_idx, "decimal_wrap", raw="".join(parts))
        if fixed is not None:
            updates[cell.row_idx, cell.col_idx] = fixed
    return updates


def _missing_money_cells(page, table, mappings, bands):
    positions = {(c.row_idx, c.col_idx): c for c in table.cells}
    updates = {}
    for mapping in mappings:
        col, labels = mapping["source_col_idx"], mapping.get("source_label_cols", [])
        if col not in bands:
            continue
        for cell in table.cells:
            if cell.col_idx != col or cell.is_header or cell.raw_text or cell.bbox:
                continue
            anchors = [
                positions[cell.row_idx, label]
                for label in labels
                if (cell.row_idx, label) in positions
                and positions[cell.row_idx, label].bbox
                and positions[cell.row_idx, label].raw_text.strip()
            ]
            # 已有标签格的同一行提供垂直范围；不向无科目名的金额行反猜标签。
            if not anchors or len({(a.bbox[1], a.bbox[3]) for a in anchors}) != 1:
                continue
            left, right = bands[col]
            if any(a.bbox[0] < right - 0.5 and a.bbox[2] > left + 0.5 for a in anchors):
                continue  # 原件明确横跨金额列的合并标签格，不是缺失金额格。
            top, bottom = anchors[0].bbox[1], anchors[0].bbox[3]
            samples = _samples(page, (left, top, right, bottom))
            text = _text(samples)
            if _number(text) is None or len(_lines(samples)) != 1:
                continue
            # 原空占位格曾被扣置信度；现在取得了与普通原生格同等级的字符/位置证明，
            # 使用表本身的原生提取质量，不沿用“空格未读到”的降级值。
            fixed = _cell(
                replace(cell, confidence=table.confidence),
                samples,
                cell.row_idx,
                "missing_grid_cell",
            )
            if fixed is not None:
                updates[cell.row_idx, col] = fixed
    return updates


def _split_merged_rows(page, table, mappings):
    money = {m["source_col_idx"] for m in mappings}
    labels = {col for m in mappings for col in m.get("source_label_cols", [])}
    if labels != {0, 1} or table.table_code not in {
        "FIN_02_income",
        "FIN_03_expenditure",
        "FIN_05_general_public_expenditure",
    }:
        return table
    by_row = defaultdict(list)
    for cell in table.cells:
        by_row[cell.row_idx].append(cell)
    rows, cells, changed = [], [], False
    for old_row, row in enumerate(table.rows):
        original = {c.col_idx: c for c in by_row[old_row]}
        split = None
        code, name = original.get(0), original.get(1)
        if code and name and not code.is_header and code.bbox and name.bbox:
            parts = code.raw_text.strip().splitlines()
            if len(parts) > 1 and all(re.fullmatch(r"\d{3}(?:\d{2}){0,2}", part) for part in parts):
                code_lines = _lines(_samples(page, code.bbox))
                if ["".join(s[1]["text"] for s in line) for line in code_lines] == parts:
                    anchors = [sum((_union(line)[1], _union(line)[3])) / 2 for line in code_lines]
                    bounds = [
                        code.bbox[1],
                        *[(a + b) / 2 for a, b in zip(anchors, anchors[1:], strict=False)],
                        code.bbox[3],
                    ]
                    split = [[] for _ in parts]
                    for col, cell in sorted(original.items()):
                        samples = _samples(page, cell.bbox)
                        groups = [
                            [
                                s
                                for s in samples
                                if bounds[i] <= (s[2][1] + s[2][3]) / 2 < bounds[i + 1]
                            ]
                            for i in range(len(parts))
                        ]
                        if normalize_text("".join(_text(g) for g in groups)) != normalize_text(
                            cell.raw_text
                        ):
                            split = None
                            break
                        if (
                            cell.raw_text
                            and col in money
                            and any(
                                _number(_text(g)) is None or len(_lines(g)) != 1 for g in groups
                            )
                        ):
                            split = None
                            break
                        for index, group in enumerate(groups):
                            if col == 1:
                                if not group or not re.search(r"[\u4e00-\u9fff]", _text(group)):
                                    split = None
                                    break
                                box = _union(group)
                                height = max(s[2][3] - s[2][1] for s in group)
                                if abs((box[1] + box[3]) / 2 - anchors[index]) > height * 0.4:
                                    split = None
                                    break
                            fixed = _cell(cell, group, len(rows) + index, "merged_row_split")
                            if fixed is None:
                                split = None
                                break
                            split[index].append(fixed)
                        if split is None:
                            break
        if split and all(
            any(c.col_idx in money and c.numeric_value is not None for c in group)
            for group in split
        ):
            changed = True
            for group in split:
                new_row = list(row)
                for cell in group:
                    new_row[cell.col_idx] = cell.raw_text
                rows.append(new_row)
                cells.extend(group)
        else:
            cells.extend(replace(c, row_idx=len(rows)) for c in by_row[old_row])
            rows.append(list(row))
    return replace(table, rows=rows, cells=cells) if changed else table


def _open_bilateral_tail(page, table, mappings, bands):
    if table.table_code not in {"FIN_04_fiscal_grant_total", "FIN_06_basic_expenditure"}:
        return table
    width = max(map(len, table.rows), default=0)
    if len(bands) != width:
        return table
    heights = [
        c.bbox[3] - c.bbox[1] for c in table.cells if c.row_idx == len(table.rows) - 1 and c.bbox
    ]
    if not heights:
        return table
    samples = _samples(
        page, (table.bbox[0], table.bbox[3], table.bbox[2], table.bbox[3] + min(heights) + 3)
    )
    if not samples or len(_lines(samples)) != 1:
        return table
    # 尾行下方还有紧邻文字时，可能是标签续行，不能只截第一行生成残缺科目。
    font_height = max(s[2][3] - s[2][1] for s in samples)
    extended = _samples(
        page,
        (
            table.bbox[0],
            table.bbox[3],
            table.bbox[2],
            table.bbox[3] + min(heights) + 3 + font_height,
        ),
    )
    if len(_lines(extended)) != 1:
        return table
    groups = {col: [] for col in bands}
    for sample in samples:
        candidates = [
            col
            for col, (left, right) in bands.items()
            if left <= (sample[2][0] + sample[2][2]) / 2 < right
        ]
        if len(candidates) != 1:
            return table
        groups[candidates[0]].append(sample)
    row = [_text(groups[col]) for col in range(width)]
    any_amount = False
    for mapping in mappings:
        amount = row[mapping["source_col_idx"]]
        label_cols = mapping.get("source_label_cols", [])
        if not amount and not any(row[c] for c in label_cols):
            continue
        if _number(amount) is None:
            return table
        labels = [row[c] for c in label_cols if row[c]]
        if table.table_code == "FIN_06_basic_expenditure":
            code = "".join(s for s in labels if s.isdigit())
            if not re.fullmatch(r"\d{3}(?:\d{2}){0,2}", code) or not any(
                re.search(r"[\u4e00-\u9fff]", s) for s in labels
            ):
                return table
        elif not labels or not any(
            re.fullmatch(r"[一二三四五六七八九十]+、[^。:：]+支出", s)
            or s in {"总计", "本年收入合计", "本年支出合计"}
            for s in labels
        ):
            return table
        any_amount = True
    if not any_amount:
        return table
    templates = {c.col_idx: c for c in table.cells if c.row_idx == len(table.rows) - 1}
    if len(templates) != width:
        return table
    cells = [
        _cell(templates[col], groups[col], len(table.rows), "open_grid_tail")
        for col in range(width)
    ]
    if any(c is None for c in cells):
        return table
    return replace(
        table,
        rows=[*table.rows, row],
        cells=[*table.cells, *cells],
        bbox=(*table.bbox[:3], max(s[2][3] for s in samples)),
    )


def repair_native_structure(page, table):
    """仅对可由原字符与已知列车道唯一重建的原生表生效，疑义保持原状。"""
    rule = NINE_TABLE_RULES.get(table.table_code)
    if (
        table.extraction_method not in {"lines", "lines_strict"}
        or not rule
        or not getattr(page, "chars", None)
    ):
        return table
    mappings = canonical_column_mappings(table, rule)
    if not mappings:
        return table
    original = table
    table = _split_merged_rows(page, table, mappings)
    bands = _bands(table)
    updates = _wrapped_amounts(page, table, mappings)
    updates.update(_missing_money_cells(page, table, mappings, bands))
    if updates:
        rows = [list(row) for row in table.rows]
        for (r, c), cell in updates.items():
            rows[r][c] = cell.raw_text
        table = replace(
            table, rows=rows, cells=[updates.get((c.row_idx, c.col_idx), c) for c in table.cells]
        )
    table = _open_bilateral_tail(page, table, mappings, bands)
    if table is original:
        return table
    uid = hashlib.sha256(
        json.dumps([original.fragment_uid, table.rows], ensure_ascii=False).encode()
    ).hexdigest()[:16]
    return replace(table, fragment_uid=uid)
