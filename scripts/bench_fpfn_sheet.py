#!/usr/bin/env python
"""FP/FN 人工归因工作表（WP4-I S1 工具 4）。

从评测产物（eval_benchmark 的逐份报告或 run_benchmark 产物目录）导出
FP/FN 归因 CSV：

- 每行一条待归因项：doc_id / row_type(FP|FN) / rule / truth_group /
  page / message_or_evidence，末尾留空的 ``归因`` 列与 ``备注`` 列；
- FP 归因枚举：rule-logic / parsing / exemption-gap / annotation-miss
  （§3.4：annotation-miss = 实为标注遗漏 → 回修 golden，annotation_version+1）；
- FN 归因枚举：no-rule / rule-defect / parsing；
- 归因回填后重跑 eval_benchmark（消费 annotation_version 修订）即得
  修订后指标——本工具只导出工作表，不做任何自动归因。

用法：
    python scripts/bench_fpfn_sheet.py --eval-dir outputs/benchmark/<ts>/eval \
        --out outputs/benchmark/<ts>/fpfn_sheet.csv
    python scripts/bench_fpfn_sheet.py --replay-dir outputs/benchmark/<ts> \
        --corpus corpus --out outputs/benchmark/<ts>/fpfn_sheet.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FP_ATTRIBUTIONS = ("rule-logic", "parsing", "exemption-gap", "annotation-miss")
FN_ATTRIBUTIONS = ("no-rule", "rule-defect", "parsing")

CSV_FIELDS = [
    "row_type",
    "doc_id",
    "rule",
    "truth_group",
    "page",
    "detail",
    "归因",
    "备注",
]


def _fp_rows(doc_id: str, report: Dict[str, Any]) -> List[dict]:
    rows = []
    for item in report.get("false_positive_details", []):
        rows.append(
            {
                "row_type": "FP",
                "doc_id": doc_id,
                "rule": str(item.get("rule") or ""),
                "truth_group": "",
                "page": item.get("page"),
                "detail": str(item.get("message") or ""),
                "归因": "",
                "备注": "",
            }
        )
    return rows


def _fn_rows(doc_id: str, report: Dict[str, Any]) -> List[dict]:
    rows = []
    for item in report.get("missed", []):
        rows.append(
            {
                "row_type": "FN",
                "doc_id": doc_id,
                "rule": str(item.get("rule_id") or ""),
                "truth_group": str(item.get("truth_group") or ""),
                "page": item.get("page"),
                "detail": str(item.get("evidence") or ""),
                "归因": "",
                "备注": "",
            }
        )
    return rows


def collect_from_eval_reports(eval_reports: Dict[str, Dict[str, Any]]) -> List[dict]:
    """从逐份评测报告收集 FP/FN 行（聚合工具产出的 docs 子结构同样适用）。"""
    rows: List[dict] = []
    for doc_id, report in eval_reports.items():
        rows.extend(_fp_rows(doc_id, report))
        rows.extend(_fn_rows(doc_id, report))
    return rows


def collect_from_replay_dir(replay_dir: Path, corpus_dir: Path, mode: str) -> List[dict]:
    """直接从 run_benchmark 产物评测后收集（无 golden 的材料没有评测面，
    不会出现在工作表里）。"""
    from scripts.eval_benchmark import build_doc_entries

    entries = build_doc_entries(replay_dir, corpus_dir, mode)
    eval_reports = {
        entry["doc_id"]: entry["eval_report"]
        for entry in entries
        if entry.get("eval_report")
    }
    return collect_from_eval_reports(eval_reports)


def write_sheet(rows: List[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--eval-dir", help="评测报告目录（DOC-ID-eval-*.json）")
    group.add_argument("--replay-dir", help="run_benchmark 产物目录（现场评测）")
    parser.add_argument("--corpus", default="corpus", help="语料根目录（--replay-dir 时必读）")
    parser.add_argument("--mode", choices=("auto", "legacy", "structured"), default="auto")
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    args = parser.parse_args()

    if args.eval_dir:
        eval_dir = Path(args.eval_dir)
        if not eval_dir.is_absolute():
            eval_dir = _REPO_ROOT / eval_dir
        eval_reports: Dict[str, Dict[str, Any]] = {}
        for path in sorted(eval_dir.glob("*eval*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            doc_id = str(payload.get("doc_id") or path.stem)
            eval_reports[doc_id] = payload
        rows = collect_from_eval_reports(eval_reports)
    else:
        replay_dir = Path(args.replay_dir)
        if not replay_dir.is_absolute():
            replay_dir = _REPO_ROOT / replay_dir
        corpus_dir = Path(args.corpus)
        if not corpus_dir.is_absolute():
            corpus_dir = _REPO_ROOT / corpus_dir
        rows = collect_from_replay_dir(replay_dir, corpus_dir, args.mode)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    write_sheet(rows, out_path)
    fp = sum(1 for r in rows if r["row_type"] == "FP")
    fn = sum(1 for r in rows if r["row_type"] == "FN")
    print(f"written: {out_path} (FP={fp}, FN={fn})")
    print(f"归因枚举 FP: {FP_ATTRIBUTIONS}；FN: {FN_ATTRIBUTIONS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
