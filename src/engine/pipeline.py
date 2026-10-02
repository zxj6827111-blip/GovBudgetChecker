from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.utils.rule_text import default_rule_suggestion, infer_rule_title
from src.utils.provenance import DEFAULT_RULE_SET_VERSION, ENGINE_VERSION

from src.services.document_profile_resolver import (
    resolve_report_kind_from_path,
)

from . import budget_rules as _budget_rules
from . import common_rules as _common_rules
from . import rules_v33 as _final_rules

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
    Issue,
    order_and_number_issues,
)
from .rule_registry import select_rules, select_specific_rules
from .rules_v33 import (
    build_document as _build_document,
)
# re-export for existing callers importing build_document from this module
build_document = _build_document

# Backwards-compatible patch point used by existing callers/tests. The source
# list remains the canonical common-rule family registered by rule_registry.
ALL_COMMON_RULES = _common_rules.ALL_COMMON_RULES
ALL_BUDGET_RULES = _budget_rules.ALL_BUDGET_RULES
FINAL_ALL_RULES = _final_rules.ALL_RULES
_INITIAL_COMMON_RULES = ALL_COMMON_RULES
_INITIAL_BUDGET_RULES = ALL_BUDGET_RULES
_INITIAL_FINAL_RULES = FINAL_ALL_RULES


def _resolve_report_kind(doc: Any, report_kind: Optional[str] = None) -> str:
    """材料文种判定。

    实现已收敛到唯一解析器 ``src/services/document_profile_resolver``：此前
    pipeline / engine_rule_runner / common_rules / api.runtime 各写一份，
    兜底值互相矛盾（本模块默认 unknown、common_rules 默认 final），存在
    "同一 PDF 在不同入口换掉整套专项检查"的风险。

    ``path`` 仍按基名参与判断，不整串路径匹配：仓库目录名
    ``GovBudgetChecker`` 自带 "Budget"，整串匹配会把任何材料都判成预算。
    解析器内部显式按两种路径分隔符取基名，避免 Windows 路径在 Linux 上
    把整串当文件名。
    """
    explicit = str(report_kind or "").strip().lower()
    if explicit in {"budget", "final"}:
        return explicit
    return resolve_report_kind_from_path(
        str(getattr(doc, "path", "") or ""),
        getattr(doc, "page_texts", []) or [],
    )


def _rule_set_for_kind(kind: str) -> List[Any]:
    """按文种选择专项规则集。识别不了文种就不选专项规则，只跑通用规则。"""
    if kind == "budget" and ALL_BUDGET_RULES is not _INITIAL_BUDGET_RULES:
        return ALL_BUDGET_RULES
    if kind == "final" and FINAL_ALL_RULES is not _INITIAL_FINAL_RULES:
        return FINAL_ALL_RULES
    return select_specific_rules(kind)


def _common_rule_set() -> List[Any]:
    """Use registry routing in production; honor legacy monkeypatch hooks in tests."""
    if ALL_COMMON_RULES is not _INITIAL_COMMON_RULES:
        return ALL_COMMON_RULES
    return select_rules("unknown")


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
    resolved_kind = _resolve_report_kind(doc, report_kind)
    # 一次解析、全程消费：把文种结论挂到文档上，规则体（如 CMM-003 的
    # 锚点选择）读同一个值，不再各自用不同输入重新猜。独立验收
    # 2026-09-17 kind_disagreement 反例：显式指定 final、文件名与正文为
    # "部门预算"的材料，主流程判 final，通用规则却按文件名判 budget——
    # 同一份 PDF 在两个环节拿到互斥的检查配置。
    if doc is not None:
        doc.report_kind = resolved_kind

    if rules is not None:
        selected_rules = list(rules)
    else:
        selected_rules = [
            *_rule_set_for_kind(resolved_kind),
            *_common_rule_set(),
        ]
    issues: List[Issue] = []
    outcomes: List[RuleOutcome] = []
    if resolved_kind == "unknown":
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


