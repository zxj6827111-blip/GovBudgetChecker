#!/usr/bin/env python3
"""从同一 run 重算严格 F/P 分栏（基线裁决继承 + 新条目金标链接）。

口径：
- 新 F 条目若唯一对齐到基线 F 条目，继承基线 final（TP/FP_GOLD/ADJ_TRUE/ADJ_FALSE/INDET）；
- 无基线对应时，`match_item` 能链到金标 defect 单位 → TP；该检查无金标单位 → NOGOLD；
  有金标单位但链不上 → UNMATCHED；
- 多个新条目争同一基线条目 → AMBIGUOUS。
输出只作本轮 metrics，不覆盖旧指标。
"""
from __future__ import annotations

import csv
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
RUN = REPO / "outputs/unified_closeout_round3_20261104_work/gold_r3_1"
BASELINE_FINAL = REPO / "outputs/gold_baseline_20261102/rev2/analysis/final_rows.json"
ALIGNMENT = REPO / "outputs/unified_closeout_round3_20261104_work/strict_eval_r3/item_alignment.csv"
OUT = REPO / "outputs/unified_closeout_round3_20261104_work/strict_metrics_r3.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    gm = load("gm_metrics", REPO / "scripts/gold_baseline_match.py")
    docs = gm.load_gold()
    items, _ = gm.load_system(RUN, docs)
    baseline = json.loads(BASELINE_FINAL.read_text(encoding="utf-8"))
    old_by_key = {(row["doc"], str(row.get("raw_id"))): row for row in baseline}
    matched_old = {}
    for row in csv.DictReader(ALIGNMENT.open(encoding="utf-8-sig")):
        if row.get("kind") == "matched" and row.get("new_raw_id"):
            matched_old[(row["sample_id"], row["new_raw_id"])] = row.get("old_raw_id")
    old_claims = defaultdict(list)
    for item in items:
        if item.get("tier") != "F" or not item.get("raw_id"):
            continue
        old_raw = matched_old.get((item["doc"], str(item["raw_id"])))
        if old_raw:
            old_claims[(item["doc"], str(old_raw))].append(item["id"])

    def gold_units_for(doc, rule):
        units = docs.get(doc, {}).get("units") or []
        return [unit for unit in units
                if unit.get("check") and str(unit["check"]) == str(rule)]

    result = {"run": str(RUN), "tiers": Counter(item["tier"] for item in items)}
    for tier in ("F", "P"):
        rows = [item for item in items if item.get("tier") == tier]
        categories = {}
        details = []
        for item in rows:
            old_raw = matched_old.get((item["doc"], str(item.get("raw_id"))))
            old = old_by_key.get((item["doc"], str(old_raw))) if old_raw else None
            claim = old_claims.get((item["doc"], str(old_raw)), []) if old_raw else []
            if old and str(old.get("final") or ""):
                category = str(old["final"])
                source = "baseline"
                if len(claim) > 1:
                    category = "AMBIGUOUS"
                    source = "shared_baseline"
            else:
                try:
                    match = gm.match_item(item, docs[item["doc"]])
                except Exception:  # noqa: BLE001
                    match = {}
                links = [link for link in match.get("links") or [] if isinstance(link, dict)]
                if links:
                    category = "TP"
                    source = "gold_link"
                elif not gold_units_for(item["doc"], item.get("rule")):
                    category = "NOGOLD"
                    source = "no_gold_units"
                else:
                    category = "UNMATCHED"
                    source = "no_link"
            categories[category] = categories.get(category, 0) + 1
            details.append({"id": item["id"], "category": category, "source": source,
                            "gold_links": [link.get("unit") for link in
                                           (gm.match_item(item, docs[item["doc"]]).get("links") or [])
                                           if isinstance(link, dict)]})
        result[f"{tier}_match"] = dict(sorted(categories.items(), key=lambda kv: -kv[1]))
        result[f"{tier}_details"] = details
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k.endswith("_match") or k == "tiers"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
