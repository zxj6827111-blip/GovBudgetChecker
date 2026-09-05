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

只有 ``fail`` 能生成 finding；其余状态一律进入 ``rule_execution_summary``，
作为质量门（fail-closed）与回放评测的输入。
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

# 不允许生成正式 finding 的状态集合
NON_FINDING_STATUSES = frozenset(
    {
        STATUS_NOT_APPLICABLE,
        STATUS_INSUFFICIENT_DATA,
        STATUS_PARSE_ERROR,
        STATUS_EXECUTION_ERROR,
    }
)


class RuleDeferred(BaseException):
    """规则判定数据不足时抛出：不生成 finding，记为 insufficient_data。

    继承 ``BaseException`` 而非 ``Exception``：规则内部常见笼统的
    ``except Exception`` 兜底，若用普通异常会被吞掉重新伪装成正常返回；
    BaseException 会穿透这类兜底，由 pipeline 的规则调度层统一接住。
    """

    def __init__(self, rule_id: str, detail: str = "") -> None:
        super().__init__(detail or rule_id)
        self.rule_id = rule_id
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return f"insufficient_data: {self.rule_id}: {self.detail}"


@dataclass
class RuleOutcome:
    """单条规则的一次执行结果。"""

    rule_id: str
    status: str
    detail: str = ""
    # 仅 status=fail 时携带的正式 finding（Issue 或 dict 均可）
    issue: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"rule_id": self.rule_id, "status": self.status}
        if self.detail:
            data["detail"] = self.detail
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
    unresolved_rules: List[Dict[str, str]] = []
    total = 0
    for outcome in outcomes:
        total += 1
        status = outcome.status if outcome.status in counts else STATUS_EXECUTION_ERROR
        counts[status] += 1
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
    }


def empty_rule_execution_summary() -> Dict[str, Any]:
    """空摘要（没有任何规则被调度时使用），供契约字段占位。"""

    summary = summarize_rule_outcomes([])
    return summary
