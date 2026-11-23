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
    STATUS_PARSE_ERROR,
    STATUS_PASS,
    resolve_rule_status,
    summarize_rule_outcomes,
)
from .rules_v33 import (
    Issue,
    order_and_number_issues,
)
from .rule_registry import select_rules, select_specific_rules
from .rule_deadline import RuleItemTimeout, rule_item_deadline
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
    profile_kind = getattr(getattr(doc, "document_profile", None), "kind", None)
    if profile_kind in {"unknown", "non_budget"} or (profile_kind in {"budget", "final"} and explicit in {"", "auto", "unknown"}):
        return profile_kind
    if explicit in {"budget", "final"}:
        return explicit
    return resolve_report_kind_from_path(
        str(getattr(doc, "path", "") or ""),
        getattr(doc, "page_texts", []) or [],
    )


def _rule_set_for_kind(kind: str) -> List[Any]:
    """按文种选择专项规则；未知文种保留不依赖文种的原格数值检查。"""
    if kind == "budget" and ALL_BUDGET_RULES is not _INITIAL_BUDGET_RULES:
        return ALL_BUDGET_RULES
    if kind == "final" and FINAL_ALL_RULES is not _INITIAL_FINAL_RULES:
        return FINAL_ALL_RULES
    return select_specific_rules(kind)


