from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rapidfuzz import fuzz

from .rules_v33 import Document, Issue, Rule, normalize_text, parse_number


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


def _extract_t1_totals(rows: Sequence[Sequence[Any]]) -> Tuple[Optional[float], Optional[float]]:
    fallback: Optional[Tuple[float, float]] = None
    for row in rows:
        txt = _row_text(row)
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

        nums = _numbers_in_row(row)
        if len(nums) < 2:
            continue
        if "\u603b\u8ba1" in txt or "\u5408\u8ba1" in txt:
            fallback = (nums[0], nums[-1])
    return fallback if fallback else (None, None)


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


def _extract_t9_values(rows: Sequence[Sequence[Any]]) -> Dict[str, float]:
    headers = _extract_headers(rows, depth=3)
    norm_headers = [normalize_text(h) for h in headers]

    idx_map: Dict[str, int] = {}
    for idx, h in enumerate(norm_headers):
        if "\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97\u6570" in h:
            idx_map["org_run"] = idx
        elif "\u56e0\u516c\u51fa\u56fd\u5883\u8d39" in h:
            idx_map["abroad"] = idx
        elif "\u516c\u52a1\u63a5\u5f85\u8d39" in h:
            idx_map["reception"] = idx
        elif "\u516c\u52a1\u7528\u8f66\u8d2d\u7f6e\u53ca\u8fd0\u884c\u8d39\u5c0f\u8ba1" in h or (
            "\u516c\u52a1\u7528\u8f66\u8d2d\u7f6e\u53ca\u8fd0\u884c\u8d39" in h and "\u5c0f\u8ba1" in h
        ):
            idx_map["car_sub"] = idx
        elif "\u8d2d\u7f6e\u8d39" in h:
            idx_map["car_buy"] = idx
        elif "\u8fd0\u884c\u8d39" in h:
            idx_map["car_run"] = idx
        elif ("\u5408\u8ba1" in h) and ("\u5c0f\u8ba1" not in h):
            idx_map["total"] = idx

    data_row: Optional[Sequence[Any]] = None
    for row in reversed(rows):
        nums = [parse_number(c) for c in row]
        if sum(v is not None for v in nums) >= 2:
            data_row = row
            break

    if not data_row:
        return {}

    def read_value(col_name: str) -> float:
        idx = idx_map.get(col_name)
        if idx is None:
            return 0.0
        if idx >= len(data_row):
            return 0.0
        val = parse_number(data_row[idx])
        return float(val) if val is not None else 0.0

    return {
        "total": read_value("total"),
        "abroad": read_value("abroad"),
        "reception": read_value("reception"),
        "car_sub": read_value("car_sub"),
        "car_buy": read_value("car_buy"),
        "car_run": read_value("car_run"),
        "org_run": read_value("org_run"),
    }


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
            return issues

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
    desc = "T1 \u6536\u5165\u603b\u8ba1 = \u652f\u51fa\u603b\u8ba1"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T1")
        if not rows:
            return []

        income_total, expense_total = _extract_t1_totals(rows)
        if income_total is None or expense_total is None:
            return []

        if _is_close(income_total, expense_total, abs_tol=1.0, rel_tol=0.0005):
            return []

        return [
            self._issue(
                f"T1\u6536\u652f\u603b\u8ba1\u4e0d\u4e00\u81f4: \u6536\u5165={income_total:.2f}, \u652f\u51fa={expense_total:.2f}",
                {"page": page or 1, "table": "BUD_T1"},
                severity="error",
            )
        ]


class BUD102_T3TotalFormula(Rule):
    code, severity = "BUD-102", "error"
    desc = "T3 \u5408\u8ba1 = \u57fa\u672c\u652f\u51fa + \u9879\u76ee\u652f\u51fa"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T3")
        if not rows:
            return []

        total, basic, project = _extract_total_basic_project(rows)
        if total is None or basic is None or project is None:
            return []

        calc = basic + project
        if _is_close(total, calc, abs_tol=1.0, rel_tol=0.0005):
            return []

        return [
            self._issue(
                f"T3\u52fe\u7a3d\u9519\u8bef: \u5408\u8ba1={total:.2f}, \u57fa\u672c+\u9879\u76ee={calc:.2f}",
                {"page": page or 1, "table": "BUD_T3"},
                severity="error",
            )
        ]


