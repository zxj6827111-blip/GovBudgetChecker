"""Conservative AI/rule finding merge based on explicit fact/check identity (ZC-06)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.schemas.issues import AnalysisConfig, ConflictItem, IssueItem, MergedSummary

logger = logging.getLogger(__name__)


def _text_id(value: Any) -> str:
    return str(value or "").strip()


def _relation_parts(finding: IssueItem) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """Return an explicit relation identity; display similarities are never keys."""
    check_id = _text_id(finding.check_relation_id or finding.rule_id)
    fact_ids = tuple(sorted({_text_id(item) for item in finding.fact_ids if _text_id(item)}))
    if not check_id or not fact_ids:
        return None
    return check_id, fact_ids


def _relation_key(parts: Tuple[str, Tuple[str, ...]]) -> str:
    return f"check={parts[0]}|facts={','.join(parts[1])}"


def _relation_id(key: str) -> str:
    return "rel-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _same_value(left: Any, right: Any) -> bool:
    """Compare candidate operands exactly; the merger never applies tolerance."""
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if left is None or right is None:
        return left is right
    try:
        left_decimal = Decimal(str(left))
        right_decimal = Decimal(str(right))
        if left_decimal.is_finite() and right_decimal.is_finite():
            return left_decimal == right_decimal
    except (InvalidOperation, ValueError, TypeError):
        pass
    if isinstance(left, (Mapping, list)) or isinstance(right, (Mapping, list)):
        try:
            return json.dumps(left, sort_keys=True, ensure_ascii=False, default=str) == json.dumps(
                right, sort_keys=True, ensure_ascii=False, default=str
            )
        except (TypeError, ValueError):
            return False
    return left == right


class FindingsMerger:
    """Merge only exact relation pairs; keep originals and conflicts auditable.

    ``AnalysisConfig`` is retained for import/API compatibility. Title, page,
    amount proximity, and tags intentionally do not participate in matching.
    ``merged_ids`` remains the compatibility projection used by current UI and
    report consumers. For an exact AI/rule relation, the deterministic rule
    finding is the representative; both complete source findings remain in the
    original result buckets and, on conflict, in the conflict record.
    """

    def __init__(self, config: AnalysisConfig):
        self.config = config

    def merge_findings(
        self, ai_findings: List[IssueItem], rule_findings: List[IssueItem]
    ) -> MergedSummary:
        logger.info("Start relation merge: AI=%s Rule=%s", len(ai_findings), len(rule_findings))
        ai_by_relation: Dict[str, List[int]] = defaultdict(list)
        rule_by_relation: Dict[str, List[int]] = defaultdict(list)
        relation_parts: Dict[str, Tuple[str, Tuple[str, ...]]] = {}

        for index, finding in enumerate(ai_findings):
            metrics = finding.metrics if isinstance(finding.metrics, Mapping) else {}
            if (
                metrics.get("ai_merge_eligible") is False
                or metrics.get("ai_candidate_contract_state") == "rejected"
            ):
                continue
            parts = _relation_parts(finding)
            if parts:
                key = _relation_key(parts)
                ai_by_relation[key].append(index)
                relation_parts[key] = parts
        for index, finding in enumerate(rule_findings):
            parts = _relation_parts(finding)
            if parts:
                key = _relation_key(parts)
                rule_by_relation[key].append(index)
                relation_parts[key] = parts

        matched_ai: set[int] = set()
        matched_rule: set[int] = set()
        merged_ids: List[str] = []
        conflicts: List[ConflictItem] = []
        agreements: List[str] = []
        relations: List[Dict[str, Any]] = []
        conflict_count = 0
        ambiguous_ai_indices: set[int] = set()
        ambiguous_rule_indices: set[int] = set()
        failed_relation_ai_indices: set[int] = set()
        candidate_issue_ids: List[str] = []
        manual_review_issue_ids: List[str] = []

        for key in sorted(set(ai_by_relation) & set(rule_by_relation)):
            ai_indices = ai_by_relation[key]
            rule_indices = rule_by_relation[key]
            relation_id = _relation_id(key)
            parts = relation_parts[key]
            if len(ai_indices) != 1 or len(rule_indices) != 1:
                # Multiple candidates for one fact/check relation cannot be
                # greedily paired. Keep every source item and put the relation
                # into review with all candidate IDs attached.
                # AC-11：歧义只影响 AI 侧——规则发现是确定性来源，必须保留在
                # 正式集合（merged_ids），不得因 AI 重复/更弱候选被移入待复核。
                conflict_count += 1
                ambiguous_ai_indices.update(ai_indices)
                ambiguous_rule_indices.update(rule_indices)
                rule_representative = (
                    rule_findings[rule_indices[0]].id if rule_indices else None
                )
                if rule_representative:
                    merged_ids.append(rule_representative)
                conflict = ConflictItem(
                    key=relation_id,
                    ai_issue=ai_findings[ai_indices[0]].id if ai_indices else None,
                    rule_issue=rule_findings[rule_indices[0]].id if rule_indices else None,
                    reason="ambiguous-relation",
                    reasons=["ambiguous-relation"],
                    details={
                        "ai_issue_ids": [ai_findings[index].id for index in ai_indices],
                        "rule_issue_ids": [rule_findings[index].id for index in rule_indices],
                    },
                    ai_finding=ai_findings[ai_indices[0]] if ai_indices else None,
                    rule_finding=rule_findings[rule_indices[0]] if rule_indices else None,
                    recommended_action="manual_review",
                )
                conflicts.append(conflict)
                relations.append(
                    {
                        "relation_id": relation_id,
                        "check_relation_id": parts[0],
                        "fact_ids": list(parts[1]),
                        "ai_issue_ids": [ai_findings[index].id for index in ai_indices],
                        "rule_issue_ids": [rule_findings[index].id for index in rule_indices],
                        "representative_issue_id": rule_representative,
                        "status": "ambiguous",
                        "conflict_reasons": ["ambiguous-relation"],
                    }
                )
                continue

            ai_index, rule_index = ai_indices[0], rule_indices[0]
            ai_item = ai_findings[ai_index]
            rule_item = rule_findings[rule_index]
            matched_ai.add(ai_index)
            matched_rule.add(rule_index)
            merged_ids.append(rule_item.id)

            try:
                conflict = self._check_relation_conflict(ai_item, rule_item, relation_id)
            except Exception as exc:
                # A malformed candidate or unexpected metric must not abort
                # adjudication of unrelated relations. Keep the deterministic
                # rule finding formal; quarantine only this AI pairing and
                # record a relation-scoped failure without copying model text.
                logger.error(
                    "AI/rule relation evaluation failed",
                    extra={"relation_id": relation_id, "error_type": type(exc).__name__},
                )
                conflict = ConflictItem(
                    key=relation_id,
                    ai_issue=ai_item.id,
                    rule_issue=rule_item.id,
                    reason="relation-evaluation-error",
                    reasons=["relation-evaluation-error"],
                    details={"error_type": type(exc).__name__},
                    ai_finding=ai_item,
                    rule_finding=rule_item,
                    recommended_action="manual_review",
                )
                conflict_count += 1
                failed_relation_ai_indices.add(ai_index)
                candidate_issue_ids.append(ai_item.id)
                manual_review_issue_ids.append(ai_item.id)
            reasons = list(conflict.reasons) if conflict else []
            if conflict:
                conflicts.append(conflict)
                if "relation-evaluation-error" not in reasons:
                    conflict_count += 1
            else:
                agreements.append(relation_id)
            relations.append(
                {
                    "relation_id": relation_id,
                    "check_relation_id": parts[0],
                    "fact_ids": list(parts[1]),
                    "ai_issue_ids": [ai_item.id],
                    "rule_issue_ids": [rule_item.id],
                    "representative_issue_id": rule_item.id,
                    "status": (
                        "execution_error"
                        if "relation-evaluation-error" in reasons
                        else "conflict" if conflict else "agreement"
                    ),
                    "conflict_reasons": reasons,
                }
            )

        ai_unmatched = [index for index in range(len(ai_findings)) if index not in matched_ai]
        rule_unmatched = [index for index in range(len(rule_findings)) if index not in matched_rule]
        for index in ai_unmatched:
            item = ai_findings[index]
            candidate_issue_ids.append(item.id)
            metrics = item.metrics if isinstance(item.metrics, Mapping) else {}
            rejected_candidate = (
                metrics.get("ai_merge_eligible") is False
                or metrics.get("ai_candidate_contract_state") == "rejected"
            )
            if rejected_candidate:
                manual_review_issue_ids.append(item.id)
            if index in ambiguous_ai_indices:
                manual_review_issue_ids.append(item.id)
                continue
            parts = _relation_parts(item)
            key = _relation_key(parts) if parts else f"ai-candidate:{item.id}"
            relations.append(
                {
                    "relation_id": _relation_id(key),
                    "check_relation_id": parts[0] if parts else item.check_relation_id or item.rule_id,
                    "fact_ids": list(parts[1]) if parts else list(item.fact_ids),
                    "ai_issue_ids": [item.id],
                    "rule_issue_ids": [],
                    "representative_issue_id": None,
                    "status": "rejected_candidate" if rejected_candidate else "ai_candidate",
                    "conflict_reasons": (
                        list(metrics.get("ai_candidate_rejection_reasons") or ["candidate-contract-rejected"])
                        if rejected_candidate
                        else []
                    ),
                }
            )
        for index in rule_unmatched:
            item = rule_findings[index]
            # AC-11：规则发现不因关系歧义退出正式集合；歧义留痕在 relation。
            merged_ids.append(item.id)
            parts = _relation_parts(item)
            key = _relation_key(parts) if parts else f"rule-only:{item.id}"
            relations.append(
                {
                    "relation_id": _relation_id(key),
                    "check_relation_id": parts[0] if parts else item.check_relation_id or item.rule_id,
                    "fact_ids": list(parts[1]) if parts else list(item.fact_ids),
                    "ai_issue_ids": [],
                    "rule_issue_ids": [item.id],
                    "representative_issue_id": item.id,
                    "status": "unmatched_rule",
                    "conflict_reasons": (
                        ["ambiguous-relation"] if index in ambiguous_rule_indices else []
                    ),
                }
            )

        # Preserve first occurrence if source buckets contain duplicate IDs.
        merged_ids = list(dict.fromkeys(item for item in merged_ids if item))
        totals = {
            "ai": len(ai_findings),
            "rule": len(rule_findings),
            "merged": len(merged_ids),
            "conflicts": conflict_count,
            "agreements": len(agreements),
            "ai_only": len(ai_unmatched) + len(failed_relation_ai_indices),
            "rule_only": len(rule_unmatched),
        }
        logger.info("Relation merge complete: %s", totals)
        return MergedSummary(
            totals=totals,
            conflicts=conflicts,
            agreements=agreements,
            merged_ids=merged_ids,
            ai_only=[
                ai_findings[index]
                for index in sorted(set(ai_unmatched) | failed_relation_ai_indices)
            ],
            rule_only=[rule_findings[index] for index in rule_unmatched],
            relations=relations,
            candidate_issue_ids=list(dict.fromkeys(candidate_issue_ids)),
            manual_review_issue_ids=list(dict.fromkeys(manual_review_issue_ids)),
            merge_policy_version="fact-check-relation-v1",
        )

    @staticmethod
    def _check_relation_conflict(
        ai_item: IssueItem, rule_item: IssueItem, relation_id: str
    ) -> Optional[ConflictItem]:
        reasons: List[str] = []
        details: Dict[str, Any] = {}

        if ai_item.severity != rule_item.severity:
            reasons.append("severity-mismatch")
            details["severity"] = {"ai": ai_item.severity, "rule": rule_item.severity}

        metric_conflicts: Dict[str, Dict[str, Any]] = {}
        for metric_key in sorted(set(ai_item.metrics) & set(rule_item.metrics)):
            ai_value = ai_item.metrics[metric_key]
            rule_value = rule_item.metrics[metric_key]
            if not _same_value(ai_value, rule_value):
                metric_conflicts[metric_key] = {"ai": ai_value, "rule": rule_value}
        if metric_conflicts:
            reasons.insert(0, "value-mismatch")
            details["metrics"] = metric_conflicts

        if not reasons:
            return None
        return ConflictItem(
            key=relation_id,
            ai_issue=ai_item.id,
            rule_issue=rule_item.id,
            reason=reasons[0],
            reasons=reasons,
            details=details,
            ai_finding=ai_item,
            rule_finding=rule_item,
            recommended_action="manual_review",
        )


def merge_findings(
    ai_findings: List[IssueItem],
    rule_findings: List[IssueItem],
    config: AnalysisConfig,
) -> MergedSummary:
    """Public merge entrypoint used by the production dual-mode pipeline."""
    return FindingsMerger(config).merge_findings(ai_findings, rule_findings)
