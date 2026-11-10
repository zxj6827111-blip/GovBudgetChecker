"""关系级检查回执契约（DH-02 结构化卡点的落点）。

回执、诊断原因和展示字段不属于解析产物身份：它们变化只能改变完整运行
指纹，不能让解析缓存失效或重新签发 ``parse_run_uid``。``parse_artifact``
仍从本模块导出同名对象，保持既有导入路径兼容。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

#: 六态子检查状态（ZC-03 的回执消费此常量）
SUB_CHECK_STATUSES = (
    "pass",
    "fail",
    "not_applicable",
    "insufficient_data",
    "parse_error",
    "execution_error",
)


@dataclass
class CheckReceipt:
    """子检查回执：每个适用检查关系一条，不能用规则级成功替代。"""

    rule_id: str
    sub_check_id: str
    required_sub_check_ids: List[str] = field(default_factory=list)
    required_fact_ids: List[str] = field(default_factory=list)
    operand_fact_ids: List[str] = field(default_factory=list)
    required_operand_roles: List[str] = field(default_factory=list)
    operand_sources: Dict[str, str] = field(default_factory=dict)
    formula: Optional[str] = None
    tolerance: Dict[str, Any] = field(default_factory=dict)
    status: str = "insufficient_data"
    incomplete_reason: Optional[str] = None
    issue_refs: List[str] = field(default_factory=list)
    obligation_id: Optional[str] = None
    evaluation_basis: str = "fiscal_fact"
    fact_migration_status: str = "complete"
    policy_version: str = "v3_3_portable"
    cell_observations: List[Dict[str, Any]] = field(default_factory=list)
    disclosure_sources: List[Dict[str, Any]] = field(default_factory=list)
    # 结构化未完成原因：身份冲突、来源缺失、候选不唯一、口径待确认、主体不匹配、几何不支持等。
    reason_codes: List[str] = field(default_factory=list)
    # DH-02 结构化卡点：上游阻断、缺数位置、多候选位置、下一步动作与根因键。
    # 只增字段，不改写 incomplete_reason 兼容文本。
    blocked_by: List[Dict[str, Any]] = field(default_factory=list)
    missing_operands: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    next_action: Optional[str] = None
    root_cause_key: Optional[str] = None
    # 2A：下游回执的金额子检查状态与单位错解释
    # - amount_subcheck_status：executed/not_executed，关闭/隐藏金额检查必须留痕
    # - explained_by：unit_error 表示差额被同源单位错精确解释
    # - residual_difference_wan：解释后仍存在的差额（万元，字符串保留精度）
    # - cascade_of：运行期缺陷键（文档指纹+页码+span+操作数），不依赖金标 ID
    amount_subcheck_status: Optional[str] = None
    explained_by: Optional[str] = None
    residual_difference_wan: Optional[str] = None
    cascade_of: Optional[str] = None
    # R2-01（第二轮复核）：证据性质分账——
    # - real_amount_comparison：比较双方均来自真实取数或 A1 合法授权零
    #   （说明零披露/审定约定），可计独立金额核对；
    # - applicability_judgment：适用性/披露判断（如未列项+总额0 → 不适用），
    #   不是金额核对；
    # - 零推导比较（以被核对另一侧反推本侧空白为0）**不允许**出现在 pass
    #   回执里，此类关系只能保持未知。
    evidence_nature: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in SUB_CHECK_STATUSES:
            raise ValueError(f"invalid sub-check status: {self.status}")
        if self.evaluation_basis not in {"fiscal_fact", "legacy_document", "ai_candidate"}:
            raise ValueError(f"invalid evaluation_basis: {self.evaluation_basis}")
        if self.fact_migration_status not in {"complete", "pending", "not_applicable"}:
            raise ValueError(f"invalid fact_migration_status: {self.fact_migration_status}")
        if self.next_action is not None and self.next_action not in {
            "system_recheck",
            "tech_fix",
            "human_confirm",
            "rerun_ai",
        }:
            raise ValueError(f"invalid next_action: {self.next_action}")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckReceipt":
        return cls(
            rule_id=str(data.get("rule_id") or ""),
            sub_check_id=str(data.get("sub_check_id") or ""),
            required_sub_check_ids=[str(i) for i in data.get("required_sub_check_ids") or []],
            required_fact_ids=[str(i) for i in data.get("required_fact_ids") or []],
            operand_fact_ids=[str(i) for i in data.get("operand_fact_ids") or []],
            required_operand_roles=[str(i) for i in data.get("required_operand_roles") or []],
            operand_sources={str(k): str(v) for k, v in (data.get("operand_sources") or {}).items()},
            cell_observations=[dict(item) for item in data.get("cell_observations") or [] if isinstance(item, dict)],
            disclosure_sources=[dict(item) for item in data.get("disclosure_sources") or [] if isinstance(item, dict)],
            reason_codes=[str(item) for item in data.get("reason_codes") or [] if str(item)],
            blocked_by=[dict(item) for item in data.get("blocked_by") or [] if isinstance(item, dict)],
            missing_operands=[dict(item) for item in data.get("missing_operands") or [] if isinstance(item, dict)],
            candidates=[dict(item) for item in data.get("candidates") or [] if isinstance(item, dict)],
            next_action=_opt_str(data.get("next_action")),
            root_cause_key=_opt_str(data.get("root_cause_key")),
            amount_subcheck_status=_opt_str(data.get("amount_subcheck_status")),
            explained_by=_opt_str(data.get("explained_by")),
            residual_difference_wan=_opt_str(data.get("residual_difference_wan")),
            cascade_of=_opt_str(data.get("cascade_of")),
            evidence_nature=_opt_str(data.get("evidence_nature")),
            formula=_opt_str(data.get("formula")),
            tolerance=dict(data.get("tolerance") or {}),
            status=str(data.get("status") or "insufficient_data"),
            incomplete_reason=_opt_str(data.get("incomplete_reason")),
            issue_refs=[str(i) for i in data.get("issue_refs") or []],
            obligation_id=_opt_str(data.get("obligation_id")),
            evaluation_basis=str(data.get("evaluation_basis") or "fiscal_fact"),
            fact_migration_status=str(data.get("fact_migration_status") or "complete"),
            policy_version=str(data.get("policy_version") or "v3_3_portable"),
        )


def _opt_str(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None
