"""R2.2 §5：_section_page 只接受章节锚（全文关键词首命中删除）的反例与正控。

错页反例：正文第 1 页任何一句提到"决算"时，旧实现把"说明文本中未提取到"
类缺数位置错标到第 1 页；新实现只认章节标题行，无锚返回 None（未知页码
优先于错误页码）。
"""
from __future__ import annotations

from types import SimpleNamespace

from src.engine.diagnostic_fields import _section_page


def _doc(pages):
    return SimpleNamespace(page_texts=list(pages))


def test_body_keyword_first_hit_is_removed():
    """错页反例：p1 正文句提到"决算"，真正的章节在 p22 → 不得再返回 1。"""
    doc = _doc([
        "甲局2024年度部门决算\n本年决算数比预算减少，具体情况如下。\n",
        '（二）"三公"经费财政拨款支出决算具体情况说明。\n',
    ])
    assert _section_page(doc, "说明文本中未提取到三公") == 2


def test_toc_line_is_not_an_anchor():
    """目录行（点线/行尾页码）不是章节锚。"""
    doc = _doc([
        '目录\n七、财政拨款"三公"经费支出决算情况说明……21\n',
        '七、财政拨款"三公"经费支出决算情况说明\n',
    ])
    assert _section_page(doc, "说明文本中未提取到三公") == 2


def test_body_sentence_with_keyword_is_not_an_anchor():
    """正文长句提到提示词（非标题形态）→ 不是锚。"""
    doc = _doc([
        '甲局2024年度部门决算\n财政拨款"三公"经费支出决算数比2023年度减少5.95万元，下降91.40%，其中：因公出国（境）费支出决算减少6.00万元。\n',
    ])
    assert _section_page(doc, "说明文本中未提取到三公") is None


def test_unnumbered_short_heading_is_an_anchor():
    """无序号但提示词几乎占满整行的短标题也是锚。"""
    doc = _doc([
        "封面\n",
        '财政拨款"三公"经费支出决算表\n',
    ])
    assert _section_page(doc, "三公表金额无法解析") == 2


def test_arabic_numbered_body_item_is_not_an_anchor():
    """阿拉伯序号"1、"是正文条目，不作为章节锚。"""
    doc = _doc([
        "1、因公出国（境）费支出0万元。当年度未安排因公出国。\n",
    ])
    assert _section_page(doc, "说明文本中未提取到三公") is None


def test_no_anchor_returns_none():
    """无锚 → None（缺数位置页码未知，不编页码）。"""
    doc = _doc(["完全没有相关内容的页面\n"])
    assert _section_page(doc, "说明文本中未提取到项目支出") is None
