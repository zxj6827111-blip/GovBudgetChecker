from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rapidfuzz import fuzz

from .rules_v33 import (
    Document,
    Issue,
    Rule,
    normalize_text,
    parse_number,
    _resolve_fiscal_year,
    _txt_fund_merged_pages,
    _txt_fund_page_for,
)
from .amount_math import classify_amount_diff, compute_dynamic_envelope
from .rule_outcome import (
    RuleDeferred,
    RuleNotApplicable,
    RuleOutcomeSignal,
    STATUS_INSUFFICIENT_DATA,
)
from .field_extractor import (
    STATUS_MISSING_COLUMN,
    STATUS_OUT_OF_BOUNDS,
    StrictValue,
    extract_row_strict,
    find_column_index,
    parse_strict_cell,
)


# Require both sides to be non-digit so numeric amounts like 20765.62
# are not misread as year 2076.
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?:\s*\u5e74)?(?!\d)")
_YEAR_SHORT_RE = re.compile(r"(?<!\d)(\d{2})(?=\s*\u5e74)")
_YEAR_SHORT_HINT_RE = re.compile(
    r"(?<!\d)(\d{2})(?=\s*(?:\u5e74|\u5e74\u5ea6|\u9884\u7b97|\u51b3\u7b97|budget|final|settlement|accounts))",
    re.I,
)

_SECTION_TOKENS: List[Tuple[str, Sequence[str]]] = [
    ("BUD-SEC-001", ("\u9884\u7b97\u7f16\u5236\u8bf4\u660e",)),
    ("BUD-SEC-002", ("\u5176\u4ed6\u76f8\u5173\u60c5\u51b5\u8bf4\u660e",)),
    ("BUD-SEC-003", ("\u4e09\u516c", "\u9884\u7b97", "\u8bf4\u660e")),
    ("BUD-SEC-004", ("\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97",)),
]

_PLACEHOLDER_PATTERNS: List[re.Pattern[str]] = [
    # Match placeholders like XXXXX even when adjacent to Chinese text.
    re.compile(r"(?<![A-Za-z0-9])[Xx]{2,}(?![A-Za-z0-9])"),
    re.compile(r"Ｘ{2,}"),
    re.compile(r"[×✕]{2,}"),
    re.compile(r"\bTBD\b", re.I),
    re.compile(r"\bN/?A\b", re.I),
    re.compile(r"\{\s*\u5355\u4f4d\s*\}"),
    re.compile(r"\{\s*\u90e8\u95e8\s*\}"),
    re.compile(r"\u5f85\u586b\u5199"),
    re.compile(r"\u8bf7\u586b\u5199"),
    re.compile(r"\(\s*\u5f85A\s*\)"),
    re.compile(r"\(\s*\u7565\s*\)"),
    # Repeated punctuation often appears in unfinished template text.
    re.compile(r"(?:\.{3,}|\u2026{2,}|\u3002{3,}|\u00b7{3,})"),
]

_YEAR_WHITELIST_TOKENS = (
    "\u622a\u81f3",
    "\u7acb\u9879\u4f9d\u636e",
    "\u4efb\u52a1\u4e66",
    "\u4f9d\u636e",
)

_YEAR_TARGET_LINE_TOKENS = (
    "\u9884\u7b97",
    "\u51b3\u7b97",
    "\u76ee\u5f55",
    "\u62a5\u544a",
    "\u8868",
)

_EMPTY_TABLE_NOTE_TOKEN = "\u672c\u8868\u4e3a\u7a7a\u8868"
_EMPTY_TABLE_EXPECTED_PHRASES: Dict[str, str] = {
    "BUD_T6": "\u65e0\u653f\u5e9c\u6027\u57fa\u91d1\u9884\u7b97\u8d22\u653f\u62e8\u6b3e\u5b89\u6392",
    "BUD_T7": "\u65e0\u56fd\u6709\u8d44\u672c\u7ecf\u8425\u9884\u7b97\u8d22\u653f\u62e8\u6b3e\u5b89\u6392",
    "BUD_T9": "\u65e0\u8d22\u653f\u62e8\u6b3e\u4e09\u516c\u7ecf\u8d39\u9884\u7b97\u5b89\u6392",
}
_EMPTY_TABLE_NOTE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "BUD_T6": ("\u653f\u5e9c\u6027\u57fa\u91d1", "\u57fa\u91d1\u9884\u7b97"),
    "BUD_T7": ("\u56fd\u6709\u8d44\u672c\u7ecf\u8425", "\u56fd\u6709\u8d44\u672c"),
    "BUD_T9": ("\u4e09\u516c", "\u56e0\u516c\u51fa\u56fd", "\u516c\u52a1\u63a5\u5f85", "\u516c\u52a1\u7528\u8f66"),
}
_EMPTY_TABLE_NOTE_TOKENS: Tuple[str, ...] = (
    "\u672c\u8868\u4e3a\u7a7a\u8868",
    "\u65e0\u6b64\u9879\u8d44\u91d1\u5b89\u6392",
    "\u65e0\u6570\u636e",
    "\u6545\u672c\u8868\u65e0\u6570\u636e",
    "\u672a\u5b89\u6392",
    "\u4e0d\u5b58\u5728",
)

_PERFORMANCE_LINE_RE = re.compile(r"\u7ee9\u6548\u76ee\u6807[^\n\uff1b;\u3002]{0,180}")
_PERFORMANCE_COUNT_RE = re.compile(r"(\d+)\s*\u4e2a(?:\u9879\u76ee)?")
_PERFORMANCE_AMOUNT_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(\u4ebf\u5143|\u4e07\u5143|\u5143)")


BUDGET_TABLE_SPECS: List[Dict[str, Any]] = [
    {
        "key": "BUD_T1",
        "aliases": [
            "\u90e8\u95e8\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868",
            "\u9884\u7b97\u5355\u4f4d\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868",
            "\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868",
        ],
    },
    {
        "key": "BUD_T2",
        "aliases": [
            "\u90e8\u95e8\u6536\u5165\u9884\u7b97\u603b\u8868",
            "\u9884\u7b97\u5355\u4f4d\u6536\u5165\u9884\u7b97\u603b\u8868",
            "\u6536\u5165\u9884\u7b97\u603b\u8868",
        ],
    },
    {
        "key": "BUD_T3",
        "aliases": [
            "\u90e8\u95e8\u652f\u51fa\u9884\u7b97\u603b\u8868",
            "\u9884\u7b97\u5355\u4f4d\u652f\u51fa\u9884\u7b97\u603b\u8868",
            "\u652f\u51fa\u9884\u7b97\u603b\u8868",
        ],
    },
    {
        "key": "BUD_T4",
        "aliases": [
            "\u90e8\u95e8\u8d22\u653f\u62e8\u6b3e\u6536\u652f\u9884\u7b97\u603b\u8868",
            "\u9884\u7b97\u5355\u4f4d\u8d22\u653f\u62e8\u6b3e\u6536\u652f\u9884\u7b97\u603b\u8868",
            "\u8d22\u653f\u62e8\u6b3e\u6536\u652f\u9884\u7b97\u603b\u8868",
        ],
    },
    {
        "key": "BUD_T5",
        "aliases": [
            "\u90e8\u95e8\u4e00\u822c\u516c\u5171\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
            "\u9884\u7b97\u5355\u4f4d\u4e00\u822c\u516c\u5171\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
            "\u4e00\u822c\u516c\u5171\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
        ],
    },
    {
        "key": "BUD_T6",
        "aliases": [
            "\u90e8\u95e8\u653f\u5e9c\u6027\u57fa\u91d1\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
            "\u9884\u7b97\u5355\u4f4d\u653f\u5e9c\u6027\u57fa\u91d1\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
            "\u653f\u5e9c\u6027\u57fa\u91d1\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
        ],
    },
    {
        "key": "BUD_T7",
        "aliases": [
            "\u90e8\u95e8\u56fd\u6709\u8d44\u672c\u7ecf\u8425\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
            "\u9884\u7b97\u5355\u4f4d\u56fd\u6709\u8d44\u672c\u7ecf\u8425\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
            "\u56fd\u6709\u8d44\u672c\u7ecf\u8425\u9884\u7b97\u652f\u51fa\u529f\u80fd\u5206\u7c7b\u9884\u7b97\u8868",
        ],
    },
    {
        "key": "BUD_T8",
        "aliases": [
            "\u90e8\u95e8\u4e00\u822c\u516c\u5171\u9884\u7b97\u57fa\u672c\u652f\u51fa\u90e8\u95e8\u9884\u7b97\u7ecf\u6d4e\u5206\u7c7b\u9884\u7b97\u8868",
            "\u9884\u7b97\u5355\u4f4d\u4e00\u822c\u516c\u5171\u9884\u7b97\u57fa\u672c\u652f\u51fa\u90e8\u95e8\u9884\u7b97\u7ecf\u6d4e\u5206\u7c7b\u9884\u7b97\u8868",
            "\u4e00\u822c\u516c\u5171\u9884\u7b97\u57fa\u672c\u652f\u51fa\u90e8\u95e8\u9884\u7b97\u7ecf\u6d4e\u5206\u7c7b\u9884\u7b97\u8868",
        ],
    },
    {
        "key": "BUD_T9",
        "aliases": [
            "\u90e8\u95e8\u201c\u4e09\u516c\u201d\u7ecf\u8d39\u548c\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97\u8868",
            "\u5355\u4f4d\u201c\u4e09\u516c\u201d\u7ecf\u8d39\u548c\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97\u8868",
            "\u4e09\u516c\u7ecf\u8d39\u548c\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97\u8868",
        ],
    },
]

_TABLE_NAME_BY_KEY: Dict[str, str] = {
    spec["key"]: str(spec["aliases"][0]) for spec in BUDGET_TABLE_SPECS
}

_BUDGET_TABLE_TITLE_NORMS: Tuple[str, ...] = tuple(
    sorted(
        {
            normalize_text(alias)
            for spec in BUDGET_TABLE_SPECS
            for alias in spec.get("aliases", [])
            if alias
        },
        key=len,
        reverse=True,
    )
)


def _table_norm_aliases() -> Dict[str, List[str]]:
    return {
        spec["key"]: [normalize_text(alias) for alias in spec["aliases"]]
        for spec in BUDGET_TABLE_SPECS
    }


def _numbers_in_row(row: Sequence[Any]) -> List[float]:
    vals: List[float] = []
    for cell in row:
        v = parse_number(cell)
        if v is not None:
            vals.append(v)
    return vals


def _row_text(row: Sequence[Any]) -> str:
    return "".join(str(c or "") for c in row)


def _table_display_name(table_key: str) -> str:
    return _TABLE_NAME_BY_KEY.get(table_key, table_key)


def _unique_pages(*values: Any) -> List[int]:
    pages: List[int] = []
    seen = set()
    for value in values:
        if value is None:
            continue
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page <= 0 or page in seen:
            continue
        seen.add(page)
        pages.append(page)
    return pages


def _make_location_ref(
    *,
    role: Optional[str] = None,
    page: Optional[int] = None,
    table: Optional[str] = None,
    section: Optional[str] = None,
    row: Optional[str] = None,
    field: Optional[str] = None,
    code: Optional[str] = None,
    subject: Optional[str] = None,
    value: Optional[float] = None,
) -> Dict[str, Any]:
    ref: Dict[str, Any] = {}
    if role:
        ref["role"] = role
    if page:
        ref["page"] = page
    if table:
        ref["table"] = table
    if section:
        ref["section"] = section
    if row:
        ref["row"] = row
    if field:
        ref["field"] = field
    if code:
        ref["code"] = code
    if subject:
        ref["subject"] = subject
    if value is not None:
        ref["value"] = value
    return ref


def _make_cross_table_location(
    *refs: Dict[str, Any],
    field: Optional[str] = None,
    row: Optional[str] = None,
) -> Dict[str, Any]:
    valid_refs = [ref for ref in refs if ref]
    location: Dict[str, Any] = {}

    pages = _unique_pages(*(ref.get("page") for ref in valid_refs))
    if len(pages) == 1:
        location["page"] = pages[0]
    elif pages:
        location["pages"] = pages

    tables: List[str] = []
    seen_tables = set()
    for ref in valid_refs:
        table = str(ref.get("table") or "").strip()
        if not table or table in seen_tables:
            continue
        seen_tables.add(table)
        tables.append(table)

    if tables:
        location["table"] = " / ".join(tables)
    if row:
        location["row"] = row
    if field:
        location["field"] = field
    if valid_refs:
        location["table_refs"] = valid_refs
    return location


def _extract_year_candidates(text: str) -> List[int]:
    years: List[int] = []
    if not text:
        return years

    for raw in _YEAR_RE.findall(text):
        year = int(raw)
        if 2000 <= year <= 2099:
            years.append(year)

    for raw in _YEAR_SHORT_RE.findall(text):
        year = 2000 + int(raw)
        if 2000 <= year <= 2099:
            years.append(year)

    return years


def _line_contains_budget_table_title(line: str) -> bool:
    normalized_line = normalize_text(line or "")
    if not normalized_line:
        return False
    if ("\u9884\u7b97\u8868" in (line or "")) or ("\u9884\u7b97\u603b\u8868" in (line or "")):
        return True
    return any(alias_norm and alias_norm in normalized_line for alias_norm in _BUDGET_TABLE_TITLE_NORMS)


def _is_toc_page(text: str) -> bool:
    if not text:
        return False
    normalized = normalize_text(text)
    head_norm = normalize_text("\n".join(text.splitlines()[:12]))
    if "\u76ee\u5f55" in head_norm:
        return True
    if ("\u76ee\u5f55" in normalized) and (
        normalized.count("\u9884\u7b97\u8868") >= 3 or normalized.count("\u8868") >= 8
    ):
        return True
    return False


def _line_for_span(text: str, start: int, end: int) -> str:
    left = text.rfind("\n", 0, start)
    right = text.find("\n", end)
    if left < 0:
        left = 0
    else:
        left += 1
    if right < 0:
        right = len(text)
    return text[left:right].strip()


def _snippet(text: str, start: int, end: int, radius: int = 24) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]


_TOC_LEADER_LINE_RE = re.compile(
    r"^\s*(?:\d+[\.、]?)?\s*.+(?:\.{3,}|\u2026{2,}|\u3002{3,}|\u00b7{3,})\s*\d+\s*$"
)


def _is_toc_leader_line(line: str) -> bool:
    if not line:
        return False
    if not _TOC_LEADER_LINE_RE.search(line):
        return False
    return any(token in line for token in ("\u8868", "\u7ae0", "\u76ee\u5f55", "\u8bf4\u660e"))


def _is_target_year_line(line: str) -> bool:
    return any(token in line for token in _YEAR_TARGET_LINE_TOKENS)


