from types import SimpleNamespace
from copy import deepcopy

import pytest

from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import resolve_native_table_candidates


def table(columns: int, profile=None, filled_every: int = 1) -> ParsedTable:
    """构造候选表；filled_every>1 用于模拟重复框线把一列拆成多列后的空子列。

    拆列后文字只落在其中一个子列，其余子列为空——这是真实的 IR-11 形态。
    旧夹具用“每列都填同一个数”来模拟，那恰好把“不同列金额相同”当成了缺陷，
    与政府报表的实际版式相反。
    """
    rows = [
        [f"c{index}" for index in range(columns)],
        ["1" if index % filled_every == 0 else "" for index in range(columns)],
    ]
    width = 270
    step = width / columns
    cells = [
        ParsedCell(
            row, col, text, text, float(text) if text.isdigit() else None, 1,
            (col * step, row * 10, (col + 1) * step, (row + 1) * 10),
            row == 0, 0.98, None, "lines",
        )
        for row, values in enumerate(rows)
        for col, text in enumerate(values)
    ]
    return ParsedTable(
        table_code="FIN_02_income",
        title="收入决算表",
        page_number=1,
        table_index=0,
        bbox=(0, 0, width, 20),
        header_row_count=1,
        confidence=0.98,
        extraction_method="lines",
        unit_hint=None,
        rows=rows,
        cells=cells,
        fragment_uid=f"t{columns}-{profile or 'default'}",
        geometry_profile=profile,
    )


def test_vertical_line_clusters_merge_only_close_lines():
    page = SimpleNamespace(edges=[
        {"orientation": "v", "x0": 0},
        {"orientation": "v", "x0": 0.5},
        {"orientation": "v", "x0": 10},
        {"orientation": "v", "x0": 16.1},
        {"orientation": "h", "x0": 4},
    ])
    assert PDFParser._vertical_line_clusters(page) == [0.0, 10.0, 16.1]


def test_structure_priority_prefers_the_unsplit_grid():
    """重复框线拆出的 27 列带 18 个空子列，结构分应低于干净的 9 列。"""
    parser = PDFParser(None)
    assert parser._structure_priority(table(9)) > parser._structure_priority(table(27, filled_every=3))


def test_tie_breaker_selects_snap6_candidate_for_same_priority():
    parser = PDFParser(None)
    snap3 = table(27, filled_every=3)
    snap6 = table(9, profile="snap6")
    assert parser._table_priority(snap3) == parser._table_priority(snap6)
    merged = resolve_native_table_candidates(parser, [snap3], [snap6])
    assert len(merged) == 1
    assert merged[0].geometry_profile == "snap6"
    assert len(merged[0].rows[0]) == 9


def test_equal_amounts_in_different_columns_are_not_treated_as_duplicates():
    """两个来源列印相同金额是常态，不得因此扣分而偏向漏列候选。"""
    parser = PDFParser(None)
    full = table(9)
    lost = table(8)
    assert parser._structure_priority(lost) <= parser._structure_priority(full)


def test_the_same_source_token_claimed_twice_is_penalised():
    """同一个原页字符 token 被两个金额格认领，才是真正的重复抽取。"""
    parser = PDFParser(None)
    duplicated = table(3)
    for cell in duplicated.cells:
        if cell.numeric_value is not None:
            cell.native_text_sources = [
                {"physical_page": 1, "start_char_index": 10, "end_char_index": 11, "text": "1"}
            ]
    clean = table(3)
    for cell in clean.cells:
        if cell.numeric_value is not None:
            cell.native_text_sources = [
                {"physical_page": 1,
                 "start_char_index": 10 + cell.col_idx,
                 "end_char_index": 11 + cell.col_idx,
                 "text": "1"}
            ]
    assert parser._structure_priority(duplicated) < parser._structure_priority(clean)


