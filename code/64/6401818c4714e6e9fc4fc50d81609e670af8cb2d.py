#!/usr/bin/env python3
"""从 B1 基本支出表原页解析 T06 末级项（非零项），生成可审清单。"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pymupdf

RUN = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/gold_b1_20261103/run_20261103T150928")
PDFS = json.loads((RUN / "b1_pdfs.json").read_text(encoding="utf-8"))
OUT = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_closeout_round4_20261104_work/t06_items.json")

LEAF_RE = re.compile(r"(\d{3})\s*(\d{2})\s+([^\d]+?)\s+([\d,]+\.\d{2})")
CAT_RE = re.compile(r"(?<!\d)(\d{3})\s+([^\d]+?)\s+([\d,]+\.\d{2})(?!\d)")


def parse(sid: str, pages: list[int]) -> list[dict]:
    doc = pymupdf.open(PDFS[sid])
    items: list[dict] = []
    for page in pages:
        text = re.sub(r"\s+", " ", doc[page - 1].get_text())
        for match in LEAF_RE.finditer(text):
            code3, code2, name, value = match.groups()
            if "合计" in name:
                continue
            code = f"{code3}{code2}"
            items.append({
                "code": code,
                "name": name.strip(),
                "page": page,
                "column": "人员经费" if code3 in {"301", "303"} else "公用经费",
                "category": code3,
                "value": value.replace(",", ""),
            })
    # 去重（同一行可能被两个正则/两页重复捕获）
    seen, unique = set(), []
    for item in items:
        key = (item["code"], item["page"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def main() -> int:
    data = {}
    for sid, pages in (("B1-2024-D02", [15, 16]), ("B1-2025-D01", [18, 19])):
        items = parse(sid, pages)
        nonzero = [item for item in items if Decimal(item["value"]) != 0]
        for group, column in (("人员经费", "人员经费"), ("公用经费", "公用经费")):
            rows = [item for item in nonzero if item["column"] == column]
            data.setdefault(sid, {})[group] = {
                "count": len(rows),
                "sum": str(sum((Decimal(item["value"]) for item in rows), Decimal("0"))),
                "items": rows,
            }
        data[sid]["all_nonzero_count"] = len(nonzero)
        data[sid]["all_printed_count"] = len(items)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({sid: {"nonzero": value["all_nonzero_count"],
                            "printed": value["all_printed_count"],
                            "people": [value["人员经费"]["count"], value["人员经费"]["sum"]],
                            "public": [value["公用经费"]["count"], value["公用经费"]["sum"]]}
                      for sid, value in data.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
