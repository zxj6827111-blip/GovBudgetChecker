#!/usr/bin/env python3
"""114 目标统一重评：按当前运行逐项、逐出现点核对。

- FP 目标：在 S1 冻结基线中找到原条目，再到当前运行里按规则+页码+原文
  复核是否仍以正式问题存在；原序号位移不算新问题。
- MISS 目标：用金标单位的原文出现点（有阶段契约缺陷词的按契约收窄）逐一
  核对当前 F 层且证据合格的条目；全部出现点覆盖才算 satisfied。
- 只报告当前状态与缺口，不用阶段契约放行，也不按目标编号写例外。

输出：
    target_ledger.csv        114 行逐项状态
    target_ledger.json       含覆盖明细与分类
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
EVIDENCE = Path("/Volumes/workSSD/software development/gbc-worktrees/joint-execution-20261103-evidence")
TOOLS = Path("/Volumes/workSSD/software development/gbc-worktrees/joint-exp-batch2-tools-20261103/scripts")
DEFAULT_RUN = EVIDENCE / "s4b/gold/final"
BASELINE_RUN = EVIDENCE / "s1_integration/gold/run1"
TARGET_MATRIX = REPO / "docs/joint_execution_20261103/target_matrix.csv"
HISTORICAL_STATUS = EVIDENCE / "target_status.csv"
STRICT_STATUS = EVIDENCE / "rework/target_status_strict.csv"
CONTRACT_FILES = (
    EVIDENCE / "s5/S5_contract.json",
    EVIDENCE / "s4/S4_contract.json",
    EVIDENCE / "s4b/contract_n05_all.json",
    EVIDENCE / "s4b/contract_residual.json",
)
KNOWN_PENDING = {
    "FP|GS-2022-03|V33-202-16",
    "FP|GS-2024-02|V33-202-9",
    "MISS|GS-2022-06|C|GS-N05|1",
    "MISS|GS-2023-05|C|GS-N05|2",
    "MISS|GS-2024-08|C|GS-N05|2",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_gz(path: Path):
    import gzip

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def enrich_evidence(items: List[Dict[str, Any]], raw_index) -> None:
    """冻结匹配器条目行不带证据字段，从原始 status 补齐。"""
    eval_lib = load_module("eval_lib", TOOLS / "gold_baseline_eval_fp_batch1.py")
    for item in items:
        raw = eval_lib.raw_item(raw_index, item) or {}
        if not str(item.get("evidence_status") or "").strip():
            item["evidence_status"] = raw.get("evidence_status")
        if not item.get("evidence_verification"):
            item["evidence_verification"] = raw.get("evidence_verification")


def evidence_ok(item: Dict[str, Any]) -> bool:
    if str(item.get("evidence_status") or "").strip() != "complete":
        return False
    raw = item.get("evidence_verification") or {}
    return not (isinstance(raw, dict) and str(raw.get("status") or "") == "failed")


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=str(DEFAULT_RUN))
    parser.add_argument("--baseline-run", default=str(BASELINE_RUN))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    cmp = load_module("cmp", TOOLS / "batch2_gold_compare.py")
    eval_lib = load_module("eval_lib2", TOOLS / "gold_baseline_eval_fp_batch1.py")
    gm = load_module("gm", REPO / "scripts/gold_baseline_match.py")
    docs = gm.load_gold()

    needles: Dict[str, List[str]] = {}
    for path in CONTRACT_FILES:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (payload.get("target_message_contains") or {}).items():
                needles.setdefault(key, list(value))
    # 收窄词可能随小批收敛；优先使用最窄（元素最多）的一份。
    for path in CONTRACT_FILES:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (payload.get("target_message_contains") or {}).items():
                if len(value) > len(needles.get(key) or []):
                    needles[key] = list(value)

    run = Path(args.run).resolve()
    baseline = Path(args.baseline_run).resolve()
    current_items, _ = gm.load_system(run, docs)
    baseline_items, _ = gm.load_system(baseline, docs)
    enrich_evidence(current_items, eval_lib.load_raw_index(run))
    enrich_evidence(baseline_items, eval_lib.load_raw_index(baseline))

    baseline_by_key = {f"{i['doc']}|{i.get('raw_id')}": i for i in baseline_items}
    current_by_id = {i["id"]: i for i in current_items}
    # 基线内容索引：判断当前条目是否“新出现”。
    baseline_texts = defaultdict(list)
    for item in baseline_items:
        baseline_texts[(item["doc"], item.get("rule"))].append(item)

    historical = {r["target_id"]: r for r in csv.DictReader(
        HISTORICAL_STATUS.open(encoding="utf-8-sig"))}
    strict = {r["target_id"]: r for r in csv.DictReader(
        STRICT_STATUS.open(encoding="utf-8-sig"))}
    matrix = list(csv.DictReader(TARGET_MATRIX.open(encoding="utf-8-sig")))

    def is_new(item: Dict[str, Any]) -> bool:
        """当前条目在基线中没有同规则同正文的对应项。"""
        key = norm(item.get("message"))
        for old in baseline_texts.get((item["doc"], item.get("rule")), []):
            if norm(old.get("message")) == key:
                return False
        return True

    def covers(item: Dict[str, Any], anchor: str) -> bool:
        return cmp._anchor_covered(item, anchor, eval_lib)

    rows: List[Dict[str, Any]] = []
    for target in matrix:
        tid = target["target_id"]
        sample = target["sample_id"]
        ttype = target["target_type"]
        rule_or_check = target.get("rule_or_check") or ""
        entry: Dict[str, Any] = {
            "target_id": tid,
            "stage": target.get("owner_stage"),
            "type": ttype,
            "sample_id": sample,
            "rule_or_check": rule_or_check,
            "gold_units": target.get("gold_units"),
            "evidence_pages": target.get("evidence_pages"),
            "historical_status": (historical.get(tid) or {}).get("status"),
            "strict_status": (strict.get(tid) or {}).get("status"),
            "known_pending": tid in KNOWN_PENDING,
        }
        if ttype == "FP":
            source_id = target.get("source_id") or ""
            old = baseline_by_key.get(f"{sample}|{source_id}")
            if old is None:
                entry.update(current_state="baseline_identity_missing",
                             detail="冻结基线中找不到该原条目")
            else:
                still = []
                anchors = [str(old.get("message") or "")] + list(old.get("quotes") or [])
                anchors = [a for a in anchors if norm(a)]
                for item in current_items:
                    if item["doc"] != sample or item.get("rule") != old.get("rule"):
                        continue
                    if not (set(cmp._pages(item)) & set(cmp._pages(old))):
                        continue
                    if any(covers(item, anchor) for anchor in anchors):
                        still.append(item["id"])
                entry.update(
                    current_state="still_present" if still else "removed",
                    current_items=still[:10],
                    baseline_rule=old.get("rule"),
                    baseline_pages=cmp._pages(old),
                    baseline_message=str(old.get("message") or "")[:200],
                )
        else:
            unit = cmp._find_unit(docs, sample, target.get("gold_units") or "")
            scoped = needles.get(tid) or []
            anchors = cmp._defect_anchors(unit, scoped) if unit else []
            raw_anchors = cmp._defect_anchors(unit) if unit else []
            f_items = [i for i in current_items
                       if i["doc"] == sample and i.get("tier") == "F" and evidence_ok(i)]
            p_items = [i for i in current_items
                       if i["doc"] == sample and i.get("tier") == "P"]
            covered, covering, covering_p = [], [], []
            if unit is None:
                entry.update(current_state="gold_unit_missing",
                             anchors_total=0, anchors_covered=0)
            else:
                for anchor in anchors:
                    f_hits = [i["id"] for i in f_items if covers(i, anchor)]
                    if f_hits:
                        covered.append(anchor)
                        covering.extend(f_hits)
                    elif any(covers(i, anchor) for i in p_items):
                        covering_p.append(anchor)
                entry.update(
                    anchors_total=len(anchors),
                    anchors_covered=len(covered),
                    anchors_raw_total=len(raw_anchors),
                    scoped_needles=scoped,
                    missing_anchors=[a for a in anchors if a not in covered],
                    covering_f_items=sorted(set(covering))[:12],
                    new_f_items=sorted({i for i in set(covering)
                                        if is_new(current_by_id[i])})[:12],
                    covering_p_items=len(covering_p),
                )
                if anchors and len(covered) == len(anchors):
                    state = "satisfied"
                elif covered:
                    state = "partial"
                elif covering_p:
                    state = "candidate_only"
                else:
                    state = "missing"
                entry["current_state"] = state
                if state != "satisfied" and covering_p and not covered:
                    entry["classification"] = "candidate_pending_confirmation"
                elif state == "partial":
                    entry["classification"] = "occurrence_gap"
                elif state == "missing":
                    entry["classification"] = "no_formal_occurrence"
                else:
                    entry["classification"] = "satisfied"
            entry["rule_matches"] = sorted({i.get("rule") for i in f_items
                                            if any(covers(i, a) for a in anchors)}) if unit else []
        if tid in KNOWN_PENDING:
            entry["classification"] = "pending_adjudication"
        rows.append(entry)

    columns = [
        "target_id", "stage", "type", "sample_id", "rule_or_check", "gold_units",
        "evidence_pages", "historical_status", "strict_status", "known_pending",
        "current_state", "classification", "anchors_total", "anchors_covered",
        "anchors_raw_total", "covering_f_items", "new_f_items", "covering_p_items",
        "missing_anchors", "current_items", "baseline_rule", "baseline_pages",
        "baseline_message", "detail",
    ]
    with (args.out / "target_ledger.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("missing_anchors", "current_items", "baseline_pages"):
                if isinstance(flat.get(key), list):
                    flat[key] = " | ".join(str(v) for v in flat[key])
            writer.writerow(flat)
    (args.out / "target_ledger.json").write_text(
        json.dumps({
            "run": str(run),
            "baseline_run": str(baseline),
            "targets": len(rows),
            "counts": {
                "current_state": _counts(rows, "current_state"),
                "classification": _counts(rows, "classification"),
            },
            "rows": rows,
        }, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "targets": len(rows),
        "current_state": _counts(rows, "current_state"),
        "classification": _counts(rows, "classification"),
    }, ensure_ascii=False, indent=2))
    return 0


def _counts(rows, key):
    result = {}
    for row in rows:
        value = str(row.get(key))
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
