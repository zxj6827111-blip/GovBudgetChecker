"""Canonical versioned analysis report shared by status and exports."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from src.schemas.result_semantics import (
    CAUSE_EXECUTION_FAILURE,
    CAUSE_EXTRACTION_FAILURE,
    CAUSE_IDENTITY_CONFLICT,
    CAUSE_LEGACY_UNKNOWN,
    annotate_diagnostic,
    processing_summary as build_processing_summary,
    review_summary as build_review_summary,
)


@dataclass
class ReviewReport:
    schema_version: int
    job_id: str
    analysis_revision: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    conclusion: Dict[str, Any] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    tips: List[Dict[str, Any]] = field(default_factory=list)
    manual_review: List[Dict[str, Any]] = field(default_factory=list)
    check_receipts: List[Dict[str, Any]] = field(default_factory=list)
    obligation_coverage: Any = None
    human_decisions: List[Dict[str, Any]] = field(default_factory=list)
    merge_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    candidate_findings: List[Dict[str, Any]] = field(default_factory=list)
    #: R2-06：结构化交付抑制台账（规则→政策/关系/理由），读取与导出可对账。
    delivery_suppressions: List[Dict[str, Any]] = field(default_factory=list)
    #: schema v3：技术诊断与执行/覆盖状态。诊断只作内部维护与材料层汇总，
    #: 不进入业务问题队列，也不要求用户逐条表态。
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    processing_summary: Dict[str, Any] = field(default_factory=dict)
    review_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewReport":
        return cls(
            schema_version=int(payload.get("schema_version") or 2),
            job_id=str(payload.get("job_id") or ""),
            analysis_revision=payload.get("analysis_revision"),
            metadata=_mapping(payload.get("metadata")),
            conclusion=_mapping(payload.get("conclusion")),
            issues=[normalize_issue_source_page(item) for item in _mapping_list(payload.get("issues"))],
            tips=[normalize_issue_source_page(item) for item in _mapping_list(payload.get("tips"))],
            manual_review=[normalize_manual_review(item) for item in _mapping_list(payload.get("manual_review"))],
            check_receipts=_mapping_list(payload.get("check_receipts")),
            obligation_coverage=payload.get("obligation_coverage"),
            human_decisions=_mapping_list(payload.get("human_decisions")),
            merge_conflicts=_mapping_list(payload.get("merge_conflicts")),
            candidate_findings=_mapping_list(payload.get("candidate_findings")),
            delivery_suppressions=_mapping_list(payload.get("delivery_suppressions")),
            diagnostics=_mapping_list(payload.get("diagnostics")),
            processing_summary=_mapping(payload.get("processing_summary")),
            review_summary=_mapping(payload.get("review_summary")),
        )


def normalize_manual_review(item: Mapping[str, Any]) -> Dict[str, Any]:
    """One title contract for JSON, CSV and visible reports, including legacy reasons."""
    normalized = dict(item)
    title = str(item.get("title") or item.get("group_title") or item.get("code")
                or item.get("obligation_id") or "待人工复核")
    normalized["title"] = title
    normalized["display_title"] = str(item.get("group_title") or title)
    return normalized


def normalize_issue_source_page(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep the primary source page in canonical JSON as well as visible exports.

    This only copies an existing page reference. It does not verify the source,
    assign a bbox, or change a candidate's evidence/business status.
    """
    normalized = dict(item)
    location = _mapping(item.get('location'))
    if item.get('page_number') or item.get('page') or location.get('page'):
        return normalized
    refs = _mapping_list(location.get('table_refs')) + _mapping_list(item.get('evidence'))
    pages = [ref.get('page') or ref.get('physical_page') for ref in refs]
    pages += location.get('pages', []) if isinstance(location.get('pages'), list) else []
    page = next((p for p in pages if type(p) is int and p > 0), None)
    if page is not None:
        normalized['page_number'] = page
    return normalized


