"""V33-223 说明6↔T6 双侧行格身份回归（D0 RC-2a）。

真实场景（宜川路街道办事处本级 2025 单位决算，P22-23）：
T6 表尾续页行是双侧行 ``[人员经费合计,,2468.48,公用经费合计,,6376.12]``。
旧实现按行内 ``max(vals)`` 取值——两个标签都命中同一行、又都取到行内最大值
6376.12（公用经费），人员经费被错配成 6376.12，真差异（2347.89 vs 2468.48，
差 120.59 = T6 抚恤金 30304）被错误呈现为 2347.89 vs 6376.12。

修复后：人员/公用各取标签同侧值；说明6原文（2347.89/514.97）与表尾
（2468.48/6376.12）两侧真实不一致都照常交付。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.rules_v33 import R33223_Narrative6_T6, build_document  # noqa: E402


def _document() -> Any:
    page_texts = [
        # P1 封面/目录锚
        "上海市某某街道办事处2025年度单位决算\n目录\n"
        "六、一般公共预算财政拨款基本支出决算情况说明\n",
        # P2 T6 首页（经济分类明细）
        "一般公共预算财政拨款基本支出决算表\n单位：万元\n"
        "301 工资福利支出 2190.83 302 商品和服务支出 6315.83\n"
        "30101 基本工资 310.00 30201 办公费 38.53\n",
        # P3 T6 续页表尾双侧行（真实版式：一行两侧标签）
        "31022 无形资产购置\n31099 其他资本性支出\n"
        "人员经费合计 2468.48 公用经费合计 6376.12\n"
        "注：本表反映单位本年度一般公共预算财政拨款基本支出明细情况。\n",
        # P4 说明六（原文数字与表尾真实不一致：2347.89≠2468.48）
        "六、一般公共预算财政拨款基本支出决算情况说明\n"
        "一般公共预算财政拨款基本支出 2862.86 万元。其中：人员\n"
        "经费 2347.89 万元，主要包括：基本工资、津贴补贴；公用\n"
        "经费 514.97 万元，主要包括：办公费、劳务费。\n",
    ]
    page_tables = [
        [],
        [
            [["301", "工资福利支出", "2190.83", "302", "商品和服务支出", "6315.83"]],
        ],
        [
            [
                ["31022", "无形资产购置", ""],
                ["31099", "其他资本性支出", ""],
                ["人员经费合计", "", "2468.48", "公用经费合计", "", "6376.12"],
            ],
        ],
        [],
    ]
    return build_document(
        path="dual-side-t6.pdf",
        page_texts=page_texts,
        page_tables=json.loads(json.dumps(page_tables)),
        filesize=0,
    )


def _issues(doc: Any) -> List[Any]:
    return list(R33223_Narrative6_T6().apply(doc))


def test_dual_side_tail_row_values_bound_to_their_own_labels():
    """人员经费必须取 2468.48（同侧），不得错配行内最大值 6376.12。"""
    issues = _issues(_document())
    messages = [str(i.message) for i in issues]
    personnel = next((m for m in messages if "人员经费不一致" in m), None)
    assert personnel is not None, f"人员经费差异应照常交付: {messages}"
    # 表侧值 = 标签同侧 2468.48，不是行内最大值 6376.12
    assert "2468.48" in personnel, personnel
    assert "6376.12" not in personnel, f"人员经费错配公用经费值: {personnel}"
    assert "2347.89" in personnel, personnel
    public = next((m for m in messages if "公用经费不一致" in m), None)
    assert public is not None, f"公用经费差异应照常交付: {messages}"
    assert "6376.12" in public and "514.97" in public, public


def test_personnel_evidence_is_verbatim_narrative_sentence():
    """evidence 是说明侧原句，不含拼贴标签（D0 RC-1b 契约）。"""
    issues = _issues(_document())
    personnel = next(i for i in issues if "人员经费不一致" in str(i.message))
    evidence = str(personnel.evidence_text or "")
    assert "2347.89" in evidence.replace(" ", "")
    assert "文档说明" not in evidence and "表6" not in evidence


def test_missing_tail_row_is_explicit_insufficient_not_silent_pass():
    """表尾合计行缺席时记取数不足，不得静默跳过该侧比较。"""
    from src.engine.rule_outcome import RuleDeferred

    doc = _document()
    # 摘掉续页表尾双侧行
    doc.page_tables[2] = [["31099", "其他资本性支出", ""]]
    try:
        R33223_Narrative6_T6().apply(doc)
        raise AssertionError("缺表尾合计行应记取数不足（RuleDeferred）")
    except RuleDeferred as exc:
        assert "人员经费合计" in str(getattr(exc, "detail", "")) or "人员经费合计" in str(exc)
