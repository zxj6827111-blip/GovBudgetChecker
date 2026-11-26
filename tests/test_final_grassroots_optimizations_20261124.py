"""2026-11-24 基层决算报告“先报错纠偏、再借位核数”系列优化回归测试。"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.engine.cell_value_policy import interpret_cell_value
from src.engine.fact_context import (
    FactContext,
    _is_clerical_table_year_typo,
    build_fact_context_from_artifact,
)
from src.engine.native_empty_operands import NativeBlankTerm, blank_term
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.native_summary_checks import check_grant_summary, derive_grant_summary_total_fact
from src.engine.native_table_arithmetic import derive_missing_detail_total_fact
from src.engine.native_table_text import check_fiscal_grant_total
from src.engine.rules_v33 import (
    R33105_FinGrantTotal_vs_Text,
    R33109_EmptyTables_Statement,
    Rule,
    build_document,
)


class _GrantBalanceRule(Rule):
    code, severity, desc = "V33-239", "error", "表四收支平衡"


class _PerfCountRule(Rule):
    code, severity, desc = "V33-245", "error", "绩效自评数量闭合"
from src.schemas.parse_artifact import (
    CellPresence,
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.services.check_subject import resolve_check_subject


def test_dash_and_double_dash_interpret_as_zero_and_empty_table_requires_statement(monkeypatch):
    monkeypatch.delenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", raising=False)
    identity = dict(region="上海市普陀区某校", year=2023, table_code="FIN_02_income", row=2, column=4)
    for dash in ("-", "--", "—", "——", "－"):
        val, presence, basis = interpret_cell_value(dash, native_blank_verified=False, **identity)
        assert val == Decimal("0")
        assert presence == "dash"
        assert basis is not None

    # 整表全填 "-" 但缺少空表说明时，必须按空表报出缺空表说明问题
    page_text = (
        "财政拨款“三公”经费支出决算表\n单位：万元\n"
        "合计 因公出国（境）费 公务用车购置及运行维护费 公务接待费\n"
        "预算数 决算数 预算数 决算数 预算数 决算数 预算数 决算数\n"
        "- - - - - - - -\n"
        "注：本表反映单位本年度“三公”经费支出预决算情况。"
    )
    rows = [
        ["合计", "", "因公出国（境）费", "", "公务用车购置及运行维护费", "", "公务接待费", ""],
        ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
        ["-", "-", "-", "-", "-", "-", "-", "-"],
    ]
    doc = build_document("dash_empty.pdf", ["甲单位2022年度决算", page_text], [[], [rows]], 1000)
    issues = R33109_EmptyTables_Statement().apply(doc)
    assert len(issues) == 1
    assert "空表说明" in issues[0].message


def test_clerical_table_year_typo_overrides_only_in_multi_table_report():
    t1 = LogicalTableInstance(
        instance_uid="u1", table_code="FIN_01_income_expenditure_total",
        title="收入支出决算总表", fragment_uids=["u1"], page_start=5, page_end=5,
        org_name=None, fiscal_year=None, row_count=5, confidence=0.95,
    )
    t5 = LogicalTableInstance(
        instance_uid="u5", table_code="FIN_05_general_public_expenditure",
        title="2022年度一般公共预算财政拨款支出决算表", fragment_uids=["u5"], page_start=11, page_end=11,
        org_name=None, fiscal_year=2022, row_count=5, confidence=0.95,
    )
    t6_typo = LogicalTableInstance(
        instance_uid="u6", table_code="FIN_06_basic_expenditure",
        title="2021年度一般公共预算财政拨款基本支出决算表", fragment_uids=["u6"], page_start=13, page_end=13,
        org_name=None, fiscal_year=2021, row_count=5, confidence=0.95,
    )
    multi_artifact = SimpleNamespace(logical_tables=[t1, t5, t6_typo])
    assert _is_clerical_table_year_typo(multi_artifact, t6_typo, 2022) is True

    # 单表合成负控不触发覆写
    single_artifact = SimpleNamespace(logical_tables=[t6_typo])
    assert _is_clerical_table_year_typo(single_artifact, t6_typo, 2022) is False


def test_archive_slot_mismatch_adopts_cover_subject_for_internal_checks():
    res = resolve_check_subject(
        organization_id="org-enf-team",
        organization_name="上海市普陀区城市管理行政执法局执法大队",
        organization_level="unit",
        cover_name="上海市普陀区城市管理行政执法局",
        cover_conflicted=False,
        subject_level="unit",
        cover_text="上海市普陀区城市管理行政执法局\n2022 年度单位决算",
    )
    assert res["name"] == "上海市普陀区城市管理行政执法局"
    assert res["source"] == "cover"
    assert res["conflict"]["code"] == "archive_organization_mismatch"


def test_missing_summary_total_reports_formal_error_and_derives_total_for_downstream():
    # 表四支出总计格留空（NativeBlankTerm=0），但本年支出=600.00、年末结转=50.00
    f_hash = "b" * 64
    run_uid = "run-miss-tot"
    cells = [
        ParsedCell(0, 0, "收入项目", "收入项目", None, 10, (0, 0, 50, 10), True, 0.99, "万元", "lines"),
        ParsedCell(0, 1, "决算数", "决算数", None, 10, (50, 0, 100, 10), True, 0.99, "万元", "lines"),
        ParsedCell(0, 2, "支出项目", "支出项目", None, 10, (100, 0, 150, 10), True, 0.99, "万元", "lines"),
        ParsedCell(0, 3, "合计", "合计", None, 10, (150, 0, 200, 10), True, 0.99, "万元", "lines"),
        ParsedCell(0, 4, "一般公共预算财政拨款", "一般公共预算财政拨款", None, 10, (200, 0, 250, 10), True, 0.99, "万元", "lines"),
        ParsedCell(0, 5, "政府性基金预算财政拨款", "政府性基金预算财政拨款", None, 10, (250, 0, 300, 10), True, 0.99, "万元", "lines"),
        ParsedCell(0, 6, "国有资本经营预算财政拨款", "国有资本经营预算财政拨款", None, 10, (300, 0, 350, 10), True, 0.99, "万元", "lines"),
        ParsedCell(1, 0, "本年收入合计", "本年收入合计", None, 10, (0, 10, 50, 20), False, 0.99, "万元", "lines"),
        ParsedCell(1, 1, "650.00", "650.00", 650.0, 10, (50, 10, 100, 20), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("650.00")),
        ParsedCell(1, 2, "本年支出合计", "本年支出合计", None, 10, (100, 10, 150, 20), False, 0.99, "万元", "lines"),
        ParsedCell(1, 3, "600.00", "600.00", 600.0, 10, (150, 10, 200, 20), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("600.00")),
        ParsedCell(1, 4, "600.00", "600.00", 600.0, 10, (200, 10, 250, 20), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("600.00")),
        ParsedCell(1, 5, "", "", None, 10, (250, 10, 300, 20), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
        ParsedCell(1, 6, "", "", None, 10, (300, 10, 350, 20), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
        ParsedCell(2, 0, "年初财政拨款结转和结余", "年初财政拨款结转和结余", None, 10, (0, 20, 50, 30), False, 0.99, "万元", "lines"),
        ParsedCell(2, 1, "0.00", "0.00", 0.0, 10, (50, 20, 100, 30), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("0.00")),
        ParsedCell(2, 2, "年末财政拨款结转和结余", "年末财政拨款结转和结余", None, 10, (100, 20, 150, 30), False, 0.99, "万元", "lines"),
        ParsedCell(2, 3, "50.00", "50.00", 50.0, 10, (150, 20, 200, 30), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("50.00")),
        ParsedCell(2, 4, "50.00", "50.00", 50.0, 10, (200, 20, 250, 30), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("50.00")),
        ParsedCell(2, 5, "", "", None, 10, (250, 20, 300, 30), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
        ParsedCell(2, 6, "", "", None, 10, (300, 20, 350, 30), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
        ParsedCell(3, 0, "总计", "总计", None, 10, (0, 30, 50, 40), False, 0.99, "万元", "lines"),
        ParsedCell(3, 1, "650.00", "650.00", 650.0, 10, (50, 30, 100, 40), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("650.00")),
        ParsedCell(3, 2, "总计", "总计", None, 10, (100, 30, 150, 40), False, 0.99, "万元", "lines"),
        ParsedCell(3, 3, "", "", None, 10, (150, 30, 200, 40), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
        ParsedCell(3, 4, "650.00", "650.00", 650.0, 10, (200, 30, 250, 40), False, 0.99, "万元", "lines", normalized_value_wan=Decimal("650.00")),
        ParsedCell(3, 5, "", "", None, 10, (250, 30, 300, 40), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
        ParsedCell(3, 6, "", "", None, 10, (300, 30, 350, 40), False, 0.99, "万元", "lines", presence=CellPresence.BLANK, native_blank_verified=True),
    ]
    for c in cells:
        if c.raw_text:
            c.native_text_sources = [{"text": c.raw_text, "bbox": list(c.bbox)}]
    rows = [[cells[r * 7 + k].raw_text for k in range(7)] for r in range(4)]
    table = ParsedTable(
        table_code="FIN_04_fiscal_grant_total",
        title="财政拨款收入支出决算总表",
        page_number=10,
        table_index=0,
        bbox=(0, 0, 350, 40),
        header_row_count=1,
        confidence=0.98,
        extraction_method="lines",
        unit_hint="万元",
        rows=rows,
        cells=cells,
        fragment_uid="f4",
    )
    inst = LogicalTableInstance(
        instance_uid="f4",
        table_code="FIN_04_fiscal_grant_total",
        title="财政拨款收入支出决算总表",
        fragment_uids=["f4"],
        page_start=10,
        page_end=10,
        org_name="甲校",
        fiscal_year=2022,
        row_count=4,
        confidence=0.98,
        column_mappings=[
            {"source_col_idx": 1, "source_col_name": "决算数", "canonical_measure": "income_actual", "source_label_cols": [0]},
            {"source_col_idx": 3, "source_col_name": "合计", "canonical_measure": "expenditure_actual", "source_label_cols": [2]},
            {"source_col_idx": 4, "source_col_name": "一般公共预算财政拨款", "canonical_measure": "general_public_budget", "source_label_cols": [2]},
            {"source_col_idx": 5, "source_col_name": "政府性基金预算财政拨款", "canonical_measure": "government_fund_budget", "source_label_cols": [2]},
            {"source_col_idx": 6, "source_col_name": "国有资本经营预算财政拨款", "canonical_measure": "state_capital_budget", "source_label_cols": [2]},
        ],
    )
    artifact = ParseArtifact(
        parse_run_uid=run_uid,
        source_file_hash=f_hash,
        page_count=15,
        physical_fragments=[table],
        table_fragments=[table],
        logical_tables=[inst],
    )
    ctx = build_fact_context_from_artifact(
        artifact, org_name="甲校", fiscal_year=2022, report_kind="final", doc_type="dept_final"
    )
    pages = [""] * 15
    pages[0] = "甲校2022年度单位决算"
    pages[9] = "财政拨款收入支出决算总表"
    pages[14] = (
        "第三部分 甲校2022年度决算情况说明\n"
        "四、财政拨款收入支出决算总体情况说明\n"
        "甲校2022年度财政拨款收入支出总决算640.00万元。"
    )
    doc = build_document("miss_tot.pdf", pages, [[] for _ in range(15)], 1000)
    doc.parse_artifact = artifact
    doc.fact_context = ctx

    # 1) 表内勾稽直接报出“支出总计单元格漏填留空”，不转人工
    sum_issues = check_grant_summary(_GrantBalanceRule(), doc)
    assert any("漏填留空" in i.message and "650.00" in i.message for i in sum_issues)

    # 2) 下游说明四同时识别“总决算”用词不规范 + 用实算的 650.00 继续查出正文 640.00 的差额！
    txt_issues = check_fiscal_grant_total(R33105_FinGrantTotal_vs_Text(), doc)
    assert any("总决算" in i.message for i in txt_issues)
    assert any("640.00" in i.message and "650.00" in i.message for i in txt_issues)


def test_performance_missing_ziping_and_year_typo_report_and_continue():
    # 1) 漏写“自评”二字，且存在分项加总错误（总数 2 != 1+0+0+0）：应同时报漏字和数量不平！
    pages_missing_word = [
        "甲幼儿园2022年度单位决算",
        "第三部分 决算情况说明\n十、预算绩效管理情况\n"
        "2022年度开展的绩效跟踪评价项目1个；2022年度开展的绩效评价项目2个，平均得分100分"
        "（其中，绩效评级为“优”的项目1个；绩效评级为“良”的项目0个；"
        "绩效评级为“合格”的项目0个；绩效评级为“不合格”的项目0个）。",
    ]
    doc1 = build_document("perf1.pdf", pages_missing_word, [[], []], 1000)
    doc1.fact_context = FactContext([], "c" * 64, "r1", org_name="甲幼儿园", fiscal_year=2022, report_kind="final")
    issues1 = check_performance_counts(_PerfCountRule(), doc1)
    assert any("漏写“自评”二字" in i.message for i in issues1)
    assert any("相差1个" in i.message for i in issues1)

    # 2) 绩效自评年度笔误（同节前文已明确为2022年度绩效管理，后句误写“绩效自评的2021年度项目”）：报错并继续完成核对
    pages_year_typo = [
        "甲局2022年度单位决算",
        "第三部分 决算情况说明\n十、预算绩效管理情况\n"
        "编报绩效目标的2022年度项目3个；绩效跟踪评价的2022年度项目3个；"
        "绩效自评的2021年度项目3个（其中，绩效评级为“优”的项目3个）。",
    ]
    doc2 = build_document("perf2.pdf", pages_year_typo, [[], []], 1000)
    doc2.fact_context = FactContext([], "d" * 64, "r2", org_name="甲局", fiscal_year=2022, report_kind="final")
    issues2 = check_performance_counts(_PerfCountRule(), doc2)
    assert len(issues2) == 1
    assert "年度笔误" in issues2[0].message and "2021年度" in issues2[0].message
