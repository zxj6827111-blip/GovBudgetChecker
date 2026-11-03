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

    def __post_init__(self) -> None:
        if self.status not in SUB_CHECK_STATUSES:
            raise ValueError(f"invalid sub-check status: {self.status}")
        if self.evaluation_basis not in {"fiscal_fact", "legacy_document", "ai_candidate"}:
            raise ValueError(f"invalid evaluation_basis: {self.evaluation_basis}")
        if self.fact_migration_status not in {"complete", "pending", "not_applicable"}:
            raise ValueError(f"invalid fact_migration_status: {self.fact_migration_status}")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckReceipt":
        return cls(
            rule_id=str(data.get("rule_id") or ""),
            sub_check_id=str(data.get("sub_check_id") or ""),
            required_fact_ids=[str(i) for i in data.get("required_fact_ids") or []],
            operand_fact_ids=[str(i) for i in data.get("operand_fact_ids") or []],
            required_operand_roles=[str(i) for i in data.get("required_operand_roles") or []],
            operand_sources={str(k): str(v) for k, v in (data.get("operand_sources") or {}).items()},
            cell_observations=[dict(item) for item in data.get("cell_observations") or [] if isinstance(item, dict)],
            disclosure_sources=[dict(item) for item in data.get("disclosure_sources") or [] if isinstance(item, dict)],
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
