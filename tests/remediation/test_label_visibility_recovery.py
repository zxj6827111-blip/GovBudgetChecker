"""单元格级名称恢复：正反例、几何、受保护格与隐藏层边界。"""
from __future__ import annotations

from types import SimpleNamespace

import pdfplumber
import pymupdf
import pytest

from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services import native_label_visibility as visibility
from src.services.native_label_visibility import (
    PageVisibility,
    REASON_BOUNDARY,
    REASON_CHAR_MISMATCH,
    REASON_CODE_ROLE,
    REASON_AMOUNT_ROLE,
    REASON_HIDDEN_RENDER,
    REASON_HIDDEN_WHITE,
    REASON_GEOMETRY,
    _match_cell_chars,
    recover_label_cells,
)

GRID = {"vertical_strategy": "lines", "horizontal_strategy": "lines",
        "snap_tolerance": 6, "join_tolerance": 6, "intersection_tolerance": 6}
X0, X1, X2, X3 = 50, 110, 300, 350
Y0, Y1, Y2, Y3 = 50, 75, 105, 135


def _merged_total_pdf(tmp_path, *, clipped=True):
    path=tmp_path/'merged-total.pdf'
    doc=pymupdf.open()
    page=doc.new_page(width=400,height=300)
    for x in (X0,X2,X3):page.draw_line((x,Y0),(x,Y3))
    for y in (Y0,Y1,Y2,Y3):page.draw_line((X0,y),(X3,y))
    page.draw_line((X1,Y0),(X1,Y1))
    page.draw_line((X1,Y2),(X1,Y3))
    for x,y,text,font in [(55,66,'Code','helv'),(115,66,'Name','helv'),(305,66,'Amount','helv'),
        (170,94,'合计','china-s'),(305,94,'10.00','helv'),(55,124,'201','helv'),
        (115,124,'一般公共服务支出','china-s'),(305,124,'10.00','helv')]:
        page.insert_text((x,y),text,fontname=font,fontsize=9)
    page.insert_text((55,94),'编码',fontname='china-s',fontsize=9)
    if clipped:
        last=page.get_contents()[-1]
        doc.update_stream(last,b'q 10 10 5 5 re W n\n'+doc.xref_stream(last)+b'\nQ')
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize('clipped',[True,False])
def test_only_source_proven_hidden_header_tail_is_removed_from_merged_total(tmp_path,monkeypatch,clipped):
    path=_merged_total_pdf(tmp_path,clipped=clipped)
    page,table=_table(path)
    cell=next(c for c in table.cells if (c.row_idx,c.col_idx)==(1,0))
    assert '编码' in cell.raw_text and '合计' in cell.raw_text
    cell.col_span=2
    cell.is_header=True  # 旧角色误把“编码”所在格当成表头。
    amount=next(c for c in table.cells if (c.row_idx,c.col_idx)==(1,2))
    amount.numeric_value=10
    monkeypatch.setattr(visibility,'_amount_columns',lambda _table:{2})
    result,stats=recover_label_cells(page,table,code_columns=[0],name_columns=[1],page_visibility=PageVisibility(_fitz_page(path)))
    total=next(c for c in result.cells if (c.row_idx,c.col_idx)==(1,0))
    if clipped:
        assert total.raw_text=='合计' and not total.is_header
        assert total.grid_raw_text==cell.raw_text
        assert total.label_recovery['role_basis']=='native_merged_total_label'
    else:
        assert total.raw_text==cell.raw_text and total.is_header
    assert next(c for c in result.cells if (c.row_idx,c.col_idx)==(1,2)).raw_text=='10.00'


@pytest.mark.parametrize('change',['not_merged','not_first_row','overlaps_amount','not_numeric_row','contains_code'])
def test_merged_total_role_never_rewrites_an_unproven_cell(tmp_path,monkeypatch,change):
    path=_merged_total_pdf(tmp_path)
    page,table=_table(path)
    cell=next(c for c in table.cells if (c.row_idx,c.col_idx)==(1,0))
    cell.col_span=2
    cell.is_header=True
    amount=next(c for c in table.cells if (c.row_idx,c.col_idx)==(1,2))
    amount.numeric_value=10
    if change=='not_merged':cell.col_span=1
    elif change=='not_first_row':table.header_row_count=0
    elif change=='overlaps_amount':cell.col_span=3
    elif change=='not_numeric_row':amount.numeric_value=None
    else:cell.raw_text='301 合计'
    monkeypatch.setattr(visibility,'_amount_columns',lambda _table:{2})
    result,_=recover_label_cells(page,table,code_columns=[0],name_columns=[1],page_visibility=PageVisibility(_fitz_page(path)))
    total=next(c for c in result.cells if (c.row_idx,c.col_idx)==(1,0))
    assert total.raw_text==cell.raw_text


