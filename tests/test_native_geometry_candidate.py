from types import SimpleNamespace

from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import resolve_native_table_candidates


def table(columns: int, profile=None) -> ParsedTable:
    rows = [[f"c{index}" for index in range(columns)], ["1"] * columns]
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


def test_structure_priority_prefers_fewer_duplicate_columns():
    parser = PDFParser(None)
    assert parser._structure_priority(table(9)) > parser._structure_priority(table(27))


def test_tie_breaker_selects_snap6_candidate_for_same_priority():
    parser = PDFParser(None)
    snap3 = table(27)
    snap6 = table(9, profile="snap6")
    assert parser._table_priority(snap3) == parser._table_priority(snap6)
    merged = resolve_native_table_candidates(parser, [snap3], [snap6])
    assert len(merged) == 1
    assert merged[0].geometry_profile == "snap6"
    assert len(merged[0].rows[0]) == 9


def test_geometry_profile_roundtrip_and_default_omission():
    explicit = table(9, profile="snap6")
    payload = explicit.to_dict()
    assert payload["geometry_profile"] == "snap6"
    assert ParsedTable.from_dict(payload).geometry_profile == "snap6"

    default = table(9)
    assert "geometry_profile" not in default.to_dict()
    assert ParsedTable.from_dict(default.to_dict()).geometry_profile is None
