"""空白语义验证：构造最小 doc，直接调用 blank_term，确认空白兜底是否生效。

用于回答：表实例唯一性修复后，规则能否把"整列空白"判成"无此项"。

用法: .venv/bin/python scripts/diag_blank_term.py <pdf>
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.fact_context import FactContext  # noqa: E402
from src.engine.native_empty_operands import blank_term  # noqa: E402
from src.services.fiscal_column_identity import canonical_column_mappings  # noqa: E402
from src.services.fiscal_fact_materializer import FiscalFactMaterializer  # noqa: E402
from src.services.fiscal_table_rules import NINE_TABLE_RULES  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402

ORG = "上海市人民政府办公厅"
YEAR = 2025


def main(pdf: str) -> None:
    art = PDFParser(None).build_parse_artifact(pdf)
    art.parse_run_uid = "diag-run"
    mat = FiscalFactMaterializer(None)

    facts = []
    for table in art.table_fragments:
        if table.table_code not in NINE_TABLE_RULES:
            continue
        mappings = canonical_column_mappings(table, NINE_TABLE_RULES[table.table_code])
        inst = next((i for i in art.logical_tables if i.table_code == table.table_code), None)
        facts.extend(mat.build_fiscal_facts_for_table(
            table, mappings, org_name=ORG, fiscal_year=YEAR,
            budget_stage="决算", instance_uid=getattr(inst, "instance_uid", None),
            file_hash=art.source_file_hash,
        ))

    ctx = FactContext(facts=facts, file_hash=art.source_file_hash,
                      parse_run_uid="diag-run", org_name=ORG,
                      fiscal_year=YEAR, report_kind="final")
    doc = SimpleNamespace(fact_context=ctx, parse_artifact=art)

    print(f"事实 {len(facts)} 条；逻辑表 {len(art.logical_tables)} 个")
    print("实例唯一性：")
    for code in ("FIN_03_expenditure", "FIN_04_fiscal_grant_total"):
        n = len([i for i in art.logical_tables if i.table_code == code])
        print(f"  {code:<34} 实例数 {n}")

    print("\nblank_term 探测（FIN_03 支出表的三列空白）：")
    for indicator, code in (("to_superior_actual", "201"),
                            ("to_superior_actual", "210"),
                            ("operational_actual", "201"),
                            ("subsidiary_actual", "208")):
        term = blank_term(doc, "FIN_03_expenditure", indicator, code=code)
        if term is None:
            print(f"  {indicator:<22} 科目{code}  →  None（未认定空白）")
        else:
            print(f"  {indicator:<22} 科目{code}  →  空白项 value={term.normalized_value} "
                  f"page={term.extra_dims['native_blank_observation']['physical_page']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1])
