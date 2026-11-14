"""C2（2026-11-14 复验）：基金逐项推导——每条声明先独立证实对象。

真实反例：第一条完整标签 + 7402.47；第二条"2、另一项目支出决算为
7402.47万元。"无自身标签，旧实现从前 400 字借到第一条标签，按同码同额
去重后 pass——未证实对象的声明混入了完备集合推导。修复口径：
1. 声明只能借用**同一条目**（最近编号条目起点之后）且位于其前的最近一个
   未被占用的完整标签；跨新编号/标签已被占用一律 insufficient_data；
2. 标签偏移使用 finditer(pos,endpos) 的全文绝对偏移——旧实现 head 切片内
   offset 被直接当全文偏移，长章节下期间/主体守卫范围错位。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.engine.native_final_disclosure import check_fund_totals
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33248_FundTotals
from tests.remediation.test_fund_total_class_rows import _fact, _rule

ORG = "上海市某某事务中心"
YEAR = 2025
LABEL1 = ("城乡社区支出（类）国有土地使用权出让收入安排的支出（款）"
          "土地出让业务支出（项）")
LABEL2 = "城乡社区支出（类）其他土地支出（款）土地整理支出（项）"

#: 两条目合法材料：类 212 合计 = 两项之和。
TWO_ITEM_FACTS = [
    _fact("total", "合计", "income_actual", "7,902.47"),
    _fact("212", "城乡社区支出", "total_actual", "7,902.47"),
    _fact("21208", "国有土地使用权出让收入安排的支出", "total_actual", "7,402.47"),
    _fact("2120801", "土地出让业务支出", "total_actual", "7,402.47"),
    _fact("21209", "其他土地支出", "total_actual", "500.00"),
    _fact("2120901", "土地整理支出", "total_actual", "500.00"),
]


def _doc(facts, body):
    texts = [
        "上海市某某事务中心\n2025 年度单位决算\n",
        "政府性基金预算财政拨款收入支出决算表\n单位：万元\n",
        "八、政府性基金预算财政拨款收入支出决算情况说明\n" + body + "\n",
    ]
    doc = build_document("fund.pdf", texts, [[], [], []], 0)

    class _Artifact:
        source_file_hash = "a" * 64
        parse_run_uid = "run"

    class _Context:
        file_hash = "a" * 64
        parse_run_uid = "run"
        org_name = ORG
        fiscal_year = YEAR
        version_token = "v1"

    _Context.facts = facts
    doc.parse_artifact = _Artifact()
    doc.fact_context = _Context()
    doc.fact_receipts = []
    return doc


def _receipt(doc):
    return doc.fact_receipts[-1]


def test_two_labelled_items_derive_total():
    """合法多项：各条自带标签，逐项之和 = 类级行合计 → pass。"""
    body = (f"1、{LABEL1}。支出决算为 7,402.47 万元。"
            f"2、{LABEL2}。支出决算为 500.00 万元。")
    doc = _doc(TWO_ITEM_FACTS, body)
    issues = check_fund_totals(_rule(), doc)
    assert _receipt(doc)["status"] == "pass"
    assert issues == []


def test_unlabelled_second_item_cannot_borrow_previous_label():
    """第二条无自身标签：不得跨新编号借第一条身份，完备推导拒绝。"""
    body = (f"1、{LABEL1}。支出决算为 7,402.47 万元。"
            "2、另一项目支出决算为 7,402.47 万元。")
    doc = _doc([
        _fact("total", "合计", "income_actual", "7,402.47"),
        _fact("212", "城乡社区支出", "total_actual", "7,402.47"),
        _fact("21208", "国有土地使用权出让收入安排的支出", "total_actual", "7,402.47"),
        _fact("2120801", "土地出让业务支出", "total_actual", "7,402.47"),
    ], body)
    with pytest.raises(RuleDeferred):
        check_fund_totals(_rule(), doc)
    assert _receipt(doc)["status"] == "insufficient_data"
    assert "未伴随自身完整" in (_receipt(doc).get("incomplete_reason") or "")


def test_repeated_same_object_same_amount_dedupes():
    """同条目内重复叙述同一对象且金额一致：有自身标签（重复打印）→ 去重通过。"""
    body = (f"1、{LABEL1}。支出决算为 7,402.47 万元。"
            f"同一项目{LABEL1}。支出决算为 7,402.47 万元。")
    doc = _doc([
        _fact("total", "合计", "income_actual", "7,402.47"),
        _fact("212", "城乡社区支出", "total_actual", "7,402.47"),
        _fact("21208", "国有土地使用权出让收入安排的支出", "total_actual", "7,402.47"),
        _fact("2120801", "土地出让业务支出", "total_actual", "7,402.47"),
    ], body)
    issues = check_fund_totals(_rule(), doc)
    assert _receipt(doc)["status"] == "pass"
    assert issues == []


def test_consumed_label_cannot_serve_second_claim():
    """同条目中标签已被第一条声明占用：第二条无自身标签 → 拒绝（即使同额）。"""
    body = (f"1、{LABEL1}。支出决算为 7,402.47 万元。"
            "此外本项支出决算为 7,402.47 万元。")
    doc = _doc([
        _fact("total", "合计", "income_actual", "7,402.47"),
        _fact("212", "城乡社区支出", "total_actual", "7,402.47"),
        _fact("21208", "国有土地使用权出让收入安排的支出", "total_actual", "7,402.47"),
        _fact("2120801", "土地出让业务支出", "total_actual", "7,402.47"),
    ], body)
    with pytest.raises(RuleDeferred):
        check_fund_totals(_rule(), doc)
    assert _receipt(doc)["status"] == "insufficient_data"


def test_long_preamble_offset_keeps_period_guard_in_scope():
    """长章节偏移：条目前垫 500+ 字含异年叙述，期间锚不得错位扫进垫文本。

    旧实现把 head 切片内 offset 当全文偏移——守卫窗口/期间锚从条目起点
    之前开始，垫文本里的异年叙述会误伤合法条目；修复后使用全文绝对偏移，
    期间锚只看标签前 60 字与本条目自身。
    """
    preamble = ("2024年度结转事项已按规定处理完毕，相关资金已列报。"
                + "其他专项工作均按年度计划推进完成。" * 24)  # 异年距条目 > 60 字
    body = (preamble + f"1、{LABEL1}。支出决算为 7,402.47 万元。"
            "2、另一项目支出决算为 500.00 万元。")
    doc = _doc([
        _fact("total", "合计", "income_actual", "7,402.47"),
        _fact("212", "城乡社区支出", "total_actual", "7,402.47"),
        _fact("21208", "国有土地使用权出让收入安排的支出", "total_actual", "7,402.47"),
        _fact("2120801", "土地出让业务支出", "total_actual", "7,402.47"),
    ], body)
    with pytest.raises(RuleDeferred):
        check_fund_totals(_rule(), doc)
    reason = _receipt(doc).get("incomplete_reason") or ""
    # 第一条不因垫文本异年被误伤（拒绝原因不是期间锚）；第二条无自身标签拒绝。
    assert "携带异年" not in reason
    assert "未伴随自身完整" in reason


def test_wrong_amount_still_fails_with_real_difference():
    """既有负例保持：改金额产生真实差额 fail，不被身份修复吞掉。"""
    body = f"1、{LABEL1}。支出决算为 7,400.00 万元。"
    doc = _doc([
        _fact("total", "合计", "income_actual", "7,402.47"),
        _fact("212", "城乡社区支出", "total_actual", "7,402.47"),
        _fact("21208", "国有土地使用权出让收入安排的支出", "total_actual", "7,402.47"),
        _fact("2120801", "土地出让业务支出", "total_actual", "7,402.47"),
    ], body)
    issues = check_fund_totals(_rule(), doc)
    assert _receipt(doc)["status"] == "fail"
    assert issues and "2.4700" in issues[0].message
