#!/usr/bin/env python3
"""金标准标注的版面证据抽取。

用法：
  python scripts/gold_extract.py pages GS-2022-01      # 导出每页文本与词坐标
  python scripts/gold_extract.py map   GS-2022-01      # 打印页面结构图
  python scripts/gold_extract.py table GS-2022-01 14   # 重建第 14 页表格网格

产物：gold/<sample_id>/pages/pNNN.txt、pNNN.words.json、manifest.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
SOURCE_ROOT = Path.home() / "Desktop" / "预决算信息收集" / "决算测试上海市"
GOLD_DIR = ROOT / "gold"

NUMBER_RE = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$")


def load_samples() -> dict[str, dict]:
    with SAMPLE_CSV.open(encoding="utf-8-sig") as f:
        return {row["sample_id"]: row for row in csv.DictReader(f)}


def resolve_pdf(sample_id: str) -> tuple[Path, dict]:
    samples = load_samples()
    if sample_id not in samples:
        raise SystemExit(f"unknown sample_id: {sample_id}")
    row = samples[sample_id]
    pdf_path = SOURCE_ROOT / row["path"]
    if not pdf_path.exists():
        raise SystemExit(f"missing pdf: {pdf_path}")
    digest = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != row["sha256"]:
        raise SystemExit(f"sha256 mismatch for {sample_id}: {pdf_path}")
    return pdf_path, row


def page_words(page: pdfplumber.page.Page) -> list[dict]:
    out = []
    for w in page.extract_words(keep_blank_chars=False, use_text_flow=False):
        out.append(
            {
                "text": w["text"],
                "x0": round(w["x0"], 1),
                "x1": round(w["x1"], 1),
                "top": round(w["top"], 1),
                "bottom": round(w["bottom"], 1),
            }
        )
    return out


def cluster_rows(words: list[dict], tol: float = 4.0) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    for r in rows:
        r.sort(key=lambda x: x["x0"])
    return rows


def is_number(text: str) -> bool:
    return bool(NUMBER_RE.match(text))


def row_text(row: list[dict]) -> str:
    return " ".join(w["text"] for w in row)


def build_grid(words: list[dict], tol: float = 4.0) -> dict:
    """把一页的词按行聚类，并按表头列中心把数字归列。"""
    rows = cluster_rows(words, tol)
    numeric_rows = [
        (i, [w for w in r if is_number(w["text"])]) for i, r in enumerate(rows)
    ]
    numeric_rows = [(i, ns) for i, ns in numeric_rows if len(ns) >= 2]
    if not numeric_rows:
        return {"rows": rows, "columns": [], "cells": []}

    first_num_row = numeric_rows[0][0]
    # 表头 = 第一个数字行上方、token 最多的一行
    header_candidates = [
        (i, r) for i, r in enumerate(rows[:first_num_row]) if len(r) >= 2
    ]
    if not header_candidates:
        return {"rows": rows, "columns": [], "cells": []}
    header_idx, header_row = max(header_candidates, key=lambda t: len(t[1]))

    columns = []
    for w in header_row:
        cells = [x for x in header_row if x is not w]
        columns.append(
            {
                "label": w["text"],
                "center": round((w["x0"] + w["x1"]) / 2, 1),
                "x0": w["x0"],
                "x1": w["x1"],
            }
        )
    columns.sort(key=lambda c: c["center"])

    assigned = []
    prev_col = -1
    monotonic = True
    for row_i, r in enumerate(rows):
        nums = [w for w in r if is_number(w["text"])]
        for w in nums:
            center = (w["x0"] + w["x1"]) / 2
            best = min(
                range(len(columns)), key=lambda k: abs(columns[k]["center"] - center)
            )
            if best <= prev_col:
                monotonic = False
            prev_col = best
            assigned.append(
                {
                    "row": row_i,
                    "top": w["top"],
                    "text": w["text"],
                    "column": columns[best]["label"],
                    "column_index": best,
                    "drift": round(abs(columns[best]["center"] - center), 1),
                }
            )

    return {
        "rows": rows,
        "header_row": header_idx,
        "columns": columns,
        "cells": assigned,
        "monotonic": monotonic,
    }


def cmd_pages(sample_id: str) -> None:
    pdf_path, row = resolve_pdf(sample_id)
    out_dir = GOLD_DIR / sample_id
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    manifest_pages = []
    with pdfplumber.open(pdf_path) as plumb:
        for i, page in enumerate(doc):
            text = page.get_text()
            (pages_dir / f"p{i + 1:03d}.txt").write_text(text, encoding="utf-8")
            words = page_words(plumb.pages[i])
            (pages_dir / f"p{i + 1:03d}.words.json").write_text(
                json.dumps(words, ensure_ascii=False), encoding="utf-8"
            )
            tables = plumb.pages[i].extract_tables()
            (pages_dir / f"p{i + 1:03d}.tables.json").write_text(
                json.dumps(tables, ensure_ascii=False), encoding="utf-8"
            )
            manifest_pages.append(
                {
                    "page": i + 1,
                    "chars": len(text),
                    "words": len(words),
                    "tables": [
                        {
                            "rows": len(t),
                            "cols": max((len(r) for r in t), default=0),
                        }
                        for t in tables
                    ],
                }
            )

    manifest = {
        "sample_id": sample_id,
        "sha256": row["sha256"],
        "source_path": str(pdf_path),
        "declared_pages": int(row["pages"]),
        "actual_pages": doc.page_count,
        "extractor": {
            "text": f"pymupdf {pymupdf.__version__}",
            "words": f"pdfplumber {pdfplumber.__version__}",
        },
        "pages": manifest_pages,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"{sample_id}: {doc.page_count} pages, declared {row['pages']}, "
        f"out={out_dir.relative_to(ROOT)}"
    )


def cmd_map(sample_id: str, limit: int) -> None:
    pdf_path, row = resolve_pdf(sample_id)
    doc = pymupdf.open(pdf_path)
    print(f"# {sample_id} {row['file']}  pages={doc.page_count}")
    for i, page in enumerate(doc):
        head = re.sub(r"\s+", "", page.get_text())[:limit]
        print(f"{i + 1:3d}  {head}")


def cmd_table(sample_id: str, page_no: int) -> None:
    pdf_path, _ = resolve_pdf(sample_id)
    with pdfplumber.open(pdf_path) as plumb:
        words = page_words(plumb.pages[page_no - 1])
    grid = build_grid(words)
    print(f"# {sample_id} page {page_no}")
    print(f"monotonic={grid.get('monotonic')}")
    print("## columns")
    for k, c in enumerate(grid["columns"]):
        print(f"  [{k}] {c['label']}  center={c['center']}")
    print("## rows")
    for i, r in enumerate(grid["rows"]):
        nums = {c["column_index"]: c["text"] for c in grid["cells"] if c["row"] == i}
        cells = " | ".join(f"{k}:{v}" for k, v in sorted(nums.items()))
        print(f"  {i:3d} {row_text(r)[:90]}")
        if cells:
            print(f"        -> {cells}")


def cmd_show(sample_id: str, page_no: int) -> None:
    path = GOLD_DIR / sample_id / "pages" / f"p{page_no:03d}.tables.json"
    if not path.exists():
        raise SystemExit(f"run `pages {sample_id}` first: {path}")
    tables = json.loads(path.read_text(encoding="utf-8"))
    print(f"# {sample_id} page {page_no}: {len(tables)} table(s)")
    for ti, t in enumerate(tables):
        print(f"## table {ti}  {len(t)} x {max((len(r) for r in t), default=0)}")
        for ri, r in enumerate(t):
            print(f"  {ri:3d} | " + " | ".join((c or "").replace("\n", "/") for c in r))


def cmd_glyph(sample_id: str, page_no: int, text: str) -> None:
    """R11：把含指定文字的页面区域渲染成图，先看字形再判怪字。"""
    pdf_path, _ = resolve_pdf(sample_id)
    doc = pymupdf.open(pdf_path)
    page = doc[page_no - 1]
    hits = page.search_for(text)
    if not hits:
        # 按行拼接 span 文本再找，避免逐字搜索把整页同字都圈进来
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                line_text = "".join(s["text"] for s in line["spans"])
                if text in line_text:
                    hits.append(pymupdf.Rect(line["bbox"]))
        if not hits:
            raise SystemExit(f"not found on page {page_no}: {text!r}")
    rect = pymupdf.Rect(hits[0])
    print(f"{sample_id} p{page_no} {text!r} hits={len(hits)} bboxes="
          f"{[tuple(round(x, 1) for x in b) for b in hits[:4]]}")
    rect = pymupdf.Rect(rect.x0 - 12, rect.y0 - 8, rect.x1 + 12, rect.y1 + 8)
    pix = page.get_pixmap(clip=rect, dpi=300)
    out = GOLD_DIR / sample_id / f"glyph_p{page_no:03d}_{abs(hash(text)) % 100000:05d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out)
    print(f"{sample_id} p{page_no} {text!r} bbox={tuple(round(x, 1) for x in rect)} → {out}")


def cmd_compare(sample_id: str, render: bool = False) -> None:
    """R14：逐页比较 PyMuPDF 与 pdfplumber 的汉字数，找提取漏读页。"""
    pdf_path, row = resolve_pdf(sample_id)
    doc = pymupdf.open(pdf_path)
    flagged = []
    with pdfplumber.open(pdf_path) as plumb:
        for i, page in enumerate(doc):
            t1 = page.get_text()
            t2 = plumb.pages[i].extract_text() or ""
            c1 = len(re.findall(r"[\u4e00-\u9fff]", t1))
            c2 = len(re.findall(r"[\u4e00-\u9fff]", t2))
            diff = c2 - c1
            ratio = (c1 / c2) if c2 else 1.0
            if diff > 20 or ratio < 0.8:
                flagged.append((i + 1, c1, c2, diff, ratio))
                if render:
                    pix = page.get_pixmap(dpi=150)
                    out = GOLD_DIR / sample_id / f"extract_diff_p{i + 1:03d}.png"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    pix.save(out)
                    print(f"  rendered → {out}")
    print(f"# {sample_id} 页数={doc.page_count} 差异页={len(flagged)}")
    for p, c1, c2, diff, ratio in flagged:
        print(f"  p{p}: PyMuPDF={c1} pdfplumber={c2} diff={diff:+d} ratio={ratio:.2f}")
    if not flagged:
        print("  两提取器逐页汉字数无超阈值差异")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("pages")
    p1.add_argument("sample_id")

    p2 = sub.add_parser("map")
    p2.add_argument("sample_id")
    p2.add_argument("--limit", type=int, default=60)

    p3 = sub.add_parser("table")
    p3.add_argument("sample_id")
    p3.add_argument("page", type=int)

    p4 = sub.add_parser("show")
    p4.add_argument("sample_id")
    p4.add_argument("page", type=int)

    p5 = sub.add_parser("glyph")
    p5.add_argument("sample_id")
    p5.add_argument("page", type=int)
    p5.add_argument("text")

    p6 = sub.add_parser("compare")
    p6.add_argument("sample_id")
    p6.add_argument("--render", action="store_true")

    args = ap.parse_args()
    if args.cmd == "pages":
        cmd_pages(args.sample_id)
    elif args.cmd == "map":
        cmd_map(args.sample_id, args.limit)
    elif args.cmd == "show":
        cmd_show(args.sample_id, args.page)
    elif args.cmd == "glyph":
        cmd_glyph(args.sample_id, args.page, args.text)
    elif args.cmd == "compare":
        cmd_compare(args.sample_id, args.render)
    else:
        cmd_table(args.sample_id, args.page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
