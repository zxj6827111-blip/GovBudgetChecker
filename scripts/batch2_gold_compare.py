#!/usr/bin/env python3
"""第二批 33 份金标阶段比较器（阶段契约版，独立于第一批评估器）。

用途：
- ``--mode self``：同一代码两遍采集自比，要求零差异（冻结组合基线 / 每段回归的第一步）。
- ``--mode baseline``：组合基线对第一批已验收结果，要求零业务差异（集成只许组合，不许夹带）。
- ``--mode stage``：本段结果对冻结组合基线，只允许本段目标与契约列明字段变化。

与第一批评估器的关系：
- 复用其对齐、完整裁决签名、原始 status 指纹、引用/计数精确例外与歧义 REVIEW 逻辑；
- 目标语义改为第二批 ``target_matrix.csv``：FP 目标=指定条目消失/转回执，MISS 目标=按检查键+页码+原文出现新正式条目；
- 第一批 45 条引用例外**不继承**；本工具默认无例外，只有显式 ``--reference-exceptions`` 才读取，
  且必须是第二批自己冻结的清单。

用法：
    .venv/bin/python scripts/batch2_gold_compare.py \
        --mode stage --stage S2 \
        --run outputs/batch2/S2/run1 \
        --baseline-run outputs/batch2/baseline/run1 \
        --baseline-analysis outputs/batch2/baseline/analysis \
        --targets docs/joint_execution_20261103/target_matrix.csv \
        --contract docs/batch2_r1_20261103/batch2_stage_contract.json \
        --out outputs/batch2/S2/compare
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
BATCH1_EVAL = HERE / "gold_baseline_eval_fp_batch1.py"
#: r2 冻结匹配脚本在运行仓库 scripts/（未入 git），固定只读路径；REPO 由脚本自身位置推导。
DEFAULT_MATCH = Path(
    "/Volumes/workSSD/software development/GovBudgetChecker/scripts/gold_baseline_match.py")
FORMAL_TIERS = {"F", "P", "O", "R", "D"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_targets(path: Path, stage: str) -> Dict[str, Any]:
    """读取 target_matrix，按阶段切分 FP 移除目标与 MISS 新增目标。"""
    fp_rows, miss_rows = [], []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("owner_stage") != stage:
                continue
            if row["target_type"] == "FP":
                fp_rows.append({
                    "target_id": row["target_id"], "sample_id": row["sample_id"],
                    "system_item_id": row["source_id"], "rule": row.get("rule_or_check"),
                    "pages": [int(p) for p in (row.get("evidence_pages") or "").split() if p.isdigit()],
                })
            elif row["target_type"] == "MISS":
                miss_rows.append({
                    "target_id": row["target_id"], "sample_id": row["sample_id"],
                    "check_key": row.get("rule_or_check"), "gold_unit": row.get("gold_units"),
                    "pages": [int(p) for p in (row.get("evidence_pages") or "").split() if p.isdigit()],
                })
    return {"fp_removals": fp_rows, "miss_additions": miss_rows}


def load_contract(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {"check_rules": {}, "allowed_field_changes": [], "resolved_earlier": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("check_rules", {})
    payload.setdefault("allowed_field_changes", [])
    payload.setdefault("resolved_earlier", [])
    return payload


def item_key(item: Dict[str, Any]) -> Tuple[str, str]:
    return item["doc"], str(item.get("raw_id") or item["rule"])


def _pages(item: Dict[str, Any]) -> List[int]:
    return [int(p) for p in item.get("pages") or [] if str(p).isdigit()]


def _rule_matches(item: Dict[str, Any], rules: Sequence[str]) -> bool:
    if not rules:
        return False
    rule = str(item.get("rule") or "")
    for expected in rules:
        token = str(expected).strip()
        if not token:
            continue
        if rule == token or rule.startswith(token + "-"):
            return True
    return False


def _quote_matches(item: Dict[str, Any], quotes: Sequence[str], eval_lib) -> bool:
    if not quotes:
        return True
    haystack = "".join(eval_lib.norm(q) for q in item.get("quotes") or [])
    return any(eval_lib.norm(q) and eval_lib.norm(q) in haystack for q in quotes)


def match_addition(item: Dict[str, Any], targets: Sequence[Dict[str, Any]],
                   contract: Dict[str, Any], eval_lib) -> List[Dict[str, Any]]:
    """把新增正式条目匹配到 MISS 目标：样本+页码+规则族+（可选）原文。"""
    hits = []
    for target in targets:
        if item["doc"] != target["sample_id"]:
            continue
        if target["pages"] and not (set(_pages(item)) & set(target["pages"])):
            continue
        rules = contract["check_rules"].get(target["check_key"] or "", [])
        if not _rule_matches(item, rules):
            continue
        if not _quote_matches(item, contract.get("target_quotes", {}).get(target["target_id"], []), eval_lib):
            continue
        hits.append(target)
    return hits


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run).resolve()
    baseline_run = Path(args.baseline_run).resolve()
    baseline_analysis = Path(args.baseline_analysis).resolve()
    targets_path = Path(args.targets).resolve()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"输出目录已存在且非空：{out}")
    out.mkdir(parents=True, exist_ok=True)

    eval_lib = load_module("batch1_eval_lib", BATCH1_EVAL)
    gm = load_module("gold_baseline_match", Path(args.match_script).resolve())
    sys.path.insert(0, str(Path(args.match_script).resolve().parent))
    import gold_baseline_decisions_r2 as dec  # noqa: E402

    docs = gm.load_gold()
    items, _aux = gm.load_system(run_dir, docs)
    old_items = json.loads((baseline_analysis / "system_items.json").read_text(encoding="utf-8"))
    baseline_rows = {f"{row['doc']}|{row.get('raw_id')}": row for row in
                     json.loads((baseline_analysis / "final_rows.json").read_text(encoding="utf-8"))}

    targets = load_targets(targets_path, args.stage) if args.mode == "stage" else {"fp_removals": [], "miss_additions": []}
    contract = load_contract(Path(args.contract).resolve() if args.contract else None)

    def decision_of(item: Dict[str, Any]):
        return eval_lib.decision_signature(baseline_rows.get(f"{item['doc']}|{item.get('raw_id') or item['rule']}"))

    pairs, old_only, new_only, review, resolved_duplicates = eval_lib.align(old_items, items, decision_of)
    new_to_old = {new["id"]: old for old, new, _ in pairs}

    # ---- 目标判定 ----
    fp_targets = {(t["sample_id"], str(t["system_item_id"])): t for t in targets["fp_removals"]}
    miss_targets = targets["miss_additions"]
    removed_keys = {item_key(o) for o in old_only}
    removed_target_hits = []
    removed_unexpected = []
    for old in old_only:
        key = item_key(old)
        target = fp_targets.get(key)
        (removed_target_hits if target else removed_unexpected).append({
            "sample_id": old["doc"], "raw_id": old.get("raw_id"), "rule": old["rule"],
            "pages": _pages(old), "target_id": (target or {}).get("target_id"),
        })
    missing_removals = [t for key, t in fp_targets.items() if key not in removed_keys]

    formal_additions = [n for n in new_only if n["tier"] in FORMAL_TIERS]
    satisfied: Dict[str, List[str]] = defaultdict(list)
    unexpected_additions = []
    for item in formal_additions:
        hits = match_addition(item, miss_targets, contract, eval_lib)
        if len(hits) > 1:
            review.append({"new_id": item["id"], "reason": "新增条目同时匹配多个 MISS 目标",
                           "targets": [h["target_id"] for h in hits]})
        if hits:
            satisfied[hits[0]["target_id"]].append(item["id"])
        else:
            unexpected_additions.append({"sample_id": item["doc"], "raw_id": item.get("raw_id"),
                                         "rule": item["rule"], "pages": _pages(item)})
    missing_additions = [t["target_id"] for t in miss_targets if not satisfied.get(t["target_id"])]

    # ---- 非目标条目漂移 ----
    target_fp_keys = set(fp_targets)
    fingerprint_drift = []
    decision_drift = []
    for old, new, how in pairs:
        if item_key(old) in target_fp_keys:
            continue
        diffs = eval_lib.fingerprint_diffs(old, new)
        if diffs:
            fingerprint_drift.append({"sample_id": old["doc"], "old_raw_id": old.get("raw_id"),
                                      "new_raw_id": new.get("raw_id"), "how": how, "fields": diffs,
                                      "old": {f: old.get(f) for f in diffs},
                                      "new": {f: new.get(f) for f in diffs}})
        old_row, new_row = baseline_rows.get(f"{old['doc']}|{old.get('raw_id')}"), None
        parent = new_to_old.get(new["id"])
        if parent is not None and old_row is not None and old_row.get("final") != (baseline_rows.get(
                f"{new['doc']}|{new.get('raw_id')}") or {}).get("final"):
            decision_drift.append({"sample_id": old["doc"], "raw_id": new.get("raw_id")})

    # ---- 引用/计数/原始指纹 ----
    old_raw_index = eval_lib.load_raw_index(baseline_run)
    new_raw_index = eval_lib.load_raw_index(run_dir)
    old_entity = eval_lib.load_entity_index(baseline_run)
    new_entity = eval_lib.load_entity_index(run_dir)
    id_maxima = eval_lib.collect_id_maxima(baseline_run)
    ref_exceptions, count_exceptions = {}, {}
    exceptions_sha = None
    if args.reference_exceptions:
        ref_path = Path(args.reference_exceptions).resolve()
        ref_exceptions, count_exceptions, _payload = eval_lib.load_reference_exceptions(ref_path)
        exceptions_sha = eval_lib.hashlib.sha256(ref_path.read_bytes()).hexdigest()
    exception_conflicts = eval_lib.exception_bidirectional_conflicts(list(ref_exceptions.values()))

    raw_drift, raw_missing = [], []
    for old, new, how in pairs:
        if item_key(old) in target_fp_keys:
            continue
        raw_old = eval_lib.raw_item(old_raw_index, old)
        raw_new = eval_lib.raw_item(new_raw_index, new)
        if raw_old is None or raw_new is None:
            raw_missing.append({"sample_id": old["doc"], "old_raw_id": old.get("raw_id"),
                                "new_raw_id": new.get("raw_id"),
                                "old_found": raw_old is not None, "new_found": raw_new is not None})
            continue
        a, b = eval_lib.canonical_raw(raw_old), eval_lib.canonical_raw(raw_new)
        if a != b:
            raw_drift.append({"sample_id": old["doc"], "old_raw_id": old.get("raw_id"),
                              "new_raw_id": new.get("raw_id")})

    # ---- TP 保持 ----
    def finals(rows_items):
        result = Counter()
        for row in rows_items:
            if row["tier"] in {"F", "P", "O", "R"}:
                result[row.get("final")] += 1
        return result
    baseline_finals = Counter(row.get("final") for row in baseline_rows.values())
    # 直接用基线 final_rows 与当前重算结果对比（当前裁决按对齐继承）
    current_finals = Counter()
    for item in items:
        if item["tier"] in {"F", "P"}:
            old = new_to_old.get(item["id"])
            key = f"{item['doc']}|{old.get('raw_id')}" if old else None
            row = baseline_rows.get(key) if key else None
            current_finals[(row or {}).get("final")] += 1
        elif item["tier"] in {"O", "R"}:
            if item["tier"] == "O":
                current_finals[gm.match_o(item, docs[item["doc"]]).get("status")] += 1
            else:
                # R 层按参考层裁决：TP -> REF_HIT，其余 -> REF_OTHER（与 r2 finalize 一致）。
                status = gm.match_item(item, docs[item["doc"]]).get("status")
                current_finals["REF_HIT" if status == "TP" else "REF_OTHER"] += 1
    tp_before = sum(v for k, v in baseline_finals.items() if k in {"TP", "HIT"})
    tp_after = sum(v for k, v in current_finals.items() if k in {"TP", "HIT"})

    checks = []

    def add(check_id, title, ok, details=()):
        checks.append({"id": check_id, "title": title,
                       "verdict": "PASS" if ok else "FAIL",
                       "count": len(details), "details": list(details)[:50]})

    add("B1", "两次采集样本集合一致", set(i["doc"] for i in old_items) == set(i["doc"] for i in items))
    add("B2", "条目对齐无歧义/重复签名冲突", not review, [str(r)[:300] for r in review])
    add("B3", "FP 目标条目全部移除", not missing_removals,
        [f"{m['sample_id']}|{m['system_item_id']}" for m in missing_removals])
    add("B4", "无目标外条目移除", not removed_unexpected,
        [f"{r['sample_id']}|{r['raw_id']}" for r in removed_unexpected])
    add("B5", "MISS 目标全部出现新正式条目", not missing_additions, missing_additions)
    add("B6", "无目标外新增正式条目", not unexpected_additions,
        [f"{r['sample_id']}|{r['raw_id']}|{r['rule']}" for r in unexpected_additions])
    add("B7", "非目标条目全文指纹无漂移", not fingerprint_drift,
        [f"{r['sample_id']}|{r['old_raw_id']}:{','.join(r['fields'])}" for r in fingerprint_drift])
    add("B8", "非目标条目原始 status 指纹无漂移", not raw_drift,
        [f"{r['sample_id']}|{r['old_raw_id']}" for r in raw_drift])
    add("B9", "原始 status 均可定位", not raw_missing, [str(r)[:200] for r in raw_missing])
    add("B10", "TP/HIT 数量不回退", tp_after >= tp_before, [f"{tp_before}->{tp_after}"])
    add("B11", "引用/计数例外无双向冲突", not exception_conflicts, exception_conflicts)
    add("B12", "裁决继承无漂移", not decision_drift, [str(r) for r in decision_drift[:10]])

    # ---- todo/队列计数完整指纹（计数值、方向、移除数被篡改都要暴露）----
    todo_drift = []
    for sid in sorted({i["doc"] for i in old_items}):
        old_todo = eval_lib.read_gz(baseline_run / "todo" / f"{sid}.todo.json.gz")
        new_todo = eval_lib.read_gz(run_dir / "todo" / f"{sid}.todo.json.gz")
        if eval_lib.canonical_raw(old_todo) != eval_lib.canonical_raw(new_todo):
            todo_drift.append(sid)
    add("B13", "待办/队列计数指纹无漂移", not todo_drift, todo_drift)

    # ---- 跨表示一致性与引用 ID 边界（F5 口径，第二批不继承任何引用例外）----
    entity_conflicts = []
    for old, new, _how in pairs:
        raw_new = eval_lib.raw_item(new_raw_index, new)
        if raw_new is None:
            continue
        if not eval_lib.membership_consistent(raw_new):
            entity_conflicts.append(f"{old['doc']}|{new.get('raw_id')}:成员引用与 related_findings 不一致")
        view = eval_lib.reference_view(raw_new)
        for path, value in view.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str) and not eval_lib.reference_id_allowed(item, old["doc"], id_maxima):
                    entity_conflicts.append(f"{old['doc']}|{new.get('raw_id')}:{path}={item} 超出基线 ID 范围")
    add("B14", "跨表示引用一致且新引用不越界", not entity_conflicts, entity_conflicts[:50])

    overall = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    summary = {
        "mode": args.mode, "stage": args.stage, "run": str(run_dir),
        "baseline_run": str(baseline_run), "baseline_analysis": str(baseline_analysis),
        "targets": {k: len(v) for k, v in targets.items()},
        "items_old": len(old_items), "items_new": len(items),
        "pairs": len(pairs), "removed": len(old_only), "added": len(new_only),
        "target_removed": len(removed_target_hits), "target_missing": missing_removals,
        "unexpected_removed": removed_unexpected,
        "satisfied_miss_targets": {k: v for k, v in satisfied.items()},
        "missing_additions": missing_additions, "unexpected_additions": unexpected_additions,
        "fingerprint_drift": fingerprint_drift, "raw_fingerprint_drift": raw_drift,
        "raw_missing": raw_missing, "decision_drift": decision_drift,
        "review_required": review, "resolved_duplicates": resolved_duplicates,
        "tp_before": tp_before, "tp_after": tp_after,
        "reference_exceptions_sha256": exceptions_sha,
        "exception_conflicts": exception_conflicts,
        "checks": checks, "overall": overall,
    }
    (out / "batch2_gold_compare.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
    with (out / "targets.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "target_id", "sample_id", "outcome", "evidence"])
        for target in targets["fp_removals"]:
            hit = any(r["sample_id"] == target["sample_id"] and r["raw_id"] == target["system_item_id"]
                      for r in removed_target_hits)
            writer.writerow(["fp_removal", target["target_id"], target["sample_id"],
                             "removed" if hit else "still_present", target["system_item_id"]])
        for target in miss_targets:
            ids = satisfied.get(target["target_id"], [])
            writer.writerow(["miss_addition", target["target_id"], target["sample_id"],
                             "satisfied" if ids else "missing", ";".join(ids)])
    print(json.dumps({"overall": overall, "stage": args.stage, "mode": args.mode,
                      "checks": {c["id"]: c["verdict"] for c in checks},
                      "target_removed": len(removed_target_hits),
                      "target_missing": len(missing_removals),
                      "missing_additions": len(missing_additions),
                      "unexpected_additions": len(unexpected_additions),
                      "unexpected_removed": len(removed_unexpected),
                      "tp": [tp_before, tp_after]}, ensure_ascii=False, indent=1))
    return 0 if overall == "PASS" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["self", "baseline", "stage"], required=True)
    parser.add_argument("--stage", default="S1")
    parser.add_argument("--run", required=True)
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--baseline-analysis", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--contract", default=None)
    parser.add_argument("--reference-exceptions", default=None,
                        help="第二批自己的冻结例外清单；默认不继承第一批")
    parser.add_argument("--match-script", default=str(DEFAULT_MATCH))
    parser.add_argument("--out", required=True)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
