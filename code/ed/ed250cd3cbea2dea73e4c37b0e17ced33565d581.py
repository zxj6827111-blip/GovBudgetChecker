#!/usr/bin/env python3
"""汇总第五轮（R4-01~R4-03）台账/验证指标。"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
WORK = REPO / "outputs/unified_closeout_round5_20261104_work"
B1 = REPO / "outputs/gold_b1_20261103/run_20261103T150928/revisions/round5_annotation_revision"


def counts(path: Path) -> Counter:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    return Counter(row["final_state"] for row in rows)


def states(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    return {row["target_id"]: row["final_state"] for row in rows}


def log_tail(name: str) -> str:
    lines = (WORK / "evidence" / name).read_text(encoding="utf-8").strip().splitlines()
    return next((line for line in reversed(lines) if " passed" in line or " failed" in line),
                lines[-1] if lines else "")


clean = counts(WORK / "final_clean/target_status.csv")
pending = [tid for tid, state in states(WORK / "final_clean/target_status.csv").items()
           if state == "pending_adjudication"]
strict = [tid for tid, state in states(WORK / "final_clean/target_status.csv").items()
          if state == "strict_partial"]
fragment = states(WORK / "fragment_final/target_status.csv")
neighbor = states(WORK / "neighbor_amount_final/target_status.csv")
amount = states(WORK / "amount_mutant_final/target_status.csv")
fp = states(WORK / "fp_mutant_final/target_status.csv")
gold = json.loads((WORK / "gold33_selfcompare_r5.json").read_text(encoding="utf-8"))
reg = json.loads((WORK / "reg160_selfcompare_r5/batch2_regression_compare.json").read_text(encoding="utf-8"))
probes = json.loads((WORK / "probes_final/summary.json").read_text(encoding="utf-8"))
b1_summary = json.loads((B1 / "decimal_checks_v4_summary.json").read_text(encoding="utf-8"))
b1_audit = json.loads((B1 / "decimal_evidence_audit.json").read_text(encoding="utf-8"))
b1_identity = json.loads((B1 / "decimal_identity_audit.json").read_text(encoding="utf-8"))
t06 = json.loads((B1 / "t06_items.json").read_text(encoding="utf-8"))
closure = json.loads((B1 / "closure_evidence.json").read_text(encoding="utf-8"))
probe_states = {}
for probe in probes:
    checks = probe.get("checks") or {}
    probe_states[probe.get("probe") or probe.get("name")] = {
        key: value for key, value in checks.items() if value != "PASS"} or "ALL PASS"

payload = {
    "run_id": "unified-closeout-round5-20261104",
    "generated_at_local": "2026-11-04",
    "status_meaning": "R4-01~R4-03 限定返工自验；不是独立验收",
    "code_identity": {
        "business_head": "4cda0edd361c81530654da8915306a400bd7f100",
        "business_changed_this_round": False,
        "comparator_head": "0a1379cd5a4336fda61b2aea217a728c664c0b94",
        "tools_head": "86282f385d8fb46f762df3780ff01d7ee52729d4",
        "b1_head": "e0efa58a5c56b77404f92e6c0dabb07dd8d79f3d",
    },
    "targets_114": {
        "total": sum(clean.values()),
        "fixed": clean.get("fixed", 0),
        "pending_adjudication": clean.get("pending_adjudication", 0),
        "strict_partial": clean.get("strict_partial", 0),
        "pending_ids": pending,
        "strict_ids": strict,
        "unchanged_vs_round4": True,
    },
    "r4_01_fragment_identity": {
        "fragment_pool_mutant_state": fragment.get("MISS|GS-2024-06|C|GS-W01|0"),
        "fragment_pool_counts": dict(Counter(fragment.values())),
        "neighbor_amount_state": neighbor.get("MISS|GS-2024-06|C|GS-W01|0"),
        "amount_mutant_states": {tid: state for tid, state in amount.items() if state != "fixed"},
        "fp_resurrection_state": fp.get("FP|GS-2022-03|V33-117-18"),
        "comparator_tests": log_tail("comparator21.log"),
    },
    "r4_02_decimal_identity": {
        "decimal_rows": b1_summary["rows"],
        "decimal_counts": b1_summary["counts"],
        "identity_sources": b1_identity["total"],
        "material_sources": b1_identity["material_sources"],
        "all_source_ok": b1_identity["all_source_ok"],
        "evidence_items": b1_audit["total"],
        "evidence_all_found": b1_audit["all_found"],
        "control_tests": log_tail("tools9.log"),
    },
    "r4_03_t06_printed_vs_leaf": {
        "printed_vs_leaf": closure["printed_vs_leaf"],
        "chains": [
            {key: row.get(key) for key in
             ("sample_id", "code", "total", "part1", "part2", "operand_count",
              "tolerance", "within_tolerance", "verdict")}
            for row in closure["relations"] if row["target"] == "T06"],
        "t06_nonzero": {sid: {"nonzero": data["nonzero_count"],
                              "people_leaf": data["人员经费"]["sum"],
                              "public_leaf": data["公用经费"]["sum"],
                              "printed_people": data["printed"]["人员经费合计"],
                              "printed_public": data["printed"]["公用经费合计"]}
                        for sid, data in t06.items()},
    },
    "determinism": {
        "gold_33": gold["totals"],
        "reg160": {"diffs": reg["diffs"],
                   "checks": {check["id"]: check["verdict"] for check in reg["checks"]}},
    },
    "official_probes": probe_states,
    "tests": {
        "comparator_anchor": log_tail("comparator21.log"),
        "tools_identity_controls": log_tail("tools9.log"),
        "prior_6": log_tail("prior6.log"),
        "prior_8": log_tail("prior8.log"),
        "own_5": log_tail("own5.log"),
        "review_boundaries_10": log_tail("review_boundaries10.log"),
        "review_boundary_3": log_tail("review_boundary3.log"),
        "business_boundaries_47": log_tail("boundaries47.log"),
        "review_r4_negatives_6": log_tail("review_r4_negatives.log"),
    },
    "carried_from_round4": {
        "db_e2e": "14 steps, first upload 200 / permission 403 / stale session 409 / new session 200 / export parity true",
        "real_page_basis": "old page 409 analysis_basis_stale, reload 200, new basis job:2",
        "business_full_pytest": "5086 passed / 196 skipped (business HEAD unchanged 4cda0ed; not rerun)",
        "note": "业务代码未变，不重复起数据库/前端；按复审要求只重评冻结输出。",
    },
}
out = WORK / "metrics_r5.json"
out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
