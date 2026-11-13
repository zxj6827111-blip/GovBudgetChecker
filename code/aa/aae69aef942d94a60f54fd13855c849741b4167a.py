#!/usr/bin/env python3
"""第 0 步比对：把重放 run1 的系统条目与 r2 的 system_items.json / ledgers.json 逐条比较。

用法：.venv/bin/python outputs/fp_batch1_20261103/scripts/step0_compare.py <重放 run1> <输出 json>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
import gold_baseline_match as gm  # noqa: E402

OLD_ANALYSIS = REPO / "outputs/gold_baseline_20261102/rev2/analysis"
FIELDS = ("tier", "rule", "pages", "title", "message", "quotes", "sev", "checklist_item", "bucket",
          "obligation_ids", "reason", "ob_status")


def key(item: dict) -> str:
    return item["id"]


def main(argv: list[str]) -> int:
    run = Path(argv[0]).resolve()
    out = Path(argv[1]).resolve()
    docs = gm.load_gold()
    items, aux = gm.load_system(run, docs)
    old_items = json.loads((OLD_ANALYSIS / "system_items.json").read_text(encoding="utf-8"))
    old_ledgers = json.loads((OLD_ANALYSIS / "ledgers.json").read_text(encoding="utf-8"))

    new_by = {key(i): i for i in items}
    old_by = {key(i): i for i in old_items}
    added = sorted(set(new_by) - set(old_by))
    removed = sorted(set(old_by) - set(new_by))
    changed = []
    for k in sorted(set(new_by) & set(old_by)):
        a, b = old_by[k], new_by[k]
        diffs = [f for f in FIELDS if a.get(f) != b.get(f)]
        if diffs:
            changed.append({"id": k, "fields": diffs,
                            "old": {f: a.get(f) for f in diffs}, "new": {f: b.get(f) for f in diffs}})

    # O 层底层账本逐实例比较
    ledger_diffs = []
    for sid in sorted(set(old_ledgers) | set(aux["ledgers"])):
        o, n = old_ledgers.get(sid, {}), aux["ledgers"].get(sid, {})
        for oid in sorted(set(o) | set(n)):
            if o.get(oid) != n.get(oid):
                ledger_diffs.append({"sample": sid, "obligation": oid, "old": o.get(oid), "new": n.get(oid)})

    selfcheck_bad = {sid: v for sid, v in aux["selfcheck"].items() if not v["ok"]}
    result = {
        "run": str(run),
        "items_new": len(items), "items_old": len(old_items),
        "tiers_new": dict(Counter(i["tier"] for i in items)),
        "tiers_old": dict(Counter(i["tier"] for i in old_items)),
        "old_tiers_missing": False,
        "added": added, "removed": removed, "changed": changed,
        "ledger_diff_count": len(ledger_diffs), "ledger_diffs": ledger_diffs[:50],
        "selfcheck_mismatch": selfcheck_bad,
        "identical": not added and not removed and not changed and not ledger_diffs and not selfcheck_bad,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("items_new", "items_old", "tiers_new", "tiers_old",
                                            "added", "removed", "changed", "ledger_diff_count",
                                            "selfcheck_mismatch", "identical")},
                     ensure_ascii=False, indent=1, default=dict)[:4000])
    return 0 if result["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
