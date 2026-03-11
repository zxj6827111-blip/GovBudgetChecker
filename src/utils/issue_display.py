"""Helpers for turning raw findings into readable UI/export text."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional


_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")
_PAIR_MISMATCH_PATTERN = re.compile(
    r"(?P<label>[0-9A-Za-z_\-\u4e00-\u9fff]+)\s*:\s*"
    r"(?P<left>-?\d[\d,]*(?:\.\d+)?)\s*(?:!=|\u2260)\s*"
    r"(?P<right>-?\d[\d,]*(?:\.\d+)?)"
)
_GENERIC_COMPARE_PATTERN = re.compile(
    "(?P<label>[^:\\uff1a\\n]{1,40})[:\\uff1a]\\s*"
    "(?:\\u8868(?:\\u7b97|\\u683c\\u8ba1\\u7b97|\\u683c|\\u5185)?|\\u8868\\u683c\\u8ba1\\u7b97)?\\s*"
    "(?P<left>-?\\d[\\d,]*(?:\\.\\d+)?%?)\\s*(?:!=|\\u2260)\\s*"
    "(?:\\u6587\\u672c|\\u8bf4\\u660e|\\u6587\\u4e2d|\\u53d9\\u8ff0)?\\s*"
    "(?P<right>-?\\d[\\d,]*(?:\\.\\d+)?%?)"
)
_KEY_VALUE_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;\n]+)")
_ASSIGNMENT_PAIR_PATTERN = re.compile(
    r"(?P<label>[A-Za-z0-9_+\-/()（）\u4e00-\u9fff]{1,30})\s*=\s*"
    r"(?P<value>\[[^\]]+\]|-?\d[\d,]*(?:\.\d+)?(?:%|万元|元|亿元)?|[^,，;；\s]+)"
)

_KEY_LABELS = {
    "code": "\u7f16\u7801",
    "income": "\u6536\u5165\u8868\u91d1\u989d",
    "expense": "\u652f\u51fa\u8868\u91d1\u989d",
    "diff": "\u5dee\u989d",
    "income_only": "\u4ec5\u6536\u5165\u8868\u51fa\u73b0",
    "expense_only": "\u4ec5\u652f\u51fa\u8868\u51fa\u73b0",
    "narrative": "\u6587\u5b57\u8bf4\u660e",
    "table": "\u8868\u5185\u91d1\u989d",
    "field": "\u5bf9\u5e94\u5b57\u6bb5",
    "car_total": "\u516c\u8f66\u5c0f\u8ba1",
    "car_buy": "\u8d2d\u7f6e\u8d39",
    "car_run": "\u8fd0\u884c\u8d39",
    "table_row_vals": "\u8868\u683c\u884c\u503c",
    "table_vals": "\u8868\u683c\u6570\u503c",
    "narrative_key": "\u8bf4\u660e\u9879",
    "narrative_val": "\u8bf4\u660e\u503c",
    "table_row": "\u8868\u683c\u884c",
    "subject": "\u79d1\u76ee",
    "value": "\u91d1\u989d",
}


def build_issue_display(issue: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a compact, readable display payload from a raw issue."""

    title = _clean_text(issue.get("title"))
    message = _clean_text(issue.get("message"))
    why_not = _clean_text(issue.get("why_not"))
    page = _resolve_page(issue)
    location = issue.get("location") if isinstance(issue.get("location"), Mapping) else {}
    evidence_text = _extract_evidence_text(issue)

    detail_lines = _build_detail_lines(
        title=title,
        message=message,
        evidence_text=evidence_text,
        why_not=why_not,
        location=location,
        page=page,
    )

    summary = title or _first_sentence(message) or "\u672a\u547d\u540d\u95ee\u9898"
    if not summary and detail_lines:
        summary = detail_lines[0]

    page_text = _build_page_text(location, page)
    location_text = _build_location_text(location, page=page, evidence_text=evidence_text)

    return {
        "summary": summary,
        "page_text": page_text,
        "location_text": location_text,
        "detail_lines": detail_lines,
        "evidence_text": evidence_text,
    }