def test_numeric_cells_sharing_one_bbox_are_penalised():
    """合并格被展开成两个同框金额格，也只能来自一次重复抽取。"""
    parser = PDFParser(None)
    shared = table(3)
    for cell in shared.cells:
        if cell.numeric_value is not None:
            cell.bbox = (10.0, 10.0, 20.0, 20.0)
    distinct = table(3)
    assert parser._structure_priority(shared) < parser._structure_priority(distinct)


def test_geometry_profile_roundtrip_and_default_omission():
    explicit = table(9, profile="snap6")
    payload = explicit.to_dict()
    assert payload["geometry_profile"] == "snap6"
    assert ParsedTable.from_dict(payload).geometry_profile == "snap6"

    default = table(9)
    assert "geometry_profile" not in default.to_dict()
    assert ParsedTable.from_dict(default.to_dict()).geometry_profile is None


def _income_candidate(*, drop_last: bool, blank_last_cell: bool, profile=None) -> ParsedTable:
    """收入决算表：两条数据行，第二行末列可能真实为空。"""
    from tests.remediation.test_budget_2022_table_semantics import grid

    header = ["科目编码", "科目名称", "本年收入合计", "财政拨款收入", "上级补助收入",
              "事业收入", "经营收入", "附属单位上缴收入", "其他收入"]
    first = ["206", "科学技术支出", "600.00", "100.00", "100.00", "100.00", "100.00", "100.00", "100.00"]
    second = ["221", "住房保障支出", "500.00", "100.00", "100.00", "100.00", "100.00", "100.00",
              "" if blank_last_cell else "0.00"]
    if drop_last:
        header, first, second = header[:-1], first[:-1], second[:-1]
    # 两候选总宽相同（9×30 = 8×33.75），保证基础优先级只差在结构上。
    width = 33.75 if drop_last else 30
    table = grid("FIN_02_income", "收入决算表", [header, first, second], {}, 1, uid="lost" if drop_last else "full", width=width)
    table.geometry_profile = profile
    for cell in table.cells:
        if cell.numeric_value is not None and not cell.is_header:
            cell.native_text_sources = [{
                "physical_page": 1,
                "start_char_index": 100 * cell.row_idx + 10 * cell.col_idx,
                "end_char_index": 100 * cell.row_idx + 10 * cell.col_idx + len(cell.raw_text),
                "text": cell.raw_text,
            }]
    return table


def test_coverage_outranks_structure_when_one_source_column_has_a_real_blank():
    """回归第二轮复核 R1：完整候选有一格真实空白，不能因此输给删列的候选。

    结构分按“非空格/总格”算，会奖励删掉整列的候选（它每格都填满）。
    因此裁决必须先比原页金额 token 覆盖，再比结构。
    """
    parser = PDFParser(None)
    full = _income_candidate(drop_last=False, blank_last_cell=True)
    lost = _income_candidate(drop_last=True, blank_last_cell=False, profile="snap6")
    assert parser._table_priority(full) == parser._table_priority(lost)
    assert parser._structure_priority(lost) > parser._structure_priority(full)  # 结构分仍然偏向漏列
    assert parser._covered_amount_tokens(full) > parser._covered_amount_tokens(lost)
    merged = resolve_native_table_candidates(parser, [full], [lost])
    assert len(merged) == 1
    assert merged[0].fragment_uid == "full"


def test_coverage_is_ignored_without_token_evidence():
    """没有来源 token 时不能把“无证据”当成“覆盖为零”，应退回结构比较。"""
    parser = PDFParser(None)
    clean = table(9)
    split = table(27, filled_every=3)
    assert parser._covered_amount_tokens(clean) == 0
    assert parser._covered_amount_tokens(split) == 0
    merged = resolve_native_table_candidates(parser, [split], [clean])
    assert merged[0].fragment_uid == clean.fragment_uid


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("bbox_delta", [0.0, 0.1])
def test_complete_source_coverage_precedes_area_in_either_order(reverse, bbox_delta):
    from src.services.source_reconstruction import _amount_coverage_preference

    parser = PDFParser(None)
    full = _income_candidate(drop_last=False, blank_last_cell=True)
    lost = _income_candidate(drop_last=True, blank_last_cell=False, profile="snap6")
    lost.bbox = (*lost.bbox[:3], lost.bbox[3] + bbox_delta)
    assert _amount_coverage_preference(full, lost) is False
    assert _amount_coverage_preference(lost, full) is True
    first, second = (lost, full) if reverse else (full, lost)
    assert resolve_native_table_candidates(parser, [first], [second])[0].fragment_uid == "full"


