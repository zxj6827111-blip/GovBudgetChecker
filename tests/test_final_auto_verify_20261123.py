"""2026-11-23 文字版决算自动审校独立核查与修复回归测试。

覆盖三类核心修复：
1. 原件清楚但系统未核对（跨页 1 字科目尾名合并、无表头续表 7→5/7→3 与双栏总表续页、
   合署办公括号封面匹配、零基数同比确定性报错、100% 项目支出零基本支出对称豁免、
   视觉行基线偏移 >4.5pt 重排）；
2. 局部异常阻断解耦与同根因人工待办归并（V33-005 决算准入收敛、T6 合计行直证解耦
   OBL-CROSS-T5-T6 / OBL-TXT-T6、同页同表阻断归并为单条待办）；
3. 后台与网页投影一致性（折行小数拼接坐标对齐、目录续页跳过、显式页码提取与第1页锚定、
   机器字段名与 Python 字典串中文化）。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.checklist_identity_rules import CMM019_CoverElements
from src.engine.common_rules import _cmm007_canon
from src.engine.native_narrative_checks import check_narrative_relations
from src.engine.rule_identity import effective_legacy_table_inputs
from src.engine.rules_v33 import Rule, build_document
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.pdf_page_extract import _rejoin_wrapped_decimals, extract_aligned_page_text_and_chars
from src.services.pdf_parser import PDFParser
from src.services.template_scope import cover_matches_selected_org
from src.services.unfinished_check_groups import _is_toc_page, build_unfinished_check_groups


def _make_table(code: str, page: int, rows: list[list[str]], header: int = 0, title: str = "") -> ParsedTable:
    widths = [70.0] * max((len(r) for r in rows), default=1)
    cells = []
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            x0 = sum(widths[:c_idx])
            x1 = x0 + widths[c_idx]
            cells.append(
                ParsedCell(
                    r_idx,
                    c_idx,
                    str(val),
                    str(val),
                    None,
                    page,
                    (x0, 100.0 + r_idx * 18.0, x1, 118.0 + r_idx * 18.0),
                    r_idx < header,
                    0.96,
                    "万元",
                    "lines",
                )
            )
    return ParsedTable(
        table_code=code,
        title=title,
        page_number=page,
        table_index=0,
        bbox=(0.0, 100.0, sum(widths), 100.0 + 18.0 * len(rows)),
        header_row_count=header,
        confidence=0.95,
        extraction_method="lines",
        unit_hint="万元",
        rows=rows,
        cells=cells,
        fragment_uid=f"p{page}-{code}",
    )


def test_narrative_page_uses_5_2pt_tolerance_while_table_page_keeps_4_5pt():
    """无表说明页放宽至 y_tolerance=5.2 合并西文字体微偏数字，含表页保持 4.5 防止跨行粘连。"""
    seen_tolerances: list[tuple[str, float]] = []

    class FakeNarrativePage:
        def find_tables(self, **kwargs):
            return []

        def extract_text(self, y_tolerance=3.0):
            seen_tolerances.append(("narrative", y_tolerance))
            return "本年收入合计 835,571.40 万元。" if y_tolerance >= 5.0 else "本年收入合计 万元。\n835,571.40"

    class FakeTablePage:
        def find_tables(self, **kwargs):
            return [object()]

        def extract_text(self, y_tolerance=3.0):
            seen_tolerances.append(("table", y_tolerance))
            return "220 自然资源支出 47,846.16"

    nar_text, nar_chars = extract_aligned_page_text_and_chars(FakeNarrativePage())
    tbl_text, tbl_chars = extract_aligned_page_text_and_chars(FakeTablePage())
    assert nar_chars is None and tbl_chars is None
    assert "本年收入合计 835,571.40 万元。" == nar_text
    assert "47,846.16" in tbl_text
    assert seen_tolerances == [("narrative", 5.2), ("table", 4.5)]


def test_rejoin_wrapped_decimals_preserves_compact_nonspace_alignment():
    """折行小数 83557.1\\n4 合并后，非空白字符序列与坐标数组逐字对齐，后续偏移不漂移。"""
    raw_text = "收入合计 83557.1\n4 万元，标点。；错误。"
    raw_chars = [
        {"text": ch, "x0": float(i), "x1": float(i + 1), "top": 10.0, "bottom": 20.0}
        for i, ch in enumerate(raw_text)
        if not ch.isspace()
    ]
    joined_text = _rejoin_wrapped_decimals(raw_text)
    assert "83557.14 万元" in joined_text
    compact_chars_from_joined = [ch for ch in joined_text if not ch.isspace()]
    assert len(compact_chars_from_joined) == len(raw_chars)
    idx = "".join(compact_chars_from_joined).index("。；")
    assert "".join(c["text"] for c in raw_chars[idx : idx + 2]) == "。；"


def test_single_char_wrapped_subject_tail_and_functional_continuations():
    """曹杨街道 1 字尾名“出”与信访办 7->5 续页可识别（只放行有序列证据的续页）。"""
    parser = PDFParser(None)
    prev_fin02 = _make_table(
        "FIN_02_income",
        8,
        [
            ["科目编码", "科目名称", "本年收入合计", "财政拨款收入", "其他收入"],
            ["20799", "其他文化旅游体育与传媒支出", "120.00", "120.00", ""],
            ["2079999", "其他文化旅游体育与传媒支", "120.00", "120.00", ""],
        ],
        header=1,
        title="收入决算表",
    )
    # 1. 页首孤立 1 字尾名“出”，后续为同类下一款/类（原先 {2,12} 的长度下限会把它拒掉）
    tail_frag = _make_table(
        "UNKNOWN_P9_T0",
        9,
        [
            ["", "出", "", "", ""],
            ["208", "社会保障和就业支出", "300.00", "300.00", ""],
            ["20805", "行政事业单位养老支出", "300.00", "300.00", ""],
        ],
    )
    assert parser._functional_name_tail_row(prev_fin02, tail_frag, require_proof=False) is True
    # 尾名含数字或金额列非空时仍拒绝，避免把金额行当成名称尾字
    bad_tail = _make_table(
        "UNKNOWN_P9_T0",
        9,
        [
            ["", "出", "5.00", "", ""],
            ["208", "社会保障和就业支出", "300.00", "300.00", ""],
            ["20805", "行政事业单位养老支出", "300.00", "300.00", ""],
        ],
    )
    assert parser._functional_name_tail_row(prev_fin02, bad_tail, require_proof=False) is False

    # 2. 7 位项（2101101）后接无表头续页 5 位同大类款（21099）
    prev_7 = _make_table(
        "FIN_02_income",
        7,
        [
            ["科目编码", "科目名称", "本年收入合计", "财政拨款收入", "其他收入"],
            ["210", "卫生健康支出", "65.00", "65.00", ""],
            ["21011", "行政事业单位医疗", "55.00", "55.00", ""],
            ["2101101", "行政单位医疗", "55.00", "55.00", ""],
        ],
        header=1,
        title="收入决算表",
    )
    frag_7_to_5 = _make_table(
        "UNKNOWN_P8_T0",
        8,
        [
            ["21099", "其他卫生健康支出", "10.00", "10.00", ""],
            ["2109999", "其他卫生健康支出", "10.00", "10.00", ""],
        ],
    )
    assert parser._continuation_row_sequence_evidence(prev_7, frag_7_to_5) is True
    # 7 位后接不同大类且非递增的 5 位款（20101 < 210 类），不得作为续页证据
    frag_bad = _make_table(
        "UNKNOWN_P8_T0",
        8,
        [["20101", "人大事务", "10.00", "10.00", ""]],
    )
    assert parser._continuation_row_sequence_evidence(prev_7, frag_bad) is False


def test_cover_org_co_agency_parenthetical_and_cid_cover_deferral():
    """合署办公括号单位名视为一致，下属执法总队仍判不一致；(cid:) 乱码封面延期而非伪造缺单位警告。"""
    assert cover_matches_selected_org("上海市水务局（上海市海洋局）", "上海市水务局") is True
    assert cover_matches_selected_org("上海市文化和旅游局执法总队", "上海市文化和旅游局") is False

    cid_cover = "(cid:120)(cid:121)(cid:122)(cid:123)(cid:124)(cid:125)\n2022年度单位决算"
    doc = build_document("/tmp/cid_cover.pdf", [cid_cover], [[]], 100)
    doc.report_kind = "final"
    doc.dominant_year = 2022
    doc.org_name = "上海市普陀区市容管理中心"
    from src.engine.rule_outcome import RuleDeferred

    with pytest.raises(RuleDeferred):
        CMM019_CoverElements().apply(doc)


def test_zero_base_yoy_emits_formal_error_and_cmm007_strips_leading_niandu():
    """上年为 0 且本年增加 >0 却写“增长100.00%”时直接报正式错误，不留人工待办。"""

    class DummyRule(Rule):
        code, severity = "V33-234", "error"

        def apply(self, doc):
            return []

    page = (
        "第三部分 2024年度部门决算情况说明\n"
        "一、收入支出决算总体情况说明\n"
        "2024年度收入支出总计1,250.00万元。与2023年度相比，收入支出总计增加1,250.00万元，增长100.00%。\n"
        "二、收入决算情况说明\n"
        "本年收入合计1,250.00万元。\n"
    )
    doc = build_document("/tmp/yoy_zero.pdf", ["2024年度部门决算", page], [[], []], 100)
    doc.report_kind = "final"
    doc.dominant_year = 2024
    doc.fact_receipts = []
    issues, unresolved = check_narrative_relations(DummyRule(), doc)
    zero_base = [issue for issue in issues if "零基数" in issue.message]
    assert len(zero_base) == 1
    assert zero_base[0].location.get("zero_base") is True
    assert zero_base[0].location.get("table_refs")
    assert not any("有效分母" in item for item in unresolved)
    assert _cmm007_canon("年度财政拨款收入支出总计") == _cmm007_canon("财政拨款收入支出总计")


def test_v33_005_final_legacy_inputs_exclude_fin06_and_fin07():
    """决算模式下 V33-005 仅以五张功能分类明细表为准入范围，不被 T6/T7 局部缺口连带阻断。"""
    doc = SimpleNamespace(report_kind="final")
    inputs = set(effective_legacy_table_inputs("V33-005", doc))
    assert "FIN_06_basic_expenditure" not in inputs
    assert "FIN_07_three_public" not in inputs
    assert {"FIN_02_income", "FIN_03_expenditure", "FIN_05_general_public_expenditure"} <= inputs


def test_unfinished_groups_merge_same_blocked_table_skip_toc_p3_and_clean_raw_codes():
    """同页同表阻断归并为单条待办、跳过第 3 页目录续页、消除 FIN_0x 与 Python 字典串泄漏。"""
    checks = [
        {
            "id": "OBL-STRUCT-NUMBER-VALIDITY",
            "record_type": "unfinished_check",
            "title": "数值单元格可解析性",
            "detail": "来源表映射/读取未完成：第13页FIN_06_basic_expenditure {'rule_admission_blocked': 65}；结构：阻断 第13页FIN_06_basic_expenditure",
            "reason_codes": ["table_mapping_incomplete"],
            "blocked_by": [{"ref": "第13页FIN_06_basic_expenditure", "reason_code": "table_mapping_incomplete"}],
        },
        {
            "id": "OBL-INTERNAL-BASIC-EXPENSE",
            "record_type": "unfinished_check",
            "title": "基本支出 = 人员经费 + 公用经费",
            "detail": "第4列（人员经费合计）：经济分类表第2行科目编码无法唯一识别，明细归集未完成",
            "reason_codes": ["row_level_unresolved"],
            "missing_operands": [
                {
                    "page": 13,
                    "table_code": "FIN_06_basic_expenditure",
                    "row_key": "人员经费合计",
                    "column": "col_4",
                    "role": "第4列（人员经费合计）科目行身份",
                }
            ],
        },
        {
            "id": "OBL-CROSS-SAN-GONG-ECON",
            "record_type": "unfinished_check",
            "title": "三公经费表 与 财政拨款经济分类表 资金来源一致性",
            "detail": "第8列（公用经费合计）：经济分类表第2行科目编码无法唯一识别，明细归集未完成",
            "reason_codes": ["row_level_unresolved"],
            "missing_operands": [
                {
                    "page": 13,
                    "table_code": "FIN_06_basic_expenditure",
                    "row_key": "公用经费合计",
                    "column": "col_8",
                    "role": "第8列（公用经费合计）科目行身份",
                }
            ],
        },
        {
            "id": "OBL-TXT-T3",
            "record_type": "unfinished_check",
            "title": "支出决算表 与 支出决算说明一致",
            "detail": "支出决算表(第10页)金额单位未知，无法安全换算比较",
            "reason_codes": ["unit_unknown"],
        },
        {
            "id": "OBL-PROFILE-KIND-CONFLICT",
            "record_type": "unfinished_check",
            "title": "文种冲突人工确认",
            "detail": "文件名含“预算”而封面写“2025年度决算”",
            "reason_codes": ["profile_conflict"],
        },
    ]
    toc_p2 = "目 录\n第一部分 部门概况\n第二部分 2024年度部门决算表\n一、收入支出决算总表"
    toc_p3 = (
        "第三部分 2024年度部门决算情况说明\n"
        "一、收入支出决算总体情况说明\n"
        "二、收入决算情况说明\n"
        "三、支出决算情况说明\n"
        "四、财政拨款收入支出决算总体情况说明\n"
        "第四部分 名词解释"
    )
    assert _is_toc_page(toc_p3, 3) is True
    page_texts = ["2024年度部门决算", toc_p2, toc_p3] + [""] * 15
    page_texts[9] = "支出决算表\n项目 本年支出合计 100.00"
    page_texts[12] = "一般公共预算财政拨款基本支出决算表\n301 工资福利支出 50.00"

    groups = build_unfinished_check_groups(
        checks,
        {
            "FIN_03_expenditure": "支出决算表",
            "FIN_06_basic_expenditure": "一般公共预算财政拨款基本支出决算表",
        },
        page_texts=page_texts,
        blocking_obligation_ids=[c["id"] for c in checks],
    )
    assert len(groups) == 3
    by_page = {g["page"]: g for g in groups}
    assert set(by_page) == {1, 10, 13}
    assert set(by_page[13]["check_ids"]) == {
        "OBL-STRUCT-NUMBER-VALIDITY",
        "OBL-INTERNAL-BASIC-EXPENSE",
        "OBL-CROSS-SAN-GONG-ECON",
    }
    assert by_page[13]["title"] == "基本支出经济分类编码待核对"
    assert "未标注金额单位" in by_page[10]["plain_reason"]
    for g in groups:
        text_blob = f"{g['title']} | {g['plain_reason']} | {g['action_hint']}"
        assert "FIN_0" not in text_blob
        assert "{'rule_admission_blocked'" not in text_blob
