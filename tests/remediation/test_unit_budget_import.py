"""单位预算/决算导入：桌面新目录抽检暴露的版式差异（2026-10-21）。"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from src.services.document_profile_resolver import detect_cover_facts
from src.services.fiscal_row_roles import native_bilateral_padding_positions
from src.services.fiscal_table_rules import is_auxiliary_grant_expenditure_title
from src.services.pdf_parser import PDFParser
from src.services.table_import_receipt import unclassified_table_scope
from tests.remediation.test_budget_2022_table_semantics import grid, measures

FIN_01 = "FIN_01_income_expenditure_total"
FIN_04 = "FIN_04_fiscal_grant_total"

UNIT_LEDGER = [["本年收入", None, "本年支出", None, None, None, None],
               ["项目", "预算数", "项目", "预算数", None, None, None],
               [None, None, None, "合计", "基本支出", None, "项目支出"],
               [None, None, None, None, "人员经费", "公用经费", None],
               ["一、财政拨款收入", "300", "一、教育支出", "300", "200", "50", "50"]]
UNIT_SPANS = {(0, 0): (2, 1), (0, 2): (5, 1), (1, 0): (1, 3), (1, 1): (1, 3), (1, 2): (1, 3),
              (1, 3): (4, 1), (2, 3): (1, 2), (2, 4): (2, 1), (2, 6): (1, 2)}


def test_unit_ledger_maps_the_split_expenditure_side():
    # 单位版收支总表：本年支出拆成 合计/人员经费/公用经费/项目支出，部门版只有一列预算数。
    unit = grid(FIN_01, "2023年预算单位财务收支预算总表", UNIT_LEDGER, UNIT_SPANS, 4)
    assert measures(unit) == {1: "income_actual", 3: "expenditure_actual", 4: "expenditure_personnel",
                              5: "expenditure_public", 6: "expenditure_project"}
    department = grid(FIN_01, "2023年部门财务收支预算总表",
                      [["本年收入", None, "本年支出", None], ["项目", "预算数", "项目", "预算数"],
                       ["一、财政拨款收入", "300", "一、教育支出", "300"]],
                      {(0, 0): (2, 1), (0, 2): (2, 1)}, 2)
    assert measures(department) == {1: "income_actual", 3: "expenditure_actual"}


def test_framed_title_rows_do_not_push_the_header_out_of_the_window():
    rows = [[""] * 7,
            ["2025年预算单位财政拨款收支预算总表"] + [None] * 6,
            ["编制单位：上海孙中山故居纪念馆", None, None, None, None, None, "单位：元"],
            ["财政拨款收入", None, "财政拨款支出", None, None, None, None],
            ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算"],
            ["一、一般公共预算资金", "100", "一、教育支出", "100", "100", "", ""]]
    parser = PDFParser(None)
    assert parser._detect_header_row_count(rows, framed_preamble=True) == 5
    assert parser._detect_header_row_count(rows) == 4  # 文字策略候选沿用原判定
    table = grid(FIN_04, "", rows, {(1, 0): (7, 1), (2, 0): (6, 1), (3, 0): (2, 1), (3, 2): (5, 1)}, 5)
    assert measures(table) == {1: "income_actual", 3: "expenditure_actual", 4: "general_public_budget",
                               5: "government_fund_budget", 6: "state_capital_budget"}


def test_labelled_table_owner_keeps_the_full_name():
    # 9 个字的「上海市道路运输管理局」曾被短匹配截成「海市道路运输管理局」，整表被主体校验拦下。
    table = grid(FIN_04, "2024年预算单位财政拨款收支预算总表\n编制单位：上海市道路运输管理局 单位：元",
                 [["项目", "预算数"], ["一、一般公共预算资金", "100"]], {}, 1)
    assert PDFParser(None)._build_logical_instance(table).org_name == "上海市道路运输管理局"


def test_header_box_and_body_box_on_one_page_form_one_table():
    # 经信委 2024：表头框与表体框之间空出一行（约 25pt），表体曾成为认不出的表。
    header = grid("FIN_03_expenditure", "2024年预算单位支出预算总表",
                  [["科目编码", "科目名称", "合计", "基本支出", "项目支出"]], {}, 1, uid="head")
    body = grid("UNKNOWN_P1_T1", "项目 支出预算",
                [["206", "科学技术支出", "100", "", "100"], ["208", "社会保障和就业支出", "30", "30", ""]],
                {}, 0, top=35, uid="body")
    merged = PDFParser(None)._coalesce_tables([header, body])
    assert [(t.table_code, len(t.rows)) for t in merged] == [("FIN_03_expenditure", 3)]
    far = replace(body, bbox=(0, 80, 50, 100), cells=[replace(c, bbox=(c.bbox[0], c.bbox[1] + 45, c.bbox[2], c.bbox[3] + 45))
                                                         for c in body.cells])
    assert len(PDFParser(None)._coalesce_tables([header, far])) == 2


def test_text_projection_with_fewer_columns_is_a_shadow_of_the_grid():
    rows = [["财政拨款收入", None, "财政拨款支出", None, None, None, None],
            ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算"],
            ["一、一般公共预算资金", "100", "一、教育支出", "100", "100", "", ""]]
    ruled = grid(FIN_04, "2025年部门财政拨款收支预算总表", rows, {(0, 0): (2, 1), (0, 2): (5, 1)}, 2)
    text = grid(FIN_04, "2025年部门财政拨款收支预算总表",
                [["财政拨款支出", None], ["项目", "一般公共预算"], ["一、教育支出", "100"]], {(0, 0): (2, 1)}, 2)
    text.extraction_method = "text"
    assert PDFParser(None)._text_grid_shadow(ruled, text)


def test_cover_owner_wrapped_inside_the_name_is_joined():
    wrapped = "上海市2024年市级单位预算\n预算单位：中国（上海）自由贸易试验区临港新片区管理委员会综合执\n法大队\n2024年2月"
    assert detect_cover_facts([wrapped])["cover_org_name"] == "中国（上海）自由贸易试验区临港新片区管理委员会综合执法大队"
    bracket = "上海市2023年市级单位预算\n预算单位：上海市农业农村委员会执法总队（上海市动物卫生监督所、上海渔港监督\n局）"
    assert detect_cover_facts([bracket])["cover_org_name"] == "上海市农业农村委员会执法总队（上海市动物卫生监督所、上海渔港监督局）"
    complete = "上海市2025年市级单位预算\n预算单位：上海市交通委员会\n2025年2月"
    assert detect_cover_facts([complete])["cover_org_name"] == "上海市交通委员会"
    assert detect_cover_facts(["上海市吴家洼监狱\n2023 年度决算", "目 录"])["cover_org_name"] == "上海市吴家洼监狱"


def test_auxiliary_tables_outside_the_nine_are_counted_not_alarmed():
    performance = grid("UNKNOWN_P1_T0", "财政项目支出绩效目标申报表",
                       [["项目名称", "全球贸易投资监控预警系统", None, None],
                        ["一级指标", "二级指标", "三级指标", "指标值"], ["产出指标", "数量指标", "报告数", "12"]], {}, 1)
    grant = grid("UNKNOWN_P14_T0", "2024年预算单位财政拨款支出预算表",
                 [["功能分类科目编码", "功能分类科目名称", "合计", "人员经费", "公用经费", "项目支出"],
                  ["208", "社会保障和就业支出", "2,625,748", "2,041,608", "197,040", "387,100"]], {}, 1)
    projects = grid("UNKNOWN_P23_T0", "单位预算表10",
                    [["功能分类科目编码", None, None, "功能分类科目名称", "序号", "项目名称", "预算数"],
                     ["类", "款", "项", None, None, None, None],
                     ["208", "", "", "社会保障和就业支出", "", "", "387,100"]], {(0, 0): (3, 1)}, 2)
    assert [unclassified_table_scope(t) for t in (performance, grant, projects)] == [
        "auxiliary_performance_targets", "auxiliary_grant_expenditure", "auxiliary_project_detail"]
    assert not is_auxiliary_grant_expenditure_title("2024年一般公共预算财政拨款支出预算表")


def test_unlabelled_zero_rows_on_the_expenditure_side_are_padding():
    rows = UNIT_LEDGER + [["二、事业收入", "50", "", "0", "", "", ""],
                          ["三、其他收入", "20", "", "0", "", "5", ""],
                          ["收入总计", "370", "支出总计", "305", "200", "55", "50"]]
    table = grid(FIN_01, "2026年预算单位财务收支预算总表", rows, UNIT_SPANS, 4)
    for cell in table.cells:
        if cell.row_idx >= 5 and not cell.raw_text:
            cell.native_blank_verified = True
    # 第 5 行只有占位 0；第 6 行另有非零金额 5，整行不算占位。
    assert native_bilateral_padding_positions(table) == {(5, 3)}
    without_total = replace(table, rows=table.rows[:-1], cells=[c for c in table.cells if c.row_idx < 7])
    assert native_bilateral_padding_positions(without_total) == set()


def test_continuation_grid_without_the_empty_last_column_is_padded():
    parser = PDFParser(None)
    head = [["科目编码", "科目名称", "合计", "基本支出", "项目支出", "对附属单位补助支出"]]
    previous = grid("FIN_03_expenditure", "支出决算表", head + [["201", "一般公共服务支出", "10", "10", "", ""]],
                    {}, 1, page=8, uid="p8")
    current = grid("UNKNOWN_P9_T0", "", [["22102", "住房改革支出", "5", "5", ""]], {}, 0, page=9, uid="p9")
    padded = parser._pad_native_trailing_columns(previous, current)
    assert padded.rows == [["22102", "住房改革支出", "5", "5", "", None]]
    with_money = grid("FIN_03_expenditure", "支出决算表", head + [["201", "一般公共服务支出", "10", "8", "", "2"]],
                      {}, 1, page=8, uid="p8b")
    assert parser._pad_native_trailing_columns(with_money, current) is current


def test_bilateral_total_line_below_the_grid_is_attached():
    rows = [["收入", None, "支出", None], ["项目", "决算数", "项目", "决算数"],
            ["本年收入合计", "1,928.63", "本年支出合计", "2,010.94"]]
    table = grid(FIN_01, "收入支出决算总表", rows, {(0, 0): (2, 1), (0, 2): (2, 1)}, 2)
    words = [{"text": text, "x0": x0, "x1": x0 + 8, "top": 32, "bottom": 38}
             for text, x0 in (("总计", 1), ("2,010.94", 11), ("总计", 21), ("2,010.94", 31))]
    page = SimpleNamespace(extract_words=lambda: words)
    attached = PDFParser(None)._attach_bilateral_total_line(page, table)
    assert attached.rows[-1] == ["总计", "2,010.94", "总计", "2,010.94"]
    assert [c.bbox for c in attached.cells if c.row_idx == 3][1] == (11, 32, 19, 38)
    stray = [dict(word, x0=word["x0"] + 4, x1=word["x1"] + 4) for word in words]  # 金额越出列边界
    assert PDFParser(None)._attach_bilateral_total_line(SimpleNamespace(extract_words=lambda: stray), table) is table


def test_upload_does_not_auto_bind_a_tie_between_same_named_department_and_unit(monkeypatch):
    from api import runtime
    from api.routes import upload

    assert upload._normalize_org_name("上海市绿化和市容管理局本部") == upload._normalize_org_name("上海市绿化和市容管理局（本级）")
    department = SimpleNamespace(id="d", name="上海市经济和信息化委员会", level="department", parent_id=None)
    unit = SimpleNamespace(id="u", name="上海市经济和信息化委员会", level="unit", parent_id="d")
    monkeypatch.setattr(runtime, "extract_pdf_page_texts", lambda path, max_pages=3: [""])
    monkeypatch.setattr(upload, "_collect_org_matches",
                        lambda **kwargs: [(department, .96, "content_fallback"), (unit, .96, "content_fallback")])
    monkeypatch.setattr(upload, "_serialize_match_candidate",
                        lambda org, confidence, basis: {"organization_id": org.id, "confidence": confidence})
    preflight = upload._inspect_document_preflight(filename="上海市经济和信息化委员会2023年度预算.pdf",
                                                    pdf_path="scan.pdf")
    assert preflight["current"] is None and len(preflight["suggestions"]) == 2


def test_ocr_cells_are_untouched_by_padding():
    rows = UNIT_LEDGER + [["二、事业收入", "50", "", "0", "", "", ""], ["收入总计", "350", "支出总计", "300", "200", "50", "50"]]
    table = grid(FIN_01, "2026年预算单位财务收支预算总表", rows, UNIT_SPANS, 4)
    table.extraction_method = "ocr"
    table.cells = [replace(c, native_blank_verified=True) if not c.raw_text else c for c in table.cells]
    assert native_bilateral_padding_positions(table) == set()


def _budget_doc(t4_total="90.00", t8_total="35.00"):
    from src.engine.pipeline import build_document

    tables = {
        "2025年部门财务收支预算总表\n单位：万元": [
            ["项目", "预算数", "项目", "预算数"], ["一、财政拨款收入", "90.00", "一、教育支出", "100.00"],
            ["二、事业收入", "10.00", "", ""], ["收入总计", "100.00", "支出总计", "100.00"]],
        "2025年部门支出预算总表\n单位：万元": [
            ["科目编码", "科目名称", "合计", "基本支出", "项目支出"], ["", "合计", "100.00", "40.00", "60.00"]],
        "2025年部门财政拨款收支预算总表\n单位：万元": [
            ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算"],
            ["一、一般公共预算资金", t4_total, "一、教育支出", t4_total, t4_total, "", ""],
            ["收入总计", t4_total, "支出总计", t4_total, t4_total, "", ""]],
        "2025年部门一般公共预算支出功能分类预算表\n单位：万元": [
            ["科目编码", "科目名称", "合计", "基本支出", "项目支出"], ["", "合计", "90.00", "35.00", "55.00"]],
        "2025年部门一般公共预算基本支出部门预算经济分类预算表\n单位：万元": [
            ["科目编码", "科目名称", "合计", "人员经费", "公用经费"], ["", "合计", t8_total, "30.00", "5.00"]],
    }
    return build_document("budget.pdf", list(tables), [[rows] for rows in tables.values()], 100)


def _bud105(doc):
    from src.engine.budget_rules import BUD105_CrossTableChecks
    from src.engine.rule_outcome import RuleDeferred

    try:
        return [i.message for i in BUD105_CrossTableChecks().apply(doc)], []
    except RuleDeferred as deferred:
        return [i.message for i in deferred.partial_issues], deferred.unresolved_reasons


def test_cross_table_totals_compare_like_with_like_when_income_is_not_all_fiscal():
    # 交通委 2025：T1 含事业收入，总计本就大于只含财政拨款的 T4；T3 基本支出含非财拨来源，T8 只是一般公共预算。
    messages, reasons = _bud105(_budget_doc())
    assert not any("T1与T4" in m or "基本支出与T8" in m for m in messages)
    assert not any("对偶口径不可比" in r for r in reasons)  # T3−T5 恰为非财拨来源，已实证
    messages, _ = _bud105(_budget_doc(t8_total="30.00"))
    assert any(m.startswith("T5基本支出与T8合计不一致: T5=35.00, T8=30.00") for m in messages)
    messages, _ = _bud105(_budget_doc(t4_total="85.00"))
    assert any(m.startswith("T1财政拨款收入与T4收入总计不一致: T1=90.00, T4=85.00") for m in messages)


def test_explicit_zero_statement_counts_as_empty_table_disclosure():
    from src.services.fiscal_empty_disclosure import explicit_zero_statement

    text = "财政拨款支出预算中，一般公共预算拨款支出预算7,347万元；政府性基金拨款支出预算0万元，与2025年预算持平；国有资本经营预算拨款支出预算为0"
    assert explicit_zero_statement([text], "政府性基金预算")
    assert explicit_zero_statement([text], "国有资本经营预算")
    assert not explicit_zero_statement(["政府性基金拨款支出预算78,657万元，比2024年预算增加5,898万元"], "政府性基金预算")
    assert not explicit_zero_statement(["政府性基金拨款支出预算为0.5亿元"], "政府性基金预算")


def test_econ_code_version_follows_the_table_itself():
    from decimal import Decimal

    from src.engine.rules_v33 import _econ_table_uses_2025_codes

    def row(*cells):
        return SimpleNamespace(cells=[SimpleNamespace(text=t, number=n) for t, n in cells])

    # 2025 年度决算：类、款拆格，款码 09 按数值读成 9。
    new = SimpleNamespace(rows=[row((None, Decimal(302)), (None, Decimal(9)), ("差旅费", None), (None, Decimal("21.67")))])
    old = SimpleNamespace(rows=[row((None, Decimal(30211)), ("差旅费", None), (None, Decimal("21.67")))])
    assert _econ_table_uses_2025_codes(new) and not _econ_table_uses_2025_codes(old)


def test_identical_info_hints_from_several_rules_are_merged():
    from src.engine.native_issue_dedup import deduplicate_native_issues

    refs = [{"fact_uid": f"t:11:{m}"} for m in ("total_actual", "basic_actual")]

    def hint(rule):
        return {"rule": rule, "severity": "info", "message": rule,
                "location": {"operand_refs": refs, "fact_context_version_token": "v"}}

    other = {"rule": "V33-005", "severity": "info", "message": "x",
             "location": {"operand_refs": [{"fact_uid": "t:12:total_actual"}, {"fact_uid": "t:12:basic_actual"}],
                          "fact_context_version_token": "v"}}
    merged = deduplicate_native_issues([hint("V33-005"), hint("V33-120"), hint("V33-241"), other])
    assert len(merged) == 2


def test_completion_rate_within_display_rounding_is_a_hint():
    from decimal import Decimal

    from src.engine.native_three_public_ratios import _rounding_range

    def fact(raw):
        return SimpleNamespace(raw_value=raw, unit="万元", normalized_value=Decimal(raw))

    low, high = _rounding_range(fact("0.82"), fact("3.70"))
    assert low <= Decimal("22.27") <= high  # 原文按 0.824 万元计算
    low, high = _rounding_range(fact("6.23"), fact("8.49"))
    assert not low <= Decimal("74.14") <= high  # 节能中心：抄了因公出国的完成率，仍是错误
