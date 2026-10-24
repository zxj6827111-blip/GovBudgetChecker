"""入库质量报告：把"能不能信这份解析结果"变成一份可批量跑的评级清单。

面向日常收料：新一批材料进来后跑一次，直接得到
  A 可信入库 / B 可用但有瑕疵 / C 需人工确认
而不是等规则跑完再逐条看"取数不足"。

用法: .venv/bin/python scripts/intake_report.py <dir> [--json out.json] [--md out.md]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.fiscal_table_rules import NINE_TABLE_RULES  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402
from src.services.table_reading_assessment import (  # noqa: E402
    assess_table_reading,
    number,
)

CORE = [
    "FIN_01_income_expenditure_total", "FIN_02_income", "FIN_03_expenditure",
    "FIN_04_fiscal_grant_total", "FIN_05_general_public_expenditure",
    "FIN_06_basic_expenditure", "FIN_07_three_public", "FIN_08_gov_fund",
    "FIN_09_state_capital",
]
#: 这两个表在多数单位是"无此项"，缺表不直接判 C
CONDITIONAL = {"FIN_08_gov_fund", "FIN_09_state_capital"}


def _gap_is_zero(gap: dict) -> bool:
    raw = str(gap.get("raw_text") or "").strip()
    if not raw:
        return False
    value = number(raw)
    return value is not None and value == Decimal(0)


def evaluate(pdf: Path) -> dict:
    art = PDFParser(None).build_parse_artifact(str(pdf))
    reading = assess_table_reading(art)
    gaps = reading.get("gaps") or []

    tokens = sum(len((p.get("amounts") or []))
                 for p in (art.reading_assessment or {}).get("pages", []))
    stored = sum(1 for t in art.table_fragments for c in t.cells
                 if c.bbox and number(c.raw_text) is not None)

    have = {t.table_code for t in art.table_fragments if t.table_code in NINE_TABLE_RULES}
    missing_core = [c for c in CORE if c not in have and c not in CONDITIONAL]
    missing_cond = [c for c in CORE if c not in have and c in CONDITIONAL]

    zero_gaps = [g for g in gaps if _gap_is_zero(g)]
    hard_gaps = [g for g in gaps if not _gap_is_zero(g)]
    blocked_tables = sorted({
        inst.table_code
        for g in hard_gaps for uid in (g.get("affected_fragment_uids") or [])
        for inst in art.logical_tables
        if uid in inst.fragment_uids
    })

    if missing_core or hard_gaps:
        grade = "C"
    elif missing_cond or zero_gaps:
        grade = "B"
    else:
        grade = "A"

    reasons = []
    if missing_core:
        reasons.append(f"缺核心表 {'/'.join(missing_core)}")
    if missing_cond:
        reasons.append(f"缺条件表 {'/'.join(missing_cond)}（需有空表说明）")
    if hard_gaps:
        reasons.append(f"非零读取缺口 {len(hard_gaps)} 处，影响 {'/'.join(blocked_tables) or '未知表'}")
    if zero_gaps:
        reasons.append(f"零值读取缺口 {len(zero_gaps)} 处（不影响勾稽，已在事实留痕）")

    return {
        "file": pdf.name,
        "year": pdf.parent.name,
        "pages": art.page_count,
        "grade": grade,
        "amount_tokens": tokens,
        "stored_amounts": stored,
        # 召回按缺口口径：缺几个金额词 / 页面金额词总数。
        # 不用"已入库格数/词数"——跨页续表会把同一金额重复计入单元格，比值会超 1。
        "recall": round(1 - len(gaps) / tokens, 6) if tokens else None,
        "gaps": len(gaps),
        "gap_reasons": dict(Counter(g.get("reason") for g in gaps)),
        "zero_gap": len(zero_gaps),
        "hard_gap": len(hard_gaps),
        "blocked_tables": blocked_tables,
        "missing_core": missing_core,
        "missing_conditional": missing_cond,
        "reasons": reasons,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--json")
    ap.add_argument("--md")
    args = ap.parse_args()

    root = Path(os.path.expanduser(args.dir))
    pdfs = sorted(root.rglob("*.pdf")) if root.is_dir() else [root]
    if not pdfs:
        raise SystemExit("目录下没有 PDF")

    rows = []
    for pdf in pdfs:
        try:
            rows.append(evaluate(pdf))
        except Exception as e:  # noqa: BLE001
            rows.append({"file": pdf.name, "grade": "X", "reasons": [f"解析失败 {type(e).__name__}: {e}"]})
        r = rows[-1]
        print(f"  [{r['grade']}] {pdf.name[:32]:<34} {('；'.join(r.get('reasons') or []))[:60]}",
              flush=True)

    counts = Counter(r["grade"] for r in rows)
    print(f"\n共 {len(rows)} 份：A {counts.get('A',0)} / B {counts.get('B',0)} "
          f"/ C {counts.get('C',0)} / 失败 {counts.get('X',0)}")
    ok = [r for r in rows if r.get("recall") is not None]
    if ok:
        print(f"金额召回（缺口口径）：平均 {sum(r['recall'] for r in ok)/len(ok):.4%}"
              f"｜缺口合计 {sum(r['gaps'] for r in ok)} 处"
              f"（其中零值 {sum(r['zero_gap'] for r in ok)}、非零 {sum(r['hard_gap'] for r in ok)}）")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        lines = ["# 入库质量报告", "",
                 f"材料数 {len(rows)}｜A {counts.get('A',0)}｜B {counts.get('B',0)}｜C {counts.get('C',0)}｜失败 {counts.get('X',0)}", "",
                 "| 评级 | 文件 | 页码 | 缺口 | 核心表缺失 | 说明 |",
                 "|---|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda x: (x["grade"], x["file"])):
            lines.append(
                f"| {r['grade']} | {r['file']} | {r.get('pages','')} | {r.get('gaps','')} | "
                f"{'/'.join(r.get('missing_core') or []) or '-'} | {'；'.join(r.get('reasons') or []) or '无'} |"
            )
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    for path in (args.json, args.md):
        if path:
            print(f"已写入 {path}")


if __name__ == "__main__":
    main()
