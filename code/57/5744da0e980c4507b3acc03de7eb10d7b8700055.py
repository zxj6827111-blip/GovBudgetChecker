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
