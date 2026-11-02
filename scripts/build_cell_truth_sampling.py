"""IR-18 逐格真值集：分层抽样、渲染原页、生成标注模板。

样本来自 batch2 全量解析（按 SHA 去重），按年度、部门/单位、提取方式、
表种、是否续页分层。真值必须由人工对照 render/*.png 填写；模板里的系统候选
不能当真值，`truth_source` 未改成 human 的样本不进入评分分母。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARSE = ROOT / "outputs/intake_remediation_20261024/batch2_final/parse.jsonl.gz"
DEFAULT_INTAKE = ROOT / "outputs/intake_remediation_20261024/batch2_final/intake.jsonl.gz"
DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir18_cell_truth"

INSTRUCTIONS = (
    "对照 render/<sample_id>.png 逐行填写。rows 每项为 "
    '{"code": "科目编码或合计", "cells": {"<指标>": "<原页显示值>"}}；'
    "原件空白写 BLANK，横线写 DASH，无此项写 NONE。"
    "amount_cells 可选，填 {\"row\": 0, \"col\": 2, \"raw_text\": \"1,234.56\"} 形式的逐格真值。"
    "column_identity 可选，填 {\"<指标>\": <列号>}；不填则列身份正确率记 null。"
    "truth_source 必须改为 human 或 t0_frozen，并填写 annotator/annotated_at。"
)


def load_parse(path: Path) -> list[dict[str, Any]]:
    rows, seen = [], set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            sha = str(row.get("sha256") or "")
            if not sha or sha in seen:
                continue
            seen.add(sha)
            rows.append(row)
    return rows


def load_intake(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            sha = str(row.get("sha256") or "")
            if sha:
                result[sha] = row
    return result


def file_year(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "unknown"


def build_samples(parse_rows: list[dict[str, Any]], intake: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in parse_rows:
        sha = str(row.get("sha256") or "")
        fragments = row.get("fragments") or []
        previous: dict[str, int] = {}
        for fragment in fragments:
            if not isinstance(fragment, list) or len(fragment) < 5:
                continue
            page, table_code, rows, cols, method = fragment[:5]
            try:
                page, rows, cols = int(page), int(rows), int(cols)
            except (TypeError, ValueError):
                continue
            continuation = previous.get(str(table_code)) == page - 1
            previous[str(table_code)] = page
            meta = intake.get(sha) or {}
            samples.append({
                "sample_id": f"{sha[:12]}-p{page}-{table_code}",
                "sha256": sha,
                "file": row.get("file"),
                "physical_page": page,
                "table_code": str(table_code),
                "rows": rows,
                "cols": cols,
                "method": str(method),
                "continuation": continuation,
                "year": file_year(str(row.get("file") or "")),
                "level": str(meta.get("bound_level") or "unknown"),
                "report_kind": str(meta.get("report_kind") or "unknown"),
            })
    return samples


def layer_key(sample: dict[str, Any]) -> tuple[str, str, str, str, bool]:
    return (
        str(sample["year"]), str(sample["level"]), str(sample["method"]),
        str(sample["table_code"]), bool(sample["continuation"]),
    )


def stratified_pick(samples: list[dict[str, Any]], target: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[layer_key(sample)].append(sample)
    for group in groups.values():
        rng.shuffle(group)
    keys = sorted(groups)
    if not keys:
        return []
    # 层数比目标页还多时，只能按层大小选前 target 个层；每层一页。
    if len(keys) >= target:
        selected = sorted(keys, key=lambda key: len(groups[key]), reverse=True)[:target]
        return sorted((groups[key][0] for key in selected),
                      key=lambda item: (item["year"], item["file"], item["physical_page"]))
    # 每层先保 1 页，剩余名额按“层内可补数量”轮询，保证一定终止。
    quotas = {key: 1 for key in keys}
    remaining = target - len(keys)
    while remaining > 0:
        progressed = False
        for key in sorted(keys, key=lambda key: len(groups[key]), reverse=True):
            if remaining <= 0:
                break
            if len(groups[key]) > quotas[key]:
                quotas[key] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    picked = [item for key in keys for item in groups[key][:quotas[key]]]
    return sorted(picked, key=lambda item: (item["year"], item["file"], item["physical_page"]))


def render_page(corpus: Path, sample: dict[str, Any], render_dir: Path) -> str | None:
    import pymupdf

    pdf_path = corpus / str(sample["file"])
    if not pdf_path.exists():
        return None
    with pymupdf.open(pdf_path) as pdf:
        if not 1 <= int(sample["physical_page"]) <= len(pdf):
            return None
        pix = pdf[int(sample["physical_page"]) - 1].get_pixmap(dpi=150)
    target = render_dir / f"{sample['sample_id']}.png"
    target.write_bytes(pix.tobytes("png"))
    return str(target.relative_to(render_dir.parent))


def truth_template(sample: dict[str, Any], render_path: str | None) -> dict[str, Any]:
    return {
        **sample,
        "truth_source": "unannotated",
        "annotator": None,
        "annotated_at": None,
        "whole_table": None,
        "rows": [],
        "amount_cells": [],
        "column_identity": {},
        "notes": "",
        "render": render_path,
        "instructions": INSTRUCTIONS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parse", type=Path, default=DEFAULT_PARSE)
    parser.add_argument("--intake", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--target", type=int, default=120, help="100-200 pages")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not 1 <= args.target <= 500:
        parser.error("--target must be 1-500")

    samples = build_samples(load_parse(args.parse), load_intake(args.intake))
    picked = stratified_pick(samples, args.target, args.seed)
    render_dir = args.out / "render"
    truth_dir = args.out / "truth"
    render_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    records = []
    rendered = 0
    for sample in picked:
        render_path = render_page(args.corpus, sample, render_dir)
        rendered += int(render_path is not None)
        template = truth_template(sample, render_path)
        (truth_dir / f"{sample['sample_id']}.json").write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
        records.append(template)

    (args.out / "sampling.json").write_text(
        json.dumps({"seed": args.seed, "target": args.target, "total_candidates": len(samples),
                    "picked": len(records), "rendered": rendered, "samples": records},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.out / "sampling.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "sample_id", "year", "level", "report_kind", "method", "table_code",
            "continuation", "physical_page", "rows", "cols", "file", "sha256", "render"])
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key) for key in writer.fieldnames})

    by_year: dict[str, int] = defaultdict(int)
    by_level: dict[str, int] = defaultdict(int)
    by_method: dict[str, int] = defaultdict(int)
    for record in records:
        by_year[str(record["year"])] += 1
        by_level[str(record["level"])] += 1
        by_method[str(record["method"])] += 1
    summary = {
        "candidates": len(samples), "picked": len(records), "rendered": rendered,
        "missing_render": len(records) - rendered,
        "by_year": dict(sorted(by_year.items())),
        "by_level": dict(sorted(by_level.items())),
        "by_method": dict(sorted(by_method.items())),
        "continuations": sum(1 for record in records if record["continuation"]),
        "instructions": "人工填写 truth/*.json 后运行 score_cell_truth.py；未标注样本不计入指标。",
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
