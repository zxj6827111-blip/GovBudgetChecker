"""2A：单位错主问题与下游回执（缺失单位/零值单位/差额与方向解释）。

覆盖 R1 v2 的 9 类负控与三类 W01：
- 显式单位冲突（既有 CMM-011）；
- 完全缺单位（同段万元对照）；
- 零值金额写作“0 元”。
V33-251/V33-110 只在差额/方向被单位错精确解释时转回执，金额子检查保持 executed。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.checklist_text_rules import CMM011_AmountUnitWan  # noqa: E402
from src.engine.rules_v33 import (  # noqa: E402
    _unit_error_explains_amount,
    _unit_error_explains_direction,
)


def _issues(text: str, *, pages=None):
    doc = SimpleNamespace(page_texts=pages if pages is not None else [text], tables=[])
    return CMM011_AmountUnitWan().apply(doc)


def _messages(issues):
    return [str(issue.message) for issue in issues]


def test_missing_unit_detected_with_wan_siblings():
    text = ("一般公共预算财政拨款支出109,202.39万元，主要用于以下方面："
            "科学技术支出11,198.78万元，占10.26%；自然资源海洋气象等支出623.91，占0.57%；"
            "住房保障支出2,756.88万元，占2.52%。")
    messages = _messages(_issues(text))
    assert any("623.91" in message and "缺少单位" in message for message in messages)


def test_zero_value_unit_detected_with_wan_siblings():
    text = "年初预算为0 元,支出决算为10,142.71万元，决算数大于预算数的主要原因：口径因素。"
    messages = _messages(_issues(text))
    assert any("0 元" in message and "零值金额单位" in message for message in messages)


def test_missing_unit_negative_controls():
    # 单价/人均语境、编码/人数、无万元对照、表格页均不报。
    assert not any("缺少单位" in m for m in _messages(_issues(
        "人均补助标准623.91，占0.57%；合计1,000.00万元。")) )
    assert not any("缺少单位" in m for m in _messages(_issues(
        "科目编码2080505，占0.57%；本年支出1,000.00万元。")))
    # 同段没有任何万元金额 → 不认定漏写万元（避免把元/计数误判）。
    assert not any("缺少单位" in m for m in _messages(_issues(
        "自然资源海洋气象等支出623.91，占0.57%；住房保障支出2756.88，占2.52%。")))
    assert not any("缺少单位" in m for m in _messages(_issues(
        "合计1,000.00万元；自然资源海洋气象等支出623.91万元，占0.57%。")))


def test_zero_value_negative_when_no_wan_context():
    assert not any("零值金额单位" in m for m in _messages(_issues(
        "年初预算为0 元，本年无此支出。")))


def test_unit_error_explains_amount_only_when_within_tolerance():
    assert _unit_error_explains_amount("-218.178180", "218.178180", "0.01")
    assert _unit_error_explains_amount("-665.523442", "665.513442", "0.01")
    assert not _unit_error_explains_amount("-218.178180", "100.000000", "0.01")
    assert not _unit_error_explains_amount("abc", "1", "0.01")


def test_unit_error_explains_direction_for_yuan_vs_wan():
    # 151.35万元 vs 218.20元，原文“大于”：按 218.20 万元重算成立。
    assert _unit_error_explains_direction(151.35, "万元", 218.20, "元", "大于")
    # 230.00元 vs 226.48万元，原文“小于”：按 230.00 万元重算成立。
    assert _unit_error_explains_direction(0.023, "元", 226.48, "万元", "小于")
    # 两侧同为万元：不解释。
    assert not _unit_error_explains_direction(151.35, "万元", 218.20, "万元", "大于")
    # 重算后方向仍不成立：不解释。
    # 300万元 vs 218.20元（0.02182万元）：按名义值 218.20 万元仍小于 300，方向不成立。
    assert not _unit_error_explains_direction(300.0, "万元", 0.02182, "元", "大于")