def _largest_table_on_page(tables: Sequence[Sequence[Sequence[Any]]]) -> Optional[List[List[str]]]:
    if not tables:
        return None

    def _score(tb: Sequence[Sequence[Any]]) -> Tuple[int, int]:
        rows = len(tb)
        non_empty = 0
        for row in tb:
            for c in row:
                if str(c or "").strip():
                    non_empty += 1
        return (rows, non_empty)

    largest = max(tables, key=_score)
    return [[str(c or "").strip() for c in row] for row in largest]


def _has_total_row(rows: Sequence[Sequence[Any]]) -> bool:
    for row in rows[-8:]:
        txt = _row_text(row)
        if ("\u5408\u8ba1" in txt) or ("\u603b\u8ba1" in txt):
            return True
    return False


def _infer_report_year(doc: Document) -> Optional[int]:
    weighted_counts: Dict[int, int] = {}

    def _bump(year: int, weight: int) -> None:
        if 2000 <= year <= 2099:
            weighted_counts[year] = weighted_counts.get(year, 0) + weight

    path_text = doc.path or ""
    for y in _extract_year_candidates(path_text):
        _bump(y, 6)
    for raw in _YEAR_SHORT_HINT_RE.findall(path_text):
        _bump(2000 + int(raw), 7)

    for pidx, text in enumerate(doc.page_texts[:6]):
        if not text:
            continue
        for raw_line in text.splitlines()[:40]:
            line = raw_line.strip()
            if not line:
                continue
            years_in_line = _extract_year_candidates(line)
            if not years_in_line:
                continue
            weight = 1
            if pidx == 0:
                weight += 1
            if any(
                token in line
                for token in (
                    "\u9884\u7b97",
                    "\u51b3\u7b97",
                    "\u90e8\u95e8",
                    "\u5355\u4f4d",
                    "\u76ee\u5f55",
                    "\u62a5\u544a",
                )
            ):
                weight += 2
            if _line_contains_budget_table_title(line):
                weight += 2
            for y in years_in_line:
                _bump(y, weight)

    if not weighted_counts:
        return None
    return max(weighted_counts.items(), key=lambda item: (item[1], item[0]))[0]


def _is_close(a: Optional[float], b: Optional[float], abs_tol: float = 1.0, rel_tol: float = 0.001) -> bool:
    if a is None or b is None:
        return False
    tol = max(abs_tol, rel_tol * max(abs(a), abs(b)))
    return abs(a - b) <= tol