def _build_detail_lines(
    *,
    title: str,
    message: str,
    evidence_text: str,
    why_not: str,
    location: Mapping[str, Any],
    page: Optional[int],
) -> List[str]:
    lines: List[str] = []

    lines.extend(_build_location_detail_lines(location, page=page))

    for source in (message, evidence_text):
        if not source:
            continue
        lines.extend(_parse_pair_mismatches(source))
        lines.extend(_parse_generic_compares(source))
        lines.extend(_parse_key_value_lines(source))
        lines.extend(_parse_assignment_pairs(source))

    sanitized_message = _sanitize_message(message, title)
    if sanitized_message:
        lines.insert(0, sanitized_message)

    if why_not and not why_not.startswith(("NO_", "AI_LOCATED:", "TOLERANCE_FILTERED:")):
        lines.append(why_not)

    return _dedupe_lines(lines)


def _build_location_detail_lines(
    location: Mapping[str, Any],
    *,
    page: Optional[int],
) -> List[str]:
    refs = _extract_location_refs(location)
    lines: List[str] = []

    for ref in refs:
        parts: List[str] = []
        ref_page = _to_positive_int(ref.get("page"))
        if ref_page:
            parts.append(f"\u7b2c{ref_page}\u9875")

        for key, label in (
            ("table", "\u8868"),
            ("section", "\u7ae0\u8282"),
            ("row", "\u884c"),
            ("col", "\u5217"),
            ("field", "\u5b57\u6bb5"),
            ("code", "\u7f16\u7801"),
            ("subject", "\u79d1\u76ee"),
        ):
            raw = _clean_text(ref.get(key))
            if not raw:
                continue
            parts.append(f"{label}: {raw}")

        raw_value = ref.get("value")
        if raw_value not in (None, ""):
            parts.append(f"\u91d1\u989d: {_format_numeric_text(raw_value)}")

        if not parts:
            continue

        role = _clean_text(ref.get("role")) or _infer_ref_role(ref)
        prefix = f"{role}: " if role else "\u5b9a\u4f4d: "
        lines.append(prefix + " / ".join(parts))

    if lines:
        return lines

    if location:
        fallback_parts: List[str] = []
        if page:
            fallback_parts.append(f"\u7b2c{page}\u9875")
        for key, label in (
            ("table", "\u8868"),
            ("section", "\u7ae0\u8282"),
            ("row", "\u884c"),
            ("col", "\u5217"),
            ("field", "\u5b57\u6bb5"),
            ("code", "\u7f16\u7801"),
            ("subject", "\u79d1\u76ee"),
        ):
            raw = _clean_text(location.get(key))
            if not raw:
                continue
            fallback_parts.append(f"{label}: {raw}")
        if fallback_parts:
            return ["\u5b9a\u4f4d: " + " / ".join(fallback_parts)]

    return []


def _sanitize_message(message: str, title: str) -> str:
    if not message:
        return ""

    sanitized = re.sub(r"\u547d\u4e2d\u539f\u6587[:\uff1a].*$", "", message).strip(" \uff1b;,\uff0c\u3002")
    if not sanitized or sanitized == title:
        return ""
    return sanitized


def _parse_pair_mismatches(text: str) -> List[str]:
    lines: List[str] = []
    for match in _PAIR_MISMATCH_PATTERN.finditer(text or ""):
        label = match.group("label").strip()
        left = match.group("left").strip()
        right = match.group("right").strip()
        diff_text = _diff_text(left, right)
        line = f"\u7f16\u7801 {label}: {_format_numeric_text(left)} vs {_format_numeric_text(right)}"
        if diff_text:
            line += f"\uff0c\u5dee\u989d {diff_text}"
        lines.append(line)
    return lines


def _parse_generic_compares(text: str) -> List[str]:
    lines: List[str] = []
    for match in _GENERIC_COMPARE_PATTERN.finditer(text or ""):
        label = match.group("label").strip()
        left = match.group("left").strip()
        right = match.group("right").strip()
        diff_text = _diff_text(left, right)
        line = f"{label}: \u8868\u683c {_format_numeric_text(left)}\uff0c\u6587\u5b57\u8bf4\u660e {_format_numeric_text(right)}"
        if diff_text:
            line += f"\uff0c\u5dee\u989d {diff_text}"
        lines.append(line)
    return lines


