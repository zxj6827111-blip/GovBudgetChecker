#!/usr/bin/env python3
"""两个金标重放结果（run 目录）的逐条比对：按规则 + 页码 + 原文文字对齐，不按展示序号继承。

用法：.venv/bin/python outputs/fp_batch1_20261103/scripts/compare_runs.py <old_run> <new_run> <targets_csv> <out_json> [out_csv]
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
import gold_baseline_match as gm  # noqa: E402


def norm(text: str) -> str:
    """比较用归一：去空白，并去掉规则自动加的“一、二、…”展示序号（重编号不算文字变化）。"""
    text = re.sub(r"\s+", "", text or "")
    return re.sub(r"^[一二三四五六七八九十百]+、", "", text)


def item_key(item: dict) -> str:
    return item["id"]


def similarity(a: dict, b: dict) -> float:
    if a["doc"] != b["doc"] or a["tier"] != b["tier"] or a["rule"] != b["rule"]:
        return 0.0
    if a["pages"] != b["pages"]:
        return 0.0
    message = SequenceMatcher(None, norm(a["message"]), norm(b["message"])).ratio()
    quote_a = norm("".join(a.get("quotes") or []))
    quote_b = norm("".join(b.get("quotes") or []))
    quote = SequenceMatcher(None, quote_a, quote_b).ratio() if (quote_a or quote_b) else 1.0
    if message == 1.0 and quote == 1.0:
        return 1.0
    return 0.85 * message + 0.15 * quote


def align(old: list[dict], new: list[dict]):
    """先按原文完全相同一对一，再按相似度（同页同规则）补配。"""
    pool = {item_key(i): i for i in new}
    pairs, old_only, new_only = [], [], []
    unmatched_old = []
    for o in old:
        exact = [n for n in pool.values() if similarity(o, n) == 1.0]
        if len(exact) == 1:
            n = exact[0]
            pairs.append((o, n, "exact"))
            pool.pop(item_key(n))
        elif len(exact) > 1:
            # 同文多条：按 raw_id 相同优先，其次按原顺序
            same_raw = [n for n in exact if n.get("raw_id") == o.get("raw_id")]
            if len(same_raw) == 1:
                n = same_raw[0]
                pairs.append((o, n, "exact+raw_id"))
                pool.pop(item_key(n))
            else:
                unmatched_old.append(o)
        else:
            unmatched_old.append(o)
    for o in unmatched_old:
        candidates = [(similarity(o, n), n) for n in pool.values()]
        candidates = [(s, n) for s, n in candidates if s >= 0.90]
        candidates.sort(key=lambda x: -x[0])
        if candidates:
            s, n = candidates[0]
            pairs.append((o, n, f"similar:{s:.2f}"))
            pool.pop(item_key(n))
        else:
            old_only.append(o)
    new_only = list(pool.values())
    return pairs, old_only, new_only


def main(argv: list[str]) -> int:
    old_run, new_run, targets_csv, out_json = (Path(a).resolve() for a in argv[:4])
    out_csv = Path(argv[4]).resolve() if len(argv) > 4 else None
    docs = gm.load_gold()
    old_items, old_aux = gm.load_system(old_run, docs)
    new_items, new_aux = gm.load_system(new_run, docs)
    targets = {}
    with targets_csv.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            targets[(row["sample_id"], row["system_item_id"])] = row

    pairs, old_only, new_only = align(old_items, new_items)
    changed = []
    for o, n, how in pairs:
        substantive = [f for f in ("sev", "title", "quotes", "pages", "bucket", "obligation_ids")
                       if o.get(f) != n.get(f)]
        if norm(o.get("message")) != norm(n.get("message")):
            substantive.append("message")
        if o.get("raw_id") != n.get("raw_id") or substantive:
            changed.append({"old_id": o["id"], "new_id": n["id"], "how": how,
                            "fields": ["raw_id", *substantive],
                            "substantive": substantive,
                            "old": {f: o.get(f) for f in substantive},
                            "new": {f: n.get(f) for f in substantive}})
    substantive_changed = [c for c in changed if c["substantive"]]

    old_only_rows = []
    for o in old_only:
        target = targets.get((o["doc"], o.get("raw_id")))
        old_only_rows.append({"sample_id": o["doc"], "tier": o["tier"], "rule": o["rule"],
                              "raw_id": o.get("raw_id"), "pages": o["pages"], "message": o.get("message"),
                              "in_targets": bool(target), "target_class": (target or {}).get("class"),
                              "expect_after": (target or {}).get("expect_after")})
    new_only_rows = [{"sample_id": n["doc"], "tier": n["tier"], "rule": n["rule"], "raw_id": n.get("raw_id"),
                      "pages": n["pages"], "message": n.get("message")} for n in new_only]

    target_removed = [r for r in old_only_rows if r["in_targets"] and r["expect_after"] == "移除"]
    target_missing = [k for k, v in targets.items() if v["expect_after"] == "移除"
                      and not any(r["sample_id"] == k[0] and r["raw_id"] == k[1] for r in old_only_rows)]
    target_still_present = [k for k in target_missing]
    unexpected_removed = [r for r in old_only_rows if not (r["in_targets"] and r["expect_after"] == "移除")]
    limited_removed = [r for r in old_only_rows if r["in_targets"] and r["expect_after"].startswith("不限")]

    ledger_diffs = []
    ledger_other = []
    for sid in sorted(set(old_aux["ledgers"]) | set(new_aux["ledgers"])):
        o, n = old_aux["ledgers"].get(sid, {}), new_aux["ledgers"].get(sid, {})
        for oid in sorted(set(o) | set(n)):
            if o.get(oid) == n.get(oid):
                continue
            old_item, new_item = o.get(oid), n.get(oid)
            kind = "other"
            if old_item and new_item:
                rest_old = {k: v for k, v in old_item.items() if k != "detail"}
                rest_new = {k: v for k, v in new_item.items() if k != "detail"}
                if rest_old == rest_new and "pass" in str(new_item.get("detail")) and "fail" in str(old_item.get("detail")):
                    kind = "rule_status_fail_to_pass"
            entry = {"sample": sid, "obligation": oid, "kind": kind, "old": old_item, "new": new_item}
            ledger_diffs.append(entry)
            if kind != "rule_status_fail_to_pass":
                ledger_other.append(entry)

    new_formal = [r for r in new_only_rows if r["tier"] in {"F", "P", "O", "R", "D"}]
    new_notices = [r for r in new_only_rows if r["tier"] == "N"]

    result = {
        "old_run": str(old_run), "new_run": str(new_run),
        "items_old": len(old_items), "items_new": len(new_items),
        "tiers_old": dict(Counter(i["tier"] for i in old_items)),
        "tiers_new": dict(Counter(i["tier"] for i in new_items)),
        "pairs": len(pairs), "changed": changed, "substantive_changed": substantive_changed,
        "old_only": old_only_rows, "new_only": new_only_rows,
        "new_formal": new_formal, "new_notices": new_notices,
        "target_removed": len(target_removed), "target_removed_rows": target_removed,
        "target_missing": target_missing, "unexpected_removed": unexpected_removed,
        "limited_removed": limited_removed,
        "ledger_diff_count": len(ledger_diffs),
        "ledger_rule_status_changes": [e for e in ledger_diffs if e["kind"] == "rule_status_fail_to_pass"],
        "ledger_other": ledger_other,
    }
    result["ok"] = (not new_formal and not unexpected_removed and not target_missing
                    and not substantive_changed and not ledger_other)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
    if out_csv:
        with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["kind", "sample_id", "tier", "rule", "old_raw_id", "new_raw_id", "pages", "in_targets",
                        "target_class", "expect_after", "message"])
            for r in old_only_rows:
                w.writerow(["removed", r["sample_id"], r["tier"], r["rule"], r["raw_id"], "", " ".join(map(str, r["pages"])),
                            "Y" if r["in_targets"] else "", r["target_class"] or "", r["expect_after"] or "", r["message"]])
            for r in new_only_rows:
                w.writerow(["added", r["sample_id"], r["tier"], r["rule"], "", r["raw_id"], " ".join(map(str, r["pages"])),
                            "", "", "", r["message"]])
    print(json.dumps({k: result[k] for k in ("items_old", "items_new", "tiers_old", "tiers_new", "pairs",
                                            "target_removed", "target_missing", "unexpected_removed",
                                            "limited_removed", "new_only", "changed", "ledger_diff_count", "ok")},
                     ensure_ascii=False, indent=1, default=dict)[:5000])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
