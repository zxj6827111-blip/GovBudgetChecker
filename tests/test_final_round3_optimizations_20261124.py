"""Round 3 batch-post-audit optimizations (2026-11-24) unit & negative-control tests."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from src.engine.native_narrative_checks import _foreign_owner
from src.engine.native_three_public_ratios import _SUBJECT
from src.engine.three_public_disclosure_policy import movement_is_explicit_zero
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.check_subject import resolve_check_subject
from src.services.fiscal_column_identity import canonical_column_mappings, header_paths
from src.services.fiscal_subject_names import subject_name_candidates
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text
from src.services.pdf_parser import PDFParser
from src.services.rule_source_binding import _native_span
from src.services.template_scope import cover_matches_selected_org


def test_template_parenthesized_zero_movement_is_explicit_zero():
    assert movement_is_explicit_zero("公务接待费支出决算减少（增加）0 万元")
    assert movement_is_explicit_zero("下降（增长）0%")
    assert movement_is_explicit_zero("增加(减少)0.00万元")
    # Genuine non-zero or directional-to-zero movement must remain False
    assert not movement_is_explicit_zero("公务接待费支出决算减少（增加）1.20 万元")
    assert not movement_is_explicit_zero("公务接待费支出决算减少为0万元")


def test_native_span_disambiguates_repeated_quote_via_pos_only_when_exact():
    page_text = (
        "1. 因公出国（境）费支出决算减少（增加）0 万元，下降（增长）0%；\n"
        "2. 公务用车购置及运行费支出决算减少（增加）0 万元，下降（增长）0%；\n"
        "3. 公务接待费支出决算减少（增加）0 万元，下降（增长）0%；"
    )
    digest = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
    quote = "支出决算减少（增加）0 万元，下降（增长）0%；"
    # Without pos or context, duplicate quote still fails closed
    assert _native_span(quote, 1, [page_text], digest) is None
    # With exact pos inside the 2nd occurrence, expands to unique verbatim span
    second_pos = page_text.index("公务用车") + len("公务用车购置及运行费")
    bound = _native_span(quote, 1, [page_text], digest, pos=second_pos)
    assert bound is not None
    assert bound["source_identity_status"] == "verified"
    assert "公务用车" in bound["quote"]
    assert page_text.count(bound["quote"]) == 1


def test_legacy_subject_aliases_and_founder_zhi_glyph():
    pool = {
        "2100000": "卫生健康支出",
        "2200000": "自然资源海洋气象等支出",
    }
    assert subject_name_candidates("医疗卫生支出", pool) == ["2100000"]
    assert subject_name_candidates("国土海洋气象等支出", pool) == ["2200000"]
    # Founder GBK variant 臵 / 罝
    assert normalize_text("公务用车购臵费") == "公务用车购置费"
    m1 = _SUBJECT.search("公务用车购臵及运行维护费支出26.92万元")
    assert m1 is not None and m1.lastgroup == "vehicle_purchase_operation"
    m2 = _SUBJECT.search("公务用车购臵支出为0.00万元")
    assert m2 is not None and m2.lastgroup == "vehicle_purchase"


def test_three_row_header_leaf_does_not_leak_horizontally_into_neighbor_column():
    rows = [
        ["项目", None, "本年支出合计", "基本支出", "项目支出", "上缴上级支出", "经营支出", ""],
        [None, None, None, None, None, None, None, "对附属单位"],
        ["功能分类\n科目编码", "科目名称", "", "", "", "", "", "补助支出"],
        ["合计", None, "100.00", "40.00", "60.00", "", "", ""],
    ]
    cells = [
        ParsedCell(r, c, str(v or ""), normalize_text(str(v or "")), None, 1,
                   (c * 60, r * 20, (c + 1) * 60, (r + 1) * 20), r < 3, 0.99, "万元", "lines")
        for r, row in enumerate(rows)
        for c, v in enumerate(row)
    ]
    frag = ParsedTable(
        "FIN_03_expenditure", "支出决算表", 1, 0, (0, 0, 480, 80), 3, 0.99, "lines", "万元", rows, cells, "f3"
    )
    paths = header_paths(frag)
    assert paths[6] == ["经营支出", "", ""]
    mappings = {
        m["canonical_measure"]: m["source_col_idx"]
        for m in canonical_column_mappings(frag, NINE_TABLE_RULES["FIN_03_expenditure"])
    }
    assert mappings["operational_actual"] == 6
    assert mappings["subsidiary_actual"] == 7


def test_clipped_subsidiary_and_kindergarten_education_income_headers_bind():
    rows = [
        ["项目", None, "本年支出合计", "基本支出", "项目支出", "上缴上级支出", "经营支出", "对附属单 补\n助支出"],
        ["功能分类科目编码", "科目名称", None, None, None, None, None, None],
        ["合计", None, "10.00", "10.00", "", "", "", ""],
    ]
    cells = [
        ParsedCell(r, c, str(v or ""), normalize_text(str(v or "")), None, 1,
                   (c * 60, r * 20, (c + 1) * 60, (r + 1) * 20), r < 2, 0.99, "万元", "lines")
        for r, row in enumerate(rows)
        for c, v in enumerate(row)
    ]
    frag = ParsedTable(
        "FIN_03_expenditure", "支出决算表", 1, 0, (0, 0, 480, 60), 2, 0.99, "lines", "万元", rows, cells, "f3b"
    )
    mappings = {
        m["canonical_measure"]: m["source_col_idx"]
        for m in canonical_column_mappings(frag, NINE_TABLE_RULES["FIN_03_expenditure"])
    }
    assert mappings["subsidiary_actual"] == 7


def test_wps_double_border_rect_does_not_unverify_blank_cell_while_inner_mark_still_blocks():
    cell = ParsedCell(2, 4, "", "", None, 9, (482.42, 204.48, 560.59, 225.68), False, 0.99, "万元", "lines")
    page = SimpleNamespace(
        chars=[{"text": "9", "x0": 100.0, "top": 210.0, "x1": 106.0, "bottom": 220.0}],
        images=[],
        curves=[],
        lines=[],
        rects=[
            {
                "x0": 483.55,
                "top": 203.81,
                "x1": 484.51,
                "bottom": 226.37,
                "fill": True,
                "non_stroking_color": 0.0,
            }
        ],
    )
    PDFParser._certify_native_blank_cells(page, [cell], "lines")
    assert cell.native_blank_verified is True

    # Interior horizontal bar (e.g. vector dash inside cell) must STILL block blank certification
    cell_with_dash = ParsedCell(2, 4, "", "", None, 9, (482.42, 204.48, 560.59, 225.68), False, 0.99, "万元", "lines")
    page.rects.append(
        {
            "x0": 515.0,
            "top": 214.5,
            "x1": 525.0,
            "bottom": 215.5,
            "fill": True,
            "non_stroking_color": 0.0,
        }
    )
    PDFParser._certify_native_blank_cells(page, [cell_with_dash], "lines")
    assert cell_with_dash.native_blank_verified is False


def test_head_unit_parentheses_and_self_shorthand_owner_guards():
    assert cover_matches_selected_org("上海市普陀区住房保障和房屋管理局（本部）", "上海市普陀区住房保障和房屋管理局本级") is True
    subj = resolve_check_subject(
        organization_id="org-1",
        organization_name="上海市普陀区住房保障和房屋管理局本级",
        organization_level="unit",
        cover_name="上海市普陀区住房保障和房屋管理局(本部)",
        cover_text="上海市普陀区住房保障和房屋管理局（本部）2023年度单位决算",
    )
    assert subj["conflict"] is None
    assert subj["source"] == "verified_cover_alias"

    assert not _foreign_owner("上海市体育局（本级）2022年度收入支出总计100万元", "上海市体育局本级")
    assert not _foreign_owner("上海市绿化和市容管理局（本级）2022年度收入支出总计100万元", "上海市绿化和市容管理局本部")
    assert not _foreign_owner("上海市经济和信息化委员会2022年度收入支出总计100万元", "上海市经济和信息化委员会本级")
    assert not _foreign_owner("乡村振兴中心2022年度收入支出总计100万元", "上海市乡村振兴研究中心")
    assert not _foreign_owner("上海临床研究中心2022年度收入支出总计100万元", "上海市临床研究中心")

    # Strict negative controls: parent agency and sibling agency MUST remain foreign
    assert _foreign_owner("上海市水务局2022年度收入支出总计100万元", "上海市水务局行政服务中心")
    assert _foreign_owner("上海市普陀区财政局2022年度收入支出总计100万元", "上海市普陀区教育局")
    assert _foreign_owner("上海市普陀区财政事务中心2022年度收入支出总计100万元", "上海市普陀区教育事务中心")
