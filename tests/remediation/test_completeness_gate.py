"""完整性闸门：映射不完整的表要显式报出，说明文字和附表不能淹没真正的漏格；
整列算术对不上的映射不能当原件错误。
"""
from decimal import Decimal

from src.schemas.parse_artifact import FiscalFact, LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.services.fact_column_arithmetic import column_arithmetic_conflicts, constrain_column_arithmetic
from src.services.pdf_parser import PDFParser
from src.services.table_import_receipt import build_table_import_receipt, unclassified_table_scope
from tests.remediation.test_budget_2022_table_semantics import grid

FIN_05 = "FIN_05_general_public_expenditure"


def fact(row, indicator, value, *, uid="i5", code=FIN_05, status="unverified", **extra):
    return FiscalFact(fact_uid=f"{uid}:{row}:{indicator}", indicator=indicator, raw_value=value, unit="万元",
                      normalized_value=Decimal(value.replace(",", "")), org_name="某局", fiscal_year=2024,
                      period="本年", budget_stage="决算", subject_code=f"2010{row}", subject_name="行政运行",
                      table_code=code, instance_uid=uid, row_order=row, recognition_status=status,
                      confidence=.99, extra_dims=extra)


def ledger(rows):
    """rows: [(合计, 基本, 项目)]，返回同一实例的事实。"""
    return [fact(row, name, value) for row, values in enumerate(rows)
            for name, value in zip(("total_actual", "basic_actual", "project_actual"), values, strict=False)]


def test_code_column_bound_as_total_is_a_mapping_conflict_not_a_document_error():
    # 修复前 SH2022-16：「项」编码 08/99/01 被当成合计金额，整列对不上。
    facts = constrain_column_arithmetic(ledger([("08", "120.00", "30.00"), ("99", "40.00", "0.00"),
                                                ("01", "15.50", "4.50"), ("30.00", "20.00", "10.00")]))
    flagged = {f.indicator for f in facts if f.extra_dims.get("column_identity_status") == "arithmetic_conflict"}
    assert flagged == {"total_actual", "basic_actual", "project_actual"}
    assert all(f.recognition_status == "ambiguous" for f in facts)
    conflict = facts[0].extra_dims["column_arithmetic_conflicts"][0]
    assert conflict["checked_rows"] == 4 and conflict["failed_rows"] == 3


def test_one_data_error_carried_up_the_hierarchy_stays_a_document_finding():
    # 项级合计录错 10.00，款、类、合计逐级带上去：每行差额相同，是原件差错，交给规则报出。
    facts = ledger([("160.00", "120.00", "30.00"), ("160.00", "120.00", "30.00"),
                    ("160.00", "120.00", "30.00"), ("55.00", "40.00", "5.00"), ("20.00", "15.50", "4.50")])
    assert column_arithmetic_conflicts(facts) == {}


def test_single_wrong_row_stays_a_document_finding():
    facts = ledger([("150.00", "120.00", "30.00"), ("45.00", "40.00", "0.00"),
                    ("20.00", "15.50", "4.50"), ("30.00", "20.00", "10.00")])
    assert column_arithmetic_conflicts(facts) == {}
    assert constrain_column_arithmetic(facts) == facts


def text_table(code, title, rows, method="text", uid="u", header_rows=1):
    table = grid(code, title, rows, {}, header_rows, uid=uid)
    table.extraction_method = method
    return table


def test_unclassified_tables_are_split_into_prose_auxiliary_and_unidentified():
    prose = text_table("UNKNOWN_P3_T0", "2022年部门预算编制说明",
                       [["2", "022年，本部门支出预算8,599万元，其中"], ["", "基本支出2,100.00万元。"]])
    roster = text_table("UNKNOWN_P4_T0", "纳入编制范围的单位", [["序号", "单位名称"], ["1", "某局（本级）"], ["2", "某中心"]],
                        method="lines")
    breakdown = text_table("UNKNOWN_P15_T0", "2022年部门支出预算分单位明细表",
                           [["序号", "预算单位", "合计", "基本支出", "项目支出"],
                            ["1", "某局", "23,635,984", "13,579,830", "10,056,154"]], method="lines")
    footer = text_table("UNKNOWN_P16_T0", "合计 142,183.70 126,627.78 15,555.93",
                        [["合计", "142,183.70", "126,627.78", "15,555.93"], ["注：本表反映部门本年度支出。", "", "", ""]],
                        method="lines", header_rows=0)
    # 没接上的基本支出续页：金额格都空，只剩经济分类编码和名称，仍是表格，不能当正文数字放过。
    tail = text_table("UNKNOWN_P2_T0", "", [["", "", "", "31099", "其他资本性支出", ""],
                                            ["人员经费合计", "", "", "另一项合计", "", ""]], method="lines", header_rows=0)
    assert [unclassified_table_scope(t) for t in (prose, roster, breakdown, footer, tail)] == [
        "narrative_numbers", "narrative_numbers", "auxiliary_unit_breakdown", "unidentified_table",
        "unidentified_table"]


def artifact_with(*tables):
    artifact = ParseArtifact(source_file_hash="a" * 64)
    artifact.table_fragments = list(tables)
    artifact.physical_fragments = list(tables)
    artifact.logical_tables = [LogicalTableInstance(t.fragment_uid, t.table_code, t.title, [t.fragment_uid],
                                                    t.page_number, t.page_number) for t in tables]
    return artifact


