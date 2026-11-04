#!/usr/bin/env python3
"""为任一 33 金标 run 生成比较用 analysis（system_items / final_rows）。

裁决沿用 r2 人工决定（按 doc|raw_id 精确继承），不按展示序号。
用于把 S1 组合基线固定为后续阶段的对照分析。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MATCH = Path(
    "/Volumes/workSSD/software development/GovBudgetChecker/scripts/gold_baseline_match.py")


def load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--match-script", default=str(DEFAULT_MATCH))
    parser.add_argument("--inherit-from", default=None,
                        help="第一批已验收 analysis（system_items/final_rows），按旧 raw_id 继承裁决")
    args = parser.parse_args()
    run, out = Path(args.run).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    gm = load_module("gold_baseline_match", Path(args.match_script).resolve())
    sys.path.insert(0, str(Path(args.match_script).resolve().parent))
    import gold_baseline_decisions_r2 as dec  # noqa: E402

    docs = gm.load_gold()
    items, aux = gm.load_system(run, docs)
    inherited = {}
    if args.inherit_from:
        base = Path(args.inherit_from).resolve()
        old_items = json.loads((base / "system_items.json").read_text(encoding="utf-8"))
        old_rows = {f"{row['doc']}|{row.get('raw_id')}": row
                    for row in json.loads((base / "final_rows.json").read_text(encoding="utf-8"))}
        eval_lib = load_module("batch1_eval_lib", HERE / "gold_baseline_eval_fp_batch1.py")
        pairs, _old_only, _new_only, _review, _dups = eval_lib.align(old_items, items)
        for old, new, _how in pairs:
            row = old_rows.get(f"{old['doc']}|{old.get('raw_id')}")
            if row is not None:
                inherited[new["id"]] = row
    rows = []
    for item in items:
        if item["tier"] in {"N", "D"}:
            continue
        auto = (gm.match_o(item, docs[item["doc"]]) if item["tier"] == "O"
                else gm.match_item(item, docs[item["doc"]]))
        old_row = inherited.get(item["id"])
        d = dec.D.get(f"{item['doc']}|{item.get('raw_id')}") if item["tier"] in {"F", "P"} else None
        row = {
            "id": auto["id"], "doc": item["doc"], "tier": item["tier"], "rule": item["rule"],
            "raw_id": item.get("raw_id") or item["rule"], "sev": item.get("sev"),
            "ct": auto["ct"], "pages": item["pages"], "title": item["title"],
            "message": item["message"], "auto_status": auto["status"],
            "auto_links": [link["unit"] for link in auto["links"]],
            "final": "", "links": [], "cause": "", "why": "", "evidence": "", "amend": False,
            "decided": "auto",
        }
        if old_row is not None and old_row.get("final"):
            row.update(final=old_row.get("final"), cause=old_row.get("cause") or "",
                       why=old_row.get("why") or "", evidence=old_row.get("evidence") or "",
                       amend=bool(old_row.get("amend")), decided=old_row.get("decided") or "auto",
                       links=list(old_row.get("links") or []))
        elif d:
            row.update(final=d["final"], cause=d["cause"], why=d["why"], evidence=d["evidence"],
                       amend=d["amend"], decided="manual")
            row["links"] = list(d["links"] or [])
        else:
            status = auto["status"]
            if item["tier"] == "F":
                row.update(final="TP" if status == "TP" else status, links=list(row["auto_links"]))
            elif item["tier"] == "P":
                if status == "TP":
                    row.update(final="HIT", links=list(row["auto_links"]))
                elif status == "NOGOLD":
                    row.update(final="OUT_OF_SCOPE", why="D-011 口径决议⑤")
                else:
                    # 第一批验收口径：P 层非 HIT/OUT_OF_SCOPE 的自动状态统一记 NOISE。
                    row.update(final="NOISE", links=list(row["auto_links"]))
            elif item["tier"] == "O":
                row.update(final=status, links=list(row["auto_links"]))
            else:
                row.update(final="REF_HIT" if status == "TP" else "REF_OTHER",
                           links=list(row["auto_links"]) if status == "TP" else [])
        rows.append(row)
    (out / "system_items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "final_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "gold_docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"items": len(items), "rows": len(rows), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
