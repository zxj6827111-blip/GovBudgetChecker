"""R2.2 附页 A3（轻路径）：整项决定的依据校验升级与行为负控。

负控场景（计划 A3 依据校验升级 + 轻路径行为负控）：
- 总额10、财政拨款10、经营收入空白：仅确认经营收入为 0（局部草稿）→
  不得生成整项 not_applicable 或自动 verified_ok；
- "确认全部 N 个阻断点"的范围确认不能代替适用性依据；
- verified_ok 的正文须指明核对了哪些关系/哪些数（通用表态不够）；
- 一键提交路径（系统定位自动带 evidence_reference）不受影响。
"""
from __future__ import annotations

import pytest

from src.services.review_lifecycle_service import (
    LOCAL_CONFIRM_DRAFT_MARKER,
    _obligation_evidence_error,
    _strip_local_confirm_drafts,
)

OPERATING_INCOME_DRAFT = (
    f"{LOCAL_CONFIRM_DRAFT_MARKER}第8页经营收入列空白，本人确认为无此项")


def test_operating_income_local_confirm_alone_closes_nothing():
    """A3 负控：经营收入单列确认 0 → 整项 not_applicable 与 verified_ok 都不放行。"""
    assert _obligation_evidence_error("not_applicable", OPERATING_INCOME_DRAFT, None) is not None
    assert _obligation_evidence_error("verified_ok", OPERATING_INCOME_DRAFT, None) is not None


def test_scope_confirmation_without_basis_is_not_evidence():
    """"确认全部阻断点"式的范围话术不是适用性依据：局部草稿之外无正文 → 422。"""
    scope_only = f"{OPERATING_INCOME_DRAFT}\n确认覆盖该义务全部 3 个阻断点。"
    # 残余只剩范围确认句，没有任何整项适用性依据 → 仍须拒绝
    assert _obligation_evidence_error("not_applicable", scope_only, None) is not None


def test_not_applicable_with_whole_obligation_basis_passes():
    """正控：整条义务不适用的依据 + 覆盖范围 → 放行。"""
    note = (
        f"{OPERATING_INCOME_DRAFT}\n"
        "本材料无经营活动，收入决算表整张表无经营收入及相关分项，"
        "该收入核对义务对本材料整体不适用，覆盖全部阻断点。")
    assert _obligation_evidence_error("not_applicable", note, None) is None


def test_verified_ok_requires_relation_or_numbers():
    """verified_ok：通用表态不够，须写明核对的关系/数字。"""
    assert _obligation_evidence_error("verified_ok", "核对无误", None) is not None
    ok_note = "第12页基本支出决算表，人员经费120.00+公用经费30.00与说明合计150.00一致"
    assert _obligation_evidence_error("verified_ok", ok_note, None) is None
    ok_relation = "收入总表各行合计等于各收入来源之和，逐项核对一致"
    assert _obligation_evidence_error("verified_ok", ok_relation, None) is None


def test_verified_ok_one_click_with_system_evidence_unaffected():
    """一键提交：系统定位自动带 evidence_reference、note 为空 → 不受草稿纪律影响。"""
    assert _obligation_evidence_error("verified_ok", None, "第12页 基本支出决算表") is None


def test_local_draft_marker_stripping():
    """草稿剥离：残余只含标记之外正文；无标记时原样返回。"""
    assert _strip_local_confirm_drafts(OPERATING_INCOME_DRAFT) == ""
    mixed = f"{OPERATING_INCOME_DRAFT}\n整项关系10=8+0已逐项核对一致"
    assert _strip_local_confirm_drafts(mixed) == "整项关系10=8+0已逐项核对一致"
    assert _strip_local_confirm_drafts("普通说明，无草稿标记") == "普通说明，无草稿标记"


def test_verified_issue_and_pending_unchanged():
    """既有纪律不回退：verified_issue 必填 note；pending 允许空依据。"""
    assert _obligation_evidence_error("verified_issue", None, None) is not None
    assert _obligation_evidence_error("verified_issue", "表文金额与说明不一致", None) is None
    assert _obligation_evidence_error("pending", None, None) is None


def test_missing_note_still_required_for_not_applicable():
    """not_applicable 无 note → 维持必填。"""
    error = _obligation_evidence_error("not_applicable", None, None)
    assert error is not None
    assert "判定不适用必须写明原因" in error["message"]
