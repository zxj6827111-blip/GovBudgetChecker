"""对单张表复现事实物化，输出 subject_code 分布，定位"取数不足"。

用法: .venv/bin/python scripts/diag_facts.py <pdf> <table_code>
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.fiscal_column_identity import canonical_column_mappings  # noqa: E402
from src.services.fiscal_fact_materializer import FiscalFactMaterializer  # noqa: E402
from src.services.fiscal_table_rules import NINE_TABLE_RULES  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402


def main(pdf_path: str, table_code: str) -> None:
    art = PDFParser(None).build_parse_artifact(pdf_path)
    tables = [t for t in art.table_fragments if t.table_code == table_code]
    if not tables:
        print(f"未找到 {table_code}")
        return
    table = max(tables, key=lambda t: len(t.rows))
    rule = NINE_TABLE_RULES[table_code]
    mappings = canonical_column_mappings(table, rule)
    print(f"表 {table_code} rows={len(table.rows)} hdr={table.header_row_count}")
    print("列映射：")
    for m in mappings:
        print(f"   col={m['source_col_idx']:<2} measure={m['canonical_measure']:<22} "
              f"labels={m['source_label_cols']} header={m['source_col_name'][:40]!r}")

    mat = FiscalFactMaterializer(None)
    facts = mat.build_fiscal_facts_for_table(table, mappings, org_name="测试", fiscal_year=2025)
    print(f"\n事实数: {len(facts)}")
    cnt = Counter(f.subject_code for f in facts)
    print("subject_code 分布（前 25）：")
    for code, n in cnt.most_common(25):
        flag = "  <== 重复" if n > 1 else ""
        print(f"   {str(code):<12} x{n}{flag}")

    print("\n301/302 相关事实：")
    for f in facts:
        if str(f.subject_code or "") in {"301", "302", "303", "310"} or str(f.subject_code or "").startswith("301"):
            print(f"   row={f.row_order:<3} code={str(f.subject_code):<8} name={str(f.subject_name)[:18]:<20} "
                  f"val={f.normalized_value} col={f.extra_dims.get('source_label_cols')}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(args[0], args[1])
