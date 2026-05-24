"""Translate raw AI findings into stable, user-friendly local issue text."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional


_WHITESPACE_RE = re.compile(r"\s+")
_DIGIT_SUFFIX_RE = re.compile(r"(\d{3,})$")

_RULE_PREFIX = {
    "ratio_recalc": "AI-RATIO",
    "sum_mismatch": "AI-SUM",
    "document_kind_mismatch": "AI-DOC",
    "placeholder_residue": "AI-TPL",
    "unit_scope_conflict": "AI-SCOPE",
    "duplicate_text": "AI-TEXT",
    "missing_explanation": "AI-DESC",
    "direction_conflict": "AI-DIR",
    "code_subject_mismatch": "AI-CODE",
    "generic": "AI-SEM",
}


def interpret_ai_issue(
    raw_issue: Mapping[str, Any],
    *,
    page_number: int,
    fallback_rule_id: str,
) -> Dict[str, Any]:
    issue_type = _compact_text(
        raw_issue.get("problem_type") or raw_issue.get("type") or raw_issue.get("category")
    )
    quote = _compact_text(raw_issue.get("quote") or raw_issue.get("original"))
    context = _compact_text(raw_issue.get("context") or raw_issue.get("evidence"))
    original = _compact_text(raw_issue.get("original") or quote or context)
    table_or_section = _compact_text(
        raw_issue.get("table_or_section")
        or raw_issue.get("section")
        or raw_issue.get("table")
    )
    check_name = _compact_text(raw_issue.get("check"))
    expected = _compact_text(_first_non_empty(raw_issue.get("expected"), _metrics_value(raw_issue, "expected")))
    actual = _compact_text(_first_non_empty(raw_issue.get("actual"), _metrics_value(raw_issue, "actual")))
    difference = _compact_text(
        _first_non_empty(raw_issue.get("difference"), raw_issue.get("diff"), _metrics_value(raw_issue, "diff"))
    )
    normalized_kind = _normalize_problem_kind(
        issue_type=issue_type,
        title=_compact_text(raw_issue.get("title")),
        message=_compact_text(raw_issue.get("message")),
        original=original,
        suggestion=_compact_text(raw_issue.get("suggestion")),
        check_name=check_name,
        table_or_section=table_or_section,
        expected=expected,
        actual=actual,
        difference=difference,
    )

    raw_severity = _compact_text(raw_issue.get("severity")).lower()
    is_manual_review = raw_severity == "manual_review"
    severity = normalize_ai_severity(raw_severity)
    rule_id = _resolve_rule_id(
        raw_rule_id=_compact_text(raw_issue.get("rule_id")),
        fallback_rule_id=fallback_rule_id,
        normalized_kind=normalized_kind,
    )

    location = _build_location(
        page_number=page_number,
        table_or_section=table_or_section,
        check_name=check_name,
        raw_issue=raw_issue,
    )
    metrics = _build_metrics(
        expected=expected,
        actual=actual,
        difference=difference,
        confidence=raw_issue.get("confidence"),
        raw_issue=raw_issue,
    )
    title = _build_title(
        normalized_kind=normalized_kind,
        fallback_title=_compact_text(raw_issue.get("title")),
        issue_type=issue_type,
        is_manual_review=is_manual_review,
    )
    message = _build_message(
        normalized_kind=normalized_kind,
        title=title,
        page_number=page_number,
        table_or_section=table_or_section,
        quote=quote,
        context=context,
        expected=expected,
        actual=actual,
        difference=difference,
        check_name=check_name,
        fallback_message=_compact_text(raw_issue.get("message")),
    )
    suggestion = _build_suggestion(
        normalized_kind=normalized_kind,
        fallback_suggestion=_compact_text(raw_issue.get("suggestion")),
        is_manual_review=is_manual_review,
    )
    tags = _build_tags(
        normalized_kind=normalized_kind,
        issue_type=issue_type,
        table_or_section=table_or_section,
        is_manual_review=is_manual_review,
    )

    return {
        "rule_id": rule_id,
        "title": title,
        "message": message,
        "severity": severity,
        "suggestion": suggestion,
        "severity_label": "待人工复核" if is_manual_review else "",
        "location": location,
        "metrics": metrics,
        "tags": tags,
        "quote": quote,
        "context": context,
        "original": original,
        "raw_severity": raw_severity,
    }


def normalize_ai_severity(value: Any, default: str = "medium") -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "critical": "critical",
        "fatal": "critical",
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
    return mapping.get(raw, default)


def _build_location(
    *,
    page_number: int,
    table_or_section: str,
    check_name: str,
    raw_issue: Mapping[str, Any],
) -> Dict[str, Any]:
    location: Dict[str, Any] = {
        "page": page_number,
        "span_start": _span_value(raw_issue.get("span"), 0),
        "span_end": _span_value(raw_issue.get("span"), 1),
    }
    if check_name:
        location["check"] = check_name
    if table_or_section:
        location["table_or_section"] = table_or_section
        if _looks_like_table_name(table_or_section):
            location["table"] = table_or_section
        else:
            location["section"] = table_or_section
    raw_table = _compact_text(raw_issue.get("table"))
    if raw_table and "table" not in location:
        location["table"] = raw_table
    raw_section = _compact_text(raw_issue.get("section"))
    if raw_section and "section" not in location:
        location["section"] = raw_section
    return location


def _build_metrics(
    *,
    expected: str,
    actual: str,
    difference: str,
    confidence: Any,
    raw_issue: Mapping[str, Any],
) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    if expected:
        metrics["expected"] = expected
    if actual:
        metrics["actual"] = actual
    if difference:
        metrics["diff"] = difference
    if _compact_text(raw_issue.get("check")):
        metrics["check"] = _compact_text(raw_issue.get("check"))
    confidence_value = _safe_float(confidence)
    if confidence_value is not None:
        metrics["confidence"] = confidence_value
    return metrics


def _build_title(
    *,
    normalized_kind: str,
    fallback_title: str,
    issue_type: str,
    is_manual_review: bool,
) -> str:
    title_map = {
        "ratio_recalc": "同比/比例复算异常",
        "sum_mismatch": "合计与明细勾稽不一致",
        "document_kind_mismatch": "部门/单位文种表述错误",
        "placeholder_residue": "模板残留或占位符未清理",
        "unit_scope_conflict": "金额单位或统计口径不一致",
        "duplicate_text": "重复表述或模板残句",
        "missing_explanation": "说明或空表说明可能缺失",
        "direction_conflict": "增减方向与数字变化不一致",
        "code_subject_mismatch": "科目编码或用途表述异常",
    }
    base_title = title_map.get(normalized_kind, fallback_title or issue_type or "AI识别问题")
    if is_manual_review and not base_title.startswith("待人工复核"):
        return f"待人工复核：{base_title}"
    return base_title


def _build_message(
    *,
    normalized_kind: str,
    title: str,
    page_number: int,
    table_or_section: str,
    quote: str,
    context: str,
    expected: str,
    actual: str,
    difference: str,
    check_name: str,
    fallback_message: str,
) -> str:
    scope = _format_scope(page_number=page_number, table_or_section=table_or_section)
    quote_text = _quote_text(quote or context)
    check_text = f"（检查项：{check_name}）" if check_name else ""

    if normalized_kind == "ratio_recalc":
        return _join_sentences(
            f"{scope}存在同比、占比或完成率复算异常{check_text}",
            _comparison_text(actual=actual, expected=expected, difference=difference),
            quote_text,
        )
    if normalized_kind == "sum_mismatch":
        return _join_sentences(
            f"{scope}存在合计与明细勾稽不一致{check_text}",
            _comparison_text(actual=actual, expected=expected, difference=difference),
            quote_text,
        )
    if normalized_kind == "document_kind_mismatch":
        return _join_sentences(
            f"{scope}“部门/单位预算（决算）”表述可能写错{check_text}",
            quote_text,
        )
    if normalized_kind == "placeholder_residue":
        return _join_sentences(
            f"{scope}仍存在模板残留、占位符或未清理内容{check_text}",
            quote_text,
        )
    if normalized_kind == "unit_scope_conflict":
        return _join_sentences(
            f"{scope}金额单位或统计口径前后不一致{check_text}",
            _comparison_text(actual=actual, expected=expected, difference=difference),
            quote_text,
        )
    if normalized_kind == "duplicate_text":
        return _join_sentences(
            f"{scope}存在重复表述、残句或异常标点{check_text}",
            quote_text,
        )
    if normalized_kind == "missing_explanation":
        return _join_sentences(
            f"{scope}缺少必要说明、空表说明或变动原因说明{check_text}",
            quote_text,
        )
    if normalized_kind == "direction_conflict":
        return _join_sentences(
            f"{scope}文字描述的增减方向与数字变化不一致{check_text}",
            _comparison_text(actual=actual, expected=expected, difference=difference),
            quote_text,
        )
    if normalized_kind == "code_subject_mismatch":
        return _join_sentences(
            f"{scope}科目编码、名称或用途说明疑似不匹配{check_text}",
            quote_text,
        )
    if fallback_message:
        return fallback_message
    return _join_sentences(f"{scope}发现{title}", quote_text)


def _build_suggestion(
    *,
    normalized_kind: str,
    fallback_suggestion: str,
    is_manual_review: bool,
) -> str:
    if fallback_suggestion:
        return fallback_suggestion

    suggestion_map = {
        "ratio_recalc": "请按表内金额重新复算同比、占比或完成率，并同步修正文中百分比表述。",
        "sum_mismatch": "请核对合计与明细分项金额，修正表格或说明中的错误数据。",
        "document_kind_mismatch": "请按材料实际类型统一“部门/单位预算（决算）”表述，并同步检查全文。",
        "placeholder_residue": "请删除模板占位符、残留示例或未清理文本，补齐正式内容后再公开。",
        "unit_scope_conflict": "请统一金额单位和统计口径，避免将不同口径数据直接比较。",
        "duplicate_text": "请删除重复表述、残句或异常标点，保持公开文本规范。",
        "missing_explanation": "请补充空表说明、无此项说明或同比变动原因说明。",
        "direction_conflict": "请核对增减方向及对应数据，确保文字描述与数字一致。",
        "code_subject_mismatch": "请核对科目编码、名称和用途说明，按正式口径修订。",
        "generic": "请结合原表、原文和截图证据复核后修正。",
    }
    if is_manual_review:
        return "请结合原表、原文和截图证据进行人工复核后确认是否需要修改。"
    return suggestion_map.get(normalized_kind, suggestion_map["generic"])


def _build_tags(
    *,
    normalized_kind: str,
    issue_type: str,
    table_or_section: str,
    is_manual_review: bool,
) -> list[str]:
    labels = {
        "ratio_recalc": "比例复算",
        "sum_mismatch": "勾稽检查",
        "document_kind_mismatch": "文种检查",
        "placeholder_residue": "模板残留",
        "unit_scope_conflict": "口径检查",
        "duplicate_text": "文本规范",
        "missing_explanation": "说明完整性",
        "direction_conflict": "方向校验",
        "code_subject_mismatch": "科目校验",
        "generic": "AI检测",
    }
    tags = [labels.get(normalized_kind, "AI检测")]
    if issue_type:
        tags.append(issue_type)
    if table_or_section:
        tags.append(table_or_section)
    if is_manual_review:
        tags.append("待人工复核")
    seen = set()
    ordered: list[str] = []
    for item in tags:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _normalize_problem_kind(
    *,
    issue_type: str,
    title: str,
    message: str,
    original: str,
    suggestion: str,
    check_name: str,
    table_or_section: str,
    expected: str,
    actual: str,
    difference: str,
) -> str:
    parts = [
        issue_type,
        title,
        message,
        original,
        suggestion,
        check_name,
        table_or_section,
        expected,
        actual,
        difference,
    ]
    corpus = " | ".join(part for part in parts if part).lower()
    corpus_no_space = "|".join(_WHITESPACE_RE.sub("", part.lower()) for part in parts if part)

    if any(token in corpus_no_space for token in ("部门预算", "单位预算", "部门决算", "单位决算", "文种")):
        return "document_kind_mismatch"
    if any(token in corpus_no_space for token in ("同比", "占比", "完成率", "增长率", "下降率", "百分点")) or (
        "%" in corpus and (expected or actual or difference)
    ):
        return "ratio_recalc"
    if any(token in corpus_no_space for token in ("勾稽", "合计", "明细", "分项之和", "汇总", "总计")):
        return "sum_mismatch"
    if any(token in corpus_no_space for token in ("xx", "xxx", "待填", "见附件", "占位符", "模板")):
        return "placeholder_residue"
    if any(token in corpus_no_space for token in ("单位错误", "口径", "万元", "亿元", "财政拨款", "一般公共预算")):
        return "unit_scope_conflict"
    if any(token in corpus_no_space for token in ("重复", "残句", "连续句号", "引号", "主要原因是。")):
        return "duplicate_text"
    if any(token in corpus_no_space for token in ("空表", "无此项", "未说明原因", "缺少说明", "原因缺失", "不涉及说明")):
        return "missing_explanation"
    if any(token in corpus_no_space for token in ("方向相反", "方向矛盾", "增加", "减少", "增长", "下降")) and (
        expected or actual or difference
    ):
        return "direction_conflict"
    if any(token in corpus_no_space for token in ("编码", "科目", "用途错配", "名称错配", "住房公积金", "医疗保险")):
        return "code_subject_mismatch"
    return "generic"


def _resolve_rule_id(
    *,
    raw_rule_id: str,
    fallback_rule_id: str,
    normalized_kind: str,
) -> str:
    if raw_rule_id:
        return raw_rule_id
    prefix = _RULE_PREFIX.get(normalized_kind, "AI-SEM")
    match = _DIGIT_SUFFIX_RE.search(fallback_rule_id)
    if match:
        return f"{prefix}-{match.group(1)}"
    return fallback_rule_id or prefix


def _comparison_text(*, actual: str, expected: str, difference: str) -> str:
    parts = []
    if actual and expected:
        parts.append(f"当前写为 {actual}，按当前证据复核应为 {expected}")
    elif actual:
        parts.append(f"当前识别值为 {actual}")
    elif expected:
        parts.append(f"按当前证据复核应为 {expected}")
    if difference:
        parts.append(f"差异 {difference}")
    return "，".join(parts)


def _format_scope(*, page_number: int, table_or_section: str) -> str:
    if table_or_section:
        return f"第{page_number}页“{table_or_section}”"
    return f"第{page_number}页"


def _quote_text(value: str) -> str:
    if not value:
        return ""
    return f"命中文本：{value}"


def _join_sentences(*parts: str) -> str:
    cleaned = [part.strip(" ，。") for part in parts if part and part.strip(" ，。")]
    if not cleaned:
        return ""
    message = "。".join(cleaned)
    if not message.endswith("。"):
        message += "。"
    return message


def _looks_like_table_name(value: str) -> bool:
    lowered = value.lower()
    if "说明" in value or "章节" in value:
        return False
    return "表" in value or lowered.startswith(("t", "bud_t"))


def _metrics_value(raw_issue: Mapping[str, Any], key: str) -> Any:
    metrics = raw_issue.get("metrics")
    if isinstance(metrics, Mapping):
        return metrics.get(key)
    return None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _span_value(span: Any, index: int) -> int:
    if not isinstance(span, (list, tuple)) or len(span) <= index:
        return 0
    try:
        value = int(span[index])
    except Exception:
        return 0
    return value if value >= 0 else 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _compact_text(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    text = _WHITESPACE_RE.sub(" ", str(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