def review_report_counts(report: Mapping[str, Any], ignored_issue_ids=()) -> Dict[str, int]:
    """Count source-verified material findings separately from candidates and notices.

    A finding is part of the issue count only after its original source identity has
    been verified. Incomplete evidence remains visible as a manual-review candidate,
    while informational/rounding findings remain tips.
    """
    from src.services.evidence_guard import is_formal_finding

    ignored = {str(value) for value in ignored_issue_ids}
    def visible(items):
        seen, selected = set(), []
        for item in _mapping_list(items):
            identity = str(item.get("id") or "")
            if identity and (identity in ignored or identity in seen):
                continue
            seen.add(identity)
            selected.append(item)
        return selected
    issues, tips = visible(report.get("issues")), visible(report.get("tips"))
    candidate_records = [item for item in visible(report.get("manual_review"))
                         if item.get("record_type") == "finding_review_candidate"]
    candidates = [
        item for item in candidate_records
        if str(_mapping(item.get("finding")).get("evidence_status") or "").strip().lower()
        != "degraded_missing_evidence"
    ]
    notices = [item for item in visible(report.get("manual_review"))
               if item.get("record_type", "review_reason") not in {"unfinished_check", "finding_review_candidate"}]
    verified_issues = [item for item in issues if _is_confirmed_material_finding(item)]
    # formal_issue_total retains the evidence_guard contract, including legacy
    # findings that lack source-verification metadata. Those rows remain
    # candidates in the visible report until their source identity is bound.
    formal_sources = list(issues)
    formal_sources.extend(
        _mapping(item.get("finding"))
        for item in candidate_records
        if isinstance(item.get("finding"), Mapping)
    )
    formal_sources.extend(
        _mapping(item.get("finding"))
        for item in _mapping_list(report.get("candidate_findings"))
        if isinstance(item.get("finding"), Mapping)
    )
    unique_formal_sources, seen_formal_ids = [], set()
    for item in formal_sources:
        identity = str(item.get("id") or "")
        if identity and (identity in ignored or identity in seen_formal_ids):
            continue
        if identity:
            seen_formal_ids.add(identity)
        unique_formal_sources.append(item)
    return {"review_issue_total": len(verified_issues),
            "review_candidate_total": len(candidates), "tip_total": len(tips),
            "review_notice_total": len(notices),
            "formal_issue_total": sum(is_formal_finding(item) for item in unique_formal_sources)}


_WORKBENCH_FORMAL_SEVERITIES = {"critical", "fatal", "error", "high", "warn", "warning", "medium", "low"}


def review_queue_counts(report: Mapping[str, Any], ignored_issue_ids=()) -> Dict[str, int]:
    """Counts for the review workbench's two queues, so the job list shows the same numbers.

    必须处理 holds formal findings (``is_formal_finding``, the completion-gate rule);
    参考 holds every other visible row, split like the workbench's 结果类型 filter into
    待核实 / 证据不足 / 提示 / 复核事项. Rows are taken and de-duplicated in the same
    order as the frontend ``toUiProblems`` (issues, tips, then manual-review records).
    """
    from src.services.evidence_guard import EVIDENCE_STATUS_DEGRADED

    ignored = {str(value) for value in ignored_issue_ids}
    seen: set[str] = set()
    counts = {"required": 0, "candidate": 0, "degraded": 0, "tip": 0, "notice": 0}

    def take(identity: str) -> bool:
        if identity and (identity in ignored or identity in seen):
            return False
        if identity:
            seen.add(identity)
        return True

    def degraded(item: Mapping[str, Any]) -> bool:
        return str(item.get("evidence_status") or "").strip() == EVIDENCE_STATUS_DEGRADED

    for item in [*_mapping_list(report.get("issues")), *_mapping_list(report.get("tips"))]:
        if not take(str(item.get("id") or "")):
            continue
        review_kind = str(item.get("review_kind") or "")
        # 前端 normalizeSeverityCode 把未知或空级别归为提示，这里同样处理，
        # 否则同一行在列表算“必须处理”、在工作台算“提示”。
        severity = str(item.get("severity") or "").strip().lower()
        if degraded(item):
            counts["degraded"] += 1
        elif severity in _WORKBENCH_FORMAL_SEVERITIES:
            counts["required"] += 1
        elif review_kind == "review_notice" or str(item.get("source") or "") == "structured_ingest":
            counts["notice"] += 1
        elif review_kind != "manual_review" and severity != "manual_review":
            counts["tip"] += 1
        else:
            counts["candidate"] += 1
    for index, item in enumerate(_mapping_list(report.get("manual_review"))):
        record_type = str(item.get("record_type") or "review_reason")
        if record_type == "unfinished_check":
            continue
        identity = str(item.get("id") or f"{record_type}:{item.get('code') or item.get('obligation_id') or index}")
        if not take(identity):
            continue
        if record_type in {"finding_review_candidate", "ai_candidate"}:
            counts["degraded" if degraded(_mapping(item.get("finding"))) else "candidate"] += 1
        else:
            counts["notice"] += 1
    reference = counts["candidate"] + counts["degraded"] + counts["tip"] + counts["notice"]
    return {"queue_required_total": counts["required"], "queue_reference_total": reference,
            "queue_candidate_total": counts["candidate"], "queue_degraded_total": counts["degraded"],
            "queue_tip_total": counts["tip"], "queue_notice_total": counts["notice"]}


