#!/usr/bin/env python3
"""误报整改第一批的独立评估入口（不覆盖 r2 输出、不改匹配/真值/指标公式）。

r2 的 ``scripts/gold_baseline_match.py`` 固定读写 ``outputs/gold_baseline_20261102``，
且人工裁决按旧条目 ID（``doc|raw_id``）关联；整改后条目会自动重编号。本入口：

1. 用同一份 ``gold_baseline_match.py`` 的 ``load_gold``/``load_system``/``match_item``
   /``match_o`` 与 ``metrics_main`` 计算（匹配、真值、公式、容差一行未改）；
2. 新旧条目按“规则 + 页码 + 原文/引文”对齐，不按展示序号继承裁决；
3. 只把旧裁决通过唯一对齐带到新条目；无法唯一对齐的差异单独列出；
4. 全部写入新的输出目录，r2 的 ``rev2/`` 与 ``run1/`` 只读。

用法：
    .venv/bin/python scripts/gold_baseline_eval_fp_batch1.py \
        --run outputs/fp_batch1_20261103/after/run1 \
        --baseline-analysis outputs/gold_baseline_20261102/rev2/analysis \
        --targets outputs/gold_baseline_20261102/rev2/fp_batch1_targets.csv \
        --out outputs/fp_batch1_20261103/after/eval
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
DEFAULT_MATCH = HERE / "gold_baseline_match.py"


def load_match_module(path: Path):
    spec = importlib.util.spec_from_file_location("gold_baseline_match", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载匹配脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gold_baseline_match"] = module
    spec.loader.exec_module(module)
    return module


def norm(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return re.sub(r"^[一二三四五六七八九十百]+、", "", text)


def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    if a["doc"] != b["doc"] or a["tier"] != b["tier"] or a["rule"] != b["rule"]:
        return 0.0
    if a["pages"] != b["pages"]:
        return 0.0
    message = SequenceMatcher(None, norm(a["message"]), norm(b["message"])).ratio()
    quote_a, quote_b = norm("".join(a.get("quotes") or [])), norm("".join(b.get("quotes") or []))
    quote = SequenceMatcher(None, quote_a, quote_b).ratio() if (quote_a or quote_b) else 1.0
    if message == 1.0 and quote == 1.0:
        return 1.0
    return 0.85 * message + 0.15 * quote


def identity_key(item: Dict[str, Any]):
    """完整业务身份：文档、层级、规则、页码、标题、去序号正文与原文引文。"""
    return (item["doc"], item["tier"], item["rule"], tuple(item["pages"]), item.get("sev"),
            _ws(item.get("title")), norm(item.get("message")),
            tuple(_ws(q) for q in item.get("quotes") or []))


def align(old: List[Dict[str, Any]], new: List[Dict[str, Any]], decision_of=None):
    """一对一配对；同指纹重复组只有组内裁决完全相同时才按组配对，其余一律 REVIEW。"""
    pool = {item["id"]: item for item in new}
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
    old_only: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    resolved: List[Dict[str, Any]] = []

    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for item in old:
        groups.setdefault(identity_key(item), []).append(item)
    handled: set = set()
    for o in old:
        key = identity_key(o)
        if key in handled:
            continue
        old_group = groups[key]
        candidates = [n for n in pool.values() if identity_key(n) == key]
        if len(old_group) == 1 and len(candidates) == 1:
            pairs.append((o, candidates[0], "exact"))
            pool.pop(candidates[0]["id"])
            handled.add(key)
            continue
        if len(old_group) == len(candidates) and candidates:
            decisions = {decision_of(m) for m in old_group if m["tier"] in {"F", "P"}} if decision_of else set()
            if len(decisions) <= 1:
                for old_item, new_item in zip(old_group, candidates, strict=True):
                    pairs.append((old_item, new_item, "duplicate-group"))
                for candidate in candidates:
                    pool.pop(candidate["id"])
                resolved.append({"old_ids": [m["id"] for m in old_group],
                                 "new_ids": [n["id"] for n in candidates],
                                 "decision": next(iter(decisions)) if decisions else None,
                                 "reason": "同指纹重复组；实例不可区分，但组内旧裁决相同（或均无裁决），按组配对"})
                handled.add(key)
                continue
            review.append({"old_ids": [m["id"] for m in old_group],
                           "candidates": [n["id"] for n in candidates],
                           "reason": "同指纹重复组内旧裁决不一致，无法按组继承"})
            for candidate in candidates:
                pool.pop(candidate["id"])
            handled.add(key)
            continue
        if candidates:
            review.append({"old_ids": [m["id"] for m in old_group],
                           "candidates": [n["id"] for n in candidates],
                           "reason": "旧同指纹组与候选数量不一致，无法一一对应"})
            for candidate in candidates:
                pool.pop(candidate["id"])
            handled.add(key)
            continue
        pending.append(o)

    for o in pending:
        scored = sorted(((similarity(o, n), n) for n in pool.values()), key=lambda x: -x[0])
        scored = [(s, n) for s, n in scored if s >= 0.90]
        if not scored:
            old_only.append(o)
            continue
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1.0
        if best_score - second_score < 0.02:
            review.append({"old_id": o["id"], "candidates": [n["id"] for _, n in scored[:3]],
                           "scores": [round(s, 4) for s, _ in scored[:3]],
                           "reason": "最高分不唯一（并列或差 <0.02），无法唯一对应"})
            for _, candidate in scored:
                pool.pop(candidate["id"], None)
            continue
        pairs.append((o, best, f"similar:{best_score:.3f}"))
        pool.pop(best["id"])
    return pairs, old_only, list(pool.values()), review, resolved


def _ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def message_equal(left: str, right: str) -> bool:
    """去展示序号后相等；仅当两侧都触发 600 字截断时允许 1–2 字前缀差。"""
    a, b = norm(left), norm(right)
    if a == b:
        return True
    if len(left or "") >= 600 and len(right or "") >= 600:
        short, long = sorted((a, b), key=len)
        return len(long) - len(short) <= 2 and long.startswith(short)
    return False


def _text_key(item: Dict[str, Any]) -> str:
    """text = 标题 + 空格 + 正文/原文；只归一规则自动加的展示序号，不归一原文。"""
    text = item.get("text") or ""
    title = item.get("title") or ""
    prefix = len(title) + 1 if title and text.startswith(title + " ") else 0
    head, rest = text[:prefix], text[prefix:]
    rest = re.sub(r"^[一二三四五六七八九十百]+、", "", rest)
    return _ws(head + rest)


def fingerprint_diffs(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """完整实质指纹：严重度、原文、证据位置、期望/实际值（在 text 中）等。"""
    diffs = [field for field in ("tier", "rule", "pages", "sev", "title", "bucket",
                                 "checklist_item", "obligation_ids")
             if old.get(field) != new.get(field)]
    if not message_equal(old.get("message"), new.get("message")):
        diffs.append("message")
    if [_ws(q) for q in old.get("quotes") or []] != [_ws(q) for q in new.get("quotes") or []]:
        diffs.append("quotes")
    if _text_key(old) != _text_key(new):
        diffs.append("text")
    return diffs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="整改后重放的 run1 目录")
    parser.add_argument("--baseline-analysis", required=True, help="r2 的 analysis 目录（只读）")
    parser.add_argument("--targets", required=True, help="目标行 CSV（判断移除是否符合预期）")
    parser.add_argument("--out", required=True, help="独立评估输出目录（不得已存在非空）")
    parser.add_argument("--match-script", default=str(DEFAULT_MATCH), help="gold_baseline_match.py 路径")
    args = parser.parse_args(argv)

    run = Path(args.run).resolve()
    baseline = Path(args.baseline_analysis).resolve()
    targets_path = Path(args.targets).resolve()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"输出目录已存在且非空：{out}")
    analysis = out / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)

    gm = load_match_module(Path(args.match_script).resolve())
    sys.path.insert(0, str(Path(args.match_script).resolve().parent))
    import gold_baseline_decisions_r2 as dec  # noqa: E402

    docs = gm.load_gold()
    items, aux = gm.load_system(run, docs)
    old_items = json.loads((baseline / "system_items.json").read_text(encoding="utf-8"))

    targets: Dict[Tuple[str, str], Dict[str, str]] = {}
    with targets_path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            targets[(row["sample_id"], row["system_item_id"])] = row

    def decision_of(item: Dict[str, Any]):
        if item["tier"] not in {"F", "P"}:
            return None
        decision = dec.D.get(f"{item['doc']}|{item.get('raw_id')}")
        return decision.get("final") if decision else None

    pairs, old_only, new_only, review, resolved_duplicates = align(old_items, items, decision_of)
    new_to_old = {n["id"]: o for o, n, _ in pairs}
    fingerprint_drift = []
    for old, new, how in pairs:
        diffs = fingerprint_diffs(old, new)
        if diffs:
            fingerprint_drift.append({"old_id": old["id"], "new_id": new["id"], "how": how,
                                      "fields": diffs,
                                      "old": {f: old.get(f) for f in diffs},
                                      "new": {f: new.get(f) for f in diffs}})

    # 旧裁决只能通过唯一对齐继承；移除条目必须全部是目标 CSV 的“移除”行。
    unused, unexpected_unused, missing_decision = [], [], []
    used = set()
    for item in items:
        if item["tier"] not in {"F", "P"}:
            continue
        old = new_to_old.get(item["id"])
        key = f"{item['doc']}|{old.get('raw_id')}" if old else None
        if key and key in dec.D:
            used.add(key)
        elif old is None:
            missing_decision.append({"new_id": item["id"], "old_id": None, "tier": item["tier"]})
    for key in dec.D:
        if key in used:
            continue
        doc, raw = key.split("|", 1)
        target = targets.get((doc, raw))
        if target and target["expect_after"] == "移除":
            unused.append(key)
        else:
            unexpected_unused.append({"key": key, "target": target})

    removed_rows = []
    for o in old_only:
        target = targets.get((o["doc"], o.get("raw_id")))
        removed_rows.append({"sample_id": o["doc"], "tier": o["tier"], "rule": o["rule"],
                             "raw_id": o.get("raw_id"), "pages": o["pages"], "message": o.get("message"),
                             "in_targets": bool(target), "target_class": (target or {}).get("class"),
                             "expect_after": (target or {}).get("expect_after")})
    target_removed = [r for r in removed_rows if r["in_targets"] and r["expect_after"] == "移除"]
    target_missing = [f"{sid}|{raw}" for (sid, raw), row in targets.items()
                      if row["expect_after"] == "移除"
                      and not any(r["sample_id"] == sid and r["raw_id"] == raw for r in removed_rows)]
    unexpected_removed = [r for r in removed_rows
                          if not (r["in_targets"] and r["expect_after"] == "移除")]
    added_rows = [{"sample_id": n["doc"], "tier": n["tier"], "rule": n["rule"], "raw_id": n.get("raw_id"),
                   "pages": n["pages"], "message": n.get("message")} for n in new_only]
    added_formal = [r for r in added_rows if r["tier"] in {"F", "P", "O", "R", "D"}]

    # 与 r2 相同的 finalize 逻辑，只是新条目通过对齐后的旧 raw_id 取裁决。
    by_id = {item["id"]: item for item in items}

    def finalize_mapped(rows_items, auto):
        rows = []
        for r in auto:
            item = by_id[r["id"]]
            old = new_to_old.get(item["id"])
            key = f"{item['doc']}|{old.get('raw_id')}" if old else ""
            row = {"id": r["id"], "doc": item["doc"], "tier": item["tier"], "rule": item["rule"],
                   "raw_id": item.get("raw_id") or item["rule"], "sev": item.get("sev"),
                   "ct": r["ct"], "pages": item["pages"], "title": item["title"], "message": item["message"],
                   "auto_status": r["status"], "auto_links": [link["unit"] for link in r["links"]],
                   "final": "", "links": [], "cause": "", "why": "", "evidence": "", "amend": False,
                   "decided": "auto"}
            d = dec.D.get(key) if item["tier"] in {"F", "P"} else None
            if d:
                row.update(final=d["final"], cause=d["cause"], why=d["why"], evidence=d["evidence"],
                           amend=d["amend"], decided="manual")
                row["links"] = list(d["links"]) if d["final"] in {"TP", "HIT"} and d["links"] is not None \
                    else list(d["links"] or [])
            else:
                status = r["status"]
                if item["tier"] == "F":
                    if status != "TP":
                        raise SystemExit(f"F 条目缺少可继承的旧裁决：{item['id']} 自动状态 {status}")
                    row.update(final="TP", links=list(row["auto_links"]))
                elif item["tier"] == "P":
                    if status == "TP":
                        row.update(final="HIT", links=list(row["auto_links"]))
                    elif status == "NOGOLD":
                        row.update(final="OUT_OF_SCOPE",
                                   why="D-011 同科目收入表/支出表金额差异：口径决议⑤不判错，无金标对应")
                    else:
                        raise SystemExit(f"P 条目缺少可继承的旧裁决：{item['id']} 自动状态 {status}")
                elif item["tier"] == "O":
                    row.update(final=status, links=list(row["auto_links"]))
                else:
                    row.update(final="REF_HIT" if status == "TP" else "REF_OTHER",
                               links=list(row["auto_links"]) if status == "TP" else [])
            rows.append(row)
        return rows

    auto = []
    for item in items:
        if item["tier"] in {"N", "D"}:
            continue
        auto.append(gm.match_o(item, docs[item["doc"]]) if item["tier"] == "O"
                    else gm.match_item(item, docs[item["doc"]]))

    def write_reports(summary: Dict[str, Any]) -> int:
        (out / "eval_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
        with (out / "item_alignment.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["kind", "sample_id", "tier", "rule", "old_raw_id", "new_raw_id", "pages",
                             "how_or_status", "in_targets", "target_class", "message"])
            for old, new, how in sorted(pairs, key=lambda p: (p[0]["doc"], str(p[0].get("raw_id")))):
                writer.writerow(["matched", old["doc"], old["tier"], old["rule"], old.get("raw_id"),
                                 new.get("raw_id"), " ".join(map(str, old["pages"])), how, "", "",
                                 new.get("message")])
            for row in removed_rows:
                writer.writerow(["removed", row["sample_id"], row["tier"], row["rule"], row["raw_id"], "",
                                 " ".join(map(str, row["pages"])), "", "Y" if row["in_targets"] else "",
                                 row["target_class"] or "", row["message"]])
            for row in added_rows:
                writer.writerow(["added", row["sample_id"], row["tier"], row["rule"], "", row["raw_id"],
                                 " ".join(map(str, row["pages"])), "", "", "", row["message"]])
        print(json.dumps({k: summary[k] for k in ("items_old", "items_new", "tiers_old", "tiers_new", "pairs",
                                                 "target_removed", "target_missing", "unexpected_removed",
                                                 "added_formal", "decisions_unused_unexpected", "missing_decision",
                                                 "review_required", "fingerprint_drift", "decision_drift",
                                                 "headline", "ok")},
                         ensure_ascii=False, indent=1, default=dict)[:6000])
        return 0 if summary["ok"] else 1

    summary: Dict[str, Any] = {
        "run": str(run), "baseline_analysis": str(baseline),
        "items_old": len(old_items), "items_new": len(items),
        "tiers_old": dict(Counter(i["tier"] for i in old_items)),
        "tiers_new": dict(Counter(i["tier"] for i in items)),
        "pairs": len(pairs), "removed": len(removed_rows), "added": len(added_rows),
        "target_removed": len(target_removed), "target_missing": target_missing,
        "unexpected_removed": unexpected_removed, "added_formal": added_formal,
        "decisions_inherited": len(used), "decisions_unused_expected": unused,
        "decisions_unused_unexpected": unexpected_unused, "missing_decision": missing_decision,
        "review_required": review, "resolved_duplicates": resolved_duplicates,
        "fingerprint_drift": fingerprint_drift, "decision_drift": [],
        "headline": None,
    }
    if review or missing_decision:
        summary["ok"] = False
        return write_reports(summary)

    # 复用 r2 的指标实现：只换数据目录与“新条目→旧裁决”的取用方式。
    (analysis / "gold_docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    (analysis / "system_items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (analysis / "auto_match.json").write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
    (analysis / "ledgers.json").write_text(json.dumps(aux["ledgers"], ensure_ascii=False), encoding="utf-8")
    gm.REV = "fp_batch1"
    gm.RUN_ROOT = run.parent
    gm.OUT = out
    gm.ANALYSIS = analysis
    gm.finalize = finalize_mapped
    gm.metrics_main(SimpleNamespace())

    metrics = json.loads((analysis / "metrics.json").read_text(encoding="utf-8"))
    old_rows = {f"{row['doc']}|{row.get('raw_id')}": row for row in
                json.loads((baseline / "final_rows.json").read_text(encoding="utf-8"))}
    new_rows = {row["id"]: row for row in
                json.loads((analysis / "final_rows.json").read_text(encoding="utf-8"))}
    decision_drift = []
    for old, new, _ in pairs:
        old_row = old_rows.get(f"{old['doc']}|{old.get('raw_id')}")
        new_row = new_rows.get(new["id"])
        if old_row is None or new_row is None:
            continue
        for field in ("final", "links", "cause", "amend"):
            left, right = old_row.get(field), new_row.get(field)
            if field == "links" and isinstance(left, list) and isinstance(right, list):
                left, right = sorted(left), sorted(right)
            if left != right:
                decision_drift.append({"old_id": old["id"], "new_id": new["id"], "field": field,
                                       "old": left, "new": right})
    summary.update({
        "decision_drift": decision_drift,
        "headline": {
            "F_total": metrics["F_total"], "F_final": metrics["F_final"],
            "FPR_headline": metrics["FPR_headline"], "FPR_strict_gold": metrics["FPR_strict_gold"],
            "MR_overall": metrics["MR_overall"], "MR_CU": metrics["MR_CU"],
            "MR_FU_exhaustive": metrics["MR_FU_exhaustive"],
            "PendingHit": metrics["PendingHit"], "coverage": metrics["coverage"],
            "workload": metrics["workload"], "tiers": metrics["tiers"],
        },
    })
    summary["ok"] = (not added_formal and not unexpected_removed and not target_missing
                     and not unexpected_unused and not missing_decision and not review
                     and not fingerprint_drift and not decision_drift)
    return write_reports(summary)


if __name__ == "__main__":
    raise SystemExit(main())
