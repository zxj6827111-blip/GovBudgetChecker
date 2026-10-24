from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.fact_table_projection import fact_bound_table_rows
from src.engine.rules_v33 import R33120_DetailTableCheck
from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser


def grid(rows, bounds, *, uid, page, code="UNKNOWN_P2_T0", headers=1, confidence=.6, unit=None):
    parser = PDFParser(None)
    cells = [ParsedCell(r, c, text, text, parser._parse_numeric(text), page,
                        (bounds[c], r * 10, bounds[c + 1], (r + 1) * 10),
                        r < headers, confidence, unit, "lines")
             for r, values in enumerate(rows) for c, text in enumerate(values)]
    return ParsedTable(code, "支出决算表" if code.startswith("FIN_") else "续表", page, 0,
                       (bounds[0], 0, bounds[-1], len(rows) * 10), headers, confidence,
                       "lines", unit, rows, cells, uid)


def expense_fragments():
    first = grid([
        ["科目编码", "", "", "科目名称", "本年支出合计", "基本支出", "项目支出"],
        ["合计", "", "", "", "150", "100", "50"],
        ["208", "", "", "社会保障", "100", "70", "30"],
    ], [0, 10, 20, 30, 60, 80, 100, 120], uid="first", page=1,
        code="FIN_03_expenditure", confidence=.99, unit="万元")
    first.cells[0].bbox = (0, 0, 30, 10)
    first.cells[1].bbox = first.cells[2].bbox = None
    second = grid([["210", "卫生健康", "50", "30", "10"]],
                  [0, 30, 60, 80, 100, 120], uid="second", page=2)
    # 纯逻辑单测用合成字符证明满足新准入合同；真实字符索引另由公开PDF回归核验。
    for cell in second.cells:
        if cell.col_idx >= 2:
            cell.native_text_sources = [{"text": cell.raw_text, "bbox": list(cell.bbox),
                                         "physical_page": 2, "method": "synthetic_unit_fixture"}]
    return first, second


def context_for(tables, physical):
    artifact = ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run", page_count=2,
                             table_fragments=tables, physical_fragments=physical,
                             logical_tables=[LogicalTableInstance(t.fragment_uid, t.table_code, t.title,
                                                                  [t.fragment_uid], t.page_number, 2)
                                             for t in tables])
    artifact = ParseArtifact.from_dict(artifact.to_dict())
    context = build_fact_context_from_artifact(artifact, org_name="甲局", fiscal_year=2025,
                                               report_kind="final", doc_type="dept_final")
    return SimpleNamespace(parse_artifact=artifact, fact_context=context, page_texts=["", ""],
                           page_tables=[[], []], anchors={})


def test_split_code_columns_align_without_changing_original_amount_identity():
    first, second = expense_fragments()
    merged = PDFParser(None)._coalesce_tables([first, second])
    assert len(merged) == 1
    assert merged[0].rows[-1] == ["210", None, None, "卫生健康", "50", "30", "10"]
    doc = context_for(merged, [first, second])
    fact = next(f for f in doc.fact_context.facts if f.subject_code == "210" and f.indicator == "total_actual")
    assert fact.normalized_value == Decimal("50")
    assert fact.recognition_status == "unverified"
    ref = fact.extra_dims["evidence_refs"][0]
    assert ref["source_cell_id"] == "second:r0c2"  # Logical column is now 4.
    assert ref["physical_page"] == 2 and ref["quote"] == "50"
    assert second.rows[0] == ["210", "卫生健康", "50", "30", "10"]
    assert list(map(Decimal, fact_bound_table_rows(doc, "支出决算表")[-1][4:])) == [Decimal("50"), Decimal("30"), Decimal("10")]
    issues = R33120_DetailTableCheck().apply(doc)
    assert any("合计行列校验失败" in issue.message and "50.00" in issue.message
               and "40.00" in issue.message for issue in issues)


