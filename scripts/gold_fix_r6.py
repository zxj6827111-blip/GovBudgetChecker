#!/usr/bin/env python3
"""第 6 轮返工：S03 目录/正文（R23）、R21 口径、R22 半角标点、R24 序号体例。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import (  # noqa: E402
    annotation_frozen, scan_r21, scan_r22, scan_r24, scope_of,
)

V = lambda n, p, q, u="": {"name": n, "page": p, "quote": q, "unit": u}  # noqa: E731


def put(ann: dict, cid: str, verdict: str, items: list[dict], reason: str = "") -> None:
    for c in ann["checks"]:
        if c["check_id"] == cid:
            c["verdict"] = verdict
            c["items"] = items
            c["reason"] = reason
            return
    raise KeyError(cid)


def replace_free(ann: dict, sid: str) -> None:
    """把同类的旧条目就地改写为扫描条；新增条放末尾，最后统一按页码重排。"""
    def find(pred):
        return next((f for f in ann["free_findings"] if pred(f)), None)

    def make(rule: str, category: str, desc: str, occ: list[dict]) -> dict:
        return {
            "id": "",
            "scan": rule,
            "page": occ[0]["page"],
            "category": category,
            "quote": occ[0]["quote"],
            "expected": f"{desc}；共 {len(occ)} 处，见 occurrences",
            "confidence": 0.9,
            "occurrences": occ,
        }

    def apply(rule: str, category: str, desc: str, occ: list[dict], pred=None) -> None:
        if not occ:
            return
        new = make(rule, category, desc, occ)
        old = find(pred) if pred else None
        if old is not None:
            old.clear()
            old.update(new)
        else:
            ann["free_findings"].append(new)
        print(f"  {rule}: {len(occ)} 处")

    scope = scope_of(sid)
    if scope == "unit":
        desc21 = "封面为年度决算（单位），正文出现部门口径表述（本表反映部门／本部门／部门决算单位构成／年度部门决算）"
        pred21 = lambda f: "本部门" in f.get("quote", "") or "封面疑漏" in f.get("expected", "")
    else:
        desc21 = "封面为部门决算，正文出现单位口径表述（本表反映单位／单位本年度没有／本单位）"
        pred21 = lambda f: "单位本年度没有" in f.get("quote", "")

    r21 = scan_r21(sid)
    if r21:
        apply("R21", "其他", desc21, r21, pred21)
    apply("R22", "标点", "中文语境使用半角逗号、半角分号或句末半角句点", scan_r22(sid),
          lambda f: f.get("category") == "标点" and ("," in f.get("quote", "") or ";" in f.get("quote", "")
                                                      or f.get("quote", "").rstrip().endswith(".")))
    apply("R24", "其他", "同一列表序号体例混用（“1、”与“3.”混用）", scan_r24(sid),
          lambda f: "序号体例" in f.get("expected", ""))


def renumber(ann: dict) -> dict[str, str]:
    keep = []
    for f in ann["free_findings"]:
        if f.get("id"):
            keep.append((f["id"], f))
    ann["free_findings"].sort(key=lambda f: (f["page"], f.get("category", "")))
    mapping = {}
    for i, f in enumerate(ann["free_findings"], 1):
        new = f"F{i:02d}"
        old = f.get("id") or ""
        if old:
            mapping[old] = new
        f["id"] = new
    return mapping


def update_refs(ann: dict, mapping: dict[str, str]) -> None:
    import re
    for old, new in mapping.items():
        if old == new:
            continue
        pat = re.compile(rf"\b{old}\b")
        for c in ann["checks"]:
            c["reason"] = pat.sub(new, c.get("reason", ""))
            for it in c.get("items", []):
                it["note"] = pat.sub(new, it.get("note", ""))
        for f in ann["free_findings"]:
            f["note"] = pat.sub(new, f.get("note", ""))


def fix_basic(ann: dict) -> None:
    pass


S03 = {
    "GS-2024-06": {
        "items": [{
            "label": "目录“二、机构设置”与正文第二部分标题",
            "verdict": "defect",
            "values": [V("目录标题", 2, "二、机构设置"), V("正文标题", 7, "二、部门决算单位构成")],
            "formula": "",
            "note": "目录第一部分第二项作“机构设置”，正文第7页作“部门决算单位构成”（R23）；其余九表九章与目录一致。",
        }],
        "reason": "目录与正文第一部分第二项标题不一致。",
    },
    "GS-2025-06": {
        "items": [{
            "label": "目录“二、部门决算单位构成”与正文标题",
            "verdict": "defect",
            "values": [V("目录标题", 2, "二、部门决算单位构成"), V("正文标题", 4, "二、机构设置")],
            "formula": "",
            "note": "目录第一部分第二项作“部门决算单位构成”，正文第4页作“机构设置”（R23）；其余九表九章与目录一致。",
        }],
        "reason": "目录与正文第一部分第二项标题不一致。",
    },
    "GS-2024-07": {
        "items": [{
            "label": "目录“二、机构设置”与正文第一部分",
            "verdict": "defect",
            "values": [V("目录标题", 2, "二、机构设置"),
                       V("第一部分末句", 4, "9.承担委领导交办的其他事项。"),
                       V("下一部分", 5, "第二部分")],
            "formula": "",
            "note": "目录第一部分列有“二、机构设置”，正文第一部分只有“一、主要职能”，第4页后直接进入第二部分，整节缺失（R23；缺的是第一部分的节，不适用 R17）。",
        }],
        "reason": "目录有“二、机构设置”，正文第一部分整节缺失。",
    },
}

DROP = {"GS-2024-06": "上海市交通委员会2024 年度决算"}


def main() -> int:
    sids = sys.argv[1:] or list(S03)
    for sid in sids:
        if annotation_frozen(sid):
            print(f"{sid}: 已冻结，跳过")
            continue
        path = GOLD / sid / "annotation.json"
        ann = json.loads(path.read_text(encoding="utf-8"))
        print(f"{sid}:")
        if sid in S03:
            put(ann, "GS-S03", "defect", S03[sid]["items"], S03[sid]["reason"])
        if sid in DROP:
            ann["free_findings"] = [f for f in ann["free_findings"] if DROP[sid] not in f.get("quote", "")]
        replace_free(ann, sid)
        mapping = renumber(ann)
        update_refs(ann, mapping)
        path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  free={len(ann['free_findings'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
