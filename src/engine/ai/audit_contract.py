"""AI 审校响应的业务契约校验。

合法 JSON 不等于合法业务响应：这里逐条校验数组元素、必需字段、类型、枚举、
引文连续性和证据引用；非法条目单独拒绝并留原因，合法条目保留，不让一条坏数据
把整批结果变空，也不让坏数据伪装成成功。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROBLEM_TYPES = frozenset(
    {
        "ratio_recalc",
        "sum_mismatch",
        "unit_scope_conflict",
        "direction_conflict",
        "typo",
        "missing_char",
        "extra_char",
        "repeated_words",
        "grammar",
        "fiscal_usage_error",
        "document_identity_conflict",
        "placeholder_residue",
        "fragment",
        "explanation_insufficient",
        "cross_section_conflict",
        "cost_purpose_mismatch",
        "missing_explanation",
        "code_subject_mismatch",
        "generic",
    }
)

_SEVERITIES = {
    "critical": "critical",
    "fatal": "high",
    "error": "high",
    "high": "high",
    "warn": "medium",
    "warning": "medium",
    "medium": "medium",
    "manual_review": "manual_review",
    "low": "low",
    "info": "info",
    "hint": "info",
}

_TEXT_TYPES = {
    "typo",
    "missing_char",
    "extra_char",
    "repeated_words",
    "grammar",
    "fiscal_usage_error",
    "document_identity_conflict",
    "placeholder_residue",
    "fragment",
}

_ALIASES = {
    "cross_section_inconsistency": "cross_section_conflict",
    "cross_section_mismatch": "cross_section_conflict",
    "consistency_conflict": "cross_section_conflict",
    "typo_inconsistency": "typo",
    "duplicate": "repeated_words",
    "duplicate_text": "repeated_words",
    "repeat_word": "repeated_words",
    "repeat": "repeated_words",
    "explanation_missing": "explanation_insufficient",
    "insufficient_explanation": "explanation_insufficient",
    "missing_coverage": "missing_explanation",
    "sentence_error": "grammar",
    "usage_mismatch": "fiscal_usage_error",
}

_WHITESPACE_RE = re.compile(r"\s+")
_CJK_OR_WORD_RE = re.compile(r"[\u4e00-\u9fff0-9A-Za-z%]")


@dataclass
class AuditValidation:
    accepted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    raw_count: int = 0
    reasons: Dict[str, int] = field(default_factory=dict)
    status: str = "empty"

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "reasons": dict(self.reasons),
            "status": self.status,
        }


def locate_quote(window_text: str, quote: str) -> Optional[Tuple[int, int]]:
    """Return raw [start,end) for a verbatim quote; soft line breaks may differ.

    The fallback only ignores whitespace differences (PDF soft line breaks/OCR
    double spaces) and maps the match back to raw offsets through an index map.
    It never drops, adds, or rewrites non-whitespace characters.
    """
    text = window_text or ""
    target = (quote or "").strip()
    if not text or not target:
        return None
    exact = text.find(target)
    if exact >= 0:
        return exact, exact + len(target)

    raw_positions: List[int] = []
    normalized_chars: List[str] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        raw_positions.append(index)
        normalized_chars.append(char)
    normalized = "".join(normalized_chars)
    target_normalized = _WHITESPACE_RE.sub("", target)
    if not target_normalized:
        return None
    hit = normalized.find(target_normalized)
    if hit < 0:
        return None
    start = raw_positions[hit]
    end = raw_positions[hit + len(target_normalized) - 1] + 1
    return start, end


def validate_audit_items(
    items: Any,
    *,
    window_text: str,
    allowed_pages: Optional[Iterable[int]] = None,
    min_confidence: float = 0.75,
    truncated: bool = False,
) -> AuditValidation:
    result = AuditValidation()
    if items is None:
        result.status = "invalid"
        result.reasons["response_not_array"] = 1
        return result
    if not isinstance(items, list):
        result.status = "invalid"
        result.reasons["response_not_array"] = 1
        return result

    pages = {int(page) for page in (allowed_pages or []) if str(page).isdigit()}
    result.raw_count = len(items)
    if not items:
        result.status = "truncated" if truncated else "empty"
        return result

    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            _reject(result, index, "item_not_object", type(raw).__name__)
            continue
        accepted, reason, detail = _validate_one(
            raw,
            window_text=window_text,
            pages=pages,
            min_confidence=min_confidence,
        )
        if accepted is None:
            _reject(result, index, reason or "invalid_item", detail)
            continue
        result.accepted.append(accepted)

    if truncated:
        # 截断优先于 partial：即使保留了可解析的候选，本次输出仍不完整。
        result.status = "truncated"
    elif not result.accepted:
        result.status = "invalid"
    elif result.rejected:
        result.status = "partial"
    else:
        result.status = "ok"
    return result


def _reject(result: AuditValidation, index: int, reason: str, detail: Any = None) -> None:
    entry: Dict[str, Any] = {"index": index, "reason": reason}
    if detail is not None:
        entry["detail"] = detail
    result.rejected.append(entry)
    result.reasons[reason] = result.reasons.get(reason, 0) + 1


def _validate_one(
    raw: Dict[str, Any],
    *,
    window_text: str,
    pages: set[int],
    min_confidence: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Any]:
    quote = _text(raw.get("quote"))
    original = _text(raw.get("original")) or quote
    if not quote:
        return None, "missing_quote", None
    located = locate_quote(window_text, quote)
    if located is None:
        return None, "quote_not_in_window", {"quote_length": len(quote)}
    if not original:
        return None, "missing_original", None

    problem_type = _canonical_problem_type(raw)
    if not problem_type:
        return None, "unknown_problem_type", _text(
            raw.get("problem_type") or raw.get("type") or raw.get("category")
        )[:40]

    severity = _SEVERITIES.get(_text(raw.get("severity")).lower())
    if not severity:
        return None, "invalid_severity", _text(raw.get("severity"))[:40]

    confidence = _confidence(raw.get("confidence"))
    if confidence is None or confidence < 0 or confidence > 1:
        return None, "invalid_confidence", raw.get("confidence")
    if confidence < min_confidence:
        return None, "below_min_confidence", confidence

    suggestion = _text(raw.get("suggestion"))
    if not suggestion:
        return None, "missing_suggestion", None

    context = _text(raw.get("context")) or quote
    page = _page(raw.get("page"))
    page_claim_rejected = False
    if page is not None and pages and page not in pages:
        page_claim_rejected = True
        page = None

    span, span_state = _span(raw.get("span"), located, window_text, quote)
    expected, expected_coerced = _field_text(raw.get("expected"))
    actual, actual_coerced = _field_text(raw.get("actual"))
    difference, difference_coerced = _field_text(
        raw.get("difference") if raw.get("difference") is not None else raw.get("diff")
    )
    check = _text(raw.get("check"))
    table_or_section = _text(raw.get("table_or_section") or raw.get("section") or raw.get("table"))

    evidence_pairs, rejected_pairs, pair_reasons = _evidence_pairs(
        raw.get("evidence_pairs"), window_text
    )
    uncertainties = _string_list(raw.get("semantic_uncertainties"))
    if problem_type == "cross_section_conflict" and len(evidence_pairs) < 2:
        severity = "manual_review"
        uncertainties.append("跨段矛盾缺少双方可核验证据，降级为待人工复核")
    if problem_type in {
        "ratio_recalc",
        "sum_mismatch",
        "unit_scope_conflict",
        "direction_conflict",
        "cross_section_conflict",
    } and not (expected or actual):
        if severity != "manual_review":
            severity = "manual_review"
        uncertainties.append("模型未提供可核对的 expected/actual，保留待人工复核")

    if severity != "manual_review" and problem_type in _TEXT_TYPES and severity == "high":
        # 文字类问题不因模型声称“high”而升为高风险；业务定级由规则/人工拥有。
        severity = "medium"

    fact_ids, fact_reasons = _string_list_with_reason(raw.get("fact_ids"))
    evidence_ids, evidence_reasons = _string_list_with_reason(raw.get("evidence_ids"))
    relation_id = _text(raw.get("check_relation_id") or raw.get("relation_id")) or None
    if relation_id is not None and not isinstance(
        raw.get("check_relation_id") or raw.get("relation_id"), str
    ):
        relation_id = None

    validation: Dict[str, Any] = {
        "span": span_state,
        "page_claim_rejected": page_claim_rejected,
        "coerced_fields": [
            name
            for name, coerced in (
                ("expected", expected_coerced),
                ("actual", actual_coerced),
                ("difference", difference_coerced),
            )
            if coerced
        ],
        "evidence_pair_reasons": pair_reasons,
        "id_reasons": {**fact_reasons, **evidence_reasons},
    }
    if span_state != "valid":
        validation["span_reason"] = span_state

    accepted = {
        "problem_type": problem_type,
        "original": original,
        "quote": quote,
        "suggestion": suggestion,
        "severity": severity,
        "confidence": confidence,
        "span": [int(span[0]), int(span[1])],
        "context": context,
        "page": page,
        "table_or_section": table_or_section or None,
        "check": check or None,
        "expected": expected or None,
        "actual": actual or None,
        "difference": difference or None,
        "evidence_pairs": evidence_pairs,
        "evidence_pairs_rejected": rejected_pairs,
        "semantic_uncertainties": uncertainties,
        "fact_ids": fact_ids,
        "evidence_ids": evidence_ids,
        "relation_id": relation_id,
        "validation": validation,
    }
    return accepted, None, None


def _canonical_problem_type(raw: Dict[str, Any]) -> Optional[str]:
    value = _text(raw.get("problem_type") or raw.get("type") or raw.get("category")).lower()
    value = _ALIASES.get(value, value)
    if value in PROBLEM_TYPES:
        return value
    # 兼容旧模型把类型写进 rule_id 的情况（只接受明确枚举值）。
    rule_id = _text(raw.get("rule_id")).lower()
    rule_id = _ALIASES.get(rule_id, rule_id)
    if rule_id in PROBLEM_TYPES:
        return rule_id
    return None


def _span(
    value: Any,
    located: Tuple[int, int],
    window_text: str,
    quote: str,
) -> Tuple[Tuple[int, int], str]:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and 0 <= value[0] < value[1] <= len(window_text)
    ):
        start, end = int(value[0]), int(value[1])
        span_text = _WHITESPACE_RE.sub("", window_text[start:end])
        quote_text = _WHITESPACE_RE.sub("", quote)
        if span_text == quote_text:
            return (start, end), "valid"
        return (located[0], located[1]), "mismatch_repaired_from_quote"
    return (located[0], located[1]), "repaired_from_quote"


def _evidence_pairs(
    value: Any, window_text: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    pairs: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = {}
    if value is None:
        return pairs, rejected, reasons
    if not isinstance(value, list):
        reasons["evidence_pairs_not_array"] = 1
        return pairs, rejected, reasons
    for item in value:
        if not isinstance(item, dict):
            reasons["evidence_pair_not_object"] = reasons.get("evidence_pair_not_object", 0) + 1
            continue
        quote = _text(item.get("quote"))
        page = _page(item.get("page"))
        if not quote or locate_quote(window_text, quote) is None:
            reasons["evidence_pair_quote_not_in_window"] = (
                reasons.get("evidence_pair_quote_not_in_window", 0) + 1
            )
            # 不把未通过逐字校验的“证据”当已核实证据，但保留模型声称的
            # 页码/表名，供人工复核，不静默丢弃。
            rejected.append(
                {
                    "quote": quote,
                    "page": page,
                    "table_or_section": _text(item.get("table_or_section")) or None,
                    "reason": "quote_not_contiguous_in_window",
                }
            )
            continue
        pairs.append(
            {
                "quote": quote,
                "page": page,
                "table_or_section": _text(item.get("table_or_section")) or None,
            }
        )
    return pairs, rejected, reasons


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _field_text(value: Any) -> Tuple[str, bool]:
    if value is None:
        return "", False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value), True
    return _text(value), False


def _page(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        page = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _confidence(value: Any) -> Optional[float]:
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("%"):
                return float(text[:-1]) / 100.0
            return float(text)
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _string_list_with_reason(value: Any) -> Tuple[List[str], Dict[str, int]]:
    if value is None:
        return [], {}
    if not isinstance(value, list):
        return [], {"ids_not_array": 1}
    invalid = sum(
        1 for item in value if not isinstance(item, str) or not item.strip()
    )
    parsed = _string_list(value)
    return parsed, ({"invalid_id_entries": invalid} if invalid else {})


__all__ = ["AuditValidation", "PROBLEM_TYPES", "locate_quote", "validate_audit_items"]
