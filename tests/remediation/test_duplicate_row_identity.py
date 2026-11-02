"""整行排版重复（同一科目编码在原表印两遍）的身份判定。

真值材料：上海市建设用地和土地整理事务中心 2025 年度决算第 8、10 页——「21211
农业土地开发资金安排的支出」这一行各列数字完全相同地印了两遍，导致收入决算表、
支出决算表三条表内勾稽义务同时报「科目 21211 存在重复行身份」而阻塞。

口径：整行文字完全相同的连续重复＝排版重复，按一行参与合计；同名编码但内容不同
仍是身份歧义，必须取数不足转人工（不允许挑一行当答案）。
"""
from __future__ import annotations

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33005_TableTotalConsistency,
    R33241_Table3_ExpenseAdvancedCheck,
)
from src.schemas.parse_artifact import (
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.services.pdf_parser import PDFParser

T3 = "FIN_03_expenditure"
ORG = "上海市某某事务中心"
YEAR = 2025

_HEADER = ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出",
           "上缴上级支出", "经营支出", "对附属单位补助支出"]


def _document(rows):
    cells = []
    parser = PDFParser(None)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            raw = str(value or "")
            cells.append(ParsedCell(r, c, raw, raw, parser._parse_numeric(raw), 1,
                                    (c * 70, r * 25 + 50, (c + 1) * 70, r * 25 + 75), r < 2,
                                    .99, "万元", "lines", native_blank_verified=not raw))
    table = ParsedTable(T3, "支出决算表", 1, 0,
                        (0, 50, len(rows[0]) * 70, len(rows) * 25 + 50), 2, .99,
                        "lines", "万元", rows, cells, "fragment")
    instance = LogicalTableInstance("instance", T3, table.title, ["fragment"], 1, 1,
                                    org_name=ORG, fiscal_year=YEAR)
    artifact = ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run",
                             table_fragments=[table], physical_fragments=[table],
                             logical_tables=[instance])
    context = build_fact_context_from_artifact(
        artifact, org_name=ORG, fiscal_year=YEAR, report_kind="final", doc_type="dept_final")
    doc = build_document("native.pdf", ["某某中心2025年度决算"], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    return doc


def _rows(second_amount="7,044.50"):
    return [
        _HEADER,
        ["功能分类科目编码", "科目名称", None, None, None, None, None, None],
        ["合计", None, "7,044.50", "0.00", "7,044.50", "0.00", "0.00", "0.00"],
        ["212", "城乡社区支出", "7,044.50", "0.00", "7,044.50", "0.00", "0.00", "0.00"],
        ["21211", "农业土地开发资金安排的支出", "7,044.50", "0.00", "7,044.50", "0.00", "0.00", "0.00"],
        ["21211", "农业土地开发资金安排的支出", second_amount, "0.00", second_amount, "0.00", "0.00", "0.00"],
    ]


def test_exact_repeated_row_is_counted_once():
    """整行完全相同的重复行按一行参与合计：勾稽可执行，不再阻塞。"""
    assert R33241_Table3_ExpenseAdvancedCheck().apply(_document(_rows())) == []


def test_exact_repeated_row_is_still_reported_as_a_document_defect():
    """按一行参与合计不等于不报：整行重复本身是原件缺陷，按 low 级提示出来。

    实样：该行本应是项级行 2121100，却印成了款级编码 21211，项级科目整行缺失。
    只在表格级规则（V33-005）报一次，避免同一缺陷在多条规则里重复出现。
    """
    issues = R33005_TableTotalConsistency().apply(_document(_rows()))
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "low"
    assert issue.location["code"] == "21211"
    assert "整行重复印了两遍" in issue.message
    assert "编码是否误植" in issue.message

    # 同一份材料在收入/支出专项规则里不重复报这条缺陷
    assert R33241_Table3_ExpenseAdvancedCheck().apply(_document(_rows())) == []


def test_conflicting_repeated_row_stays_ambiguous():
    """同编码但金额不同的重复行仍是身份歧义 → 取数不足，不得挑一行。"""
    with pytest.raises(RuleDeferred) as excinfo:
        R33241_Table3_ExpenseAdvancedCheck().apply(_document(_rows("6,000.00")))
    assert excinfo.value.status == "insufficient_data"
    # 事实层看到的是同一编码两个不同金额 → 操作数不唯一；表视图看到的是重复行身份。
    detail = str(excinfo.value)
    assert "21211" in detail
    assert "不唯一" in detail or "重复行身份" in detail
