"""空白格盘点：区分"整列空白（表意=无此项）"与"抽取空洞（解析失败）"。

判定口径：
- 整列空白：该 measure 列在全部数据行均为空 → 财政表语义为"无此项"，应按 0/不适用处理
- 抽取空洞：同列既有值又有空，且空行在原文中存在对应行 → 可能是解析丢格
"""
from __future__ import annotations

import glob
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.fiscal_column_identity import canonical_column_mappings  # noqa: E402
from src.services.fiscal_table_rules import NINE_TABLE_RULES  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402

CORE = ["FIN_01_income_expenditure_total", "FIN_02_income", "FIN_03_expenditure",
        "FIN_04_fiscal_grant_total", "FIN_05_general_public_expenditure",
        "FIN_06_basic_expenditure", "FIN_07_three_public", "FIN_08_gov_fund",
        "FIN_09_state_capital"]


def main() -> None:
    files = sorted(glob.glob("uploads/*/*.pdf"))
    total_cols = 0
    blank_cols = 0
    per_file = []
    detail = Counter()

    for f in files:
        art = PDFParser(None).build_parse_artifact(f)
        n_col = n_blank = 0
        for t in art.table_fragments:
            if t.table_code not in CORE:
                continue
            rule = NINE_TABLE_RULES[t.table_code]
            try:
                maps = canonical_column_mappings(t, rule)
            except Exception:  # noqa: BLE001
                continue
            hdr = min(t.header_row_count or 1, len(t.rows))
            data_rows = t.rows[hdr:]
            # 数据行：第 0 列非空且不是纯文本标题
            data_rows = [r for r in data_rows if any(str(c or "").strip() for c in r)]
            if not data_rows:
                continue
            for m in maps:
                col = m["source_col_idx"]
                vals = [str(r[col] or "").strip() if col < len(r) else "" for r in data_rows]
                filled = sum(1 for v in vals if v)
                n_col += 1
                if filled == 0:
                    n_blank += 1
                    detail[(t.table_code, m["canonical_measure"])] += 1
                elif filled < len(vals) * 0.3:
                    detail[(t.table_code, m["canonical_measure"] + "(多数空)")] += 1
        total_cols += n_col
        blank_cols += n_blank
        per_file.append((Path(f).name, n_col, n_blank))

    print(f"{'文件':<30}{'金额列':>6}{'整列空白':>8}")
    for name, n_col, n_blank in per_file:
        print(f"{name[:28]:<30}{n_col:>6}{n_blank:>8}")
    print("-" * 46)
    print(f"合计金额列 {total_cols}，其中整列空白 {blank_cols} "
          f"（{blank_cols/max(total_cols,1):.1%}）")
    print("\n整列空白高发表（表, 指标, 出现次数）：")
    for (code, measure), n in detail.most_common(15):
        print(f"   {code:<32}{measure:<40}{n}")


if __name__ == "__main__":
    main()
