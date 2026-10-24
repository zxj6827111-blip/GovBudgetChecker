"""规则直跑：不依赖服务与数据库，直接执行单条 V33 规则，观察未决原因。

用途：验证解析层修复是否真正改善了规则取数（而不只是表结构好看）。

用法: .venv/bin/python scripts/diag_rule_direct.py <pdf> [RULE_CODE ...]
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine import rules_v33  # noqa: E402
from src.engine.fact_context import FactContext  # noqa: E402
from src.services.fiscal_column_identity import canonical_column_mappings  # noqa: E402
from src.services.fiscal_fact_materializer import FiscalFactMaterializer  # noqa: E402
from src.services.fiscal_table_rules import NINE_TABLE_RULES  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402

DEFAULT_RULES = ["V33-241", "V33-117", "V33-101"]
RULES = {
    "V33-241": "R33241_Table3_ExpenseAdvancedCheck",
    "V33-117": "R33117_BasicExpenseClassification",
    "V33-101": "R33101_IncomeExpenditureTotal",
}


def build_doc(pdf: str, org: str, year: int):
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
            table, mappings, org_name=org, fiscal_year=year,
            budget_stage="决算", instance_uid=getattr(inst, "instance_uid", None),
            file_hash=art.source_file_hash))
    ctx = FactContext(facts=facts, file_hash=art.source_file_hash, parse_run_uid="diag-run",
                      org_name=org, fiscal_year=year, report_kind="final")
    return SimpleNamespace(fact_context=ctx, parse_artifact=art, org_name=org,
                           fiscal_year=year, report_kind="final",
                           structured_consumption=[], relation_receipts=[], fact_receipts=[],
                           page_texts={}, doc_type="dept_final")


def run(pdf: str, codes: list[str], org: str, year: int, legacy: bool = False) -> None:
    if legacy:
        # 模拟修复前行为：关闭目录页判据，让 page_text_fallback 重新产生幽灵表
        PDFParser._is_contents_or_section_page = lambda self, lines: False
        print("[legacy 模式] 已关闭目录页判据\n")
    doc = build_doc(pdf, org, year)
    print(f"{Path(pdf).name}  事实 {len(doc.fact_context.facts)} 条\n")
    for code in codes:
        cls_name = RULES.get(code)
        if not cls_name:
            print(f"{code}: 未登记类名，跳过")
            continue
        rule = getattr(rules_v33, cls_name)()
        try:
            issues = rule.apply(doc)
        except Exception:  # noqa: BLE001
            print(f"{code}: 抛异常")
            traceback.print_exc(limit=3)
            continue
        # 关系回执里的未决
        receipts = [r for r in getattr(doc, "fact_receipts", [])
                    if str(getattr(r, "rule_id", "")) == code]
        unresolved = [r for r in receipts if getattr(r, "status", "") == "insufficient_data"]
        print(f"{code}: 问题 {len(issues)} 条；回执 {len(receipts)} 条，其中 insufficient_data {len(unresolved)} 条")
        for r in unresolved[:6]:
            print(f"    - {getattr(r, 'relation_id', '')}: {str(getattr(r, 'incomplete_reason', ''))[:90]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(1)
    pdf = args[0]
    codes = [a for a in args[1:] if not a.startswith("--")] or DEFAULT_RULES
    org = "上海市人民政府办公厅"
    year = 2025
    run(pdf, codes, org, year, legacy="--legacy" in sys.argv)