class BUD103_T8TotalFormula(Rule):
    code, severity = "BUD-103", "error"
    desc = "T8 \u5408\u8ba1 = \u4eba\u5458\u7ecf\u8d39 + \u516c\u7528\u7ecf\u8d39"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T8")
        if not rows:
            return []

        total, personnel, public = _extract_total_basic_project(rows)
        if total is None or personnel is None or public is None:
            return []

        calc = personnel + public
        if _is_close(total, calc, abs_tol=1.0, rel_tol=0.0005):
            return []

        return [
            self._issue(
                f"T8\u52fe\u7a3d\u9519\u8bef: \u5408\u8ba1={total:.2f}, \u4eba\u5458+\u516c\u7528={calc:.2f}",
                {"page": page or 1, "table": "BUD_T8"},
                severity="error",
            )
        ]


class BUD104_T9Formula(Rule):
    code, severity = "BUD-104", "error"
    desc = "T9 \u4e09\u516c\u5e8f\u5217\u516c\u5f0f\u68c0\u67e5"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        rows, page = _get_budget_table_rows(doc, anchors, "BUD_T9", include_continuation=False)
        if not rows:
            return []

        v = _extract_t9_values(rows)
        if not v:
            return []

        issues: List[Issue] = []
        lhs = v["total"]
        rhs = v["abroad"] + v["reception"] + v["car_sub"]
        if not _is_close(lhs, rhs, abs_tol=0.01, rel_tol=0.0005):
            issues.append(
                self._issue(
                    f"T9\u4e09\u516c\u5408\u8ba1\u4e0d\u4e00\u81f4: \u5408\u8ba1={lhs:.2f}, \u56e0\u516c+\u63a5\u5f85+\u516c\u8f66\u5c0f\u8ba1={rhs:.2f}",
                    {"page": page or 1, "table": "BUD_T9"},
                    severity="error",
                )
            )

        car_sub = v["car_sub"]
        car_calc = v["car_buy"] + v["car_run"]
        if not _is_close(car_sub, car_calc, abs_tol=0.01, rel_tol=0.0005):
            issues.append(
                self._issue(
                    f"T9\u516c\u8f66\u5c0f\u8ba1\u4e0d\u4e00\u81f4: \u5c0f\u8ba1={car_sub:.2f}, \u8d2d\u7f6e+\u8fd0\u884c={car_calc:.2f}",
                    {"page": page or 1, "table": "BUD_T9"},
                    severity="error",
                )
            )

        return issues


class BUD105_CrossTableChecks(Rule):
    code, severity = "BUD-105", "error"
    desc = "\u8868\u95f4\u52fe\u7a3d\u5173\u7cfb\u68c0\u67e5"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)

        t1_rows, t1_page = _get_budget_table_rows(doc, anchors, "BUD_T1")
        t4_rows, t4_page = _get_budget_table_rows(doc, anchors, "BUD_T4")
        t3_rows, t3_page = _get_budget_table_rows(doc, anchors, "BUD_T3")
        t5_rows, t5_page = _get_budget_table_rows(doc, anchors, "BUD_T5")
        t8_rows, t8_page = _get_budget_table_rows(doc, anchors, "BUD_T8")

        issues: List[Issue] = []

        if t1_rows and t4_rows:
            t1_income, t1_expense = _extract_t1_totals(t1_rows)
            t4_income, t4_expense = _extract_t4_totals(t4_rows)

            if (
                t1_income is not None
                and t4_income is not None
                and not _is_close(t1_income, t4_income, abs_tol=1.0, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"T1\u4e0eT4\u6536\u5165\u603b\u8ba1\u4e0d\u4e00\u81f4: T1={t1_income:.2f}, T4={t4_income:.2f}",
                        {"page": t1_page or t4_page or 1, "table": "BUD_T1/BUD_T4"},
                        severity="error",
                    )
                )

            if (
                t1_expense is not None
                and t4_expense is not None
                and not _is_close(t1_expense, t4_expense, abs_tol=1.0, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"T1\u4e0eT4\u652f\u51fa\u603b\u8ba1\u4e0d\u4e00\u81f4: T1={t1_expense:.2f}, T4={t4_expense:.2f}",
                        {"page": t1_page or t4_page or 1, "table": "BUD_T1/BUD_T4"},
                        severity="error",
                    )
                )

        if t3_rows and t5_rows:
            t3_total, t3_basic, t3_project = _extract_total_basic_project(t3_rows)
            t5_total, t5_basic, t5_project = _extract_total_basic_project(t5_rows)

            if (
                t3_total is not None
                and t5_total is not None
                and not _is_close(t3_total, t5_total, abs_tol=1.0, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"T3\u4e0eT5\u5408\u8ba1\u4e0d\u4e00\u81f4: T3={t3_total:.2f}, T5={t5_total:.2f}",
                        {"page": t3_page or t5_page or 1, "table": "BUD_T3/BUD_T5"},
                        severity="error",
                    )
                )

            if (
                t3_basic is not None
                and t5_basic is not None
                and not _is_close(t3_basic, t5_basic, abs_tol=1.0, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"T3\u4e0eT5\u57fa\u672c\u652f\u51fa\u4e0d\u4e00\u81f4: T3={t3_basic:.2f}, T5={t5_basic:.2f}",
                        {"page": t3_page or t5_page or 1, "table": "BUD_T3/BUD_T5"},
                        severity="error",
                    )
                )

            if (
                t3_project is not None
                and t5_project is not None
                and not _is_close(t3_project, t5_project, abs_tol=1.0, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"T3\u4e0eT5\u9879\u76ee\u652f\u51fa\u4e0d\u4e00\u81f4: T3={t3_project:.2f}, T5={t5_project:.2f}",
                        {"page": t3_page or t5_page or 1, "table": "BUD_T3/BUD_T5"},
                        severity="error",
                    )
                )

        if t3_rows and t8_rows:
            _, t3_basic, _ = _extract_total_basic_project(t3_rows)
            t8_total, _, _ = _extract_total_basic_project(t8_rows)
            if (
                t3_basic is not None
                and t8_total is not None
                and not _is_close(t3_basic, t8_total, abs_tol=1.0, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"T3\u57fa\u672c\u652f\u51fa\u4e0eT8\u5408\u8ba1\u4e0d\u4e00\u81f4: T3={t3_basic:.2f}, T8={t8_total:.2f}",
                        {"page": t3_page or t8_page or 1, "table": "BUD_T3/BUD_T8"},
                        severity="error",
                    )
                )

        return issues


