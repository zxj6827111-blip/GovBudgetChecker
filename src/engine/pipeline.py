from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.utils.rule_text import default_rule_suggestion, infer_rule_title
from src.utils.provenance import DEFAULT_RULE_SET_VERSION, ENGINE_VERSION

from .budget_rules import ALL_BUDGET_RULES
from .common_rules import ALL_COMMON_RULES
from .rule_outcome import (
    RuleDeferred,
    RuleNotApplicable,
    RuleOutcome,
    RuleOutcomeSignal,
    STATUS_EXECUTION_ERROR,
    STATUS_FAIL,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PASS,
    resolve_rule_status,
    summarize_rule_outcomes,
)
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
# re-export for existing callers importing build_document from this module
build_document = _build_document


def _resolve_report_kind(doc: Any, report_kind: Optional[str] = None) -> str:
    kind = (report_kind or "").strip().lower()
    if kind in {"budget", "final"}:
        return kind

    # The repository path itself contains "GovBudgetChecker".  Detect only
    # from the uploaded filename, otherwise every document can be routed to
    # the budget rule set before its content is considered.
    #
    # NOTE: ``Path(...).name`` is host-OS dependent.  On Linux a
    # Windows-style path such as ``E:\dir\plain.pdf`` has no path separator,
    # so ``Path.name`` returns the whole string (which contains "Budget" via
    # the repository directory) and misroutes the document.  Split on both
    # separators explicitly so basename extraction is platform independent.
    path = str(getattr(doc, "path", "") or "")
    filename = re.split(r"[\\/]", path)[-1] if path else ""
    lowered = filename.lower()
    if "budget" in lowered or "预算" in filename:
        return "budget"
    if "final" in lowered or "决算" in filename:
        return "final"

    page_texts = getattr(doc, "page_texts", []) or []
    first_text = page_texts[0] if page_texts else ""
    if "预算" in first_text:
        return "budget"
    if "决算" in first_text:
        return "final"
    return "unknown"


def _select_rule_set(doc: Any, report_kind: Optional[str] = None) -> List[Any]:
    kind = _resolve_report_kind(doc, report_kind)
    if kind == "budget":
        return ALL_BUDGET_RULES
    if kind == "final":
        return FINAL_ALL_RULES
    return []


def run_rules(
    doc: Any, use_ai_assist: bool = False, report_kind: Optional[str] = None
) -> List[Issue]:
    issues, _ = run_rules_with_outcomes(doc, use_ai_assist, report_kind=report_kind)
    return issues


def run_rules_with_outcomes(
    doc: Any,
    use_ai_assist: bool = False,
    report_kind: Optional[str] = None,
    rules: Optional[List[Any]] = None,
) -> "Tuple[List[Issue], List[RuleOutcome]]":
    """执行规则并返回 (issues, outcomes)。

    outcomes 是逐规则的 RuleOutcome 记录。在 Contract C1 契约下：
    - 规则返回的 issue 无论规则最终状态是 fail 还是因部分检查未完成记为
      insufficient_data/parse_error，已确认的 finding 均保留在 issues 中，
      不能被丢弃或隐藏；
    - 规则聚合状态按优先级判定（execution_error > parse_error > insufficient_data > fail > not_applicable > pass），
      部分子检查 not_applicable 绝不能覆盖已确认的 fail；
    - 未完成状态供质量门 fail-closed 判定与回放评测消费。
    """
    if rules is not None:
        selected_rules = list(rules)
    else:
        selected_rules = [
            *_select_rule_set(doc, report_kind=report_kind),
            *ALL_COMMON_RULES,
        ]
    issues: List[Issue] = []
    outcomes: List[RuleOutcome] = []
    if _resolve_report_kind(doc, report_kind) == "unknown":
        issues.append(
            Issue(
                rule="DOC-TYPE-UNKNOWN",
                severity="manual_review",
                message="未能可靠识别材料为预算或决算，已仅执行通用规则，请人工确认材料类型后重新检查。",
                location={"page": 1, "pos": 0},
            )
        )

    for rule_obj in selected_rules:
        code = getattr(rule_obj, "code", None) or getattr(
            getattr(rule_obj, "__class__", object), "code", "UNKNOWN"
        )
        try:
            rule = rule_obj() if isinstance(rule_obj, type) else rule_obj
            if hasattr(rule, "apply_with_ai") and use_ai_assist:
                produced = rule.apply_with_ai(doc, use_ai_assist)
            else:
                produced = rule.apply(doc)
            produced_list = list(produced or [])
        except RuleOutcomeSignal as signal:
            # 非 fail 结局（insufficient_data / not_applicable 等）：
            # 若有已确认的 partial_issues，保留为正式 finding（合入原始 Issue 列表）
            partial_list = list(getattr(signal, "partial_issues", []) or [])
            if partial_list:
                issues.extend(partial_list)
            detail_str = str(signal.detail or signal)
            unresolved = list(getattr(signal, "unresolved_reasons", []) or [])
            if not unresolved and detail_str:
                unresolved = [detail_str]
            outcome_status = resolve_rule_status(
                base_status=signal.status,
                has_findings=bool(partial_list),
            )
            outcomes.append(
                RuleOutcome(
                    rule_id=str(code),
                    status=outcome_status,
                    detail=detail_str,
                    partial_findings_count=len(partial_list),
                    unresolved_reasons=unresolved,
                )
            )
            continue
        except Exception as err:
            # 规则代码异常：同样不产出"规则执行异常"伪装 finding，进运行摘要
            outcomes.append(
                RuleOutcome(
                    rule_id=str(code),
                    status=STATUS_EXECUTION_ERROR,
                    detail=f"{type(err).__name__}: {err}",
                )
            )
            continue
        issues.extend(produced_list)
        outcomes.append(
            RuleOutcome(
                rule_id=str(code),
                status=STATUS_FAIL if produced_list else STATUS_PASS,
            )
        )

    return order_and_number_issues(doc, issues), outcomes


