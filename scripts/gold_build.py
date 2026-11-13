#!/usr/bin/env python3
"""返工/放量辅助：R10 算式取值填充 + R9 格式扫描。

用法：
  python scripts/gold_build.py fill GS-2022-01     # 按表格重建 T05/T07/T08 的 values 与 formula
  python scripts/gold_build.py format GS-2022-01   # 打印 R9 五类格式问题的 occurrences 片段
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

import pdfplumber
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
CSV = ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
SRC = Path.home() / "Desktop" / "预决算信息收集" / "决算测试上海市"

sys.path.insert(0, str(ROOT / "scripts"))
from gold_checks import (  # noqa: E402
    TABLE_PAGES, DOC_LAYOUT, collect, cols, is_total, code_of, norm, num, page_rows, code_level,
    text_entries, text_be_entries, load_pages_map,
)


def ensure_pages(sid: str) -> None:
    load_pages_map(sid)


# R8：各年模板原文，不报自由发现
TEMPLATE_PHRASES = ("支出收入", "单价100万元（含）", "其中:")


def is_template_phrase(text: str) -> bool:
    return any(p in (text or "") for p in TEMPLATE_PHRASES)


def annotation_frozen(sid: str) -> bool:
    path = GOLD / sid / "annotation.json"
    return path.exists() and bool(json.loads(path.read_text(encoding="utf-8")).get("truth_frozen_at"))


def cell_quote(s: str) -> str:
    return (s or "").replace("\n", "").strip()


def table_pages(sid: str) -> set[int]:
    """第二部分决算表各页：优先用 pages_map，试标 4 份回落到硬编码表。"""
    pm = GOLD / sid / "pages_map.json"
    if pm.exists():
        tables = json.loads(pm.read_text(encoding="utf-8")).get("tables", {})
        return {p for pages in tables.values() for p in pages}
    return {p for pages in TABLE_PAGES.get(sid, {}).values() for p in pages}


def t05_values(sid: str) -> list[dict]:
    """一般公共预算支出表：各类 + 合计，全部逐格取原文。R13：缺类时用逐行文字补。"""
    ensure_pages(sid)
    out = []
    total = None
    for p, layout, r in collect(sid, "ge"):
        _, vs = cols(layout)
        if is_total(r, layout):
            total = (p, cs := cell_quote(r[vs]))
            continue
        c = code_of(r)
        if not c:
            continue
        v = num(r[vs])
        if v is None:
            continue
        out.append({"name": f"{c} {norm(r[cols(layout)[0]])}", "page": p,
                    "quote": cell_quote(r[vs]), "unit": "万元", "_code": c})
    classes = [o for o in out if code_level(o["_code"]) == 1]
    if total:
        known = {o["_code"] for o in classes}
        for page, code, name, value, quote in text_entries(sid, "ge"):
            if code_level(code) != 1 or code in known or value is None:
                continue
            classes.append({"name": f"{code} {name}", "page": page,
                            "quote": quote, "unit": "万元",
                            "_code": code, "_recovered": True})
            known.add(code)
    for o in classes:
        o.pop("_code", None)
    if total:
        classes.append({"name": "合计", "page": total[0], "quote": total[1], "unit": "万元"})
    return classes


def t07_values(sid: str) -> list[dict]:
    ensure_pages(sid)
    rows = [r for _, _, r in collect(sid, "sg") if len(r) >= 12 and num(r[0]) is not None]
    if not rows:
        return []
    data = rows[-1]
    # 三公表只有一页，取该页
    page = TABLE_PAGES[sid]["sg"][-1]
    names = ["合计（预算数）", "合计（决算数）", "因公出国（境）费（预算数）", "因公出国（境）费（决算数）",
             "公务用车小计（预算数）", "公务用车小计（决算数）", "公务用车购置费（预算数）", "公务用车购置费（决算数）",
             "公务用车运行维护费（预算数）", "公务用车运行维护费（决算数）", "公务接待费（预算数）", "公务接待费（决算数）"]
    return [{"name": n, "page": page, "quote": cell_quote(c), "unit": "万元"}
            for n, c in zip(names, data[:12])]


def t08_values(sid: str) -> list[dict]:
    """基本支出表：按文档顺序输出“类”及其“款”。版面按页判定；续页类列可能为空。

    R13：pdfplumber 漏行时，用逐行文字读法补回同类的缺失款行（如 GS-2025-02 的 31022）。
    """
    ensure_pages(sid)
    rows = list(collect(sid, "be"))
    out = []
    for lyt in ("A", "B"):
        blocks = [(0, -1, 1, 2), (3, -1, 4, 5)] if lyt == "A" else [(0, 1, 2, 3), (4, 5, 6, 7)]
        page_rows_l = [(p, r) for p, l2, r in rows if l2 == lyt]
        for ci, si, ni, vi in blocks:
            cur = None
            def cname(x: str) -> str:
                s0 = re.sub(r"\s+", "", norm(x))
                if s0.startswith("支出") and s0.endswith("支出") and len(s0) > 4:
                    s0 = s0[2:]
                return s0
            for p, r in page_rows_l:
                if len(r) <= vi:
                    continue
                def norm_code(x: str) -> str:
                    x = (x or "").strip()
                    if re.fullmatch(r"\d{3}", x):
                        return x
                    m = re.search(r"(30\d|31\d)\d{2}$", x)
                    if m:
                        return m.group(0)[-5:]
                    m = re.search(r"(30\d|31\d)$", x)
                    if m:
                        return m.group(0)[-3:]
                    return x
                code = norm_code(r[ci])
                sub = norm_code(r[si]) if si >= 0 else ""
                if re.fullmatch(r"\d{3}", code) and (si < 0 or not sub):
                    cur = code
                    if num(r[vi]) is not None:
                        out.append({"name": f"{code} {cname(r[ni])}（类）", "page": p,
                                    "quote": cell_quote(r[vi]), "unit": "万元", "_k": "cat"})
                    continue
                if cur is None or num(r[vi]) is None:
                    continue
                if si >= 0:
                    if not re.fullmatch(r"\d{2}", sub):
                        continue
                    out.append({"name": f"{cur}{sub} {cname(r[ni])}（款）", "page": p,
                                "quote": cell_quote(r[vi]), "unit": "万元", "_k": "kid"})
                else:
                    if not re.fullmatch(r"\d{5}", code):
                        continue
                    out.append({"name": f"{code} {cname(r[ni])}（款）", "page": p,
                                "quote": cell_quote(r[vi]), "unit": "万元", "_k": "kid"})
    known = {(x["name"].split(" ")[0], x["_k"]) for x in out}
    cats = {x["name"].split(" ")[0] for x in out if x["_k"] == "cat"}
    for p, cat, sub, name, value, quote in text_be_entries(sid):
        full = cat + sub
        kind = "kid" if sub else "cat"
        if kind == "kid" and (full, kind) in known:
            continue
        if kind == "cat" and (cat, kind) in known:
            continue
        if kind == "kid" and cat not in cats:
            continue
        clean_name = re.sub(r"\s+", "", name)
        row = {"name": f"{full} {clean_name}（{'款' if sub else '类'}）", "page": p,
               "quote": quote, "unit": "万元", "_k": kind, "_recovered": True}
        pos = max((i for i, x in enumerate(out) if x["name"].startswith(cat)), default=len(out) - 1)
        out.insert(pos + 1, row)
        known.add((full, kind))
    return out


def fmt_num(s: str) -> str:
    return cell_quote(s)


def fill(sid: str) -> None:
    if annotation_frozen(sid):
        print(f"{sid}: 已冻结，跳过 fill")
        return
    ensure_pages(sid)
    path = GOLD / sid / "annotation.json"
    ann = json.loads(path.read_text(encoding="utf-8"))

    # T05
    vals = t05_values(sid)
    classes = [v for v in vals if v["name"] != "合计"]
    total = [v for v in vals if v["name"] == "合计"][0]
    recovered = sum(1 for v in vals if v.get("_recovered"))
    expr = " + ".join(v["quote"] for v in classes)
    s = sum(num(v["quote"]) for v in classes)
    tail = "" if abs(s - num(total["quote"])) <= 0.005 else f"（表列 {total['quote']}，差 {s - num(total['quote']):+.2f}）"
    formula = f"{expr} = {s:,.2f}" + tail
    ok = abs(s - num(total["quote"])) <= max(0.01, (len(classes) + 1) * 0.005)
    for c in ann["checks"]:
        if c["check_id"] == "GS-T05":
            if not c["items"]:
                c["items"] = [{"label": "合计 = 各类之和", "verdict": "pass", "values": [],
                               "formula": "", "note": ""}]
            c["items"][0]["values"] = vals
            c["items"][0]["formula"] = formula
            c["items"][0]["verdict"] = "pass" if ok else "defect"
            note = c["items"][0].get("note", "")
            if recovered:
                note = (note + f" R13：表格解析漏 {recovered} 个类，已按逐行文字补全并复算。").strip()
            c["items"][0]["note"] = note

    # T07
    vals = t07_values(sid)
    if not vals:
        for c in ann["checks"]:
            if c["check_id"] == "GS-T07":
                c["items"] = []
                c["verdict"] = "cannot_determine"
                c["reason"] = "三公经费表无数据行，表内取不到数值。"
        vals = []
    v = {x["name"]: x["quote"] for x in vals}
    have = [x for x in vals if x["quote"]]

    def expr(*names: str, total: str) -> str:
        terms = [v[n] for n in names if v.get(n)]
        return " + ".join(terms) + f" = {v[total]}"

    def eq(*names: str, total: str) -> str:
        terms = [v[n] for n in names if v.get(n)]
        subs = sum(num(t) for t in terms)
        tgt = v.get(total, "")
        tail = ""
        if num(tgt) is not None and abs(subs - num(tgt)) > 0.005:
            tail = f"（表列 {tgt}，差 {subs - num(tgt):+.2f}）"
        return " + ".join(terms) + f" = {subs:,.2f}" + tail

    f1 = eq("因公出国（境）费（预算数）", "公务用车小计（预算数）", "公务接待费（预算数）", total="合计（预算数）")
    f2 = eq("因公出国（境）费（决算数）", "公务用车小计（决算数）", "公务接待费（决算数）", total="合计（决算数）")
    f3 = eq("公务用车购置费（预算数）", "公务用车运行维护费（预算数）", total="公务用车小计（预算数）")
    f4 = eq("公务用车购置费（决算数）", "公务用车运行维护费（决算数）", total="公务用车小计（决算数）")
    empty = [k for k, q in v.items() if not q]
    def ok_eq(*names, total):
        terms = [num(v[n]) for n in names if v.get(n)]
        t = num(v.get(total))
        return t is not None and abs(sum(terms) - t) <= 0.015

    t07_ok = (ok_eq("因公出国（境）费（预算数）", "公务用车小计（预算数）", "公务接待费（预算数）", total="合计（预算数）")
              and ok_eq("因公出国（境）费（决算数）", "公务用车小计（决算数）", "公务接待费（决算数）", total="合计（决算数）")
              and ok_eq("公务用车购置费（预算数）", "公务用车运行维护费（预算数）", total="公务用车小计（预算数）")
              and ok_eq("公务用车购置费（决算数）", "公务用车运行维护费（决算数）", total="公务用车小计（决算数）"))
    for c in ann["checks"]:
        if c["check_id"] == "GS-T07" and vals:
            c["items"] = [{
                "label": "预算数、决算数 合计 = 出国+小计+接待",
                "verdict": "pass" if t07_ok else "defect",
                "values": have,
                "formula": f"{f1}；{f2}；{f3}；{f4}",
                "note": ("空单元格（按 0 计，不进算式）：" + "、".join(empty) + "。") if empty else "",
            }]

    # T08
    vals = t08_values(sid)
    by_cat: dict[str, list[dict]] = {}
    cur = None
    for x in vals:
        if x["_k"] == "cat":
            cur = x["name"].split("（类）")[0]
            by_cat[cur] = [x]
        elif cur:
            by_cat[cur].append(x)
    for c in ann["checks"]:
        if c["check_id"] != "GS-T08":
            continue
        items = []
        for cat, xs in sorted(by_cat.items()):
            head, kids = xs[0], xs[1:]
            if not kids:
                continue
            expr = " + ".join(k["quote"] for k in kids)
            subtotal = sum(num(k["quote"]) for k in kids)
            target = num(head["quote"])
            safe = [dict(x) for x in xs]
            rec = sum(1 for x in safe if x.get("_recovered"))
            for x in safe:
                x.pop("_k", None)
                x.pop("_recovered", None)
            note = f"类合计 {head['quote']}，款之和复算 {subtotal:,.2f}，差 {target-subtotal:+.2f}。"
            if rec:
                note += f" R13：表格解析漏 {rec} 个款，已按逐行文字补全。"
            items.append({
                "label": f"{cat} = 所属款之和",
                "verdict": "pass" if abs(target - subtotal) <= max(0.01, (len(kids) + 1) * 0.005) else "defect",
                "values": safe,
                "formula": f"{expr} = {subtotal:,.2f}",
                "note": note,
            })
        if items:
            c["items"] = items
            c["verdict"] = "defect" if any(i["verdict"] == "defect" for i in items) else "pass"
            c["reason"] = "" if c["verdict"] == "pass" else c.get("reason") or "经济分类类合计与所属款之和不符。"

    for c in ann["checks"]:
        if c["check_id"] not in ("GS-T05", "GS-T07", "GS-T08"):
            continue
        vs = [i.get("verdict") for i in c["items"]]
        if "defect" in vs:
            c["verdict"] = "defect"
        elif vs and all(x == "pass" for x in vs):
            c["verdict"] = "pass"
        elif vs:
            c["verdict"] = "cannot_determine"
        if c["verdict"] == "pass":
            c["reason"] = ""
    path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sid}: T05/T07/T08 已按表格重建 values 与 formula")


# ---------------- R9 格式扫描 ----------------

P_THOU = re.compile(r"(?<![\d,.])(\d{4,}(?:\.\d+)?)(?![\d,])")
P_COMMA_NUM = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d+)+(?:\.\d+)?)(?![\d,])")
P_FULL = re.compile(r"(?<![\d,.，])(\d[\d,，]*(?:\.\d+)?)(?![\d,])")
P_AMT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万元|万(?!元)|元|%)")
P_COLON = re.compile(r"(?:[\u4e00-\u9fff]:(?!\d))|(?:(?<!\d):[\u4e00-\u9fff])")
P_DUP = re.compile(r"(。|，|、|；|：)\1")
P_DOT2 = re.compile(r"\d\.\.\d+(?:\.\d+)?%?")

QTY_UNITS = set("人次个辆台项批件户家所条只张套份名位艘架部")


def narrative_lines(pdf_path: Path, skip_pages: set[int] | None = None) -> list[tuple[int, str]]:
    skip_pages = skip_pages or set()
    out = []
    pm = pymupdf.open(pdf_path)
    with pdfplumber.open(pdf_path) as pl:
        for i, page in enumerate(pm):
            if i + 1 in skip_pages:
                continue
            boxes = [t.bbox for t in pl.pages[i].find_tables()]
            acc: list[str] = []
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                t = "".join(s["text"] for line in b["lines"] for s in line["spans"])
                x0, y0, x1, y1 = b["bbox"]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if any(a <= cx <= c and bb <= cy <= d for a, bb, c, d in boxes):
                    continue
                if t.strip():
                    acc.append(t)
            if acc:
                out.append((i + 1, "\n".join(acc)))
            acc = []
    return out


def _after(text: str, m: re.Match, n: int = 3) -> str:
    return text[m.end():m.end() + n].lstrip()


def _before(text: str, m: re.Match, n: int = 10) -> str:
    return text[max(0, m.start() - n):m.start()]


def _is_quantity(text: str, m: re.Match) -> bool:
    after = _after(text, m, 2)
    return bool(after) and after[0] in QTY_UNITS


def _is_percent(text: str, m: re.Match) -> bool:
    return _after(text, m, 2).startswith("%")


def _is_code_or_non_amount(text: str, m: re.Match) -> bool:
    """R9：千分位只认金额。排除科目编码、电话/热线、数量、百分比、年份。"""
    tok = m.group(1)
    if _is_percent(text, m) or _is_quantity(text, m):
        return True
    if "." not in tok and 1990 <= int(tok) <= 2030:
        return True
    before, after = _before(text, m), _after(text, m, 4)
    if before.endswith("第") or after.startswith(("号", "文号")):
        return True
    if tok == "12345" or any(k in before for k in ("热线", "电话", "咨询", "投诉", "邮编", "传真")):
        return True
    if "." not in tok:
        if any(k in before for k in ("编码", "科目", "代码")) or after[:1] in ("类", "款", "项"):
            return True
        money_ctx = any(k in before + after for k in
                        ("万元", "万", "元", "金额", "支出", "收入", "预算", "决算", "拨款", "经费"))
        if len(tok) in (5, 6, 7) and not money_ctx:
            return True
    return False


def scan_format(sid: str) -> dict[str, list[dict]]:
    rows = {r["sample_id"]: r for r in csv.DictReader(CSV.open(encoding="utf-8-sig"))}
    lines = narrative_lines(SRC / rows[sid]["path"], skip_pages=table_pages(sid))
    cats: dict[str, list[dict]] = {"千分位": [], "小数位": [], "半角冒号": [], "标点连用": [], "双小数点": []}

    def add(cat: str, page: int, quote: str) -> None:
        cats[cat].append({"page": page, "quote": quote})

    for p, t in lines:
        for m in P_DOT2.finditer(t):
            add("双小数点", p, m.group(0))
        if P_DOT2.search(t):
            continue
        for m in P_COMMA_NUM.finditer(t):
            s = m.group(1)
            groups = s.split(".")[0].split(",")
            if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
                add("千分位", p, s)
        for m in P_THOU.finditer(t):
            if _is_code_or_non_amount(t, m):
                continue
            add("千分位", p, m.group(1))
        for m in P_FULL.finditer(t):
            if "，" not in m.group(1) or _is_percent(t, m) or _is_quantity(t, m):
                continue
            add("千分位", p, m.group(1))
        for m in P_AMT.finditer(t):
            v = m.group(1)
            ctx = t[max(0, m.start() - 6):m.end() + 8]
            if "单价100万元（含）" in re.sub(r"\s+", "", ctx):  # R8 模板门槛
                continue
            if "，" in v:                             # 全角逗号已归千分位
                continue
            d = len(v.split(".")[1]) if "." in v else 0
            if d != 2:
                add("小数位", p, v + m.group(2))
        for m in P_COLON.finditer(t):
            ctx = t[max(0, m.start() - 12):m.end() + 12]
            if _before(t, m, 2).endswith("其中"):       # R8 模板“其中:”
                continue
            if "因公出国" in ctx or "公务接待费" in ctx:  # R8 模板原文
                continue
            add("半角冒号", p, ctx.strip())
        for m in P_DUP.finditer(t):
            add("标点连用", p, t.strip()[:60])
    return cats


# ---------------- R21 口径措辞 ----------------

def source_pdf(sid: str) -> Path:
    rows = {r["sample_id"]: r for r in csv.DictReader(CSV.open(encoding="utf-8-sig"))}
    return SRC / rows[sid]["path"]


def scope_of(sid: str) -> str:
    p = GOLD / sid / "annotation.json"
    if p.exists():
        scope = json.loads(p.read_text(encoding="utf-8")).get("basic", {}).get("material_scope")
        if scope:
            return scope
    row = {r["sample_id"]: r for r in csv.DictReader(CSV.open(encoding="utf-8-sig"))}[sid]
    return "department" if row["kind"] == "部门决算" else "unit"


R21_PATTERNS = {
    "unit": [("本表反映部门", ()), ("本部门", ("本部门建立了",)), ("部门决算单位构成", ()), ("年度部门决算", ())],
    "department": [("本表反映单位", ()), ("单位本年度没有", ()), ("本单位", ())],
}


def scan_r21(sid: str, scope: str | None = None) -> list[dict]:
    """R21：与封面口径相反的措辞。每页按出现位置合并重叠匹配，避免“本部门决算”重复计。"""
    scope = scope or scope_of(sid)
    doc = pymupdf.open(source_pdf(sid))
    out = []
    for i, page in enumerate(doc):
        flat = re.sub(r"\s+", "", page.get_text())
        hits = []
        for pat, excludes in R21_PATTERNS[scope]:
            for m in re.finditer(pat, flat):
                ctx = flat[max(0, m.start() - 15):m.end() + 22]
                if any(e in ctx for e in excludes):
                    continue
                hits.append((m.start(), m.end(), ctx))
        hits.sort()
        last_end = -1
        for s, e, ctx in hits:
            if s < last_end:
                continue
            out.append({"page": i + 1, "quote": ctx})
            last_end = e
    return out


# ---------------- R22 半角逗号/分号/句末句点 ----------------

def narrative_page_lines(sid: str) -> list[tuple[int, str]]:
    """逐行文字。R22/R24 范围同 R9：只限文字部分，决算表各页整页跳过。"""
    skip = table_pages(sid)
    out = []
    for i, page in enumerate(pymupdf.open(source_pdf(sid))):
        if i + 1 in skip:
            continue
        for line in page.get_text().splitlines():
            if line.strip():
                out.append((i + 1, line))
    return out


def _cjk(c: str) -> bool:
    return bool(c) and "\u4e00" <= c <= "\u9fff"


def _ctx(text: str, i: int, before: int = 12, after: int = 14) -> str:
    return text[max(0, i - before):i + after].strip()


def scan_r22(sid: str) -> list[dict]:
    """R22：中文语境半角逗号、分号、句末句点。数字千分位/小数点/列表序号不计。"""
    out = []
    for p, t in narrative_page_lines(sid):
        for m in re.finditer(r"[,;]", t):
            i = m.start()
            prev = t[i - 1] if i else ""
            nxt = t[i + 1] if i + 1 < len(t) else ""
            if prev.isdigit() and nxt.isdigit():          # 1,234 / 小数
                continue
            if prev.isascii() and prev.isalnum() and nxt.isascii() and nxt.isalnum():
                continue                                  # ASCII 代码/网址
            if not (_cjk(prev) or prev in "%）)》】”>%" or _cjk(nxt) or nxt.isdigit()):
                continue
            out.append({"page": p, "quote": _ctx(t, i)})
        for m in re.finditer(r"\.", t):
            i = m.start()
            prev = t[i - 1] if i else ""
            nxt = t[i + 1] if i + 1 < len(t) else ""
            if prev.isdigit() and nxt.isdigit():          # 小数点
                continue
            if nxt and not nxt.isspace():                 # “1.” 序号、省略号等
                continue
            if prev.isdigit():                            # 行尾序号
                continue
            if _cjk(prev) or prev in "%）)》】”":
                out.append({"page": p, "quote": _ctx(t, i)})
    return out


# ---------------- R24 序号体例 ----------------

ITEM_RE = re.compile(r"^(\d{1,2})[、.．](?!\d)\s*(\S.*)$")


def scan_r24(sid: str) -> list[dict]:
    """R24：同一列表内“1、”与“3.”混用。只在编号连续（n, n+1）且分隔符不同处报。"""
    out = []
    prev_num: int | None = None
    prev_delim = ""
    for p, t in narrative_page_lines(sid):
        s = t.strip()
        m = ITEM_RE.match(s)
        if not m:
            continue
        num, delim = int(m.group(1)), s[len(m.group(1))]
        if prev_num is not None and num == prev_num + 1 and delim != prev_delim:
            out.append({"page": p, "quote": s[:36]})
        prev_num, prev_delim = num, delim
    return out


# ---------------- R23 目录与正文标题 ----------------

PART_RE = re.compile(r"^第([一二三四])部分")
HEAD_RE = re.compile(r"^([一二三四五六七八九十]+)、(.+)$")


def _head_key(s: str) -> str:
    x = re.sub(r"\s+", "", s or "").replace("的", "")
    for suf in ("情况说明", "说明", "情况"):
        if x.endswith(suf):
            x = x[:-len(suf)]
    return x


def _toc_range(doc) -> tuple[int, int] | None:
    """目录所在页码区间（0 基）：从含“目录”的页到“第四部分”页。"""
    pages = []
    for i, page in enumerate(doc):
        flat = re.sub(r"\s+", "", page.get_text())
        if "目录" in flat[:120]:
            pages.append(i)
    if not pages:
        return None
    start = pages[0]
    end = start
    while end + 1 < doc.page_count:
        flat = re.sub(r"\s+", "", doc[end].get_text())
        if "第四部分" in flat:
            break
        end += 1
    return start, end


def _title_done(x: str) -> bool:
    return bool(x) and x.endswith(("说明", "概况", "名词解释", "表"))


def _part_title(lines: list[str], k: int) -> str:
    """第X部分标题常被换行拆开：把后续行接到标题末尾，直到看起来完整。"""
    s = lines[k]
    m = PART_RE.match(s)
    rest = s[m.end():] if m else ""
    j = k + 1
    while j < len(lines) and not _title_done(rest):
        nxt = lines[j]
        if not nxt or PART_RE.match(nxt) or HEAD_RE.match(nxt):
            break
        rest += nxt
        j += 1
    return rest


def _heading_title(lines: list[str], k: int) -> str:
    """第三部分节标题可能换行：接上后续行，直到“…说明/情况”。"""
    m = HEAD_RE.match(lines[k])
    rest = m.group(2) if m else ""
    j = k + 1
    while j < len(lines) and not rest.endswith(("说明", "情况")):
        nxt = lines[j]
        if not nxt or PART_RE.match(nxt) or HEAD_RE.match(nxt):
            break
        rest += nxt
        j += 1
    return rest


def scan_r23(sid: str) -> dict:
    """R23：第一部分、第三部分、第四部分目录与正文标题（含部分标题）逐条比对。"""
    doc = pymupdf.open(source_pdf(sid))
    rng = _toc_range(doc)
    if not rng:
        return {"toc": [], "body": {}, "problems": [], "missing_toc": True}
    ts, te = rng
    toc: list[dict] = []
    toc_parts: dict[int, tuple[str, int]] = {}
    part = 0
    for i in range(ts, te + 1):
        lines = [re.sub(r"\s+", "", x) for x in doc[i].get_text().splitlines()]
        for k, s in enumerate(lines):
            if not s:
                continue
            pm = PART_RE.match(s)
            if pm:
                part = "一二三四".index(pm.group(1)) + 1
                title = _part_title(lines, k)
                if title:
                    toc_parts[part] = (title, i + 1)
                if part == 4:
                    toc.append({"part": 4, "num": "", "title": title, "page": i + 1})
                continue
            hm = HEAD_RE.match(s)
            if hm and part in (1, 3):
                title = _heading_title(lines, k) if part == 3 else hm.group(2)
                toc.append({"part": part, "num": hm.group(1), "title": title, "page": i + 1})

    body_starts: dict[int, int] = {}
    body_parts: dict[int, tuple[str, int]] = {}
    for i, page in enumerate(doc):
        if ts <= i <= te:
            continue
        lines = [re.sub(r"\s+", "", x) for x in page.get_text().splitlines()]
        for k, s in enumerate(lines):
            pm = PART_RE.match(s)
            if pm:
                part = "一二三四".index(pm.group(1)) + 1
                body_starts.setdefault(part, i)
                title = _part_title(lines, k)
                if title:
                    body_parts.setdefault(part, (title, i + 1))
                break
    body: dict[int, list[dict]] = {1: [], 3: [], 4: []}
    for part in (1, 3, 4):
        start = body_starts.get(part)
        if start is None:
            continue
        end = min((body_starts[p] for p in (part + 1, part + 2, part + 3) if p in body_starts), default=doc.page_count)
        for i in range(start, end):
            lines = [re.sub(r"\s+", "", x) for x in doc[i].get_text().splitlines()]
            for k, s in enumerate(lines):
                hm = HEAD_RE.match(s)
                if hm and part in (1, 3):
                    title = _heading_title(lines, k) if part == 3 else hm.group(2)
                    body[part].append({"num": hm.group(1), "title": title, "page": i + 1})

    problems = []
    for item in toc:
        part = item["part"]
        if part == 4:
            continue
        match = next((b for b in body[part] if b["num"] == item["num"]), None)
        if not match:
            # R14：PyMuPDF 漏读整段时，用 pdfplumber 在所属部分范围内再找标题
            start = body_starts.get(part)
            end = min((body_starts[q] for q in (part + 1, part + 2, part + 3) if q in body_starts),
                      default=doc.page_count)
            if start is not None:
                key = re.sub(r"\s+", "", item["num"] + "、" + item["title"])
                with pdfplumber.open(source_pdf(sid)) as pl:
                    for i in range(start, end):
                        if key in re.sub(r"\s+", "", pl.pages[i].extract_text() or ""):
                            match = {"num": item["num"], "title": item["title"], "page": i + 1}
                            break
        if not match:
            problems.append({"part": part, "num": item["num"], "toc": item["title"],
                             "body": "", "toc_page": item["page"], "body_page": 0,
                             "status": "missing"})
        elif _head_key(item["title"]) != _head_key(match["title"]):
            problems.append({"part": part, "num": item["num"], "toc": item["title"],
                             "body": match["title"], "toc_page": item["page"],
                             "body_page": match["page"], "status": "mismatch"})
    for part in (1, 3, 4):
        t, b = toc_parts.get(part), body_parts.get(part)
        if t and b and _head_key(t[0]) != _head_key(b[0]):
            problems.append({"part": part, "num": "", "toc": t[0], "body": b[0],
                             "toc_page": t[1], "body_page": b[1], "status": "mismatch"})
    return {"toc": toc, "body": body, "problems": problems, "missing_toc": False}


R28_RE = re.compile(r"[\u4e00-\u9fff]{1,12}[\[【（(]\d{4}[\]】）)]\d+号")


def scan_r28(sid: str) -> list[dict]:
    """R28：文号年份未用六角括号〔〕。"""
    out = []
    for i, page in enumerate(pymupdf.open(source_pdf(sid))):
        flat = re.sub(r"\s+", "", page.get_text())
        for m in R28_RE.finditer(flat):
            out.append({"page": i + 1, "quote": m.group(0)})
    return out


def scan_all(sid: str) -> dict:
    return {"r21": scan_r21(sid), "r22": scan_r22(sid), "r24": scan_r24(sid),
            "r28": scan_r28(sid), "r23": scan_r23(sid)["problems"]}


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: gold_build.py {fill|format|audit-be|scan} <sample_id> [r21|r22|r23|r24]")
    cmd, sid = sys.argv[1], sys.argv[2]
    if cmd == "fill":
        fill(sid)
    elif cmd == "audit-be":
        ensure_pages(sid)
        print(f"== {sid} 基本支出表 R13 对账 ==")
        for x in t08_values(sid):
            flag = " [文字补行]" if x.get("_recovered") else ""
            print(f"   p{x['page']} {x['name']} {x['quote']}{flag}")
    elif cmd == "scan":
        what = sys.argv[3] if len(sys.argv) > 3 else "all"
        if what == "r21":
            print(json.dumps(scan_r21(sid), ensure_ascii=False, indent=1))
        elif what == "r22":
            print(json.dumps(scan_r22(sid), ensure_ascii=False, indent=1))
        elif what == "r24":
            print(json.dumps(scan_r24(sid), ensure_ascii=False, indent=1))
        elif what == "r28":
            print(json.dumps(scan_r28(sid), ensure_ascii=False, indent=1))
        elif what == "r23":
            print(json.dumps(scan_r23(sid), ensure_ascii=False, indent=1))
        else:
            print(json.dumps(scan_all(sid), ensure_ascii=False, indent=1))
    else:
        cats = scan_format(sid)
        for k, v in cats.items():
            print(f"## {k}（{len(v)} 处）")
            print(json.dumps(v, ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