@pytest.mark.parametrize("defect", ["partial", "duplicate", "row_identity", "column_identity",
                                     "unit", "page", "different_sources", "different_text"])
def test_incomplete_or_inconsistent_sources_cannot_override_structure(defect):
    from src.services.source_reconstruction import _amount_coverage_preference

    full = _income_candidate(drop_last=False, blank_last_cell=True)
    lost = _income_candidate(drop_last=True, blank_last_cell=False)
    amounts = [c for c in full.cells if not c.is_header and c.col_idx >= 2 and c.numeric_value is not None]
    if defect == "partial":
        amounts[0].native_text_sources = []  # 非零但不完整，不能直接按记录数量排。
    elif defect == "duplicate":
        amounts[1].native_text_sources = deepcopy(amounts[2].native_text_sources)
    elif defect == "row_identity":
        full.rows[1][1] = "另一科目"
    elif defect == "column_identity":
        full.rows[0][3], full.rows[0][5] = full.rows[0][5], full.rows[0][3]
    elif defect == "unit":
        full.unit_hint = "万元"
    elif defect == "page":
        amounts[0].native_text_sources[0]["physical_page"] = 2
    elif defect == "different_sources":
        for cell in amounts:
            for source in cell.native_text_sources:
                source["start_char_index"] += 10000
                source["end_char_index"] += 10000
    else:
        amounts[0].native_text_sources[0]["text"] = "999.00"
    assert _amount_coverage_preference(full, lost) is None


def test_source_segmentation_does_not_change_coverage():
    from src.services.source_reconstruction import _verified_amount_coverage

    full = _income_candidate(drop_last=False, blank_last_cell=True)
    split_proofs = deepcopy(full)
    for cell in split_proofs.cells:
        if cell.is_header or cell.col_idx < 2 or cell.numeric_value is None:
            continue
        [source] = cell.native_text_sources
        cell.native_text_sources = [
            {**source, "start_char_index": source["start_char_index"] + offset,
             "end_char_index": source["start_char_index"] + offset + 1, "text": char}
            for offset, char in enumerate(cell.raw_text)]
    assert _verified_amount_coverage(full) == _verified_amount_coverage(split_proofs)


def test_sparse_recovery_proofs_are_bound_before_candidate_selection(monkeypatch):
    """模拟同页候选只有恢复格留证明，普通金额格必须在裁决前同样绑定。"""
    import src.services.native_structure_recovery as recovery
    import src.services.native_dimension_recovery as dimensions
    import src.services.pdf_parser as parser_module

    full = _income_candidate(drop_last=False, blank_last_cell=True)
    for cell in full.cells:
        cell.native_text_sources = []
    page_chars = []
    for cell in full.cells:
        if cell.is_header or cell.numeric_value is None:
            continue
        for offset, char in enumerate(cell.raw_text):
            page_chars.append({"text": char, "x0": cell.bbox[0] + 1 + offset,
                               "x1": cell.bbox[0] + 2 + offset,
                               "top": cell.bbox[1] + 1, "bottom": cell.bbox[1] + 5})
    page = SimpleNamespace(chars=page_chars, images=[], edges=[],
                           find_tables=lambda **kwargs: [SimpleNamespace(extract=lambda: full.rows)],
                           extract_text=lambda: "")
    parser = PDFParser(None)
    monkeypatch.setattr(parser_module, "DEFAULT_TABLE_SETTINGS", [("lines", {})])
    monkeypatch.setattr(parser, "_build_parsed_table", lambda **kwargs: deepcopy(full))
    for name in ("_recover_open_top_grid_row", "_recover_open_bottom_grid_row"):
        monkeypatch.setattr(parser, name, lambda page, table, **kwargs: table)
    for module, names in ((recovery, ("repair_native_structure",)),
                          (dimensions, ("repair_native_dimensions", "repair_native_code_baselines"))):
        for name in names:
            monkeypatch.setattr(module, name, lambda page, table, **kwargs: table)
    monkeypatch.setattr(parser, "_certify_native_blank_cells", lambda *args: None)
    monkeypatch.setattr(parser, "_extract_native_basic_footer", lambda *args: None)
    monkeypatch.setattr(parser, "_build_page_text_placeholder", lambda *args: None)
    monkeypatch.setattr(parser, "_attach_bilateral_total_line", lambda page, table: table)
    checked = []
    def merge(existing, incoming, **kwargs):
        money = [c for c in incoming[0].cells if not c.is_header and c.col_idx >= 2
                 and c.numeric_value is not None]
        assert money and all(c.native_text_sources for c in money)
        checked.append(True)
        return incoming
    monkeypatch.setattr(parser, "_merge_candidates", merge)
    parser._extract_tables_from_page(page, 1)
    assert checked == [True, True]  # 普通 lines 与 snap6 都必须先绑定。



