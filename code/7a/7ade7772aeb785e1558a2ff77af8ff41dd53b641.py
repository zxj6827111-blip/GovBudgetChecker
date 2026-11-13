"""真实几何反例的最小控制；保留片段粒度，不能把尾词再拼成重复字。"""

from copy import deepcopy
import pytest

from src.services.ocr_grid_fragments import group_projections, compose_literal


def fragment(text, box, *, clipped=False):
    return {"text": text, "grid_bbox": box, "score": .999, "raster_dpi": 300,
            "status": "requires_regional_read" if clipped else "qualified_fragment",
            "reasons": ["fragment_clipped_at_tile_boundary"] if clipped else []}


def test_clipped_tail_remains_original_alternate_of_full_source_fragment():
    values = [fragment("其他人大事务支出", [562, 1535, 936, 1590]),
              fragment("支出", [826, 1530, 942, 1596], clipped=True)]
    before = deepcopy(values)
    groups = group_projections(values)
    assert len(groups) == 1 and groups[0]["status"] == "resolved"
    assert compose_literal(groups) == "其他人大事务支出"
    assert groups[0]["alternates"] == before and values == before


@pytest.mark.parametrize("text,box,clipped", [
    ("收入", [826, 1530, 942, 1596], True),
    ("支出", [826, 1600, 942, 1666], True),
    ("支出", [960, 1530, 1076, 1596], True),
    ("支出", [826, 1530, 942, 1596], False),
])
def test_different_literal_line_position_or_complete_conflict_is_not_erased(text, box, clipped):
    groups = group_projections([fragment("其他人大事务支出", [562, 1535, 936, 1590]),
                                fragment(text, box, clipped=clipped)])
    assert len(groups) == 2 or any(g["status"] != "resolved" for g in groups)


def test_numeric_substrings_never_use_han_tail_relation():
    groups = group_projections([fragment("123.45", [562, 1535, 936, 1590]),
                                fragment("45", [826, 1530, 942, 1596], clipped=True)])
    assert len(groups) == 2


def response_fragment(text, box, request, dpi):
    return {**fragment(text, box), "raster_dpi": dpi,
            "source": {"input_sha256": request, "raw_prediction_sha256": request},
            "render_bbox": box, "page_bbox": box}


def test_higher_resolution_split_word_is_kept_as_real_fragment_sequence():
    values = [response_fragment("附属单位上缴", [2627, 771, 2899, 826], "first", 300),
              response_fragment("附属", [2628.3, 773.6, 2721, 826.9], "second", 450),
              response_fragment("单位上缴", [2714.4, 770.7, 2899.7, 828.1], "second", 450),
              response_fragment("单位上缴", [2714, 771.6, 2900, 828.1], "third", 600)]
    groups = group_projections(values)
    assert len(groups) == 1 and groups[0]["status"] == "resolved"
    selected = groups[0]["selected"]
    assert selected["text"] == "附属单位上缴" and selected["raster_dpi"] == 450
    assert selected["granularity"] == "fragment_sequence"
    assert [p["text"] for p in selected["members"]] == ["附属", "单位上缴"]
    assert groups[0]["alternates"] == values


def test_separate_requests_cannot_supply_half_a_word_each():
    values = [response_fragment("附属单位上缴", [2627, 771, 2899, 826], "first", 300),
              response_fragment("附属", [2628.3, 773.6, 2721, 826.9], "left", 450),
              response_fragment("单位上缴", [2714.4, 770.7, 2899.7, 828.1], "right", 450)]
    selected = group_projections(values)[0]["selected"]
    assert selected["text"] == "附属单位上缴" and selected["raster_dpi"] == 300
    assert "members" not in selected


def test_sequence_union_box_cannot_hide_unread_ink_between_actual_members():
    from src.services.ocr_grid_fragments import uncovered_ink_pixels
    from types import SimpleNamespace

    cell = SimpleNamespace(ocr_provenance={"physical_grid": {"cell": {
        "content_ink_runs_px": [[10, 1, 5], [10, 8, 10], [10, 12, 16]],
    }}})
    selected = {"grid_bbox": [0, 0, 17, 20], "members": [
        {"grid_bbox": [0, 0, 5, 20]}, {"grid_bbox": [12, 0, 17, 20]},
    ]}
    assert uncovered_ink_pixels(cell, [{"selected": selected, "status": "resolved"}]) == 2


def test_same_literal_real_boxes_cover_detector_padding_jitter_without_claiming_independent_votes():
    from src.services.ocr_grid_fragments import uncovered_ink_pixels
    from types import SimpleNamespace

    values = [response_fragment("83.8", [1174, 1349, 1286, 1406], "first", 300),
              response_fragment("83.8", [1175.7, 1353.8, 1286.2, 1405.3], "second", 600)]
    groups = group_projections(values)
    cell = SimpleNamespace(ocr_provenance={"physical_grid": {"cell": {
        "content_ink_runs_px": [[1368, 1174, 1178]],
    }}})
    assert uncovered_ink_pixels(cell, groups) == 0
    assert groups[0]["selected"]["raster_dpi"] == 600
    assert len(groups[0]["coverage_support"]) == 2
    assert groups[0]["accuracy_independently_verified"] is False


def test_source_speck_has_explicit_image_role_while_decimal_and_sign_on_baseline_remain_missing():
    from src.services.ocr_grid_fragments import uncovered_ink_pixels
    from types import SimpleNamespace

    group = {"status": "resolved", "selected": {"grid_bbox": [1174, 1349, 1286, 1406]}}
    cell = SimpleNamespace(ocr_provenance={"physical_grid": {"cell": {
        "content_ink_runs_px": [[1413, 1097, 1099], [1414, 1097, 1099],
                                [1370, 1100, 1115], [1400, 1130, 1133]],
    }}})
    observed = []
    assert uncovered_ink_pixels(cell, [group], image_marks=observed) == 18
    assert len(observed) == 1 and observed[0]["original_ink_pixels"] == [
        [1097, 1413], [1097, 1414], [1098, 1413], [1098, 1414],
    ]
    assert observed[0]["accuracy_independently_verified"] is False