def _strip_list_prefix(message: str) -> str:
    return re.sub(r"^\s*[一二三四五六七八九十\d]+[、.)]\s*", "", message or "").strip()


def _infer_title(rule_code: str, message: str) -> str:
    return infer_rule_title(rule_code, message)


def _default_suggestion(rule_code: str, page: Optional[int]) -> str:
    return default_rule_suggestion(rule_code, page)


def _normalize_page(location: Dict[str, Any]) -> Optional[int]:
    """Return the first valid page carried by a location/evidence object.

    规则定位长期同时存在 ``page``、``pages`` 和 ``table_refs[*].page``
    三种形态。统一输出以前只读取 ``page``，导致已经具备页码证据的
    跨表 finding 被错误标成 ``missing_page``。这里只消费已有的显式页码，
    不把任意数值字段（例如 ``t1``/``diff``）当成页码。
    """
    pages = _location_pages(location)
    return pages[0] if pages else None


def _location_pages(location: Dict[str, Any]) -> List[int]:
    """Collect explicit page references without inventing a location."""
    values: List[Any] = [location.get("page")]
    raw_pages = location.get("pages")
    if isinstance(raw_pages, (list, tuple)):
        values.extend(raw_pages)
    raw_refs = location.get("table_refs")
    if isinstance(raw_refs, (list, tuple)):
        for ref in raw_refs:
            if not isinstance(ref, dict):
                continue
            values.append(ref.get("page"))
            ref_pages = ref.get("pages")
            if isinstance(ref_pages, (list, tuple)):
                values.extend(ref_pages)

    pages: List[int] = []
    seen = set()
    for raw in values:
        if isinstance(raw, bool):
            continue
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page <= 0 or page in seen:
            continue
        seen.add(page)
        pages.append(page)
    return pages


def _anchor_page_for_table(doc: Any, table_name: Any) -> Optional[int]:
    """Resolve a table location against the document's existing anchors.

    A table name must match an existing anchor key after whitespace
    normalization. If no anchor exists, the page remains unknown rather than
    being guessed from an unrelated numeric field.
    """
    anchors = getattr(doc, "anchors", None)
    if not isinstance(anchors, dict):
        return None
    target = re.sub(r"\s+", "", str(table_name or ""))
    if not target:
        return None
    candidates: List[int] = []
    for anchor_name, raw_pages in anchors.items():
        normalized = re.sub(r"\s+", "", str(anchor_name or ""))
        if not normalized or not (
            target == normalized or target in normalized or normalized in target
        ):
            continue
        if not isinstance(raw_pages, (list, tuple)):
            continue
        for raw_page in raw_pages:
            if isinstance(raw_page, bool):
                continue
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if page > 0:
                candidates.append(page)
    return min(candidates) if candidates else None