def _to_wanyuan(v: Optional[float], unit: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    unit_key = (unit or "").strip()
    factor = {
        "\u5143": 0.0001,
        "\u4e07\u5143": 1.0,
        "\u4ebf\u5143": 10000.0,
    }.get(unit_key, 1.0)
    return v * factor


def _dynamic_half_unit_tol(a: float, b: float) -> float:
    """P1-2b: Return half-unit tolerance based on displayed decimal places of both values."""
    from decimal import Decimal as _D
    try:
        sa = abs(_D(str(a)).as_tuple().exponent)
        sb = abs(_D(str(b)).as_tuple().exponent)
        return 0.5 * (10 ** (-max(sa, sb)))
    except Exception:
        return 0.05  # safe fallback


def _contains_all_tokens(text: str, tokens: Sequence[str]) -> bool:
    return all(tok in text for tok in tokens)


def find_budget_anchors(doc: Document) -> Dict[str, List[int]]:
    aliases = _table_norm_aliases()
    anchors: Dict[str, List[int]] = {spec["key"]: [] for spec in BUDGET_TABLE_SPECS}

    for pidx, raw in enumerate(doc.page_texts):
        if not raw:
            continue
        # Skip TOC / glossary pages, which often contain table names but are not actual table pages.
        if ("目录" in raw) or ("名词解释" in raw):
            continue
        normalized = normalize_text(raw)
        if not normalized:
            continue

        for key, norms in aliases.items():
            hit = False
            for alias_norm in norms:
                if not alias_norm:
                    continue
                if (alias_norm in normalized) or (fuzz.partial_ratio(alias_norm, normalized) >= 95):
                    hit = True
                    break
            if hit:
                anchors[key].append(pidx + 1)

    for key, pages in anchors.items():
        anchors[key] = sorted(set(pages))
    return anchors


def _first_anchor_page(anchors: Dict[str, List[int]], key: str) -> Optional[int]:
    pages = anchors.get(key) or []
    if not pages:
        return None
    return min(pages)


def _get_budget_table_rows(
    doc: Document,
    anchors: Dict[str, List[int]],
    table_key: str,
    include_continuation: bool = True,
) -> Tuple[Optional[List[List[str]]], Optional[int]]:
    page = _first_anchor_page(anchors, table_key)
    if not page:
        return None, None

    if page > len(doc.page_tables):
        return None, page

    main_table = _largest_table_on_page(doc.page_tables[page - 1])
    if not main_table:
        return None, page

    rows = main_table
    if include_continuation and not _has_total_row(rows) and page < len(doc.page_tables):
        next_table = _largest_table_on_page(doc.page_tables[page])
        if next_table:
            rows = rows + next_table

    return rows, page




def _is_table_boundary_cell(c: str) -> bool:
    """判断单元格是否为下一个字段/指标的文本边界，防止越界扫描补数。"""
    if not c:
        return False
    c_strip = c.strip()
    if any(kw in c_strip for kw in ("收入", "支出", "总计", "合计", "项目", "结转", "结余", "资金", "科目", "预算数")):
        return True
    if re.search(r"[\u4e00-\u9fa5]", c_strip) and c_strip not in ("万元", "元", "亿元"):
        return True
    return False


def _extract_t1_t4_strict_totals(
    rows: Sequence[Sequence[Any]],
    default_unit: str = "万元",
) -> Tuple[Optional[StrictValue], Optional[StrictValue]]:
    """从 T1/T4 表格中严格提取收入总计与支出总计单元格，保留原始精度与单位。

    支持单栏、双栏及多年度（本年/上年、纯年份 2025年/2024年、“2025 预算数”等）布局；
    严格按期间列定位，遇非数值或空白单元格立即停止（fail-closed），绝不猜借上一期间金额。
    期间判定只依据真正的表头行：含数值单元格（金额/科目编码）的数据行一律不参与，
    防止数据金额中的四位片段（如 2050.00）被误当作期间年份污染 max_year。
    期间无法判定时（多数值列且无任何期间语义），取数一律 fail-closed 返回 None，不得猜列。
    """
    income_val: Optional[StrictValue] = None
    expense_val: Optional[StrictValue] = None

    if not rows:
        return None, None

    max_len = max(len(r) for r in rows)
    col_is_prior = [False] * max_len
    col_is_current = [False] * max_len
    col_is_amount = [False] * max_len

    header_years: List[int] = []
    col_years: List[Optional[int]] = [None] * max_len

    # 年份采信双通道：带“年”字锚定（2025年/2024年度），或单元格含明确期间词
    # （预算数/决算数/执行数/调整数，如“2025 预算数”）；金额四位片段、文号数字一律不采信
    _YEAR_WITH_NIAN_RE = re.compile(r"(?:19|20)\d{2}\s*年")
    _YEAR_4DIGIT_RE = re.compile(r"(?:19|20)\d{2}")
    _PERIOD_WORDS = ("预算数", "决算数", "执行数", "调整数")
    _AMOUNT_HEADER_WORDS = _PERIOD_WORDS + (
        "金额",
        "数额",
        "本年",
        "当年",
        "预算年度",
        "上年",
        "去年",
        "以前年度",
        "上年度",
    )

    # 表头行识别：任一单元格为数值（金额/科目编码）即判定为数据行，整行不参与期间推断
    header_rows: List[List[str]] = []
    for r in rows[:3]:
        str_r = [str(c or "").strip() for c in r]
        if any(parse_strict_cell(c, default_unit=default_unit).is_numeric for c in str_r if c):
            continue
        header_rows.append(str_r)

    for str_r in header_rows:
        for ci, txt in enumerate(str_r):
            if not txt:
                continue
            if _YEAR_WITH_NIAN_RE.search(txt) or any(w in txt for w in _AMOUNT_HEADER_WORDS):
                col_is_amount[ci] = True
            if not (_YEAR_WITH_NIAN_RE.search(txt) or any(w in txt for w in _PERIOD_WORDS)):
                continue
            for ym in _YEAR_4DIGIT_RE.findall(txt):
                y = int(ym)
                if 2000 <= y <= 2099:
                    header_years.append(y)
                    if col_years[ci] is None or y > col_years[ci]:
                        col_years[ci] = y

    distinct_years = sorted(set(header_years))
    max_year = max(distinct_years) if distinct_years else None

    for str_r in header_rows:
        for ci, txt in enumerate(str_r):
            if not txt:
                continue
            if any(p in txt for p in ("上年", "去年", "以前年度", "上年度", "结转", "执行数", "决算数", "决算")):
                col_is_prior[ci] = True
            elif any(cur in txt for cur in ("本年", "当年", "预算年度")):
                col_is_current[ci] = True
            elif col_years[ci] is not None and max_year is not None and len(distinct_years) >= 2:
                if col_years[ci] < max_year:
                    col_is_prior[ci] = True
                elif col_years[ci] == max_year:
                    col_is_current[ci] = True

    def _extract_metric(row_cells: List[str], label_idx: int, stop_idx: int) -> Optional[StrictValue]:
        candidate_cols = list(range(label_idx + 1, stop_idx))
        if not candidate_cols:
            return None

        current_cols = [ci for ci in candidate_cols if col_is_current[ci]]
        if len(current_cols) > 1:
            # 重复 current 表头没有列组证据可消歧，不能按排列顺序取首列。
            return None

        if len(current_cols) == 1:
            target_ci = current_cols[0]
        else:
            # fallback 只接受表头结构能证明唯一的金额列；单元格是否为空不能参与列身份判断。
            # 因而多个未识别期间列即使恰好只有一个有值，也必须 fail-closed。
            amount_cols = [ci for ci in candidate_cols if col_is_amount[ci]]
            if len(amount_cols) > 1:
                return None
            if len(amount_cols) == 1:
                target_ci = amount_cols[0]
            elif len(candidate_cols) == 1:
                target_ci = candidate_cols[0]
            else:
                return None

            # “唯一但明确为 prior”仍不是 current，不能靠排除法借用上期金额。
            if col_is_prior[target_ci]:
                return None

        probe_c = row_cells[target_ci]
        if _is_table_boundary_cell(probe_c):
            return None
        sv = parse_strict_cell(probe_c, default_unit=default_unit, col_index=target_ci)
        return sv if sv.is_numeric else None

    for row in rows:
        cells = [str(c or "").strip() for c in row]
        inc_label_idx: Optional[int] = None
        exp_label_idx: Optional[int] = None

        for idx, cell in enumerate(cells):
            if "收入总计" in cell or ("本年收入" in cell and "合计" in cell):
                if inc_label_idx is None:
                    inc_label_idx = idx
            if "支出总计" in cell or ("本年支出" in cell and "合计" in cell):
                if exp_label_idx is None:
                    exp_label_idx = idx

        if inc_label_idx is not None and income_val is None:
            stop_idx = exp_label_idx if (exp_label_idx is not None and exp_label_idx > inc_label_idx) else len(cells)
            income_val = _extract_metric(cells, inc_label_idx, stop_idx)

        if exp_label_idx is not None and expense_val is None:
            stop_idx = len(cells)
            expense_val = _extract_metric(cells, exp_label_idx, stop_idx)

        if income_val is not None and expense_val is not None:
            return income_val, expense_val

    return income_val, expense_val


_extract_t4_strict = _extract_t1_t4_strict_totals


def _extract_total_basic_project_strict(
    rows: Sequence[Sequence[Any]],
    default_unit: str = "万元",
) -> Tuple[Optional[StrictValue], Optional[StrictValue], Optional[StrictValue]]:
    """严格提取表格中的合计、基本支出、项目支出单元格，保留原始精度与单位。

    逐字段独立提取，某一列缺失不阻断其余已定位列的取数与核验。
    """
    idx_total: Optional[int] = None
    idx_basic: Optional[int] = None
    idx_project: Optional[int] = None

    for r in rows[:3]:
        str_r = [str(c or "").strip() for c in r]
        if idx_total is None:
            idx_total = find_column_index(str_r, ["合计", "总计", "本年支出合计", "支出预算合计"])
        if idx_basic is None:
            idx_basic = find_column_index(str_r, ["基本支出", "基本支出预算", "基本支出合计"], exclude_keys=["人员", "公用"])
        if idx_project is None:
            idx_project = find_column_index(str_r, ["项目支出", "项目支出预算", "项目支出合计"])
        if idx_total is not None and idx_basic is not None and idx_project is not None:
            break

    # 至少有一列被识别到，否则无法提取
    if idx_total is None and idx_basic is None and idx_project is None:
        return None, None, None

    for row in rows:
        txt = _row_text(row)
        if "合计" not in txt and "总计" not in txt:
            continue
        # P2-1: only check column 0 (subject code column); do not scan amount columns
        _first_cell = str(row[0] if row else "").strip()
        if _first_cell.isdigit() and len(_first_cell) in (3, 5, 7):
            continue
        if not any(parse_strict_cell(c, default_unit=default_unit).is_numeric for c in row):
            continue

        v_tot = extract_row_strict(row, idx_total, "合计", default_unit=default_unit) if idx_total is not None else None
        v_bas = extract_row_strict(row, idx_basic, "基本支出", default_unit=default_unit) if idx_basic is not None else None
        v_prj = extract_row_strict(row, idx_project, "项目支出", default_unit=default_unit) if idx_project is not None else None
        return (
            v_tot if (v_tot and v_tot.is_numeric) else None,
            v_bas if (v_bas and v_bas.is_numeric) else None,
            v_prj if (v_prj and v_prj.is_numeric) else None,
        )

    return None, None, None


_extract_t1_strict = _extract_t1_t4_strict_totals


def _extract_text_strict_by_patterns(
    text: str, patterns: Sequence[str], default_unit: str = "万元"
) -> Optional[StrictValue]:
    """从说明文本中按正则提取金额，保留原始字面量、小数位与单位。"""
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(1).strip()
            sv = parse_strict_cell(raw, default_unit=default_unit)
            if sv.is_numeric:
                return sv
    return None



def _extract_t1_totals(rows: Sequence[Sequence[Any]]) -> Tuple[Optional[float], Optional[float]]:
    v_inc, v_exp = _extract_t1_strict(rows)
    inc_flt = float(v_inc.decimal_val) if v_inc and v_inc.decimal_val is not None else None
    exp_flt = float(v_exp.decimal_val) if v_exp and v_exp.decimal_val is not None else None
    if inc_flt is not None and exp_flt is not None:
        return inc_flt, exp_flt
    for row in rows:
        txt = _row_text(row)
        nums = _numbers_in_row(row)
        if len(nums) >= 2 and ("\u603b\u8ba1" in txt or "\u5408\u8ba1" in txt):
            return (nums[0], nums[-1])
    return (None, None)


def _extract_t4_totals(rows: Sequence[Sequence[Any]]) -> Tuple[Optional[float], Optional[float]]:
    for row in rows:
        cells = [str(c or "").strip() for c in row]
        income_val: Optional[float] = None
        expense_val: Optional[float] = None
        for idx, cell in enumerate(cells):
            if "\u6536\u5165\u603b\u8ba1" in cell:
                for probe in cells[idx + 1 :]:
                    v = parse_number(probe)
                    if v is not None:
                        income_val = float(v)
                        break
            if "\u652f\u51fa\u603b\u8ba1" in cell:
                for probe in cells[idx + 1 :]:
                    v = parse_number(probe)
                    if v is not None:
                        expense_val = float(v)
                        break
        if income_val is not None and expense_val is not None:
            return income_val, expense_val
    return _extract_t1_totals(rows)


def _extract_total_basic_project(rows: Sequence[Sequence[Any]]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    for row in rows:
        txt = _row_text(row)
        nums = _numbers_in_row(row)
        if len(nums) < 3:
            continue
        if "\u5408\u8ba1" in txt or "\u603b\u8ba1" in txt:
            return nums[-3], nums[-2], nums[-1]
    return None, None, None


def _extract_t3_strict(
    rows: Sequence[Sequence[Any]],
    default_unit: str = "万元",
) -> Tuple[Optional[StrictValue], Optional[StrictValue], Optional[StrictValue]]:
    """从 T3 表格中严格提取合计、基本支出、项目支出单元格，保留原始精度与单位。"""
    return _extract_total_basic_project_strict(rows, default_unit=default_unit)



def _extract_t8_strict(
    rows: Sequence[Sequence[Any]],
    default_unit: str = "万元",
) -> Tuple[Optional[StrictValue], Optional[StrictValue], Optional[StrictValue]]:
    """从 T8 表格中严格提取合计、人员经费、公用经费单元格，保留原始精度与单位。"""
    idx_total: Optional[int] = None
    idx_personnel: Optional[int] = None
    idx_public: Optional[int] = None

    for r in rows[:3]:
        str_r = [str(c or "").strip() for c in r]
        if idx_total is None:
            idx_total = find_column_index(str_r, ["合计", "总计", "本年支出合计", "支出预算合计", "小计"])
        if idx_personnel is None:
            idx_personnel = find_column_index(str_r, ["人员经费", "人员支出", "基本支出人员经费"])
        if idx_public is None:
            idx_public = find_column_index(str_r, ["公用经费", "公用支出", "日常公用经费", "公用经费支出"])
        if idx_total is not None and idx_personnel is not None and idx_public is not None:
            break

    if idx_total is None or idx_personnel is None or idx_public is None:
        return None, None, None

    for row in rows:
        txt = _row_text(row)
        if "合计" not in txt and "总计" not in txt:
            continue
        # P2-1: only check column 0 (subject code column); do not scan amount columns
        _first_cell = str(row[0] if row else "").strip()
        if _first_cell.isdigit() and len(_first_cell) in (3, 5, 7):
            continue
        if not any(parse_strict_cell(c, default_unit=default_unit).is_numeric for c in row):
            continue

        v_tot = extract_row_strict(row, idx_total, "合计", default_unit=default_unit)
        v_per = extract_row_strict(row, idx_personnel, "人员经费", default_unit=default_unit)
        v_pub = extract_row_strict(row, idx_public, "公用经费", default_unit=default_unit)
        return (
            v_tot if v_tot.is_numeric else None,
            v_per if v_per.is_numeric else None,
            v_pub if v_pub.is_numeric else None,
        )

    return None, None, None


def _extract_budget_formula_rows(rows: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) < 5:
            continue
        class_code = cells[0]
        if not re.fullmatch(r"\d{3}", class_code):
            continue

        name = cells[3] if len(cells) > 3 else ""
        if not name or ("\u5408\u8ba1" in name) or ("\u603b\u8ba1" in name):
            continue

        payload_values = [parse_number(cell) for cell in cells[4:]]
        numeric_values = [float(value) for value in payload_values if value is not None]
        if len(numeric_values) < 3:
            continue

        row_code = "".join(
            part for part in cells[:3] if re.fullmatch(r"\d{2,3}", str(part or "").strip())
        )
        row_label = f"{_format_functional_code(row_code)} {name}".strip() if row_code else name
        row_text = " ".join(part for part in (*cells[:3], name) if part)
        total, basic, project = numeric_values[-3:]
        extracted.append(
            {
                "code": row_code,
                "name": name,
                "row_label": row_label,
                "row_text": row_text,
                "total": total,
                "basic": basic,
                "project": project,
            }
        )
    return extracted


def _extract_headers(rows: Sequence[Sequence[Any]], depth: int = 3) -> List[str]:
    if not rows:
        return []
    max_cols = max(len(r) for r in rows[:depth])
    headers: List[str] = []
    for col in range(max_cols):
        parts: List[str] = []
        for r in rows[:depth]:
            if col < len(r):
                cell = str(r[col] or "").strip()
                if cell:
                    parts.append(cell)
        headers.append("".join(parts))
    return headers


def _extract_t9_strict_values(
    rows: Sequence[Sequence[Any]],
    default_unit: str = "万元",
) -> Dict[str, StrictValue]:
    """严格提取 T9 三公表格的各列数值（Contract C2 实现）。

    缺列、越界、空白、无法解析保留 StrictValue 状态（is_missing=True），
    绝对不隐式填充为 0.0。明确零（如 0, 0.00, -）正常保留为 is_zero=True 的数值。
    """
    headers = _extract_headers(rows, depth=3)
    norm_headers = [normalize_text(h) for h in headers]

    idx_map: Dict[str, int] = {}
    for idx, h in enumerate(norm_headers):
        if "机关运行经费预算数" in h or "机关运行经费" in h:
            idx_map["org_run"] = idx
        elif "因公出国境费" in h or "因公出国" in h:
            idx_map["abroad"] = idx
        elif "公务接待费" in h:
            idx_map["reception"] = idx
        elif "公务用车购置及运行费小计" in h or (
            "公务用车购置及运行费" in h and "小计" in h
        ):
            idx_map["car_sub"] = idx
        elif ("购置费" in h and "运行" not in h) or "公务用车购置" in h:
            idx_map["car_buy"] = idx
        elif ("运行费" in h or "运行维护费" in h) and "购置" not in h:
            idx_map["car_run"] = idx
        elif ("合计" in h or "总计" in h) and ("小计" not in h):
            idx_map["total"] = idx

    data_row: Optional[Sequence[Any]] = None
    for row in reversed(rows):
        nums = [parse_number(c) for c in row]
        if sum(v is not None for v in nums) >= 2:
            data_row = row
            break

    target_fields = ["total", "abroad", "reception", "car_sub", "car_buy", "car_run", "org_run"]
    if not data_row:
        return {
            field: StrictValue(
                raw_text="",
                status=STATUS_MISSING_COLUMN,
                col_name=field,
                reason="未在表格中找到有效数据行",
            )
            for field in target_fields
        }

    return {
        field: extract_row_strict(
            data_row,
            idx_map.get(field),
            field,
            default_unit=default_unit,
        )
        for field in target_fields
    }


def _extract_t9_values(rows: Sequence[Sequence[Any]]) -> Dict[str, Optional[float]]:
    """提取 T9 数值（兼容旧调用，缺失返回 None，明确零返回 0.0）。"""
    strict_map = _extract_t9_strict_values(rows)
    if all(sv.reason == "未在表格中找到有效数据行" for sv in strict_map.values()):
        return {}
    return {k: sv.as_float() for k, sv in strict_map.items()}


def _extract_number_by_patterns(text: str, patterns: Sequence[str]) -> Optional[float]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.S)
        if not m:
            continue
        raw = m.group(1).replace(",", "")
        try:
            return float(raw)
        except Exception:
            continue
    return None


def _is_year_like_number(value: float) -> bool:
    return abs(value - round(value)) < 1e-9 and 1900 <= value <= 2100


def _table_data_numbers(rows: Sequence[Sequence[Any]]) -> List[float]:
    data_numbers: List[float] = []
    for ridx, row in enumerate(rows):
        cells = [str(c or "").strip() for c in row]
        for cidx, cell in enumerate(cells):
            # Skip first column to avoid class/item codes being treated as money values.
            if cidx == 0:
                continue
            value = parse_number(cell)
            if value is None:
                continue
            if ridx <= 2 and _is_year_like_number(float(value)):
                continue
            data_numbers.append(float(value))
    return data_numbers


def _table_is_effectively_empty(rows: Sequence[Sequence[Any]]) -> bool:
    if not rows:
        return True
    values = _table_data_numbers(rows)
    if not values:
        return True
    return all(abs(v) <= 1e-9 for v in values)


def _has_structured_empty_note(page_text: str, table_key: str) -> bool:
    if not page_text:
        return False
    expected_phrase = _EMPTY_TABLE_EXPECTED_PHRASES.get(table_key, "")
    if expected_phrase and expected_phrase in page_text:
        return True

    keywords = _EMPTY_TABLE_NOTE_KEYWORDS.get(table_key, ())
    has_keyword = any(token in page_text for token in keywords)
    has_note_token = any(token in page_text for token in _EMPTY_TABLE_NOTE_TOKENS)
    has_note_prefix = bool(re.search(r"注[：:]", page_text))
    return has_keyword and (has_note_token or has_note_prefix)


def _find_foreign_empty_phrase(page_text: str, table_key: str) -> Optional[str]:
    for key, phrase in _EMPTY_TABLE_EXPECTED_PHRASES.items():
        if key == table_key:
            continue
        if phrase and phrase in page_text:
            return phrase
    return None


def _extract_performance_summary_metrics(
    text: str,
) -> Tuple[Optional[int], Optional[float], Optional[str]]:
    if not text:
        return None, None, None

    best: Optional[Tuple[int, Optional[int], Optional[float], str]] = None
    for match in _PERFORMANCE_LINE_RE.finditer(text):
        line = match.group(0).strip()
        if not line:
            continue

        count: Optional[int] = None
        count_match = _PERFORMANCE_COUNT_RE.search(line)
        if count_match:
            try:
                count = int(count_match.group(1))
            except Exception:
                count = None

        amount_wanyuan: Optional[float] = None
        for amount_match in _PERFORMANCE_AMOUNT_RE.finditer(line):
            raw_amount = amount_match.group(1).replace(",", "")
            unit = amount_match.group(2)
            try:
                parsed = float(raw_amount)
            except Exception:
                continue
            converted = _to_wanyuan(parsed, unit)
            if converted is not None:
                amount_wanyuan = converted

        if count is None and amount_wanyuan is None:
            continue

        score = 0
        if count is not None:
            score += 1
        if amount_wanyuan is not None:
            score += 1
        if "\u8bbe\u7f6e\u60c5\u51b5" in line or "\u7f16\u62a5\u60c5\u51b5" in line:
            score += 1

        candidate = (score, count, amount_wanyuan, line)
        if best is None or candidate[0] > best[0]:
            best = candidate

    if best is None:
        return None, None, None
    return best[1], best[2], best[3]


def _find_text_page(doc: Document, snippet: Optional[str]) -> Optional[int]:
    needle = normalize_text(str(snippet or "")).strip()
    if not needle:
        return None

    candidates = [needle]
    if len(needle) > 24:
        candidates.append(needle[:40])

    for pidx, page_text in enumerate(doc.page_texts):
        hay = normalize_text(page_text or "")
        if any(candidate and candidate in hay for candidate in candidates):
            return pidx + 1
    return None


def _find_text_page_after(
    doc: Document, snippet: Optional[str], start_page: int = 1
) -> Optional[int]:
    needle = normalize_text(str(snippet or "")).strip()
    if not needle:
        return None

    candidates = [needle]
    if len(needle) > 24:
        candidates.append(needle[:40])

    start_idx = max(start_page, 1) - 1
    for pidx in range(start_idx, len(doc.page_texts)):
        hay = normalize_text(doc.page_texts[pidx] or "")
        if any(candidate and candidate in hay for candidate in candidates):
            return pidx + 1
    return None


_FUNCTIONAL_NARRATIVE_ENTRY_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+\s*[、.．]\s*)?"
    r"(?P<class_name>[^（）()\n]{1,40}?)\s*[（(]\s*(?P<class_code>\d{3})\s*类\s*[）)]\s*"
    r"(?P<section_name>[^（）()\n]{1,60}?)\s*[（(]\s*(?P<section_code>\d{2})\s*款\s*[）)]\s*"
    r"(?P<item_name>[^（）()\n]{1,80}?)\s*[（(]\s*(?P<item_code>\d{2})\s*项\s*[）)]",
    re.M,
)


def _format_functional_code(code: str) -> str:
    if len(code) == 3:
        return code
    if len(code) == 5:
        return f"{code[:3]}-{code[3:5]}"
    if len(code) == 7:
        return f"{code[:3]}-{code[3:5]}-{code[5:7]}"
    return code


def _normalize_functional_name(name: str) -> str:
    normalized = normalize_text(name or "")
    for suffix in ("预算支出", "支出", "预算"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _functional_name_matches(table_name: str, narrative_name: str) -> bool:
    left = _normalize_functional_name(table_name)
    right = _normalize_functional_name(narrative_name)
    return bool(left and right and left == right)


def _extract_t5_functional_name_index(
    rows: Sequence[Sequence[Any]],
) -> Dict[str, Dict[str, str]]:
    entries: Dict[str, Dict[str, str]] = {}
    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) < 4:
            continue
        class_code = cells[0]
        section_code = cells[1]
        item_code = cells[2]
        name = cells[3]
        if not re.fullmatch(r"\d{3}", class_code) or not name:
            continue

        class_display = _format_functional_code(class_code)
        entries.setdefault(
            class_code,
            {
                "name": name,
                "level": "类",
                "code_display": class_display,
                "row_text": f"{class_code} {name}",
            },
        )

        if re.fullmatch(r"\d{2}", section_code):
            section_full_code = f"{class_code}{section_code}"
            section_display = _format_functional_code(section_full_code)
            entries.setdefault(
                section_full_code,
                {
                    "name": name,
                    "level": "款",
                    "code_display": section_display,
                    "row_text": f"{class_code} {section_code} {name}",
                },
            )

            if re.fullmatch(r"\d{2}", item_code):
                item_full_code = f"{section_full_code}{item_code}"
                item_display = _format_functional_code(item_full_code)
                entries[item_full_code] = {
                    "name": name,
                    "level": "项",
                    "code_display": item_display,
                    "row_text": f"{class_code} {section_code} {item_code} {name}",
                }
    return entries


