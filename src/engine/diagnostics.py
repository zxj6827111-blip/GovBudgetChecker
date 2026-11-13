"""结构化卡点诊断：原因码、阻断项、缺失操作数与候选。

本模块只做**数据整形**，不判定业务结论，也不从中文 ``detail`` 反推原因。
诊断由产生卡点的调用点按真实取数/绑定过程构造；义务级诊断在
:mod:`src.engine.check_obligations` 中按关联回执汇总。

根因键算法与验收包 ``verify_pending_review._root_signature`` 保持同构：
优先最上游阻断点，其次完整缺失位置，最后回退到规则/子检查/原因/缺失/候选。
同一主阻断点必须得到同一键，不同主阻断点得到不同键。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: 验收包冻结的维度优先级（与 rule_identity_dimensions.json 同值）。
ROOT_PRIORITY: Dict[str, int] = {
    "organization": 0,
    "year": 1,
    "fund_scope": 1,
    "budget_stage": 1,
    "org_scope": 1,
    "instance_uid": 2,
    "row": 3,
    "column": 4,
    "cell": 5,
}

#: 只有这些原因码可以作为“上游阻断”，且各自只能挂在冻结维度上。
UPSTREAM_REASON_DIMENSIONS: Dict[str, Sequence[str]] = {
    "subject_unconfirmed": ("organization",),
    "subject_conflict": ("organization",),
    "scope_unconfirmed": ("org_scope", "fund_scope", "budget_stage", "year"),
    "table_missing": ("instance_uid",),
    "table_mapping_incomplete": ("instance_uid",),
    "row_code_missing": ("row",),
    "row_identity_duplicate": ("row",),
    "row_level_unresolved": ("row",),
    "column_identity_unconfirmed": ("column",),
    "column_width_drift": ("column",),
    "cell_blank_semantics_unknown": ("cell",),
    "cell_low_confidence": ("cell",),
    "cell_unbound": ("cell",),
}

NEXT_ACTIONS = ("system_recheck", "tech_fix", "human_confirm", "rerun_ai")

#: 验收包 reason_codes_v1 冻结词表。引擎只输出这些码；旧代码里的
#: operand_missing_or_ambiguous / identity_unconfirmed / source_missing
#: 不在词表内，由调用点改写为结构化诊断或退回 rule_no_reason。
KNOWN_REASON_CODES = frozenset(
    {
        "ai_not_requested",
        "ai_partial_citation_rejected",
        "ai_partial_invalid_output",
        "ai_timeout",
        "candidate_amount_conflict",
        "cell_blank_semantics_unknown",
        "cell_low_confidence",
        "cell_unbound",
        "code_name_conflict",
        "column_identity_unconfirmed",
        "column_width_drift",
        "comparison_identity_unresolved",
        "exception_amount_conflict",
        "exception_scope_ambiguous",
        "execution_error",
        "item_amount_not_included",
        "item_disclosure_missing",
        "item_sum_mismatch",
        "name_mismatch_confirmed",
        "operand_ambiguous",
        "operand_missing",
        "parse_error",
        "row_code_missing",
        "row_identity_duplicate",
        "row_level_unresolved",
        "rule_no_reason",
        "scope_unconfirmed",
        "source_unit_normalized",
        "stated_total_missing",
        "subject_conflict",
        "subject_name_unlocated",
        "subject_unconfirmed",
        "sum_scope_not_comparable",
        "table_mapping_incomplete",
        "table_missing",
        "text_binding_failed",
        "text_statement_absent",
        "text_statement_unparsed",
        "withheld_amount_unknown",
    }
)

#: 方案/验收包明确规定唯一或受限路由的原因码；其余按实际证据选择。
_ALLOWED_ACTIONS: Dict[str, set[str]] = {
    "table_mapping_incomplete": {"tech_fix"},
    "text_statement_unparsed": {"system_recheck", "tech_fix"},
    "ai_not_requested": {"rerun_ai"},
    "ai_partial_invalid_output": {"rerun_ai"},
    "ai_partial_citation_rejected": {"rerun_ai"},
    "ai_timeout": {"rerun_ai"},
}
_DEFAULT_ALLOWED = {"human_confirm", "system_recheck", "tech_fix"}
_RECOMMENDED: Dict[str, str] = {
    "table_mapping_incomplete": "tech_fix",
    "text_statement_unparsed": "system_recheck",
    "ai_not_requested": "rerun_ai",
    "ai_partial_invalid_output": "rerun_ai",
    "ai_partial_citation_rejected": "rerun_ai",
    "ai_timeout": "rerun_ai",
    "table_missing": "human_confirm",
    "text_statement_absent": "human_confirm",
    "subject_name_unlocated": "human_confirm",
    "name_mismatch_confirmed": "human_confirm",
    "code_name_conflict": "human_confirm",
    "subject_unconfirmed": "human_confirm",
    "subject_conflict": "human_confirm",
    "scope_unconfirmed": "human_confirm",
    "row_code_missing": "tech_fix",
    "row_identity_duplicate": "tech_fix",
    "row_level_unresolved": "tech_fix",
    "column_identity_unconfirmed": "tech_fix",
    "column_width_drift": "tech_fix",
    "cell_blank_semantics_unknown": "human_confirm",
    "cell_low_confidence": "tech_fix",
    "cell_unbound": "tech_fix",
    "operand_missing": "system_recheck",
    "operand_ambiguous": "system_recheck",
    "text_binding_failed": "human_confirm",
    "rule_no_reason": "tech_fix",
    "parse_error": "tech_fix",
    "execution_error": "tech_fix",
}
_PREFERENCE = ("system_recheck", "tech_fix", "human_confirm", "rerun_ai")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _root_signature(item: Dict[str, Any]) -> str:
    blockers = (
        [b for b in item.get("blocked_by", []) if isinstance(b, dict)]
        if isinstance(item.get("blocked_by"), list)
        else []
    )
    if blockers:
        points = [
            {key: blocker.get(key) for key in ("dimension", "ref", "reason_code")}
            for blocker in blockers
        ]
        point = min(
            points,
            key=lambda blocker: (
                ROOT_PRIORITY.get(str(blocker["dimension"]), 99),
                canonical(blocker),
            ),
        )
        return canonical({"upstream": point})
    missing = item.get("missing_operands")
    if (
        isinstance(missing, list)
        and missing
        and all(
            isinstance(value, dict)
            and type(value.get("page")) is int
            and value["page"] > 0
            and value.get("source") in {"table", "text"}
            for value in missing
        )
    ):
        return canonical({"missing_locations": sorted(missing, key=canonical)})
    candidates = item.get("candidates", [])
    return canonical(
        {
            "rule": item.get("rule_id"),
            "sub_check": item.get("sub_check_id"),
            "obligation": item.get("obligation_id"),
            "reasons": sorted(item.get("reason_codes") or []),
            "missing": item.get("missing_operands"),
            "candidate_locations": [
                {key: value.get(key) for key in ("source", "table_code", "section", "page", "cell_id", "span")}
                for value in candidates
                if isinstance(value, dict)
            ]
            if isinstance(candidates, list)
            else [],
        }
    )


def diagnostic_root_key(item: Dict[str, Any]) -> str:
    return "rc:" + _sha256_text(_root_signature(item))


def choose_next_action(codes: Sequence[str]) -> str:
    """选一个对所有原因码都允许的动作；无交集时退回主原因建议。"""
    allowed: Optional[set[str]] = None
    for code in codes:
        current = _ALLOWED_ACTIONS.get(str(code), _DEFAULT_ALLOWED)
        allowed = set(current) if allowed is None else allowed & current
    if allowed:
        for action in _PREFERENCE:
            if action in allowed:
                return action
    for code in codes:
        recommended = _RECOMMENDED.get(str(code))
        if recommended:
            return recommended
    return "human_confirm"


def make_diagnosis(
    *,
    reason_codes: Sequence[str],
    blocked_by: Optional[Sequence[Dict[str, Any]]] = None,
    missing_operands: Optional[Sequence[Dict[str, Any]]] = None,
    candidates: Optional[Sequence[Dict[str, Any]]] = None,
    next_action: Optional[str] = None,
    root_cause_key: Optional[str] = None,
) -> Dict[str, Any]:
    codes = list(dict.fromkeys(str(code) for code in reason_codes if str(code)))
    item: Dict[str, Any] = {
        "reason_codes": codes,
        "blocked_by": [dict(value) for value in (blocked_by or [])],
        "missing_operands": [dict(value) for value in (missing_operands or [])],
        "candidates": [dict(value) for value in (candidates or [])],
        "next_action": next_action or choose_next_action(codes),
    }
    item["root_cause_key"] = root_cause_key or diagnostic_root_key(item)
    return item


def merge_diagnoses(diagnoses: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    codes: List[str] = []
    blocked: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    seen = {"blocked": set(), "missing": set(), "candidates": set()}
    found = False
    for diagnosis in diagnoses:
        if not isinstance(diagnosis, dict):
            continue
        found = True
        for code in diagnosis.get("reason_codes") or []:
            if str(code) not in codes:
                codes.append(str(code))
        for key, target in (("blocked_by", blocked), ("missing_operands", missing), ("candidates", candidates)):
            for value in diagnosis.get(key) or []:
                if not isinstance(value, dict):
                    continue
                marker = canonical(value)
                if marker not in seen[key]:
                    seen[key].add(marker)
                    target.append(value)
    if not found:
        return None
    return make_diagnosis(
        reason_codes=codes,
        blocked_by=blocked,
        missing_operands=missing,
        candidates=candidates,
    )


def blocker(dimension: str, ref: Any, reason_code: str) -> Dict[str, Any]:
    return {"dimension": dimension, "ref": str(ref or "未识别到"), "reason_code": reason_code}


def missing_table_operand(
    *,
    role: str,
    table_code: str,
    page: Optional[int] = None,
    row_key: Any = None,
    column: Optional[str] = None,
    fund_scope: Optional[str] = None,
    stage: Optional[str] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "source": "table",
        "role": str(role),
        "table_code": str(table_code),
        "page": page if type(page) is int and page > 0 else None,
        "row_key": row_key,
        "column": column,
        "fund_scope": fund_scope,
        "stage": stage,
        "year": year,
    }


def missing_text_operand(
    *,
    role: str,
    section: str,
    page: Optional[int] = None,
    row_key: Any = None,
    column: Optional[str] = None,
    fund_scope: Optional[str] = None,
    stage: Optional[str] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "source": "text",
        "role": str(role),
        "section": str(section),
        "page": page if type(page) is int and page > 0 else None,
        "row_key": row_key,
        "column": column,
        "fund_scope": fund_scope,
        "stage": stage,
        "year": year,
    }


def table_candidate(*, table_code: str, page: Optional[int], cell_id: Any, value: Any) -> Dict[str, Any]:
    return {
        "source": "table",
        "table_code": str(table_code),
        "page": page if type(page) is int and page > 0 else None,
        "cell_id": str(cell_id or ""),
        "value": value,
    }


def text_candidate(*, section: str, page: int, span: Sequence[int], value: Any) -> Dict[str, Any]:
    return {
        "source": "text",
        "section": str(section),
        "page": int(page),
        "span": [int(span[0]), int(span[1])],
        "value": value,
    }


def fact_candidate(fact: Any) -> Optional[Dict[str, Any]]:
    """从财政事实的原格证据构造表格候选；缺少原格身份时返回 None。"""
    refs = (getattr(fact, "extra_dims", None) or {}).get("evidence_refs") or []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        cell_id = ref.get("source_cell_id")
        page = ref.get("physical_page")
        if cell_id and type(page) is int and page > 0:
            return table_candidate(
                table_code=str(getattr(fact, "table_code", "") or ""),
                page=page,
                cell_id=cell_id,
                value=str(getattr(fact, "normalized_value", "") or ""),
            )
    return None


__all__ = [
    "NEXT_ACTIONS",
    "ROOT_PRIORITY",
    "UPSTREAM_REASON_DIMENSIONS",
    "blocker",
    "canonical",
    "choose_next_action",
    "diagnostic_root_key",
    "fact_candidate",
    "make_diagnosis",
    "merge_diagnoses",
    "missing_table_operand",
    "missing_text_operand",
    "table_candidate",
    "text_candidate",
]
