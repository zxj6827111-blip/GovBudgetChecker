"""流水线测试共用的规则执行摘要桩。

为什么需要单独一个模块
----------------------
``_run_pipeline_inner`` 的多个测试各自内联了一份 `rule_execution_summary` 桩。
2026-09-16 引入逐规则回执（``rule_statuses``）后，内联桩与生产契约脱节：
桩只声明 `total_rules/executed` 这类总数，而检查义务台账要按**规则编号**逐条
核对"这项检查到底跑没跑"，总数无法证明任何一条具体规则执行过，于是所有
义务都会落到"未执行"。把桩集中在这里有两点好处：

1. 桩的规则编号直接从真实注册表取，不可能再与生产契约漂移；
2. "未执行"与"尚未实现"是不同结论——桩只能消除前者。真实的实现缺口
   （清单里的 ``pending_checkers``）不会被桩掩盖，这正是我们要的。
"""

from __future__ import annotations

from typing import Any, Dict

from src.engine import check_obligations


def full_rule_receipt(report_kind: str = "budget") -> Dict[str, Any]:
    """构造"全部适用规则均已执行且无未决项"的规则执行摘要。"""
    rules = sorted(check_obligations.registered_rule_ids(report_kind))
    return {
        "total_rules": len(rules),
        "executed": len(rules),
        "pass": len(rules),
        "fail": 0,
        "not_applicable": 0,
        "insufficient_data": 0,
        "parse_error": 0,
        "execution_error": 0,
        "unresolved_total": 0,
        "failed_rules": [],
        "unresolved_rules": [],
        "rule_statuses": {code: "pass" for code in rules},
    }


def without_gap_obligations(monkeypatch) -> None:
    """把"尚未实现"与 AI 语义义务从清单里摘掉，用于测试"全部完成"路径。

    只改清单、不动任何计数逻辑：这样才能验证"完成"是靠检查真的做完得到的，
    而不是靠把分母改小。反向用它也就说明了——只要清单里有未实现的要求，
    同一条流水线就走不到 done。
    """
    implemented_only = tuple(
        item
        for item in check_obligations.OBLIGATION_CATALOG
        if not item.pending_checkers and not item.requires_ai
    )
    monkeypatch.setattr(
        check_obligations, "OBLIGATION_CATALOG", implemented_only, raising=True
    )
