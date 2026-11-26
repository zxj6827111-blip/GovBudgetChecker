"""Round 4 决算规则与解析优化单元测试及防假绿反例。"""
from __future__ import annotations

from decimal import Decimal
import re
from types import SimpleNamespace

import pytest

from src.engine.checklist_identity_rules import CMM019_CoverElements, _independent_body_name
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.native_narrative_checks import check_narrative_relations
from src.engine.native_table_arithmetic import check_basic_economics
from src.engine.native_table_text import _inside_full_quotation, check_three_public_details
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33107_BasicExpense_Check,
    R33214_T1_TotalBalance,
    R33245_ThreePublicDirectionContradiction,
    R33NarrativeIndicatorRepeat,
    _extract_final_functional_narrative_mentions,
)
from src.schemas.document_profile import DocumentProfile
from src.schemas.parse_artifact import (
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES
from src.services.native_dimension_recovery import (
    repair_native_cross_page_code_tail,
    repair_native_cross_page_name,
)
from src.services.pdf_parser import PDFParser
from src.services.rule_source_binding import bind_rule_sources


HASH = "a" * 64


def _make_parsed_table(
    rows,
    *,
    table_code="FIN_03_expenditure",
    title="支出决算表",
    page=1,
    header_rows=2,
    bboxes=None,
    frag_name=None,
    extraction_method="lines",
):
    parser = PDFParser(None)
    cells = []
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            raw = "" if val is None else str(val)
            bbox = (
                bboxes[(r, c)]
                if bboxes and (r, c) in bboxes
                else (c * 80.0, r * 20.0 + 40.0, (c + 1) * 80.0, r * 20.0 + 60.0)
            )
            cells.append(
                ParsedCell(
                    r,
                    c,
                    raw,
                    raw,
                    parser._parse_numeric(raw),
                    page,
                    bbox,
                    r < header_rows,
                    0.99,
                    "万元",
                    extraction_method,
                    native_blank_verified=not raw,
                )
            )
    tbl_x1 = max((b[2] for b in bboxes.values()), default=len(rows[0]) * 80.0) if bboxes else len(rows[0]) * 80.0
    tbl_y1 = max((b[3] for b in bboxes.values()), default=len(rows) * 20.0 + 40.0) if bboxes else len(rows) * 20.0 + 40.0
    return ParsedTable(
        table_code,
        title,
        page,
        0,
        (0.0, 40.0, tbl_x1, tbl_y1),
        header_rows,
        0.99,
        extraction_method,
        "万元",
        rows,
        cells,
        frag_name or f"frag_{table_code}_{page}",
    )


def _chars_for_cell(text: str, bbox: tuple[float, float, float, float]) -> list[dict]:
    """为单格内容构造带真实几何坐标的字符流，供 native_dimension_recovery 验证。"""
    x0, y0, x1, y1 = bbox
    lines = str(text or "").split("\n")
    out = []
    line_h = (y1 - y0) / max(len(lines), 1)
    for li, line in enumerate(lines):
        cy0 = y0 + li * line_h
        cy1 = cy0 + line_h
        step = (x1 - x0) / max(len(line), 1)
        for ci, ch in enumerate(line):
            out.append({
                "text": ch,
                "x0": x0 + ci * step,
                "x1": x0 + (ci + 1) * step,
                "top": cy0,
                "bottom": cy1,
            })
    return out


def _t7_fact(code, name, indicator, raw):
    normalized = Decimal(raw.replace(",", "")) if raw else None
    return FiscalFact(
        fact_uid=f"fact-{code}-{indicator}",
        indicator=indicator,
        raw_value=raw,
        unit="万元",
        normalized_value=normalized,
        org_name="上海市某某单位",
        org_scope="单位",
        fiscal_year=2024,
        period="本年",
        fund_scope="general_public",
        budget_stage="决算",
        subject_code=code,
        subject_name=name,
        table_code="FIN_07_three_public",
        instance_uid="inst-t7",
    )


def _issue_to_payload(issue):
    rule_id = getattr(issue, "rule", None) or getattr(issue, "rule_id", "")
    return {
        "id": f"rule:{rule_id}",
        "source": "rule",
        "rule_id": rule_id,
        "severity": issue.severity,
        "message": issue.message,
        "evidence_text": issue.evidence_text,
        "location": dict(issue.location or {}),
    }


def test_step1_fin02_subject_code_dimension_and_parser_fixes():
    """Step 1: 科目代码列归入 dimensions、多行孤尾拼回、5位经济代码尾拼回、prose 假表丢弃。"""
    rows_fin02 = [
        ["项目", None, "本年收入合计", "财政拨款收入"],
        ["科目代码", "科目名称", None, None],
        ["合计", None, "100.00", "100.00"],
        ["208", "社会保障和就业支出", "100.00", "100.00"],
    ]
    tbl_fin02 = _make_parsed_table(rows_fin02, table_code="FIN_02_income", title="收入决算表")
    mappings = {
        m["canonical_measure"]: m
        for m in canonical_column_mappings(tbl_fin02, NINE_TABLE_RULES["FIN_02_income"])
    }
    assert mappings["total_actual"]["source_col_idx"] == 2
    assert mappings["total_actual"]["source_label_cols"] == [0, 1]

    prev_bbox = (80.0, 740.0, 220.0, 760.0)
    next_bbox = (80.0, 40.0, 220.0, 82.0)
    prev_tbl = _make_parsed_table(
        [
            ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
            ["2080505", "机关事业单位", "50.00", "50.00", "0.00"],
        ],
        table_code="FIN_05_general_public_expenditure",
        title="一般公共预算财政拨款支出决算表",
        page=1,
        header_rows=1,
        bboxes={(1, 1): prev_bbox},
        frag_name="frag_fin05_p1",
    )
    next_tbl = _make_parsed_table(
        [
            ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出"],
            ["", "基本养老保险\n缴费支出", "", "", ""],
            ["2080506", "职业年金缴费支出", "10.00", "10.00", "0.00"],
        ],
        table_code="UNKNOWN_2",
        title="续页",
        page=2,
        header_rows=0,
        bboxes={(1, 1): next_bbox},
        frag_name="frag_fin05_p2",
    )
    # 无表头的纯数据续页：行 0 是孤立尾行
    next_tbl_pure = _make_parsed_table(
        [
            ["", "基本养老保险\n缴费支出", "", "", ""],
            ["2080506", "职业年金缴费支出", "10.00", "10.00", "0.00"],
        ],
        table_code="UNKNOWN_2",
        title="续页",
        page=2,
        header_rows=0,
        bboxes={(0, 1): next_bbox, (1, 1): (80.0, 82.0, 220.0, 102.0)},
        frag_name="frag_fin05_p2",
    )
    prev_page = SimpleNamespace(chars=_chars_for_cell("机关事业单位", prev_bbox), height=800.0)
    curr_page = SimpleNamespace(chars=_chars_for_cell("基本养老保险\n缴费支出", next_bbox), height=800.0)
    repaired_frags = repair_native_cross_page_name(prev_page, curr_page, prev_tbl, next_tbl_pure, [prev_tbl])
    assert repaired_frags[0].rows[1][1] == "机关事业单位基本养老保险缴费支出"
    assert next_tbl.rows[1][1].startswith("基本养老保险")

    t6_prev_b0 = (0.0, 740.0, 80.0, 760.0)
    t6_prev_b3 = (240.0, 740.0, 320.0, 760.0)
    t6_next_b0 = (0.0, 42.0, 80.0, 60.0)
    t6_next_b3 = (240.0, 42.0, 320.0, 60.0)
    t6_prev = _make_parsed_table(
        [
            ["编码", "名称", "金额", "编码", "名称", "金额"],
            ["303", "奖励金", "0.00", "302", "福利费", "6.91"],
        ],
        table_code="FIN_06_basic_expenditure",
        title="一般公共预算财政拨款基本支出决算表",
        page=1,
        header_rows=1,
        bboxes={(1, 0): t6_prev_b0, (1, 3): t6_prev_b3},
        frag_name="frag_t6_p1",
    )
    t6_next = _make_parsed_table(
        [
            ["09", "", "", "29", "", ""],
            ["30399", "其他对个人和家庭的补助", "1.00", "30299", "其他商品和服务支出", "2.00"],
        ],
        table_code="UNKNOWN_2",
        title="续页",
        page=2,
        header_rows=0,
        bboxes={(0, 0): t6_next_b0, (0, 3): t6_next_b3},
        frag_name="frag_t6_p2",
    )
    t6_prev_page = SimpleNamespace(
        chars=[*_chars_for_cell("303", t6_prev_b0), *_chars_for_cell("302", t6_prev_b3)],
        height=800.0,
    )
    t6_curr_page = SimpleNamespace(
        chars=[*_chars_for_cell("09", t6_next_b0), *_chars_for_cell("29", t6_next_b3)],
        height=800.0,
    )
    code_repaired = repair_native_cross_page_code_tail(t6_prev_page, t6_curr_page, t6_prev, t6_next, [t6_prev])
    assert code_repaired[0].rows[1][0] == "30309"
    assert code_repaired[0].rows[1][3] == "30229"

    parser = PDFParser(None)
    ord_tbl = _make_parsed_table(
        [
            ["二十二、公共服务支出", "", ""],
            ["科目编码", "科目名称", "金额"],
            ["201", "一般公共服务支出", "10.00"],
        ],
        table_code="FIN_05_general_public_expenditure",
        title="二十二、公共服务支出",
        page=2,
        header_rows=2,
    )
    assert not parser._starts_new_table(prev_tbl, ord_tbl)
    col_prev = _make_parsed_table(
        [["科目编码", "科目名称", "本年支出合计", "基本支出"]],
        table_code="FIN_03_expenditure",
        title="支出决算表",
        page=1,
        header_rows=1,
        bboxes={(0, 0): (0.0, 40.0, 100.0, 60.0), (0, 1): (100.0, 40.0, 200.0, 60.0),
                (0, 2): (200.0, 40.0, 350.0, 60.0), (0, 3): (350.0, 40.0, 500.0, 60.0)},
    )
    col_drift_1pt = _make_parsed_table(
        [["208", "社保支出", "10.00", "10.00"]],
        table_code="UNKNOWN_2",
        title="续页",
        page=2,
        header_rows=0,
        bboxes={(0, 0): (0.0, 40.0, 100.0, 60.0), (0, 1): (100.0, 40.0, 201.0, 60.0),
                (0, 2): (201.0, 40.0, 350.0, 60.0), (0, 3): (350.0, 40.0, 500.0, 60.0)},
    )
    col_drift_40pt = _make_parsed_table(
        [["208", "社保支出", "10.00", "10.00"]],
        table_code="UNKNOWN_2",
        title="续页",
        page=2,
        header_rows=0,
        bboxes={(0, 0): (0.0, 40.0, 100.0, 60.0), (0, 1): (100.0, 40.0, 240.0, 60.0),
                (0, 2): (240.0, 40.0, 350.0, 60.0), (0, 3): (350.0, 40.0, 500.0, 60.0)},
    )
    assert parser._native_continuation_columns_match(col_prev, col_drift_1pt)
    assert not parser._native_continuation_columns_match(col_prev, col_drift_40pt)

    prose_rows = [
        ["十一、其他重要事项的情况说明", "", ""],
        ["（一）机关运行经费支出情况说明。本部门2024年度无机关运行", "", "经费支出。"],
        ["（二）政府采购支出情况说明。本部门2024年度无政府采购", "", "支出预算。"],
        ["（三）国有资产占用情况说明。截至2024年12月31日，", "", "共有车辆0辆。"],
        ["（四）预算绩效情况说明。1、预算绩效管理工作开展情况。", "", "无。"],
        ["2、部门决算中项目绩效自评结果：本部门开展绩效自评的项目", "", "共0个。"],
    ]
    prose_tbl = _make_parsed_table(
        prose_rows,
        table_code="UNKNOWN_24",
        title="十一、其他重要事项的情况说明",
        page=24,
        header_rows=1,
        extraction_method="text",
    )
    assert parser._looks_like_narrative_prose_table(prose_tbl)
    real_rows = [
        ["功能分类科目编码", "科目名称", "本年支出合计"],
        ["208", "社会保障和就业支出", "100.00"],
        ["20805", "行政事业单位养老支出", "100.00"],
        ["2080501", "行政单位离退休", "100.00"],
        ["2080502", "事业单位离退休", "0.00"],
        ["合计", "", "100.00"],
    ]
    real_tbl = _make_parsed_table(
        real_rows,
        table_code="UNKNOWN_12",
        title="表",
        page=12,
        header_rows=1,
        extraction_method="text",
    )
    assert not parser._looks_like_narrative_prose_table(real_tbl)


def test_step2_three_public_quote_scope_and_yoy_fallback():
    """Step 2: _inside_full_quotation 不受前页未闭合引号污染；(一)回退与 YoY-only 排除。"""
    assert not _inside_full_quotation("因公出国（境）费决算0.00万元。", 0, 10)
    assert _inside_full_quotation("说明中“因公出国（境）费决算0.00万元”为示例。", 4, 15)

    p1_unclosed = "一、单位概况\n根据“相关工作要求，本单位严格执行规定。\n"
    p2_three_public = (
        "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n"
        "（一）“三公”经费财政拨款支出决算总体情况说明。\n"
        "2024年度“三公”经费财政拨款支出决算为0.50万元，本单位2024年度无因公出国（境）收入和支出；"
        "公务接待费支出决算较2023年度增加0.10万元。\n"
        "（二）“三公”经费财政拨款支出决算具体情况说明。\n"
        "2、公务用车购置及运行维护费支出决算为0.00万元，其中：公务用车购置费0.00万元，公务用车运行维护费0.00万元。\n"
        "3、公务接待费支出决算为0.50万元，国内公务接待1批次、5人次。\n"
    )
    tbl = _make_parsed_table(
        [
            ["合计", "因公出国（境）费", "公务用车购置及运行维护费", "公务接待费"],
            ["决算数", "决算数", "小计", "公务用车购置费", "公务用车运行维护费", "决算数"],
        ],
        table_code="FIN_07_three_public",
        title="财政拨款“三公”经费支出决算表",
        page=1,
    )
    doc = build_document(
        "three_public.pdf",
        [p1_unclosed, p2_three_public],
        [[], []],
        0,
    )
    doc.page_texts = [p1_unclosed, p2_three_public]

    class _Rule:
        code = "V33-209"

        def _issue(self, msg, loc, severity="medium", **kw):
            return SimpleNamespace(rule_id=self.code, message=msg, location=loc, severity=severity, **kw)

    class _Ctx:
        file_hash = HASH
        parse_run_uid = "run"
        org_name = "上海市某某单位"
        fiscal_year = 2024
        version_token = "v1"
        facts = [
            _t7_fact("total", "合计", "total_actual", "0.50"),
            _t7_fact("total", "合计", "overseas", "0.00"),
            _t7_fact("total", "合计", "vehicle_purchase_operation", "0.00"),
            _t7_fact("total", "合计", "vehicle_purchase", "0.00"),
            _t7_fact("total", "合计", "vehicle_operation", "0.00"),
            _t7_fact("total", "合计", "reception", "0.50"),
        ]

    doc.parse_artifact = ParseArtifact(
        source_file_hash=HASH,
        parse_run_uid="run",
        table_fragments=[tbl],
        physical_fragments=[tbl],
        logical_tables=[],
    )
    doc.fact_context = _Ctx()
    doc.fact_receipts = []
    issues = check_three_public_details(_Rule(), doc)
    assert all("未说明本年度" not in i.message for i in issues)


def test_step3_proc_absence_org_alias_yoy_typo_and_self_count():
    """Step 3: 采购无支出识别、团委别名、YoY 错年 warn+算术核验、绩效自评数识别及反例。"""
    p_perf_and_proc = (
        "2024 年度共青团上海市某某区委员会决算\n"
        "一、收入支出决算总体情况说明\n"
        "共青团上海市某某区委员会2024年度收入支出总计120.00万元，与2024年度相比，增加20.00万元，增长20.00%。\n"
        "九、预算绩效情况说明\n"
        "本部门绩效自评价的2024年年度一级项目2个，"
        "评价共涉及资金20.00万元，绩效评级为“优”的项目2个。\n"
        "十一、其他重要事项的情况说明\n"
        "（二）政府采购支出情况说明。\n"
        "本部门2024年度政府采购支出总额0.00万元，其中：政府采购货物支出0.00万元、"
        "无工程采购支出金额、无服务采购支出。\n"
    )
    doc = build_document("yoy.pdf", [p_perf_and_proc], [[]], 0)
    doc.dominant_year = 2024
    doc.report_kind = "final"

    class _PerfRule:
        code = "V33-250"

        def _issue(self, msg, loc, severity="medium", **kw):
            return SimpleNamespace(rule_id=self.code, message=msg, location=loc, severity=severity, **kw)

    class _NarRule:
        code = "V33-112"

        def _issue(self, msg, loc, severity="medium", **kw):
            return SimpleNamespace(rule_id=self.code, message=msg, location=loc, severity=severity, **kw)

    perf_issues = check_performance_counts(_PerfRule(), doc)
    assert perf_issues == []

    # 错字 “项1个”（缺少“目”字）应报 low 提示但不当成无项目漏检
    doc_typo = build_document(
        "typo.pdf",
        [
            "九、预算绩效情况说明\n"
            "2、单位决算中项目绩效自评结果。\n"
            "本单位绩效自评项1个，涉及资金10.00万元，绩效评级为“优”的项目1个。"
        ],
        [[]],
        0,
    )
    doc_typo.dominant_year = 2024
    typo_issues = check_performance_counts(_PerfRule(), doc_typo)
    assert any(i.severity == "low" and "项1个" in i.message for i in typo_issues)

    facts = [
        FiscalFact(
            fact_uid="f_cur", table_code="FIN_01_income_expenditure_total", instance_uid="inst1",
            budget_stage="决算", period="本年", org_name="共青团上海市某某区委员会",
            fiscal_year=2024, subject_code="total", subject_name="总计", indicator="income_actual",
            raw_value="120.00", normalized_value=Decimal("120.00"), unit="万元",
            fund_scope="general_public", org_scope="单位",
        ),
        FiscalFact(
            fact_uid="f_prev", table_code="FIN_01_income_expenditure_total", instance_uid="inst1",
            budget_stage="决算", period="上年", org_name="共青团上海市某某区委员会",
            fiscal_year=2023, subject_code="total", subject_name="总计", indicator="income_actual",
            raw_value="100.00", normalized_value=Decimal("100.00"), unit="万元",
            fund_scope="general_public", org_scope="单位",
        ),
    ]

    class _Artifact:
        source_file_hash = HASH
        parse_run_uid = "run"

    class _Ctx:
        file_hash = HASH
        parse_run_uid = "run"
        org_name = "中国共产主义青年团上海市某某区委员会"
        fiscal_year = 2024
        version_token = "v1"

    _Ctx.facts = facts
    doc.parse_artifact = _Artifact()
    doc.fact_context = _Ctx()
    nar_issues, nar_unresolved = check_narrative_relations(_NarRule(), doc)
    assert any("2024年度" in i.message and i.severity == "warn" for i in nar_issues)
    assert all("外单位" not in i.message for i in nar_issues)

    # 反例：无工程采购预算 在决算材料不得充当 政府采购决算0支出
    doc_bad_proc = build_document(
        "bad_proc.pdf",
        ["十一、其他重要事项的情况说明\n（二）政府采购支出情况说明。\n"
         "本部门2024年度政府采购支出总额0.00万元，其中：无工程采购预算、政府采购货物支出0.00万元、政府采购服务支出0.00万元。"],
        [[]],
        0,
    )
    doc_bad_proc.dominant_year = 2024
    doc_bad_proc.parse_artifact = _Artifact()
    doc_bad_proc.fact_context = _Ctx()
    _bad_issues, bad_unresolved = check_narrative_relations(_NarRule(), doc_bad_proc)
    assert bad_unresolved


def test_step4_v33243_duplicate_blank_row_location_binding():
    """Step 4: V33-243 空金额重复行可通过 table.cells+pos 唯一绑定原文页。"""
    rows = [
        ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
        ["人员经费合计", "", "10.00", "公用经费合计", "", "5.00"],
        ["303", "对个人和家庭的补助", "10.00", "302", "商品和服务支出", "5.00"],
        ["30311", "其他对个人和家庭的补助", "", "30201", "办公费", "5.00"],
        ["30311", "其他对个人和家庭的补助", "", "", "", ""],
    ]
    tbl = _make_parsed_table(
        rows,
        table_code="FIN_06_basic_expenditure",
        title="一般公共预算财政拨款基本支出决算表",
        page=1,
        header_rows=1,
        frag_name="frag_t6",
    )
    instance = LogicalTableInstance("inst_t6", "FIN_06_basic_expenditure", tbl.title, ["frag_t6"], 1, 1, org_name="某单位", fiscal_year=2024)
    artifact = ParseArtifact(source_file_hash=HASH, parse_run_uid="run", table_fragments=[tbl], physical_fragments=[tbl], logical_tables=[instance])
    ctx = build_fact_context_from_artifact(artifact, org_name="某单位", fiscal_year=2024, report_kind="final", doc_type="dept_final")
    p1 = (
        "一般公共预算财政拨款基本支出决算表\n"
        "人员经费合计 10.00 公用经费合计 5.00\n"
        "30311 其他对个人和家庭的补助 30201 办公费 5.00\n"
        "30311 其他对个人和家庭的补助\n"
    )
    doc = build_document("t6_dup.pdf", [p1], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, ctx
    doc._economic_duplicate_row_observations = [{"subject_code": "30311", "subject_name": "其他对个人和家庭的补助"}]

    class _Rule:
        code = "V33-243"

        def _issue(self, msg, loc, severity="low", **kw):
            return SimpleNamespace(rule_id=self.code, message=msg, location=loc, severity=severity, **kw)

    try:
        issues = check_basic_economics(_Rule(), doc)
    except RuleDeferred as exc:
        issues = list(exc.partial_issues)
    dup_issue = next(i for i in issues if "重复科目行" in i.message)
    assert dup_issue.location["table_refs"][0]["page"] == 1
    assert "pos" in dup_issue.location["table_refs"][0]
    res = {"rule_findings": [_issue_to_payload(dup_issue)]}
    bind_rule_sources(res, page_texts=[p1], source_file_hash=HASH, artifact=artifact, fact_context=ctx)
    assert res["rule_findings"][0].get("evidence")
    assert all(e["source_identity_status"] == "verified" for e in res["rule_findings"][0]["evidence"])


def test_step5_functional_narrative_parens_code_nested_and_fallback():
    """Step 5: FIN_05 含（室）、(类代码)(款代码)、其中：嵌套项，以及仅全空时的二级/一级回退。"""
    table_entries = {
        "201": {"level": "类", "name": "一般公共服务支出"},
        "20103": {"level": "款", "name": "政府办公厅（室）及相关机构事务"},
        "2010301": {"level": "项", "name": "行政运行"},
        "208": {"level": "类", "name": "社会保障和就业支出"},
        "20805": {"level": "款", "name": "行政事业单位养老支出"},
        "2080502": {"level": "项", "name": "事业单位离退休"},
        "2080505": {"level": "项", "name": "机关事业单位基本养老保险缴费支出"},
    }
    p_nested = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（二）一般公共预算财政拨款支出决算具体情况。\n"
        "1、一般公共服务支出（类）政府办公厅（室）及相关机构事务（款）行政运行（项）。决算100.00万元。\n"
        "2、“社会保障和就业支出（类代码 208）行政事业单位养老支出（款代码 05）”2024年度决算50.00万元，"
        "其中：“事业单位离退休（项代码 02）”决算10.00万元；"
        "“机关事业单位基本养老保险缴费支出（项代码 05）”决算40.00万元。\n"
    )
    doc = build_document("fin05_nested.pdf", [p_nested], [[]], 0)
    mentions = _extract_final_functional_narrative_mentions(doc, table_entries)
    assert "2010301" in mentions
    assert "2080502" in mentions
    assert "2080505" in mentions
    assert mentions["20103"][0]["name"] == "政府办公厅（室）及相关机构事务"

    # 仅二级标题无项级明细时回退（e35c4c25 口径）
    p_two = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1. 社会保障和就业支出（类代码 208）行政事业单位养老支出（款代码 05）2024年年初预算50.00万元。\n"
    )
    doc_two = build_document("fin05_two.pdf", [p_two], [[]], 0)
    m_two = _extract_final_functional_narrative_mentions(doc_two, table_entries)
    assert "208" in m_two and "20805" in m_two

    # 反例：若已有项级条目，未带“其中”的纯二级标题不混入兜底污染项级列表
    doc_mixed = build_document(
        "fin05_mixed.pdf",
        [
            "五、一般公共预算财政拨款支出决算情况说明\n"
            "1、一般公共服务支出（类）政府办公厅（室）及相关机构事务（款）行政运行（项）。\n"
            "2、社会保障和就业支出（类代码 208）行政事业单位养老支出（款代码 05）说明。"
        ],
        [[]],
        0,
    )
    m_mixed = _extract_final_functional_narrative_mentions(doc_mixed, table_entries)
    assert "2010301" in m_mixed and "20805" not in m_mixed


