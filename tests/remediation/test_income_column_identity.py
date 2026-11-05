"""收入决算表单列不得冒充本年收入合计（F-04 反例）。

真实来源（2026-11-04 真实材料诊断，第 1 份）：《收入决算表》只印一列
“财政拨款收入”，没有“本年收入合计”列。该列只能形成 ``fiscal_allocation``，
不得被重命名为 ``total_actual``——否则“收入总额”的勾稽会拿到错误操作数，
把材料本身缺列的问题伪装成系统解析完成。
"""

from src.engine.fact_context import build_fact_context_from_artifact
from src.schemas.parse_artifact import (
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.services.pdf_parser import PDFParser


def _income_context(header: str, rows):
    parser = PDFParser(None)
    cells = []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            raw = str(value or "")
            cells.append(
                ParsedCell(
                    r, c, raw, raw, parser._parse_numeric(raw), 1,
                    (c * 70, r * 25 + 50, (c + 1) * 70, r * 25 + 75),
                    r < 1, .99, "万元", "lines",
                    native_blank_verified=not raw,
                )
            )
    table = ParsedTable(
        "FIN_02_income", "收入决算表", 1, 0,
        (0, 50, len(rows[0]) * 70, len(rows) * 25 + 50), 1, .99, "lines", "万元",
        rows, cells, "fragment",
    )
    instance = LogicalTableInstance(
        "instance", "FIN_02_income", table.title, ["fragment"], 1, 1,
        org_name="甲局", fiscal_year=2024,
    )
    artifact = ParseArtifact(
        source_file_hash="a" * 64, parse_run_uid="run",
        table_fragments=[table], physical_fragments=[table], logical_tables=[instance],
    )
    return build_fact_context_from_artifact(
        artifact, org_name="甲局", fiscal_year=2024, report_kind="final", doc_type="dept_final"
    )


def test_single_fiscal_allocation_column_never_becomes_total_income():
    context = _income_context(
        "财政拨款收入",
        [
            ["科目编码", "科目名称", "财政拨款收入"],
            ["201", "一般公共服务支出", "100.00"],
            ["20101", "行政运行", "60.00"],
        ],
    )
    indicators = {fact.indicator for fact in context.facts}
    assert indicators == {"fiscal_allocation"}
    assert "total_actual" not in indicators


def test_total_income_only_appears_when_the_column_is_explicitly_printed():
    context = _income_context(
        "本年收入合计",
        [
            ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
            ["201", "一般公共服务支出", "160.00", "100.00"],
            ["20101", "行政运行", "60.00", "60.00"],
        ],
    )
    by_indicator = {}
    for fact in context.facts:
        by_indicator.setdefault(fact.indicator, set()).add(str(fact.normalized_value))
    assert by_indicator["total_actual"] == {"160.00", "60.00"}
    assert by_indicator["fiscal_allocation"] == {"100.00", "60.00"}
