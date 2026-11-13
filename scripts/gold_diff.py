#!/usr/bin/env python3
"""R7 §六.5⑩：新旧 annotation 逐项回归比对。

用法：
  python scripts/gold_diff.py --old gold/_backup_r7 --new gold \
      --allow gold/ALLOW_R7.json --out gold/DIFF_R7.md --json gold/DIFF_R7.json

任何未在 allow 清单内的改动都会以非零退出码报出。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def norm(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def diff_check(old: dict, new: dict, sid: str) -> list[dict]:
    out: list[dict] = []
    cid = new.get("check_id") or old.get("check_id")

    def add(field: str, a, b) -> None:
        out.append({"sid": sid, "kind": "check", "id": cid, "field": field,
                    "old": a, "new": b})

    if old.get("verdict") != new.get("verdict"):
        add("verdict", old.get("verdict"), new.get("verdict"))
    if (old.get("reason") or "") != (new.get("reason") or ""):
        add("reason", old.get("reason", ""), new.get("reason", ""))
    oi, ni = old.get("items", []), new.get("items", [])
    if [i.get("label") for i in oi] != [i.get("label") for i in ni]:
        add("items", [i.get("label") for i in oi], [i.get("label") for i in ni])
        return out
    for a, b in zip(oi, ni):
        lab = a.get("label", "")
        for f in ("verdict", "formula", "note", "rate_check"):
            if norm(a.get(f)) != norm(b.get(f)):
                add(f"item[{lab}].{f}", a.get(f), b.get(f))
        if norm(a.get("values")) != norm(b.get("values")):
            add(f"item[{lab}].values", a.get("values"), b.get("values"))
    return out


def diff_free(old: dict, new: dict, sid: str) -> list[dict]:
    out: list[dict] = []
    om = {f.get("id"): f for f in old.get("free_findings", [])}
    nm = {f.get("id"): f for f in new.get("free_findings", [])}
    for fid in sorted(set(om) | set(nm)):
        a, b = om.get(fid), nm.get(fid)
        if a is None:
            out.append({"sid": sid, "kind": "free", "id": fid, "field": "added",
                        "old": None, "new": b.get("quote")})
            continue
        if b is None:
            out.append({"sid": sid, "kind": "free", "id": fid, "field": "removed",
                        "old": a.get("quote"), "new": None})
            continue
        for f in ("page", "category", "quote", "expected", "scan", "occurrences"):
            if norm(a.get(f)) != norm(b.get(f)):
                out.append({"sid": sid, "kind": "free", "id": fid, "field": f,
                            "old": a.get(f), "new": b.get(f)})
    return out


def allowed(rec: dict, allow: list[dict]) -> bool:
    if rec.get("kind") == "amendment":
        return True
    for a in allow:
        if a.get("sid") not in ("*", rec["sid"]):
            continue
        if a.get("kind") not in ("*", rec["kind"]):
            continue
        if a.get("id") not in ("*", rec["id"]):
            continue
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--allow")
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", required=True)
    args = ap.parse_args()
    old_root, new_root = Path(args.old), Path(args.new)
    allow = load(Path(args.allow)) if args.allow else []

    records: list[dict] = []
    for op in sorted(old_root.glob("GS-*/annotation.json")):
        sid = op.parent.name
        np = new_root / sid / "annotation.json"
        if not np.exists():
            records.append({"sid": sid, "kind": "basic", "id": "*", "field": "file",
                            "old": "old-file", "new": None})
            continue
        old, new = load(op), load(np)
        if norm(old.get("basic")) != norm(new.get("basic")):
            records.append({"sid": sid, "kind": "basic", "id": "*", "field": "basic",
                            "old": old.get("basic"), "new": new.get("basic")})
        oc = {c["check_id"]: c for c in old.get("checks", [])}
        nc = {c["check_id"]: c for c in new.get("checks", [])}
        for cid in sorted(set(oc) | set(nc), key=lambda x: list(nc).index(x) if x in nc else 999):
            if cid not in oc:
                records.append({"sid": sid, "kind": "check", "id": cid, "field": "added",
                                "old": None, "new": "check"})
            elif cid not in nc:
                records.append({"sid": sid, "kind": "check", "id": cid, "field": "removed",
                                "old": "check", "new": None})
            else:
                records += diff_check(oc[cid], nc[cid], sid)
        records += diff_free(old, new, sid)
        oa, na = old.get("amendments", []), new.get("amendments", [])
        for k, x in enumerate(na[len(oa):], len(oa) + 1):
            records.append({"sid": sid, "kind": "amendment", "id": f"A{k}",
                            "field": "added", "old": None,
                            "new": f"{x.get('reason', '')}：{x.get('change', '')}"})

    bad = [r for r in records if allow and not allowed(r, allow)]
    lines = ["# 回归差异清单", "",
             f"对比：`{old_root}` → `{new_root}`；改动 {len(records)} 条，"
             f"其中未在 allow 清单 {len(bad)} 条。", "",
             "| 材料 | 范围 | 编号 | 字段 | 原值 | 新值 | 允许 |",
             "|---|---|---|---|---|---|---|"]
    for r in records:
        a = "是" if (not allow or allowed(r, allow)) else "**否**"
        ov = str(r["old"]).replace("\n", " ")[:90]
        nv = str(r["new"]).replace("\n", " ")[:90]
        lines.append(f"| {r['sid']} | {r['kind']} | {r['id']} | {r['field']} | {ov} | {nv} | {a} |")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(args.json).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"差异 {len(records)} 条；未允许 {len(bad)} 条 → {args.out}")
    for r in bad:
        print(f"  !! {r['sid']} {r['kind']} {r['id']} {r['field']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
