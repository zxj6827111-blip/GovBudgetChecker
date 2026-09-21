"""规则执行结果统一模型（RuleOutcome）。

规则分析层引入六态语义，杜绝"解析不可靠被伪造成正式问题"：

- ``fail``：规则判定命中，允许生成正式 finding；
- ``pass``：规则已执行且未命中；
- ``not_applicable``：当前材料口径下规则不适用（如收入表与支出表之间
  不可比），不得产出 finding；
- ``insufficient_data``：无法可靠确定列、主体或句子归属，证据不足，
  不得产出正式 finding，转运行摘要供质量门与人工复核消费；
- ``parse_error``：解析层失败（表格跨页断裂、列重映射失败等）；
- ``execution_error``：规则代码本身抛出异常。

各规则可通过正常的返回值产出正式 finding；当规则包含多个独立子检查、
部分子检查已确认问题而部分子检查因缺少数据/解析异常无法完成时，规则
可抛出携带 partial_issues 的 RuleOutcomeSignal（如 RuleDeferred）。
已确认的 partial_issues 将被系统保留并作为正式 finding 呈现，而规则本身的未完成状态
与原因将如实记录在 rule_execution_summary 中，供质量门与人工复核消费。
杜绝“因未完成而掩盖已确认问题”，也杜绝“因有部分问题而误报全项已核验”。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# 六态枚举值。用字符串常量而非 Enum 类，便于 JSON 序列化与跨进程传输
# （规则运行在子进程里，摘要需要走 JSON）。
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
STATUS_PARSE_ERROR = "parse_error"
STATUS_EXECUTION_ERROR = "execution_error"

ALL_STATUSES = (
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_NOT_APPLICABLE,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PARSE_ERROR,
    STATUS_EXECUTION_ERROR,
)

# 未完成状态集合（未得出完整可信结论，需供质量门 fail-closed 判定）
UNRESOLVED_STATUSES = frozenset(
    {
        STATUS_INSUFFICIENT_DATA,
        STATUS_PARSE_ERROR,
        STATUS_EXECUTION_ERROR,
    }
)

# 兼容历史命名：在契约 C1 下，未完成规则仍可携带已确认 partial_findings
NON_FINDING_STATUSES = UNRESOLVED_STATUSES | frozenset({STATUS_NOT_APPLICABLE})


# 六态状态优先级（聚合时高优先级优先）：
# execution_error (50) > parse_error (40) > insufficient_data (30) > fail (20) > not_applicable (10) > pass (0)
# 核心契约：
# 1) 部分子检查 not_applicable 绝不能覆盖已确认的 fail（fail > not_applicable）；
# 2) 存在未完成子检查时，整条规则 fail-closed 记为未完成，同时保留已确认 finding。
STATUS_PRIORITY: Dict[str, int] = {
    STATUS_EXECUTION_ERROR: 50,
    STATUS_PARSE_ERROR: 40,
    STATUS_INSUFFICIENT_DATA: 30,
    STATUS_FAIL: 20,
    STATUS_NOT_APPLICABLE: 10,
    STATUS_PASS: 0,
}


def resolve_rule_status(
    base_status: str,
    has_findings: bool = False,
    has_conversion_error: bool = False,
) -> str:
    """按状态优先级聚合规则最终 outcome 状态。

    优先级：execution_error (50) > parse_error (40) > insufficient_data (30) > fail (20) > not_applicable (10) > pass (0)

    保证：
    - 已确认问题存在（has_findings=True）时，若无更严重的未完成状态，状态必为 fail；
      not_applicable 无法覆盖 fail。
    - 若存在数据缺失或解析错误，状态反映未完成性质（insufficient_data / parse_error），
      同时 partial_findings 仍如实保留。
    """
    candidates = [base_status]
    if has_findings:
        candidates.append(STATUS_FAIL)
    if has_conversion_error:
        candidates.append(STATUS_PARSE_ERROR)
    return max(candidates, key=lambda s: STATUS_PRIORITY.get(s, -1))


class RuleOutcomeSignal(BaseException):
    """规则主动上报非 fail 结局的信号基类（继承 BaseException 见 RuleDeferred 说明）。"""

    status = STATUS_INSUFFICIENT_DATA

    def __init__(
        self,
        rule_id: str,
        detail: str = "",
        partial_issues: Optional[List[Any]] = None,
        unresolved_reasons: Optional[List[str]] = None,
        status: Optional[str] = None,
    ) -> None:
        super().__init__(detail or rule_id)
        self.rule_id = rule_id
        if status:
            self.status = status
        self.detail = detail
        self.partial_issues: List[Any] = list(partial_issues or [])
        self.unresolved_reasons: List[str] = list(unresolved_reasons or [])


class RuleDeferred(RuleOutcomeSignal):
    """规则判定数据不足：不阻断已确认 finding，记为 insufficient_data。"""

    status = STATUS_INSUFFICIENT_DATA

    def __init__(
        self,
        rule_id: str,
        detail: str = "",
        partial_issues: Optional[List[Any]] = None,
        unresolved_reasons: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            rule_id,
            detail=detail,
            partial_issues=partial_issues,
            unresolved_reasons=unresolved_reasons,
        )

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"insufficient_data: {self.rule_id}: {self.detail}"


class RuleNotApplicable(RuleOutcomeSignal):
    """规则对当前材料不适用（如科目域/资金口径不可比）：不阻断已确认 finding。"""

    status = STATUS_NOT_APPLICABLE

    def __init__(
        self,
        rule_id: str,
        detail: str = "",
        partial_issues: Optional[List[Any]] = None,
        unresolved_reasons: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            rule_id,
            detail=detail,
            partial_issues=partial_issues,
            unresolved_reasons=unresolved_reasons,
        )

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"not_applicable: {self.rule_id}: {self.detail}"


class RuleExecutionError(RuleOutcomeSignal):
    """规则代码执行异常：记为 execution_error。"""

    status = STATUS_EXECUTION_ERROR

    def __init__(
        self,
        rule_id: str,
        detail: str = "",
        partial_issues: Optional[List[Any]] = None,
        unresolved_reasons: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            rule_id,
            detail=detail,
            partial_issues=partial_issues,
            unresolved_reasons=unresolved_reasons,
        )

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"execution_error: {self.rule_id}: {self.detail}"


class RuleParseError(RuleOutcomeSignal):
    """解析层失败：记为 parse_error。"""

    status = STATUS_PARSE_ERROR

    def __init__(
        self,
        rule_id: str,
        detail: str = "",
        partial_issues: Optional[List[Any]] = None,
        unresolved_reasons: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            rule_id,
            detail=detail,
            partial_issues=partial_issues,
            unresolved_reasons=unresolved_reasons,
        )

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"parse_error: {self.rule_id}: {self.detail}"


@dataclass
class RuleOutcome:
    """单条规则的一次执行结果。"""

    rule_id: str
    status: str
    detail: str = ""
    # 仅 status=fail 时携带的正式 finding（Issue 或 dict 均可）
    issue: Optional[Any] = None
    # 增量字段：携带的已确认 partial findings 数量
    partial_findings_count: int = 0
    # 增量字段：未完成原因列表（支持双保留：规则缺数据 + 转换失败）
    unresolved_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"rule_id": self.rule_id, "status": self.status}
        if self.detail:
            data["detail"] = self.detail
        if self.partial_findings_count > 0:
            data["partial_findings_count"] = self.partial_findings_count
        if self.unresolved_reasons:
            data["unresolved_reasons"] = self.unresolved_reasons
        return data


def summarize_rule_outcomes(outcomes: Iterable[RuleOutcome]) -> Dict[str, Any]:
    """把逐规则 outcome 汇总为 ``rule_execution_summary``。

    结构（全部为增量字段，向后兼容）::

        {
            "total_rules": 62,
            "executed": 60,          # 已实际执行（pass+fail+not_applicable）
            "pass": 58,
            "fail": 2,
            "not_applicable": 0,
            "insufficient_data": 1,  # 未执行且未产出结论
            "parse_error": 0,
            "execution_error": 1,
            "unresolved_total": 2,   # insufficient+parse+execution 之和
            "failed_rules": [...],   # status=fail 的规则（去重）
            "unresolved_rules": [...],
        }
    """

    counts = {status: 0 for status in ALL_STATUSES}
    failed_rules: List[str] = []
    unresolved_rules: List[Dict[str, Any]] = []
    # 逐规则状态回执。此前摘要只有"未决规则列表"与"命中规则列表"，
    # 通过的规则只剩一个计数——于是"这条检查项到底有没有跑"无法回答，
    # 检查义务台账只能靠总数推断（总数对不上就什么都证明不了）。
    # 这里把每个规则 id 的最终状态如实列出，让台账能逐条对账。
    rule_statuses: Dict[str, str] = {}
    total = 0
    partial_findings_total = 0
    for outcome in outcomes:
        total += 1
        status = outcome.status if outcome.status in counts else STATUS_EXECUTION_ERROR
        counts[status] += 1
        partial_findings_total += outcome.partial_findings_count
        # 同一规则编号在多轮/多渠道被调度时，保留更严重的状态，
        # 避免后一次 pass 覆盖前一次 execution_error。
        previous = rule_statuses.get(outcome.rule_id)
        if previous is None or STATUS_PRIORITY.get(status, -1) > STATUS_PRIORITY.get(
            previous, -1
        ):
            rule_statuses[outcome.rule_id] = status
        if status == STATUS_FAIL and outcome.rule_id not in failed_rules:
            failed_rules.append(outcome.rule_id)
        if status in NON_FINDING_STATUSES and status != STATUS_NOT_APPLICABLE:
            unresolved_rules.append(outcome.to_dict())

    unresolved_total = (
        counts[STATUS_INSUFFICIENT_DATA]
        + counts[STATUS_PARSE_ERROR]
        + counts[STATUS_EXECUTION_ERROR]
    )
    return {
        "total_rules": total,
        "executed": counts[STATUS_PASS] + counts[STATUS_FAIL] + counts[STATUS_NOT_APPLICABLE],
        "pass": counts[STATUS_PASS],
        "fail": counts[STATUS_FAIL],
        "not_applicable": counts[STATUS_NOT_APPLICABLE],
        "insufficient_data": counts[STATUS_INSUFFICIENT_DATA],
        "parse_error": counts[STATUS_PARSE_ERROR],
        "execution_error": counts[STATUS_EXECUTION_ERROR],
        "unresolved_total": unresolved_total,
        "failed_rules": failed_rules,
        "unresolved_rules": unresolved_rules,
        "partial_findings_total": partial_findings_total,
        "rule_statuses": rule_statuses,
    }


def empty_rule_execution_summary() -> Dict[str, Any]:
    """空摘要（没有任何规则被调度时使用），供契约字段占位。"""

    summary = summarize_rule_outcomes([])
    return summary
