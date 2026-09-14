"""PDF 单页文本/表格抽取（无副作用模块）。

为什么单独拆一个模块：解析要迁到独立子进程执行（缺口 P2-05 / B-09），
子进程在 spawn 模式下会重新导入目标函数所在模块。如果这些函数留在
`api/main.py`，子进程就会连带执行 `load_dotenv()`、创建 FastAPI app、
`UPLOAD_ROOT.mkdir()` 等一堆启动副作用——既慢又危险。

本模块只依赖 pdfplumber 的页面对象，导入时不做任何 I/O。
`api/main.py` 以别名方式导入这两个函数，保持既有名字与可替换性。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


_TABLE_NAME_RE = re.compile(r"^.{0,28}?(决算表|决算总表|预算表|支出表|收入表)$")


class ExtractedTable(list):
    """保持二维 ``list`` 兼容，同时携带结构化解析所需的几何元数据。

    legacy 规则、页面质量评估和 JSON 序列化都继续把它当作普通表格行
    列表使用；structured 路径可以读取 ``bbox`` 和按表裁切得到的表名锚。
    用 list 子类而不是替换为 dict，是为了不破坏已有消费者的输入契约。
    """

    bbox: Optional[Tuple[float, float, float, float]]
    anchor_table_name: str

    def __init__(
        self,
        rows: Sequence[Sequence[str]],
        *,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        anchor_table_name: str = "",
    ) -> None:
        super().__init__(rows)
        self.bbox = bbox
        self.anchor_table_name = anchor_table_name


def _normalize_bbox(raw_bbox: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    try:
        values = tuple(float(item) for item in raw_bbox)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        return None
    return values


def _find_tables(page: Any, table_settings: Optional[Dict[str, Any]]) -> List[Any]:
    """获取与 ``extract_tables`` 同序的 pdfplumber Table 对象（含 bbox）。"""
    finder = getattr(page, "find_tables", None)
    if not callable(finder):
        return []
    try:
        found = finder(table_settings=table_settings) if table_settings is not None else finder()
    except Exception:
        return []
    return list(found or [])


def _table_bbox(table: Any) -> Optional[Tuple[float, float, float, float]]:
    if isinstance(table, dict):
        return _normalize_bbox(table.get("bbox"))
    return _normalize_bbox(getattr(table, "bbox", None))


def _anchor_candidate(raw_text: Any) -> str:
    """归一化并筛选一个可能的业务表名行。"""
    text = re.sub(r"\s+", "", str(raw_text or "")).strip()
    if (
        not text
        or len(text) > 30
        or "部门" in text
        or "单位" in text
        or text.startswith(("一、", "二、", "三、"))
    ):
        return ""
    return text if _TABLE_NAME_RE.search(text) else ""


def _extract_anchor_from_words(
    page: Any, bbox: Tuple[float, float, float, float]
) -> str:
    """用 ``extract_words`` 按几何关系查找表 bbox 上方最近的表名。

    ``crop(...).extract_text()`` 的窗口受限于固定高度，标题稍远时会把
    当前表误判成无锚；整页首行又会把多表页的标题错绑给其它表。词级
    bbox 同时提供垂直距离和水平重叠，只有真正位于当前表上方的最近
    标题才可绑定；找不到时由调用方回退到 crop 兼容路径。
    """
    extractor = getattr(page, "extract_words", None)
    if not callable(extractor):
        return ""
    try:
        words = list(extractor() or [])
    except Exception:
        return ""
    grouped: Dict[float, List[Tuple[str, Tuple[float, float, float, float]]]] = {}
    for word in words:
        if not isinstance(word, dict):
            continue
        word_bbox = _normalize_bbox(
            [word.get("x0"), word.get("top"), word.get("x1"), word.get("bottom")]
        )
        if word_bbox is None:
            continue
        top_key = round(word_bbox[1], 2)
        grouped.setdefault(top_key, []).append((str(word.get("text") or ""), word_bbox))

    candidates: List[Tuple[float, float, str]] = []
    table_left, table_top, table_right, _ = bbox
    for line_top, line_words in grouped.items():
        line_bbox = (
            min(item[1][0] for item in line_words),
            min(item[1][1] for item in line_words),
            max(item[1][2] for item in line_words),
            max(item[1][3] for item in line_words),
        )
        line_text = _anchor_candidate("".join(item[0] for item in line_words))
        if not line_text:
            continue
        distance = table_top - line_bbox[3]
        if distance < 0 or distance > 240.0:
            continue
        overlap = min(table_right, line_bbox[2]) - max(table_left, line_bbox[0])
        line_center = (line_bbox[0] + line_bbox[2]) / 2.0
        if overlap <= 0 and not table_left - 30.0 <= line_center <= table_right + 30.0:
            continue
        candidates.append((distance, -max(overlap, 0.0), line_text))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][2]


def _extract_anchor_table_name(page: Any, bbox: Optional[Tuple[float, float, float, float]]) -> str:
    """在单张表 bbox 上方裁切标题，避免整页首个标题误绑定到其他表。"""
    if bbox is None:
        return ""
    word_anchor = _extract_anchor_from_words(page, bbox)
    if word_anchor:
        return word_anchor
    try:
        page_width = float(getattr(page, "width", bbox[2]) or bbox[2])
        page_height = float(getattr(page, "height", bbox[3]) or bbox[3])
        left = max(0.0, bbox[0] - 5.0)
        top = max(0.0, bbox[1] - 120.0)
        right = min(page_width, bbox[2] + 5.0)
        bottom = min(page_height, bbox[1])
        region = page.crop((left, top, right, bottom))
        text = region.extract_text() or ""
    except Exception:
        return ""
    for line in str(text).splitlines()[:8]:
        candidate = _anchor_candidate(line)
        if candidate:
            return candidate
    return ""


def extract_tables_from_page(page: Any) -> List[List[List[str]]]:
    """
    读取单页表格，返回：该页的多张表；每张表是 2D 数组（行→列）
    （和引擎里的逻辑一致，先用线策略，再退回默认）
    """
    tables: List[List[List[str]]] = []
    table_settings: Optional[Dict[str, Any]] = None
    try:
        table_settings = {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 3,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        }
        t1 = page.extract_tables(table_settings=table_settings) or []
        tables += t1
    except Exception:
        pass
    try:
        if not tables:
            table_settings = None
            t2 = page.extract_tables() or []
            tables += t2
    except Exception:
        pass

    table_objects = _find_tables(page, table_settings)
    if len(table_objects) != len(tables):
        # 找表结果与二维抽取结果数量不一致时，顺序无法证明相同；不做
        # 部分绑定，避免把另一张表的 bbox/标题错配给当前表。
        table_objects = []
    norm_tables: List[List[List[str]]] = []
    for tb in tables:
        norm_tables.append(
            [[("" if c is None else str(c)).strip() for c in row] for row in (tb or [])]
        )
    # ``find_tables`` 使用同一策略时通常与 ``extract_tables`` 一一对应。
    # 若具体 pdfplumber 版本的 fallback 顺序不同，无法可靠对齐就不猜，
    # structured 侧会因缺 bbox 走 fail-closed；二维表数据仍照常返回。
    extracted: List[List[List[str]]] = []
    for index, rows in enumerate(norm_tables):
        bbox = _table_bbox(table_objects[index]) if index < len(table_objects) else None
        extracted.append(
            ExtractedTable(
                rows,
                bbox=bbox,
                anchor_table_name=_extract_anchor_table_name(page, bbox),
            )
        )
    return extracted


def is_visible_char(obj: Dict[str, Any], page_height: float) -> bool:
    if obj.get("object_type") != "char":
        return True
    top = obj.get("top")
    bottom = obj.get("bottom")
    if top is None or bottom is None:
        return True
    try:
        top_v = float(top)
        bottom_v = float(bottom)
    except Exception:
        return True
    return top_v >= 0 and bottom_v <= page_height


def extract_visible_text_from_page(page: Any) -> str:
    raw_text = page.extract_text() or ""
    try:
        page_height = float(page.height)
        filtered_page = page.filter(lambda obj, h=page_height: is_visible_char(obj, h))
        filtered_text = filtered_page.extract_text() or ""
        if filtered_text.strip():
            return filtered_text
    except Exception:
        pass
    return raw_text