def _income_original_page(full):
    # 原页与候选分开保存：后续候选漏格/改字不会改变这份字符快照。
    chars = [{"text": " ", "x0": 0, "x1": 1, "top": 0, "bottom": 1} for _ in range(300)]
    for cell in full.cells:
        if not cell.native_text_sources:
            continue
        [proof] = cell.native_text_sources
        for offset, char in enumerate(cell.raw_text):
            chars[proof["start_char_index"] + offset] = {
                "text": char, "x0": cell.bbox[0] + 1 + offset,
                "x1": cell.bbox[0] + 2 + offset,
                "top": cell.bbox[1] + 1, "bottom": cell.bbox[1] + 5}
    return SimpleNamespace(chars=chars, images=[])


@pytest.mark.parametrize("reverse", [False, True])
def test_point_one_bbox_with_independent_original_page(reverse):
    from src.services.source_reconstruction import _amount_coverage_preference, _original_amount_universe

    full = _income_candidate(drop_last=False, blank_last_cell=True)
    page = _income_original_page(full)
    lost = _income_candidate(drop_last=True, blank_last_cell=False)
    lost.bbox = (*lost.bbox[:3], lost.bbox[3] + 0.1)
    # 只算金额列：13笔金额合计78个原字符，编码列的两个数不计入。
    assert len(_original_amount_universe(page, (full, lost))) == 78
    assert _amount_coverage_preference(full, lost, page) is False
    first, second = (lost, full) if reverse else (full, lost)
    selected = resolve_native_table_candidates(PDFParser(None), [first], [second], page=page)
    assert selected[0].fragment_uid == "full"


def test_source_union_must_cover_independent_original_amount_universe():
    from src.services.source_reconstruction import _amount_coverage_preference

    full = _income_candidate(drop_last=False, blank_last_cell=True)
    page = _income_original_page(full)
    lost = _income_candidate(drop_last=True, blank_last_cell=False)
    for candidate in (full, lost):
        cell = next(c for c in candidate.cells if (c.row_idx, c.col_idx) == (1, 2))
        cell.raw_text = ""
        cell.numeric_value = None
        cell.native_text_sources = []
        candidate.rows[1][2] = ""
    assert _amount_coverage_preference(full, lost) is False  # 仅两候选集合仍呈真包含。
    assert _amount_coverage_preference(full, lost, page) is None  # 原页另有一笔，不能宣称完整。


def test_claim_text_must_match_the_original_character_snapshot():
    from src.services.source_reconstruction import _amount_coverage_preference

    full = _income_candidate(drop_last=False, blank_last_cell=True)
    page = _income_original_page(full)
    lost = _income_candidate(drop_last=True, blank_last_cell=False)
    page.chars[120]["text"] = "9"
    assert _amount_coverage_preference(full, lost, page) is None
