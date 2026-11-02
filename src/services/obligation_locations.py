"""为人工补核生成可点击的原文位置。

为什么在服务层做
----------------
补核义务来自引擎，本身只带技术编号和中文 ``detail``，不带页码。前端拿不到
PDF 页文本，若在前端用正则猜页码，必然随材料版式漂移；而且"上一版材料的
页码"比"没有页码"更危险。服务层手里有当前分析代际的两份结构化数据：

1. ``structured_ingest.reading_import.tables``：表名 → 起始页 / bbox；
2. ``analysis_results.rule_findings``：checker 编号 → 证据页 / bbox。

两者都属于当前文件版本的那一次分析，因此可以合法地当作"这份材料的页码"。
本模块只输出 ``ObligationLocation``，不输出"确定错误"；取不到位置就返回空
列表，页面显示"未识别到位置"，**不猜页码、不用上一版页码**。

匹配纪律
--------
- 表名先做全角/空白/标点归一化；长表名命中时丢弃它的短子串命中
  （例如 ``财政拨款收入支出决算总表`` 命中后，不再把 ``收入支出决算总表``
  当成另一张表）；
- ``detail`` 里明写的"第 N 页/第 N、M 页"只作为补充位置，且页码必须落在
  1..999；
- 同一页同一 bbox 只保留一次，最多 6 个位置，避免补核卡被位置芯片淹没。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.schemas.review_lifecycle import ObligationLocation

#: 逻辑表代号 → 可接受的表名别名。别名只用于"在义务标题/原因里认出这张表"，
#: 不参与任何金额或科目判定。
_TABLE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "FIN_01_income_expenditure_total": ("收入支出决算总表",),
    "FIN_02_income": ("收入决算表",),
    "FIN_03_expenditure": ("支出决算表",),
    "FIN_04_fiscal_grant_total": ("财政拨款收入支出决算总表", "财政拨款收支总表"),
    "FIN_05_general_public_expenditure": (
        "一般公共预算财政拨款支出决算表",
        "一般公共预算支出决算表",
    ),
    "FIN_06_basic_expenditure": (
        "一般公共预算财政拨款基本支出决算表",
        "基本支出决算表",
        # 三公与经济分类表勾稽义务里用的是这个简称。
        "财政拨款经济分类表",
    ),
    "FIN_07_three_public": (
        "财政拨款“三公”经费支出决算表",
        "三公经费支出决算表",
        "三公经费表",
    ),
    "FIN_08_gov_fund": ("政府性基金预算财政拨款收入支出决算表",),
    "FIN_09_state_capital": ("国有资本经营预算财政拨款收入支出决算表",),
}

#: 义务编号 → 除标题匹配外还必须查看的表。
#: 空表/无此项：除了财政拨款总表，还要去两张空表看"本表无数据"说明。
_EXTRA_TABLES: Dict[str, Tuple[str, ...]] = {
    "OBL-STRUCT-EMPTY-STATEMENT": (
        "FIN_04_fiscal_grant_total",
        "FIN_08_gov_fund",
        "FIN_09_state_capital",
    ),
}

_PUNCT_RE = re.compile(r"[\s“”‘’\"'（）()【】\[\]·,，。；;：:、—–\-]+")
#: 匹配"18、12页" / "第12页" / "8,9页" 里的页码列表；不匹配"第21行第6列"。
_PAGE_LIST_RE = re.compile(r"((?:\d{1,3}\s*[、,，]?\s*)+)页")
#: detail 里的中文引号短语（科目名/表名/说明名），用于在分页文本里反查页码。
_QUOTED_RE = re.compile(r"[“「]([^”」]{2,40})[”」]")
#: 支出总表里的行标签，如"十九、住房保障支出"。
_ROW_LABEL_RE = re.compile(r"([一二三四五六七八九十]+、[^\s，。；;、]{2,30}支出)")

_MAX_LOCATIONS = 6


def _normalize(text: Any) -> str:
    """全角/半角、空白、标点归一化；只用于表名匹配，不改原文。"""
    return _PUNCT_RE.sub("", str(text or ""))


def _json_value(raw: Any) -> Any:
    """JSONB 列可能是字符串（本仓库未注册 asyncpg JSON codec）。"""
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按原值处理，由调用方兜底
            return raw
    return raw


def _json_dict(raw: Any) -> Dict[str, Any]:
    value = _json_value(raw)
    return value if isinstance(value, dict) else {}


def _json_list(raw: Any) -> List[Any]:
    value = _json_value(raw)
    return value if isinstance(value, list) else []


def _reading_tables(structured_ingest: Any) -> Dict[str, Dict[str, Any]]:
    """当前分析代际的"表名 → 起始页/bbox"索引。"""
    payload = _json_dict(structured_ingest)
    reading = _json_dict(payload.get("reading_import"))
    index: Dict[str, Dict[str, Any]] = {}
    for row in _json_list(reading.get("tables")):
        if not isinstance(row, dict):
            continue
        code = str(row.get("table_code") or "").strip()
        page = row.get("page")
        if not code or not isinstance(page, int) or isinstance(page, bool) or page < 1:
            continue
        bbox = row.get("bbox")
        index[code] = {
            "page": page,
            "label": str(row.get("title") or "").strip() or code,
            "bbox": bbox if isinstance(bbox, list) else None,
        }
    return index


def _matched_table_codes(text: str, index: Dict[str, Dict[str, Any]]) -> List[str]:
    """按别名在义务文本里认表；长别名命中时丢弃它的短子串命中。"""
    hits: List[Tuple[int, str, str]] = []
    for code, aliases in _TABLE_ALIASES.items():
        if code not in index:
            continue
        for alias in aliases:
            normalized = _normalize(alias)
            if normalized and normalized in text:
                hits.append((len(normalized), code, normalized))
    hits.sort(key=lambda item: (-item[0], item[1]))
    kept: List[Tuple[int, str, str]] = []
    for length, code, normalized in hits:
        if any(normalized in longer for _, _, longer in kept):
            continue
        kept.append((length, code, normalized))
    return [code for _, code, _ in kept]


def _pages_from_text(text: Any) -> List[int]:
    """从"第18、12页"这类展示文本里取页码；只做补充，不作为唯一依据。"""
    pages: List[int] = []
    for match in _PAGE_LIST_RE.finditer(str(text or "")):
        for raw in re.findall(r"\d{1,3}", match.group(1)):
            page = int(raw)
            if 1 <= page <= 999:
                pages.append(page)
    return pages


def _page_texts(result_meta: Any) -> List[str]:
    """当前分析代际的逐页文本（``result_meta.structure_view.page_texts``）。"""
    payload = _json_dict(result_meta)
    structure = _json_dict(payload.get("structure_view"))
    return [str(text or "") for text in _json_list(structure.get("page_texts"))]


def _pages_containing(page_texts: Sequence[str], needle: str, *, limit: int = 3) -> List[int]:
    """在逐页文本里找短语出现的页；归一化后比较，跨行也能命中。"""
    target = _normalize(needle)
    if not target:
        return []
    pages: List[int] = []
    for index, text in enumerate(page_texts, start=1):
        if target in _normalize(text):
            pages.append(index)
            if len(pages) >= limit:
                break
    return pages


def _finding_locations(
    checkers: Sequence[str], result_row: Any
) -> List[Tuple[int, Optional[List[float]], str]]:
    """checker 编号 → 当前分析代际的 finding 证据页/bbox。"""
    wanted = {str(code).strip() for code in checkers if str(code).strip()}
    if not wanted:
        return []
    findings = _json_list(_json_dict(result_row).get("rule_findings"))
    locations: List[Tuple[int, Optional[List[float]], str]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        tags = {str(tag).strip() for tag in _json_list(finding.get("tags"))}
        rule_id = str(finding.get("rule_id") or finding.get("check") or "").strip()
        if not (wanted & tags or rule_id in wanted):
            continue
        label = str(finding.get("title") or finding.get("check") or "问题证据").strip()
        for ref in _json_list(finding.get("evidence")):
            if not isinstance(ref, dict):
                continue
            page = ref.get("page")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                continue
            bbox = ref.get("bbox")
            locations.append((page, bbox if isinstance(bbox, list) else None, label))
        display = _json_dict(finding.get("display"))
        for page in _pages_from_text(display.get("page_text")):
            locations.append((page, None, label))
    return locations


def _dedupe(
    locations: Sequence[Tuple[int, Optional[List[float]], str]],
) -> List[ObligationLocation]:
    # 页面跳转是页级的：同一页只留一个入口。先出现的位置（表清单）优先，
    # 后面的同页 finding 证据不重复出芯片；bbox 仍随该入口保留。
    seen_pages = set()
    result: List[ObligationLocation] = []
    for page, bbox, label in locations:
        if page in seen_pages:
            continue
        seen_pages.add(page)
        result.append(
            ObligationLocation(
                page=page,
                label=label or None,
                bbox=bbox if isinstance(bbox, list) else None,
            )
        )
    result.sort(key=lambda item: (item.page, item.label or ""))
    return result[:_MAX_LOCATIONS]


def merge_locations(*groups: Sequence[ObligationLocation]) -> List[ObligationLocation]:
    """同源义务共享位置：按页去重，保留先出现的 label/bbox。"""
    seen_pages = set()
    merged: List[ObligationLocation] = []
    for group in groups:
        for location in group:
            if location.page in seen_pages:
                continue
            seen_pages.add(location.page)
            merged.append(location)
    merged.sort(key=lambda item: (item.page, item.label or ""))
    return merged[:_MAX_LOCATIONS]


def resolve_obligation_locations(
    item: Any,
    structured_ingest: Any = None,
    result_row: Any = None,
    result_meta: Any = None,
) -> List[ObligationLocation]:
    """把一条义务解析成可点击的原文位置；没有可靠位置时返回空列表。"""
    table_index = _reading_tables(structured_ingest)
    locations: List[Tuple[int, Optional[List[float]], str]] = []

    if getattr(item, "obligation_id", "") == "OBL-STRUCT-COVER":
        # 封面要素的定义就是第 1 页；再给一个正文表题页，便于核对年度是否一致。
        locations.append((1, None, "封面"))
        if table_index:
            first_code = min(table_index, key=lambda code: table_index[code]["page"])
            first = table_index[first_code]
            locations.append(
                (
                    first["page"],
                    first["bbox"],
                    f"正文表题 · {first['label'] or '首张表'}",
                )
            )

    text = _normalize(
        " ".join(
            filter(
                None,
                [
                    getattr(item, "title", None),
                    getattr(item, "detail", None),
                    getattr(item, "basis", None),
                ],
            )
        )
    )
    codes = _matched_table_codes(text, table_index)
    for code in _EXTRA_TABLES.get(getattr(item, "obligation_id", ""), ()):
        if code in table_index and code not in codes:
            codes.append(code)
    table_pages: List[int] = []
    for code in codes:
        row = table_index.get(code)
        if row is not None:
            table_pages.append(row["page"])
            locations.append((row["page"], row["bbox"], row["label"]))

    locations.extend(
        _finding_locations(getattr(item, "checkers", None) or [], result_row)
    )

    # 逐页文本：detail 里的引号短语（旧科目名等）和"十九、住房保障支出"行标签
    # 能把位置钉到续页/说明页，而不是只停在表首。
    page_texts = _page_texts(result_meta)
    detail = str(getattr(item, "detail", None) or "")
    if page_texts:
        for quoted in _QUOTED_RE.findall(detail):
            for page in _pages_containing(page_texts, quoted, limit=2):
                locations.append((page, None, f"含“{quoted}”"))
        if table_pages:
            min_page = min(table_pages)
            max_page = min_page + 3
            for label in _ROW_LABEL_RE.findall(detail):
                for page in _pages_containing(page_texts, label, limit=5):
                    if min_page <= page <= max_page:
                        locations.append((page, None, f"含“{label}”"))

    for page in _pages_from_text(detail):
        locations.append((page, None, "原文提及"))

    return _dedupe(locations)
