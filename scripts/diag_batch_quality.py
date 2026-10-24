"""批量评测：9 张核心表的识别完整度 + 表内勾稽。

用法: .venv/bin/python scripts/diag_batch_quality.py <dir_or_pdf> [...]
"""
from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.pdf_parser import PDFParser  # noqa: E402

CORE = [
    "FIN_01_income_expenditure_total",
    "FIN_02_income",
    "FIN_03_expenditure",
    "FIN_04_fiscal_grant_total",
    "FIN_05_general_public_expenditure",
    "FIN_06_basic_expenditure",
    "FIN_07_three_public",
    "FIN_08_gov_fund",
    "FIN_09_state_capital",
]


def num(s: str | None) -> Decimal | None:
    if s is None:
        return None
    t = str(s).replace(",", "").replace("，", "").strip()
    if not t or t in {"-", "—", "－"}:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def class_rows(rows: list[list[str]]) -> tuple[Decimal, Decimal, int]:
    """返回 (类级合计, 明细合计, 类级行数)。类级 = 编码为3位数字。"""
    cls_sum = Decimal(0)
    det_sum = Decimal(0)
    n = 0
    for r in rows:
        code = (r[0] or "").strip() if r else ""
        if len(code) == 3 and code.isdigit():
            v = num(r[4] if len(r) > 4 else None)
            if v is not None:
                cls_sum += v
                n += 1
        elif len(code) == 7 and code.isdigit():
            v = num(r[4] if len(r) > 4 else None)
            if v is not None:
                det_sum += v
    return cls_sum, det_sum, n


def evaluate(pdf_path: Path) -> dict:
    parser = PDFParser(None)
    art = parser.build_parse_artifact(str(pdf_path))
    by_code: dict[str, list] = {}
    for t in art.table_fragments:
        by_code.setdefault(t.table_code, []).append(t)

    missing = [c for c in CORE if c not in by_code]
    report = {
        "file": pdf_path.name,
        "pages": art.page_count,
        "scanned_pages": [p.page_number for p in art.page_states if p.is_scanned],
        "tables": len(art.table_fragments),
        "missing": missing,
        "rows": {},
        "audit": [],
    }
    for c in CORE:
        if c in by_code:
            t = max(by_code[c], key=lambda x: len(x.rows))
            report["rows"][c] = len(t.rows)

    # 表内勾稽：类级之和 vs 合计行
    for code in ("FIN_02_income", "FIN_03_expenditure"):
        if code not in by_code:
            continue
        t = max(by_code[code], key=lambda x: len(x.rows))
        total = None
        for r in t.rows:
            if not r:
                continue
            label = " ".join(str(x or "") for x in r)
            if (str(r[0] or "").strip() == "合计") or (len(r) > 3 and "合计" in str(r[3] or "")):
                total = num(r[4] if len(r) > 4 else None)
                break
        cls_sum, det_sum, n = class_rows(t.rows)
        if total is not None:
            diff = cls_sum - total
            report["audit"].append(
                f"{code} 合计={total} 类级和={cls_sum} 差={diff} 类级行={n} "
                f"{'OK' if abs(diff) <= Decimal('0.5') else 'MISMATCH'}"
            )
    return report


def main(paths: list[str]) -> None:
    pdfs: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            pdfs.extend(sorted(pp.rglob("*.pdf")))
        elif pp.suffix.lower() == ".pdf":
            pdfs.append(pp)
    print(f"共 {len(pdfs)} 个 PDF\n")

    summaries = []
    for pdf in pdfs:
        try:
            r = evaluate(pdf)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {pdf.name}: {type(e).__name__}: {e}")
            summaries.append((pdf.name, -1, [], str(e)))
            continue
        miss = len(r["missing"])
        print(f"[{'OK ' if miss == 0 else 'MISS'}] {pdf.name}  pages={r['pages']} "
              f"scanned={len(r['scanned_pages'])} tables={r['tables']} 缺表={r['missing'] or '无'}")
        for a in r["audit"]:
            print(f"        {a}")
        summaries.append((pdf.name, miss, r["missing"], ""))

    print("\n" + "=" * 70)
    total = len([s for s in summaries if s[1] >= 0])
    perfect = len([s for s in summaries if s[1] == 0])
    print(f"解析成功 {total}/{len(summaries)}；九表齐全 {perfect}/{total}")
    for name, miss, missing, err in summaries:
        if err or miss:
            print(f"  {name}: 缺 {missing} {err}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    main(args)
