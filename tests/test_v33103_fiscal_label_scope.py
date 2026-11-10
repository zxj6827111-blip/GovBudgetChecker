"""D0 复核（用户裁定 2026-11-10）：财政拨款收入口径标签缺陷必须交付。

背景：RC-2b 按资金口径绑定后，"说明首项'财政拨款收入 8844.60'（实为
一般公共口径）+ 并列政府性基金 105.00"与表侧全口径 8949.60 数值自洽，
数值不一致误报撤下——但首项标签与《收入决算表》财政拨款收入列口径不符
（差 105.00 恰为基金项）是真实缺陷，静默即漏报。修复后交付 medium 标签
缺陷 finding；首项规范写作"一般公共预算财政拨款收入"时不报。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.rules_v33 import R33103_Income_vs_Text, build_document  # noqa: E402


def _doc(narrative: str):
    page_texts = [
        narrative,
        "收入决算表\n单位：万元\n",
    ]
    table = [
        ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
        ["合计", "合计", "8949.60", "8949.60"],
        ["201", "一般公共服务支出", "207.40", "207.40"],
        ["221", "住房保障支出", "105.00", "105.00"],
    ]
    return build_document("label-scope.pdf", page_texts, [[], [table]], 100)


def _issues(doc):
    return list(R33103_Income_vs_Text().apply(doc))


def test_plain_label_defect_is_delivered_not_silenced():
    doc = _doc("二、收入决算情况说明\n本年收入合计 8949.60 万元，其中：财政拨款收入 8844.60"
               "万元，占 98.83%；政府性基金预算财政拨款收入 105.00 万元，占 1.17%。")
    issues = _issues(doc)
    label_hits = [i for i in issues if "口径标签不一致" in str(i.message)]
    assert label_hits, f"标签口径缺陷应交付: {[str(i.message)[:80] for i in issues]}"
    assert label_hits[0].severity == "medium"
    # 数值不一致（跨口径误报）不再交付
    assert not [i for i in issues if "≠ 文本" in str(i.message)], \
        [str(i.message)[:80] for i in issues]


def test_properly_scoped_label_is_not_flagged():
    doc = _doc("二、收入决算情况说明\n本年收入合计 8949.60 万元，其中：一般公共预算"
               "财政拨款收入 8844.60万元，占 98.83%；政府性基金预算财政拨款收入"
               " 105.00 万元，占 1.17%。")
    issues = _issues(doc)
    assert not [i for i in issues if "口径标签不一致" in str(i.message)], \
        [str(i.message)[:80] for i in issues]


def test_no_fund_sibling_keeps_strict_comparison():
    """无并列基金拆项 → 首项就是全口径财政拨款收入，保持原有严格比较。"""
    doc = _doc("二、收入决算情况说明\n本年收入合计 8949.60 万元，其中：财政拨款收入 "
               "8000.00万元，占 89.40%。")
    issues = _issues(doc)
    assert [i for i in issues if "≠ 文本" in str(i.message)], \
        "无拆项绑定时应保持原有数值比较"
    assert not [i for i in issues if "口径标签不一致" in str(i.message)]