def _candidate_budget_explanation_pages(
    doc: Document, anchors: Dict[str, List[int]]
) -> List[Tuple[int, str]]:
    all_anchor_pages = sorted({page for pages in anchors.values() for page in pages})
    first_table_page = all_anchor_pages[0] if all_anchor_pages else len(doc.page_texts) + 1
    candidates: List[Tuple[int, str]] = []
    for page_num in range(1, min(first_table_page, len(doc.page_texts) + 1)):
        text = doc.page_texts[page_num - 1] or ""
        if not text.strip():
            continue
        if (
            "预算编制说明" in text
            or "财政拨款支出主要内容如下" in text
            or (
                ("类）" in text or "类)" in text)
                and ("款）" in text or "款)" in text)
                and ("项）" in text or "项)" in text)
            )
        ):
            candidates.append((page_num, text))
    return candidates


def _extract_budget_functional_narrative_mentions(
    doc: Document, anchors: Dict[str, List[int]]
) -> Dict[str, List[Dict[str, str]]]:
    mentions: Dict[str, List[Dict[str, str]]] = {}
    for page_num, text in _candidate_budget_explanation_pages(doc, anchors):
        for match in _FUNCTIONAL_NARRATIVE_ENTRY_RE.finditer(text):
            snippet = _snippet(text, match.start(), match.end(), radius=40).replace("\n", " ").strip()
            matched_parts = [
                (match.group("class_code"), match.group("class_name"), "类"),
                (
                    f"{match.group('class_code')}{match.group('section_code')}",
                    match.group("section_name"),
                    "款",
                ),
                (
                    f"{match.group('class_code')}{match.group('section_code')}{match.group('item_code')}",
                    match.group("item_name"),
                    "项",
                ),
            ]
            for code, raw_name, level in matched_parts:
                name = str(raw_name or "").strip(" 　：:；;，,。.")
                if not code or not name:
                    continue
                bucket = mentions.setdefault(code, [])
                if any(
                    item.get("page") == str(page_num)
                    and normalize_text(item.get("name", "")) == normalize_text(name)
                    for item in bucket
                ):
                    continue
                bucket.append(
                    {
                        "page": str(page_num),
                        "name": name,
                        "level": level,
                        "snippet": snippet,
                    }
                )
    return mentions


_BUDGET_ITEM_START_RE = re.compile(r"(?:(?<=\n)|^|\s{2,})(?P<marker>\d+\u3001)")
_BUDGET_DELTA_PERCENT_RE = re.compile(
    r"(?P<prev_year>20\d{2})\u5e74(?:\u5f53\u5e74)?\s*(?:\u9884\u7b97\u6267\u884c\u6570|\u9884\u7b97\u6570|\u51b3\u7b97\u6570)?\s*\u4e3a"
    r"\s*(?P<prev>[0-9][0-9,]*(?:\.[0-9]+)?)\u4e07\u5143"
    r"[\s\S]{0,120}?"
    r"(?P<curr_year>20\d{2})\u5e74(?:\u9884\u7b97\u5b89\u6392|\u9884\u7b97\u6570|\u51b3\u7b97\u6570)"
    r"[^0-9]{0,8}\s*(?P<curr>[0-9][0-9,]*(?:\.[0-9]+)?)\u4e07\u5143"
    r"[\s\S]{0,120}?"
    r"\u6bd4(?P=prev_year)\u5e74(?:\u5f53\u5e74)?\s*(?:\u9884\u7b97\u6267\u884c\u6570|\u9884\u7b97\u6570|\u51b3\u7b97\u6570)?\s*"
    r"(?P<direction>\u589e\u52a0|\u51cf\u5c11)(?P<pct>[0-9][0-9,]*(?:\.[0-9]+)?)%",
    re.S,
)
_BUDGET_ITEM_OPENING_AMOUNT_RE = re.compile(
    r"(?:^|\s)\d+\u3001[\s\S]{0,160}?(?P<front>[0-9][0-9,]*(?:\.[0-9]+)?)\u4e07\u5143",
    re.S,
)
_BUDGET_ITEM_ARRANGED_AMOUNT_RE = re.compile(
    r"20\d{2}\u5e74\u9884\u7b97\u5b89\u6392(?P<budget>[0-9][0-9,]*(?:\.[0-9]+)?)\u4e07\u5143"
)


def _iter_budget_explanation_item_segments(
    doc: Document, anchors: Dict[str, List[int]]
) -> List[Tuple[int, str]]:
    segments: List[Tuple[int, str]] = []
    for page_num, text in _candidate_budget_explanation_pages(doc, anchors):
        matches = list(_BUDGET_ITEM_START_RE.finditer(text))
        if not matches:
            continue
        for idx, match in enumerate(matches):
            start = match.start("marker")
            end = matches[idx + 1].start("marker") if idx + 1 < len(matches) else len(text)
            segment = text[start:end].strip()
            if segment:
                segments.append((page_num, segment))
    return segments


def _budget_segment_subject(segment: str) -> str:
    head = re.split(r"[\u3002\uff1b;\n]", segment, maxsplit=1)[0].strip()
    head = re.sub(r"^\d+\u3001", "", head).strip()
    return head[:80] if len(head) > 80 else head


def _infer_budget_scope(doc: Document) -> Optional[str]:
    path_text = str(getattr(doc, "path", "") or "")
    head_text = "\n".join(doc.page_texts[:3])
    if "\u5355\u4f4d\u9884\u7b97" in path_text or "\u5355\u4f4d\u9884\u7b97" in head_text:
        return "unit"
    if "\u90e8\u95e8\u9884\u7b97" in path_text or "\u90e8\u95e8\u9884\u7b97" in head_text:
        return "department"
    return None


class BUD001_StructureAndAnchors(Rule):
    code, severity = "BUD-001", "error"
    desc = "\u9884\u7b97\u4e5d\u5f20\u8868\u5b8c\u6574\u6027\u4e0e\u5fc5\u5907\u7ae0\u8282\u68c0\u67e5"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        anchors = find_budget_anchors(doc)
        doc.anchors = anchors

        for spec in BUDGET_TABLE_SPECS:
            key = spec["key"]
            if not anchors.get(key):
                table_name = _table_display_name(key)
                issues.append(
                    self._issue(
                        f"\u7f3a\u5931\u9884\u7b97\u8868: {table_name} ({key})",
                        {"table": key, "table_name": table_name},
                        severity="error",
                    )
                )

        for spec in BUDGET_TABLE_SPECS:
            key = spec["key"]
            pages = anchors.get(key, [])
            if len(pages) > 2:
                table_name = _table_display_name(key)
                issues.append(
                    self._issue(
                        f"\u8868\u9898\u91cd\u590d\u8fc7\u591a: {table_name} ({key}) @ {pages}",
                        {"table": key, "table_name": table_name, "pages": pages},
                        severity="warn",
                    )
                )

        for section_id, tokens in _SECTION_TOKENS:
            if not any(_contains_all_tokens(text or "", tokens) for text in doc.page_texts):
                issues.append(
                    self._issue(
                        f"\u7f3a\u5931\u5fc5\u8981\u7ae0\u8282: {section_id}",
                        {"section": section_id},
                        severity="warn",
                    )
                )

        return issues


class BUD002_PlaceholderCheck(Rule):
    code, severity = "BUD-002", "warn"
    desc = "\u9884\u7b97\u6587\u672c\u5360\u4f4d\u7b26\u68c0\u6d4b"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        punctuation_pattern = _PLACEHOLDER_PATTERNS[-1]
        for pidx, text in enumerate(doc.page_texts):
            if not text:
                continue
            toc_page = _is_toc_page(text)
            seen_spans: set[Tuple[int, int]] = set()
            page_hits = 0
            for pat in _PLACEHOLDER_PATTERNS:
                for m in pat.finditer(text):
                    span = (m.start(), m.end())
                    if span in seen_spans:
                        continue
                    if pat is punctuation_pattern:
                        line = _line_for_span(text, m.start(), m.end())
                        if toc_page:
                            continue
                        if _is_toc_leader_line(line):
                            continue
                    seen_spans.add(span)
                    snippet = text[max(0, m.start() - 12): m.end() + 20].replace("\n", " ")
                    issues.append(
                        self._issue(
                            f"\u53ef\u80fd\u5b58\u5728\u672a\u586b\u5145\u5185\u5bb9: {m.group(0)}",
                            {"page": pidx + 1, "pos": m.start()},
                            severity="warn",
                            evidence_text=snippet,
                        )
                    )
                    page_hits += 1
                    # Keep signal focused and avoid flooding on heavily broken pages.
                    if page_hits >= 8:
                        break
                if page_hits >= 8:
                    break
        return issues


class BUD003_YearConsistency(Rule):
    code, severity = "BUD-003", "warn"
    desc = "\u9884\u7b97\u5e74\u4efd\u4e00\u81f4\u6027\u68c0\u67e5"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        report_year = _infer_report_year(doc)
        if not report_year:
            raise RuleDeferred(
                self.code,
                detail="未识别到报告年度",
                unresolved_reasons=["未识别到报告年度"],
            )

        strict_year_issue_pages: set[int] = set()
        for pidx, text in enumerate(doc.page_texts):
            if not text:
                continue
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if not _line_contains_budget_table_title(line):
                    continue
                line_years = sorted(set(_extract_year_candidates(line)))
                if not line_years:
                    continue
                wrong_years = [y for y in line_years if y != report_year]
                if not wrong_years:
                    continue
                wrong_year = wrong_years[0]
                pos = text.find(raw_line)
                issues.append(
                    self._issue(
                        f"\u9884\u7b97\u8868\u6807\u9898\u5e74\u4efd\u7591\u4f3c\u9519\u8bef: \u5e94\u4e3a{report_year}\u5e74, \u547d\u4e2d={wrong_year}\u5e74",
                        {"page": pidx + 1, "pos": pos if pos >= 0 else 0},
                        severity="warn",
                        evidence_text=line,
                    )
                )
                strict_year_issue_pages.add(pidx + 1)
                break

        allowed = {report_year, report_year - 1}
        for pidx, text in enumerate(doc.page_texts):
            if not text:
                continue
            if (pidx + 1) in strict_year_issue_pages:
                continue
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if not _is_target_year_line(line):
                    continue
                line_years = sorted(set(_extract_year_candidates(line)))
                if not line_years:
                    continue
                wrong_years = [year for year in line_years if year not in allowed]
                if not wrong_years:
                    continue
                if any(token in line for token in _YEAR_WHITELIST_TOKENS):
                    continue
                pos = text.find(raw_line)
                issues.append(
                    self._issue(
                        f"\u5e74\u4efd\u53ef\u80fd\u4e0d\u4e00\u81f4: \u62a5\u544a\u5e74={report_year}, \u547d\u4e2d={wrong_years[0]}",
                        {"page": pidx + 1, "pos": pos if pos >= 0 else 0},
                        severity="warn",
                        evidence_text=line,
                    )
                )
                break
        return issues

class BUD101_T1Balance(Rule):
    code, severity = "BUD-101", "error"
    desc = "T1 收入总计 = 支出总计"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T1")
        if not rows:
            raise RuleDeferred(
                self.code,
                detail="未找到部门收支总表(T1)表格数据",
                unresolved_reasons=["未找到部门收支总表(T1)表格数据"],
            )

        unit = (doc.units_per_page[page - 1] if page and page <= len(doc.units_per_page) else None) or doc.dominant_unit
        if not unit:
            raise RuleDeferred(
                self.code,
                detail=f"部门收支总表(第{page}页)金额单位未知，无法可靠核验",
                unresolved_reasons=[f"部门收支总表(第{page}页)金额单位未知，无法可靠核验"],
            )

        v_inc, v_exp = _extract_t1_strict(rows, default_unit=unit)
        if v_inc is None or v_exp is None or not (v_inc.is_numeric and v_exp.is_numeric):
            missing = []
            if v_inc is None or not v_inc.is_numeric:
                missing.append("收入总计")
            if v_exp is None or not v_exp.is_numeric:
                missing.append("支出总计")
            raise RuleDeferred(
                self.code,
                detail=f"T1未找到{','.join(missing)}数值",
                unresolved_reasons=[f"T1未找到{','.join(missing)}数值"],
            )

        envelope = compute_dynamic_envelope([v_inc, v_exp])
        inc_d = v_inc.decimal_val or Decimal("0")
        exp_d = v_exp.decimal_val or Decimal("0")
        diff = abs(inc_d - exp_d)
        if diff > envelope:
            max_scale = max(v_inc.scale_digits, v_exp.scale_digits, 2)
            return [
                self._issue(
                    f"T1收支总计不一致: 收入={inc_d:.{max_scale}f}, 支出={exp_d:.{max_scale}f} (差额={diff:.{max_scale}f})",
                    {"page": page or 1, "table": "BUD_T1"},
                    severity="error",
                )
            ]
        return []


class BUD102_T3TotalFormula(Rule):
    code, severity = "BUD-102", "error"
    desc = "T3 合计 = 基本支出 + 项目支出"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T3")
        if not rows:
            raise RuleDeferred(
                self.code,
                detail="未找到支出预算总表(T3)表格数据",
                unresolved_reasons=["未找到支出预算总表(T3)表格数据"],
            )

        unit = (doc.units_per_page[page - 1] if page and page <= len(doc.units_per_page) else None) or doc.dominant_unit
        if not unit:
            raise RuleDeferred(
                self.code,
                detail=f"支出预算总表(第{page}页)金额单位未知，无法可靠核验",
                unresolved_reasons=[f"支出预算总表(第{page}页)金额单位未知，无法可靠核验"],
            )

        v_total, v_basic, v_project = _extract_t3_strict(rows, default_unit=unit)
        if v_total is None or v_basic is None or v_project is None or not (v_total.is_numeric and v_basic.is_numeric and v_project.is_numeric):
            missing = []
            if v_total is None or not v_total.is_numeric:
                missing.append("合计")
            if v_basic is None or not v_basic.is_numeric:
                missing.append("基本支出")
            if v_project is None or not v_project.is_numeric:
                missing.append("项目支出")
            raise RuleDeferred(
                self.code,
                detail=f"T3未找到{','.join(missing)}数值",
                unresolved_reasons=[f"T3未找到{','.join(missing)}数值"],
            )

        envelope = compute_dynamic_envelope([v_total, v_basic, v_project])
        calc = (v_basic.decimal_val or Decimal("0")) + (v_project.decimal_val or Decimal("0"))
        tot = v_total.decimal_val or Decimal("0")
        diff = abs(tot - calc)
        if diff > envelope:
            max_scale = max(v_total.scale_digits, v_basic.scale_digits, v_project.scale_digits, 2)
            return [
                self._issue(
                    f"T3勾稽错误: 合计={tot:.{max_scale}f}, 基本+项目={calc:.{max_scale}f} (差额={diff:.{max_scale}f})",
                    {"page": page or 1, "table": "BUD_T3"},
                    severity="error",
                )
            ]
        return []


