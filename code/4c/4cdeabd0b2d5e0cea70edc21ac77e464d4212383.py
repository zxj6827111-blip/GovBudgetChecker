#!/usr/bin/env python3
"""一次性生成第一批引用/计数精确例外清单（开发期工具，不参与评估运行时推导）。

输入：基线 run（c6cd033d 的 step0/run1）与冻结的整改后 run（round2/gold/run1）。
输出：scripts/fp_batch1_reference_exceptions.json，供评估器按“精确条目”匹配，
不按引用差异自行推导映射。
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
EVAL_SCRIPT = Path("/Volumes/workSSD/software development/gbc-worktrees/fp-batch1-20261103/scripts/gold_baseline_eval_fp_batch1.py")


def read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("gb_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_path(value, path: str):
    tokens = re.findall(r"/([^/\[]+)|\[(\d+)\]", path)
    current = value
    for key, index in tokens:
        if key:
            current = current[key]
        else:
            current = current[int(index)]
    return current


def main() -> int:
    g = load_module(EVAL_SCRIPT)
    sys.path.insert(0, str(REPO / "scripts"))
    import gold_baseline_match as gm

    docs = gm.load_gold()
    old_items = json.loads((REPO / "outputs/gold_baseline_20261102/rev2/analysis/system_items.json").read_text(encoding="utf-8"))
    new_items, _ = gm.load_system(REPO / "outputs/fp_batch1_20261103/round2/gold/run1", docs)
    baseline_rows = {f"{row['doc']}|{row.get('raw_id')}": row for row in
                     json.loads((REPO / "outputs/gold_baseline_20261102/rev2/analysis/final_rows.json").read_text(encoding="utf-8"))}

    def decision_of(item):
        return g.decision_signature(baseline_rows.get(f"{item['doc']}|{item.get('raw_id') or item['rule']}"))

    pairs, _, _, _, _ = g.align(old_items, new_items, decision_of)
    old_raw = g.load_raw_index(REPO / "outputs/fp_batch1_20261103/step0/run1")
    new_raw = g.load_raw_index(REPO / "outputs/fp_batch1_20261103/round2/gold/run1")
    entries = []
    for old, new, _ in pairs:
        ro = g.raw_item(old_raw, old)
        rn = g.raw_item(new_raw, new)
        if ro is None or rn is None:
            continue
        left = g.canonical_raw(ro)
        right = g.canonical_raw(rn)
        for path in g.diff_paths(left, right):
            key = path.rstrip("/").split("/")[-1].split("[")[0]
            if key not in {"pre_group_issue_id", "member_issue_ids", "merged_issue_ids"}:
                continue
            try:
                old_value, new_value = get_path(left, path), get_path(right, path)
            except (KeyError, IndexError, TypeError):
                continue
            if old_value == new_value:
                continue
            entries.append({
                "doc": old["doc"], "old_id": old["id"], "new_id": new["id"],
                "rule": old["rule"], "path": path,
                "old_value": old_value, "new_value": new_value,
                "context": {
                    "old_message": g.norm(old.get("message")),
                    "new_message": g.norm(new.get("message")),
                    "old_pages": old.get("pages"), "new_pages": new.get("pages"),
                    "old_bbox": ro.get("bbox"), "new_bbox": rn.get("bbox"),
                },
            })
    entries.sort(key=lambda e: (e["doc"], e["old_id"], e["path"]))
    payload = {
        "version": 1,
        "batch": "fp_batch1_20261103",
        "note": "只列第一批冻结样本中实际发生的合法引用重编号；评估器按 (doc, old_id, new_id, path, old_value, new_value) 精确匹配，不从差异自行推导。待复核方审核。",
        "entries": entries,
    }
    out = Path("/Volumes/workSSD/software development/gbc-worktrees/fp-batch1-20261103/scripts/fp_batch1_reference_exceptions.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"entries": len(entries), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
