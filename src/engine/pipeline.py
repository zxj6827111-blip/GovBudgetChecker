from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from .budget_rules import ALL_BUDGET_RULES
from .common_rules import ALL_COMMON_RULES
from .rules_v33 import (
    ALL_RULES as FINAL_ALL_RULES,
)
from .rules_v33 import (
    Issue,
    order_and_number_issues,
)
from .rules_v33 import (
    build_document as _build_document,
)

_LIST_PREFIX_RE = re.compile(r"^\s*[一二三四五六七八九十\d]+[、.)]\s*")

# re-export for existing callers importing build_document from this module
build_document = _build_document


def _resolve_report_kind(doc: Any, report_kind: Optional[str] = None) -> str:
    kind = (report_kind or "").strip().lower()
    if kind in {"budget", "final"}:
        return kind

    path = str(getattr(doc, "path", "") or "")
    lowered = path.lower()
    if "budget" in lowered or "预算" in path:
        return "budget"
    if "final" in lowered or "决算" in path:
        return "final"

    page_texts = getattr(doc, "page_texts", []) or []
    first_text = page_texts[0] if page_texts else ""
    if "预算" in first_text:
        return "budget"
    if "决算" in first_text:
        return "final"
    return "final"


def _select_rule_set(doc: Any, report_kind: Optional[str] = None) -> List[Any]:
    return (
        ALL_BUDGET_RULES
        if _resolve_report_kind(doc, report_kind) == "budget"
        else FINAL_ALL_RULES
    )


def run_rules(
    doc: Any, use_ai_assist: bool = False, report_kind: Optional[str] = None
) -> List[Issue]:
    selected_rules = [
        *_select_rule_set(doc, report_kind=report_kind),
        *ALL_COMMON_RULES,
    ]
    issues: List[Issue] = []

    for rule_obj in selected_rules:
        try:
            rule = rule_obj() if isinstance(rule_obj, type) else rule_obj
            if hasattr(rule, "apply_with_ai") and use_ai_assist:
                issues.extend(rule.apply_with_ai(doc, use_ai_assist))
            else:
                issues.extend(rule.apply(doc))
        except Exception as err:
            code = getattr(rule_obj, "code", None) or getattr(
                getattr(rule_obj, "__class__", object), "code", "UNKNOWN"
            )
            issues.append(
                Issue(
                    rule=str(code),
                    severity="hint",
                    message=f"规则执行异常：{err}",
                    location={"page": 1, "pos": 0},
                )
            )

    return order_and_number_issues(doc, issues)


def _strip_list_prefix(message: str) -> str:
    return _LIST_PREFIX_RE.sub("", message or "").strip()


def _infer_title(rule_code: str, message: str) -> str:
    clean = _strip_list_prefix(message)
    if not clean:
        return rule_code or "规则命中"
    if "：" in clean:
        return clean.split("：", 1)[0].strip()[:80] or clean[:80]
    return clean[:80]


def _default_suggestion(rule_code: str, page: Optional[int]) -> str:
    page_hint = f"（第{page}页）" if page else ""
    if rule_code == "CMM-001":
        return (
            f"请逐项核对“三公”表与“其他相关情况说明”金额口径{page_hint}，"
            "尤其确认公务用车运行费是否一致，再统一正文与表格。"
        )
    if rule_code:
        return (
            f"请按 {rule_code} 规则复核原表与说明文字{page_hint}，必要时修订披露口径。"
        )
    return f"请复核原表与说明文字{page_hint}，确认口径与数值一致。"


def _normalize_page(location: Dict[str, Any]) -> Optional[int]:
    raw = location.get("page")
    if raw is None:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def _normalize_bbox(raw_bbox: Any) -> Optional[List[float]]:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    bbox: List[float] = []
    for item in raw_bbox:
        try:
            bbox.append(float(item))
        except Exception:
            return None
    return bbox


def _issue_to_dict(issue: Any, idx: int) -> Dict[str, Any]:
    if isinstance(issue, dict):
        rule_code = str(issue.get("rule_id") or issue.get("rule") or "").strip()
        location = (
            issue.get("location") if isinstance(issue.get("location"), dict) else {}
        )
        message = str(issue.get("message") or issue.get("title") or "").strip()
        evidence_list = (
            issue.get("evidence") if isinstance(issue.get("evidence"), list) else []
        )
        bbox = _normalize_bbox(issue.get("bbox"))
    else:
        rule_code = str(getattr(issue, "rule", "") or "").strip()
        location = getattr(issue, "location", None) or {}
        message = str(getattr(issue, "message", "") or "").strip()
        evidence_text = getattr(issue, "evidence_text", None)
        bbox = _normalize_bbox(location.get("bbox"))
        evidence_list = []
        if evidence_text:
            evidence_list.append(
                {
                    "page": _normalize_page(location),
                    "text": str(evidence_text),
                    "text_snippet": str(evidence_text),
                    "bbox": bbox,
                }
            )

    if not rule_code:
        rule_code = "UNKNOWN"

    page = _normalize_page(location)
    title = _infer_title(rule_code, message)
    created_at = int(time.time())

    evidence = [ev for ev in evidence_list if isinstance(ev, dict)]
    if not evidence:
        evidence = [
            {
                "page": page,
                "text": message,
                "text_snippet": _strip_list_prefix(message)[:200],
                "bbox": bbox,
            }
        ]

    suggestion = (
        issue.get("suggestion") if isinstance(issue, dict) else None
    ) or _default_suggestion(rule_code, page)

    data: Dict[str, Any] = {
        "id": f"{rule_code}-{idx}",
        "source": "rule",
        "rule": rule_code,
        "rule_id": rule_code,
        "severity": (
            issue.get("severity")
            if isinstance(issue, dict)
            else getattr(issue, "severity", None)
        )
        or "info",
        "title": title,
        "message": message,
        "evidence": evidence,
        "location": location,
        "bbox": bbox,
        "suggestion": str(suggestion),
        "tags": [rule_code],
        "metrics": {},
        "created_at": created_at,
    }
    return data


def _norm_sev(severity: Optional[str]) -> str:
    value = (severity or "").lower()
    if value in {"error", "err", "fatal", "critical", "high"}:
        return "error"
    if value in {"warn", "warning", "medium", "low"}:
        return "warn"
    return "info"


def build_issues_payload(
    doc: Any,
    use_ai_assist: bool = False,
    report_kind: Optional[str] = None,
) -> Dict[str, Any]:
    raw_list = run_rules(doc, use_ai_assist, report_kind=report_kind)
    items = [_issue_to_dict(item, idx) for idx, item in enumerate(raw_list, start=1)]

    buckets: Dict[str, List[Dict[str, Any]]] = {"error": [], "warn": [], "info": []}
    for item in items:
        bucket = _norm_sev(str(item.get("severity") or ""))
        item["severity"] = bucket
        buckets[bucket].append(item)
    buckets["all"] = items
    return {"issues": buckets}
