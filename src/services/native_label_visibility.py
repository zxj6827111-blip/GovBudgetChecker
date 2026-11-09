"""单元格级名称标签恢复：只删除可证明被裁剪的字符，金额/编码格不改写。

恢复链：pdfplumber 原格字符 → PyMuPDF 全量字符按位置/字符/出现次数一一对应
→ 其中未见于此页可见文字流的字符视为被裁剪 → 仅当该格角色是已验证的名称列、
几何可验证、且字符对应唯一时，用可见字符重建名称并留痕。

批次 1 不处理：渲染模式 3 的扫描 OCR 隐藏层、白字、被矩形遮挡的文字。
这些字符即使被检测为不可见也只记录证据，不删除。
"""
from __future__ import annotations

from dataclasses import replace
from operator import itemgetter
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text

try:  # 可选依赖：没有 PyMuPDF 时整项恢复关闭，并如实记录，不静默降级。
    import pymupdf
except Exception:  # pragma: no cover - 运行环境已固定安装
    pymupdf = None  # type: ignore[assignment]

try:
    from pdfplumber.utils.clustering import cluster_objects
    from pdfplumber.utils.text import DEFAULT_Y_TOLERANCE, WordExtractor, get_line_cluster_key
except Exception:  # pragma: no cover
    cluster_objects = None  # type: ignore[assignment]

POLICY_VERSION = "label-visibility-v2"
DISABLE_ENV = "GOVBUDGET_PDF_DISABLE_LABEL_VISIBILITY_RECOVERY"
CENTER_TOLERANCE = 0.75
BOUNDARY_TOLERANCE = 0.75
NINE_TABLE_CODES = frozenset(NINE_TABLE_RULES)

_FALSE = {"1", "true", "yes", "on"}

#: 恢复/跳过原因：结构化，供读取回执、规则取数不足和验收逐项统计。
REASON_OK = "recovered"
REASON_NO_HIDDEN = "no_hidden_characters"
REASON_PROTECTED_ROLE = "protected_cell_role_conflict"
REASON_CODE_ROLE = "code_cell_visibility_conflict"
REASON_AMOUNT_ROLE = "amount_cell_visibility_conflict"
REASON_UNKNOWN_ROLE = "unknown_cell_role_conflict"
REASON_GEOMETRY = "unsupported_geometry"
REASON_NO_FITZ = "visibility_source_unavailable"
REASON_CHAR_MISMATCH = "unresolved_char_correspondence"
REASON_BOUNDARY = "ambiguous_cell_boundary"
REASON_PROVENANCE = "unresolved_cell_text_provenance"
REASON_CROSS_CELL = "cross_cell_source_unproven"
REASON_HIDDEN_RENDER = "invisible_render_mode_not_removed"
REASON_HIDDEN_WHITE = "white_text_not_removed"
REASON_EMPTY = "recovered_label_empty"
REASON_SOURCE = "unsupported_source_handle"
REASON_DISABLED = "recovery_disabled"


def recovery_enabled() -> bool:
    return os.getenv(DISABLE_ENV, "").strip().lower() not in _FALSE


def _center(bbox: Sequence[float]) -> Tuple[float, float]:
    return ((float(bbox[0]) + float(bbox[2])) / 2, (float(bbox[1]) + float(bbox[3])) / 2)


def _center_inside(bbox: Sequence[float], cell: Sequence[float]) -> bool:
    x, y = _center(bbox)
    return cell[0] <= x < cell[2] and cell[1] <= y < cell[3]


def _near(a: Sequence[float], b: Sequence[float], tolerance: float = CENTER_TOLERANCE) -> bool:
    """同一字符的位置对应：横向中心接近且纵向显著重叠。

    pdfplumber 与 PyMuPDF 对同一字形的 bbox 高度可能不同（字体升降部
    计入方式不同），因此不能用纵向中心严格相等；纵向重叠至少半个字高
    才能证明仍在同一行。
    """
    ax = (float(a[0]) + float(a[2])) / 2
    bx = (float(b[0]) + float(b[2])) / 2
    if abs(ax - bx) > tolerance:
        return False
    a0, a1 = float(a[1]), float(a[3])
    b0, b1 = float(b[1]), float(b[3])
    height = min(max(a1 - a0, 1e-6), max(b1 - b0, 1e-6))
    overlap = min(a1, b1) - max(a0, b0)
    return overlap >= height * 0.5


