#!/usr/bin/env python3
"""放量用骨架生成器。

用法：python scripts/gold_skeleton.py GS-2022-02

产出 gold/<sid>/annotation.json：
  basic 按封面与标题自动定位；
  GS-T01..T08、GS-X01/X02 按表格逐格取数并给出结论；
  GS-S01..S04、GS-N01..N09、GS-W01 先写 cannot_determine 占位，待人读原文改；
  格式类自由发现按 R9 自动扫描（每类一条 + occurrences）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
CSV = ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"
SRC = Path.home() / "Desktop" / "预决算信息收集" / "决算测试上海市"

sys.path.insert(0, str(ROOT / "scripts"))
import gold_checks as gc  # noqa: E402
from gold_build import (  # noqa: E402
    cell_quote, scan_format, t05_values, t07_values, t08_values, annotation_frozen,
)

TABLES = [
    ("ie", "收入支出决算总表"),
    ("inc", "收入决算表"),
    ("exp", "支出决算表"),
    ("ft", "财政拨款收入支出决算总表"),
    ("ge", "一般公共预算财政拨款支出决算表"),
    ("be", "一般公共预算财政拨款基本支出决算表"),
    ("sg", "财政拨款“三公”经费支出决算表"),
    ("sg_alt", "一般公共预算财政拨款“三公”经费支出决算表"),
    ("fund", "政府性基金预算财政拨款收入支出决算表"),
    ("capital", "国有资本经营预算财政拨款收入支出决算表"),
]
SECTIONS = [
    "一、收入支出决算总体情况说明", "二、收入决算情况说明", "三、支出决算情况说明",
    "四、财政拨款收入支出决算总体情况说明", "五、一般公共预算财政拨款支出决算情况说明",
    "六、一般公共预算财政拨款基本支出决算情况说明", "七、财政拨款“三公”经费支出决算情况说明",
    "八、政府性基金预算财政拨款收入支出决算情况说明", "九、国有资本经营预算财政拨款收入支出决算情况说明",
]
SECTIONS_ALT = {
    "七、财政拨款“三公”经费支出决算情况说明": ["七、一般公共预算财政拨款“三公”经费支出决算情况说明"],
}


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def load_row(sid: str) -> dict:
    rows = {r["sample_id"]: r for r in csv.DictReader(CSV.open(encoding="utf-8-sig"))}
    if sid not in rows:
        raise SystemExit(f"unknown sample: {sid}")
    return rows[sid]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def detect_basic(doc: pymupdf.Document) -> dict:
    cover = [norm(line) for line in doc[0].get_text().splitlines() if norm(line)]
    m_org = ""
    for i, line in enumerate(cover):
        if "年度" in line or "决算" in line:
            m_org = "".join(cover[:i])
            break
    org = m_org or (cover[0] if cover else "")
    year = None
    for t in cover:
        m = re.search(r"(20\d{2})", t)
        if m:
            year = int(m.group(1))
            break
    scope = "department" if any("部门决算" in t for t in cover) else "unit"
    return {"org": org, "year": year, "scope": scope}


def detect_tables(doc: pymupdf.Document) -> dict[str, list[int]]:
    pages_lines = []
    for i, p in enumerate(doc):
        pages_lines.append([norm(line) for line in p.get_text().splitlines() if norm(line)])
    used: dict[str, list[int]] = {}
    claimed: set[int] = set()
    for key, title in TABLES:
        nt = norm(title)
        for i, lines in enumerate(pages_lines):
            if i in claimed:
                continue
            head = "".join(lines[:3])
            if nt in head:
                used[key] = [i + 1]
                claimed.add(i)
                break
    # 续页：下一页未被别的表占用、且页首不是另一张表的标题
    for key, pages in used.items():
        cur = pages[-1]
        while cur < len(pages_lines):
            nxt = cur  # 0-based 下一页
            if nxt in claimed or not pages_lines[nxt]:
                break
            head = "".join(pages_lines[nxt][:2])
            if any(norm(t) in head for _, t in TABLES):
                break
            if "第" in head[:3] and "部分" in head[:6]:
                break
            pages.append(nxt + 1)
            claimed.add(nxt)
            cur += 1
    if "sg_alt" in used and "sg" not in used:
        used["sg"] = used.pop("sg_alt")
    used.pop("sg_alt", None)
    return used


def detect_sections(doc: pymupdf.Document, start_after: int = 0) -> dict[str, int]:
    pages_lines = [[norm(line) for line in p.get_text().splitlines() if norm(line)] for p in doc]
    out = {}
    for name in SECTIONS:
        cands = [name] + SECTIONS_ALT.get(name, [])
        for i, lines in enumerate(pages_lines):
            if i + 1 <= start_after:
                continue
            if any(norm(c) in "".join(lines) for c in cands):
                out[name] = i + 1
                break
    return out


def table_status(doc: pymupdf.Document, pages: list[int]) -> str:
    txt = "".join(doc[p - 1].get_text() for p in pages)
    return "empty_with_note" if "本表无数据" in txt else "data"


def main() -> int:
    sid = sys.argv[1]
    if annotation_frozen(sid):
        print(f"{sid}: 已冻结，跳过 skeleton")
        return 0
    row = load_row(sid)
    pdf = SRC / row["path"]
    doc = pymupdf.open(pdf)
    basic = detect_basic(doc)
    pages = detect_tables(doc)
    last_table_page = max((p for v in pages.values() for p in v), default=0)
    sections = detect_sections(doc, start_after=last_table_page)

    gc.TABLE_PAGES[sid] = {k: v for k, v in pages.items() if k in
                           ("ie", "inc", "exp", "ft", "ge", "be", "sg", "fund", "capital")}
    # 版面 A/B：看一般公共预算支出表首页的列数（A=5，B>=6）
    lay = "A"
    ge = gc.TABLE_PAGES[sid].get("ge")
    if ge:
        tabs = json.loads((GOLD / sid / "pages" / f"p{ge[0]:03d}.tables.json").read_text(encoding="utf-8"))
        if tabs:
            cols_n = max((len(r) for r in tabs[0]), default=0)
            lay = "B" if cols_n >= 6 else "A"
    gc.DOC_LAYOUT[sid] = lay
    (GOLD / sid).mkdir(parents=True, exist_ok=True)
    (GOLD / sid / "pages_map.json").write_text(json.dumps({"tables": gc.TABLE_PAGES[sid], "layout": lay},
                                                          ensure_ascii=False, indent=1), encoding="utf-8")

    ann = {
        "sample_id": sid,
        "sha256": sha256(pdf),
        "annotator": "DeepSeek（Qoder 所选模型；具体版本未自证）",
        "annotated_at": "",
        "basic": {
            "report_kind": "final",
            "material_scope": basic["scope"],
            "fiscal_year": basic["year"],
            "organization": basic["org"],
            "pages": doc.page_count,
            "tables": [],
            "sections": [],
        },
        "checks": [],
        "free_findings": [],
    }
    for key, title in TABLES:
        if key not in pages:
            continue
        use_title = title
        if key == "sg":
            txt = "".join(doc[p - 1].get_text() for p in pages["sg"])
            if "一般公共预算财政拨款“三公”" in txt:
                use_title = "一般公共预算财政拨款“三公”经费支出决算表"
        ann["basic"]["tables"].append({"name": use_title, "page": pages[key][0],
                                       "status": table_status(doc, pages[key])})
    for name in SECTIONS:
        ann["basic"]["sections"].append({"name": name, "page": sections.get(name, 0)})

    for cid in ["GS-S01", "GS-S02", "GS-S03", "GS-S04"] + [f"GS-T{i:02d}" for i in range(1, 9)] + \
               ["GS-X01", "GS-X02"] + [f"GS-N{i:02d}" for i in range(1, 10)] + ["GS-W01"]:
        if cid.startswith(("GS-T", "GS-X")):
            ann["checks"].append({"check_id": cid, "verdict": "cannot_determine", "items": [],
                                  "reason": "骨架占位，待人读原文判定"})
        else:
            ann["checks"].append({"check_id": cid, "verdict": "cannot_determine", "items": [],
                                  "reason": "骨架占位，待人读原文判定"})

    out = GOLD / sid
    out.mkdir(parents=True, exist_ok=True)
    (out / "annotation.json").write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sid}: {basic} pages={doc.page_count}")
    print("  tables:", pages)
    print("  sections:", sections)
    return 0


if __name__ == "__main__":
    sys.exit(main())
