"""Normalize issue locations into a consistent structured shape."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence


_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_ROW_LINE_RE = re.compile(r"第(?P<row>\d+)行")
_COLUMN_LINE_RE = re.compile(r"列[:：](?P<col>[^\n]+)")
_ASSIGNMENT_RE = re.compile(
    r"(?P<label>T\d+|BUD_T\d+|说明\d+)\s*=\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)"
)

_FINAL_TABLE_BY_CODE = {
    "T1": "收入支出决算总表",
    "T2": "收入决算表",
    "T3": "支出决算表",
    "T4": "财政拨款收入支出决算总表",
    "T5": "一般公共预算财政拨款支出决算表",
    "T6": "一般公共预算财政拨款基本支出决算表",
    "T7": "一般公共预算财政拨款“三公”经费支出决算表",
    "T8": "政府性基金预算财政拨款收入支出决算表",
    "T9": "国有资本经营预算财政拨款收入支出决算表",
}

_TABLE_ALIAS = {
    "总表": "收入支出决算总表",
    "收入表": "收入决算表",
    "支出表": "支出决算表",
    "财政拨款总表": "财政拨款收入支出决算总表",
    "一般公共支出表": "一般公共预算财政拨款支出决算表",
    "基本支出表": "一般公共预算财政拨款基本支出决算表",
    "三公经费表": "一般公共预算财政拨款“三公”经费支出决算表",
}

_RULE_SPECS: Dict[str, Dict[str, Any]] = {
    "V33-200": {
        "field": "本年收入合计",
        "refs": [
            {"role": "T1", "table": "收入支出决算总表", "row": "本年收入合计", "field": "本年收入合计"},
            {"role": "T2", "table": "收入决算表", "row": "合计", "field": "本年收入合计"},
        ],
    },
    "V33-201": {
        "field": "本年支出合计",
        "refs": [
            {"role": "T1", "table": "收入支出决算总表", "row": "本年支出合计", "field": "本年支出合计"},
            {"role": "T3", "table": "支出决算表", "row": "合计", "field": "合计"},
        ],
    },
    "V33-202": {
        "field": "一般公共预算支出",
        "refs": [
            {"role": "T4", "table": "财政拨款收入支出决算总表", "row": "本年支出合计", "field": "一般公共预算支出"},
            {"role": "T5", "table": "一般公共预算财政拨款支出决算表", "row": "合计", "field": "合计"},
        ],
    },
    "V33-203": {
        "field": "基本支出",
        "refs": [
            {"role": "T5", "table": "一般公共预算财政拨款支出决算表", "row": "合计", "field": "基本支出"},
            {"role": "T6", "table": "一般公共预算财政拨款基本支出决算表", "row": "基本支出合计", "field": "基本支出合计"},
        ],
    },
    "V33-204": {
        "field": "财政拨款收入",
        "refs": [
            {"role": "T2", "table": "收入决算表", "row": "合计", "field": "财政拨款收入"},
            {"role": "T4", "table": "财政拨款收入支出决算总表", "row": "本年收入合计", "field": "本年收入合计"},
        ],
    },
    "V33-210": {
        "table": "收入决算表",
        "field": "行内合计",
    },
    "V33-211": {
        "table": "支出决算表",
        "field": "合计",
    },
    "V33-214": {
        "table": "收入支出决算总表",
        "row": "总计",
        "field": "收入侧总计 / 支出侧总计",
        "refs": [
            {"role": "收入侧", "table": "收入支出决算总表", "row": "总计", "field": "收入侧总计"},
            {"role": "支出侧", "table": "收入支出决算总表", "row": "总计", "field": "支出侧总计"},
        ],
    },
    "V33-242": {
        "table": "财政拨款收入支出决算总表",
    },
    "V33-243": {
        "table": "一般公共预算财政拨款基本支出决算表",
    },
    "V33-244": {
        "table": "一般公共预算财政拨款“三公”经费支出决算表",
    },
}


def normalize_issue_location(
    *,
    rule_id: Optional[str],
    location: Optional[Mapping[str, Any]],
    message: str,
    evidence_text: str,
    evidence: Optional[Sequence[Mapping[str, Any]]] = None,
    document: Any = None,
) -> Dict[str, Any]:
    normalized = dict(location or {})
    spec = _RULE_SPECS.get(str(rule_id or "").strip(), {})

    if not normalized.get("table") and spec.get("table"):
        normalized["table"] = spec["table"]

    _apply_rule_specific_fields(normalized, rule_id=rule_id, message=message, evidence_text=evidence_text, spec=spec)

    refs = _normalize_refs(normalized.get("table_refs"))
    if not refs:
        refs = _build_refs_from_spec(
            spec=spec,
            location=normalized,
            message=message,
            evidence_text=evidence_text,
            document=document,
        )

    if refs:
        normalized["table_refs"] = refs

    pages = _collect_pages(
        normalized.get("page"),
        *(normalized.get("pages") or [] if isinstance(normalized.get("pages"), list) else []),
        *[item.get("page") for item in refs],
        *[item.get("page") for item in (evidence or []) if isinstance(item, Mapping)],
    )
    if pages:
        normalized["page"] = pages[0]
        if len(pages) > 1:
            normalized["pages"] = pages
        else:
            normalized.pop("pages", None)

    if refs and not normalized.get("table"):
        tables = _unique_strings(str(ref.get("table") or "").strip() for ref in refs)
        if tables:
            normalized["table"] = " / ".join(tables)

    return normalized


def _apply_rule_specific_fields(
    location: Dict[str, Any],
    *,
    rule_id: Optional[str],
    message: str,
    evidence_text: str,
    spec: Mapping[str, Any],
) -> None:
    rule_key = str(rule_id or "").strip()

    if not location.get("field") and spec.get("field"):
        location["field"] = spec["field"]
    if not location.get("row") and spec.get("row"):
        location["row"] = spec["row"]

    row_index = _extract_row_index(evidence_text)
    if row_index and not location.get("row") and rule_key in {"V33-210", "V33-211"}:
        location["row"] = f"第{row_index}行"

    column_label = _extract_column_label(evidence_text)
    if column_label and not location.get("col") and rule_key == "V33-242":
        location["col"] = column_label

    if rule_key == "V33-242":
        if "收支总计不平衡" in message and not location.get("field"):
            location["field"] = "收入侧总计 / 支出侧总计"
            location.setdefault("row", "总计")
        elif "收入侧" in message and not location.get("field"):
            location["field"] = "收入侧合计"
        elif "支出侧" in message and not location.get("field"):
            location["field"] = "支出侧合计"
        elif "年末结转" in message and not location.get("field"):
            location["field"] = "年末结转"

    if rule_key == "V33-243":
        code = _clean_text(location.get("code")) or _extract_code(evidence_text)
        if code:
            location["code"] = code
            location.setdefault("row", code)
            location.setdefault("field", "一级科目汇总")

        type_key = _clean_text(location.get("type"))
        if type_key == "personnel":
            location.setdefault("row", "人员经费合计")
            location.setdefault("field", "人员经费")
        elif type_key == "public":
            location.setdefault("row", "公用经费合计")
            location.setdefault("field", "公用经费")
        elif "基本支出总结不平" in message or "基本支出合计" in evidence_text:
            location.setdefault("row", "基本支出合计")
            location.setdefault("field", "基本支出合计")

    if rule_key == "V33-244":
        item = _clean_text(location.get("item"))
        type_key = _clean_text(location.get("type"))
        index = location.get("index")
        if not item:
            item = _item_from_index(index)
        if item:
            location.setdefault("field", item)
        if type_key == "budget":
            location.setdefault("col", "预算")
        elif type_key == "final":
            location.setdefault("col", "决算")


def _build_refs_from_spec(
    *,
    spec: Mapping[str, Any],
    location: Mapping[str, Any],
    message: str,
    evidence_text: str,
    document: Any,
) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    role_values = _extract_role_values(message) or _extract_role_values(evidence_text)

    spec_refs = spec.get("refs")
    if isinstance(spec_refs, list):
        for ref_spec in spec_refs:
            if not isinstance(ref_spec, Mapping):
                continue
            role = _clean_text(ref_spec.get("role"))
            table_name = _normalize_table_name(ref_spec.get("table"))
            ref: Dict[str, Any] = {
                "role": role,
                "table": table_name,
            }
            for key in ("row", "field", "section", "col", "code", "subject"):
                raw = _clean_text(ref_spec.get(key))
                if raw:
                    ref[key] = raw

            page = _resolve_table_page(document, table_name)
            if page:
                ref["page"] = page

            if role and role in role_values:
                ref["value"] = role_values[role]
            refs.append(ref)
        return refs

    table_name = _normalize_table_name(location.get("table") or spec.get("table"))
    if not table_name:
        return refs

    fallback_ref: Dict[str, Any] = {"table": table_name}
    page = _resolve_table_page(document, table_name)
    if page:
        fallback_ref["page"] = page

    for key in ("row", "field", "section", "col", "code", "subject"):
        raw = _clean_text(location.get(key))
        if raw:
            fallback_ref[key] = raw
    refs.append(fallback_ref)
    return refs


def _resolve_table_page(document: Any, table_name: str) -> Optional[int]:
    if not document or not table_name:
        return None

    table_name = _normalize_table_name(table_name)
    anchors = getattr(document, "anchors", {}) or {}
    if not anchors:
        try:
            if table_name.startswith("BUD_T") or str(getattr(document, "path", "")).lower().endswith("_budget.pdf"):
                from src.engine.budget_rules import find_budget_anchors

                anchors = find_budget_anchors(document)
            else:
                from src.engine.rules_v33 import find_table_anchors

                anchors = find_table_anchors(document)
        except Exception:
            anchors = {}
        document.anchors = anchors

    pages = anchors.get(table_name) or anchors.get(_normalize_table_name(table_name)) or []
    return pages[0] if pages else None


def _normalize_table_name(raw: Any) -> str:
    token = _clean_text(raw)
    if not token:
        return ""
    if token in _FINAL_TABLE_BY_CODE:
        return _FINAL_TABLE_BY_CODE[token]
    if token in _TABLE_ALIAS:
        return _TABLE_ALIAS[token]
    return token


def _extract_role_values(text: str) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for match in _ASSIGNMENT_RE.finditer(text or ""):
        label = _clean_text(match.group("label"))
        value = _to_number(match.group("value"))
        if not label or value is None:
            continue
        values[label] = value
    return values


def _extract_row_index(text: str) -> Optional[int]:
    match = _ROW_LINE_RE.search(text or "")
    if not match:
        return None
    return _to_positive_int(match.group("row"))


def _extract_column_label(text: str) -> str:
    match = _COLUMN_LINE_RE.search(text or "")
    if not match:
        return ""
    return _clean_text(match.group("col"))


def _extract_code(text: str) -> str:
    match = re.search(r"科目[:：]\s*(\d{3,7})", text or "")
    if not match:
        return ""
    return _clean_text(match.group(1))


def _item_from_index(raw_index: Any) -> str:
    index = _to_positive_int(raw_index)
    mapping = {
        0: "合计",
        1: "因公出国",
        2: "公务用车",
        5: "公务接待",
    }
    if raw_index == 0:
        return mapping[0]
    return mapping.get(index or -1, "")


def _normalize_refs(raw_refs: Any) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if not isinstance(raw_refs, list):
        return refs
    for item in raw_refs:
        if not isinstance(item, Mapping):
            continue
        refs.append(dict(item))
    return refs


def _collect_pages(*values: Any) -> List[int]:
    pages: List[int] = []
    seen = set()
    for value in values:
        if isinstance(value, list):
            iterable = value
        else:
            iterable = [value]
        for item in iterable:
            page = _to_positive_int(item)
            if not page or page in seen:
                continue
            seen.add(page)
            pages.append(page)
    return pages


def _unique_strings(values: Sequence[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _to_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(float(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    match = _NUMBER_RE.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except Exception:
        return None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