def _infer_issue_page(
    issue: Any,
    location: Dict[str, Any],
    evidence_list: List[Dict[str, Any]],
    doc: Any = None,
) -> Optional[int]:
    """Resolve a page from explicit refs, or one unique exact table anchor.

    A table label can provide a page only when it maps to exactly one physical
    page. Fuzzy/partial names and multi-page anchors remain unresolved.
    """
    pages = _location_pages(location)
    if pages:
        return pages[0]
    for item in evidence_list:
        item_pages = _location_pages(item)
        if item_pages:
            return item_pages[0]

    table_name = str(location.get("table") or "").strip()
    anchors = getattr(doc, "anchors", None)
    if table_name and "/" not in table_name and isinstance(anchors, dict):
        raw_pages = anchors.get(table_name)
        if isinstance(raw_pages, (list, tuple)):
            anchor_pages = _location_pages({"pages": raw_pages})
            if len(anchor_pages) == 1:
                return anchor_pages[0]

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
        location_level = str(issue.get("location_level") or "unresolved")
        if location_level not in {"exact_cell", "exact_span"}:
            location_level = str(location.get("location_level") or location_level)
        verification_status = str(issue.get("verification_status") or "unverified")
        if verification_status != "verified":
            verification_status = str(location.get("verification_status") or verification_status)
        bbox = (
            _normalize_bbox(issue.get("bbox") or location.get("bbox"))
            if location_level in {"exact_cell", "exact_span"} and verification_status == "verified"
            else None
        )
    else:
        rule_code = str(getattr(issue, "rule", "") or "").strip()
        location = dict(getattr(issue, "location", None) or {})
        message = str(getattr(issue, "message", "") or "").strip()
        evidence_text = getattr(issue, "evidence_text", None)
        location_level = str(getattr(issue, "location_level", None) or "unresolved")
        if location_level not in {"exact_cell", "exact_span"}:
            location_level = str(location.get("location_level") or location_level)
        verification_status = str(getattr(issue, "verification_status", None) or "unverified")
        if verification_status != "verified":
            verification_status = str(location.get("verification_status") or verification_status)
        bbox = (
            _normalize_bbox(location.get("bbox"))
            if location_level in {"exact_cell", "exact_span"} and verification_status == "verified"
            else None
        )
        evidence_list = []
        if evidence_text:
            evidence_list.append(
                {
                    "page": _normalize_page(location),
                    "text": str(evidence_text),
                    "text_snippet": str(evidence_text),
                    "bbox": bbox,
                    "location_level": location_level,
                    "verification_status": verification_status,
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
        if not (
            str(ev.get("location_level") or "") in {"exact_cell", "exact_span"}
            and str(ev.get("verification_status") or "") == "verified"
        ):
            ev.pop("bbox", None)
            ev.setdefault("location_level", "unresolved")
            ev.setdefault("verification_status", "unverified")

    if location_level not in {"exact_cell", "exact_span"} or verification_status != "verified":
        has_region = any(location.get(key) for key in ("table", "section", "row", "field"))
        location_level = "region" if has_region else "unresolved"
        location.pop("bbox", None)
    location["location_level"] = location_level
    location["verification_status"] = verification_status

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
        "location_level": location_level,
        "verification_status": verification_status,
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
    payload = {
        "issues": buckets,
        "rule_execution_summary": summarize_rule_outcomes(outcomes),
    }
    # ZC-03（AC-04）：规则写下的关系级子检查回执随 payload 返回——
    # Document 在子进程内构建，其上的回执必须在这里收割后才传得回来。
    fact_receipts = list(getattr(doc, "fact_receipts", None) or [])
    if fact_receipts:
        payload["fact_receipts"] = fact_receipts
    fact_context = getattr(doc, "fact_context", None)
    if fact_context is not None:
        try:
            payload["fact_context_summary"] = fact_context.summary()
        except Exception:
            payload["fact_context_summary"] = {"error": "summary_failed"}
    return payload


#: MR-3 文档级可读性闸门的覆盖率阈值（与 api 层质量门的 low_page_coverage
#: 语义对齐；规则引擎不 import api，阈值在此常量声明）
_READABILITY_COVERAGE_THRESHOLD = 0.8


def apply_readability_gate(
    payload: Dict[str, Any],
    page_assessment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """MR-3（2026-09-27「查得准」A 档）：扫描件/低覆盖材料的误报闸门。

    要拦的形态是**部分扫描**：材料仍有文本层（文种识别成功、规则真的跑起来），
    但表页被拍成图片——规则基于残缺数据报出 error 级假阳性（缺表/缺章节/勾稽
    不平），属于"解析不可信"而非材料错误。实测口径见
    ``tests/fixtures/scan_sim_mixed_page_data.json``（派生自实际样张：前 8 页保留
    文本、其余栅格化）：**闸门前 9 条 error → 闸门后 0 条 + 1 条转人工**。

    ⚠️ 口径澄清（2026-09-28 实测）：**整份**无文本层时不是这个形态——文种识别会
    失败，引擎只跑通用规则（8 条），本就不产生 error 级假阳性（实测 0 条）。
    所以本闸门不是"无文本层"路径的兜底，而是"有文本但覆盖不足"路径的兜底；
    整份扫描件由文种识别失败 + 质量门负责。两种形态都有测试覆盖。

    本闸门在 page_coverage < 0.8 或存在扫描页时，把 error 级 finding 全量降级为
    一条 manual_review（"材料可读性不足，转人工判读"），原始 finding 压入
    ``readability_gate.suppressed`` 供调试，不进主列表。

    page_assessment 未提供或可读性正常时为 no-op，历史调用方行为不变。
    warn/info 不动：它们本来就不进 fail 口径，且降低可见噪声不是本闸门目标。
    """
    if not isinstance(page_assessment, dict):
        return payload
    try:
        coverage = float(page_assessment.get("page_coverage") or 0.0)
        scanned = int(page_assessment.get("scanned_page_count") or 0)
    except (TypeError, ValueError):
        return payload
    if coverage >= _READABILITY_COVERAGE_THRESHOLD and scanned == 0:
        return payload

    issues = payload.get("issues")
    if not isinstance(issues, dict):
        return payload
    error_items = issues.get("error") or []
    if not error_items:
        return payload

    issues["error"] = []
    gate = payload.setdefault(
        "readability_gate",
        {
            "page_coverage": coverage,
            "scanned_page_count": scanned,
            "suppressed": [],
        },
    )
    gate["suppressed"].extend(error_items)
    suppressed_ids = {item.get("id") for item in error_items if isinstance(item, dict)}

    manual_item: Dict[str, Any] = {
        "id": "READABILITY-GATE-1",
        "source": "rule",
        "rule": "READABILITY-GATE",
        "rule_id": "READABILITY-GATE",
        # 与 DOC-TYPE-UNKNOWN 同口径：manual_review 归一进 warn 分桶
        "severity": "manual_review",
        "title": "材料可读性不足，检查结果要求人工判读",
        "message": (
            f"材料文本覆盖率 {coverage:.0%}（扫描页 {scanned} 页）低于可信阈值，"
            f"规则基于残缺文本产生的 {len(error_items)} 条错误级结论已整体转人工复核，"
            "不作为材料问题呈现。建议补充可读文本层后重新检查。"
        ),
        "evidence": [],
        "location": {"page": 1, "pos": 0},
        "bbox": None,
        "suggestion": "人工核对原件；如需系统复审请提供带文本层的 PDF。",
        "tags": ["READABILITY-GATE"],
        "metrics": {"suppressed_error_count": len(error_items)},
        "created_at": int(time.time()),
        "rule_version": DEFAULT_RULE_SET_VERSION,
        "model_version": None,
        "prompt_version": None,
        "engine_version": ENGINE_VERSION,
        "suppressed_issue_ids": sorted(x for x in suppressed_ids if x),
    }
    issues.setdefault("warn", []).append(manual_item)
    return payload
