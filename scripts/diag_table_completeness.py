"""表格完整度体检：以同表种的行数中位数为基线，找出明显偏少的表。

用途：发现"表识别到了，但只抽到部分行"的隐性丢数——这类问题不会让
九表齐全率下降，但会让规则拿不到明细。

用法: .venv/bin/python scripts/diag_table_completeness.py <dir> [...]
"""
from __future__ import annotations

import glob
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.pdf_parser import PDFParser  # noqa: E402

CORE = ["FIN_01_income_expenditure_total", "FIN_02_income", "FIN_03_expenditure",
        "FIN_04_fiscal_grant_total", "FIN_05_general_public_expenditure",
        "FIN_06_basic_expenditure", "FIN_07_three_public", "FIN_08_gov_fund",
        "FIN_09_state_capital"]


def main(paths: list[str]) -> None:
    pdfs: list[Path] = []
    for p in paths:
        pp = Path(os.path.expanduser(p))
        pdfs.extend(sorted(pp.rglob("*.pdf")) if pp.is_dir() else [pp])

    records = []
    for f in pdfs:
        try:
            art = PDFParser(None).build_parse_artifact(str(f))
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {f.name}: {e}")
            continue
        for t in art.table_fragments:
            if t.table_code not in CORE:
                continue
            hdr = min(t.header_row_count or 1, len(t.rows))
            data_rows = len([r for r in t.rows[hdr:] if any(str(c or "").strip() for c in r)])
            pages = len({c.page_number for c in t.cells}) if t.cells else 0
            records.append((f.name, t.table_code, data_rows, pages))

    # 同表种基线
    baseline: dict[str, float] = {}
    grouped: dict[str, list[int]] = defaultdict(list)
    for _, code, rows, _ in records:
        grouped[code].append(rows)
    for code, vals in grouped.items():
        baseline[code] = statistics.median(vals)

    print(f"样本 {len(pdfs)} 份，表记录 {len(records)} 条\n")
    print("各表种数据行数基线（中位数）：")
    for code in CORE:
        if code in baseline:
            vals = sorted(grouped[code])
            print(f"  {code:<34} 中位 {baseline[code]:>5.0f}  范围 {vals[0]}~{vals[-1]}  n={len(vals)}")

    print("\n疑似行数偏少（< 基线 50% 且基线 ≥ 8）：")
    flagged = 0
    for name, code, rows, pages in records:
        base = baseline.get(code, 0)
        if base >= 8 and rows < base * 0.5:
            print(f"  {name[:32]:<34} {code:<32} 行数 {rows:>3} / 基线 {base:.0f}  页数 {pages}")
            flagged += 1
    if not flagged:
        print("  无")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    main(args)
