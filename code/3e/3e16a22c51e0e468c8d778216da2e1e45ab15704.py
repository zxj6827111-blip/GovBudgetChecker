#!/usr/bin/env python3
"""金标准标注的算式核验：把九张核心表的数字读出来，逐项复算。

用法：python scripts/gold_checks.py report GS-2022-01
只输出算式与结果，不下结论；结论由标注人判定后写入 annotation.json。

版面判定按页进行（跨页续表的列数常少于首页）：
  A  编码单列 + 名称，数值自第 3 列起（2022/2023 全篇；2024/2025 的续页）
  B  类/款/项三列 + 名称，数值自第 5 列起（2024/2025 首页）
  be 基本支出表两块并排：A 为 6 列，B 为 8 列
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = ROOT / "gold"
TOL = 0.01
ENVELOPE_UNIT = 0.005

TABLE_PAGES = {
    "GS-2022-01": {"ie": [5], "inc": [6, 7], "exp": [8, 9], "ft": [10, 11],
                   "ge": [12], "be": [13], "sg": [14], "fund": [15], "capital": [16]},
    "GS-2023-01": {"ie": [10], "inc": [11, 12, 13, 14], "exp": [15, 16, 17, 18],
                   "ft": [19, 20], "ge": [21, 22, 23], "be": [24], "sg": [25],
                   "fund": [26], "capital": [27]},
    "GS-2024-01": {"ie": [8], "inc": [9, 10, 11], "exp": [12, 13, 14],
                   "ft": [15, 16], "ge": [17, 18], "be": [19, 20], "sg": [21],
                   "fund": [22], "capital": [23]},
    "GS-2025-01": {"ie": [6], "inc": [7], "exp": [8], "ft": [9, 10],
                   "ge": [11], "be": [12, 13], "sg": [14], "fund": [15], "capital": [16]},
}

DOC_LAYOUT = {"GS-2022-01": "A", "GS-2023-01": "A", "GS-2024-01": "B", "GS-2025-01": "B"}

NUM_RE = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$")
CODE_RE = re.compile(r"^\d{3,7}$")


def num(s: str) -> float | None:
    s = (s or "").strip().replace("\n", "").replace(" ", "")
    if not s or not NUM_RE.match(s):
        return None
    return float(s.replace(",", ""))


def norm(s: str) -> str:
    return (s or "").replace("\n", "").strip()


def page_rows(sample_id: str, page: int) -> list[list[str]]:
    tables = json.loads((GOLD_DIR / sample_id / "pages" / f"p{page:03d}.tables.json").read_text(encoding="utf-8"))
    if not tables:
        return []
    t = max(tables, key=lambda x: len(x) * max((len(r) for r in x), default=0))
    return [[norm(c) for c in r] for r in t]


def is_header(row: list[str]) -> bool:
    joined = "".join(row)
    if not any(k in joined for k in ("科目编码", "科目名称", "决算数", "预算数", "经济分类", "项目")):
        # 仅含"类/款/项/合计"等表头字样的行
        if not (set(row[:3]) <= {"类", "款", "项", "合计", ""} and any(row[:3])):
            return False
    return sum(1 for c in row if num(c) is not None) <= 1


def fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


def cmp(label: str, target: float | None, parts: list[tuple[str, float | None]], note: str = "") -> bool:
    if target is None:
        print(f"  [跳过] {label}: 目标为空")
        return True
    s = sum(v for _, v in parts if v is not None)
    diff = target - s
    env = max(TOL, (len(parts) + 1) * ENVELOPE_UNIT)
    ok = abs(diff) <= env
    print(f"  {'OK ' if ok else '!! '}{label}: {fmt(target)} =? {' + '.join(fmt(v) for _, v in parts)} = {fmt(s)}"
          f"  差 {diff:+.2f}  (容差 {env:.3f}){note}")
    return ok


def page_layout(key: str, rows: list[list[str]]) -> str:
    n = max((len(r) for r in rows), default=0)
    thresh = {"be": 8, "ge": 6, "inc": 11, "exp": 10}.get(key, 99)
    return "B" if n >= thresh else "A"


def iter_pages(sample_id: str, key: str):
    """逐页产出 (page, layout, data_rows)。版面按该页表格列数判定。"""
    for p in TABLE_PAGES[sample_id][key]:
        rows = page_rows(sample_id, p)
        layout = page_layout(key, rows)
        data = [r for r in rows if not is_header(r)]
        yield p, layout, data


def cols(layout: str) -> tuple[int, int]:
    """(name_col, val_start)"""
    return (1, 2) if layout == "A" else (3, 4)


def code_of(row: list[str]) -> str:
    c = row[0] if row else ""
    return c if CODE_RE.match(c) else ""


def is_total(row: list[str], layout: str) -> bool:
    nc, _ = cols(layout)
    return bool(row) and (row[0] == "合计" or (len(row) > nc and row[nc] == "合计"))


def collect(sample_id: str, key: str) -> list[tuple[int, str, list[str]]]:
    out = []
    for p, layout, data in iter_pages(sample_id, key):
        for r in data:
            out.append((p, layout, r))
    return out


def code_level(code: str) -> int:
    """1=类 2=款 3=项；兼容 3/5/7 位与“尾部补零”写法。"""
    if not CODE_RE.match(code or ""):
        return 0
    if len(code) == 3:
        return 1
    if len(code) == 5:
        return 2
    if len(code) == 7:
        if code[3:] == "0000":     # 2010000 形式：类
            return 1
        if code[5:] == "00":       # 2010300 形式：款
            return 2
        return 3
    return 0


def code_key(code: str) -> str:
    lv = code_level(code)
    return code[:3] if lv == 1 else code[:5] if lv == 2 else code


def word_rows(sample_id: str, page: int, tol: float = 4.0) -> list[list[dict]]:
    """R13：按坐标把一页的词聚成行，作为与 pdfplumber 表格解析相互独立的第二种读法。"""
    path = GOLD_DIR / sample_id / "pages" / f"p{page:03d}.words.json"
    if not path.exists():
        return []
    words = json.loads(path.read_text(encoding="utf-8"))
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    for r in rows:
        r.sort(key=lambda x: x["x0"])
    return rows


def _amount_token(tokens: list[dict]) -> tuple[str, float] | None:
    """取行内第一个数值（类/款行的合计列；基本支出表每块只有一个数值）。"""
    for w in tokens:
        v = num(w["text"])
        if v is not None:
            return w["text"], v
    return None


def text_entries(sample_id: str, key: str) -> list[tuple[int, str, str, float, str]]:
    """逐行读页面文字，返回 (page, code, name, value, quote)。

    编码行 = 行内第一个 3/5/7 位纯数字；名称 = 编码与金额之间的非数字词；
    金额 = 该行最后一个数值（类/款行的第一列合计）。
    """
    out = []
    for p in TABLE_PAGES.get(sample_id, {}).get(key, []):
        for row in word_rows(sample_id, p):
            code_w = next((w for w in row if CODE_RE.match(w["text"])), None)
            if code_w is None:
                continue
            rest = [w for w in row if w["x0"] > code_w["x1"] + 1]
            amt = _amount_token(rest)
            if amt is None:
                continue
            first_num_x = next((w["x0"] for w in rest if num(w["text"]) is not None), None)
            name = "".join(w["text"] for w in rest
                           if num(w["text"]) is None and (first_num_x is None or w["x0"] < first_num_x))
            out.append((p, code_w["text"], name, amt[1], amt[0]))
    return out


def text_be_entries(sample_id: str) -> list[tuple[int, str, str, str, float, str]]:
    """基本支出表逐行读文字，返回 (page, 类, 款, 名称, 金额, 原文)。

    页面左右两块并排：一行里出现两个 3 位类码时以两者中点为界；只有一个时按 x<250 归左块。
    """
    out = []
    for p in TABLE_PAGES.get(sample_id, {}).get("be", []):
        for row in word_rows(sample_id, p):
            codes = [w for w in row if re.fullmatch(r"\d{3}", w["text"])]
            if not codes:
                continue
            if len(codes) >= 2:
                split = (codes[0]["x1"] + codes[1]["x0"]) / 2
            else:
                split = 300.0
            blocks = [
                [w for w in row if w["x0"] < split],
                [w for w in row if w["x0"] >= split],
            ] if len(codes) >= 2 else (
                [[w for w in row if w["x0"] < 300], [w for w in row if w["x0"] >= 300]]
            )
            for block in blocks:
                block = sorted(block, key=lambda x: x["x0"])
                # 少数页面把 5 位经济分类码拆成“30 / 213”两个词，先合并
                split5 = None
                if (len(block) >= 2 and re.fullmatch(r"\d{2}", block[0]["text"])
                        and re.fullmatch(r"\d{3}", block[1]["text"])):
                    merged = dict(block[0])
                    merged["text"] = block[0]["text"] + block[1]["text"]
                    block = [merged] + block[2:]
                    split5 = merged["text"]
                cw = next((w for w in block if re.fullmatch(r"\d{3}", w["text"])), None)
                if split5:
                    cw = block[0]
                    class_code, sub = split5[:3], split5[3:]
                else:
                    class_code, sub = (cw["text"] if cw else ""), ""
                if cw is None:
                    continue
                after = [w for w in block if w["x0"] > cw["x1"] + 1]
                if not split5 and after and re.fullmatch(r"\d{2}", after[0]["text"]):
                    sub = after[0]["text"]
                    after = after[1:]
                amt = _amount_token(after)
                if amt is None:
                    continue
                first_num_x = next((w["x0"] for w in after if num(w["text"]) is not None), None)
                name = "".join(w["text"] for w in after
                               if num(w["text"]) is None and (first_num_x is None or w["x0"] < first_num_x))
                out.append((p, class_code, sub, name, amt[1], amt[0]))
    return out


def report_ie(sample_id: str) -> None:
    print("== 收入支出决算总表 ==")
    left: dict[str, float | None] = {}
    right: dict[str, float | None] = {}
    for p, _, r in collect(sample_id, "ie"):
        if len(r) < 4:
            continue
        if r[0]:
            left[r[0]] = num(r[1])
        if r[2]:
            right[r[2]] = num(r[3])
    for k, v in left.items():
        if v is not None:
            print(f"   收 {k} = {fmt(v)}")
    for k, v in right.items():
        if v is not None:
            print(f"   支 {k} = {fmt(v)}")
    tl, tr = left.get("总计"), right.get("总计")
    print(f"  [T01] 收入总计 {fmt(tl)} vs 支出总计 {fmt(tr)}")
    inc_items = [(k, v) for k, v in left.items() if re.match(r"^[一二三四五六七八九十]+、", k)]
    exp_items = [(k, v) for k, v in right.items() if re.match(r"^[一二三四五六七八九十]+、", k)]
    cmp("收入总计 = 本年收入合计+非财政拨款结余+年初结转", tl,
        [("本年收入合计", left.get("本年收入合计")),
         ("使用非财政拨款结余", left.get("使用非财政拨款结余")),
         ("年初结转和结余", left.get("年初结转和结余"))])
    cmp("支出总计 = 本年支出合计+结余分配+年末结转", tr,
        [("本年支出合计", right.get("本年支出合计")),
         ("结余分配", right.get("结余分配")),
         ("年末结转和结余", right.get("年末结转和结余"))])
    cmp("本年收入合计 = 各收入来源之和", left.get("本年收入合计"), inc_items)
    cmp("本年支出合计 = 各支出功能分类之和", right.get("本年支出合计"), exp_items)


def hierarchy(sample_id: str, key: str, total_label: str) -> None:
    entries, total_row = [], None
    for p, layout, r in collect(sample_id, key):
        nc, vs = cols(layout)
        if is_total(r, layout):
            total_row = (p, layout, r)
            continue
        c = code_of(r)
        if not c:
            continue
        entries.append((c, norm(r[nc]) if len(r) > nc else "", num(r[vs]) if len(r) > vs else None, p))
    known = {e[0] for e in entries}
    recovered = []
    for p, code, name, value, quote in text_entries(sample_id, key):
        if code in known or code_level(code) not in (1, 2) or value is None:
            continue
        entries.append((code, name, value, p))
        known.add(code)
        recovered.append((code, p))
    problems = 0
    for code, name, v, p in entries:
        lv = code_level(code)
        if lv not in (1, 2):
            continue
        ks = [e for e in entries if code_level(e[0]) == lv + 1 and code_key(e[0]).startswith(code_key(code))]
        if not ks:
            continue
        env = max(TOL, (len(ks) + 1) * ENVELOPE_UNIT)
        s = sum(x[2] for x in ks if x[2] is not None)
        d = (v or 0) - s
        if abs(d) > env:
            problems += 1
            print(f"    !! p{p} {code} {name}: {fmt(v)} =? " + " + ".join(fmt(x[2]) for x in ks) + f" = {fmt(s)} 差 {d:+.2f}")
    if total_row:
        p, layout, r = total_row
        _, vs = cols(layout)
        tops = [e for e in entries if code_level(e[0]) == 1]
        cmp(f"{total_label} 合计 = 各类之和", num(r[vs]), [(e[1], e[2]) for e in tops])
    if recovered:
        print(f"    R13 补行：{', '.join(f'{c}@p{p}' for c, p in recovered)}")
    print(f"    层级比对：{problems} 处超差（共 {len([e for e in entries if len(e[0]) in (3, 5)])} 个类/款，{len(entries)} 行）")


def report_inc(sample_id: str) -> None:
    print("== 收入决算表 ==")
    problems = 0
    for p, layout, r in collect(sample_id, "inc"):
        nc, vs = cols(layout)
        if len(r) <= vs:
            continue
        if is_total(r, layout):
            cmp("合计行 本年收入合计 = 各来源之和", num(r[vs]),
                [(f"列{i}", num(r[i])) for i in range(vs + 1, len(r))])
            continue
        if not code_of(r):
            continue
        v = num(r[vs])
        parts = [(f"列{i}", num(r[i])) for i in range(vs + 1, len(r)) if num(r[i]) is not None]
        if v is None:
            continue
        env = max(TOL, (len(parts) + 1) * ENVELOPE_UNIT)
        s = sum(x[1] for x in parts)
        if abs(v - s) > env:
            problems += 1
            print(f"    !! p{p} {code_of(r)} {norm(r[nc])}: {fmt(v)} =? " + " + ".join(fmt(x[1]) for x in parts) + f" = {fmt(s)} 差 {v-s:+.2f}")
    print(f"    逐行比对：{problems} 处超差（共 {len([r for _, _, r in collect(sample_id, 'inc') if code_of(r)])} 行）")
    hierarchy(sample_id, "inc", "收入")


def report_exp(sample_id: str) -> None:
    print("== 支出决算表 ==")
    problems = 0
    for p, layout, r in collect(sample_id, "exp"):
        nc, vs = cols(layout)
        if len(r) <= vs:
            continue
        if is_total(r, layout):
            cmp("合计行 本年支出合计 = 基本+项目+上缴+经营+附属", num(r[vs]),
                [(f"列{i}", num(r[i])) for i in range(vs + 1, len(r))])
            continue
        if not code_of(r):
            continue
        v = num(r[vs])
        parts = [(f"列{i}", num(r[i])) for i in range(vs + 1, len(r)) if num(r[i]) is not None]
        if v is None:
            continue
        env = max(TOL, (len(parts) + 1) * ENVELOPE_UNIT)
        s = sum(x[1] for x in parts)
        if abs(v - s) > env:
            problems += 1
            print(f"    !! p{p} {code_of(r)} {norm(r[nc])}: {fmt(v)} =? " + " + ".join(fmt(x[1]) for x in parts) + f" = {fmt(s)} 差 {v-s:+.2f}")
    print(f"    逐行比对：{problems} 处超差（共 {len([r for _, _, r in collect(sample_id, 'exp') if code_of(r)])} 行）")
    hierarchy(sample_id, "exp", "支出")


def report_ft(sample_id: str) -> None:
    print("== 财政拨款收入支出决算总表 ==")
    left: dict[str, float | None] = {}
    right: dict[str, tuple] = {}
    for _, _, r in collect(sample_id, "ft"):
        if len(r) < 7:
            continue
        if r[0]:
            left[r[0]] = num(r[1])
        if r[2]:
            right[r[2]] = (num(r[3]), num(r[4]), num(r[5]), num(r[6]))
    for k, v in right.items():
        print(f"   支 {k} = 合计{fmt(v[0])} / 一般公共{fmt(v[1])} / 政府性基金{fmt(v[2])} / 国资{fmt(v[3])}")
    tl = left.get("总计")
    cmp("财政拨款 收入总计 = 支出总计", tl, [("支出总计", right.get("总计", (None,))[0])])
    cmp("收入总计 = 本年收入合计 + 年初财政拨款结转和结余", tl,
        [("本年收入合计", left.get("本年收入合计")), ("年初财政拨款结转和结余", left.get("年初财政拨款结转和结余"))])
    cmp("支出总计 = 本年支出合计 + 年末财政拨款结转和结余", tl,
        [("本年支出合计", right.get("本年支出合计", (None,) * 4)[0]),
         ("年末财政拨款结转和结余", right.get("年末财政拨款结转和结余", (None,) * 4)[0])])
    print(f"    政府性基金列 本年支出合计 = {fmt(right.get('本年支出合计', (None,) * 4)[2])}")
    print(f"    国资列 本年支出合计 = {fmt(right.get('本年支出合计', (None,) * 4)[3])}")


def report_ge(sample_id: str) -> None:
    print("== 一般公共预算财政拨款支出决算表 ==")
    hierarchy(sample_id, "ge", "一般公共预算")
    for p, layout, r in collect(sample_id, "ge"):
        if is_total(r, layout):
            _, vs = cols(layout)
            cmp("合计行 = 基本支出 + 项目支出", num(r[vs]), [("基本支出", num(r[vs + 1])), ("项目支出", num(r[vs + 2]))])


def be_blocks(layout: str) -> list[tuple[int, int, int, int]]:
    """(类列, 款列(-1 表示编码不分列), 名称列, 数值列)"""
    if layout == "A":
        return [(0, -1, 1, 2), (3, -1, 4, 5)]
    return [(0, 1, 2, 3), (4, 5, 6, 7)]


def report_be(sample_id: str) -> None:
    print("== 一般公共预算财政拨款基本支出决算表 ==")
    rows = [(p, lyt, r) for p, lyt, r in collect(sample_id, "be")]
    text_rows = text_be_entries(sample_id)
    found: dict[str, float | None] = {}
    for _, lyt, r in rows:
        for lab, (ci, si, ni, vi) in zip(("人员经费合计", "公用经费合计"), be_blocks(lyt)):
            if len(r) > vi and (r[ni] == lab or r[ci] == lab):
                found[lab] = num(r[vi])
    print(f"    人员经费合计={fmt(found.get('人员经费合计'))}  公用经费合计={fmt(found.get('公用经费合计'))}")
    for lyt in ("A", "B"):
        for tag, (ci, si, ni, vi) in zip(("左块", "右块"), be_blocks(lyt)):
            for p, lyt2, r in rows:
                if lyt2 != lyt:
                    continue
                if len(r) <= vi or not re.fullmatch(r"\d{3}", r[ci] or ""):
                    continue
                if si >= 0 and r[si]:          # 有款号 = 明细行，不是大类
                    continue
                code = r[ci]
                if si >= 0:
                    kids = [x for x in rows if x[1] == lyt and len(x[2]) > vi
                            and x[2][ci] == code and re.fullmatch(r"\d{2}", x[2][si] or "")]
                else:
                    kids = [x for x in rows if x[1] == lyt and len(x[2]) > vi
                            and re.fullmatch(r"\d{5}", x[2][ci] or "") and x[2][ci].startswith(code)]
                known = {norm(k[2][ci]) if si < 0 else code + norm(k[2][si]) for k in kids}
                parts = [(norm(k[2][ni]), num(k[2][vi])) for k in kids]
                for tp, cat, sub, name, value, quote in text_rows:
                    full = cat + sub
                    if cat == code and sub and full not in known:
                        parts.append((f"{name}（R13 文字补行）", value))
                        known.add(full)
                if not parts:
                    continue
                cmp(f"{tag} p{p} {code} {norm(r[ni])} = 明细之和", num(r[vi]), parts)


def report_sg(sample_id: str) -> None:
    print("== 三公经费表 ==")
    rows = [[r for _, _, r in collect(sample_id, "sg") if len(r) >= 12 and num(r[0]) is not None]]
    if not rows or not rows[-1]:
        print("    三公表无数据行")
        return
    data = rows[0][-1]
    vals = [num(c) for c in data[:12]]
    labels = ["合计预算", "合计决算", "出国预算", "出国决算", "小计预算", "小计决算",
              "购置预算", "购置决算", "运行预算", "运行决算", "接待预算", "接待决算"]
    for lab, v in zip(labels, vals):
        print(f"   {lab} = {fmt(v)}")
    for tag, base in (("预算", 0), ("决算", 1)):
        cmp(f"{tag} 合计 = 出国 + 小计 + 接待", vals[base],
            [("出国", vals[2 + base]), ("小计", vals[4 + base]), ("接待", vals[10 + base])])
        cmp(f"{tag} 小计 = 购置 + 运行", vals[4 + base],
            [("购置", vals[6 + base]), ("运行", vals[8 + base])])


def report_fund(sample_id: str, key: str, name: str) -> None:
    print(f"== {name} ==")
    rows = [r for _, _, r in collect(sample_id, key)]
    nonempty = [r for r in rows if any(num(c) not in (None, 0) for c in r)]
    print(f"    非空数据行：{len(nonempty)}")
    for r in nonempty[:12]:
        print("    ", " | ".join(r))


def audit_text(sample_id: str, key: str) -> None:
    """R13：把表格解析结果与逐行文字读法对账，列出文字里多出的编码行。"""
    table = []
    for p, layout, r in collect(sample_id, key):
        c = code_of(r)
        if not c or is_total(r, layout):
            continue
        _, vs = cols(layout)
        table.append((c, p, num(r[vs]) if len(r) > vs else None))
    text = text_entries(sample_id, key)
    tmap = {c: (p, v, q) for p, c, n, v, q in text}
    known = {c for c, _, _ in table}
    print(f"== {sample_id} {key} R13 对账 ==")
    print(f"   表格解析 {len(table)} 行；逐行文字 {len(text)} 行")
    for c, p, v in table:
        if c not in tmap:
            print(f"   [仅表格] p{p} {c} {fmt(v)}")
    for p, c, n, v, q in text:
        if c in known:
            continue
        print(f"   [仅文字] p{p} {c} {n} {q} = {fmt(v)}")
    dup = [c for c in known if sum(1 for x in text if x[1] == c) > 1]
    if dup:
        print(f"   文字重复编码：{sorted(set(dup))[:20]}")


def load_pages_map(sample_id: str) -> None:
    pm = GOLD_DIR / sample_id / "pages_map.json"
    if not pm.exists():
        return
    data = json.loads(pm.read_text(encoding="utf-8"))
    TABLE_PAGES[sample_id] = data["tables"]
    DOC_LAYOUT[sample_id] = data.get("layout", "A")


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: gold_checks.py report|audit <sample_id> [key]")
    cmd = sys.argv[1]
    sid = sys.argv[2]
    load_pages_map(sid)
    if cmd == "audit":
        if len(sys.argv) < 4:
            raise SystemExit("usage: gold_checks.py audit <sample_id> <key>")
        print(f"##### {sid} #####")
        audit_text(sid, sys.argv[3])
        return 0
    if cmd != "report":
        raise SystemExit("usage: gold_checks.py report|audit <sample_id> [key]")
    print(f"##### {sid} #####")
    report_ie(sid)
    report_inc(sid)
    report_exp(sid)
    report_ft(sid)
    report_ge(sid)
    report_be(sid)
    report_sg(sid)
    report_fund(sid, "fund", "政府性基金预算财政拨款收入支出决算表")
    report_fund(sid, "capital", "国有资本经营预算财政拨款收入支出决算表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