def test_step6_cover_org_name_punctuation_and_wrapped_paren_fragment():
    """Step 6: ASCII/破折号/顿号归一及跨行括号碎片不抢正文名；真错名仍报。"""
    pages_ok = [
        "上海市某某区青少年服务中心—活动站（中心本级）\n2024 年度单位决算\n",
        "第二部分 决算表\n2024 年度一般公共预算财政拨款支出决算表\n"
        "编制单位：上海市某某区青少年服务中心--活动站(中心本级)\n",
        "第一部分\n上海市某某区青少年服务中心—活动站（中心本级）\n概\n况\n"
        "第一部分（上海市机关事务行政服务中心）\n"
        "第三部分 上海市某某区青少年服务中心—活动站（中心本级） 2024 年度单位决算情况说明\n",
    ]
    doc_ok = build_document("cover_ok.pdf", pages_ok, [[]], 0)
    doc_ok.dominant_year = 2024
    doc_ok.report_kind = "final"
    doc_ok.document_profile = DocumentProfile(writing_scope={"cover_independent_scope": "unit"})
    assert _independent_body_name(doc_ok) == "上海市某某区青少年服务中心—活动站（中心本级）"
    issues_ok = CMM019_CoverElements().apply(doc_ok)
    assert all("主体名称与封面" not in i.message and "未出现单位全称" not in i.message for i in issues_ok)

    pages_bad = [
        "上海市甲单位\n2024 年度单位决算\n",
        "第二部分 决算表\n2024 年度一般公共预算财政拨款支出决算表\n编制单位：上海市乙单位\n",
        "第一部分\n上海市乙单位\n概况\n"
        "第三部分 上海市乙单位 2024 年度单位决算情况说明\n",
    ]
    doc_bad = build_document("cover_bad.pdf", pages_bad, [[]], 0)
    doc_bad.dominant_year = 2024
    doc_bad.report_kind = "final"
    doc_bad.document_profile = DocumentProfile(writing_scope={"cover_independent_scope": "unit"})
    issues_bad = CMM019_CoverElements().apply(doc_bad)
    assert any("未出现单位全称" in i.message or "主体名称" in i.message for i in issues_bad)


