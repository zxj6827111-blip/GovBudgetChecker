"""PDF 表格识别能力诊断：判断问题出在文本层、框线还是结构还原。

用法: .venv/bin/python scripts/diag_table_extract.py <pdf> [...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import pdfplumber
import pymupdf


def page_stats(pdf_path: Path) -> None:
    print(f"\n{'='*78}\n{pdf_path.name}\n{'='*78}")
    doc = pymupdf.open(pdf_path)
    print(f"页数: {doc.page_count}")

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            # 框线统计
            n_rect = len(page.rects)
            n_line = len(page.lines)
            n_curve = len(page.curves)
            # 多策略试抽取
            results = {}
            for name, settings in (
                ("lines", dict(vertical_strategy="lines", horizontal_strategy="lines",
                               snap_tolerance=3, join_tolerance=3, edge_min_length=3)),
                ("text", dict(vertical_strategy="text", horizontal_strategy="text",
                              min_words_vertical=2, min_words_horizontal=1,
                              intersection_tolerance=5)),
            ):
                try:
                    tables = page.find_tables(table_settings=settings)
                    results[name] = [
                        (len(t.rows), max((len(r.cells) for r in t.rows), default=0))
                        for t in tables
                    ]
                except Exception as e:  # noqa: BLE001
                    results[name] = f"ERR {e}"

            print(
                f"  p{i+1:>3} chars={len(text):>5} rects={n_rect:>4} lines={n_line:>4} "
                f"curves={n_curve:>3} | lines_tables={results['lines']} | text_tables={results['text']}"
            )


def dump_table(pdf_path: Path, page_no: int, strategy: str = "lines") -> None:
    settings = {
        "lines": dict(vertical_strategy="lines", horizontal_strategy="lines",
                      snap_tolerance=3, join_tolerance=3, edge_min_length=3),
        "text": dict(vertical_strategy="text", horizontal_strategy="text",
                     min_words_vertical=2, min_words_horizontal=1,
                     intersection_tolerance=5),
    }[strategy]
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no - 1]
        tables = page.find_tables(table_settings=settings)
        print(f"\n--- {pdf_path.name} p{page_no} strategy={strategy} 共 {len(tables)} 表 ---")
        for ti, t in enumerate(tables):
            print(f"[表 {ti}] rows={len(t.rows)}")
            for row in t.extract():
                print("   ", row)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    for a in args:
        page_stats(Path(a))