class BUD106_EmptyTableStatement(Rule):
    code, severity = "BUD-106", "error"
    desc = "T6/T7/T9\u7a7a\u8868\u8bf4\u660e\u68c0\u67e5"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []

        checks = ("BUD_T6", "BUD_T7", "BUD_T9")
        for table_key in checks:
            rows, page = _get_budget_table_rows(doc, anchors, table_key, include_continuation=False)
            if not page:
                continue

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

        return issues


class BUD107_TextTableConsistency(Rule):
    code, severity = "BUD-107", "warn"
    desc = "\u9884\u7b97\u8bf4\u660e\u4e0e\u8868\u683c\u6570\u503c\u4e00\u81f4\u6027"

    def apply(self, doc: Document) -> List[Issue]:
        anchors = find_budget_anchors(doc)
        issues: List[Issue] = []

        all_text = "\n".join(doc.page_texts)

        t1_rows, t1_page = _get_budget_table_rows(doc, anchors, "BUD_T1")
        t4_rows, t4_page = _get_budget_table_rows(doc, anchors, "BUD_T4")

        if t1_rows and t1_page:
            t1_income, t1_expense = _extract_t1_totals(t1_rows)
            unit = doc.units_per_page[t1_page - 1] if (t1_page - 1) < len(doc.units_per_page) else None
            t1_income_wy = _to_wanyuan(t1_income, unit)
            t1_expense_wy = _to_wanyuan(t1_expense, unit)

            text_income = _extract_number_by_patterns(
                all_text,
                [
                    r"\u6536\u5165\u9884\u7b97\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143",
                ],
            )
            text_expense = _extract_number_by_patterns(
                all_text,
                [
                    r"\u652f\u51fa\u9884\u7b97\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143",
                ],
            )

            if (
                t1_income_wy is not None
                and text_income is not None
                and not _is_close(t1_income_wy, text_income, abs_tol=0.05, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"\u6587\u672c\u6536\u5165\u9884\u7b97\u4e0eT1\u4e0d\u4e00\u81f4: \u6587\u672c={text_income:.2f}\u4e07\u5143, \u8868={t1_income_wy:.2f}\u4e07\u5143",
                        {"page": t1_page, "table": "BUD_T1"},
                        severity="warn",
                    )
                )

            if (
                t1_expense_wy is not None
                and text_expense is not None
                and not _is_close(t1_expense_wy, text_expense, abs_tol=0.05, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"\u6587\u672c\u652f\u51fa\u9884\u7b97\u4e0eT1\u4e0d\u4e00\u81f4: \u6587\u672c={text_expense:.2f}\u4e07\u5143, \u8868={t1_expense_wy:.2f}\u4e07\u5143",
                        {"page": t1_page, "table": "BUD_T1"},
                        severity="warn",
                    )
                )

        if t4_rows and t4_page:
            _, t4_expense = _extract_t4_totals(t4_rows)
            unit = doc.units_per_page[t4_page - 1] if (t4_page - 1) < len(doc.units_per_page) else None
            t4_expense_wy = _to_wanyuan(t4_expense, unit)
            text_fin_expense = _extract_number_by_patterns(
                all_text,
                [
                    r"\u8d22\u653f\u62e8\u6b3e\u652f\u51fa\u9884\u7b97\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143",
                ],
            )
            if (
                t4_expense_wy is not None
                and text_fin_expense is not None
                and not _is_close(t4_expense_wy, text_fin_expense, abs_tol=0.05, rel_tol=0.0005)
            ):
                issues.append(
                    self._issue(
                        f"\u6587\u672c\u8d22\u653f\u62e8\u6b3e\u652f\u51fa\u9884\u7b97\u4e0eT4\u4e0d\u4e00\u81f4: \u6587\u672c={text_fin_expense:.2f}\u4e07\u5143, \u8868={t4_expense_wy:.2f}\u4e07\u5143",
                        {"page": t4_page, "table": "BUD_T4"},
                        severity="warn",
                    )
                )

        t9_rows, t9_page = _get_budget_table_rows(doc, anchors, "BUD_T9", include_continuation=False)
        if t9_rows and t9_page:
            t9 = _extract_t9_values(t9_rows)
            if t9:
                text_total = _extract_number_by_patterns(
                    all_text,
                    [
                        r"\u4e09\u516c\u201d?\u7ecf\u8d39\u9884\u7b97\u6570\u4e3a\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143",
                    ],
                )
                text_abroad = _extract_number_by_patterns(
                    all_text,
                    [r"\u56e0\u516c\u51fa\u56fd\uff08?\u5883\uff09?\u8d39\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143"],
                )
                text_reception = _extract_number_by_patterns(
                    all_text,
                    [r"\u516c\u52a1\u63a5\u5f85\u8d39\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143"],
                )
                text_org_run = _extract_number_by_patterns(
                    all_text,
                    [r"\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97(?:\u4e3a)?\s*([0-9,]+(?:\.[0-9]+)?)\s*\u4e07\u5143"],
                )

                comparisons = [
                    ("\u4e09\u516c\u5408\u8ba1", text_total, t9.get("total", 0.0)),
                    ("\u56e0\u516c\u51fa\u56fd\u8d39", text_abroad, t9.get("abroad", 0.0)),
                    ("\u516c\u52a1\u63a5\u5f85\u8d39", text_reception, t9.get("reception", 0.0)),
                    ("\u673a\u5173\u8fd0\u884c\u7ecf\u8d39", text_org_run, t9.get("org_run", 0.0)),
                ]
                for label, text_val, table_val in comparisons:
                    if text_val is None:
                        continue
                    if not _is_close(text_val, table_val, abs_tol=0.01, rel_tol=0.0005):
                        issues.append(
                            self._issue(
                                f"\u6587\u5b57\u8bf4\u660e\u4e0eT9\u4e0d\u4e00\u81f4({label}): \u6587\u672c={text_val:.2f}, \u8868={table_val:.2f}",
                                {"page": t9_page, "table": "BUD_T9"},
                                severity="warn",
                            )
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
            return issues

        t3_rows, t3_page = _get_budget_table_rows(doc, anchors, "BUD_T3")
        if not t3_rows or not t3_page:
            return issues

        _, _, t3_project = _extract_total_basic_project(t3_rows)
        unit = doc.units_per_page[t3_page - 1] if (t3_page - 1) < len(doc.units_per_page) else None
        t3_project_wy = _to_wanyuan(t3_project, unit)
        if t3_project_wy is None:
            return issues

        # Narrative and table may differ by tiny rounding; tolerate 0.10万元.
        if _is_close(perf_amount_wy, t3_project_wy, abs_tol=0.10, rel_tol=0.005):
            return issues

        issues.append(
            self._issue(
                f"\u7ee9\u6548\u76ee\u6807\u8bf4\u660e\u989d\u5ea6\u4e0eT3\u9879\u76ee\u652f\u51fa\u5dee\u5f02\u8f83\u5927\uff1a\u8bf4\u660e={perf_amount_wy:.2f}\u4e07\u5143\uff0cT3={t3_project_wy:.2f}\u4e07\u5143\uff1b\u82e5\u4e3a\u4e0d\u540c\u53e3\u5f84\uff0c\u5efa\u8bae\u5728\u6587\u672c\u4e2d\u8865\u5145\u8bf4\u660e",
                {"page": t3_page, "table": "BUD_T3"},
                severity="warn",
                evidence_text=perf_line,
            )
        )
        return issues


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
]