def test_receipt_alarms_only_on_tables_that_lost_amounts():
    prose = text_table("UNKNOWN_P3_T0", "2022年部门预算编制说明",
                       [["2", "022年，本部门支出预算8,599万元，其中"], ["", "基本支出 2,100.00 万元。"]], uid="prose")
    breakdown = text_table("UNKNOWN_P15_T0", "2022年部门支出预算分单位明细表",
                           [["序号", "预算单位", "合计", "基本支出", "项目支出"],
                            ["1", "某局", "23,635,984", "13,579,830", "10,056,154"]], method="lines", uid="aux")
    footer = text_table("UNKNOWN_P16_T0", "合计", [["合计", "142,183.70", "126,627.78", "15,555.93"]],
                        method="lines", uid="footer", header_rows=0)
    receipt = build_table_import_receipt(artifact_with(prose, breakdown, footer), [], org_name="某局")
    assert [t["fragment_uid"] for t in receipt["mapping_incomplete_tables"]] == ["footer"]
    assert receipt["fiscal_coverage"]["out_of_scope_amount_cell_count"] == 3
    assert {c["raw_text"] for c in receipt["reading_quality"]["unbound_money_cells"]} == {
        "142,183.70", "126,627.78", "15,555.93"}
    by_uid = {t["fragment_uid"]: t for t in receipt["tables"]}
    assert by_uid["prose"]["mapping_status"] == "not_tabular"
    assert {c["basis"] for c in by_uid["prose"]["excluded_numeric_cells"]} == {"no_tabular_amount_structure"}
    assert by_uid["aux"]["mapping_status"] == "out_of_scope"


def test_receipt_reports_arithmetic_conflicts_and_identity_gaps():
    rows = [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
            ["20101", "行政运行", "08", "120.00", "30.00"],
            ["20102", "一般行政管理事务", "99", "40.00", "0.00"],
            ["20103", "机关服务", "01", "15.50", "4.50"]]
    table = grid(FIN_05, "一般公共预算财政拨款支出决算表", rows, {}, 1, uid="f5")
    artifact = artifact_with(table)
    from src.engine.fact_context import build_fact_context_from_artifact
    context = build_fact_context_from_artifact(artifact, org_name="某局", fiscal_year=2024, report_kind="final")
    receipt = build_table_import_receipt(artifact, context.facts, org_name="某局")
    [incomplete] = receipt["mapping_incomplete_tables"]
    assert incomplete["reasons"] == {"column_arithmetic_conflict": 9}
    assert incomplete["arithmetic_conflicts"][0]["failed_rows"] == 3
    assert receipt["reading_quality"]["fact_identity_gaps"] == {"organization_missing": 0, "subject_name_is_code": 0}


def test_total_row_followed_by_a_note_joins_the_previous_page():
    # 司法局 2023：续页只剩「合计」行和横跨全表的「注：」行。
    parser = PDFParser(None)
    rows = [["科目编码", "科目名称", "合计", "基本支出", "项目支出"], ["201", "一般公共服务支出", "100", "80", "20"]]
    previous = grid(FIN_05, "一般公共预算财政拨款支出决算表", rows, {}, 1, page=15, uid="p15")
    footer = grid("UNKNOWN_P16_T0", "合计 100 80 20", [["合计", "100", "80", "20"], ["注：本表反映本年度支出。", "", "", ""]],
                  {}, 0, page=16, uid="p16")
    footer.cells = [ParsedCell(0, 0, "合计", "合计", None, 16, (0, 0, 20, 10), False, .99, "元", "lines")] + [
        ParsedCell(0, col, value, value, float(value), 16, ((col + 1) * 10, 0, (col + 2) * 10, 10), False, .99, "元", "lines")
        for col, value in ((1, "100"), (2, "80"), (3, "20"))] + [
        ParsedCell(1, 0, "注：本表反映本年度支出。", "注：本表反映本年度支出。", None, 16, (0, 10, 50, 20),
                   False, .99, "元", "lines")]
    footer.bbox = (0, 0, 50, 20)
    aligned = parser._align_native_total_row(previous, footer)
    assert aligned.rows == [["合计", None, "100", "80", "20"], ["注：本表反映本年度支出。", None, None, None, None]]
    assert parser._is_continuation_table(previous, aligned)


def test_merged_code_header_covers_the_second_code_column_on_continuations():
    # 体育局 2023：「经济分类科目编码」横跨类、款两列，续页上的款编码 99 不是金额。
    from src.services.table_import_receipt import _inherited_dimension_basis

    rows = [["经济分类科目编码", None, "科目名称", "决算数"], ["310", "99", "其他资本性支出", "0.39"]]
    table = grid("FIN_06_basic_expenditure", "一般公共预算财政拨款基本支出决算表", rows, {(0, 0): (2, 1)}, 1)
    code = next(c for c in table.cells if c.raw_text == "99")
    assert _inherited_dimension_basis(table, code, [], True).startswith("inherited_physical_dimension")
    amount = next(c for c in table.cells if c.raw_text == "0.39")
    assert _inherited_dimension_basis(table, amount, [], True) is None
