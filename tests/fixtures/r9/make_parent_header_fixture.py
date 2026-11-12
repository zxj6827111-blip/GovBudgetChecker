#!/usr/bin/env python
"""生成 R9-02 父子表头控制原件（收入/支出父栏 + 项目/决算数叶栏）。

用途：V4 父栏目身份对回的端到端控制——正常产物语义采纳，仅互换父表头
（收入↔支出）必须 GAP。金额格与原件 hash 在测试中保持不变。
运行：.venv/bin/python tests/fixtures/r9/make_parent_header_fixture.py
"""
from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parent / "parent_header_income_expenditure.pdf"


def main() -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    font = "china-s"
    left, top = 50, 80
    col_w = [180, 80, 180, 80]
    row_h = 28
    xs = [left]
    for width in col_w:
        xs.append(xs[-1] + width)
    right = xs[-1]
    rows = [
        ["收入", "收入", "支出", "支出"],
        ["项目", "决算数", "项目", "决算数"],
        ["一、一般公共预算财政拨款收入", "100.00", "一、一般公共服务支出", "80.00"],
        ["二、政府性基金预算财政拨款收入", "20.00", "二、教育支出", "40.00"],
        ["本年收入合计", "120.00", "本年支出合计", "120.00"],
    ]
    page.insert_text((left, top - 30), "收入支出决算总表", fontname=font, fontsize=14)
    page.insert_text((left, top - 12), "单位：万元", fontname=font, fontsize=9)
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            page.insert_text((xs[ci] + 4, top + ri * row_h + 18),
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