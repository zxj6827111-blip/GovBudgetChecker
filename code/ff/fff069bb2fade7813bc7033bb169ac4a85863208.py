"""基线臂：pdfplumber 参数组，在 T0 逐格真值上出分。零安装。

用法： .venv/bin/python scratch/engine_ab_20261025/run_native.py [--out results_native.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pdfplumber  # noqa: E402
from ab_lib import Grid, corpus_path, grid_pdfplumber, load_t0, score_case  # noqa: E402

ARMS = [
    {"arm_name": "plumber-snap3(现状默认)", "vertical_strategy": "lines", "horizontal_strategy": "lines",
     "snap_tolerance": 3, "join_tolerance": 3, "edge_min_length": 3},
    {"arm_name": "plumber-lines_strict", "vertical_strategy": "lines_strict",
     "horizontal_strategy": "lines_strict", "snap_tolerance": 2, "join_tolerance": 2, "edge_min_length": 2},
    {"arm_name": "plumber-text", "vertical_strategy": "text", "horizontal_strategy": "text",
     "min_words_vertical": 2, "min_words_horizontal": 1, "intersection_tolerance": 5},
    {"arm_name": "plumber-snap6(IR-11候选)", "vertical_strategy": "lines", "horizontal_strategy": "lines",
     "snap_tolerance": 6, "join_tolerance": 3, "edge_min_length": 3},
    {"arm_name": "plumber-snap8", "vertical_strategy": "lines", "horizontal_strategy": "lines",
     "snap_tolerance": 8, "join_tolerance": 3, "edge_min_length": 3},
    {"arm_name": "plumber-snap10", "vertical_strategy": "lines", "horizontal_strategy": "lines",
     "snap_tolerance": 10, "join_tolerance": 4, "edge_min_length": 3},
]


def grids_for_page(pdf_path: pathlib.Path, page_number: int) -> list[Grid]:
    grids = []
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        for arm in ARMS:
            grids.append(grid_pdfplumber(page, dict(arm), page_number))
    return grids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent / "results_native.json"))
    args = ap.parse_args()
    t0 = load_t0()
    cases = t0["cells"]
    results = []
    for case in cases:
        path = corpus_path(case["file"])
        if not path.exists():
            results.append({"case_id": case["id"], "missing_file": str(path)})
            continue
        scored = score_case(case, grids_for_page(path, case["page"]))
        results.append(scored)
        print(f"\n=== {scored['case_id']} {case['category']} p{case['page']} "
              f"列锚点未解析={scored['unresolved_columns']} 行锚点未解析={scored['unresolved_rows']}")
        for arm, data in scored["arms"].items():
            gm = data["grid_metrics"]
            print(f"  {arm:34s} 表{gm['tables']:2d} 最大表{gm['largest_rows']}x{gm['largest_cols']:2d} "
                  f"格{gm['cells']:5d} 空格比{gm['blank_ratio']} 两金额格{gm['cells_with_two_amounts']:3d} "
                  f"{gm['elapsed_s']:6.2f}s 判定={data['tally']} 表头可追溯={data['header_trace']}")
            for hf in data["hard_fail"][:6]:
                print(f"      ! {hf['row']:>8s} {hf['column']:24s} 真值={hf['truth']:>12s} "
                      f"{hf['verdict']:26s} {hf.get('detail','')}")
    pathlib.Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n写入 {args.out}")


if __name__ == "__main__":
    main()
