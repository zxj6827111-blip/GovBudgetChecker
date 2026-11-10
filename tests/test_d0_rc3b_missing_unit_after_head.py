"""D0 RC-3b 回归：金额语境词后的小数金额漏写“万元”（CMM-011，C-14）。

宜川部门决算 P29 实证："年初预算为 0.00 万元，支出决算为
4.19。决算数大于预算数的主要原因……"——"4.19"漏写"万元"。
旧实现只查"占"前缺单位金额，该形态漏报。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.checklist_text_rules import CMM011_AmountUnitWan  # noqa: E402
from src.engine.rules_v33 import build_document  # noqa: E402


def _doc(body: str):
    return build_document(
        "unit.pdf",
        ["某某街道 2025 年度部门决算\n三、支出决算情况说明\n" + body],
        [],
        0,
    )


def _hits(doc):
    return [i for i in CMM011_AmountUnitWan().apply(doc)
            if "缺少单位" in str(i.message)]


def test_bare_decimal_after_juesuan_head_is_reported():
    doc = _doc("6、一般公共服务支出（类）市场监督管理事务（款）其他市场监督管理事务（项）"
               "4.19 万元。主要用于：住宅电梯物联网建设及运行补贴资金。年初预算为 0.00 万元，"
               "支出决算为 4.19。决算数大于预算数的主要原因：上级转移支付经费。")
    hits = _hits(doc)
    assert hits, "支出决算为 4.19。应判漏写万元"
    assert any("4.19" in str(i.message) for i in hits)


def test_amounts_with_units_are_not_flagged():
    doc = _doc("年初预算为 0.00 万元，支出决算为 4.19 万元。决算数大于预算数。")
    assert _hits(doc) == []


def test_integer_and_percent_contexts_are_not_flagged():
    doc = _doc("支出决算为 8949.60 万元，完成年初预算的 93.98%。与上年相比增加 4.19 个百分点。")
    assert _hits(doc) == []
