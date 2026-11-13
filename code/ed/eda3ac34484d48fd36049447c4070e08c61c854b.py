"""Canonical versioned analysis report shared by status and exports."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


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


def build_review_report(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the canonical report from a terminal job payload without guessing."""
    job_id = str(payload.get("job_id") or "")
    result = _mapping(payload.get("result"))
    meta = _mapping(result.get("meta"))
    source_issues = [normalize_issue_source_page(item) for item in _collect_findings(result)]
    tips = [
        item
        for item in source_issues
        if str(item.get("severity") or "").strip().lower() == "info"
    ]
    tip_ids = {str(item.get("id") or "") for item in tips if item.get("id")}
    confirmed_issues = [
        item
        for item in source_issues
        if not item.get("id") or str(item.get("id")) not in tip_ids
    ]
    profile = _mapping(payload.get("document_profile") or meta.get("document_profile"))
    report_kind_field = _mapping(profile.get("report_kind"))
    year_field = _mapping(profile.get("report_year"))
    organization_field = _mapping(profile.get("organization_name"))
    report_kind = str(payload.get("report_kind") or report_kind_field.get("value") or "") or None
    organization = organization_field.get("value")

    coverage = payload.get("check_coverage") or meta.get("obligation_coverage")
    coverage_dict = _mapping(coverage)
    manual_review = [
        {"record_type": "review_reason", **item}
        for item in _mapping_list(payload.get("review_reasons"))
    ]
    merged = _mapping(result.get("merged"))
    merge_conflicts = _mapping_list(merged.get("conflicts"))
    candidate_ids = {
        str(item).strip()
        for item in merged.get("candidate_issue_ids", [])
        if str(item or "").strip()
    } if isinstance(merged.get("candidate_issue_ids"), list) else set()
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
    for item in _mapping_list(structured_ingest.get("review_items")):
        table_code = str(item.get("table_code") or "").strip()
        raw_page = item.get("page") or item.get("page_number")
        try:
            page = int(raw_page) if raw_page is not None else None
        except (TypeError, ValueError):
            page = None
        if page is not None and page <= 0:
            page = None
        manual_review.append(
            {
                **item,
                "record_type": "structured_review",
                "id": item.get("id") or f"structured-review:{table_code or len(manual_review) + 1}",
                "title": item.get("title") or (
                    f"结构化识别待复核：{table_code}"
                    if table_code else "结构化识别结果待复核"
                ),
                "message": item.get("message") or "该结构化识别项需要人工复核。",
                "severity": item.get("severity") or "manual_review",
                "location": {
                    key: value
                    for key, value in {"page": page, "table": table_code or None}.items()
                    if value is not None
                },
                "suggestion": item.get("recommended_action") or "请核对原始 PDF 后确认。",
            }
        )
    for item in coverage_dict.get("instances", []) if isinstance(coverage_dict.get("instances"), list) else []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") in {"completed", "not_applicable"}:
            continue
        manual_review.append(
            {
                "record_type": "unfinished_check",
                "obligation_id": item.get("obligation_id"),
                "title": item.get("title"),
                "status": item.get("status"),
                "reason": item.get("reason_label") or item.get("reason"),
                "detail": item.get("detail") or item.get("gap_note"),
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
        schema_version=2,
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
            "rule_registry_version": meta.get("rule_registry_version"),
            "rule_policy_version": meta.get("rule_policy_version"),
        },
        conclusion={
            "status": payload.get("status"),
            "quality_status": payload.get("quality_status"),
            "analysis_conclusion": payload.get("analysis_conclusion"),
            "conclusion_scope": payload.get("conclusion_scope"),
            "issue_total": (
                payload.get("issue_total")
                if payload.get("issue_total") is not None
            else len(source_issues)
            ),
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
    )
    return report.to_dict()


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
