#!/usr/bin/env python
"""生成 R11-01 表范围控制原件：表外章节标题含跨行年度 +  ruled 表格。

用途：原件侧对账不得把表外章节标题"2025 年/度决算表"的年度当成金额。
运行：.venv/bin/python tests/fixtures/r11/make_section_title_fixture.py
"""
from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parent / "section_title_year.pdf"


def main() -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    font = "china-s"
    page.insert_text((50, 60), "第二部分 测试单位 2025 年", fontname=font, fontsize=12)
    page.insert_text((50, 80), "度决算表", fontname=font, fontsize=12)
    page.insert_text((50, 110), "收入支出决算总表", fontname=font, fontsize=14)
    page.insert_text((50, 126), "单位：万元", fontname=font, fontsize=9)
    left, top = 50, 140
    col_w = [180, 80, 180, 80]
    row_h = 24
    xs = [left]
    for width in col_w:
        xs.append(xs[-1] + width)
    right = xs[-1]
    rows = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["一、一般公共预算财政拨款收入", "100.00", "一、一般公共服务支出", "80.00"],
        ["二、政府性基金预算财政拨款收入", "20.00", "二、教育支出", "40.00"],
        ["本年收入合计", "120.00", "本年支出合计", "120.00"],
    ]
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            if text:
                page.insert_text((xs[ci] + 4, top + ri * row_h + 16),
                                 text, fontname=font, fontsize=9)
    bottom = top + len(rows) * row_h
    for x in xs:
        page.draw_line((x, top), (x, bottom))
    for ri in range(len(rows) + 1):
        page.draw_line((left, top + ri * row_h), (right, top + ri * row_h))
    doc.save(str(OUT))
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()