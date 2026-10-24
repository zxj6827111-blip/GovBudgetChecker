"""普陀真实材料暴露的代码缺口及反例；不把原件缺数补成金额。"""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from test_native_blank_cells import grid_document
from src.services.document_profile_resolver import detect_cover_facts, resolve_document_profile
from src.services.fiscal_column_identity import mixed_expenditure_header_cells
from src.services.pdf_parser import PDFParser
from src.engine.fact_context import build_fact_context_from_artifact, FactContext
from src.engine.fact_table_projection import fact_bound_table_rows
from src.engine.native_no_activity import require_no_activity_disposition
from src.engine.check_obligations import build_obligation_ledger
from src.engine.pipeline import build_document, run_rules_with_outcomes
from src.engine.rule_outcome import RuleDeferred, RuleNotApplicable


@pytest.mark.parametrize(
    "lines,expected",
    [
        ("上海市普陀区小铃铛\n幼儿园", "上海市普陀区小铃铛幼儿园"),
        ("上海市普陀区红十字会", "上海市普陀区红十字会"),
        ("上海市普陀区市场监督管\n理局", "上海市普陀区市场监督管理局"),
        ("上海市普陀区人民政府\n万里街道办事处", "上海市普陀区人民政府万里街道办事处"),
        ("上海市普陀区人力资源和\n社会保障局", "上海市普陀区人力资源和社会保障局"),
        ("上海市普陀区真如镇街道\n城市运行管理中心", "上海市普陀区真如镇街道城市运行管理中心"),
        (
            "中国人民政治协商会议上\n海市普陀区委员会办公室",
            "中国人民政治协商会议上海市普陀区委员会办公室",
        ),
    ],
)
def test_publisher_joins_only_literal_adjacent_cover_lines(lines, expected):
    assert detect_cover_facts([lines + "\n2025年度决算"])["cover_org_name"] == expected


@pytest.mark.parametrize(
    "cover",
    [
        "公开材料\n2025年度决算",
        "2025年度决算\n上海市普陀区红十字会",
        "目录\n上海市财政局\n某实验学校\n2025年度决算",
    ],
)
def test_unknown_publisher_is_not_invented_from_filename_or_body(cover):
    profile = resolve_document_profile(
        filename="上海市普陀区红十字会2025年度决算.pdf", page_texts=[cover]
    )
    assert profile.organization_name.value is None


def mixed_grid():
    rows = [
        ["项目", None, "年初结转和结余", "本年收入", "本年支出", None, None, "年末结转和结余"],
        ["功能分类科目编码", "科目名称", None, None, None, None, None, None],
        ["合计", None, "", "207.00", "合计", "基本支出", "项目支出", ""],
        ["223", "国有资本经营预算支出", "", "207.00", "207.00", "", "207.00", ""],
    ]
    doc = grid_document(rows, code="FIN_09_state_capital")
    table = doc.parse_artifact.table_fragments[0]
    for cell in table.cells:
        if cell.row_idx == 0 and cell.col_idx == 4:
            cell.bbox = (280, 50, 490, 75)
        if cell.row_idx == 0 and cell.col_idx in (5, 6):
            cell.bbox = None
    return doc, table


def test_mixed_row_preserves_income_amount_and_marks_only_three_leaf_headers():
    doc, table = mixed_grid()
    positions = mixed_expenditure_header_cells(table)
    assert positions == {(2, 4), (2, 5), (2, 6)}
    for cell in table.cells:
        if (cell.row_idx, cell.col_idx) in positions:
            cell.is_header = True
    doc.fact_context = build_fact_context_from_artifact(
        doc.parse_artifact,
        org_name="甲局",
        fiscal_year=2024,
        report_kind="final",
        doc_type="dept_final",
    )
    income = [
        f for f in doc.fact_context.facts if f.row_order == 2 and f.indicator == "income_actual"
    ]
    assert len(income) == 1 and income[0].normalized_value == Decimal("207.00")
    rows = fact_bound_table_rows(doc, "国有资本经营预算财政拨款收入支出决算表")
    assert Decimal(rows[2][3]) == Decimal("207.00") and rows[2][4:] == [
        "合计",
        "基本支出",
        "项目支出",
        "",
    ]