@pytest.mark.parametrize("amount, disclosed, ready", [("", True, True), ("", False, False), ("10", True, False)])
def test_empty_budget_disclosure_does_not_manufacture_zero_or_hide_amounts(amount, disclosed, ready):
    table = grid([["科目编码", "科目名称", "本年支出"], ["合计", "", amount]],
                 [0, 30, 60, 90], uid="empty", page=1, code="FIN_09_state_capital", confidence=.99)
    doc = context_for([table], [table])
    doc.page_texts = ["国有资本经营预算财政拨款收入支出决算表\n" +
                      ("本单位本年度没有国有资本经营预算财政拨款收入和支出，故本表无数据。" if disclosed else "")]
    if ready:
        assert fact_bound_table_rows(doc, "国有资本经营预算财政拨款收入支出决算表")[1][2] == ""
        assert doc.fact_context is None or not doc.fact_context.facts
    else:
        from src.engine.fact_table_projection import ProjectionUnavailable

        with pytest.raises(ProjectionUnavailable):
            fact_bound_table_rows(doc, "国有资本经营预算财政拨款收入支出决算表")


@pytest.mark.parametrize("conflict", ["money_boundary", "unit", "organization", "year", "ocr"])
def test_continuation_binding_does_not_override_real_conflicts(conflict):
    first, second = expense_fragments()
    if conflict == "money_boundary":
        second.cells[2].bbox = (61, 0, 83, 10)
    elif conflict == "unit":
        second.unit_hint = "元"
    elif conflict == "organization":
        first.title, second.title = "甲局支出决算表", "乙局支出决算表"
    elif conflict == "year":
        first.title, second.title = "2025年支出决算表", "2024年支出决算表"
    else:
        second.cells[2].extraction_method = "ocr"
    aligned = PDFParser(None)._align_native_continuation_columns(first, second)
    assert not PDFParser(None)._native_continuation_columns_match(first, aligned)
    if conflict != "ocr":
        assert aligned.rows == second.rows


def test_sparse_summary_continuation_is_not_lost_due_to_guessed_header_count():
    first = grid([["收入", "", "支出", ""], ["项目", "决算数", "项目", "决算数"],
                  ["财政拨款收入", "100", "一般公共服务", "100"]], [0, 30, 60, 90, 120],
                 uid="first", page=1, code="FIN_01_income_expenditure_total", headers=2,
                 confidence=.99, unit="万元")
    first.title = "收入支出决算总表"
    first.cells[0].bbox = (0, 0, 60, 10)
    first.cells[1].bbox = None
    first.cells[2].bbox = (60, 0, 120, 10)
    first.cells[3].bbox = None
    second = grid([["", "", "其他支出", ""], ["", "", "住房保障", ""],
                   ["总计", "100", "总计", "100"]], [0, 30, 60, 90, 120],
                  uid="second", page=2, headers=3)
    merged = PDFParser(None)._coalesce_tables([first, second])
    assert len(merged) == 1 and len(merged[0].rows) == 6
    doc = context_for(merged, [first, second])
    totals = [f for f in doc.fact_context.facts if f.subject_name == "总计"]
    assert len(totals) == 2 and all(f.normalized_value == Decimal("100") for f in totals)
    assert all(f.extra_dims["evidence_refs"][0]["physical_page"] == 2 for f in totals)


def test_partial_text_shadow_does_not_create_a_duplicate_grid_instance():
    parser = PDFParser(None)
    first, _ = expense_fragments()
    text = ParsedTable(first.table_code, first.title, 1, 1, (60, -20, 119, 25), 1, .99,
                       "text", "万元", [["支出决算表", ""], ["50 30 20", ""]], [], "shadow")
    assert parser._merge_candidates([first], [text]) == [first]


