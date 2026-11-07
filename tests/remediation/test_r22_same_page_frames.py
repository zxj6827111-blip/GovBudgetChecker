"""R2.2 §7：_dedupe 同页主框+关联框（同页丢框反例）。

R1 实证：obligation_locations._dedupe 旧实现"同一页只留一个入口"，同页
其余 finding 证据整框丢弃——补核卡看不到同页的其他证据位置。新契约：
同页保留主框 + 关联框（related），按 (bbox, label) 去重，随总数上限截断。
"""
from __future__ import annotations

from src.services.obligation_locations import _dedupe, merge_locations
from src.schemas.review_lifecycle import ObligationLocation


def test_same_page_related_frames_are_kept_not_dropped():
    """同页丢框反例：同页第二个 finding 的 bbox 进 related，不再整框丢弃。"""
    locations = [
        (15, [10.0, 20.0, 30.0, 40.0], "三公经费表"),
        (15, [50.0, 60.0, 70.0, 80.0], "三公经费说明"),
    ]
    result = _dedupe(locations)
    assert len(result) == 1
    assert result[0].bbox == [10.0, 20.0, 30.0, 40.0]
    assert len(result[0].related) == 1
    assert result[0].related[0].bbox == [50.0, 60.0, 70.0, 80.0]
    assert result[0].related[0].label == "三公经费说明"


def test_same_page_identical_frame_not_duplicated():
    """同页同 bbox 同 label 只保留一次。"""
    locations = [
        (15, [10.0, 20.0, 30.0, 40.0], "表A"),
        (15, [10.0, 20.0, 30.0, 40.0], "表A"),
    ]
    result = _dedupe(locations)
    assert len(result) == 1
    assert result[0].related == []


def test_cross_page_entries_stay_separate():
    """跨页入口不合并（关联框只发生在同页）。"""
    result = _dedupe([
        (15, [1.0, 2.0, 3.0, 4.0], "p15"),
        (16, [5.0, 6.0, 7.0, 8.0], "p16"),
    ])
    assert [entry.page for entry in result] == [15, 16]
    assert all(entry.related == [] for entry in result)


def test_merge_locations_folds_related_from_same_page_groups():
    """同源义务合并：同页关联框并入主入口并去重。"""
    first = [ObligationLocation(page=15, label="表A", bbox=[1.0, 2.0, 3.0, 4.0])]
    second = [
        ObligationLocation(page=15, label="表A", bbox=[1.0, 2.0, 3.0, 4.0]),
        ObligationLocation(page=15, label="表B", bbox=[5.0, 6.0, 7.0, 8.0]),
    ]
    merged = merge_locations(first, second)
    assert len(merged) == 1
    assert merged[0].bbox == [1.0, 2.0, 3.0, 4.0]
    assert len(merged[0].related) == 1
    assert merged[0].related[0].label == "表B"


def test_related_respects_global_cap():
    """主入口总数上限不变（_MAX_LOCATIONS），关联框不改变页级入口数。"""
    locations = [(page, [float(page)], f"表{page}") for page in range(1, 12)]
    result = _dedupe(locations)
    assert len(result) <= 6  # _MAX_LOCATIONS
