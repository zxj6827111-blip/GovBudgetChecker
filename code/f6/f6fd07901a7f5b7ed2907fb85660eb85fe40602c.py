#!/usr/bin/env python3
"""33 金标逐条目对比：候选 vs 同基线采集。

按 (规则, 页码, 去掉“一、”序号前缀的正文前 60 字) 对齐；只比较系统问题条目，
不重算金标真值或指标。用法：diff_gold.py <base_status_dir> <cand_status_dir> <out_json>
"""
from __future__ import annotations

import gzip
import glob
import json
import os
import re
import sys
from collections import Counter

ORD = re.compile(r"^[一二三四五六七八九十百]+、\s*")


def load(path):
    data = json.loads(gzip.open(path, "rt", encoding="utf-8").read())
    return ((data.get("result") or {}).get("issues") or {}).get("all") or []


def key(item):
    message = ORD.sub("", (item.get("message") or "")).strip()
    return (str(item.get("rule_id") or ""), item.get("page_number"), message[:60])


def main() -> int:
    base_dir, cand_dir, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    rows = []
    totals = Counter()
    severity = Counter()
    for path in sorted(glob.glob(f"{base_dir}/*.status.json.gz")):
        sid = os.path.basename(path).split(".")[0]
        cand = os.path.join(cand_dir, os.path.basename(path))
        if not os.path.exists(cand):
            rows.append({"doc": sid, "missing_in_candidate": True})
            continue
        before, after = load(path), load(cand)
        kb, ka = Counter(key(i) for i in before), Counter(key(i) for i in after)
        added = list((ka - kb).elements())
        removed = list((kb - ka).elements())
        sb = Counter(str(i.get("severity")) for i in before)
        sa = Counter(str(i.get("severity")) for i in after)
        rows.append({
            "doc": sid, "before": len(before), "after": len(after),
            "added": len(added), "removed": len(removed),
            "severity_before": dict(sb), "severity_after": dict(sa),
            "added_items": [list(x) for x in added], "removed_items": [list(x) for x in removed],
        })
        totals["before"] += len(before)
        totals["after"] += len(after)
        totals["added"] += len(added)
        totals["removed"] += len(removed)
        for level, counter in (("before", sb), ("after", sa)):
            for name, count in counter.items():
                severity[f"{level}_{name}"] += count
    summary = {"docs": len(rows), "totals": dict(totals), "severity": dict(severity)}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "per_doc": rows}, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))
    changed = [r for r in rows if r.get("added") or r.get("removed")]
    print("docs with diff:", len(changed))
    for row in changed:
        print(row["doc"], "net", row["after"] - row["before"],
              "added", row["added"], "removed", row["removed"])
        for item in row["added_items"][:5]:
            print("   +", item[0], "| p", item[1], "|", item[2][:70])
        for item in row["removed_items"][:5]:
            print("   -", item[0], "| p", item[1], "|", item[2][:70])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
