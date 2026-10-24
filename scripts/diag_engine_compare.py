"""引擎对比：pdfplumber vs PyMuPDF 在决算表上的结构还原质量。

评估维度：
- 双栏表能否还原成两栏（列数是否为单栏的 2 倍 + 无串行）
- 表头合并单元格是否保留
- 数值列是否可解析
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NUM = re.compile(r"^-?\d[\d,，]*\.?\d*$")


def show(pdf: str, page_no: int, label: str) -> None:
    doc = pymupdf.open(pdf)
    page = doc[page_no - 1]
    print(f"\n{'='*70}\n{Path(pdf).name} p{page_no} —— {label}\n{'='*70}")
    tabs = page.find_tables()
    print(f"PyMuPDF 找到 {len(tabs.tables)} 张表")
    for ti, t in enumerate(tabs.tables):
        rows = t.extract()
        print(f"\n[表 {ti}] {t.row_count} 行 × {t.col_count} 列  bbox={tuple(round(v) for v in t.bbox)}")
        for r in rows[:6]:
            clean = [str(c or "").replace("\n", "")[:16] for c in r]
            print("   ", clean)
        if len(rows) > 8:
            print("    ...")
            for r in rows[-2:]:
                print("   ", [str(c or "").replace("\n", "")[:16] for c in r])


if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else \
        "uploads/396c1df8f3f6bc0ca068fec153f84d3f/上海市商务委员会.pdf"
    show(pdf, 18, "基本支出表（双栏）")
    show(pdf, 10, "收入决算表（跨页首段）")