def _is_confirmed_material_finding(item: Mapping[str, Any]) -> bool:
    """Only count non-informational findings with a verified source and full binding."""
    severity = str(item.get("severity") or "").strip().lower()
    if severity in {"info", "manual_review"}:
        return False
    verification = _mapping(item.get("evidence_verification"))
    return (
        str(item.get("evidence_status") or "").strip().lower() in {"complete", "verified"}
        and str(verification.get("status") or "").strip().lower() == "verified"
        and verification.get("source_identity_complete") is True
    )


def _is_rounding_hint(item: Mapping[str, Any]) -> bool:
    """Explicitly detected display-rounding residuals belong in tips, not candidates."""
    message = str(item.get("message") or "")
    return "可能为显示取整误差" in message or "在默认金额容差内，可能为取整误差" in message


def build_review_report(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the canonical report from a terminal job payload without guessing."""
    job_id = str(payload.get("job_id") or "")
    result = _mapping(payload.get("result"))
    meta = _mapping(result.get("meta"))
    source_issues = [normalize_issue_source_page(item) for item in _collect_findings(result)]
    tips = [
        item
        for item in source_issues
        if str(item.get("severity") or "").strip().lower() == "info" or _is_rounding_hint(item)
    ]
    confirmed_issues = [
        item
        for item in source_issues
        if _is_confirmed_material_finding(item)
    ]
    def relation_ids(item: Mapping[str, Any]) -> set[str]:
        location = _mapping(item.get("location"))
        declared = item.get("check_relation_ids")
        located = location.get("check_relation_ids")
        return {
            str(value) for value in [item.get("check_relation_id"),
                                    *(declared if isinstance(declared, list) else []),
                                    *(located if isinstance(located, list) else [])]
            if value
        }

    verified_basic_relations = {
        relation for item in confirmed_issues
        if str(item.get("rule_id") or item.get("rule") or "").upper() == "V33-117"
        for relation in relation_ids(item)
    }
    # Summary-to-class and class-to-detail checks are independent even on
    # the same page. Suppress a detail tip only when its explicit relations
    # have already been retained in a verified summary finding.
    tips = [
        item for item in tips
        if not (
            str(item.get("rule_id") or item.get("rule") or "").upper() == "V33-243"
            and relation_ids(item)
            and relation_ids(item).issubset(verified_basic_relations)
        )
    ]
    profile = _mapping(payload.get("document_profile") or meta.get("document_profile"))
    report_kind_field = _mapping(profile.get("report_kind"))
    year_field = _mapping(profile.get("report_year"))
    organization_field = _mapping(profile.get("organization_name"))
    report_kind = str(payload.get("report_kind") or report_kind_field.get("value") or "") or None
    organization = organization_field.get("value")

    coverage = payload.get("check_coverage") or meta.get("obligation_coverage")
    coverage_dict = _mapping(coverage)
    merged = _mapping(result.get("merged"))
    candidate_ids = {
        str(item).strip()
        for item in merged.get("candidate_issue_ids", [])
        if str(item or "").strip()
    } if isinstance(merged.get("candidate_issue_ids"), list) else set()
    # 全局处理原因进入 diagnostics；业务参考区只保留具体候选/冲突，
    # 不再生成“系统提示”式泛化卡片。
    manual_review: List[Dict[str, Any]] = []
    for item in source_issues:
        severity = str(item.get("severity") or "").strip().lower()
        if severity == "info" or _is_rounding_hint(item) or _is_confirmed_material_finding(item):
            continue
        issue_id = str(item.get("id") or f"finding-candidate:{len(manual_review) + 1}")
        # AI-only 候选统一由下方 ai_candidate 记录承载；这里再生成
        # finding_review_candidate 会让同一候选在页面上重复出现两次。
        if str(item.get("source") or "") == "ai" and issue_id in candidate_ids:
            continue
        manual_review.append({
            "record_type": "finding_review_candidate",
            "id": issue_id,
            "code": item.get("rule_id") or item.get("rule") or "",
            "title": item.get("title") or item.get("rule_name") or "材料发现待核实",
            "message": item.get("message") or "该项尚未完成原件证据绑定，不能确认为实质问题。",
            "suggestion": item.get("suggestion") or "请核对原始 PDF 对应页后再确认。",
            "finding": item,
        })
    merge_conflicts = _mapping_list(merged.get("conflicts"))
    source_by_id = {
        str(item.get("id") or ""): item
        for key in ("ai_findings", "rule_findings")
        for item in _mapping_list(result.get(key))
        if item.get("id")
    }
    candidate_findings = [
        {
            "issue_id": candidate_id,
            "status": "candidate_pending_confirmation",
            "finding": source_by_id[candidate_id],
        }
        for candidate_id in sorted(candidate_ids)
        if candidate_id in source_by_id
    ]
    for candidate in candidate_findings:
        finding = candidate["finding"]
        manual_review.append(
            {
                "record_type": "ai_candidate",
                "id": candidate["issue_id"],
                "title": finding.get("title") or "AI 候选发现待确认",
                "message": finding.get("message") or "该模型候选尚未与确定性规则结果建立事实关系。",
                "suggestion": "核对原始证据及财政事实后确认；该候选不计入正式问题。",
                **candidate,
            }
        )
    for conflict in merge_conflicts:
        manual_review.append(
            {
                "record_type": "merge_conflict",
                "id": f"merge-conflict:{conflict.get('key') or len(manual_review)}",
                "title": "AI 与规则结果存在冲突",
                "message": "模型候选与确定性规则结论不一致，保留双方原始结果待人工复核。",
                **conflict,
            }
        )
    structured_ingest = payload.get("structured_ingest")
    if not isinstance(structured_ingest, Mapping):
        structured_ingest = _mapping(result.get("structured_ingest"))
    # schema v3：结构化识别待复核、全局处理原因和未完成义务都进入 diagnostics。
    # 这些记录保留内部完成度，但不再伪装成业务材料问题或强制人工任务。
    diagnostics: List[Dict[str, Any]] = []
    for item in _mapping_list(structured_ingest.get("review_items")):
        table_code = str(item.get("table_code") or "").strip()
        raw_page = item.get("page") or item.get("page_number")
        try:
            page = int(raw_page) if raw_page is not None else None
        except (TypeError, ValueError):
            page = None
        if page is not None and page <= 0:
            page = None
        review_type = str(item.get("type") or "").strip()
        cause_kind = (
            CAUSE_IDENTITY_CONFLICT
            if any(token in review_type for token in ("identity", "org", "subject"))
            else CAUSE_EXTRACTION_FAILURE
        )
        diagnostics.append(
            {
                **item,
                "record_type": "structured_review",
                "id": item.get("id") or f"structured-review:{table_code or len(diagnostics) + 1}",
                "title": item.get("title") or (
                    f"结构化识别待复核：{table_code}"
                    if table_code else "结构化识别结果待复核"
                ),
                "message": item.get("message") or "该结构化识别项需要人工复核。",
                "severity": item.get("severity") or "manual_review",
                "cause_kind": cause_kind,
                "location": {
                    key: value
                    for key, value in {"page": page, "table": table_code or None}.items()
                    if value is not None
                },
                "suggestion": item.get("recommended_action") or "请核对原始 PDF 后确认。",
            }
        )

    coverage_instances = (
        coverage_dict.get("instances")
        if isinstance(coverage_dict.get("instances"), list)
        else []
    )
    # 根问题关联只在报告层用同一次分析的正式问题做证据判定；找不到就保持
    # extraction_failure，不按中文提示猜“材料缺陷”。
    cascade_row = {
        "job_id": job_id,
        "rule_findings": [],
        "issues": {"all": list(confirmed_issues)},
    }
    visible_issue_ids = {
        str(item.get("id") or "")
        for item in confirmed_issues
        if str(item.get("id") or "")
    }
    from types import SimpleNamespace

    from src.services.obligation_finding_coverage import cascade_root_issue_ids

    for item in coverage_instances:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") in {"completed", "not_applicable"}:
            continue
        record = {
            "record_type": "unfinished_check",
            "obligation_id": item.get("obligation_id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "reason": item.get("reason_label") or item.get("reason"),
            "detail": item.get("detail") or item.get("gap_note"),
            # DH-02：结构化卡点原样进入诊断，不在这里翻译或推断。
            "reason_codes": list(item.get("reason_codes") or []),
            "blocked_by": [dict(value) for value in item.get("blocked_by") or []],
            "missing_operands": [dict(value) for value in item.get("missing_operands") or []],
            "candidates": [dict(value) for value in item.get("candidates") or []],
            "next_action": item.get("next_action"),
            "root_cause_key": item.get("root_cause_key"),
            "card_summary": item.get("card_summary"),
            "suggested_actions": [dict(value) for value in item.get("suggested_actions") or []],
            "legacy_same_source_keys": list(item.get("legacy_same_source_keys") or []),
            "legacy_coverage_terms": list(item.get("legacy_coverage_terms") or []),
        }
        legacy = (
            not record["reason_codes"]
            and not record["blocked_by"]
            and not record["missing_operands"]
        )
        roots: List[str] = []
        if not legacy:
            try:
                roots = cascade_root_issue_ids(
                    SimpleNamespace(**record),
                    cascade_row,
                    visible_issue_ids=visible_issue_ids,
                    structured_ingest=structured_ingest,
                )
            except Exception:  # noqa: BLE001 - 关联失败保持技术诊断，不影响报告生成
                roots = []
        diagnostics.append(
            annotate_diagnostic(
                record,
                schema_version=3,
                root_issue_ids=roots,
                legacy=legacy,
            )
        )
    for raw_reason in _mapping_list(payload.get("review_reasons")):
        code = str(raw_reason.get("code") or "").strip()
        diagnostics.append(
            {
                **raw_reason,
                "record_type": "processing_notice",
                "id": raw_reason.get("id") or f"processing:{code or len(diagnostics) + 1}",
                "title": raw_reason.get("title") or _processing_reason_title(code),
                "message": raw_reason.get("message") or "本次检查存在处理状态说明。",
                "cause_kind": _processing_reason_cause(code),
            }
        )

    human_decisions = _mapping_list(payload.get("human_decisions"))
    decisions_by_issue = {
        str(item.get("issue_id") or ""): item
        for item in human_decisions
        if str(item.get("issue_id") or "")
    }
    for issue in confirmed_issues:
        if payload.get("analysis_revision") is not None:
            issue.setdefault("analysis_revision", payload.get("analysis_revision"))
        decision = decisions_by_issue.get(str(issue.get("id") or ""))
        if decision:
            issue["human_decision"] = {
                key: decision.get(key)
                for key in (
                    "status", "note", "analysis_revision", "decision_revision",
                    "inherited", "actor", "check_relation_id", "fact_ids", "evidence_ids",
                    "identity_fingerprint",
                )
            }

    report = ReviewReport(
        schema_version=3,
        job_id=job_id,
        analysis_revision=payload.get("analysis_revision"),
        metadata={
            "filename": payload.get("filename") or payload.get("original_filename"),
            "document_type": payload.get("doc_type"),
            "report_kind": report_kind,
            "year": payload.get("report_year") or payload.get("fiscal_year") or year_field.get("value"),
            "organization": organization,
            "profile_status": payload.get("profile_status") or profile.get("profile_status"),
            "versions": meta.get("versions"),
            'fact_snapshot': meta.get('fact_snapshot') or (payload.get('structured_ingest') or {}).get('fact_snapshot'),
            "rule_registry_version": meta.get("rule_registry_version"),
            "rule_policy_version": meta.get("rule_policy_version"),
            **({'material_dimensions':{'writing':meta.get('document_scope_resolution'),
                 'amount':meta.get('document_scope_confirmation'),'binding':meta.get('binding')}}
               if meta.get('document_scope_resolution') or meta.get('document_scope_confirmation') else {}),
        },
        conclusion={
            "status": payload.get("status"),
            "quality_status": payload.get("quality_status"),
            "analysis_conclusion": payload.get("analysis_conclusion"),
            "conclusion_scope": payload.get("conclusion_scope"),
            "issue_total": len(confirmed_issues),
            "review_reasons": _mapping_list(payload.get("review_reasons")),
        },
        issues=confirmed_issues,
        tips=tips,
        manual_review=[normalize_manual_review(item) for item in manual_review],
        check_receipts=_mapping_list(meta.get("check_receipts")),
        obligation_coverage=coverage if isinstance(coverage, (dict, list)) else None,
        human_decisions=human_decisions,
        merge_conflicts=merge_conflicts,
        candidate_findings=candidate_findings,
        delivery_suppressions=_mapping_list(
            result.get("delivery_suppressions") or meta.get("delivery_suppressions")),
        diagnostics=diagnostics,
    )
    output = report.to_dict()
    output["conclusion"].update(review_report_counts(output, payload.get("ignored_issue_ids") or []))
    output["conclusion"]["issue_total"] = output["conclusion"]["review_issue_total"]
    from src.schemas.review_lifecycle import RESOLVED_ISSUE_STATUSES

    decision_status: Dict[str, str] = {}
    for decision in _mapping_list(payload.get("human_decisions")):
        issue_id = str(decision.get("issue_id") or "").strip()
        if not issue_id:
            continue
        decision_status[issue_id] = str(
            decision.get("status") or decision.get("decision") or ""
        ).strip()
    ignored_ids = {
        str(value) for value in (payload.get("ignored_issue_ids") or []) if str(value)
    }
    pending_formal = 0
    seen_issue_ids: set[str] = set()
    for item in confirmed_issues:
        issue_id = str(item.get("id") or "").strip()
        if not issue_id or issue_id in ignored_ids or issue_id in seen_issue_ids:
            continue
        seen_issue_ids.add(issue_id)
        status = decision_status.get(issue_id, "")
        # 与完成门禁同一纪律：confirmed/no_issue/in_package 才减待处理；
        # needs_review、缺决定和未知值都必须继续计入。
        if status not in RESOLVED_ISSUE_STATUSES:
            pending_formal += 1
    output["processing_summary"] = build_processing_summary(
        status=payload.get("status"),
        obligation_coverage=coverage if isinstance(coverage, Mapping) else None,
        diagnostics=output["diagnostics"],
        formal_issue_total=output["conclusion"]["review_issue_total"],
    )
    overall = _mapping(payload.get("overall_status") or meta.get("review_status"))
    output["review_summary"] = build_review_summary(
        formal_issue_total=output["conclusion"]["review_issue_total"],
        pending_formal_issue_total=pending_formal,
        candidate_total=output["conclusion"]["review_candidate_total"],
        review_status=overall.get("review_status"),
        signoff_state=overall.get("signoff_state"),
        coverage_state=output["processing_summary"]["coverage_state"],
    )
    # 导出/页面使用同一时点的口径快照；CSV 的 conclusion_json 也随附。
    output["conclusion"]["processing_summary"] = output["processing_summary"]
    output["conclusion"]["review_summary"] = output["review_summary"]
    return output


def _processing_reason_title(code: str) -> str:
    return {
        "table_reading_incomplete": "部分表格读取未完成",
        "fact_identity_incomplete": "部分数据身份不完整",
        "low_page_coverage": "部分页面未覆盖",
        "scanned_pages_detected": "存在扫描页",
        "unknown_report_kind": "报告类型未识别",
        "unknown_report_year": "报告年度未识别",
        "ai_assist_required_but_failed": "语义复核未完成",
        "evidence_incomplete_findings": "部分候选缺少可定位证据",
        "correction_overlay_incomplete": "人工校正未完全生效",
        "structured_ingest_failed": "结构化保存失败",
        "missing_required_table": "核心表未识别",
        "ambiguous_table_schema": "表结构识别存在歧义",
        "fact_materialization_empty": "表格未生成可用事实",
        "rule_evidence_incomplete": "部分问题证据不完整",
        "rule_execution_error": "部分规则执行异常",
        "rules_insufficient_data": "部分规则取数不足",
        "check_obligations_incomplete": "部分检查关系未完成",
        "rules_not_executed": "部分规则未执行",
        "report_type_mismatch": "报告类型不一致",
        "org_scope_pending": "汇总口径待确认",
    }.get(code, "处理状态说明")


def _processing_reason_cause(code: str) -> str:
    if code in {
        "table_reading_incomplete", "fact_identity_incomplete", "low_page_coverage",
        "scanned_pages_detected", "unknown_report_kind", "unknown_report_year",
        "missing_required_table", "ambiguous_table_schema", "fact_materialization_empty",
        "rules_insufficient_data", "check_obligations_incomplete", "rules_not_executed",
    }:
        return CAUSE_EXTRACTION_FAILURE
    if code in {"report_type_mismatch", "org_scope_pending"}:
        return CAUSE_IDENTITY_CONFLICT
    if code == "evidence_incomplete_findings":
        return CAUSE_EXTRACTION_FAILURE
    return CAUSE_EXECUTION_FAILURE


def _collect_findings(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    merged = _mapping(result.get("merged"))
    merge_policy = str(merged.get("merge_policy_version") or "")
    source: List[Dict[str, Any]] = []
    seen_source = set()
    for key in ("ai_findings", "rule_findings"):
        for item in _mapping_list(result.get(key)):
            issue_id = str(item.get("id") or "")
            if issue_id and issue_id not in seen_source:
                seen_source.add(issue_id)
                source.append(item)
    by_id = {str(item.get("id") or ""): item for item in source if item.get("id")}
    merged_ids = merged.get("merged_ids")
    if merge_policy == "fact-check-relation-v1":
        candidate_ids = set()
        candidate_ids = {
            str(item).strip()
            for item in merged.get("candidate_issue_ids", [])
            if str(item or "").strip()
        } if isinstance(merged.get("candidate_issue_ids"), list) else set()
        if isinstance(merged.get("manual_review_issue_ids"), list):
            candidate_ids.update(
                str(item).strip()
                for item in merged.get("manual_review_issue_ids", [])
                if str(item or "").strip()
            )
        ordered_ids = merged_ids if isinstance(merged_ids, list) else []
        projected = [
            by_id[str(issue_id)]
            for issue_id in ordered_ids
            if str(issue_id) in by_id and str(issue_id) not in candidate_ids
        ]
        return projected
    if isinstance(merged_ids, list) and by_id:
        projected = [by_id[str(issue_id)] for issue_id in merged_ids if str(issue_id) in by_id]
        if projected:
            return projected

    issues_payload = result.get("issues")
    if isinstance(issues_payload, Mapping):
        raw = issues_payload.get("all")
    else:
        raw = issues_payload
    findings: List[Dict[str, Any]] = []
    seen = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            findings.append(dict(item))
    if findings:
        return findings

    source_issues: List[Dict[str, Any]] = []
    for key in ("ai_findings", "rule_findings"):
        bucket = result.get(key)
        if isinstance(bucket, list):
            source_issues.extend(dict(item) for item in bucket if isinstance(item, Mapping))
    merged = _mapping(result.get("merged"))
    merged_ids = merged.get("merged_ids")
    if isinstance(merged_ids, list) and source_issues:
        by_id = {str(item.get("id") or ""): item for item in source_issues}
        ordered = [by_id[str(item)] for item in merged_ids if str(item) in by_id]
        if ordered:
            source_issues = ordered
    for item in source_issues:
        item_id = str(item.get("id") or "")
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        findings.append(item)
    return findings


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]