def empty_three_public():
    rows = [
        ["财政拨款“三公”经费"] + [None] * 11,
        [
            "合计",
            None,
            "因公出国（境）费",
            None,
            "公务用车购置及运行维护费",
            None,
            None,
            None,
            None,
            None,
            "公务接待费",
            None,
        ],
        [
            None,
            None,
            None,
            None,
            "小计",
            None,
            "公务用车购置费",
            None,
            "公务用车运行维护费",
            None,
            None,
            None,
        ],
        ["预算数", "决算数"] * 6,
        [""] * 12,
    ]
    doc = grid_document(
        rows,
        code="FIN_07_three_public",
        headers=4,
        text="甲局2024年度部门决算。说明：甲局本年度没有财政拨款“三公”经费支出，故本表无数据。",
    )
    doc.fact_context = FactContext(
        [], "a" * 64, "run", org_name="甲局", fiscal_year=2024, report_kind="final"
    )
    return doc


def test_explicit_empty_table_records_sources_without_creating_zero_facts():
    doc = empty_three_public()
    before = doc.fact_context.to_dict()
    with pytest.raises(RuleNotApplicable):
        require_no_activity_disposition(SimpleNamespace(code="test"), doc)
    assert doc.fact_context.to_dict() == before and not doc.fact_context.facts
    rec = doc.fact_receipts[0]
    assert rec["status"] == "not_applicable" and len(rec["cell_observations"]) == 12
    assert rec["disclosure_sources"][0]["span"] in doc.page_texts[0]
    assert rec["operand_fact_ids"] == []


@pytest.mark.parametrize(
    "obstacle",
    ["no_note", "foreign_owner", "stale_version", "uncertified", "nonzero_cell", "negated"],
)
def test_empty_disposition_requires_all_original_source_conditions(obstacle):
    doc = empty_three_public()
    if obstacle == "no_note":
        doc.page_texts = ["甲局2024年度部门决算"]
    elif obstacle == "foreign_owner":
        doc.page_texts[0] = doc.page_texts[0].replace("说明：甲局", "说明：上海市乙财政局")
    elif obstacle == "stale_version":
        doc.parse_artifact.parse_run_uid = "old"
    elif obstacle == "uncertified":
        doc.parse_artifact.table_fragments[0].cells[-1].native_blank_verified = False
    elif obstacle == "nonzero_cell":
        doc.parse_artifact.table_fragments[0].cells[-1].raw_text = "1.00"
    else:
        doc.page_texts[0] = doc.page_texts[0].replace("本年度没有", "本年度并非没有")
    assert require_no_activity_disposition(SimpleNamespace(code="test"), doc) is None


def test_empty_table_does_not_hide_positive_narrative_or_undefined_percentage():
    doc = empty_three_public()
    doc.page_texts[0] += (
        "\n七、财政拨款“三公”经费支出决算情况说明\n三公经费支出决算为3万元。完成预算的0%。"
    )
    with pytest.raises(RuleDeferred):
        require_no_activity_disposition(SimpleNamespace(code="test"), doc)
    assert doc.fact_receipts[0]["status"] == "insufficient_data"


def test_real_separate_table_and_conflicting_money_are_not_shadows():
    doc, grid = mixed_grid()
    text = deepcopy(grid)
    text.extraction_method = "text"
    assert not PDFParser(None)._text_grid_shadow(grid, text)
    text.bbox = (0, 1000, 560, 1250)
    assert len(PDFParser(None)._merge_candidates([grid], [text])) == 2


GOVERNMENT = "长征镇二十届人大会议文件\n关于普陀区长征镇2025年财政决算和\n2026年上半年预算执行情况的报告\n在人民代表大会会议上\n长征镇财政所\n受镇人民政府委托，报告2025年决算。"


def test_government_mixed_report_routes_to_explicit_blocking_capability_gap():
    profile = resolve_document_profile(
        filename="2025决算和2026预算执行报告.pdf", page_texts=[GOVERNMENT]
    )
    assert (
        profile.level == "government"
        and profile.unsupported_reason == "government_mixed_period_report"
    )
    doc = build_document("gov.pdf", [GOVERNMENT], [[]], 0)
    doc.document_profile = profile
    _, outcomes = run_rules_with_outcomes(doc)
    assert [o.rule_id for o in outcomes] == ["CMM-002"]
    cov = build_obligation_ledger(profile, report_kind=profile.kind)
    assert cov["blocking_obligation_ids"] == ["OBL-GOVERNMENT-MIXED-SCOPE"]
    assert cov["auto_completion_rate"] == 0 and cov["completed_total"] == 0