_RULE_TABLE_HINTS: Dict[str, Tuple[str, ...]] = {
    # 这些规则的 finding 可能只带业务字段，但规则本身有明确的表域；
    # 只有在文档锚点已存在时才用锚点补页码。
    "V33-200": ("收入支出决算总表", "收入决算表"),
    "V33-201": ("收入支出决算总表", "支出决算表"),
    "V33-244": ('一般公共预算财政拨款“三公”经费支出决算表',),
}


def _infer_issue_page(
    issue: Any,
    location: Dict[str, Any],
    evidence_list: List[Dict[str, Any]],
    doc: Any = None,
) -> Optional[int]:
    """Resolve a primary page from explicit finding evidence only."""
    pages = _location_pages(location)
    if pages:
        return pages[0]
    for item in evidence_list:
        item_pages = _location_pages(item)
        if item_pages:
            return item_pages[0]

    table_names: List[str] = []
    table = location.get("table")
    if isinstance(table, str):
        table_names.extend(part.strip() for part in table.split(" / ") if part.strip())
    rule_code = str(
        (issue.get("rule_id") or issue.get("rule") or "")
        if isinstance(issue, dict)
        else (getattr(issue, "rule", "") or "")
    ).strip()
    table_names.extend(_RULE_TABLE_HINTS.get(rule_code, ()))
    for table_name in table_names:
        page = _anchor_page_for_table(doc, table_name)
        if page is not None:
            return page
    return None


def _normalize_bbox(raw_bbox: Any) -> Optional[List[float]]:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    bbox: List[float] = []
    for item in raw_bbox:
        try:
            value = float(item)
        except Exception:
            return None
        if not math.isfinite(value):
            return None
        bbox.append(value)
    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        return None
    return bbox


def _issue_to_dict(issue: Any, idx: int, doc: Any = None) -> Dict[str, Any]:
    if isinstance(issue, dict):
        rule_code = str(issue.get("rule_id") or issue.get("rule") or "").strip()
        location = (
            dict(issue.get("location"))
            if isinstance(issue.get("location"), dict)
            else {}
        )
        message = str(issue.get("message") or issue.get("title") or "").strip()
        evidence_list = (
            issue.get("evidence") if isinstance(issue.get("evidence"), list) else []
        )
        bbox = _normalize_bbox(issue.get("bbox"))
    else:
        rule_code = str(getattr(issue, "rule", "") or "").strip()
        location = dict(getattr(issue, "location", None) or {})
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

    page = _infer_issue_page(issue, location, evidence_list, doc)
    if page is not None and _normalize_page({"page": location.get("page")}) is None:
        # 只在统一页字段缺失时补写主页；原始跨页集合不被覆盖。
        location["page"] = page
    title = _infer_title(rule_code, message)
    created_at = int(time.time())

    evidence = [dict(ev) for ev in evidence_list if isinstance(ev, dict)]
    for ev in evidence:
        if _normalize_page(ev) is None and page is not None:
            ev["page"] = page
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
        # 版本留痕（P2-02）：legacy 流水线没有可配置的规则版本入口，
        # 固定使用 rules_v33/budget_rules/common_rules 这套规则模块，
        # 因此如实记录为对应的规则集版本；不涉及 AI，模型与提示词版本留空。
        "rule_version": DEFAULT_RULE_SET_VERSION,
        "model_version": None,
        "prompt_version": None,
        "engine_version": ENGINE_VERSION,
    }
    return data


def _norm_sev(severity: Optional[str]) -> str:
    value = (severity or "").lower()
    if value in {"error", "err", "fatal", "critical", "high"}:
        return "error"
    if value in {"warn", "warning", "medium", "low", "manual_review"}:
        return "warn"
    return "info"


def build_issues_payload(
    doc: Any,
    use_ai_assist: bool = False,
    report_kind: Optional[str] = None,
    rules: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    raw_list, outcomes = run_rules_with_outcomes(
        doc, use_ai_assist, report_kind=report_kind, rules=rules
    )
    items = [
        _issue_to_dict(item, idx, doc)
        for idx, item in enumerate(raw_list, start=1)
    ]

    buckets: Dict[str, List[Dict[str, Any]]] = {"error": [], "warn": [], "info": []}
    for item in items:
        bucket = _norm_sev(str(item.get("severity") or ""))
        item["severity"] = bucket
        buckets[bucket].append(item)
    buckets["all"] = items
    # 规则执行摘要（增量字段，向后兼容）：insufficient/parse/execution_error
    # 不再伪装成 finding，质量门据此 fail-closed
    return {
        "issues": buckets,
        "rule_execution_summary": summarize_rule_outcomes(outcomes),
    }