def _make_pdf(tmp_path, hidden_text="TOTAL", cell=(1, 1), render_mode=0,
              color=(0, 0, 0), clip=(10, 10, 5, 5)):
    """生成带闭合网格与裁剪隐藏文字的合成 PDF（ASCII 字体，避免字体依赖）。"""
    path = tmp_path / "clipped.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    for x in (X0, X1, X2, X3):
        page.draw_line((x, Y0), (x, Y3), color=(0, 0, 0), width=1)
    for y in (Y0, Y1, Y2, Y3):
        page.draw_line((X0, y), (X3, y), color=(0, 0, 0), width=1)
    for x, y, text in [
        (X0 + 4, Y0 + 16, "Code"), (X1 + 4, Y0 + 16, "Name"), (X2 + 4, Y0 + 16, "Amount"),
        (X0 + 4, Y1 + 19, "20103"), (X1 + 4, Y1 + 19, "Public Service"), (X2 + 4, Y1 + 19, "955.35"),
        (X0 + 4, Y2 + 19, "2010302"), (X1 + 4, Y2 + 19, "General Affairs"), (X2 + 4, Y2 + 19, "955.35"),
    ]:
        page.insert_text((x, y), text, fontname="helv", fontsize=9)
    page.clean_contents()
    old = page.get_contents()
    data = doc.xref_stream(old[0])
    # 目标格中心坐标（PDF 用户空间，原点左下）
    centers = {
        (1, 0): (65, 212), (1, 1): (150, 212), (1, 2): (310, 212),
        (2, 1): (150, 242),
    }
    x, y = centers[cell]
    r, g, b = color
    raw = (
        f"\nq {clip[0]} {clip[1]} {clip[2]} {clip[3]} re W n "
        f"BT /helv 9 Tf {render_mode} Tr {r} {g} {b} rg "
        f"1 0 0 1 {x} {y} Tm ({hidden_text}) Tj ET Q\n"
    ).encode()
    doc.update_stream(old[0], data + raw)
    doc.save(path)
    doc.close()
    return path


def _table(path, name_columns=(1,), code_columns=(0,)):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        table = page.find_tables(table_settings=GRID)[0]
        rows = table.extract()
        cells = []
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                box = table.rows[row_idx].cells[col_idx]
                cells.append(ParsedCell(
                    row_idx=row_idx, col_idx=col_idx,
                    raw_text=(value or "").strip(), normalized_text=(value or "").strip(),
                    numeric_value=None, page_number=1, bbox=box,
                    is_header=row_idx == 0, confidence=0.9, unit_hint=None,
                    extraction_method="lines",
                ))
        parsed = ParsedTable(
            table_code="FIN_02_income", title="收入决算表", page_number=1, table_index=0,
            bbox=table.bbox, header_row_count=1, confidence=0.9,
            extraction_method="lines", unit_hint=None, rows=rows, cells=cells,
            fragment_uid="fixture-fragment",
        )
        return page, parsed


def _fitz_page(path):
    return pymupdf.open(path)[0]


def test_clipped_hidden_text_removed_from_name_cell(tmp_path):
    path = _make_pdf(tmp_path)
    page, table = _table(path)
    before = next(c for c in table.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert "TOTAL" in before.raw_text
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert cell.raw_text == "Public Service"
    assert cell.grid_raw_text == before.raw_text
    assert [item["text"] for item in cell.label_recovery["excluded_characters"]] == list("TOTAL")
    assert cell.label_recovery["policy_version"] == visibility.POLICY_VERSION
    assert stats["recovered"] == 1 and stats["conflicts"] == 0


def test_visible_cell_is_untouched(tmp_path):
    path = _make_pdf(tmp_path)
    page, table = _table(path)
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (2, 1))
    assert cell.raw_text == "General Affairs"
    assert cell.label_recovery == {}
    assert stats["recovered"] == 1


def test_code_cell_is_reported_not_rewritten(tmp_path):
    path = _make_pdf(tmp_path, cell=(1, 0))
    page, table = _table(path)
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 0))
    assert "TOTAL" in cell.raw_text
    assert cell.label_recovery["status"] == REASON_CODE_ROLE
    assert stats["conflicts"] == 1


def test_amount_cell_is_reported_not_rewritten(tmp_path, monkeypatch):
    path = _make_pdf(tmp_path, cell=(1, 2))
    page, table = _table(path)
    monkeypatch.setattr(visibility, "_amount_columns", lambda _table: {2})
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 2))
    assert "TOTAL" in cell.raw_text
    assert cell.label_recovery["status"] == REASON_AMOUNT_ROLE
    assert stats["conflicts"] == 1


