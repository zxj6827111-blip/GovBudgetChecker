"""统一结果语义：区分材料缺陷、缺陷后果、取数失败与执行异常。

本模块只做**分类**，不改变任何检查结论。分类依据是规则在失败发生处写入的
结构化原因码、正式问题关联和报告版本，不从中文提示反推事实。

用户可见的四种内容（见 2026-11-16 整改方案）：

- 材料问题：``material_defect``，同时必须存在正式 Issue；
- 具体疑点：候选/待核实记录，保留原文依据；
- 处理状态：``execution_state`` / ``coverage_state`` 与材料层汇总；
- 技术异常：``extraction_failure`` / ``identity_conflict`` / ``execution_failure``，
  只进入 ``diagnostics``，不进入业务问题队列。

``upstream_material_defect`` 表示当前关系被已证实的材料缺陷阻断，必须携带
``root_issue_ids``；``legacy_unknown`` 仅用于无法可靠分类的历史结果。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

CAUSE_MATERIAL_DEFECT = "material_defect"
CAUSE_UPSTREAM_MATERIAL_DEFECT = "upstream_material_defect"
CAUSE_EXTRACTION_FAILURE = "extraction_failure"
CAUSE_IDENTITY_CONFLICT = "identity_conflict"
CAUSE_EXECUTION_FAILURE = "execution_failure"
CAUSE_LEGACY_UNKNOWN = "legacy_unknown"

CAUSE_KINDS = (
    CAUSE_MATERIAL_DEFECT,
    CAUSE_UPSTREAM_MATERIAL_DEFECT,
    CAUSE_EXTRACTION_FAILURE,
    CAUSE_IDENTITY_CONFLICT,
    CAUSE_EXECUTION_FAILURE,
    CAUSE_LEGACY_UNKNOWN,
)

#: 执行/服务类原因码：属于系统处理过程，不是材料内容错误。
_EXECUTION_CODES = frozenset(
    {
        "ai_not_requested",
        "ai_partial_citation_rejected",
        "ai_partial_invalid_output",
        "ai_timeout",
        "execution_error",
        "parse_error",
        "rule_no_reason",
    }
)

#: 身份/归档类原因码：需要核对主体、年度或归档关系，不是金额本身。
_IDENTITY_CODES = frozenset(
    {
        "subject_conflict",
        "subject_name_unlocated",
        "subject_unconfirmed",
        "code_name_conflict",
    }
)

#: 已证实材料缺陷类原因码。仅当报告层确认存在对应正式问题时才作为
#: ``material_defect``；作为未完成诊断出现时默认仍按取数失败处理。
_MATERIAL_CODES = frozenset(
    {
        "item_disclosure_missing",
        "item_sum_mismatch",
        "item_amount_not_included",
        "name_mismatch_confirmed",
        "stated_total_missing",
    }
)


def _codes(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item).strip() for item in value if str(item or "").strip()]


def cause_kind_for_codes(
    reason_codes: Any,
    *,
    schema_version: Any = 3,
) -> str:
    """按结构化原因码给出技术侧 cause_kind。

    正式问题自身的 ``material_defect`` 由报告层写入；本函数只处理未完成
    诊断。存在多类原因时按“执行异常 > 身份冲突 > 取数失败”取最上游语义。
    """
    try:
        version = int(schema_version)
    except (TypeError, ValueError):
        version = 0
    if version < 3:
        return CAUSE_LEGACY_UNKNOWN
    codes = set(_codes(reason_codes))
    if codes & _EXECUTION_CODES:
        return CAUSE_EXECUTION_FAILURE
    if codes & _IDENTITY_CODES:
        return CAUSE_IDENTITY_CONFLICT
    return CAUSE_EXTRACTION_FAILURE


def material_diagnostic_codes(reason_codes: Any) -> bool:
    """该诊断是否只由已证实材料缺陷类原因构成（供报告层复核关联）。"""
    codes = set(_codes(reason_codes))
    return bool(codes) and codes <= _MATERIAL_CODES


def annotate_diagnostic(
    record: Mapping[str, Any],
    *,
    schema_version: Any = 3,
    root_issue_ids: Optional[Sequence[str]] = None,
    material_evidence_confirmed: bool = False,
    legacy: bool = False,
) -> Dict[str, Any]:
    """给一条未完成诊断补 ``cause_kind`` 与根问题关联。

    参数 ``material_evidence_confirmed`` 只能由报告层在“同一正式 Issue 已由
    原件证据确认”时传入 True；不能从提示文字推断。存在根问题时一律标为
    ``upstream_material_defect``，保留计算未完成事实。

    ``legacy`` 只用于旧报告缺少结构化原因码、无法可靠分类的记录；此时保留
    ``legacy_unknown``，等待重分析，不自动升格为材料问题。
    """
    result = dict(record)
    roots = [str(item) for item in (root_issue_ids or []) if str(item or "").strip()]
    codes = _codes(result.get("reason_codes"))
    if roots:
        cause = CAUSE_UPSTREAM_MATERIAL_DEFECT
    elif material_evidence_confirmed and material_diagnostic_codes(codes):
        cause = CAUSE_MATERIAL_DEFECT
    elif legacy and not codes:
        cause = CAUSE_LEGACY_UNKNOWN
    else:
        cause = cause_kind_for_codes(codes, schema_version=schema_version)
    result["cause_kind"] = cause
    if roots:
        result["root_issue_ids"] = list(dict.fromkeys(roots))
    return result


def processing_summary(
    *,
    status: Any,
    obligation_coverage: Any,
    diagnostics: Sequence[Mapping[str, Any]] = (),
    formal_issue_total: int = 0,
) -> Dict[str, Any]:
    """执行状态与检查覆盖分开表达；不以“有问题”代替“已检查完成”。"""
    raw_status = str(status or "").strip().lower()
    if raw_status in {"processing", "running", "queued", "uploaded"}:
        execution_state = "running"
    elif raw_status in {"error", "failed", "cancelled"}:
        execution_state = "failed"
    elif raw_status in {"done", "completed", "review_required", "degraded"}:
        execution_state = "completed"
    else:
        execution_state = "unknown"

    coverage = obligation_coverage if isinstance(obligation_coverage, Mapping) else {}
    try:
        unresolved = int(coverage.get("unresolved_total") or 0)
    except (TypeError, ValueError):
        unresolved = 0
    if not coverage:
        coverage_state = "unknown"
    elif unresolved > 0:
        coverage_state = "partial"
    else:
        coverage_state = "complete"

    technical = [
        item for item in diagnostics
        if str(item.get("cause_kind") or "") in {
            CAUSE_EXTRACTION_FAILURE, CAUSE_IDENTITY_CONFLICT, CAUSE_EXECUTION_FAILURE,
            CAUSE_LEGACY_UNKNOWN,
        }
    ]
    blocked = [
        item for item in diagnostics
        if str(item.get("cause_kind") or "") == CAUSE_UPSTREAM_MATERIAL_DEFECT
    ]
    return {
        "execution_state": execution_state,
        "coverage_state": coverage_state,
        "formal_issue_total": int(formal_issue_total or 0),
        "technical_diagnostic_total": len(technical),
        "material_defect_blocked_total": len(blocked),
        "unfinished_obligation_total": unresolved,
        # 只有执行完成且覆盖完整、无技术诊断时，才能声明检查完整。
        "check_complete": execution_state == "completed" and coverage_state == "complete"
        and not technical,
    }


def review_summary(
    *,
    formal_issue_total: int,
    pending_formal_issue_total: int,
    candidate_total: int,
    review_status: Any = None,
    signoff_state: Any = None,
    coverage_state: str = "unknown",
) -> Dict[str, Any]:
    """业务复核摘要：正式问题、具体候选与签字状态分开记录。"""
    return {
        "formal_issue_total": int(formal_issue_total or 0),
        "pending_formal_issue_total": max(0, int(pending_formal_issue_total or 0)),
        "candidate_total": max(0, int(candidate_total or 0)),
        "review_status": review_status,
        "signoff_state": signoff_state,
        "coverage_state": coverage_state,
    }


__all__ = [
    "CAUSE_KINDS",
    "CAUSE_EXECUTION_FAILURE",
    "CAUSE_EXTRACTION_FAILURE",
    "CAUSE_IDENTITY_CONFLICT",
    "CAUSE_LEGACY_UNKNOWN",
    "CAUSE_MATERIAL_DEFECT",
    "CAUSE_UPSTREAM_MATERIAL_DEFECT",
    "annotate_diagnostic",
    "cause_kind_for_codes",
    "material_diagnostic_codes",
    "processing_summary",
    "review_summary",
]