class BUD103_T8TotalFormula(Rule):
    code, severity = "BUD-103", "error"
    desc = "T8 合计 = 人员经费 + 公用经费"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T8")
        if not rows:
            raise RuleDeferred(
                self.code,
                detail="未找到一般公共预算基本支出表(T8)表格数据",
                unresolved_reasons=["未找到一般公共预算基本支出表(T8)表格数据"],
            )

        unit = (doc.units_per_page[page - 1] if page and page <= len(doc.units_per_page) else None) or doc.dominant_unit
        if not unit:
            raise RuleDeferred(
                self.code,
                detail=f"一般公共预算基本支出表(第{page}页)金额单位未知，无法可靠核验",
                unresolved_reasons=[f"一般公共预算基本支出表(第{page}页)金额单位未知，无法可靠核验"],
            )

        v_total, v_personnel, v_public = _extract_t8_strict(rows, default_unit=unit)
        if v_total is None or v_personnel is None or v_public is None or not (v_total.is_numeric and v_personnel.is_numeric and v_public.is_numeric):
            missing = []
            if v_total is None or not v_total.is_numeric:
                missing.append("合计")
            if v_personnel is None or not v_personnel.is_numeric:
                missing.append("人员经费")
            if v_public is None or not v_public.is_numeric:
                missing.append("公用经费")
            raise RuleDeferred(
                self.code,
                detail=f"T8未找到{','.join(missing)}数值",
                unresolved_reasons=[f"T8未找到{','.join(missing)}数值"],
            )

        envelope = compute_dynamic_envelope([v_total, v_personnel, v_public])
        calc = (v_personnel.decimal_val or Decimal("0")) + (v_public.decimal_val or Decimal("0"))
        tot = v_total.decimal_val or Decimal("0")
        diff = abs(tot - calc)
        if diff > envelope:
            max_scale = max(v_total.scale_digits, v_personnel.scale_digits, v_public.scale_digits, 2)
            return [
                self._issue(
                    f"T8勾稽错误: 合计={tot:.{max_scale}f}, 人员+公用={calc:.{max_scale}f} (差额={diff:.{max_scale}f})",
                    {"page": page or 1, "table": "BUD_T8"},
                    severity="error",
                )
            ]
        return []