def test_step7_evidence_binding_complete_for_v33_107_214_245_repeat():
    """Step 7: V33-107 / V33-214 / V33-245 / V33-NARRATIVE-INDICATOR-REPEAT 完整证据绑定。"""
    # V33-107: 跨页基本支出说明引用真实绑定到 P3，而非 FIN_06 表页 P1
    t6_rows = [
        ["经济分类科目编码", "科目名称", "决算数", "经济分类科目编码", "科目名称", "决算数"],
        ["人员经费合计", "", "80.00", "公用经费合计", "", "20.00"],
        ["301", "工资福利支出", "80.00", "302", "商品和服务支出", "20.00"],
    ]
    tbl_t6 = _make_parsed_table(
        t6_rows,
        table_code="FIN_06_basic_expenditure",
        title="一般公共预算财政拨款基本支出决算表",
        page=1,
        header_rows=1,
        frag_name="frag_v107",
    )
    p1_t6 = "一般公共预算财政拨款基本支出决算表\n人员经费合计 80.00 公用经费合计 20.00"
    p2_sec6 = "六、一般公共预算财政拨款基本支出决算情况说明\n"
    p3_body = "本部门2024年度基本支出合计150.00万元。"
    inst_t6 = LogicalTableInstance("inst_t6", "FIN_06_basic_expenditure", tbl_t6.title, ["frag_v107"], 1, 1, org_name="某单位", fiscal_year=2024)
    art_t6 = ParseArtifact(source_file_hash=HASH, parse_run_uid="run", table_fragments=[tbl_t6], physical_fragments=[tbl_t6], logical_tables=[inst_t6])
    ctx_t6 = build_fact_context_from_artifact(art_t6, org_name="某单位", fiscal_year=2024, report_kind="final", doc_type="dept_final")
    doc_107 = build_document("v107.pdf", [p1_t6, p2_sec6, p3_body], [[t6_rows], [], []], 0)
    doc_107.parse_artifact, doc_107.fact_context = art_t6, ctx_t6
    issues_107 = R33107_BasicExpense_Check().apply(doc_107)
    assert issues_107
    res_107 = {"rule_findings": [_issue_to_payload(issues_107[0])]}
    bind_rule_sources(res_107, page_texts=[p1_t6, p2_sec6, p3_body], source_file_hash=HASH, artifact=art_t6, fact_context=ctx_t6)
    assert res_107["rule_findings"][0]["location"]["table_refs"][0]["page"] == 3
    assert res_107["rule_findings"][0].get("evidence")
    assert all(e["source_identity_status"] == "verified" for e in res_107["rule_findings"][0]["evidence"])

    # V33-214: operand_refs + structured_consumption 完整绑定
    t1_rows = [
        ["本年收入", None, "本年支出", None],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "100.00", "本年支出合计", "110.00"],
        ["收入总计", "100.00", "支出总计", "110.00"],
    ]
    tbl_t1 = _make_parsed_table(
        t1_rows,
        table_code="FIN_01_income_expenditure_total",
        title="收入支出决算总表",
        page=1,
        header_rows=2,
        frag_name="frag_v214",
    )
    inst_t1 = LogicalTableInstance("inst_t1", "FIN_01_income_expenditure_total", tbl_t1.title, ["frag_v214"], 1, 1, org_name="某单位", fiscal_year=2024)
    art_t1 = ParseArtifact(source_file_hash=HASH, parse_run_uid="run", table_fragments=[tbl_t1], physical_fragments=[tbl_t1], logical_tables=[inst_t1])
    ctx_t1 = build_fact_context_from_artifact(art_t1, org_name="某单位", fiscal_year=2024, report_kind="final", doc_type="dept_final")
    doc_214 = build_document("v214.pdf", ["收入支出决算总表\n收入总计 100.00 支出总计 110.00"], [[t1_rows]], 0)
    doc_214.parse_artifact, doc_214.fact_context = art_t1, ctx_t1
    doc_214.structured_consumption = []
    issues_214 = R33214_T1_TotalBalance().apply(doc_214)
    assert len(issues_214) == 1
    assert doc_214.structured_consumption
    res_214 = {"rule_findings": [_issue_to_payload(issues_214[0])]}
    bind_rule_sources(
        res_214,
        page_texts=doc_214.page_texts,
        source_file_hash=HASH,
        artifact=art_t1,
        fact_context=ctx_t1,
        consumption=doc_214.structured_consumption,
    )
    assert res_214["rule_findings"][0].get("evidence")
    assert all(e["source_identity_status"] == "verified" for e in res_214["rule_findings"][0]["evidence"])

    # V33-245: 同页重复短句靠 context 消歧
    p_245 = (
        "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n"
        "公务用车运行维护费决算10.00万元，与上年决算持平，比年初预算增长 14.93%。\n"
        "因公出国（境）费支出与上年持平，比预算持平。"
        "另：公务接待费增长 14.93%。\n"
    )
    doc_245 = build_document("v245.pdf", [p_245], [[]], 0)
    doc_245.dominant_year = 2024
    issues_245 = R33245_ThreePublicDirectionContradiction().apply(doc_245)
    assert issues_245
    res_245 = {"rule_findings": [_issue_to_payload(issues_245[0])]}
    bind_rule_sources(res_245, page_texts=[p_245], source_file_hash=HASH)
    assert res_245["rule_findings"][0].get("evidence")
    assert all(e["source_identity_status"] == "verified" for e in res_245["rule_findings"][0]["evidence"])

    # V33-NARRATIVE-INDICATOR-REPEAT: 同页同短句通过 pos 消歧
    p_rep = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）91.00万元，支出决算为89.46万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）事业单位离退休（项）89.46万元，支出决算为89.46万元。\n"
    )
    doc_rep = build_document("vrep.pdf", [p_rep], [[]], 0)
    doc_rep.dominant_year = 2024
    issues_rep = R33NarrativeIndicatorRepeat().apply(doc_rep)
    assert issues_rep
    trefs = issues_rep[0].location["table_refs"]
    assert all(isinstance(ref.get("pos"), int) for ref in trefs)
    res_rep = {"rule_findings": [_issue_to_payload(issues_rep[0])]}
    bind_rule_sources(res_rep, page_texts=[p_rep], source_file_hash=HASH)
    assert res_rep["rule_findings"][0].get("evidence")
    assert all(e["source_identity_status"] == "verified" for e in res_rep["rule_findings"][0]["evidence"])
    assert re.search(r"89\.46|91\.00", str(res_rep["rule_findings"][0]["evidence"]))
