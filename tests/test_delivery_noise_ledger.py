"""D0 RC-5b 回归：交付前噪声过滤显式留痕。

控制试验（计划第二节）：同一检查结果仅改变提示措辞（消息含/不含模式词），
交付数 0↔1——静默按消息关键词丢弃。修复后：被丢弃的问题逐条进入
doc.delivery_noise_ledger（规则、命中模式、消息摘要），过滤可审计。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.rules_v33 import order_and_number_issues  # noqa: E402


def _issue(rule="V33-000", message="正常问题"):
    return SimpleNamespace(rule=rule, message=message, desc=None,
                           location={"page": 1, "pos": 0})


def test_normal_issue_passes_without_ledger():
    doc = SimpleNamespace()
    out = order_and_number_issues(doc, [_issue()])
    assert len(out) == 1
    assert getattr(doc, "delivery_noise_ledger", []) == []


def test_noise_drop_is_ledgered_and_logged():
    doc = SimpleNamespace()
    out = order_and_number_issues(
        doc,
        [_issue("V33-999", "表×格式错误示例"),
         _issue("V33-998", "正常问题甲"),
         _issue("V33-997", "正常问题乙")],
    )
    assert len(out) == 2
    ledger = getattr(doc, "delivery_noise_ledger", [])
    assert len(ledger) == 1
    assert ledger[0]["rule"] == "V33-999"
    assert ledger[0]["pattern"] == "格式错误"
    assert "表×格式错误示例" in ledger[0]["message_excerpt"]