def _common_rule_set() -> List[Any]:
    """Use registry routing in production; honor legacy monkeypatch hooks in tests."""
    if ALL_COMMON_RULES is not _INITIAL_COMMON_RULES:
        return ALL_COMMON_RULES
    return list(_common_rules.ALL_COMMON_RULES)


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

    if resolved_kind == "final":
        from src.services.document_profile_resolver import resolve_document_profile
        from src.services.material_writing import ensure_document_resolution
        profile = getattr(doc, 'document_profile', None) or resolve_document_profile(
            path=str(getattr(doc, 'path', '') or ''), page_texts=getattr(doc, 'page_texts', []) or [])
        ensure_document_resolution(doc, profile)

    if rules is not None:
        selected_rules = list(rules)
    else:
        selected_rules = [
            *_rule_set_for_kind(resolved_kind),
            *_common_rule_set(),
        ]
        from .rule_registry import restrict_rules_to_profile
        from src.services.document_profile_resolver import resolve_document_profile
        profile = getattr(doc, 'document_profile', None) or resolve_document_profile(
            path=str(getattr(doc, 'path', '') or ''), page_texts=getattr(doc, 'page_texts', []) or [])
        selected_rules = restrict_rules_to_profile(selected_rules, profile)
    from src.services.material_writing import budget_enabled
    if rules is None and resolved_kind != "final" and not (resolved_kind=="budget" and budget_enabled()):
        selected_rules = [r for r in selected_rules if r.code != "CMM-027"]
    issues: List[Issue] = []
    outcomes: List[RuleOutcome] = []
    if resolved_kind in {"unknown", "non_budget"}:
        issues.append(
            Issue(
                rule="DOC-TYPE-UNKNOWN",
                severity="manual_review",
                message=("非预决算材料，需人工确认；未运行预算/决算规则集。" if resolved_kind == "non_budget" else
                         "未能可靠识别材料为预算或决算，已仅执行通用规则，请人工确认材料类型后重新检查。"),
                location={"page": 1, "pos": 0},
            )
        )

    receipt_start = len(getattr(doc, "fact_receipts", None) or [])
    for rule_obj in selected_rules:
        code = getattr(rule_obj, "code", None) or getattr(
            getattr(rule_obj, "__class__", object), "code", "UNKNOWN"
        )
        try:
            doc.current_rule_code = code
            rule = rule_obj() if isinstance(rule_obj, type) else rule_obj
            from .rule_identity import rule_identity_requirements
            with rule_item_deadline(doc), rule_identity_requirements(doc, code):
                if hasattr(rule, "apply_with_ai") and use_ai_assist:
                    produced = rule.apply_with_ai(doc, use_ai_assist)
                else:
                    produced = rule.apply(doc)
                produced_list = list(produced or [])
        except RuleItemTimeout as error:
            outcomes.append(RuleOutcome(
                rule_id=str(code), status=STATUS_EXECUTION_ERROR, detail=str(error),
                unresolved_reasons=[str(error)], reason_codes=["execution_error"],
            ))
            continue
        except RuleOutcomeSignal as signal:
            # 非 fail 结局（insufficient_data / not_applicable 等）：
            # 若有已确认的 partial_issues，保留为正式 finding（合入原始 Issue 列表）
            partial_list = list(getattr(signal, "partial_issues", []) or [])
            if partial_list:
                issues.extend(partial_list)
            detail_str = str(signal.detail or signal)
            # 区分材料格式缺陷与技术映射失败，并登记单元格级结构化事实。
            from .table_admission_policy import reclassify_admission_deferral

            rewritten = reclassify_admission_deferral(doc, str(code), detail_str)
            if rewritten:
                detail_str = rewritten
            unresolved = list(getattr(signal, "unresolved_reasons", []) or [])
            unresolved = [rewritten if rewritten and item == str(signal.detail or signal) else item
                          for item in unresolved]
            if not unresolved and detail_str:
                unresolved = [detail_str]
            outcome_status = resolve_rule_status(
                base_status=signal.status,
                has_findings=bool(partial_list),
            )
            # DH-02：把规则执行期间登记的失败事实转成结构化卡点；文本保持不变。
            from .diagnostic_fields import diagnostics_for_texts

            diagnostics = diagnostics_for_texts(doc, [detail_str, *unresolved], rule_id=str(code))
            outcomes.append(
                RuleOutcome(
                    rule_id=str(code),
                    status=outcome_status,
                    detail=detail_str,
                    partial_findings_count=len(partial_list),
                    unresolved_reasons=unresolved,
                    reason_codes=list(getattr(signal, "reason_codes", []) or []),
                    diagnostics=diagnostics,
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

    # 关系级回执对账（Codex IR-08 返工第 8 项）：规则可能"没有正式 finding"，
    # 但 doc.fact_receipts 里仍有 insufficient_data / parse_error /
    # execution_error 的关系回执（典型：三公数量回执三项取数不足、规则总状态
    # 仍记 pass）。若聚合状态不升到未完成态，义务台账会按 pass 把整项检查记成
    # 完成——"没检查"被"规则 pass"掩盖。这里按 RuleOutcomeSignal 同一优先级
    # （execution_error > parse_error > insufficient_data）升级状态；已确认的
    # finding 全部保留，不新增也不删除任何问题。
    receipts = list(getattr(doc, "fact_receipts", None) or [])[receipt_start:]
    unresolved_by_rule: Dict[str, List[Dict[str, Any]]] = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        receipt_status = str(receipt.get("status") or "")
        if receipt_status in {
            STATUS_INSUFFICIENT_DATA, STATUS_PARSE_ERROR, STATUS_EXECUTION_ERROR,
        }:
            unresolved_by_rule.setdefault(
                str(receipt.get("rule_id") or ""), []).append(receipt)
    for outcome in outcomes:
        pending = unresolved_by_rule.get(outcome.rule_id) or []
        if not pending or outcome.status in {
            STATUS_INSUFFICIENT_DATA, STATUS_PARSE_ERROR, STATUS_EXECUTION_ERROR,
        }:
            continue
        worst = STATUS_INSUFFICIENT_DATA
        statuses = {str(item.get("status") or "") for item in pending}
        if STATUS_PARSE_ERROR in statuses:
            worst = STATUS_PARSE_ERROR
        if STATUS_EXECUTION_ERROR in statuses:
            worst = STATUS_EXECUTION_ERROR
        outcome.status = worst
        sub_checks = [str(item.get("sub_check_id") or "") for item in pending]
        sub_checks = [item for item in dict.fromkeys(sub_checks) if item]
        reasons = [str(item.get("incomplete_reason") or "") for item in pending]
        reasons = [item for item in dict.fromkeys(reasons) if item]
        suffix = "关系级回执未完成：" + "、".join(sub_checks)
        if reasons:
            suffix += "（" + "；".join(reasons[:3]) + "）"
        outcome.detail = "；".join(
            part for part in (outcome.detail, suffix) if part)
        outcome.unresolved_reasons = list(dict.fromkeys(
            [*outcome.unresolved_reasons, *reasons]))
        for receipt in pending:
            for diagnostic in receipt.get("diagnostics") or []:
                if isinstance(diagnostic, dict):
                    outcome.diagnostics.append(dict(diagnostic))

    from .native_issue_dedup import deduplicate_native_issues
    issues = deduplicate_native_issues(issues, doc)
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
        check_relation_id = str(issue.get("check_relation_id") or "").strip() or None
        fact_ids = [str(value) for value in issue.get("fact_ids", []) if str(value)]
        evidence_ids = [str(value) for value in issue.get("evidence_ids", []) if str(value)]
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
        check_relation_id = str(getattr(issue, "check_relation_id", "") or "").strip() or None
        fact_ids = [str(value) for value in (getattr(issue, "fact_ids", None) or []) if str(value)]
        evidence_ids = [str(value) for value in (getattr(issue, "evidence_ids", None) or []) if str(value)]
        # IR-03：legacy 序列化与双模式共用同一证据构造器——
        # table_refs（含 span+page）逐条成为证据，跨表关系的另一侧原文
        # 不再只落 evidence_text 单条而丢失（Codex 核验宜川 CROSS/TXT-FUND
        # 双侧原文 failed 的根因）。
        from src.engine.issue_evidence import build_issue_evidence_list as _build_ev

        evidence_list = _build_ev(issue, _normalize_page(location) or location.get("page"))
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
    if not rule_code:
        rule_code = "UNKNOWN"

    page = _infer_issue_page(issue, location, evidence_list, doc)
    if page is not None and _normalize_page({"page": location.get("page")}) is None:
        # 只在统一页字段缺失时补写主页；原始跨页集合不被覆盖。
        location["page"] = page
    # 规则可在 location.display_title 固定展示标题；否则从消息推断。
    # 关联核算说明追加到 message 后，不能让标题被说明截断。
    title = str(location.get("display_title") or "").strip() or _infer_title(
        rule_code, message
    )
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

    # 去重前分配的原始身份已用于成员别名。若在合并后按展示顺序重新编号，
    # 某成员 id 可能正好变成另一个当前问题的 id，导致人工决定串到别组。
    # 展示序号仍由 order_and_number_issues 管理，报告身份保留原始唯一编号。
    source_issue_id = str(location.get("pre_group_issue_id") or "")
    report_issue_id = (source_issue_id if re.fullmatch(rf"{re.escape(rule_code)}-[1-9]\d*", source_issue_id)
                       else f"{rule_code}-{idx}")
    data: Dict[str, Any] = {
        "id": report_issue_id,
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
        "check_relation_id": check_relation_id,
        "fact_ids": list(dict.fromkeys(fact_ids)),
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "created_at": created_at,
        # 版本留痕（P2-02）：legacy 流水线没有可配置的规则版本入口，
        # 固定使用 rules_v33/budget_rules/common_rules 这套规则模块，
        # 因此如实记录为对应的规则集版本；不涉及 AI，模型与提示词版本留空。
        "rule_version": DEFAULT_RULE_SET_VERSION,
        "model_version": None,
        "prompt_version": None,
        "engine_version": ENGINE_VERSION,
    }
    for field in ("related_rule_ids", "check_relation_ids", "merged_issue_ids"):
        values = location.get(field)
        if isinstance(values, list) and values:
            data[field] = list(dict.fromkeys(str(value) for value in values))
    data["tags"] = list(dict.fromkeys([rule_code, *(data.get("related_rule_ids") or [])]))
    return data


def _norm_sev(severity: Optional[str]) -> str:
    value = (severity or "").lower()
    if value in {"error", "err", "fatal", "critical", "high"}:
        return "error"
    if value in {"warn", "warning", "medium", "low", "manual_review"}:
        return "warn"
    return "info"


def tag_manual_fill_issues(items: List[Dict[str, Any]], context: Any) -> None:
    """引用了人工补录数据的结论要标明来源，审核时可回到校正记录核对。"""
    manual = {fact.fact_uid for fact in (getattr(context, "facts", None) or []) if fact.extra_dims.get("manual_fill")}
    for item in items:
        used = [uid for uid in item.get("fact_ids") or [] if uid in manual]
        if used:
            item["manual_fill_fact_ids"] = used
            item["tags"] = list(dict.fromkeys([*item.get("tags", []), "人工补录"]))


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

    # Merged findings retain every original relation; each failed receipt
    # points to the retained exported ID after serialization.
    from .native_issue_dedup import link_merged_issue_receipts
    link_merged_issue_receipts(items, getattr(doc, "fact_receipts", None) or [])

    tag_manual_fill_issues(items, getattr(doc, "fact_context", None))

    buckets: Dict[str, List[Dict[str, Any]]] = {"error": [], "warn": [], "info": []}
    for item in items:
        bucket = _norm_sev(str(item.get("severity") or ""))
        # 分桶不抹除待复核语义，否则未完成提示会被计为正式 warn。
        if item.get("severity") != "manual_review":
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
    # 正文金额校正是否真正被规则消费，必须跨进程回传；父进程的 doc 不一定是
    # 规则执行时的同一对象。
    applied_text_corrections = list(
        getattr(doc, "applied_text_correction_ids", None) or []
    )
    if applied_text_corrections:
        payload["applied_text_correction_ids"] = applied_text_corrections
    unapplied_text_corrections = list(
        getattr(doc, "unapplied_text_corrections", None) or []
    )
    if unapplied_text_corrections:
        payload["unapplied_text_corrections"] = unapplied_text_corrections
    # IR-01：交付抑制台账随 payload 返回（规则→问题/合法处理决定可对账）
    suppressions = list(getattr(doc, "delivery_suppression_ledger", None) or [])
    if suppressions:
        payload["delivery_suppressions"] = suppressions
    fact_context = getattr(doc, "fact_context", None)
    payload["structured_consumption"] = list(getattr(doc, "structured_consumption", []) or [])
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
    *,
    fact_receipts=(),
) -> Dict[str, Any]:
    """部分资料未读时，保留证据完整的局部结论，其他错误级结论逐项待核。"""
    if not isinstance(page_assessment, dict):
        return payload
    try:
        coverage = float(page_assessment.get("page_coverage") or 0.0)
        scanned = int(page_assessment.get("scanned_page_count") or 0)
    except (TypeError, ValueError):
        return payload
    recovery = page_assessment.get("ocr_recovery_pending") or {}
    if coverage >= _READABILITY_COVERAGE_THRESHOLD and scanned == 0 and not recovery:
        return payload

    issues = payload.get("issues")
    legacy = isinstance(issues, dict)
    findings = payload.get("rule_findings")
    if legacy:
        findings = issues.get("all")
        if not isinstance(findings, list):
            findings = issues.get("error")
    if not isinstance(findings, list):
        return payload

    unread_pages = set(page_assessment.get("low_text_pages") or ())
    unread_pages.update(page_assessment.get("scanned_pages") or ())
    reading_gaps = (page_assessment.get("table_reading") or {}).get("gaps") or []
    for page in recovery.get("pages") or ():
        if not any(gap.get("page") == page for gap in reading_gaps):
            unread_pages.add(page)

    from src.services.table_reading_assessment import intersects, valid_box

    failed_receipts = {}
    for receipt in fact_receipts:
        if (receipt.get("status") == "fail" and receipt.get("fact_migration_status") == "complete"
                and receipt.get("evaluation_basis") == "fiscal_fact"):
            for issue_id in receipt.get("issue_refs") or ():
                failed_receipts.setdefault(issue_id, []).append(receipt)

    def ref_unaffected(ref):
        page = ref.get("page") or ref.get("physical_page")
        if type(page) is not int or page <= 0 or page in unread_pages:
            return False
        box = valid_box(ref.get("bbox"))
        return not any(
            gap.get("page") == page and (
                not box or not (gap_box := valid_box(gap.get("bbox"))) or intersects(box, gap_box)
            )
            for gap in reading_gaps
        )

    def finding_has_local_proof(finding: Dict[str, Any]) -> bool:
        evidence = finding.get("evidence") or []
        if not evidence or not all(
            isinstance(ref, dict) and ref.get("source_method") == "native_text"
            and ref.get("source_identity_status") == "verified"
            and ref.get("verification_status") == "verified"
            and ref.get("location_level") in {"exact_span", "exact_cell"}
            and ref_unaffected(ref)
            for ref in evidence
        ):
            return False
        rule_id = finding.get("rule_id") or finding.get("rule")
        if rule_id == "V33-004":
            return True  # 表内百分比越界只依赖这一格。

        # 勾稽结论还须确认本次执行用到的每个金额原格和认证空白格。
        location = finding.get("location") or {}
        receipts = failed_receipts.get(finding.get("id"), ())
        if finding.get("source_binding_status") != "bound" or not receipts:
            return False
        blank_refs = location.get("native_blank_sources") or []
        if any(not ref_unaffected(ref) for ref in blank_refs):
            return False
        fact_ids = {ref.get("fact_uid") for ref in evidence if ref.get("location_level") == "exact_cell"}
        blank_ids = {ref.get("source_cell_id") for ref in blank_refs}
        tables = {location.get("table_code"), *(ref.get("table_code") for ref in evidence)}
        tables.discard(None)
        used_cells = blank_ids | {ref.get("source_cell_id") for ref in evidence}
        # 能定位到单格的缺口只影响用到该格的关系；整表缺口仍须保守处理。
        if not tables or any(
            gap.get("table_code") in tables
            and (not gap.get("source_cell_id") or gap["source_cell_id"] in used_cells)
            for gap in reading_gaps
        ):
            return False
        return all(
            receipt.get("rule_id") == rule_id
            and bool(operands := set(receipt.get("operand_fact_ids") or []))
            and set(receipt.get("required_fact_ids") or []) <= operands <= fact_ids
            and all(observation.get("source_cell_id") in blank_ids
                    for observation in receipt.get("cell_observations") or ())
            for receipt in receipts
        )

    downgraded = [
        finding for finding in findings
        if isinstance(finding, dict)
        and str(finding.get("severity") or "").lower() in {"error", "high", "critical"}
        and not finding_has_local_proof(finding)
    ]
    if not downgraded:
        return payload

    payload["readability_gate"] = {
        "page_coverage": coverage,
        "scanned_page_count": scanned,
        "suppressed": [dict(finding) for finding in downgraded],
    }
    for finding in downgraded:
        finding["original_severity"] = finding["severity"]
        finding["severity"] = "manual_review"
        finding["why_not"] = "材料有未完成读取的内容，需核对原件后确认此项。"
        finding["tags"] = [*(finding.get("tags") or []), "READABILITY-GATE"]
    if legacy:
        downgraded_ids = {finding.get("id") for finding in downgraded if finding.get("id")}
        downgraded_objects = {id(finding) for finding in downgraded}
        issues["error"] = [
            item for item in (issues.get("error") or [])
            if id(item) not in downgraded_objects and item.get("id") not in downgraded_ids
        ]
        issues.setdefault("warn", []).extend(downgraded)
    return payload
