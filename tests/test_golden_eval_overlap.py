"""Golden 评估器内容重叠匹配的回归测试（review 🟡1）。

背景：evidence_overlaps 的滑窗固定 6 字，短于 6 字且不含数字的标注
（如「空表」「空表说明」）永远匹配不上任何 finding——窗口下限退化
为 min(6, len) 后按全串比对，消除该盲区。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_golden_corpus import (
    _normalize_for_overlap,
    evidence_overlaps,
    match_annotation,
)


def test_short_annotation_without_numbers_matches():
    """短于 6 字且无数字的标注：按全串比对，正确相关时必须命中。"""
    assert evidence_overlaps(
        "空表", {"message": "缺少空表说明", "evidence_text": ""}
    )
    assert evidence_overlaps(
        "空表说明", {"message": "", "evidence_text": "应附空表说明或等效说明"}
    )


def test_short_annotation_unrelated_still_rejected():
    """短标注的全串比对不等于子串放行：无关正文仍拒绝。"""
    assert not evidence_overlaps(
        "空表", {"message": "支出决算表合计与明细不一致", "evidence_text": ""}
    )


def test_single_char_annotation_matches_by_full_string():
    """单字标注退化为子串包含判定：在则命中、不在则拒绝。"""
    assert evidence_overlaps("表", {"message": "表格缺失", "evidence_text": ""})
    assert not evidence_overlaps("张", {"message": "表格缺失", "evidence_text": ""})


def test_numeric_intersection_still_primary_signal():
    """数字 token 交集仍是第一判据（金额/年份场景）。"""
    assert evidence_overlaps(
        "合计 1,367.76 与分项之和不符",
        {"message": "合计值 1,367.76，明细之和 1,367.75", "evidence_text": ""},
    )


def test_unrelated_content_rejected():
    """GPT5.6 复现场景：规则页码正确但正文无关必须拒绝。"""
    assert not evidence_overlaps(
        "「公务接待费支出决算减少为0.00万元，与2024年持平」逻辑矛盾",
        {"message": "完全不相关的正文内容", "evidence_text": ""},
    )


def test_empty_annotation_evidence_falls_back():
    """标注未写 evidence：退回规则+页码匹配（不惩罚标注侧缺失）。"""
    assert evidence_overlaps(None, {"message": "任何内容", "evidence_text": ""})
    assert evidence_overlaps("", {"message": "", "evidence_text": None})


def test_match_annotation_prefers_numeric_overlap():
    """多条候选时优先消费数字重叠度最高的 finding。"""
    ann = {
        "rule_id": "V33-120",
        "page": 10,
        "evidence": "合计 1,367.76 不等于分项之和（10.77+259.52）",
    }
    weak = {
        "rule": "V33-120",
        "page": 10,
        "message": "支出决算表存在取整误差",
        "evidence_text": "差 0.01",
    }
    strong = {
        "rule": "V33-120",
        "page": 10,
        "message": "合计值 1,367.76，明细之和 1,367.75",
        "evidence_text": "分项 10.77 与 259.52",
    }
    hit = match_annotation(ann, [weak, strong], set())
    assert hit is strong


def test_normalize_for_overlap_strips_punctuation():
    assert _normalize_for_overlap("  1,367.76 万元！") == "136776万元"
    assert _normalize_for_overlap("") == ""
