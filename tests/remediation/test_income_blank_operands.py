"""C5（2026-11-14 复验）：收入说明核对接纳原件认证空白格作操作数。

真实阻断：文旅材料（ed97003c…）FIN_02_income 合计行的"事业收入/经营收入"
格经网格认证为原件真空白（物理第7页，表头完整、行总计与财政拨款可核），
第18页说明明确两项为 0——V33-226 的取数点（select 未开 allow_native_blank）
把"系统可用操作数缺失"当成"原文未披露"，OBL-TXT-T2 阻断。

修复口径（与 2026-11-08 用户确认的空白政策一致）：
- 可确认原格真空白、行列/单位/主体年度均可绑定时，生成带来源的空白观测
  操作数参与核对（blank_term 全部门槛保持）；
- 横线"—"/"无"/扫描不清/缺失格不适用，仍阻断；
- 说明写非 0 而表格空白：保留阻断，不用正文反填表格。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# 本模块显式启用隔离空白约定（与 test_native_blank_cells 同口径）。
pytestmark = pytest.mark.usefixtures("approved_test_blank_convention")

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33226_Narrative2_T2
from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable

ORG = "上海市普陀区文旅发展管理中心"
YEAR = 2025
NARRATIVE_PAGE = (
    "一、收入决算情况说明\n"
    "本年收入合计6301.94万元，其中：财政拨款收入6301.94万元，"
    "事业收入{business}万元，经营收入{operational}万元。"
)


def document(*, business_cell="", operational_cell="", business_text="0.00", operational_text="0.00"):
    """文旅同布局：合计行的事业收入/经营收入格为认证空白（原生网格）。"""
    rows = [
        ["功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入",
         "上级补助收入", "事业收入", "经营收入", "附属单位上缴收入", "其他收入"],
        ["合计", "", "6301.94", "6301.94", "", business_cell, operational_cell, "", ""],
        ["207", "文化旅游体育与传媒支出", "6301.94", "6301.94", "", "", "", "", ""],
        ["20701", "文化和旅游", "6301.94", "6301.94", "", "", "", "", ""],
        ["2070101", "行政运行", "6301.94", "6301.94", "", "", "", "", ""],
    ]
    cells = [
        ParsedCell(r, c, value, value,
                   Decimal(value) if (r and c >= 2 and value and value not in ("—",)) else None,
                   1, (c * 70, r * 25, (c + 1) * 70, (r + 1) * 25), r == 0,
                   .99, "万元", "lines",
                   native_blank_verified=(value == "" and (r, c) != (0, 1)))
        for r, row in enumerate(rows) for c, value in enumerate(row)
    ]
    code = "FIN_02_income"
    table = ParsedTable(code, "收入决算表", 1, 0, (0, 0, 630, 150), 1, .99,
                        "lines", "万元", rows, cells, "fragment")
    instance = LogicalTableInstance("instance", code, table.title, ["fragment"], 1, 1,
                                    org_name=ORG, fiscal_year=YEAR)
    artifact = ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run",
                             table_fragments=[table], physical_fragments=[table],
                             logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact, org_name=ORG, fiscal_year=YEAR,
                                               report_kind="final", doc_type="unit_final")
    text = NARRATIVE_PAGE.format(business=business_text, operational=operational_text)
    doc = build_document("wenlv.pdf", [text], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    doc.fact_receipts = []
    return doc, rows


def _rule():
    return R33226_Narrative2_T2()


def test_certified_blank_cells_join_text_check_as_zero():
    """认证真空白 + 说明写 0：空白观测操作数参与核对，义务不再阻断。"""
    doc, rows = document()
    issues = _rule().apply(doc)
    # 合计行的空白格必须被空白观测接纳（不是"操作数缺失"）。
    assert not any("business_income/决算操作数缺失" in str(r.get("incomplete_reason") or "")
                   for r in doc.fact_receipts)
    assert not any("operational_income/决算操作数缺失" in str(r.get("incomplete_reason") or "")
                   for r in doc.fact_receipts)
    # 空白格观测带完整原件来源。
    observations = [o for r in doc.fact_receipts
                    for o in (r.get("cell_observations") or [])]
    blank_sources = [o for o in observations
                     if o.get("indicator") in ("business_income", "operational_income")]
    assert blank_sources, "空白观测必须随回执携带"
    for source in blank_sources:
        assert source.get("source_cell_id") and source.get("bbox") and source.get("physical_page")
        assert source.get("presence") == "blank"


def test_blank_observation_not_generated_for_dash_cells():
    """横线"—"格不是真空白：空白观测不生成，取数不足阻断保留。"""
    doc, rows = document(business_cell="—", operational_cell="—")
    with pytest.raises(RuleDeferred):
        _rule().apply(doc)
    reasons = "；".join(str(r.get("incomplete_reason") or "") for r in doc.fact_receipts)
    assert "business_income" in reasons or "operational_income" in reasons


def test_uncertified_blank_stays_blocked():
    """网格未认证（扫描不清）的空白格不能冒充真空白。"""
    doc, rows = document()
    table = doc.parse_artifact.table_fragments[0]
    for cell in table.cells:
        if cell.row_idx == 1 and cell.col_idx in (5, 6):
            cell.native_blank_verified = False
    with pytest.raises(RuleDeferred):
        _rule().apply(doc)


def test_nonzero_text_against_blank_cell_fails_with_real_difference():
    """说明写非 0 而表格认证空白：不是取数不足，而是真实金额差异 fail——
    空白按已确认政策计 0，差异照常报告并携带空白观测来源。"""
    doc, rows = document(business_text="50.00")
    issues = _rule().apply(doc)
    receipt = next(r for r in doc.fact_receipts
                   if r.get("sub_check_id") == "income:text:business_income:45")
    assert receipt["status"] == "fail"
    issue = next(i for i in issues if "事业收入表文不一致" in i.message)
    assert "50.00万元" in issue.message and "0.00万元" in issue.message
    assert "原件空白格按0参与计算" in issue.message


def test_blank_operand_admission_applies_across_columns():
    """跨科目正例：其他收入等列的认证空白同样接纳（布局级修复，非科目特例）。"""
    doc, rows = document()
    # 把"其他收入"格也置空并让说明写 0——同一布局的其他列同样成立。
    table = doc.parse_artifact.table_fragments[0]
    cell = next(c for c in table.cells if c.row_idx == 1 and c.col_idx == 8)
    cell.raw_text = ""
    cell.numeric_value = None
    cell.native_blank_verified = True
    issues = _rule().apply(doc)
    assert not any("other_income/决算操作数缺失" in str(r.get("incomplete_reason") or "")
                   for r in doc.fact_receipts)
