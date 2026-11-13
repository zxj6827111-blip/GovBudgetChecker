"""首轮分层筛选：不需要逐格真值，用几何与文本指标在 30~60 页上给各臂排序。

分层：重复框线 / 近空表 / 多层表头 / 跨页续表 / 正常表。
指标（都按原件文字层判，不看勾稽是否算平）：
- amount_words / uncovered / multi_covered：金额 token 是否被恰好一个单元格覆盖（原文保留＋坐标可回查）
- header_trace_rate：金额格往上同一条网格列里能不能看到表头词（列身份可否追溯）
- two_amount_cells：一格塞了两个金额（错列的签名）
- table_area_vs_ref：相对参照表区的整表覆盖（整表漏检）
- seconds：耗时

参照表区用「矢量线段密度」独立推出，不用任何一臂的输出，避免拿基线当唯一尺子。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import statistics
import sys
import time
from typing import Any, Dict, List

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pdfplumber  # noqa: E402
from ab_lib import CORPUS, Cell, Grid, grid_pdfplumber  # noqa: E402

AMOUNT_RE = re.compile(r"^[\d,]+\.\d+$")
CODE_RE = re.compile(r"^\d{1,7}$")
PLUMBER_ARMS = [
    {"arm_name": "plumber-snap3(现状)", "snap_tolerance": 3},
    {"arm_name": "plumber-snap6", "snap_tolerance": 6},
]
CAMELOT_ARMS = [
    {"arm_name": "camelot-lattice默认", "flavor": "lattice", "kwargs": {}},
]
HEADER_TOKEN_RE = re.compile(r"[一-鿿]{2,}")
TITLE_RE = re.compile(r"(决算表|预算表|总表|明细表|情况表|分类表)")


def vector_reference_bbox(page) -> List[tuple]:
    """用页上的水平/垂直长线段推参照表区（与解析引擎无关）。

    上海模板的框线是 **0.5pt 宽的矩形**：不能按矩形宽度先筛掉窄的，否则竖线全丢。
    """
    v, h = [], []
    for obj in list(page.rects) + list(page.lines):
        x0, x1 = sorted((obj["x0"], obj["x1"]))
        top, bottom = sorted((obj["top"], obj["bottom"]))
        w, hh = x1 - x0, bottom - top
        if w <= 4 and hh >= 15:
            v.append((x0, top))
        elif hh <= 4 and w >= 15:
            h.append((top, x0, x1))
    if len(v) < 3 or len(h) < 3:
        return []
    xs = [c for c, _ in v] + [x0 for _, x0, _ in h] + [x1 for _, _, x1 in h]
    ys = [t for _, t in v] + [t for t, _, _ in h]
    return [(min(xs), min(ys), max(xs), max(ys))]


def classify(page, arm: Dict[str, Any]) -> Dict[str, Any]:
    g = grid_pdfplumber(page, dict(arm, arm_name=arm["arm_name"]), page.page_number + 1)
    m = g.metrics()
    rows = g.metrics()["largest_rows"]
    # 第一个含数字的行之前有几行纯文字表头
    header_rows = 0
    by_row: Dict[int, List[Cell]] = {}
    for c in g.cells:
        by_row.setdefault(c.row, []).append(c)
    for r in sorted(by_row):
        texts = [(c.text or "") for c in by_row[r]]
        if any(AMOUNT_RE.match(t.strip()) for t in texts):
            break
        if any(HEADER_TOKEN_RE.search(t) for t in texts):
            header_rows += 1
    ref = vector_reference_bbox(page)
    return {
        "cols": m["largest_cols"], "rows": rows, "blank_ratio": m["blank_ratio"],
        "header_rows": header_rows, "ref_area": ref, "cells": m["cells"],
    }


def measure(grid: Grid, amount_words: List[Dict[str, float]], ref_area: List[tuple]) -> Dict[str, Any]:
    # 只统计参照表区内的金额：区外的表注数字对任何引擎都不该进网格
    words = amount_words
    if ref_area:
        x0, y0, x1, y1 = ref_area[0]
        words = [w for w in amount_words if x0 <= (w["x0"] + w["x1"]) / 2 <= x1
                 and y0 <= (w["top"] + w["bottom"]) / 2 <= y1]
    uncovered = multi = 0
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2.0, (w["top"] + w["bottom"]) / 2.0
        hits = [c for c in grid.cells if c.x0 <= cx < c.x1 and c.top <= cy < c.bottom]
        if not hits:
            uncovered += 1
        elif len({(c.grid_id, c.row, c.col) for c in hits}) > 1:
            multi += 1
    # 表头同列可追溯率：金额格往上同列是否有含中文的格
    trace_ok = trace_n = 0
    by_table_rows: Dict[Any, Any] = {}
    for c in grid.cells:
        by_table_rows.setdefault((c.grid_id, c.col), []).append(c)
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2.0, (w["top"] + w["bottom"]) / 2.0
        cell = next((c for c in grid.cells if c.x0 <= cx < c.x1 and c.top <= cy < c.bottom), None)
        if cell is None:
            continue
        trace_n += 1
        above = [c for c in by_table_rows.get((cell.grid_id, cell.col), [])
                 if c.row < cell.row and HEADER_TOKEN_RE.search(c.text or "")]
        if above:
            trace_ok += 1
    ref_cov = None
    if ref_area:
        x0, y0, x1, y1 = ref_area[0]
        area = max(1e-6, (x1 - x0) * (y1 - y0))
        inter = 0.0
        for b in grid.table_bboxes:
            ix0, iy0 = max(b[0], x0), max(b[1], y0)
            ix1, iy1 = min(b[2], x1), min(b[3], y1)
            if ix1 > ix0 and iy1 > iy0:
                inter += (ix1 - ix0) * (iy1 - iy0)
        ref_cov = round(inter / area, 3)
    m = grid.metrics()
    return {
        "amount_words": len(words), "amount_words_page": len(amount_words),
        "uncovered": uncovered, "multi_covered": multi,
        "header_trace_rate": round(trace_ok / trace_n, 3) if trace_n else None,
        "two_amount_cells": m["cells_with_two_amounts"], "tables": m["tables"],
        "largest_cols": m["largest_cols"], "largest_rows": m["largest_rows"],
        "blank_ratio": m["blank_ratio"], "ref_coverage": ref_cov, "seconds": m["elapsed_s"],
        "note": m["note"],
    }


def grid_camelot(pdf_path: pathlib.Path, page_index: int, arm: Dict[str, Any], page_height: float) -> Grid:
    import camelot
    started = time.perf_counter()
    params = {"arm_name": arm["arm_name"], "flavor": arm["flavor"], **arm["kwargs"]}
    try:
        tables = camelot.read_pdf(str(pdf_path), pages=str(page_index + 1), flavor=arm["flavor"],
                                  suppress_stdout=True, **arm["kwargs"])
    except Exception as exc:
        return Grid(engine="camelot", params=params, page_number=page_index + 1,
                    elapsed_s=time.perf_counter() - started, note=f"ERROR {type(exc).__name__}: {exc}")
    grid = Grid(engine="camelot", params=params, page_number=page_index + 1)
    for gi, t in enumerate(tables):
        grid.table_bboxes.append((t._bbox[0], page_height - t._bbox[3], t._bbox[2], page_height - t._bbox[1]))
        values = t.df.values.tolist()
        for r, row_cells in enumerate(t.cells):
            for c, cell in enumerate(row_cells):
                text = "" if r >= len(values) or c >= len(values[r]) or values[r][c] is None else str(values[r][c])
                grid.cells.append(Cell(text=text, x0=cell.x1, top=page_height - cell.y2, x1=cell.x2,
                                       bottom=page_height - cell.y1, row=r, col=c, grid_id=gi))
    grid.elapsed_s = time.perf_counter() - started
    return grid


def pick_pages(files: List[pathlib.Path], per_stratum: int, log) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in
                                                ("重复框线", "近空表", "半空表", "三层以上表头", "跨页续表", "正常两层表头")}
    for f in files:
        if all(len(v) >= per_stratum for v in buckets.values()):
            break
        try:
            with pdfplumber.open(f) as pdf:
                for pi, page in enumerate(pdf.pages[:40]):
                    if page.width < page.height:
                        continue
                    page_words = page.extract_words()
                    words = [w for w in page_words if AMOUNT_RE.match(w["text"].strip())]
                    if len(words) < 8:
                        continue
                    a3 = classify(page, PLUMBER_ARMS[0])
                    if a3["cells"] == 0:
                        continue
                    a6 = classify(page, PLUMBER_ARMS[1])
                    has_title = any(TITLE_RE.search(w["text"]) for w in page_words)
                    blank = a3["blank_ratio"] or 0.0
                    if a3["cols"] >= 1.4 * max(a6["cols"], 1) and a3["cols"] >= 12:
                        stratum = "重复框线"
                    elif blank >= 0.7:
                        stratum = "近空表"
                    elif 0.4 <= blank < 0.7:
                        stratum = "半空表"
                    elif a3["header_rows"] >= 3:
                        stratum = "三层以上表头"
                    elif not has_title:
                        stratum = "跨页续表"
                    else:
                        stratum = "正常两层表头"
                    if len(buckets[stratum]) < per_stratum:
                        buckets[stratum].append({"file": str(f), "page_index": pi,
                                                 "page": pi + 1, "amount_words": len(words),
                                                 "plumber_cols": a3["cols"], "plumber_blank": a3["blank_ratio"],
                                                 "header_rows": a3["header_rows"]})
                        log(f"  [{stratum}] {f.name} p{pi+1} 列{a3['cols']}(snap6 {a6['cols']}) "
                            f"空格比{a3['blank_ratio']} 表头行{a3['header_rows']} 金额{len(words)}")
        except Exception as exc:
            log(f"  跳过 {f.name}: {type(exc).__name__} {exc}")
    return buckets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=40)
    ap.add_argument("--per-stratum", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--out", default=str(HERE / "screen_results.json"))
    args = ap.parse_args()

    all_pdfs = sorted(CORPUS.glob("*/*/*.pdf"))
    rnd = random.Random(args.seed)
    sample = rnd.sample(all_pdfs, min(args.docs, len(all_pdfs)))
    print(f"语料 {len(all_pdfs)} 份，抽样 {len(sample)} 份（seed={args.seed}）")
    buckets = pick_pages(sample, args.per_stratum, lambda s: print(s))
    print({k: len(v) for k, v in buckets.items()})

    rows: List[Dict[str, Any]] = []
    for stratum, items in buckets.items():
        for it in items:
            path = pathlib.Path(it["file"])
            with pdfplumber.open(path) as pdf:
                page = pdf.pages[it["page_index"]]
                height = float(page.height)
                amount_words = [{"text": w["text"], "x0": w["x0"], "x1": w["x1"],
                                 "top": w["top"], "bottom": w["bottom"]}
                                for w in page.extract_words() if AMOUNT_RE.match(w["text"].strip())]
                ref = vector_reference_bbox(page)
                arms = [(Grid.__name__ + "|plumber", PLUMBER_ARMS)]
                for arm in PLUMBER_ARMS:
                    g = grid_pdfplumber(page, dict(arm), it["page"])
                    rows.append({"stratum": stratum, "file": path.name, "page": it["page"],
                                 "arm": f"pdfplumber|{arm['arm_name']}",
                                 **measure(g, amount_words, ref)})
                for arm in CAMELOT_ARMS:
                    g = grid_camelot(path, it["page_index"], arm, height)
                    rows.append({"stratum": stratum, "file": path.name, "page": it["page"],
                                 "arm": f"camelot|{arm['arm_name']}",
                                 **measure(g, amount_words, ref)})
    pathlib.Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n===== 分层汇总（每臂均值/合计）=====")
    print(f"{'层':<10}{'臂':<26}{'页':>4}{'金额':>7}{'未覆盖':>7}{'跨格':>6}{'表头同列率':>11}"
          f"{'一格两金额':>11}{'整表覆盖':>9}{'耗时s':>8}")
    for stratum in buckets:
        sub = [r for r in rows if r["stratum"] == stratum]
        if not sub:
            continue
        for arm in dict.fromkeys(r["arm"] for r in sub):
            rs = [r for r in sub if r["arm"] == arm]
            aws = sum(r["amount_words"] for r in rs)
            unc = sum(r["uncovered"] for r in rs)
            mul = sum(r["multi_covered"] for r in rs)
            traces = [r["header_trace_rate"] for r in rs if r["header_trace_rate"] is not None]
            two = sum(r["two_amount_cells"] for r in rs)
            cov = [r["ref_coverage"] for r in rs if r["ref_coverage"] is not None]
            secs = statistics.median([r["seconds"] for r in rs])
            print(f"{stratum:<10}{arm:<26}{len(rs):>4}{aws:>7}{unc:>7}{mul:>6}"
                  f"{(sum(traces)/len(traces) if traces else 0):>11.3f}{two:>11}"
                  f"{(sum(cov)/len(cov) if cov else 0):>9.3f}{secs:>8.2f}")
    print(f"\n明细写入 {args.out}")


if __name__ == "__main__":
    main()
