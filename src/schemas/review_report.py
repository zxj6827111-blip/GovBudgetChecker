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
            issues=_mapping_list(payload.get("issues")),
            tips=_mapping_list(payload.get("tips")),
            manual_review=_mapping_list(payload.get("manual_review")),
            check_receipts=_mapping_list(payload.get("check_receipts")),
            obligation_coverage=payload.get("obligation_coverage"),
            human_decisions=_mapping_list(payload.get("human_decisions")),
        )


def build_review_report(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the canonical report from a terminal job payload without guessing."""
    job_id = str(payload.get("job_id") or "")
    result = _mapping(payload.get("result"))
    meta = _mapping(result.get("meta"))
    source_issues = _collect_findings(result)
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
    structured_ingest = payload.get("structured_ingest")
    if not isinstance(structured_ingest, Mapping):
        structured_ingest = _mapping(result.get("structured_ingest"))
    for item in _mapping_list(structured_ingest.get("review_items")):
        manual_review.append({"record_type": "structured_review", **item})
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
        manual_review=manual_review,
        check_receipts=_mapping_list(meta.get("check_receipts")),
        obligation_coverage=coverage if isinstance(coverage, (dict, list)) else None,
        human_decisions=_mapping_list(payload.get("human_decisions")),
    )
    return report.to_dict()


def _collect_findings(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
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