def _norm_char(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


class PageVisibility:
    """一页的原生字符可见性证据（PyMuPDF 全量 vs 可见）。"""

    def __init__(self, page) -> None:
        self.supported = (
            int(getattr(page, "rotation", 0) or 0) == 0
            and tuple(round(float(v), 3) for v in page.cropbox)
            == tuple(round(float(v), 3) for v in page.mediabox)
        )
        self.full: List[Dict[str, Any]] = []
        self.visible: List[Dict[str, Any]] = []
        self.render_mode_indices: set = set()
        self.white_text_indices: set = set()
        if not self.supported:
            return
        self.full = self._collect(page, 0)
        self.visible = self._collect(page, pymupdf.TEXT_CLIP)
        self.render_mode_indices, self.white_text_indices = self._unsafe_characters(page, self.full)

    @classmethod
    def unsupported(cls) -> "PageVisibility":
        instance = cls.__new__(cls)
        instance.supported = False
        instance.full = []
        instance.visible = []
        instance.render_mode_indices = set()
        instance.white_text_indices = set()
        return instance

    @staticmethod
    def _collect(page, flags: int) -> List[Dict[str, Any]]:
        chars: List[Dict[str, Any]] = []
        index = 0
        data = page.get_text("rawdict", flags=flags)
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    for char in span.get("chars", []):
                        chars.append({
                            "text": str(char.get("c") or ""),
                            "norm": _norm_char(char.get("c") or ""),
                            "bbox": tuple(float(v) for v in char["bbox"]),
                            "line": index,
                        })
                index += 1
        return chars

    @staticmethod
    def _unsafe_characters(page, chars: List[Dict[str, Any]]) -> Tuple[set, set]:
        """渲染模式 3（不可见文字层）或白色填充：批次 1 不删除。"""
        render_mode: set = set()
        white_text: set = set()
        try:
            spans = page.get_texttrace()
        except Exception:
            return render_mode, white_text
        for span in spans:
            render_type = span.get("type")
            color = span.get("color")
            white = (
                isinstance(color, (tuple, list))
                and len(color) >= 3
                and all(float(value) >= 0.999 for value in color[:3])
            )
            if render_type != 3 and not white:
                continue
            target = render_mode if render_type == 3 else white_text
            for char in span.get("chars", ()):
                if len(char) < 4:
                    continue
                bbox = tuple(float(v) for v in char[3])
                for position, item in enumerate(chars):
                    if position in target:
                        continue
                    if item["norm"] == _norm_char(chr(char[0])) and _near(item["bbox"], bbox):
                        target.add(position)
                        break
        return render_mode, white_text


class CellCharacterMap:
    """一个名称格的 pdfplumber→PyMuPDF 字符对应结果。"""

    def __init__(self) -> None:
        self.pp_chars: List[Dict[str, Any]] = []
        self.full_index: List[Optional[int]] = []
        self.hidden: set = set()
        self.unsafe_render: set = set()
        self.unsafe_white: set = set()
        self.status = REASON_OK
        self.detail: Optional[str] = None
        self.visible_text = ""
        self.original_text = ""
        self.boundary_characters: List[Dict[str, Any]] = []

    def as_record(self, fragment, cell) -> Dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "status": self.status,
            "detail": self.detail,
            "fragment_uid": fragment.fragment_uid,
            "page": cell.page_number,
            "row": cell.row_idx,
            "column": cell.col_idx,
            "source_cell_id": (
                f"{cell.origin_fragment_uid or fragment.fragment_uid}:"
                f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
                f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}"
            ),
            "original_text": self.original_text,
            "recovered_text": self.visible_text if self.status == REASON_OK else None,
            "excluded_characters": [
                {
                    "text": char["text"],
                    "bbox": list(_pp_bbox(char)),
                    "pdfplumber_page_index": char.get("page_index"),
                }
                for index, char in enumerate(self.pp_chars)
                if index in self.hidden
            ],
            "unsafe_hidden_characters": [
                {
                    "text": char["text"],
                    "bbox": list(_pp_bbox(char)),
                    "pdfplumber_page_index": char.get("page_index"),
                    "reason": (
                        REASON_HIDDEN_RENDER if index in self.unsafe_render else REASON_HIDDEN_WHITE
                    ),
                }
                for index, char in enumerate(self.pp_chars)
                if index in self.unsafe_render or index in self.unsafe_white
            ],
            "boundary_characters": self.boundary_characters,
        }


def _pp_bbox(char: Dict[str, Any]) -> Tuple[float, float, float, float]:
    if "bbox" in char and char["bbox"]:
        return tuple(float(v) for v in char["bbox"])
    return (float(char["x0"]), float(char["top"]), float(char["x1"]), float(char["bottom"]))


