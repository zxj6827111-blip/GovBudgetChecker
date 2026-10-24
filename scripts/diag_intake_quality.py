"""识别准确率度量：金额级召回 + 缺口分类，面向"入库是否可信"。

不去逐份看规则结果，只看三件事：
1) 页面上真实存在的金额词，有多少被解析进了单元格（召回率）；
2) 没进单元格的金额，属于哪类缺口（可补抽 / 真空白 / 版面外）；
3) 一个缺口会影响多少张表、让多少条事实变得不可用。

用法: .venv/bin/python scripts/diag_intake_quality.py <dir> [...]
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.pdf_parser import PDFParser  # noqa: E402
from src.services.table_reading_assessment import assess_table_reading, number  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs" / "intake_quality"


def evaluate(pdf: Path) -> dict:
    art = PDFParser(None).build_parse_artifact(str(pdf))
    reading = assess_table_reading(art)

    # 页面金额词总数（分母）
    total_tokens = 0
    for probe in (art.reading_assessment or {}).get("pages", []):
        total_tokens += len(probe.get("amounts") or [])

    gaps = reading.get("gaps") or []
    by_reason = Counter(g.get("reason") for g in gaps)

    # 受影响片段 → 受影响逻辑表
    blocked_frags = {uid for g in gaps for uid in (g.get("affected_fragment_uids") or [])}
    blocked_tables = sorted({
        inst.table_code
        for inst in art.logical_tables
        if blocked_frags.intersection(inst.fragment_uids)
    })

    # 已入库金额数（分子近似：可信单元格里的金额）
    stored = sum(
        1 for t in art.table_fragments for c in t.cells
        if c.bbox and number(c.raw_text) is not None
    )
    return {
        "file": pdf.name,
        "year": pdf.parent.name,
        "pages": art.page_count,
        "tables": len([t for t in art.table_fragments if t.table_code.startswith("FIN_")]),
        "amount_tokens": total_tokens,
        "stored_amounts": stored,
        "gaps": len(gaps),
        "gap_reasons": dict(by_reason),
        "blocked_tables": blocked_tables,
        "status": reading.get("status"),
    }


def main(paths: list[str]) -> None:
    pdfs: list[Path] = []
    for p in paths:
        pp = Path(os.path.expanduser(p))
        pdfs.extend(sorted(pp.rglob("*.pdf")) if pp.is_dir() else [pp])

    records = []
    for pdf in pdfs:
        try:
            records.append(evaluate(pdf))
        except Exception as e:  # noqa: BLE001
            records.append({"file": pdf.name, "error": f"{type(e).__name__}: {e}"})
        print(f"  {pdf.name[:30]:<32} gaps={records[-1].get('gaps')} "
              f"{records[-1].get('gap_reasons') or ''}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "intake_quality_54.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1))

    ok = [r for r in records if "error" not in r]
    clean = [r for r in ok if r["gaps"] == 0]
    print(f"\n{'='*70}")
    print(f"样本 {len(ok)} 份｜零缺口 {len(clean)} 份（{len(clean)/max(len(ok),1):.1%}）"
          f"｜有缺口 {len(ok)-len(clean)} 份")
    print(f"金额词合计 {sum(r['amount_tokens'] for r in ok)}，已入库 {sum(r['stored_amounts'] for r in ok)}")
    all_reasons = Counter()
    for r in ok:
        for k, v in (r["gap_reasons"] or {}).items():
            all_reasons[k] += v
    print(f"缺口类型：{dict(all_reasons)}")
    print(f"受影响表次：{sum(len(r['blocked_tables']) for r in ok)}")
    print("\n有缺口样本：")
    for r in ok:
        if r["gaps"]:
            print(f"  {r['file'][:30]:<32} gaps={r['gaps']:>2} 表={','.join(r['blocked_tables'])[:40]} "
                  f"{r['gap_reasons']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    main(args)