def test_render_mode_three_hidden_layer_is_not_deleted(tmp_path):
    path = _make_pdf(tmp_path, render_mode=3)
    page, table = _table(path)
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert "TOTAL" in cell.raw_text
    assert cell.label_recovery["status"] == REASON_HIDDEN_RENDER
    assert stats["skipped"] == 1 and stats["recovered"] == 0


def test_white_hidden_text_is_not_deleted(tmp_path):
    path = _make_pdf(tmp_path, color=(1, 1, 1))
    page, table = _table(path)
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert "TOTAL" in cell.raw_text
    assert cell.label_recovery["status"] == REASON_HIDDEN_WHITE
    assert stats["skipped"] == 1


def test_unsupported_geometry_is_not_recovered(tmp_path):
    path = _make_pdf(tmp_path)
    page, table = _table(path)
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility.unsupported(),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert "TOTAL" in cell.raw_text
    assert stats["reasons"] == {REASON_GEOMETRY: 1}
    assert stats["recovered"] == 0


def _fit(text, bbox, line=0):
    return {"text": text, "norm": text, "bbox": bbox, "line": line}


def _pp(text, bbox, page_index=0):
    return {"text": text, "x0": bbox[0], "top": bbox[1], "x1": bbox[2],
            "bottom": bbox[3], "page_index": page_index}


def _visibility(full, visible, render=(), white=()):
    return SimpleNamespace(
        supported=True, full=full, visible=visible,
        render_mode_indices=set(render), white_text_indices=set(white),
    )


def test_duplicate_characters_are_resolved_by_occurrence_position():
    # 两个 A：一个可见、一个隐藏；顺序与出现次数必须唯一对应。
    full = [_fit("A", (10, 10, 20, 20), 0), _fit("A", (30, 10, 40, 20), 0)]
    visible = [_fit("A", (10, 10, 20, 20), 0)]
    pp = [_pp("A", (10, 10, 20, 20), 0), _pp("A", (30, 10, 40, 20), 1)]
    result = _match_cell_chars(_visibility(full, visible), (0, 0, 100, 100), pp, "AA")
    assert result.status == visibility.REASON_OK
    assert result.hidden == {1}
    assert result.visible_text == "A"


def test_order_difference_does_not_break_position_correspondence():
    # pdfplumber 字符列表顺序与页面坐标顺序不同：仍按位置唯一对应，
    # 隐藏的是位置的 A，而不是按列表首个字符删除。
    full = [_fit("B", (10, 10, 20, 20), 0), _fit("A", (30, 10, 40, 20), 0)]
    visible = [_fit("B", (10, 10, 20, 20), 0)]
    pp = [_pp("A", (30, 10, 40, 20), 0), _pp("B", (10, 10, 20, 20), 1)]
    result = _match_cell_chars(_visibility(full, visible), (0, 0, 100, 100), pp, "AB")
    assert result.status == visibility.REASON_OK
    assert result.hidden == {0}
    assert result.visible_text == "B"


def test_large_boundary_overflow_is_unresolved():
    full = [_fit("A", (-20, 70, 20, 95), 0)]  # 中心在内，但越界远超字高
    visible = [_fit("A", (-20, 70, 20, 95), 0)]
    pp = [_pp("A", (-20, 70, 20, 95))]
    result = _match_cell_chars(_visibility(full, visible), (0, 0, 100, 100), pp, "A")
    assert result.status == REASON_BOUNDARY


def test_character_count_mismatch_is_unresolved():
    full = [_fit("A", (10, 10, 20, 20), 0), _fit("B", (30, 10, 40, 20), 0)]
    visible = full
    pp = [_pp("A", (10, 10, 20, 20))]
    result = _match_cell_chars(_visibility(full, visible), (0, 0, 100, 100), pp, "A")
    assert result.status == REASON_CHAR_MISMATCH


def test_disabled_flag_keeps_old_path(monkeypatch, tmp_path):
    monkeypatch.setenv(visibility.DISABLE_ENV, "true")
    path = _make_pdf(tmp_path)
    page, table = _table(path)
    out, stats = recover_label_cells(
        page, table, code_columns=[0], name_columns=[1],
        page_visibility=PageVisibility(_fitz_page(path)),
    )
    cell = next(c for c in out.cells if (c.row_idx, c.col_idx) == (1, 1))
    assert "TOTAL" in cell.raw_text
    assert stats["recovered"] == 0