class BUD104_T9Formula(Rule):
    code, severity = "BUD-104", "error"
    desc = "T9 三公序列公式检查"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T9", include_continuation=False)
        if not rows:
            raise RuleDeferred(
                self.code,
                detail="未找到T9三公经费预算表数据",
                unresolved_reasons=["未找到T9三公经费预算表数据"],
            )

        strict_v = _extract_t9_strict_values(rows)
        if not strict_v or all(sv.reason == "未在表格中找到有效数据行" for sv in strict_v.values()):
            raise RuleDeferred(
                self.code,
                detail="未能解析T9三公经费预算表数值行",
                unresolved_reasons=["未能解析T9三公经费预算表数值行"],
            )

        issues: List[Issue] = []
        unresolved_reasons: List[str] = []

        # 子公式 1: total == abroad + reception + car_sub
        req1 = ["total", "abroad", "reception", "car_sub"]
        missing1 = [k for k in req1 if not strict_v[k].is_numeric]
        if missing1:
            unresolved_reasons.append(f"公式1(三公合计)缺少输入项: {','.join(missing1)}")
        else:
            tot = strict_v["total"].decimal_val
            ab = strict_v["abroad"].decimal_val
            rec = strict_v["reception"].decimal_val
            cs = strict_v["car_sub"].decimal_val
            if tot is not None and ab is not None and rec is not None and cs is not None:
                rhs = ab + rec + cs
                env1 = compute_dynamic_envelope([strict_v["total"], strict_v["abroad"], strict_v["reception"], strict_v["car_sub"]])
                diff_val = abs(tot - rhs)
                if diff_val > env1:
                    max_scale1 = max(strict_v["total"].scale_digits, strict_v["abroad"].scale_digits, strict_v["reception"].scale_digits, strict_v["car_sub"].scale_digits, 2)
                    issues.append(
                        self._issue(
                            f"T9三公合计不一致: 合计={tot:.{max_scale1}f}, 因公+接待+公车小计={rhs:.{max_scale1}f} (差额={diff_val:.{max_scale1}f})",
                            {"page": page or 1, "table": "BUD_T9"},
                            severity="error",
                        )
                    )

        # 子公式 2: car_sub == car_buy + car_run
        req2 = ["car_sub", "car_buy", "car_run"]
        missing2 = [k for k in req2 if not strict_v[k].is_numeric]
        if missing2:
            unresolved_reasons.append(f"公式2(公车小计)缺少输入项: {','.join(missing2)}")
        else:
            car_sub = strict_v["car_sub"].decimal_val
            cb = strict_v["car_buy"].decimal_val
            cr = strict_v["car_run"].decimal_val
            if car_sub is not None and cb is not None and cr is not None:
                car_calc = cb + cr
                env2 = compute_dynamic_envelope([strict_v["car_sub"], strict_v["car_buy"], strict_v["car_run"]])
                diff_val2 = abs(car_sub - car_calc)
                if diff_val2 > env2:
                    max_scale2 = max(strict_v["car_sub"].scale_digits, strict_v["car_buy"].scale_digits, strict_v["car_run"].scale_digits, 2)
                    issues.append(
                        self._issue(
                            f"T9公车小计不一致: 小计={car_sub:.{max_scale2}f}, 购置+运行={car_calc:.{max_scale2}f} (差额={diff_val2:.{max_scale2}f})",
                            {"page": page or 1, "table": "BUD_T9"},
                            severity="error",
                        )
                    )

        if unresolved_reasons:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved_reasons),
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD105_CrossTableChecks(Rule):
    code, severity = "BUD-105", "error"
    desc = "表间勾稽关系检查"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)

        t1_rows, t1_page = _get_budget_table_rows(doc, anchors, "BUD_T1")
        t4_rows, t4_page = _get_budget_table_rows(doc, anchors, "BUD_T4")
        t3_rows, t3_page = _get_budget_table_rows(doc, anchors, "BUD_T3")
        t5_rows, t5_page = _get_budget_table_rows(doc, anchors, "BUD_T5")
        t8_rows, t8_page = _get_budget_table_rows(doc, anchors, "BUD_T8")

        issues: List[Issue] = []
        unresolved_reasons: List[str] = []

        # 检查对 1: T1 ↔ T4 (收入总计、支出总计)
        if t1_rows and t4_rows:
            t1_unit = doc.units_per_page[t1_page - 1] if t1_page and (t1_page - 1) < len(doc.units_per_page) else None
            t4_unit = doc.units_per_page[t4_page - 1] if t4_page and (t4_page - 1) < len(doc.units_per_page) else None
            if not t1_unit:
                unresolved_reasons.append("BUD_T1单位未识别")
            if not t4_unit:
                unresolved_reasons.append("BUD_T4单位未识别")

            if t1_unit and t4_unit:
                t1_inc_sv, t1_exp_sv = _extract_t1_strict(t1_rows, default_unit=t1_unit)
                t4_inc_sv, t4_exp_sv = _extract_t4_strict(t4_rows, default_unit=t4_unit)

                if t1_inc_sv and t4_inc_sv and t1_inc_sv.is_numeric and t4_inc_sv.is_numeric:
                    env = compute_dynamic_envelope([t1_inc_sv, t4_inc_sv])
                    diff_level, diff_val = classify_amount_diff(t1_inc_sv.decimal_val, t4_inc_sv.decimal_val, 1, envelope=env)
                    if diff_level == "mismatch":
                        max_scale = max(t1_inc_sv.scale_digits, t4_inc_sv.scale_digits, 2)
                        location = _make_cross_table_location(
                            _make_location_ref(
                                role="T1",
                                page=t1_page,
                                table="BUD_T1",
                                row="收入总计",
                                field="收入总计",
                                value=float(t1_inc_sv.decimal_val),
                            ),
                            _make_location_ref(
                                role="T4",
                                page=t4_page,
                                table="BUD_T4",
                                row="收入总计",
                                field="收入总计",
                                value=float(t4_inc_sv.decimal_val),
                            ),
                            field="收入总计",
                            row="收入总计",
                        )
                        issues.append(
                            self._issue(
                                f"T1与T4收入总计不一致: T1={t1_inc_sv.decimal_val:.{max_scale}f}, T4={t4_inc_sv.decimal_val:.{max_scale}f} (差额={diff_val:.{max_scale}f})",
                                location,
                                severity="error",
                            )
                        )
                else:
                    unresolved_reasons.append("T1与T4收入总计数值缺失")

                if t1_exp_sv and t4_exp_sv and t1_exp_sv.is_numeric and t4_exp_sv.is_numeric:
                    env = compute_dynamic_envelope([t1_exp_sv, t4_exp_sv])
                    diff_level_e, diff_val_e = classify_amount_diff(t1_exp_sv.decimal_val, t4_exp_sv.decimal_val, 1, envelope=env)
                    if diff_level_e == "mismatch":
                        max_scale = max(t1_exp_sv.scale_digits, t4_exp_sv.scale_digits, 2)
                        location = _make_cross_table_location(
                            _make_location_ref(
                                role="T1",
                                page=t1_page,
                                table="BUD_T1",
                                row="支出总计",
                                field="支出总计",
                                value=float(t1_exp_sv.decimal_val),
                            ),
                            _make_location_ref(
                                role="T4",
                                page=t4_page,
                                table="BUD_T4",
                                row="支出总计",
                                field="支出总计",
                                value=float(t4_exp_sv.decimal_val),
                            ),
                            field="支出总计",
                            row="支出总计",
                        )
                        issues.append(
                            self._issue(
                                f"T1与T4支出总计不一致: T1={t1_exp_sv.decimal_val:.{max_scale}f}, T4={t4_exp_sv.decimal_val:.{max_scale}f} (差额={diff_val_e:.{max_scale}f})",
                                location,
                                severity="error",
                            )
                        )
                else:
                    unresolved_reasons.append("T1与T4支出总计数值缺失")
        else:
            unresolved_reasons.append("BUD_T1或BUD_T4表格缺失，无法核验收支总计表间勾稽")

        # 检查对 2: T3 ↔ T5 (合计、基本支出、项目支出)
        if t3_rows and t5_rows:
            t3_unit = doc.units_per_page[t3_page - 1] if t3_page and (t3_page - 1) < len(doc.units_per_page) else None
            t5_unit = doc.units_per_page[t5_page - 1] if t5_page and (t5_page - 1) < len(doc.units_per_page) else None
            if not t3_unit:
                unresolved_reasons.append("BUD_T3单位未识别")
            if not t5_unit:
                unresolved_reasons.append("BUD_T5单位未识别")

            if t3_unit and t5_unit:
                t3_tot_sv, t3_bas_sv, t3_prj_sv = _extract_total_basic_project_strict(t3_rows, default_unit=t3_unit)
                t5_tot_sv, t5_bas_sv, t5_prj_sv = _extract_total_basic_project_strict(t5_rows, default_unit=t5_unit)

                for (name, sv3, sv5) in [("合计", t3_tot_sv, t5_tot_sv), ("基本支出", t3_bas_sv, t5_bas_sv), ("项目支出", t3_prj_sv, t5_prj_sv)]:
                    if sv3 and sv5 and sv3.is_numeric and sv5.is_numeric:
                        env = compute_dynamic_envelope([sv3, sv5])
                        diff_l, diff_v = classify_amount_diff(sv3.decimal_val, sv5.decimal_val, 1, envelope=env)
                        if diff_l == "mismatch":
                            max_scale = max(sv3.scale_digits, sv5.scale_digits, 2)
                            location = _make_cross_table_location(
                                _make_location_ref(
                                    role="T3",
                                    page=t3_page,
                                    table="BUD_T3",
                                    row="合计",
                                    field=name,
                                    value=float(sv3.decimal_val),
                                ),
                                _make_location_ref(
                                    role="T5",
                                    page=t5_page,
                                    table="BUD_T5",
                                    row="合计",
                                    field=name,
                                    value=float(sv5.decimal_val),
                                ),
                                field=name,
                                row="合计",
                            )
                            issues.append(
                                self._issue(
                                    f"T3与T5{name}不一致: T3={sv3.decimal_val:.{max_scale}f}, T5={sv5.decimal_val:.{max_scale}f} (差额={diff_v:.{max_scale}f})",
                                    location,
                                    severity="error",
                                )
                            )
                    else:
                        unresolved_reasons.append(f"T3与T5{name}数值缺失")
        else:
            unresolved_reasons.append("BUD_T3或BUD_T5表格缺失，无法核验T3↔T5勾稽")

        # 检查对 3: T3 ↔ T8 (T3基本支出 ↔ T8合计)
        if t3_rows and t8_rows:
            t3_unit = doc.units_per_page[t3_page - 1] if t3_page and (t3_page - 1) < len(doc.units_per_page) else None
            t8_unit = doc.units_per_page[t8_page - 1] if t8_page and (t8_page - 1) < len(doc.units_per_page) else None
            if not t3_unit:
                unresolved_reasons.append("BUD_T3单位未识别")
            if not t8_unit:
                unresolved_reasons.append("BUD_T8单位未识别")

            if t3_unit and t8_unit:
                _, t3_bas_sv, _ = _extract_total_basic_project_strict(t3_rows, default_unit=t3_unit)
                t8_tot_sv, _, _ = _extract_t8_strict(t8_rows, default_unit=t8_unit)

                if t3_bas_sv and t8_tot_sv and t3_bas_sv.is_numeric and t8_tot_sv.is_numeric:
                    env = compute_dynamic_envelope([t3_bas_sv, t8_tot_sv])
                    diff_l8, diff_v8 = classify_amount_diff(t3_bas_sv.decimal_val, t8_tot_sv.decimal_val, 1, envelope=env)
                    if diff_l8 == "mismatch":
                        max_scale = max(t3_bas_sv.scale_digits, t8_tot_sv.scale_digits, 2)
                        location = _make_cross_table_location(
                            _make_location_ref(
                                role="T3",
                                page=t3_page,
                                table="BUD_T3",
                                row="合计",
                                field="基本支出",
                                value=float(t3_bas_sv.decimal_val),
                            ),
                            _make_location_ref(
                                role="T8",
                                page=t8_page,
                                table="BUD_T8",
                                row="合计",
                                field="合计",
                                value=float(t8_tot_sv.decimal_val),
                            ),
                            field="基本支出 / 合计",
                            row="合计",
                        )
                        issues.append(
                            self._issue(
                                f"T3基本支出与T8合计不一致: T3={t3_bas_sv.decimal_val:.{max_scale}f}, T8={t8_tot_sv.decimal_val:.{max_scale}f} (差额={diff_v8:.{max_scale}f})",
                                location,
                                severity="error",
                            )
                        )
                else:
                    unresolved_reasons.append("T3基本支出或T8合计数值缺失")
        else:
            unresolved_reasons.append("BUD_T3或BUD_T8表格缺失，无法核验T3基本支出↔T8合计勾稽")

        if unresolved_reasons:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved_reasons),
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD106_EmptyTableStatement(Rule):
    code, severity = "BUD-106", "error"
    desc = "T6/T7/T9\u7a7a\u8868\u8bf4\u660e\u68c0\u67e5"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved_reasons: List[str] = []

        checks = ("BUD_T6", "BUD_T7", "BUD_T9")
        checked_count = 0
        for table_key in checks:
            rows, page = _get_budget_table_rows(doc, anchors, table_key, include_continuation=False)
            if not page:
                unresolved_reasons.append(f"{table_key}表格未定位到")
                continue

            checked_count += 1
            page_text = doc.page_texts[page - 1] if page - 1 < len(doc.page_texts) else ""
            table_name = _table_display_name(table_key)
            expected_phrase = _EMPTY_TABLE_EXPECTED_PHRASES.get(table_key, "")
            keywords = _EMPTY_TABLE_NOTE_KEYWORDS.get(table_key, ())

            has_empty_note = _has_structured_empty_note(page_text, table_key)
            has_expected_phrase = bool(expected_phrase and expected_phrase in page_text)
            has_table_keywords = any(token in page_text for token in keywords)
            wrong_phrase = _find_foreign_empty_phrase(page_text, table_key)
            is_empty = _table_is_effectively_empty(rows or [])

            if wrong_phrase:
                table_name = _table_display_name(table_key)
                issues.append(
                    self._issue(
                        f"{table_name} ({table_key})\u7a7a\u8868\u6ce8\u91ca\u7591\u4f3c\u5957\u6a21\u677f\uff1a\u5f53\u524d\u9875\u9762\u51fa\u73b0\u300c{wrong_phrase}\u300d\uff0c\u5efa\u8bae\u6539\u4e3a\u300c{expected_phrase}\u300d\u6216\u540c\u4e49\u89c4\u8303\u8bf4\u6cd5",
                        {"page": page, "table": table_key, "table_name": table_name},
                        severity="error",
                    )
                )
                continue

            if has_empty_note and not has_table_keywords:
                issues.append(
                    self._issue(
                        f"{table_name} ({table_key})\u5df2\u6709\u7a7a\u8868\u6ce8\u91ca\uff0c\u4f46\u7f3a\u5c11\u4e0e\u8be5\u8868\u5bf9\u5e94\u7684\u53e3\u5f84\u5173\u952e\u8bcd\uff08\u5efa\u8bae\u5305\u542b\u300c{expected_phrase}\u300d\u6216\u540c\u4e49\u8868\u8ff0\uff09",
                        {"page": page, "table": table_key, "table_name": table_name},
                        severity="error",
                    )
                )
                continue

            if is_empty and not (has_expected_phrase or has_empty_note):
                issues.append(
                    self._issue(
                        f"{table_name} ({table_key})\u4e3a\u7a7a\u8868\uff0c\u4f46\u7f3a\u5c11\u89c4\u8303\u6ce8\u91ca\u8bf4\u660e\uff08\u5efa\u8bae\u5305\u542b\u300c{expected_phrase}\u300d\u6216\u300c{_EMPTY_TABLE_NOTE_TOKEN}\u300d\uff09",
                        {"page": page, "table": table_key, "table_name": table_name},
                        severity="error",
                    )
                )

        if checked_count == 0:
            raise RuleDeferred(
                self.code,
                detail="T6/T7/T9空表说明检查前置表格均未定位到",
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD107_TextTableConsistency(Rule):
    code, severity = "BUD-107", "warn"
    desc = "表内数字与文字说明一致性"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved_reasons: List[str] = []
        checked_count = 0

        all_text = "\n".join(doc.page_texts)

        t1_rows, t1_page = _get_budget_table_rows(doc, anchors, "BUD_T1")
        t4_rows, t4_page = _get_budget_table_rows(doc, anchors, "BUD_T4")

        if t1_rows and t1_page:
            checked_count += 1
            unit = doc.units_per_page[t1_page - 1] if (t1_page - 1) < len(doc.units_per_page) else None
            if not unit:
                unresolved_reasons.append("T1单位未识别")
            else:
                t1_inc_sv, t1_exp_sv = _extract_t1_strict(t1_rows, default_unit=unit)
                text_inc_sv = _extract_text_strict_by_patterns(
                    all_text,
                    [r"收入预算\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                    default_unit="万元",
                )
                text_exp_sv = _extract_text_strict_by_patterns(
                    all_text,
                    [r"支出预算\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                    default_unit="万元",
                )

                if t1_inc_sv is None or text_inc_sv is None:
                    unresolved_reasons.append("T1收入预算或文本未提取到")
                else:
                    env = compute_dynamic_envelope([t1_inc_sv, text_inc_sv])
                    diff_l, diff_v = classify_amount_diff(text_inc_sv.decimal_val, t1_inc_sv.decimal_val, 1, envelope=env)
                    if diff_l == "mismatch":
                        max_scale = max(t1_inc_sv.scale_digits, text_inc_sv.scale_digits, 2)
                        issues.append(
                            self._issue(
                                f"文本收入预算与T1不一致: 文本={text_inc_sv.decimal_val:.{max_scale}f}万元, 表={t1_inc_sv.decimal_val:.{max_scale}f}万元",
                                {"page": t1_page, "table": "BUD_T1"},
                                severity="warn",
                            )
                        )

                if t1_exp_sv is None or text_exp_sv is None:
                    unresolved_reasons.append("T1支出预算或文本未提取到")
                else:
                    env_e = compute_dynamic_envelope([t1_exp_sv, text_exp_sv])
                    diff_le, diff_ve = classify_amount_diff(text_exp_sv.decimal_val, t1_exp_sv.decimal_val, 1, envelope=env_e)
                    if diff_le == "mismatch":
                        max_scale = max(t1_exp_sv.scale_digits, text_exp_sv.scale_digits, 2)
                        issues.append(
                            self._issue(
                                f"文本支出预算与T1不一致: 文本={text_exp_sv.decimal_val:.{max_scale}f}万元, 表={t1_exp_sv.decimal_val:.{max_scale}f}万元",
                                {"page": t1_page, "table": "BUD_T1"},
                                severity="warn",
                            )
                        )
        else:
            unresolved_reasons.append("BUD_T1表格未定位到")

        if t4_rows and t4_page:
            checked_count += 1
            unit = doc.units_per_page[t4_page - 1] if (t4_page - 1) < len(doc.units_per_page) else None
            if not unit:
                unresolved_reasons.append("T4单位未识别")
            else:
                _, t4_exp_sv = _extract_t4_strict(t4_rows, default_unit=unit)
                text_fin_exp_sv = _extract_text_strict_by_patterns(
                    all_text,
                    [r"财政拨款支出预算\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                    default_unit="万元",
                )
                if t4_exp_sv is None or text_fin_exp_sv is None:
                    unresolved_reasons.append("T4财政拨款支出或文本未提取到")
                else:
                    env = compute_dynamic_envelope([t4_exp_sv, text_fin_exp_sv])
                    diff_l, diff_v = classify_amount_diff(text_fin_exp_sv.decimal_val, t4_exp_sv.decimal_val, 1, envelope=env)
                    if diff_l == "mismatch":
                        max_scale = max(t4_exp_sv.scale_digits, text_fin_exp_sv.scale_digits, 2)
                        issues.append(
                            self._issue(
                                f"文本财政拨款支出预算与T4不一致: 文本={text_fin_exp_sv.decimal_val:.{max_scale}f}万元, 表={t4_exp_sv.decimal_val:.{max_scale}f}万元",
                                {"page": t4_page, "table": "BUD_T4"},
                                severity="warn",
                            )
                        )
        else:
            unresolved_reasons.append("BUD_T4表格未定位到")

        t9_rows, t9_page = _get_budget_table_rows(doc, anchors, "BUD_T9", include_continuation=False)
        if t9_rows and t9_page:
            unit = doc.units_per_page[t9_page - 1] if (t9_page - 1) < len(doc.units_per_page) else None
            if not unit:
                unresolved_reasons.append("BUD_T9单位未识别")
            else:
                t9_strict = _extract_t9_strict_values(t9_rows, default_unit=unit)
                if not t9_strict:
                    unresolved_reasons.append("BUD_T9数值提取失败")
                else:
                    checked_count += 1
                    text_total_sv = _extract_text_strict_by_patterns(
                        all_text,
                        [r"三公”?\s*经费预算数(?:为)?\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                        default_unit="万元",
                    )
                    text_abroad_sv = _extract_text_strict_by_patterns(
                        all_text,
                        [r"因公出国（?境）?费\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                        default_unit="万元",
                    )
                    text_reception_sv = _extract_text_strict_by_patterns(
                        all_text,
                        [r"公务接待费\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                        default_unit="万元",
                    )
                    text_org_run_sv = _extract_text_strict_by_patterns(
                        all_text,
                        [r"机关运行经费预算(?:为)?\s*([0-9,]+(?:\.[0-9]+)?)\s*万元"],
                        default_unit="万元",
                    )

                    comparisons = [
                        ("三公合计", text_total_sv, t9_strict.get("total")),
                        ("因公出国费", text_abroad_sv, t9_strict.get("abroad")),
                        ("公务接待费", text_reception_sv, t9_strict.get("reception")),
                        ("机关运行经费", text_org_run_sv, t9_strict.get("org_run")),
                    ]
                    for label, text_sv, table_sv in comparisons:
                        if text_sv is None or table_sv is None or not text_sv.is_numeric or not table_sv.is_numeric:
                            unresolved_reasons.append(f"T9 {label}文本或表格数值缺失")
                            continue
                        env = compute_dynamic_envelope([text_sv, table_sv])
                        diff_l, diff_v = classify_amount_diff(text_sv.decimal_val, table_sv.decimal_val, 1, envelope=env)
                        if diff_l == "mismatch":
                            max_scale = max(text_sv.scale_digits, table_sv.scale_digits, 2)
                            issues.append(
                                self._issue(
                                    f"文字说明与T9不一致({label}): 文本={text_sv.decimal_val:.{max_scale}f}, 表={table_sv.decimal_val:.{max_scale}f} (差额={diff_v:.{max_scale}f})",
                                    {"page": t9_page, "table": "BUD_T9"},
                                    severity="warn",
                                )
                            )
        else:
            unresolved_reasons.append("BUD_T9表格未定位到")

        if unresolved_reasons:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved_reasons),
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD108_PerformanceTargetConsistency(Rule):
    code, severity = "BUD-108", "warn"
    desc = "\u7ee9\u6548\u76ee\u6807\u8bf4\u660e\u4e0e\u9879\u76ee\u652f\u51fa\u53e3\u5f84\u4e00\u81f4\u6027"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []

        all_text = "\n".join(doc.page_texts)
        _, perf_amount_wy, perf_line = _extract_performance_summary_metrics(all_text)
        if perf_amount_wy is None:
            raise RuleDeferred(
                self.code,
                detail="未提取到绩效目标说明金额",
                unresolved_reasons=["未提取到绩效目标说明金额"],
            )

        t3_rows, t3_page = _get_budget_table_rows(doc, anchors, "BUD_T3")
        if not t3_rows or not t3_page:
            raise RuleDeferred(
                self.code,
                detail="BUD_T3表格缺失，无法核验绩效目标说明与项目支出口径一致性",
                unresolved_reasons=["BUD_T3表格缺失"],
            )

        _, _, t3_project = _extract_total_basic_project(t3_rows)
        unit = doc.units_per_page[t3_page - 1] if (t3_page - 1) < len(doc.units_per_page) else None
        t3_project_wy = _to_wanyuan(t3_project, unit)
        if t3_project_wy is None:
            raise RuleDeferred(
                self.code,
                detail="BUD_T3未能提取到项目支出金额",
                unresolved_reasons=["BUD_T3未能提取到项目支出金额"],
            )

        perf_page = _find_text_page(doc, perf_line)

        # Narrative and table may differ by tiny rounding; tolerate 0.10万元.
        diff_label, diff_val = classify_amount_diff(perf_amount_wy, t3_project_wy, 1)
        if diff_label == "identical" or _is_close(perf_amount_wy, t3_project_wy, abs_tol=0.10, rel_tol=0.005):
            return issues

        location = _make_cross_table_location(
            _make_location_ref(
                role="说明",
                page=perf_page,
                section="绩效目标说明",
                field="涉及项目预算资金",
                value=perf_amount_wy,
            ),
            _make_location_ref(
                role="T3",
                page=t3_page,
                table="BUD_T3",
                row="合计",
                field="项目支出",
                value=t3_project_wy,
            ),
            field="涉及项目预算资金 / 项目支出",
        )

        issues.append(
            self._issue(
                f"绩效目标说明额度与T3项目支出差异较大：说明={perf_amount_wy:.2f}万元，T3={t3_project_wy:.2f}万元；若为不同口径，建议在文本中补充说明",
                location,
                severity="warn",
                evidence_text=perf_line,
            )
        )
        return issues


class BUD109_FunctionalClassificationNameConsistency(Rule):
    code, severity = "BUD-109", "warn"
    desc = "T5功能分类表与预算编制说明类款项名称一致性"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        t5_rows, t5_page = _get_budget_table_rows(doc, anchors, "BUD_T5")
        if not t5_rows:
            raise RuleDeferred(
                self.code,
                detail="BUD_T5表格缺失，无法核验功能分类科目名称一致性",
                unresolved_reasons=["BUD_T5表格缺失"],
            )

        table_entries = _extract_t5_functional_name_index(t5_rows)
        if not table_entries:
            raise RuleDeferred(
                self.code,
                detail="BUD_T5未能提取到功能分类科目行",
                unresolved_reasons=["BUD_T5未能提取到功能分类科目行"],
            )

        narrative_mentions = _extract_budget_functional_narrative_mentions(doc, anchors)
        if not narrative_mentions:
            raise RuleDeferred(
                self.code,
                detail="正文中未提取到功能分类科目名称说明",
                unresolved_reasons=["正文中未提取到功能分类科目名称说明"],
            )

        issues: List[Issue] = []
        table_name = _table_display_name("BUD_T5")
        for code, table_entry in table_entries.items():
            mismatched_mentions = [
                mention
                for mention in narrative_mentions.get(code, [])
                if not _functional_name_matches(table_entry["name"], mention["name"])
            ]
            if not mismatched_mentions:
                continue

            seen_names = set()
            for mention in mismatched_mentions:
                narrative_name = mention["name"]
                dedupe_key = normalize_text(narrative_name)
                if dedupe_key in seen_names:
                    continue
                seen_names.add(dedupe_key)

                raw_page = mention.get("page")
                try:
                    mention_page = int(str(raw_page).strip())
                except Exception:
                    mention_page = 1
                if mention_page <= 0:
                    mention_page = 1
                row_label = table_entry.get("code_display", _format_functional_code(code))
                table_row_page = (
                    _find_text_page_after(doc, table_entry.get("row_text", ""), start_page=t5_page or 1)
                    or t5_page
                )
                level = table_entry.get("level", mention.get("level", "类"))
                location = _make_cross_table_location(
                    _make_location_ref(
                        role="说明",
                        page=mention_page,
                        section="预算编制说明",
                        row=row_label,
                        field=f"{level}级名称",
                        code=code,
                        subject=narrative_name,
                    ),
                    _make_location_ref(
                        role="T5",
                        page=table_row_page,
                        table=table_name,
                        row=row_label,
                        field="功能分类科目名称",
                        code=code,
                        subject=table_entry["name"],
                    ),
                    row=row_label,
                    field="功能分类科目名称",
                )
                location.update(
                    {
                        "expected_name": table_entry["name"],
                        "actual_name": narrative_name,
                        "code_level": level,
                        "source_of_truth": "BUD_T5",
                    }
                )
                evidence_text = (
                    f"编码：{row_label}\n"
                    f"表格名称：{table_entry['name']}\n"
                    f"说明名称：{narrative_name}\n"
                    f"说明片段：{mention.get('snippet', '')}"
                )
                issues.append(
                    self._issue(
                        f"预算编制说明{level}级科目名称与T5不一致（{row_label}）："
                        f"表格“{table_entry['name']}”，说明“{narrative_name}”",
                        location,
                        severity="warn",
                        evidence_text=evidence_text,
                    )
                )
        return issues


class BUD110_DetailRowFormulaConsistency(Rule):
    code, severity = "BUD-110", "error"
    desc = "预算支出表明细行勾稽检查"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved_reasons: List[str] = []

        checked_tables = 0
        for table_key in ("BUD_T3", "BUD_T5"):
            rows, page = _get_budget_table_rows(doc, anchors, table_key)
            if not rows or not page:
                unresolved_reasons.append(f"{table_key}表格缺失")
                continue

            checked_tables += 1
            for entry in _extract_budget_formula_rows(rows):
                total = entry["total"]
                basic = entry["basic"]
                project = entry["project"]
                calc = basic + project
                diff_label, diff_val = classify_amount_diff(total, calc, 2)
                if diff_label != "mismatch":
                    continue

                row_page = _find_text_page_after(doc, entry.get("row_text", ""), start_page=page) or page
                issues.append(
                    self._issue(
                        f"{table_key}明细行勾稽错误（{entry['row_label']}）: "
                        f"合计={total:.2f}, 基本+项目={calc:.2f}",
                        {
                            "page": row_page,
                            "table": table_key,
                            "row": entry["row_label"],
                            "field": "合计 / 基本支出 / 项目支出",
                        },
                        severity="error",
                        evidence_text=entry["row_text"],
                    )
                )

        if checked_tables == 0:
            raise RuleDeferred(
                self.code,
                detail="BUD_T3与BUD_T5表格均缺失，无法进行明细行勾稽检查",
                unresolved_reasons=unresolved_reasons,
            )
        if unresolved_reasons:
            raise RuleDeferred(
                self.code,
                detail=f"部分表格缺失: {', '.join(unresolved_reasons)}",
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD111_ComparativePercentConsistency(Rule):
    code, severity = "BUD-111", "warn"
    desc = "预算编制说明同比百分比复算"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved_reasons: List[str] = []
        checked_items: int = 0  # M-3: count iterated segments

        for page_num, segment in _iter_budget_explanation_item_segments(doc, anchors):
            checked_items += 1  # M-3: track iteration count
            for match in _BUDGET_DELTA_PERCENT_RE.finditer(segment):
                prev = parse_number(match.group("prev"))
                curr = parse_number(match.group("curr"))
                reported_pct = parse_number(match.group("pct"))
                reported_direction = match.group("direction")
                if prev is None or curr is None or reported_pct is None or prev <= 0:
                    unresolved_reasons.append(f"第{page_num}页同比数值缺失或基期为0")
                    continue
                if max(prev, curr) < 1.0:
                    continue

                amount_diff = curr - prev
                if abs(amount_diff) <= 0.05:
                    continue

                expected_direction = "增加" if amount_diff > 0 else "减少"
                expected_pct = abs(amount_diff) / prev * 100.0
                direction_ok = reported_direction == expected_direction
                pct_ok = _is_close(reported_pct, expected_pct, abs_tol=0.5, rel_tol=0.01)
                if direction_ok and pct_ok:
                    continue

                subject = _budget_segment_subject(segment)
                severity = "error" if (not direction_ok) or (abs(reported_pct - expected_pct) > 1.0) else "warn"
                issues.append(
                    self._issue(
                        f"同比表述与金额不一致（{subject}）："
                        f"按金额应为{expected_direction}{expected_pct:.2f}%，"
                        f"当前写为{reported_direction}{reported_pct:.2f}%",
                        {"page": page_num, "section": "预算编制说明", "subject": subject},
                        severity=severity,
                        evidence_text=segment.replace("\n", " "),
                    )
                )

        if checked_items > 0 and unresolved_reasons:  # M-3: only defer if items were found
            raise RuleDeferred(
                self.code,
                detail=f"部分同比百分比数值未完成核验: {', '.join(unresolved_reasons[:5])}",
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD112_ItemAmountConsistency(Rule):
    code, severity = "BUD-112", "warn"
    desc = "同条预算说明前后金额一致性"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []
        unresolved_reasons: List[str] = []
        checked_items: int = 0  # M-3: count iterated segments

        for page_num, segment in _iter_budget_explanation_item_segments(doc, anchors):
            checked_items += 1  # M-3: track iteration count
            front_match = _BUDGET_ITEM_OPENING_AMOUNT_RE.search(segment)
            arranged_match = _BUDGET_ITEM_ARRANGED_AMOUNT_RE.search(segment)
            if not front_match or not arranged_match:
                continue

            front_amount = parse_number(front_match.group("front"))
            arranged_amount = parse_number(arranged_match.group("budget"))
            if front_amount is None or arranged_amount is None:
                unresolved_reasons.append(f"第{page_num}页预算条目金额解析失败")
                continue
            diff_label, diff_val = classify_amount_diff(front_amount, arranged_amount, 1)
            if diff_label != "mismatch":
                continue

            subject = _budget_segment_subject(segment)
            issues.append(
                self._issue(
                    f"同条预算说明前后金额不一致（{subject}）："
                    f"条目开头={front_amount:.2f}万元，"
                    f"“2026年预算安排”={arranged_amount:.2f}万元",
                    {"page": page_num, "section": "预算编制说明", "subject": subject},
                    severity="warn",
                    evidence_text=segment.replace("\n", " "),
                )
            )

        if checked_items > 0 and unresolved_reasons:  # M-3: only defer if items were found
            raise RuleDeferred(
                self.code,
                detail=f"部分预算条目金额未能解析: {', '.join(unresolved_reasons[:5])}",
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class BUD113_DocumentScopeTerminology(Rule):
    code, severity = "BUD-113", "warn"
    desc = "部门/单位预算文种表述一致性"

    def apply(self, doc: Document) -> List[Issue]:
        scope = _infer_budget_scope(doc)
        if not scope:
            raise RuleDeferred(
                self.code,
                detail="未识别到预算文种口径（部门预算/单位预算）",
                unresolved_reasons=["未识别到预算文种口径"],
            )

        if scope == "unit":
            pattern = re.compile(r"(?:20\d{2}\u5e74)?\u90e8\u95e8\u9884\u7b97\u5b89\u6392")
            current_scope = "\u5355\u4f4d\u9884\u7b97"
            expected = "\u5355\u4f4d\u9884\u7b97\u5b89\u6392"
            wrong_label = "\u90e8\u95e8\u9884\u7b97\u5b89\u6392"
        else:
            pattern = re.compile(r"(?:20\d{2}\u5e74)?\u5355\u4f4d\u9884\u7b97\u5b89\u6392")
            current_scope = "\u90e8\u95e8\u9884\u7b97"
            expected = "\u90e8\u95e8\u9884\u7b97\u5b89\u6392"
            wrong_label = "\u5355\u4f4d\u9884\u7b97\u5b89\u6392"

        issues: List[Issue] = []
        for page_num, text in enumerate(doc.page_texts, start=1):
            for match in pattern.finditer(text or ""):
                issues.append(
                    self._issue(
                        f"\u5f53\u524d\u6750\u6599\u4e3a{current_scope}\uff0c\u4f46\u6b63\u6587\u51fa\u73b0\u201c{wrong_label}\u201d\u8868\u8ff0\uff0c"
                        f"\u5efa\u8bae\u7edf\u4e00\u4e3a\u201c{expected}\u201d",
                        {"page": page_num, "section": "\u5176\u4ed6\u76f8\u5173\u8bf4\u660e", "pos": match.start()},
                        severity="warn",
                        evidence_text=_line_for_span(text, match.start(), match.end()),
                    )
                )

        return issues


# ---------- WP4-H：项目绩效阶段金额披露一致性（OBL-PERF-PHASE-AMOUNT） ----------

#: 项目经费情况说明标题：`<项目名>项目经费情况说明` 或裸标题 `项目经费情况说明`。
#: 排除目录行（行内含引导点 …）——目录条目不是正文标题。
_PERF_EXPENSE_HEADING_RE = re.compile(r"^([^\n…]{2,60}?项目经费情况说明)[ \t]*$", re.M)
#: 申报表年度资金两处口径标签（优先「年度资金申请总额」，回退「当年财政拨款」）。
_PERF_FORM_ANNUAL_APPLY_RE = re.compile(r"年度资金申请总额\s*([\d,]+(?:\.\d+)?)")
_PERF_FORM_ANNUAL_ALLOC_RE = re.compile(r"当年财政拨款\s*([\d,]+(?:\.\d+)?)")
_PERF_FORM_TOTAL_RE = re.compile(r"项目资金总额\s*([\d,]+(?:\.\d+)?)")
#: 申报表金额单位声明（建管委/城管执法局样张均为「项目资金（元）」）。
_PERF_FORM_UNIT_RE = re.compile(r"项目资金\s*[（(]\s*(元|万元|亿元)\s*[）)]")
#: 累计/阶段总口径标记——出现即该金额不得与年度金额比较。
_PERF_CUMULATIVE_RE = re.compile(r"总投资|批复|概算|估算|资金总额|累计|分期|分阶段")
#: 「年度安排缺失但有等效说明」的豁免（避免对"不再安排"类材料误报）。
_PERF_NO_ARRANGE_RE = re.compile(
    r"不(?:再|新增)?安排|无(?:新增|预算)?安排|已(?:经)?在上?年(?:度)?安排|资金已落实"
)
#: 口径差异解释标记（PA-2 豁免：义务 basis 要求"不一致时须给出解释"）。
_PERF_EXPLAIN_RE = re.compile(r"原因|因为|由于|口径|不含|另有|分年|分期|结转|追加|调整")
#: 金额 + 显式单位（单位不明不进比较——Case F fail-closed）。
_PERF_MONEY_RE = re.compile(r"(?<![\d.,])([\d,]+(?:\.\d+)?)(万元|亿元|元)(?!\d)")
_PERF_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_PERF_UNIT_TO_WAN = {
    "万元": Decimal("1"),
    "亿元": Decimal("10000"),
    "元": Decimal("0.0001"),
}


def _perf_scale_digits(raw: str) -> int:
    """原始金额文本的小数位数（'22,614.51'→2；'795'→0）——显示舍入包络用。"""
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def _perf_norm_name(name: Optional[str]) -> str:
    """项目身份归一：去空白/换行（申报表单元格换行拆名，如
    「兰溪路-真南路下立交工\\n程」）。"""
    return re.sub(r"\s+", "", str(name or ""))


def _perf_classify_sentence(sentence: str, year: Optional[int]) -> str:
    """年度预算安排小节内单句金额的口径分类。

    累计标记优先（总投资/批复/概算/估算/资金总额等——真值文旅局
    「可研批复总投资为13526.06万元」即此形态）；其次句内显式年份与
    材料年度不同 → 往年（「2025年安排建设资金5000万元」）；材料年度
    未确认时句内年份一律口径待定（fail-closed）；其余归年度（小节
    标题本身就是年度口径，覆盖「795万元」「本年度…349.80万元」等）。
    """
    if _PERF_CUMULATIVE_RE.search(sentence):
        return "cumulative"
    years = [int(value) for value in _PERF_YEAR_TOKEN_RE.findall(sentence)]
    if year is not None:
        if any(value != year for value in years):
            return "prior"
        if years:
            return "annual"
    elif years:
        return "unknown"
    return "annual"


def _perf_expense_sections(
    merged: str, offsets: List[int], doc: Document
) -> List[Dict[str, Any]]:
    """切出正文中的项目经费情况说明章节（目录页同名行排除，与
    _nar_repeat_sections 同款启发式）。

    章节终止于下一个说明标题或「财政项目支出绩效目标申报表」标题——
    最后一个说明章节若放任到文末，会把申报表页整体吸进章节文本，
    表格固定字段（如「上年结转资金」）会误触发解释豁免（实测缺陷）。
    """
    texts = [str(item or "") for item in (getattr(doc, "page_texts", []) or [])]
    toc_pages = {index for index, text in enumerate(texts) if "目录" in text[:120]}
    starts: List[Tuple[str, int]] = []
    for match in _PERF_EXPENSE_HEADING_RE.finditer(merged):
        page = _txt_fund_page_for(offsets, match.start())
        if (page - 1) in toc_pages:
            continue
        starts.append((match.group(1).strip(), match.start()))
    sections: List[Dict[str, Any]] = []
    for idx, (title, start) in enumerate(starts):
        candidates = [len(merged)]
        if idx + 1 < len(starts):
            candidates.append(starts[idx + 1][1])
        form_pos = merged.find("财政项目支出绩效目标申报表", start + 1)
        if form_pos > start:
            candidates.append(form_pos)
        end = min(candidates)
        heading = title[: -len("项目经费情况说明")].strip()
        sections.append(
            {
                "name": heading or None,
                "title": title,
                "start": start,
                "end": end,
                "text": merged[start:end],
            }
        )
    return sections


def _pair_perf_records(
    sections: List[Dict[str, Any]], forms: List[Dict[str, Any]]
) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], List[str]]:
    """项目身份配对（Mutation A 锁定点）。

    - 名称归一相等或互相包含才可比；单说明×单申报表允许无名称兜底
      （城管执法局形态：说明标题不署项目名）。
    - 同一说明命中多张申报表 → 阶段归属歧义，整体不比较。
    - 禁止按位置/顺序配对：跨项目配对会把甲项目的年度金额和乙项目的
      申报表凑成一对，制造跨项目误报。
    """
    if len(sections) == 1 and len(forms) == 1:
        return [(sections[0], forms[0])], []
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    ambiguous: List[str] = []
    used: set = set()
    for section in sections:
        name = _perf_norm_name(section["name"])
        if not name:
            continue
        matches = [
            (idx, form)
            for idx, form in enumerate(forms)
            if idx not in used
            and form.get("name")
            and (name == form["name"] or name in form["name"] or form["name"] in name)
        ]
        if len(matches) > 1:
            ambiguous.append(section["name"] or name)
            continue
        if len(matches) == 1:
            idx, form = matches[0]
            pairs.append((section, form))
            used.add(idx)
    return pairs, ambiguous


class R33PerfPhaseAmount(Rule):
    """项目绩效目标/阶段性目标金额披露一致性（V33-PERF-PHASE-AMOUNT）。

    WP4-H 收口 OBL-PERF-PHASE-AMOUNT（budget only）。两条确定性检查面：

    PA-1 阶段口径错配：项目经费情况说明的「年度预算安排」小节存在金额，
    但全部为累计/往年口径（可研批复总投资、2025 年安排等），本年度金额
    缺失且无等效说明。真值（REAL）：文旅局 2026 部门预算 P27 真如海心
    剧院精装修工程——只有"可研批复总投资 13526.06 万元（=11668.67+
    1213.29+644.10，内部自洽）+ 2025 年安排建设资金 5000 万元"，2026 年
    度金额整体缺失，历史系统漏报。

    PA-2 同项目年度金额不一致：项目经费情况说明「年度预算安排」金额 ↔
    同项目「财政项目支出绩效目标申报表」年度资金申请总额/当年财政拨款，
    单位归一（元→万元）后超显示舍入包络且未解释 → finding。负例锚
    （REAL）：建管委「兰溪路-真南路下立交工程」22,614.51 万元 ↔
    226,145,100.00 元一致；城管执法局「拆违经费」349.80 万元 ↔
    3,498,000.00 元一致。

    阶段身份红线（义务 basis"不得直接判等"的具体化）：
    - 申报表「项目资金总额」在项目性质=阶段性项目时是跨年累计口径，
      禁止与年度金额比较（Case D）；
    - 同项目多张申报表（一期/二期等）阶段归属不可确认时不比较（Case C）；
    - 项目身份（名称归一）无法确认、单位未声明时不比较（Case F），
      一律 fail-closed 记 insufficient_data，不产出确定性结论。
    聚合面（绩效涉及资金 vs 表内项目支出）归 BUD-108，本规则不重复。
    """

    code, severity = "V33-PERF-PHASE-AMOUNT", "medium"
    desc = "项目绩效阶段金额披露一致性（说明年度安排↔绩效申报表年度资金）"

    def apply(self, doc: Document) -> List[Issue]:
        merged, offsets = _txt_fund_merged_pages(doc)
        if not merged.strip():
            raise RuleDeferred(
                self.code,
                "未提取到正文文本，绩效阶段金额一致性无从检查",
                unresolved_reasons=["未提取到正文文本"],
            )

        sections = _perf_expense_sections(merged, offsets, doc)
        forms = self._collect_forms(doc)
        if not sections and not forms:
            # 两种披露形态都不存在：检查对象整体缺席，是不适用而非数据不足。
            raise RuleNotApplicable(
                self.code,
                detail="未找到项目经费情况说明或绩效目标申报表，绩效阶段金额检查不适用",
            )

        heading_pages = tuple(
            _txt_fund_page_for(offsets, section["start"]) for section in sections
        )
        year = _resolve_fiscal_year(doc, heading_pages)
        issues: List[Issue] = []
        unresolved: List[str] = []
        if year is None:
            unresolved.append(
                "未能确认材料财政年度，年度/往年/累计口径不可区分（parse_ambiguity）"
            )

        annual_amounts: Dict[int, Dict[str, Any]] = {}
        for idx, section in enumerate(sections):
            findings, annual, section_unresolved = self._check_narrative_side(
                section, year, offsets
            )
            issues.extend(findings)
            if annual is not None:
                annual_amounts[idx] = annual
            unresolved.extend(section_unresolved)

        pairs, ambiguous = _pair_perf_records(sections, forms)
        for name in ambiguous:
            unresolved.append(
                f"项目「{name}」存在多张绩效目标申报表（一期/二期等阶段归属"
                "不可确认），不做阶段金额比较"
            )
        for section, form in pairs:
            annual = None
            for idx, section_item in enumerate(sections):
                if section_item is section:
                    annual = annual_amounts.get(idx)
                    break
            self._compare_annual(
                section, form, annual, year, offsets, issues, unresolved
            )

        # 未配对成功的说明侧：单侧披露无比较面，不产结论也不算缺口——
        # 多项目材料只公开重点项目说明是常态，不构成 insufficient_data。
        if unresolved:
            reasons = list(dict.fromkeys(unresolved))
            raise RuleDeferred(
                self.code,
                "；".join(reasons),
                partial_issues=issues,
                unresolved_reasons=reasons,
            )
        return issues

    # ------------------------------------------------------------------
    def _check_narrative_side(
        self,
        section: Dict[str, Any],
        year: Optional[int],
        offsets: List[int],
    ) -> Tuple[List[Issue], Optional[Dict[str, Any]], List[str]]:
        """PA-1：年度预算安排小节的口径分类与本年度金额缺失判定。

        返回 (findings, 年度金额描述或 None, unresolved)。
        """
        chunk = self._annual_chunk(section, year)
        if chunk is None or not chunk["amounts"]:
            # 小节缺席或无金额（如"按照财政安排年度预算，按季度拨付"）：
            # 金额一致性无检查面，不在本规则产出结论。
            return [], None, []
        if year is None:
            # 财政年度未确认时金额口径不可分类——fail-closed。
            return [], None, []
        amounts = chunk["amounts"]
        annual = next((item for item in amounts if item["kind"] == "annual"), None)
        if annual is not None:
            return [], annual, []
        if _PERF_NO_ARRANGE_RE.search(chunk["text"]):
            return [], None, []

        disclosed = "、".join(
            f"{item['raw']}{item['unit']}（{item['kind_label']}）" for item in amounts
        )
        page = _txt_fund_page_for(offsets, chunk["start"])
        project = section["name"] or "（未署项目名）"
        issues = [
            self._issue(
                f"「{project}」项目经费情况说明的「年度预算安排」小节未披露"
                f"本年度（{year}）金额，仅见{disclosed}，阶段/累计口径与年度"
                "口径的差异未作说明。请补充本年度安排金额或说明口径。",
                {
                    "page": page,
                    "pos": 0,
                    "section": section["title"],
                    "project": project,
                    "predicate": "annual_amount_missing",
                    "disclosed_amounts": disclosed,
                    "fiscal_year": year,
                    "obligation_id": "OBL-PERF-PHASE-AMOUNT",
                },
                severity="medium",
                evidence_text=f"【章节:{section['title']}】{chunk['text'][:200]}",
                section_id=section["title"],
            )
        ]
        return issues, None, []

    # ------------------------------------------------------------------
    @staticmethod
    def _annual_chunk(
        section: Dict[str, Any], year: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """定位「年度预算安排」小节并抽取金额（逐句口径分类）。"""
        anchor = section["text"].find("年度预算安排")
        if anchor < 0:
            return None
        start = section["start"] + anchor
        rest = section["text"][anchor + len("年度预算安排"):]
        stop = len(rest)
        for marker in ("绩效目标", "详见", "申报表"):
            pos = rest.find(marker)
            if 0 <= pos < stop:
                stop = pos
        body = rest[:stop]
        if not body.strip():
            return None
        amounts: List[Dict[str, Any]] = []
        # 逐句口径分类：句子以。；；换行切分，金额归属其所在句；
        # 绝不跨句拼接数字（WP4-C 教训）。
        sentences = re.split(r"([。；；\n])", body)
        cursor = 0
        for idx in range(0, len(sentences) - 1, 2):
            sentence = sentences[idx]
            offset_in_body = cursor
            cursor += len(sentence) + len(sentences[idx + 1])
            for match in _PERF_MONEY_RE.finditer(sentence):
                raw, unit = match.group(1), match.group(2)
                kind = _perf_classify_sentence(sentence, year)
                kind_label = {
                    "annual": "年度",
                    "cumulative": "累计/阶段总",
                    "prior": "往年",
                    "unknown": "口径待定",
                }[kind]
                amounts.append(
                    {
                        "raw": raw,
                        "unit": unit,
                        "scale": _perf_scale_digits(raw),
                        "kind": kind,
                        "kind_label": kind_label,
                        "value": Decimal(raw.replace(",", ""))
                        * _PERF_UNIT_TO_WAN[unit],
                        "pos": start
                        + len("年度预算安排")
                        + offset_in_body
                        + match.start(),
                    }
                )
        return {"text": body, "amounts": amounts, "start": start}

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_forms(doc: Document) -> List[Dict[str, Any]]:
        """逐页解析「财政项目支出绩效目标申报表」块（表格页文本形态）。"""
        forms: List[Dict[str, Any]] = []
        for pidx, text in enumerate(doc.page_texts or []):
            page_text = str(text or "")
            if "财政项目支出绩效目标申报表" not in page_text:
                continue
            name_match = re.search(r"项目名称\s*\n(.*?)\n项目性质", page_text, re.S)
            nature_match = re.search(r"项目性质\s*\n(.*?)\n项目类别", page_text, re.S)
            unit_match = _PERF_FORM_UNIT_RE.search(page_text)
            annual_match = _PERF_FORM_ANNUAL_APPLY_RE.search(page_text)
            if annual_match is None:
                annual_match = _PERF_FORM_ANNUAL_ALLOC_RE.search(page_text)
            total_match = _PERF_FORM_TOTAL_RE.search(page_text)
            forms.append(
                {
                    "name": _perf_norm_name(name_match.group(1)) if name_match else None,
                    "staged": bool(nature_match and "阶段" in nature_match.group(1)),
                    "page": pidx + 1,
                    "unit": unit_match.group(1) if unit_match else None,
                    "annual_raw": annual_match.group(1) if annual_match else None,
                    "total_raw": total_match.group(1) if total_match else None,
                    "text": page_text,
                }
            )
        return forms

    # ------------------------------------------------------------------
    def _compare_annual(
        self,
        section: Dict[str, Any],
        form: Dict[str, Any],
        annual: Optional[Dict[str, Any]],
        year: Optional[int],
        offsets: List[int],
        issues: List[Issue],
        unresolved: List[str],
    ) -> None:
        """PA-2：同项目说明年度金额 ↔ 申报表年度资金（年度↔年度，禁止跨口径）。"""
        project = section["name"] or form.get("name") or "（未署项目名）"
        if form["annual_raw"] is None:
            if form["staged"] and form["total_raw"] is not None:
                unresolved.append(
                    f"项目「{project}」为阶段性项目，申报表仅披露累计口径"
                    f"项目资金总额（{form['total_raw']}），不得与年度金额直接"
                    "比较，且年度资金未披露"
                )
            else:
                unresolved.append(
                    f"项目「{project}」申报表未披露年度资金申请总额/当年财政"
                    "拨款，年度金额不可比"
                )
            return
        if form["unit"] is None:
            unresolved.append(
                f"项目「{project}」申报表金额单位未声明（缺「项目资金（元）」"
                "类标注），归一比较不可完成"
            )
            return
        if annual is None:
            # 说明侧无年度金额：PA-1 已按口径错配处理（或小节无金额不归
            # 本规则），此处无比较面。
            return
        annual_wan = Decimal(form["annual_raw"].replace(",", "")) * _PERF_UNIT_TO_WAN[
            form["unit"]
        ]
        narrative_wan = annual["value"]
        diff = abs(narrative_wan - annual_wan)
        envelope = compute_dynamic_envelope(
            [
                (annual["scale"], annual["unit"]),
                (_perf_scale_digits(form["annual_raw"]), form["unit"]),
            ]
        )
        if diff <= envelope:
            return
        # 解释豁免只看说明侧散文：申报表固定字段（如「上年结转资金 0」）
        # 是表格标签不是差异解释，不得触发豁免。
        if _PERF_EXPLAIN_RE.search(section["text"]):
            return
        page = _txt_fund_page_for(offsets, annual["pos"])
        diff_display = diff.quantize(Decimal("0.01"))
        issues.append(
            self._issue(
                f"「{project}」绩效阶段金额不一致：项目经费情况说明「年度预算"
                f"安排」{annual['raw']}{annual['unit']}"
                f"（={narrative_wan.quantize(Decimal('0.01'))} 万元）与财政项目"
                f"支出绩效目标申报表年度资金申请总额 {form['annual_raw']}"
                f"{form['unit']}（={annual_wan.quantize(Decimal('0.01'))} 万元）"
                f"相差 {diff_display} 万元，且未说明口径差异。"
                "请核实同项目同年度金额，或补充差异原因说明。",
                {
                    "page": page,
                    "pos": 0,
                    "section": section["title"],
                    "project": project,
                    "phase": "年度（当年）",
                    "amount_a": annual["raw"],
                    "unit_a": annual["unit"],
                    "amount_a_wan": str(narrative_wan.quantize(Decimal("0.01"))),
                    "amount_b": form["annual_raw"],
                    "unit_b": form["unit"],
                    "amount_b_wan": str(annual_wan.quantize(Decimal("0.01"))),
                    "diff_wan": str(diff_display),
                    "form_page": form["page"],
                    "fiscal_year": year,
                    "comparison": "annual_vs_annual",
                    "obligation_id": "OBL-PERF-PHASE-AMOUNT",
                },
                severity="high",
                evidence_text=(
                    f"【章节:{section['title']}】年度预算安排 {annual['raw']}"
                    f"{annual['unit']}；【申报表 p{form['page']}】年度资金申请"
                    f"总额 {form['annual_raw']}{form['unit']}"
                ),
                section_id=section["title"],
            )
        )


ALL_BUDGET_RULES: List[Rule] = [
    BUD001_StructureAndAnchors(),
    BUD002_PlaceholderCheck(),
    BUD003_YearConsistency(),
    BUD101_T1Balance(),
    BUD102_T3TotalFormula(),
    BUD103_T8TotalFormula(),
    BUD104_T9Formula(),
    BUD105_CrossTableChecks(),
    BUD106_EmptyTableStatement(),
    BUD107_TextTableConsistency(),
    BUD108_PerformanceTargetConsistency(),
    BUD109_FunctionalClassificationNameConsistency(),
    BUD110_DetailRowFormulaConsistency(),
    BUD111_ComparativePercentConsistency(),
    BUD112_ItemAmountConsistency(),
    BUD113_DocumentScopeTerminology(),
    # WP4-H：项目绩效阶段金额披露一致性（OBL-PERF-PHASE-AMOUNT，budget only）
    R33PerfPhaseAmount(),
]
