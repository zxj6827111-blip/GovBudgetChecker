"""对比臂：Camelot 2.0 Lattice/Stream，在与基线臂**同一套判据**上出分。

必须用隔离 venv 的解释器跑： scratch/engine_ab_20261025/.venv-camelot/bin/python

包含三件 GPT6 点名要求的事：
1. 参数扫的不是只有 line_scale，line_tol/joint_tol 才管"相近线合并"；
2. 坐标换算自检（Camelot 的 y 是 PDF 底向上，翻转错了整套打分都会假）；
3. 合法近空表的整表召回，验证"空白格比例高就丢表"是否真的发生。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import camelot  # noqa: E402
import pdfplumber  # noqa: E402
from ab_lib import (CORPUS, Cell, Grid, corpus_path, load_t0, page_words, score_case,  # noqa: E402
                   T0_PATH)  # noqa: E402

ARMS: List[Dict[str, Any]] = [
    {"arm_name": "camelot-lattice默认", "flavor": "lattice", "kwargs": {}},
    {"arm_name": "camelot-lattice line_tol=5", "flavor": "lattice", "kwargs": {"line_tol": 5}},
    {"arm_name": "camelot-lattice line_tol=8", "flavor": "lattice", "kwargs": {"line_tol": 8}},
    {"arm_name": "camelot-lattice line_scale=30", "flavor": "lattice", "kwargs": {"line_scale": 30}},
    {"arm_name": "camelot-lattice line_scale=45", "flavor": "lattice", "kwargs": {"line_scale": 45}},
    {"arm_name": "camelot-lattice joint_tol=5", "flavor": "lattice", "kwargs": {"joint_tol": 5}},
    # C05 上默认配置把一整行跨列文本塞进一个格（两个表头标签同源），shift_text 管这个
    {"arm_name": "camelot-lattice shift_text=rb", "flavor": "lattice", "kwargs": {"shift_text": ["r", "b"]}},
    {"arm_name": "camelot-lattice shift_text=off", "flavor": "lattice", "kwargs": {"shift_text": []}},
    {"arm_name": "camelot-lattice copy_text=h", "flavor": "lattice", "kwargs": {"copy_text": ["h"]}},
    {"arm_name": "camelot-stream", "flavor": "stream", "kwargs": {}},
]


def grid_camelot(pdf_path: pathlib.Path, page_number: int, arm: Dict[str, Any],
                 page_height: float) -> Grid:
    params = {"arm_name": arm["arm_name"], "flavor": arm["flavor"], **arm["kwargs"]}
    started = time.perf_counter()
    try:
        tables = camelot.read_pdf(str(pdf_path), pages=str(page_number), flavor=arm["flavor"],
                                  suppress_stdout=True, **arm["kwargs"])
    except Exception as exc:  # 引擎在该页直接失败也要留下证据
        return Grid(engine="camelot", params=params, page_number=page_number,
                    elapsed_s=time.perf_counter() - started, note=f"ERROR {type(exc).__name__}: {exc}")
    grid = Grid(engine="camelot", params=params, page_number=page_number)
    for gi, t in enumerate(tables):
        grid.table_bboxes.append((t._bbox[0], page_height - t._bbox[3], t._bbox[2], page_height - t._bbox[1]))
        values = t.df.values.tolist()
        for r, row_cells in enumerate(t.cells):
            for c, cell in enumerate(row_cells):
                text = "" if r >= len(values) or c >= len(values[r]) else ("" if values[r][c] is None
                                                                           else str(values[r][c]))
                grid.cells.append(Cell(text=text, x0=cell.x1, top=page_height - cell.y2,
                                       x1=cell.x2, bottom=page_height - cell.y1,
                                       row=r, col=c, grid_id=gi))
    grid.elapsed_s = time.perf_counter() - started
    if tables and getattr(tables[0], "rotation", 0):
        grid.note = f"页面旋转 {tables[0].rotation}"
    return grid


def coordinate_selfcheck(pdf_path: pathlib.Path, page_number: int, arm: Dict[str, Any]) -> Dict[str, Any]:
    """坐标换算自检：文字层里每个金额，必须恰好落在 1 个单元格里。

    翻转或缩放搞错时，金额会全部落到格子外（uncovered 高）或跨两个格（multi 高）；
    这一步先证明尺子本身没歪，再谈引擎好坏。
    """
    import re
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        height = float(page.height)
        words = [w for w in page.extract_words() if re.fullmatch(r"[\d,]+\.\d+", w["text"])]
    grid = grid_camelot(pdf_path, page_number, arm, height)
    uncovered = multi = 0
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2.0, (w["top"] + w["bottom"]) / 2.0
        hits = [c for c in grid.cells if c.x0 <= cx < c.x1 and c.top <= cy < c.bottom]
        if not hits:
            uncovered += 1
        elif len({(c.grid_id, c.col) for c in hits}) > 1:
            multi += 1
    return {"arm": arm["arm_name"], "amount_words": len(words), "uncovered": uncovered,
            "in_multiple_cells": multi, "cells": len(grid.cells)}


def near_empty_recall(pdf_path: pathlib.Path, page_numbers: List[int]) -> List[Dict[str, Any]]:
    """合法近空表：基线判为高空格比的表页，逐页问 Camelot 还认不认这张表。"""
    with pdfplumber.open(pdf_path) as pdf:
        out = []
        for pn in page_numbers:
            page = pdf.pages[pn - 1]
            height = float(page.height)
            base = _plumber_grid(page, pn, height)
            rows = _arms_grid(pdf_path, pn, height, ARMS[0])
            out.append({
                "page": pn,
                "baseline_tables": base["tables"], "baseline_blank_ratio": base["blank_ratio"],
                "camelot_tables": rows["tables"], "camelot_blank_ratio": rows["blank_ratio"],
                "camelot_cells": rows["cells"],
                "dropped_by_camelot": base["tables"] > 0 and rows["tables"] == 0,
            })
    return out


def _plumber_grid(page, pn: int, height: float) -> Dict[str, Any]:
    from ab_lib import grid_pdfplumber
    g = grid_pdfplumber(page, {"arm_name": "plumber-snap3"}, pn)
    m = g.metrics()
    return {"tables": m["tables"], "blank_ratio": m["blank_ratio"], "cells": m["cells"]}


def _arms_grid(pdf_path: pathlib.Path, pn: int, height: float, arm: Dict[str, Any]) -> Dict[str, Any]:
    g = grid_camelot(pdf_path, pn, arm, height)
    return g.metrics()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent / "results_camelot.json"))
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--recall", default="", help="逗号分隔的 PDF 相对路径:页码，如 'a/b.pdf:8,9'")
    args = ap.parse_args()

    t0 = load_t0()
    results = []
    for case in t0["cells"]:
        path = corpus_path(case["file"])
        if not path.exists():
            results.append({"case_id": case["id"], "missing_file": str(path)})
            continue
        with pdfplumber.open(path) as pdf:
            height = float(pdf.pages[case["page"] - 1].height)
        grids = [grid_camelot(path, case["page"], arm, height) for arm in ARMS]
        scored = score_case(case, grids)
        results.append(scored)
        print(f"\n=== {scored['case_id']} {case['category']} p{case['page']} "
              f"列锚点未解析={scored['unresolved_columns']} 行锚点未解析={scored['unresolved_rows']}")
        for arm_name, data in scored["arms"].items():
            gm = data["grid_metrics"]
            print(f"  {arm_name:34s} 表{gm['tables']:2d} 最大表{gm['largest_rows']}x{gm['largest_cols']:2d} "
                  f"格{gm['cells']:5d} 空格比{gm['blank_ratio']} 两金额格{gm['cells_with_two_amounts']:3d} "
                  f"{gm['elapsed_s']:6.2f}s {gm['note']}")
            print(f"      判定={data['tally']} 表头可追溯={data['header_trace']}")
            for hf in data["hard_fail"][:4]:
                print(f"      ! {hf['row']:>8s} {hf['column']:24s} 真值={hf['truth']:>12s} "
                      f"{hf['verdict']:26s} {hf.get('detail','')}")

    if args.selfcheck:
        print("\n=== 坐标换算自检（T0-C01 交通委2022 p9）===")
        p = corpus_path(t0["cells"][0]["file"])
        for arm in ARMS:
            print(" ", json.dumps(coordinate_selfcheck(p, t0["cells"][0]["page"], arm), ensure_ascii=False))

    pathlib.Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n写入 {args.out}")


if __name__ == "__main__":
    main()