def _parse_key_value_lines(text: str) -> List[str]:
    pairs = {
        key.strip(): value.strip()
        for key, value in _KEY_VALUE_PATTERN.findall(text or "")
        if key and value
    }
    if not pairs:
        return []

    lines: List[str] = []

    income_only = pairs.get("income_only")
    if income_only and income_only != "-":
        lines.append(f"\u4ec5\u6536\u5165\u8868\u51fa\u73b0\u7f16\u7801: {income_only}")

    expense_only = pairs.get("expense_only")
    if expense_only and expense_only != "-":
        lines.append(f"\u4ec5\u652f\u51fa\u8868\u51fa\u73b0\u7f16\u7801: {expense_only}")

    narrative = pairs.get("narrative")
    table = pairs.get("table")
    if narrative and table:
        field = pairs.get("field")
        prefix = f"{field}: " if field else ""
        diff_text = _diff_text(narrative, table)
        line = (
            f"{prefix}\u6587\u5b57\u8bf4\u660e {_format_numeric_text(narrative)}\uff0c"
            f"\u8868\u5185\u91d1\u989d {_format_numeric_text(table)}"
        )
        if diff_text:
            line += f"\uff0c\u5dee\u989d {diff_text}"
        lines.append(line)

    code = pairs.get("code")
    income = pairs.get("income")
    expense = pairs.get("expense")
    if code and income and expense:
        diff_text = pairs.get("diff") or _diff_text(income, expense)
        line = (
            f"\u7f16\u7801 {code}: \u6536\u5165\u8868 {_format_numeric_text(income)}\uff0c"
            f"\u652f\u51fa\u8868 {_format_numeric_text(expense)}"
        )
        if diff_text:
            line += f"\uff0c\u5dee\u989d {_format_numeric_text(diff_text)}"
        lines.append(line)

    for key, value in pairs.items():
        if key in {
            "income_only",
            "expense_only",
            "narrative",
            "table",
            "field",
            "code",
            "income",
            "expense",
            "diff",
        }:
            continue
        label = _KEY_LABELS.get(key, key)
        lines.append(f"{label}: {value}")

    return lines


def _parse_assignment_pairs(text: str) -> List[str]:
    matches = list(_ASSIGNMENT_PAIR_PATTERN.finditer(text or ""))
    if len(matches) < 2:
        return []

    pairs: List[tuple[str, str]] = []
    for match in matches[:4]:
        label = _clean_text(match.group("label"))
        value = _clean_text(match.group("value"))
        if not label or not value:
            continue
        if label in _KEY_LABELS:
            # Structured evidence already handled by key-value parser.
            continue
        pairs.append((label, value))

    if len(pairs) < 2:
        return []

    fragments = [f"{label} {_format_numeric_text(value)}" for label, value in pairs]
    line = "，".join(fragments)

    left_number = _to_number(pairs[0][1])
    right_number = _to_number(pairs[1][1])
    if left_number is not None and right_number is not None:
        diff = abs(left_number - right_number)
        if "%" in pairs[0][1] or "%" in pairs[1][1]:
            line += f"，差额 {diff:.2f}%"
        else:
            line += f"，差额 {_format_number(diff)}"

    return [line]