def test_single_row_personnel_public_footer_is_merged_with_physical_amount_ids():
    first = grid([["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
                  ["301", "工资福利支出", "100", "302", "商品和服务支出", "20"]],
                 [0, 30, 60, 80, 110, 140, 160], uid="head", page=1,
                 code="FIN_06_basic_expenditure", confidence=.99, unit="万元")
    footer = grid([["人员经费合计", "100", "公用经费合计", "20"]],
                  [0, 60, 80, 140, 160], uid="footer", page=2)
    parser = PDFParser(None)
    assert parser._is_meaningful_table(footer.rows)
    merged = parser._coalesce_tables([first, footer])
    assert len(merged) == 1
    doc = context_for(merged, [first, footer])
    personnel = next(f for f in doc.fact_context.facts if f.subject_name == "人员经费合计")
    assert personnel.normalized_value == Decimal("100")
    assert personnel.extra_dims["evidence_refs"][0]["source_cell_id"] == "footer:r0c1"
    rows = fact_bound_table_rows(doc, "一般公共预算财政拨款基本支出决算表")
    assert rows[-1][0] == "人员经费合计" and rows[-1][3] == "公用经费合计"


@pytest.mark.parametrize("first_word, recover", [("229", True), ("2025", False), ("单位：万元", False)])
def test_open_grid_top_uses_native_word_positions_and_rejects_header_metadata(first_word, recover):
    table = grid([["22999", "其他支出", "0.02", "0.00", "0.02"],
                  ["2299999", "其他支出", "0.02", "0.00", "0.02"]],
                 [0, 30, 60, 80, 100, 120], uid="grid", page=2)
    # Native row immediately above the existing top border.
    words = [{"text": word, "x0": x + 2, "x1": x + 15, "top": -8, "bottom": -2}
             for word, x in zip([first_word, "其他支出", "0.02", "0.00", "0.02"], [0, 30, 60, 80, 100], strict=False)]
    recovered = PDFParser(None)._recover_open_top_grid_row(SimpleNamespace(extract_words=lambda: words), table)
    assert len(recovered.rows) == len(table.rows) + int(recover)
    if recover:
        assert recovered.rows[0] == [first_word, "其他支出", "0.02", "0.00", "0.02"]
        assert recovered.cells[2].raw_text == "0.02" and recovered.cells[2].page_number == 2
        assert table.rows[0][0] == "22999"


@pytest.mark.parametrize("code, recover", [("2101101", True), ("2025", False), ("13", False)])
def test_open_bottom_merged_code_does_not_conflict_with_empty_header_lanes(code, recover):
    table = grid([["科目编码", "", "", "科目名称", "本年支出合计", "基本支出", "项目支出"],
                  ["21011", "", "", "行政事业单位医疗", "500", "500", "0"]],
                 [0, 10, 20, 30, 60, 80, 100, 120], uid="open", page=1,
                 code="FIN_03_expenditure", confidence=.99, unit="万元")
    table.cells[7].bbox = (0, 10, 30, 20)
    words = [{"text": value, "x0": x, "x1": x + 10, "top": 22, "bottom": 28}
             for value, x in zip([code, "行政单位医疗", "392.93", "392.93", "0.00"], [7, 35, 65, 85, 105], strict=False)]
    result = PDFParser(None)._recover_open_bottom_grid_row(SimpleNamespace(extract_words=lambda: words), table)
    assert len(result.rows) == len(table.rows) + int(recover)
    if recover:
        assert result.rows[-1] == [code, "", "", "行政单位医疗", "392.93", "392.93", "0.00"]
        assert result.cells[-3].bbox == (60, 20, 80, 28)


@pytest.mark.parametrize("wrong_lane", [False, True])
def test_native_footer_requires_both_literal_labels_and_original_money_lanes(wrong_lane):
    first = grid([["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
                  ["301", "工资福利支出", "100", "302", "商品和服务支出", "20"]],
                 [0, 30, 60, 80, 110, 140, 160], uid="head", page=1,
                 code="FIN_06_basic_expenditure", confidence=.99, unit="万元")
    words = [{"text": value, "x0": x, "x1": x + 8, "top": 5, "bottom": 12}
             for value, x in zip(["人员经费合计", "100", "公用经费合计", "20"], [20, 62, 100, 142], strict=False)]
    if wrong_lane:
        words[-1]["x0"], words[-1]["x1"] = 125, 132
    parser = PDFParser(None)
    footer = parser._extract_native_basic_footer(SimpleNamespace(extract_words=lambda: words), 2)
    merged = parser._coalesce_tables([first, footer])
    assert len(merged) == (2 if wrong_lane else 1)
    if not wrong_lane:
        doc = context_for(merged, [first, footer])
        fact = next(f for f in doc.fact_context.facts if f.subject_name == "公用经费合计")
        assert fact.normalized_value == Decimal("20")
        assert fact.extra_dims["evidence_refs"][0]["bbox"] == [142, 5, 150, 12]


def test_income_table_uses_income_columns_and_roots_at_different_detail_levels():
    table = grid([["科目编码", "科目名称", "本年收入合计", "财政拨款收入", "其他收入"],
                  ["合计", "", "150", "140", "10"],
                  ["201", "一般公共服务", "100", "90", "10"],
                  ["20101", "机关运行", "100", "90", "10"],
                  ["2010101", "行政运行", "100", "90", "10"],
                  ["208", "社会保障", "50", "50", "0"]],
                 [0, 30, 60, 80, 100, 120], uid="income", page=1,
                 code="FIN_02_income", confidence=.99, unit="万元")
    table.title = "收入决算表"
    doc = context_for([table], [table])
    assert not R33120_DetailTableCheck().apply(doc)


def test_current_native_adapter_uses_corrected_facts_and_refreshes_its_cache():
    from src.engine.rules_v33 import _ensure_parsed_tables

    first, second = expense_fragments()
    doc = context_for(PDFParser(None)._coalesce_tables([first, second]), [first, second])
    doc.page_tables = [[[["old amount", "999"]]], []]
    assert _ensure_parsed_tables(doc)
    parsed = next(t for t in doc.parsed_tables.values() if t.table_code == "FIN_03_expenditure")
    assert parsed.rows[-1].cells[4].number == Decimal("50")
    assert parsed.rows[-1].cells[4].page == 2
    assert parsed.rows[-1].cells[4].bbox == second.cells[2].bbox
    fact = next(f for f in doc.fact_context.facts if f.subject_code == "210" and f.indicator == "total_actual")
    fact.normalized_value = Decimal("55")
    doc.fact_context.version_token = doc.fact_context.compute_version_token()
    assert _ensure_parsed_tables(doc)
    refreshed = next(t for t in doc.parsed_tables.values() if t.table_code == "FIN_03_expenditure")
    assert refreshed.rows[-1].cells[4].number == Decimal("55")
    assert parsed.rows[-1].cells[4].number == Decimal("50")


def test_fiscal_grant_narrative_compares_total_including_carryover():
    from src.engine.pipeline import build_document
    from src.engine.rules_v33 import R33105_FinGrantTotal_vs_Text, R33221_Narrative4_T4

    table = grid([["收入", "", "支出", "", "", "", ""],
                  ["项目", "决算数", "项目", "合计", "一般公共预算财政拨款", "政府性基金预算财政拨款", "国有资本经营预算财政拨款"],
                  ["本年收入合计", "100", "本年支出合计", "100", "100", "", ""],
                  ["总计", "120", "总计", "120", "120", "", ""]],
                 [0, 30, 60, 90, 120, 150, 180, 210], uid="grant", page=1,
                 code="FIN_04_fiscal_grant_total", headers=2, confidence=.99, unit="万元")
    table.title = "财政拨款收入支出决算总表"
    for col, start, end in [(0, 0, 60), (2, 60, 210)]:
        table.cells[col].bbox = (start, 0, end, 10)
    for col in [1, 3, 4, 5, 6]:
        table.cells[col].bbox = None
    base = context_for([table], [table])
    texts = ["财政拨款收入支出决算总表\n单位：万元", "一、收入支出决算总体情况说明\n收入支出总计150万元。\n四、财政拨款收入支出决算总体情况说明\n2025年度财政拨款收入支出总计120万元。\n五、一般公共预算财政拨款支出决算情况说明"]
    doc = build_document("甲局2025年度部门决算.pdf", texts, [[table.rows], []], 0)
    doc.parse_artifact, doc.fact_context = base.parse_artifact, base.fact_context
    assert not R33105_FinGrantTotal_vs_Text().apply(doc)
    assert not R33221_Narrative4_T4().apply(doc)


@pytest.mark.parametrize("text, keyword, amount", [
    ("基本支出37，942.42万元", "基本支出", "37942.42"),
    ("收 入 支 出 总 计102082.14 万元。收入支出总计增加24063.47万元。", "收入支出总计", "102082.14"),
])
def test_literal_numeric_punctuation_and_chinese_spacing_do_not_change_the_amount(text, keyword, amount):
    from src.engine.rules_v33 import near_strict_number

    assert near_strict_number(text, [keyword]).decimal_val == Decimal(amount)


def test_native_section_header_on_page_end_does_not_use_the_page_label_as_amount():
    from src.engine.rules_v33 import _final_section_scope, near_number

    doc = SimpleNamespace(parse_artifact=object(), page_texts=[
        "六、一般公共预算财政拨款基本支出决算情况说明\n1",
        "一般公共预算财政拨款基本支出35987.98万元。\n七、财政拨款三公经费支出决算情况说明\n2",
    ])
    scope = _final_section_scope(doc, ("基本支出决算情况说明",))
    assert near_number(scope, ["基本支出"]) == 35987.98


def test_three_public_current_facts_compare_the_same_subject_and_decision_stage():
    from src.engine.rules_v33 import R33224_Narrative7_T7

    rows = [["合计", "", "因公出国（境）费", "", "公务用车购置及运行费", "", "公务用车购置费", "", "公务用车运行费", "", "公务接待费", ""],
            ["预算数", "决算数"] * 6,
            ["100", "80", "40", "30", "40", "30", "10", "0", "30", "30", "20", "20"]]
    table = grid(rows, list(range(0, 130, 10)), uid="three", page=1,
                 code="FIN_07_three_public", headers=2, confidence=.99, unit="万元")
    for c in range(0, 12, 2):
        table.cells[c].bbox = (c * 10, 0, (c + 2) * 10, 10)
        table.cells[c + 1].bbox = None
    doc = context_for([table], [table])
    doc.page_texts = ["七、财政拨款三公经费支出决算情况说明\n“三公”经费支出年初预算为100万元，支出决算为80万元。因公出国（境）费决算为30万元，公务用车购置及运行费决算为30万元，公务接待费决算为20万元。", ""]
    assert not R33224_Narrative7_T7().apply(doc)
    # An incorrect actual equal to a different budget must still be reported.
    doc.page_texts[0] = doc.page_texts[0].replace("接待费决算为20", "接待费决算为40")
    issues = R33224_Narrative7_T7().apply(doc)
    assert len(issues) == 1 and "公务接待费" in issues[0].message
    assert issues[0].fact_ids and issues[0].check_relation_id.startswith("V33-224:")


def test_soft_native_spacing_and_fullwidth_thousands_separator_are_normalized_before_clauses():
    from src.utils.narration import merge_page_texts, split_clauses
    from src.engine.rules_v33 import _extract_amount_from_segment

    text = merge_page_texts(["三、支出决算情况说明\n其中：基本支 出37，942.42万元，占10.72%；项目支出316059.05万元。"])
    clause = next(clause for clause in split_clauses(text) if "基本支出" in clause)
    amount = _extract_amount_from_segment(clause, ["基本支出"])
    assert amount[0] == Decimal("37942.42")


def test_spaced_leading_thousands_group_preserves_complete_budget_amount():
    from src.engine.rules_v33 import _WP4E_BUDGET_RE, _wp4e_amount

    match = _WP4E_BUDGET_RE.search("年初预算1 10,210.99万元，支出决算139407.47万元")
    assert match.group("unit") == "万元"
    assert _wp4e_amount(match.group("amount")) == Decimal("110210.99")
    # Separate decimal amounts are never concatenated into a new budget.
    match = _WP4E_BUDGET_RE.search("年初预算100.00 200.00万元")
    assert match.group("amount") == "100.00" and match.group("unit") is None
