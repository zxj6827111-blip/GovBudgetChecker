"""D3 零值省略：说明只写基本支出及其100%占比时的项目支出金额闭合。

背景（2026-11-12 民政批次诊断·社会事务管理中心）：T3 合计 209.88、基本支出
209.88，项目支出原件格空白（认证空白=0）；说明第15页只写
"本年支出合计209.88万元，其中：基本支出209.88万元，占100%。"，未单列项目支出。
V33-104/V33-220 原实现要求显式项目支出金额，整条关系转"取数不足"。

新口径：闭合条件（表内项目支出=0/认证空白、表内合计=基本支出、说明合计与
基本支出各自与表一致、说明显式声明基本支出占100%）全部成立时，金额关系
按推导 0 参与比对；任一条件不成立保持原 fail-closed，不把未提金额随意补零。
"""

from copy import deepcopy

import pytest

from src.engine.native_empty_operands import blank_term
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33104_Expense_vs_Text, R33220_Narrative3_T3
from src.schemas.parse_artifact import CellPresence
from src.services.pdf_parser import PDFParser
from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document


def t3_zero_omission_doc(*, total="209.88", basic="209.88", project="", narrative_pct="占 100%",
                         narrative_total="209.88", narrative_basic="209.88"):
    rows = [
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "", total, basic, project],
    ]
    parser, cells = PDFParser(None), []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            raw = str(value or "")
            cells.append(ParsedCell(r, c, raw, raw, parser._parse_numeric(raw), 1,
                                    (c * 70, r * 25 + 50, (c + 1) * 70, r * 25 + 75), r < 1,
                                    .99, "万元", "lines", native_blank_verified=not raw))
    table = ParsedTable("FIN_03_expenditure", "支出决算表", 1, 0,
                        (0, 50, len(rows[0]) * 70, len(rows) * 25 + 50), 1, .99, "lines", "万元",
                        rows, cells, "fragment")
    instance = LogicalTableInstance("instance", "FIN_03_expenditure", table.title, ["fragment"], 1, 1,
                                    org_name="甲中心", fiscal_year=2025)
    artifact = ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run",
                             table_fragments=[table], physical_fragments=[deepcopy(table)],
                             logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact, org_name="甲中心", fiscal_year=2025,
                                               report_kind="final", doc_type="unit_final")
    doc = build_document("native.pdf", [
        "甲中心2025年度单位决算\n支出决算表\n单位：万元\n"
        "三、支出决算情况说明\n"
        f"本年支出合计 {narrative_total} 万元，其中：基本支出 {narrative_basic} 万元，{narrative_pct}。"
    ], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    doc.units_per_page[0] = "万元"
    doc.dominant_unit = "万元"
    return doc


def test_certified_blank_project_is_zero_operand():
    doc = t3_zero_omission_doc()
    term = blank_term(doc, "FIN_03_expenditure", "project_actual", code="total", stage="决算")
    assert term is not None and float(term.normalized_value) == 0.0
    observation = term.extra_dims.get("native_blank_observation") or {}
    assert observation, "认证空白须携带原件观察记录"
    assert term.raw_value == "" and str(term.normalized_value) == "0"


def test_v33_104_closes_project_relation_via_zero_omission():
    doc = t3_zero_omission_doc()
    before = doc.fact_context.to_dict()
    issues = R33104_Expense_vs_Text().apply(doc)
    assert issues == []
    receipt = next(r for r in doc.fact_receipts if r.get("sub_check_id") == "T3:project_actual:text_vs_table")
    assert receipt["status"] == "pass"
    assert "零值省略" in receipt["formula"]
    assert doc.fact_context.to_dict() == before


def test_v33_220_derives_zero_project_and_completes():
    doc = t3_zero_omission_doc()
    issues = R33220_Narrative3_T3().apply(doc)
    assert issues == []


@pytest.mark.parametrize("variant", ["pct_not_100", "project_funded", "narrative_basic_differs",
                                     "table_total_exceeds_basic"])
def test_closure_conditions_missing_stay_fail_closed(variant):
    kwargs = {}
    if variant == "pct_not_100":
        kwargs["narrative_pct"] = "占 97.59%"
    elif variant == "project_funded":
        kwargs = dict(project="3.00", total="212.88")
    elif variant == "narrative_basic_differs":
        kwargs["narrative_basic"] = "200.00"
    elif variant == "table_total_exceeds_basic":
        kwargs = dict(total="212.88")
    doc = t3_zero_omission_doc(**kwargs)
    with pytest.raises(RuleDeferred):
        R33104_Expense_vs_Text().apply(doc)
    with pytest.raises(RuleDeferred):
        R33220_Narrative3_T3().apply(doc)


def test_explicit_project_statement_takes_normal_comparison_path():
    # 正控：正文显式列示项目支出 0 万元时走常规显式比较，不经零值省略闭合。
    doc = t3_zero_omission_doc()
    doc.page_texts[0] += "\n项目支出 0 万元。"
    receipt_before = len(doc.fact_receipts)
    R33104_Expense_vs_Text().apply(doc)
    receipt = next(r for r in doc.fact_receipts[receipt_before:]
                   if r.get("sub_check_id") == "T3:project_actual:text_vs_table")
    assert receipt["status"] == "pass"
    assert "零值省略" not in receipt["formula"]
    R33220_Narrative3_T3().apply(doc)