def _build_location_text(
    location: Mapping[str, Any],
    *,
    page: Optional[int],
    evidence_text: str,
) -> str:
    parts: List[str] = []
    pages = _extract_pages(location)

    if len(pages) > 1:
        parts.append(f"相关页: {', '.join(str(item) for item in pages)}")

    for key, label in (
        ("table", "\u8868"),
        ("section", "\u7ae0\u8282"),
        ("row", "\u884c"),
        ("col", "\u5217"),
        ("field", "\u5b57\u6bb5"),
        ("code", "\u7f16\u7801"),
        ("subject", "\u79d1\u76ee"),
    ):
        raw = location.get(key)
        if raw in (None, ""):
            continue
        parts.append(f"{label}: {raw}")

    if parts:
        return " / ".join(parts)

    if page:
        return f"\u7b2c{page}\u9875"

    if evidence_text:
        return "\u672a\u63d0\u53d6\u5230\u7ed3\u6784\u5316\u5750\u6807\uff0c\u53ef\u6309\u8bc1\u636e\u539f\u6587\u68c0\u7d22\u5b9a\u4f4d"

    return "\u672a\u63d0\u53d6\u5230\u9875\u7801\u548c\u7ed3\u6784\u5316\u5b9a\u4f4d\u4fe1\u606f"


def _extract_evidence_text(issue: Mapping[str, Any]) -> str:
    evidence = issue.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            for key in ("text", "text_snippet", "original"):
                text = _clean_text(item.get(key))
                if text:
                    return text

    for key in ("text_snippet", "evidence_text", "message"):
        text = _clean_text(issue.get(key))
        if text:
            if key == "message" and text == _clean_text(issue.get("title")):
                continue
            return text
    return ""


def _resolve_page(issue: Mapping[str, Any]) -> Optional[int]:
    location = issue.get("location")
    if isinstance(location, Mapping):
        pages = _extract_pages(location)
        if pages:
            return pages[0]
        page = _to_positive_int(location.get("page"))
        if page:
            return page

    evidence = issue.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            page = _to_positive_int(item.get("page"))
            if page:
                return page

    return _to_positive_int(issue.get("page_number"))


def _build_page_text(location: Mapping[str, Any], page: Optional[int]) -> str:
    pages = _extract_pages(location)
    if len(pages) > 1:
        return "\u7b2c" + "\u3001".join(str(item) for item in pages) + "\u9875"
    if page:
        return f"\u7b2c{page}\u9875"
    return ""


def _extract_pages(location: Mapping[str, Any]) -> List[int]:
    raw_pages = location.get("pages")
    if not isinstance(raw_pages, list):
        return []

    pages: List[int] = []
    seen = set()
    for value in raw_pages:
        page = _to_positive_int(value)
        if not page or page in seen:
            continue
        seen.add(page)
        pages.append(page)
    return pages


def _extract_location_refs(location: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    raw_refs = location.get("table_refs")
    if not isinstance(raw_refs, list):
        return []

    refs: List[Mapping[str, Any]] = []
    for item in raw_refs:
        if isinstance(item, Mapping):
            refs.append(item)
    return refs


def _infer_ref_role(ref: Mapping[str, Any]) -> str:
    section = _clean_text(ref.get("section"))
    table = _clean_text(ref.get("table"))
    if section and not table:
        return "\u8bf4\u660e"
    if table and not section:
        return table
    if section:
        return "\u8bf4\u660e/\u8868\u683c"
    return ""


def _to_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(float(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _diff_text(left: str, right: str) -> str:
    left_number = _to_number(left)
    right_number = _to_number(right)
    if left_number is None or right_number is None:
        return ""

    diff = abs(left_number - right_number)
    if "%" in str(left) or "%" in str(right):
        return f"{diff:.2f}%"
    return _format_number(diff)


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    match = _NUMBER_PATTERN.search(str(value))
    if not match:
        return None

    token = (
        match.group(0)
        .replace(",", "")
        .replace("%", "")
        .replace("万元", "")
        .replace("亿元", "")
        .replace("元", "")
    )
    try:
        return float(token)
    except Exception:
        return None


def _format_numeric_text(value: Any) -> str:
    raw = str(value).strip()
    number = _to_number(raw)
    if number is None:
        return raw
    if raw.endswith("%"):
        return f"{number:.2f}%"
    return _format_number(number)


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return format(int(round(value)), ",")
    return format(value, ",.2f")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    for separator in ("\u3002", "\uff1b", ";", "\n"):
        if separator in text:
            return text.split(separator, 1)[0].strip()
    return text.strip()


def _dedupe_lines(lines: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for line in lines:
        cleaned = line.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped
