"""PDF 单页文本/表格抽取（无副作用模块）。

为什么单独拆一个模块：解析要迁到独立子进程执行（缺口 P2-05 / B-09），
子进程在 spawn 模式下会重新导入目标函数所在模块。如果这些函数留在
`api/main.py`，子进程就会连带执行 `load_dotenv()`、创建 FastAPI app、
`UPLOAD_ROOT.mkdir()` 等一堆启动副作用——既慢又危险。

本模块只依赖 pdfplumber 的页面对象，导入时不做任何 I/O。
`api/main.py` 以别名方式导入这两个函数，保持既有名字与可替换性。
"""

from __future__ import annotations

from typing import Any, Dict, List


def extract_tables_from_page(page: Any) -> List[List[List[str]]]:
    """
    读取单页表格，返回：该页的多张表；每张表是 2D 数组（行→列）
    （和引擎里的逻辑一致，先用线策略，再退回默认）
    """
    tables: List[List[List[str]]] = []
    try:
        t1 = (
            page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "intersection_tolerance": 3,
                    "min_words_vertical": 1,
                    "min_words_horizontal": 1,
                }
            )
            or []
        )
        tables += t1
    except Exception:
        pass
    try:
        if not tables:
            t2 = page.extract_tables() or []
            tables += t2
    except Exception:
        pass

    norm_tables: List[List[List[str]]] = []
    for tb in tables:
        norm_tables.append(
            [[("" if c is None else str(c)).strip() for c in row] for row in (tb or [])]
        )
    return norm_tables


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
