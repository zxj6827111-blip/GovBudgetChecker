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


def bind_native_grid_character_sources(page, table):
    """为原生金额格附加连续字符证明；不把字符拼接失败当作已核验。"""
    if table.extraction_method not in {"lines", "lines_strict"}:
        return table
    cells = []
    for cell in table.cells:
        if cell.numeric_value is None or not cell.bbox or cell.native_text_sources:
            cells.append(cell)
            continue
        samples = _samples(page, cell.bbox)
        if not _one_numeric_run(samples) or normalize_text(_text(samples)) != normalize_text(cell.raw_text):
            cells.append(cell)
            continue
        cells.append(replace(cell, grid_raw_text=cell.grid_raw_text if cell.grid_raw_text is not None else cell.raw_text,
                             native_text_sources=_proofs(samples, cell.page_number, "native_grid_character_binding")))
    return replace(table, cells=cells)


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


def _cross_grid_decimal_tails(page, table, mappings, bands, *, continuation=False):
    """以原字符流首字归行，恢复越过横线的尾数，并移除邻格误收的尾字。

    仅有两位小数、连续原字符、相同金额车道、右对齐尾字及同基线标签
    同时成立时才调整。一个格内仍有不能归属的字符时整组保持原状。
    """
    updates, assigned, pending = {}, {}, {}
    for mapping in mappings:
        col = mapping["source_col_idx"]
        if col not in bands or not table.bbox:
            continue
        left, right = bands[col]
        cells = [c for c in table.cells if c.col_idx == col and c.bbox
                 and (continuation or not c.is_header) and c.col_span == 1]
        if not cells:
            continue
        samples = _samples(page, (left, table.bbox[1], right, table.bbox[3]))
        groups = []
        for sample in sorted(samples, key=lambda s: s[0]):
            if not groups or sample[0] != groups[-1][-1][0] + 1:
                groups.append([])
            groups[-1].append(sample)
        # 连续性被空白/异列对象打断时不能拼金额，但也不能把可疑前半段
        # 当成完整的一位小数放行。先记录紧邻跨线尾字，供读取门禁明确拒绝。
        for head, tail in zip(groups, groups[1:], strict=False):
            head_text, tail_text = _text(head), _text(tail)
            if (len(_lines(head)) != 1 or len(_lines(tail)) != 1
                    or not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d?", head_text)
                    or not re.fullmatch(r"\d{1,2}", tail_text)
                    or len(head_text.split(".")[1]) + len(tail_text) != 2):
                continue
            hb, tb = _union(head), _union(tail)
            height = max(s[2][3] - s[2][1] for s in head + tail)
            owners = [c for c in cells if c.bbox[1] <= (head[0][2][1] + head[0][2][3]) / 2 < c.bbox[3]]
            if (len(owners) == 1 and height * .7 <= tb[1] - hb[1] <= height * 1.8
                    and (tb[1] + tb[3]) / 2 >= owners[0].bbox[3]):
                pending[owners[0].row_idx, col] = (owners[0], head + tail)
        owned, crossed = defaultdict(list), set()
        for group in groups:
            lines = _lines(group)
            if len(lines) not in {1, 2}:
                continue
            parts = ["".join(s[1]["text"] for s in line) for line in lines]
            text = "".join(parts)
            if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}", text):
                continue
            if any(not _one_numeric_run(line) for line in lines):
                continue
            # 原对象的首字必须先于尾字；不能把相邻两个独立数改写为一笔。
            if [s[0] for line in lines for s in line] != [s[0] for s in group]:
                continue
            first = group[0][2]
            owners = [c for c in cells if c.bbox[1] <= (first[1] + first[3]) / 2 < c.bbox[3]]
            if len(owners) != 1:
                continue
            owner = owners[0]
            if len(lines) == 2:
                head, tail = map(_union, lines)
                height = max(s[2][3] - s[2][1] for s in group)
                if (re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d?", parts[0])
                        and re.fullmatch(r"\d{1,2}", parts[1])
                        and (tail[1] + tail[3]) / 2 >= owner.bbox[3]):
                    pending[owner.row_idx, col] = (owner, group)
                if (not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d?", parts[0])
                        or not re.fullmatch(r"\d{1,2}", parts[1])
                        or abs(head[2] - tail[2]) > height * .3
                        or not height * .7 <= tail[1] - head[1] <= height * 1.8
                        or (tail[1] + tail[3]) / 2 < owner.bbox[3]):
                    continue
                labels = [c for c in table.cells if c.row_idx == owner.row_idx
                          and c.col_idx in mapping.get("source_label_cols", []) and c.raw_text.strip()]
                anchors = [_samples(page, c.bbox) for c in labels]
                if not any(s and len(_lines(s)) == 1
                           and abs((_union(s)[1] + _union(s)[3] - head[1] - head[3]) / 2) <= height * .45
                           for s in anchors):
                    continue
                crossed.add(owner.row_idx)
            owned[owner.row_idx].append((owner, group, text))
        if not crossed:
            continue
        # 每个字符只能有一个首字归属。邻行里的尾字只能来自这次已证明的组。
        valid = {r: entries[0] for r, entries in owned.items() if len(entries) == 1}
        index_owner = {s[0]: r for r, (_, g, _) in valid.items() for s in g}
        affected = {r for r in valid if r in crossed or any(
            index_owner.get(s[0]) in crossed for s in _samples(page, valid[r][0].bbox))}
        if not crossed <= valid.keys() or any(
            s[0] not in index_owner for r in affected for s in _samples(page, valid[r][0].bbox)
        ):
            continue
        for row in affected:
            cell, group, text = valid[row]
            proofs = _proofs(group, cell.page_number, "cross_grid_decimal_tail")
            for proof in proofs:
                proof["source_grid_bbox"] = list(cell.bbox)
                proof["source_grid_fragment_uid"] = cell.origin_fragment_uid or table.fragment_uid
                proof["source_grid_row"] = cell.origin_row_idx if cell.origin_row_idx is not None else row
                proof["source_grid_col"] = cell.origin_col_idx if cell.origin_col_idx is not None else col
            glyph_box = _union(group)
            updates[row, col] = replace(
                cell, raw_text=text, normalized_text=normalize_text(text), numeric_value=float(_number(text)),
                normalized_value_wan=None, grid_raw_text=cell.raw_text,
                native_text_sources=proofs, native_blank_verified=False,
                # 保留横向原格边界；纵向证据包含跨线尾字，原框另存于证明。
                bbox=(cell.bbox[0], min(cell.bbox[1], glyph_box[1]), cell.bbox[2], max(cell.bbox[3], glyph_box[3])),
            )
            assigned.update({s[0]: (row, col) for s in group})
    # 极窄尾行可能把最后一个尾字抽成横跨全表的“8”。仅在其全部字符已
    # 被上述金额组拥有时移出，不能将未解释的数字或空白擅自变成零。
    for cell in table.cells:
        if (cell.row_idx, cell.col_idx) in updates or not cell.raw_text.strip() or not cell.bbox:
            continue
        samples = _samples(page, cell.bbox)
        if samples and all(s[0] in assigned and assigned[s[0]] != (cell.row_idx, cell.col_idx) for s in samples):
            proofs = _proofs(samples, cell.page_number, "decimal_tail_owned_by_other_cell")
            for proof in proofs:
                proof["assigned_to"] = [list(position) for position in sorted({assigned[s[0]] for s in samples})]
            updates[cell.row_idx, cell.col_idx] = replace(
                cell, raw_text="", normalized_text="", numeric_value=None, normalized_value_wan=None,
                grid_raw_text=cell.raw_text, native_text_sources=proofs, native_blank_verified=False,
            )
    for position, (cell, group) in pending.items():
        if position not in updates:
            updates[position] = replace(cell, native_text_sources=_proofs(
                group, cell.page_number, "unresolved_cross_grid_decimal_tail"))
    return updates


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
        single = [
            bool(s)
            and len(_lines(s)) == 1
            and normalize_text(_text(s)) == normalize_text(label.raw_text)
            and abs((_union(s)[1] + _union(s)[3]) / 2 - center) <= height * 0.4
            for label, s in zip(labels, label_samples, strict=True)
        ]
        # 科目名称本身折行（“机关事业单位基本养老保\n险缴费”）时，同行居中的单行科目编码
        # 已证明这一格只属于一个科目，不是两行各一笔金额。
        if not all(single) and not any(
            ok and re.fullmatch(r"\d{3,7}", normalize_text(label.raw_text))
            for ok, label in zip(single, labels, strict=True)
        ):
            continue
        # 同一格内右对齐的下一行尾数，且原始字符顺序连续。行距上限按 2.5 倍字高：
        # 上海 2022 部分表行高 41pt，折下去的尾数离首行约 1.9 倍字高（体育局基本支出表）。
        if (
            abs(first[2] - second[2]) > height * 0.3
            or not height * 0.7 <= second[1] - first[1] <= height * 2.5
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


def repair_native_structure(page, table, *, reference=None):
    """仅对可由原字符与已知列车道唯一重建的原生表生效，疑义保持原状。"""
    rule = NINE_TABLE_RULES.get(table.table_code)
    continuation = rule is None and reference is not None
    if continuation:
        rule = NINE_TABLE_RULES.get(reference.table_code)
    if (
        table.extraction_method not in {"lines", "lines_strict"}
        or not rule
        or not getattr(page, "chars", None)
    ):
        return table
    mappings = canonical_column_mappings(reference if continuation else table, rule)
    if not mappings:
        return table
    original = table
    table = _split_merged_rows(page, table, mappings)
    bands = _bands(table)
    updates = _wrapped_amounts(page, table, mappings)
    updates.update(_missing_money_cells(page, table, mappings, bands))
    cross_grid = _cross_grid_decimal_tails(page, table, mappings, bands, continuation=continuation)
    if cross_grid and len(table.rows) != len(original.rows):
        # 拆行后逻辑行号已发生变化，不能把它冒充原网格行身份。本轮只支持
        # 两种单独恢复；组合情形保留原格并显式阻断，待独立来源关系可表达后再恢复。
        anchor = next((c for c in original.cells if c.bbox
                       and c.col_idx in {m["source_col_idx"] for m in mappings} and not c.is_header), None)
        if anchor is None:
            return original
        proofs = [{**{k: v for k, v in p.items() if not k.startswith("source_grid_")},
                   "recovery_kind": "unresolved_cross_grid_decimal_tail",
                   "reason": "combined_merged_rows_and_cross_grid_tail_not_supported"}
                  for c in cross_grid.values() for p in c.native_text_sources]
        return replace(original, cells=[replace(c, native_text_sources=proofs) if c is anchor else c
                                        for c in original.cells])
    updates.update(cross_grid)
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
    if cross_grid:
        table = replace(table, cells=[replace(
            c, origin_fragment_uid=c.origin_fragment_uid or original.fragment_uid,
            origin_row_idx=c.origin_row_idx if c.origin_row_idx is not None else c.row_idx,
            origin_col_idx=c.origin_col_idx if c.origin_col_idx is not None else c.col_idx,
        ) for c in table.cells])
    # 跨线尾数只校正字符归属，没有新增/拆分原网格。保持物理片段身份，
    # 否则持久化按 origin_* 回读时会丢失该片段的完整网格元数据。
    return replace(table, fragment_uid=original.fragment_uid if cross_grid else uid)