def _rebuild_cell_text(page, cell_bbox) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """用 pdfplumber 自身的 WordExtractor 重建格内文本及其字符顺序。"""
    if cluster_objects is None:
        return None
    page_chars = list(getattr(page, "chars", []))
    index_by_id = {id(char): index for index, char in enumerate(page_chars)}
    chars = [
        char for char in page_chars
        if _center_inside((char["x0"], char["top"], char["x1"], char["bottom"]), cell_bbox)
    ]
    if not chars:
        return None
    extractor = WordExtractor()
    words = extractor.extract_words(chars, return_chars=True)
    line_key = get_line_cluster_key(extractor.line_dir)
    lines = cluster_objects(words, line_key, DEFAULT_Y_TOLERANCE)
    text = "\n".join(" ".join(word["text"] for word in line) for line in lines)
    ordered = [
        {**char, "page_index": index_by_id.get(id(char))}
        for line in lines for word in line for char in word.get("chars", [])
    ]
    return text, ordered


def _source_char_expansion(cell, page_chars: Sequence[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """把格内已记录的 native_text_sources 展开成按原文顺序的 pdfplumber 字符。"""
    sources = [source for source in (cell.native_text_sources or []) if isinstance(source, dict)]
    if not sources:
        return None
    literal = "".join(str(source.get("text") or "") for source in sources)
    if normalize_text(literal) != normalize_text(cell.raw_text):
        return None
    expanded: List[Dict[str, Any]] = []
    for source in sources:
        try:
            start = int(source.get("start_char_index"))
            end = int(source.get("end_char_index"))
        except (TypeError, ValueError):
            return None
        if start < 0 or end <= start or end > len(page_chars):
            return None
        part = page_chars[start:end]
        text = "".join(str(char.get("text") or "") for char in part)
        if normalize_text(text) != normalize_text(str(source.get("text") or "")):
            return None
        expanded.extend({**char, "page_index": start + offset} for offset, char in enumerate(part))
    return expanded


def _hidden_in_box(page_visibility: PageVisibility, cell_bbox: Sequence[float]) -> Optional[List[Dict[str, Any]]]:
    """格内可见性差异字符（只用于受保护格/失败格的证据，不据此改写）。"""
    full = [char for char in page_visibility.full
            if _center_inside(char["bbox"], cell_bbox) and not char["norm"].isspace()]
    visible = [char for char in page_visibility.visible
               if _center_inside(char["bbox"], cell_bbox) and not char["norm"].isspace()]
    used: set = set()
    hidden: List[Dict[str, Any]] = []
    for char in full:
        candidates = [
            index for index, visible_char in enumerate(visible)
            if index not in used and visible_char["norm"] == char["norm"]
            and _near(visible_char["bbox"], char["bbox"])
        ]
        if len(candidates) == 1:
            used.add(candidates[0])
        elif len(candidates) == 0:
            hidden.append(char)
        else:
            return None
    return hidden


def _match_cell_chars(
    page_visibility: PageVisibility,
    cell_bbox: Sequence[float],
    pp_chars: Sequence[Dict[str, Any]],
    original_text: str,
) -> CellCharacterMap:
    result = CellCharacterMap()
    result.original_text = original_text
    result.pp_chars = list(pp_chars)

    full = [(index, char) for index, char in enumerate(page_visibility.full)
            if _center_inside(char["bbox"], cell_bbox)]
    visible = [(index, char) for index, char in enumerate(page_visibility.visible)
               if _center_inside(char["bbox"], cell_bbox)]

    # 边界格：pdfplumber 按其中心点归属字符；CJK 字形下缘略越过行线是正常的。
    # 只对“中心在内但框体大幅越界”判为归属不唯一；有界越界逐字符留痕。
    for char in page_visibility.full:
        if not _center_inside(char["bbox"], cell_bbox):
            continue
        height = max(1.0, float(char["bbox"][3]) - float(char["bbox"][1]))
        overflow = max(
            cell_bbox[0] - char["bbox"][0], cell_bbox[1] - char["bbox"][1],
            char["bbox"][2] - cell_bbox[2], char["bbox"][3] - cell_bbox[3], 0.0,
        )
        if overflow > max(6.0, height * 0.6):
            result.status, result.detail = REASON_BOUNDARY, "字符框大幅跨越格边界，归属不唯一"
            return result
        if overflow > BOUNDARY_TOLERANCE and not char["norm"].isspace():
            result.boundary_characters.append({
                "text": char["text"],
                "bbox": list(char["bbox"]),
                "overflow_points": round(float(overflow), 3),
            })

    used_full: set = set()
    mapping: Dict[int, int] = {}
    pp_nonspace = [(index, char) for index, char in enumerate(pp_chars)
                   if not str(char.get("text") or "").isspace()]
    full_nonspace = [(index, char) for index, char in full
                     if not char["norm"].isspace()]
    if len(pp_nonspace) != len(full_nonspace):
        result.status = REASON_CHAR_MISMATCH
        result.detail = f"格内字符数不一致：pdfplumber={len(pp_nonspace)}，PyMuPDF={len(full_nonspace)}"
        return result
    for index, char in pp_nonspace:
        candidates = [
            full_index for full_index, full_char in full_nonspace
            if full_index not in used_full
            and full_char["norm"] == _norm_char(char.get("text") or "")
            and _near(full_char["bbox"], (char["x0"], char["top"], char["x1"], char["bottom"]))
        ]
        if len(candidates) != 1:
            result.status = REASON_CHAR_MISMATCH
            result.detail = (
                f"字符 {char.get('text')!r} 在格内位置对应不唯一（候选 {len(candidates)} 个）"
            )
            return result
        used_full.add(candidates[0])
        mapping[index] = candidates[0]
    result.full_index = [mapping.get(index) for index in range(len(pp_chars))]

    visible_used: set = set()
    hidden: set = set()
    for full_index, full_char in full:
        if full_char["norm"].isspace():
            continue
        candidates = [
            index for index, visible_char in visible
            if index not in visible_used
            and visible_char["norm"] == full_char["norm"]
            and _near(visible_char["bbox"], full_char["bbox"])
        ]
        if len(candidates) == 1:
            visible_used.add(candidates[0])
        elif len(candidates) == 0:
            hidden.add(full_index)
        else:
            result.status = REASON_CHAR_MISMATCH
            result.detail = f"字符 {full_char['text']!r} 的可见/隐藏归属不唯一"
            return result
    visible_nonspace = [char for _, char in visible if not char["norm"].isspace()]
    if len(visible_used) != len(visible_nonspace):
        result.status = REASON_CHAR_MISMATCH
        result.detail = "可见字符未被全量字符完整解释"
        return result

    result.hidden = {
        index for index, full_index in enumerate(result.full_index)
        if full_index is not None and full_index in hidden
    }
    result.unsafe_render = {
        index for index, full_index in enumerate(result.full_index)
        if full_index is not None and full_index in hidden
        and full_index in page_visibility.render_mode_indices
    }
    result.unsafe_white = {
        index for index, full_index in enumerate(result.full_index)
        if full_index is not None and full_index in hidden
        and full_index in page_visibility.white_text_indices
    }
    if not result.hidden:
        result.status = REASON_NO_HIDDEN
        return result

    # 用可见字符重建文本：保留 PyMuPDF 行结构，按 x 排序；不按格宽补空格。
    line_chars: Dict[int, List[Dict[str, Any]]] = {}
    for visible_index, visible_char in visible:
        line_chars.setdefault(visible_char["line"], []).append(visible_char)
    lines: List[str] = []
    for line in sorted(line_chars):
        text = "".join(
            char["text"] for char in sorted(line_chars[line], key=lambda item: item["bbox"][0])
        ).strip()
        if text:
            lines.append(text)
    result.visible_text = "\n".join(lines)
    if not normalize_text(result.visible_text):
        result.status = REASON_EMPTY
        result.detail = "恢复后名称为空，不能自动改写"
    return result


def recover_label_cells(
    page,
    table,
    *,
    code_columns: Sequence[int] = (),
    name_columns: Sequence[int] = (),
    page_visibility: Optional[PageVisibility] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """对一张原生网格表执行名称格可见性恢复，返回新表与统计。"""
    stats: Dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "table_code": table.table_code,
        "fragment_uid": table.fragment_uid,
        "page": table.page_number,
        "recovered": 0,
        "conflicts": 0,
        "skipped": 0,
        "reasons": {},
    }
    if table.table_code not in NINE_TABLE_CODES or table.extraction_method not in {"lines", "lines_strict"}:
        return table, stats
    if not recovery_enabled():
        stats["reasons"][REASON_DISABLED] = 1
        return table, stats
    if page_visibility is None:
        stats["reasons"][REASON_NO_FITZ] = 1
        return table, stats
    if not page_visibility.supported:
        stats["reasons"][REASON_GEOMETRY] = 1
        return table, stats

    code_set = set(int(value) for value in code_columns)
    name_set = set(int(value) for value in name_columns)
    if not name_set:
        return table, stats
    amount_set = _amount_columns(table)

    updates: Dict[Tuple[int, int], Any] = {}
    records: List[Dict[str, Any]] = []
    physical = getattr(page, "chars", []) or []
    for cell in table.cells:
        if cell.bbox is None or not str(cell.raw_text or "").strip():
            continue
        if cell.is_header:
            role = "unknown"
        else:
            role = (
                "code" if cell.col_idx in code_set
                else "name" if cell.col_idx in name_set
                else "amount" if cell.col_idx in amount_set
                else "unknown"
            )
        # 表头尾字可能被裁剪在首条合计行里；原格横跨全部标签列时，它是合计
        # 名称格，不是财政编码格。仍须下游逐字证明只有“合计”实际可见。
        merged_total = (
            cell.row_idx == table.header_row_count and cell.col_span > 1
            and re.fullmatch(r'(?:功能分类)?(?:科目)?编码合计', normalize_text(cell.raw_text)) is not None
            and any(col in name_set for col in range(cell.col_idx, cell.col_idx + cell.col_span))
            and not any(col in amount_set for col in range(cell.col_idx, cell.col_idx + cell.col_span))
            and any(other.row_idx == cell.row_idx and other.col_idx in amount_set
                    and other.numeric_value is not None and not other.is_header for other in table.cells)
        )
        if merged_total:
            role = 'name'
        try:
            expanded = _source_char_expansion(cell, physical)
        except Exception:
            expanded = None
        if expanded is not None:
            if any(not _center_inside((char["x0"], char["top"], char["x1"], char["bottom"]), cell.bbox)
                   for char in expanded):
                stats["skipped"] += 1
                stats["reasons"][REASON_CROSS_CELL] = stats["reasons"].get(REASON_CROSS_CELL, 0) + 1
                continue
            pp_chars = expanded
        else:
            rebuilt = _rebuild_cell_text(page, cell.bbox)
            if rebuilt is None or rebuilt[0].strip() != str(cell.raw_text or "").strip():
                stats["skipped"] += 1
                stats["reasons"][REASON_PROVENANCE] = stats["reasons"].get(REASON_PROVENANCE, 0) + 1
                continue
            pp_chars = rebuilt[1]

        character_map = _match_cell_chars(page_visibility, cell.bbox, pp_chars, cell.raw_text)
        if character_map.status == REASON_NO_HIDDEN:
            continue
        if character_map.status != REASON_OK:
            # 字符对应不唯一时也不能静默放过受保护格：只要格内确有隐藏字符差异，
            # 金额/编码格就留下冲突证明且原文不动；名称格留待复核原因。
            hidden_in_box = _hidden_in_box(page_visibility, cell.bbox)
            if hidden_in_box:
                reason = (
                    REASON_AMOUNT_ROLE if role == "amount"
                    else REASON_CODE_ROLE if role == "code"
                    else REASON_UNKNOWN_ROLE if role != "name"
                    else character_map.status
                )
                record = {
                    "policy_version": POLICY_VERSION,
                    "status": reason,
                    "detail": character_map.detail,
                    "fragment_uid": table.fragment_uid,
                    "page": cell.page_number,
                    "row": cell.row_idx,
                    "column": cell.col_idx,
                    "source_cell_id": (
                        f"{cell.origin_fragment_uid or table.fragment_uid}:"
                        f"r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}"
                        f"c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}"
                    ),
                    "original_text": cell.raw_text,
                    "recovered_text": None,
                    "excluded_characters": [
                        {"text": char["text"], "bbox": list(char["bbox"])}
                        for char in hidden_in_box
                    ],
                }
                records.append(record)
                updates[(cell.row_idx, cell.col_idx)] = replace(cell, label_recovery=record)
                if role in {"amount", "code", "unknown"}:
                    stats["conflicts"] += 1
                else:
                    stats["skipped"] += 1
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
                continue
            stats["skipped"] += 1
            stats["reasons"][character_map.status] = stats["reasons"].get(character_map.status, 0) + 1
            continue
        if character_map.unsafe_render or character_map.unsafe_white:
            reason = REASON_HIDDEN_RENDER if character_map.unsafe_render else REASON_HIDDEN_WHITE
            # 渲染模式 3 与白字都不删除；只留证据。
            record = character_map.as_record(table, cell)
            record["status"] = reason
            record["recovered_text"] = None
            records.append(record)
            updates[(cell.row_idx, cell.col_idx)] = replace(cell, label_recovery=record)
            stats["skipped"] += 1
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            continue
        if role != "name":
            reason = {
                "code": REASON_CODE_ROLE,
                "amount": REASON_AMOUNT_ROLE,
                "unknown": REASON_UNKNOWN_ROLE,
            }.get(role, REASON_PROTECTED_ROLE)
            record = character_map.as_record(table, cell)
            record["status"] = reason
            record["recovered_text"] = None
            records.append(record)
            updates[(cell.row_idx, cell.col_idx)] = replace(cell, label_recovery=record)
            stats["conflicts"] += 1
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            continue
        recovered = character_map.visible_text.strip()
        if merged_total and normalize_text(recovered) != '合计':
            stats['skipped'] += 1
            continue
        if normalize_text(recovered) == normalize_text(cell.raw_text):
            continue
        record = character_map.as_record(table, cell)
        if merged_total:
            record['role_basis'] = 'native_merged_total_label'
        records.append(record)
        updates[(cell.row_idx, cell.col_idx)] = replace(
            cell,
            raw_text=recovered,
            normalized_text=normalize_text(recovered),
            numeric_value=None,
            normalized_value_wan=None,
            grid_raw_text=cell.grid_raw_text if cell.grid_raw_text is not None else cell.raw_text,
            label_recovery=record,
            is_header=False if merged_total else cell.is_header,
        )
        stats["recovered"] += 1

    if not updates:
        if records:
            stats["records"] = records
        if not stats["recovered"] and not stats["conflicts"] and not stats["skipped"]:
            stats["reasons"][REASON_NO_HIDDEN] = 1
        return table, stats
    rows = [list(row) for row in table.rows]
    for (row_idx, col_idx), cell in updates.items():
        if row_idx < len(rows) and col_idx < len(rows[row_idx]):
            rows[row_idx][col_idx] = cell.raw_text
    updated = replace(
        table,
        rows=rows,
        cells=[updates.get((cell.row_idx, cell.col_idx), cell) for cell in table.cells],
    )
    stats["records"] = records
    return updated, stats


def _amount_columns(table) -> set:
    rule = NINE_TABLE_RULES.get(table.table_code)
    if rule is None:
        return set()
    from src.services.fiscal_column_identity import canonical_column_mappings

    try:
        mappings = canonical_column_mappings(table, rule)
    except Exception:
        return set()
    return {int(mapping["source_col_idx"]) for mapping in mappings}


class VisibilityResolver:
    """按页缓存 PyMuPDF 可见性；仅在确有原生网格时打开文档。"""

    def __init__(self, pdf_path: Optional[str]) -> None:
        self.pdf_path = str(pdf_path) if pdf_path else None
        self.document = None
        self._pages: Dict[int, PageVisibility] = {}
        self.error: Optional[str] = None
        if not self.pdf_path or pymupdf is None:
            self.error = REASON_SOURCE if not self.pdf_path else REASON_NO_FITZ
            return
        try:
            self.document = pymupdf.open(self.pdf_path)
        except Exception as exc:  # noqa: BLE001 - 打开失败必须留下可读原因
            self.error = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self.document is not None and recovery_enabled()

    def page(self, page_number: int) -> Optional[PageVisibility]:
        if not self.available:
            return None
        if page_number not in self._pages:
            try:
                self._pages[page_number] = PageVisibility(self.document[page_number - 1])
            except Exception:
                self._pages[page_number] = PageVisibility.unsupported()
        return self._pages[page_number]

    def close(self) -> None:
        if self.document is not None:
            try:
                self.document.close()
            except Exception:  # noqa: BLE001
                pass
            self.document = None


def table_name_and_code_columns(table) -> Tuple[List[int], List[int]]:
    """按已验证的列映射给出名称列与编码列；不靠 numeric_value 是否为空猜角色。"""
    rule = NINE_TABLE_RULES.get(table.table_code)
    if rule is None:
        return [], []
    from src.services.fiscal_column_identity import canonical_column_mappings, code_dimension_columns

    mappings = canonical_column_mappings(table, rule)
    name_columns: List[int] = []
    for mapping in mappings:
        labels = [int(value) for value in mapping.get("source_label_cols", [])]
        if labels:
            name_columns.append(labels[-1])
    code_columns = sorted(code_dimension_columns(table))
    return sorted(set(name_columns)), code_columns
