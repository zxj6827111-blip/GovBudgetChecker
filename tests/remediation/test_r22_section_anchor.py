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


def test_ir02_cover_year_line_is_not_an_anchor():
    """IR07：封面"2025年度单位决算"不是章节锚（裸"决算"提示词已移除），
    真正的"支出决算情况说明"标题才定位。"""
    doc = _doc([
        "上海市某局\n2025年度单位决算\n",
        "目录\n一、支出决算情况说明……3\n",
        "一、支出决算情况说明。\n本年收入合计…\n",
    ])
    assert _section_page(doc, "说明文本中未提取到决算数") == 3


def test_r206_tail_less_toc_title_page_is_not_anchor():
    """R2-06（残联 p2 真件形态）：目录页整页无页码尾巴，"三、支出决算情况
    说明"满足标题形态却位于"目 录"标题页 → 不得当正文锚；正文真标题照常命中。"""
    toc = ("目 录\n"
           "第一部分 甲局概况\n"
           "一、主要职能\n"
           "二、机构设置\n"
           "第二部分 甲局2025年度决算表\n"
           "一、收入支出决算总表\n"
           "二、收入决算表\n"
           "三、支出决算表\n"
           "第三部分 甲局2025年度决算情况说明\n"
           "一、收入支出决算总体情况说明\n"
           "二、收入决算情况说明\n"
           "三、支出决算情况说明\n")
    body = ("第三部分 甲局2025年度决算情况说明\n"
            "一、收入支出决算总体情况说明\n"
            "甲局2025年度收入支出总计1,039.92万元。主要原因：人员退休。\n")
    doc = _doc([toc, body])
    assert _section_page(doc, "说明文本中未提取到项目支出") == 2


def test_r206_dense_heading_run_without_toc_title_is_not_anchor():
    """R2-06：无"目 录"标题的目录续页形态——连续≥3行目录条目连排（行末无
    页码）不作锚；被正文隔开的真标题仍可用。"""
    toc_like = ("一、收入支出决算总体情况说明\n"
                "二、收入决算情况说明\n"
                "三、支出决算情况说明\n"
                "四、财政拨款收入支出决算总体情况说明\n")
    body_page = ("上表为本年决算数据。\n"
                 "一、收入支出决算总体情况说明\n"
                 "本年收入合计1,039.92万元，比上年减少14.32万元。\n")
    doc = _doc([toc_like, body_page])
    assert _section_page(doc, "说明文本中未提取到项目支出") == 2


def test_r206_isolated_body_heading_between_paragraphs_still_binds():
    """R2-06 正控：正文标题被正文段落隔开（不构成目录连排）→ 照常作锚，
    目录识别不做过头。"""
    body = ("第三部分 甲局2025年度决算情况说明\n"
            "本年度总收入1,039.92万元，具体情况如下。\n"
            "三、支出决算情况说明\n"
            "本年支出合计980.00万元，其中：项目支出300.00万元。\n")
    doc = _doc([body])
    assert _section_page(doc, "说明文本中未提取到项目支出") == 1


def test_r206_arabic_part_number_toc_entries_form_run():
    """R2-06 兄弟形态（自检补强）：阿拉伯"第1部分"目录条目（无页码尾巴）
    同样构成目录连排区，不作正文锚；其后正文真标题照常命中。"""
    toc = ("第1部分 甲局概况\n"
           "一、主要职能\n"
           "第2部分 甲局2025年度决算表\n"
           "一、收入支出决算总表\n"
           "三、支出决算情况说明\n")
    body = ("第3部分 甲局2025年度决算情况说明\n"
            "一、收入支出决算总体情况说明\n"
            "本年收入合计1,039.92万元。主要原因：人员退休。\n")
    doc = _doc([toc, body])
    assert _section_page(doc, "说明文本中未提取到项目支出") == 2
