"""对真实决算 PDF 跑系统解析链，输出每张逻辑表的规模与关键行。

用法: .venv/bin/python scripts/diag_parse_artifact.py <pdf> [--tables]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.pdf_parser import PDFParser  # noqa: E402


def main(pdf_path: str, show_tables: bool = False) -> None:
    parser = PDFParser(None)  # 不落库
    artifact = parser.build_parse_artifact(pdf_path)

    print(f"文件: {Path(pdf_path).name}")
    print(f"页数: {artifact.page_count}")
    scanned = [p.page_number for p in artifact.page_states if p.is_scanned]
    print(f"无文本层页: {scanned or '无'}")
    print(f"物理片段: {len(artifact.physical_fragments)}  逻辑表: {len(artifact.table_fragments)}")
    print(f"文本块: {len(artifact.text_blocks)}")

    codes = Counter(t.table_code for t in artifact.table_fragments)
    print("\n逻辑表清单：")
    for t in artifact.table_fragments:
        pages = sorted({c.page_number for c in t.cells if c.page_number}) if t.cells else []
        page_span = f"p{pages[0]}-{pages[-1]}" if pages else "-"
        print(f"  {t.table_code:<28} rows={len(t.rows):>3} cols={len(t.headers):>2} "
              f"cells={len(t.cells):>4} {page_span} conf={t.confidence:.2f} title={t.title[:40]!r}")

    print("\n表码统计:", dict(codes))
    if show_tables:
        for t in artifact.table_fragments:
            print(f"\n===== {t.table_code} | {t.title} | rows={len(t.rows)} =====")
            for row in t.rows[:8]:
                print("   ", row)
            if len(t.rows) > 12:
                print("    ...")
                for row in t.rows[-3:]:
                    print("   ", row)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    main(args[0], show_tables="--tables" in sys.argv)