@pytest.mark.parametrize(
    "body",
    [
        GOVERNMENT.replace("受镇人民政府委托", "由财政所编制"),
        GOVERNMENT.replace("在人民代表大会会议上", "2025年度部门决算"),
    ],
)
def test_government_scope_is_not_guessed_from_a_mixed_filename(body):
    profile = resolve_document_profile(filename="2025决算和2026预算执行报告.pdf", page_texts=[body])
    assert profile.unsupported_reason != "government_mixed_period_report"


def test_publisher_fragment_on_annual_heading_is_joined_in_original_order():
    assert (
        detect_cover_facts(["上海市普陀区市场监督管\n理局2025年度决算"])["cover_org_name"]
        == "上海市普陀区市场监督管理局"
    )


def test_general_public_empty_statement_variant_retains_percentage_blocker():
    doc = empty_three_public()
    doc.page_texts[0] = doc.page_texts[0].replace("没有财政拨款", "没有一般公共预算财政拨款")
    with pytest.raises(RuleNotApplicable):
        require_no_activity_disposition(SimpleNamespace(code="test"), doc)


def test_dash_projection_preserves_literal_marker_without_zero_fact():
    doc = grid_document(
        [
            ["收入", None, "支出", None],
            ["项目", "决算数", "项目", "决算数"],
            ["一般公共预算财政拨款收入", "10.00", "一般公共服务支出", "-"],
            ["本年收入合计", "10.00", "本年支出合计", "10.00"],
        ],
        code="FIN_01_income_expenditure_total",
    )
    table = doc.parse_artifact.table_fragments[0]
    for c in table.cells:
        if c.row_idx == 0 and c.col_idx in (0, 2):
            c.bbox = (c.col_idx * 70, 50, (c.col_idx + 2) * 70, 75)
        if c.row_idx == 0 and c.col_idx in (1, 3):
            c.bbox = None
    doc.fact_context = build_fact_context_from_artifact(
        doc.parse_artifact,
        org_name="甲局",
        fiscal_year=2024,
        report_kind="final",
        doc_type="dept_final",
    )
    before = doc.fact_context.to_dict()
    rows = fact_bound_table_rows(doc, "收入支出决算总表")
    assert rows[2][3] == "-" and doc.fact_context.to_dict() == before
    assert not any(f.raw_value == "-" for f in doc.fact_context.facts)


@pytest.mark.parametrize(
    "problem",
    [
        "none",
        "positive_pct",
        "no_zero_declaration",
        "foreign_year",
        "uncertified",
        "zero_denominator",
    ],
)
def test_share_zero_requires_explicit_no_activity_and_positive_current_total(problem):
    from test_native_blank_cells import three_public
    from src.engine.native_three_public_ratios import evaluate

    doc = three_public()
    # 国外分项原件为空，总额已有金额；说明原文单独明确零支出。
    table = doc.parse_artifact.table_fragments[0]
    for cell in table.cells:
        if cell.row_idx == 4 and cell.col_idx == 3:
            cell.raw_text, cell.numeric_value, cell.native_blank_verified = "", None, True
            from src.schemas.parse_artifact import CellPresence

            cell.presence = CellPresence.BLANK
    doc.fact_context.facts = [
        f
        for f in doc.fact_context.facts
        if not (f.indicator == "overseas" and f.budget_stage == "决算")
    ]
    quote = "因公出国（境）费支出决算为0万元，占0%"
    if problem == "no_zero_declaration":
        quote = "因公出国（境）费占0%"
    if problem == "foreign_year":
        quote = "2023年度" + quote
    if problem == "uncertified":
        table.cells[4 * 12 + 3].native_blank_verified = False
    if problem == "zero_denominator":
        for fact in doc.fact_context.facts:
            if fact.indicator == "total_actual" and fact.budget_stage == "决算":
                fact.normalized_value = Decimal("0")
    doc.page_texts = [quote]
    rule = SimpleNamespace(code="test", _issue=lambda *args, **kwargs: None)
    before = doc.fact_context.to_dict()
    issues, missing = evaluate(
        rule,
        doc,
        {
            "start": 0,
            "page": 1,
            "phrase": "占0%",
            "pct": Decimal("10" if problem == "positive_pct" else "0"),
        },
        indicator="overseas",
        quote=quote,
        denominator_stage="决算",
        year=2024,
        share=True,
    )
    assert not missing if problem == "none" else (missing or issues)
    assert doc.fact_context.to_dict() == before
    if problem == "none":
        assert doc.fact_receipts[0]["status"] == "not_applicable"
        assert len(doc.fact_receipts[0]["cell_observations"]) == 1
