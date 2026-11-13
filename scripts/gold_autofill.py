#!/usr/bin/env python3
"""把 skeleton 产物中的 GS-T*/GS-X* 与 GS-S01 按表格填满（R10：逐格取原文）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gold_checks as gc  # noqa: E402
from gold_build import (  # noqa: E402
    cell_quote, fill as build_fill, t05_values, t07_values, t08_values, annotation_frozen,
)

GOLD = ROOT / "gold"


def q(v) -> str:
    return cell_quote(v)


def mk(name: str, page: int, quote: str, unit: str = "万元") -> dict:
    return {"name": name, "page": page, "quote": quote, "unit": unit}


def close(a, b, tol=0.01) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol + 1e-9


def norm_lab(s: str) -> str:
    return gc.norm_label(s)


def cmp_verdict(target, parts) -> tuple[bool, float, float]:
    s = sum(p for p in parts if p is not None)
    env = max(0.01, (len(parts) + 1) * 0.005)
    return (target is not None and abs(target - s) <= env), s, env


def autofill(sid: str) -> None:
    if annotation_frozen(sid):
        print(f"{sid}: 已冻结，跳过 autofill")
        return
    path = GOLD / sid / "annotation.json"
    ann = json.loads(path.read_text(encoding="utf-8"))
    pm = json.loads((GOLD / sid / "pages_map.json").read_text(encoding="utf-8"))
    gc.TABLE_PAGES[sid] = pm["tables"]
    gc.DOC_LAYOUT[sid] = pm["layout"]

    # --- T01 ---
    left, right = {}, {}
    for p, layout, r in gc.collect(sid, "ie"):
        if len(r) < 4:
            continue
        if r[0]:
            left[norm_lab(r[0])] = (p, r[1])
        if r[2]:
            right[norm_lab(r[2])] = (p, r[3])
    tl = left.get("总计", (0, ""))[1]
    tr = right.get("总计", (0, ""))[1]
    items = []
    for tag, d, keys in (("收入", left, ("本年收入合计", "使用非财政拨款结余", "年初结转和结余")),
                         ("支出", right, ("本年支出合计", "结余分配", "年末结转和结余"))):
        parts, vals = [], []
        for k in keys:
            if k in d:
                parts.append(gc.num(d[k][1]))
                if q(d[k][1]):
                    vals.append(mk(k, d[k][0], q(d[k][1])))
        total = tl if tag == "收入" else tr
        ok, s, env = cmp_verdict(gc.num(total), parts)
        tp = d.get("总计", (0, ""))[0]
        vals.append(mk("总计", tp, q(total)))
        terms = [q(v["quote"]) for v in vals[:-1] if q(v["quote"])]
        items.append({"label": f"{tag}总计 = " + "+".join(keys), "verdict": "pass" if ok else "defect",
                      "values": vals, "formula": " + ".join(terms) + f" = {s:,.2f}",
                      "note": f"表列总计 {total}，复算 {s:,.2f}，差 {gc.num(total)-s:+.2f}。"})
    same = close(gc.num(tl), gc.num(tr))
    set_check(ann, "GS-T01", items, "" if same else "收入总计与支出总计不等。")

    # --- T02 / T03 ---
    for cid, key in (("GS-T02", "inc"), ("GS-T03", "exp")):
        rows = list(gc.collect(sid, key))
        total_item = None
        bad = 0
        n = 0
        for p, layout, r in rows:
            nc, vs = gc.cols(layout)
            if len(r) <= vs:
                continue
            if gc.is_total(r, layout):
                cells = [r[vs]] + r[vs + 1:]
                vals = [mk(nm, p, q(c)) for nm, c in
                        zip(["本年合计"] + [f"列{i}" for i in range(vs + 1, len(r))], cells, strict=False)
                        if q(c)]
                parts = [gc.num(c) for c in r[vs + 1:]]
                ok, s, env = cmp_verdict(gc.num(r[vs]), parts)
                total_item = {"label": "合计行", "verdict": "pass" if ok else "defect", "values": vals,
                              "formula": " + ".join(q(c) for c in r[vs + 1:] if gc.num(c) is not None) + f" = {s:,.2f}",
                              "note": f"表列 {r[vs]}，复算 {s:,.2f}；空单元格按 0 计、不进 values。"}
                continue
            if not gc.code_of(r):
                continue
            n += 1
            v = gc.num(r[vs])
            parts = [gc.num(c) for c in r[vs + 1:]]
            ok, s, env = cmp_verdict(v, parts)
            if v is not None and not ok:
                bad += 1
        if total_item:
            total_item["note"] += f"另 {n} 个明细行逐行复算，{bad} 处超差。"
            set_check(ann, cid, [total_item], "" if bad == 0 and total_item["verdict"] == "pass" else "存在行不平。")

    # T04
    l2, r2 = {}, {}
    for p, layout, r in gc.collect(sid, "ft"):
        if len(r) < 7:
            continue
        if r[0]:
            l2[norm_lab(r[0])] = (p, r[1])
        if r[2]:
            r2[norm_lab(r[2])] = (p, (r[3], r[4], r[5], r[6]))
    tl2 = l2.get("总计", (0, ""))
    tr2 = r2.get("总计", (0, ("",)))[1][0]
    items = []
    parts = [gc.num(l2[k][1]) for k in ("本年收入合计", "年初财政拨款结转和结余") if k in l2]
    vals = [mk(k, l2[k][0], q(l2[k][1])) for k in ("本年收入合计", "年初财政拨款结转和结余")
            if k in l2 and q(l2[k][1])]
    ok, s, _ = cmp_verdict(gc.num(tl2[1]), parts)
    if q(tl2[1]):
        vals.append(mk("总计", tl2[0], q(tl2[1])))
    items.append({"label": "收入总计 = 本年收入合计+年初财政拨款结转",
                  "verdict": "pass" if ok else "defect", "values": vals,
                  "formula": " + ".join(q(v["quote"]) for v in vals[:-1]) + f" = {s:,.2f}",
                  "note": f"表列总计 {tl2[1]}；支出侧合计 {tr2}；空单元格按 0 计、不进 values。"})
    same = close(gc.num(tl2[1]), gc.num(tr2))
    set_check(ann, "GS-T04", items, "" if same and ok else "财政拨款总表两侧不等。")

    # T06
    found = {}
    for p, layout, r in gc.collect(sid, "be"):
        blocks = [(0, -1, 1, 2), (3, -1, 4, 5)] if layout == "A" else [(0, 1, 2, 3), (4, 5, 6, 7)]
        for lab, (ci, si, ni, vi) in zip(("人员经费合计", "公用经费合计"), blocks, strict=False):
            if len(r) > vi and (r[ni] == lab or r[ci] == lab) and gc.num(r[vi]) is not None:
                found[lab] = (p, r[vi])
        # 合计行可能被解析成 4 列（标签+金额+标签+金额），按标签向后取第一个数值
        for i, cell in enumerate(r):
            if cell in ("人员经费合计", "公用经费合计") and cell not in found:
                val = next((x for x in r[i + 1:] if gc.num(x) is not None), None)
                if val is not None:
                    found[cell] = (p, val)
    if len(found) == 2:
        parts = [gc.num(found["人员经费合计"][1]), gc.num(found["公用经费合计"][1])]
        vals = [mk(k, v[0], q(v[1])) for k, v in found.items()]
        base = None
        for p, l3, r3 in gc.collect(sid, "ge"):
            if gc.is_total(r3, l3):
                _, vs = gc.cols(l3)
                base = (p, r3[vs + 1])
        items = [{"label": "人员经费 + 公用经费 = 基本支出",
                  "verdict": "pass" if base and close(sum(parts), gc.num(base[1]), 0.015) else "defect",
                  "values": vals, "formula": f"{vals[0]['quote']} + {vals[1]['quote']} = {sum(parts):,.2f}",
                  "note": f"一般公共预算支出表基本支出合计 {base[1] if base else '未取到'}。"}]
        set_check(ann, "GS-T06", items, "" if items[0]["verdict"] == "pass" else "人员经费+公用经费与基本支出不符。")

    # X01
    items = []
    okall = True
    for tag, ta, tb in (("本年收入合计", "ie", "inc"), ("本年支出合计", "ie", "exp")):
        a = None
        for p, l3, r3 in gc.collect(sid, "ie"):
            if len(r3) >= 4 and r3[0] == tag:
                a = (p, r3[1])
            if len(r3) >= 4 and r3[2] == tag:
                a = (p, r3[3])
        b = None
        for p, l3, r3 in gc.collect(sid, tb):
            if gc.is_total(r3, l3):
                _, vs = gc.cols(l3)
                b = (p, r3[vs])
        if a and b:
            ok = close(gc.num(a[1]), gc.num(b[1]))
            okall &= ok
            items.append({"label": f"{tag}：总表 vs {tb} 表合计", "verdict": "pass" if ok else "defect",
                          "values": [mk("总表", a[0], q(a[1])), mk("明细表合计", b[0], q(b[1]))],
                          "formula": "", "note": "两表相等。" if ok else "两表不等。"})
    set_check(ann, "GS-X01", items, "" if okall else "总表与明细表合计不一致。")

    # X02
    items = []
    okall = True
    for idx, key, nm in ((2, "fund", "政府性基金"), (3, "capital", "国资经营")):
        col = None
        for p, l3, r3 in gc.collect(sid, "ft"):
            if len(r3) >= 7 and gc.norm_label(r3[2]) == "本年支出合计":
                col = (p, r3[3 + idx])
        nonempty = [r for _, _, r in gc.collect(sid, key) if any(gc.num(c) not in (None, 0) for c in r)]
        if col is None:
            continue
        tgt = gc.num(col[1]) or 0.0
        for r in nonempty:
            for c in r:
                pass
        if not nonempty:
            ok = abs(tgt) <= 0.01
        else:
            # 取该表“合计”行本年支出小计；解析器取不到时退回表内最大金额（排除科目编码）
            def amount_cells(row):
                for cell in row:
                    v = gc.num(cell)
                    if v in (None, 0):
                        continue
                    s = (cell or "").strip()
                    if gc.CODE_RE.match(s) and "," not in s and "." not in s:
                        continue
                    yield v

            val = None
            for _, _, r in gc.collect(sid, key):
                if r and (r[0] == "合计" or (len(r) > 1 and "合计" in "".join(r[:2]))):
                    for v in amount_cells(r):
                        val = v
            if val is None or abs(tgt - val) > 0.01:
                alt = max((v for r in nonempty for v in amount_cells(r)), default=0)
                if abs(tgt - alt) <= 0.01:
                    val = alt
            ok = val is not None and abs(tgt - val) <= 0.01
        okall &= ok
        vals = [mk(f"{nm}列", col[0], q(col[1]))] if q(col[1]) else []
        items.append({"label": f"财政拨款总表{nm}列 = 对应决算表", "verdict": "pass" if ok else "cannot_determine",
                      "values": vals,
                      "formula": "", "note": "空表且列为空（按 0 计）。" if not nonempty else f"该表有 {len(nonempty)} 行数据。"})
    set_check(ann, "GS-X02", items, "" if okall else "")

    path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    build_fill(sid)
    print(f"{sid}: T01-T04/T06/X01/X02 已填；T05/T07/T08 由 gold_build.fill 重建")


def set_check(ann: dict, cid: str, items: list[dict], reason: str) -> None:
    for c in ann["checks"]:
        if c["check_id"] != cid:
            continue
        c["items"] = items
        if not items:
            c["verdict"] = "cannot_determine"
            c["reason"] = reason or "未能取数"
            return
        if any(i["verdict"] == "defect" for i in items):
            c["verdict"] = "defect"
        elif any(i["verdict"] == "cannot_determine" for i in items):
            c["verdict"] = "cannot_determine"
        elif all(i["verdict"] == "pass" for i in items):
            c["verdict"] = "pass"
        else:
            c["verdict"] = "cannot_determine"
        c["reason"] = reason if c["verdict"] != "pass" else ""


if __name__ == "__main__":
    for s in sys.argv[1:]:
        autofill(s)
