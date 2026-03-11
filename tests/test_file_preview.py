from api.routes.files import _clamp_bbox, _expand_bbox, _parse_bbox_query


def test_parse_bbox_query_accepts_valid_bbox():
    bbox = _parse_bbox_query("10,20,110,220")
    assert bbox == [10.0, 20.0, 110.0, 220.0]


def test_parse_bbox_query_rejects_invalid_bbox():
    assert _parse_bbox_query("10,20,30") is None
    assert _parse_bbox_query("10,20,15,5") is None
    assert _parse_bbox_query("a,b,c,d") is None


def test_clamp_and_expand_bbox_respects_page_bounds():
    clamped = _clamp_bbox([-20, 10, 250, 320], page_width=200.0, page_height=300.0)
    assert clamped == [0.0, 10.0, 200.0, 300.0]

    expanded = _expand_bbox(clamped, page_width=200.0, page_height=300.0, padding=24.0)
    assert expanded == [0.0, 0.0, 200.0, 300.0]
