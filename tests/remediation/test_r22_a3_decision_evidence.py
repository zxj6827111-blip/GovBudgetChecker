"""R2.2 附页 A3（轻路径）+ 独立复核 IR04：整项决定统一协议。

统一协议（所有关闭性决定）：scope_confirmed 必须为 True（取消"有系统定位
即豁免"）；范围确认、原因枚举标签、〔局部确认草稿〕都不构成整项依据——
剥离后必须另有余下的显式正文；verified_ok 的正文须指明核对的关系/数字。
审核方 adversarial_results.json 的五个 manual_evidence 场景全部入回归。
"""
from __future__ import annotations

from src.services.review_lifecycle_service import (
    LOCAL_CONFIRM_DRAFT_MARKER,
    _obligation_evidence_error,
    _strip_local_confirm_drafts,
)

OPERATING_INCOME_DRAFT = (
    f"{LOCAL_CONFIRM_DRAFT_MARKER}第8页经营收入列空白，本人确认为无此项")


def test_operating_income_local_confirm_alone_closes_nothing():
    """A3/IR04 负控：经营收入单列确认 0 → 整项关闭不放行（即使勾了范围）。"""
    assert _obligation_evidence_error("not_applicable", OPERATING_INCOME_DRAFT, None,
                                      scope_confirmed=True) is not None
    assert _obligation_evidence_error("verified_ok", OPERATING_INCOME_DRAFT, None,
                                      scope_confirmed=True) is not None


def test_scope_flag_is_required_for_all_closing_decisions():
    """IR04：范围确认是所有关闭性决定的必填协议，缺省 422。"""
    for decision, note in (
        ("verified_ok", "合计150=人员120+公用30，逐项一致"),
        ("verified_issue", "表文金额与说明不一致"),
        ("not_applicable", "本材料无经营活动，整条义务不适用"),
    ):
        assert _obligation_evidence_error(decision, note, None,
                                          scope_confirmed=False) is not None
        assert _obligation_evidence_error(decision, note, None,
                                          scope_confirmed=True) is None


def test_evidence_reference_alone_no_longer_passes_verified_ok():
    """IR04 实证A：只有系统定位 evidence_reference、无正文 → 不再放行。"""
    assert _obligation_evidence_error(
        "verified_ok", None, "第8页 收入决算表（按系统定位核对）",
        scope_confirmed=True) is not None
    # 定位 + 草稿仍不放行（草稿不是关系核对结论）
    assert _obligation_evidence_error(
        "verified_ok", OPERATING_INCOME_DRAFT, "第8页 收入决算表（按系统定位核对）",
        scope_confirmed=True) is not None
    # 定位 + 用户写明核对关系 → 放行
    assert _obligation_evidence_error(
        "verified_ok", "经营收入与空白格核对一致，总收入10=各来源之和",
        "第8页 收入决算表（按系统定位核对）", scope_confirmed=True) is None


def test_ui_stitched_label_plus_draft_rejected():
    """IR04 实证B：真实 UI 拼接"标签：〔草稿〕…"→ 剥离后只剩标签 → 拒绝。"""
    stitched = f"已有空表或无此项说明：{OPERATING_INCOME_DRAFT}"
    assert _obligation_evidence_error("not_applicable", stitched, None,
                                      scope_confirmed=True) is not None
    # IR04 实证B 收口：无标记的裸局部说明（单格形态、无整项指向词）在
    # scope_confirmed=True 下同样拒绝——整项指向词门（结构性关键词，与
    # verified_ok 的关系词门同一纪律）。
    assert _obligation_evidence_error(
        "not_applicable", "第8页经营收入列空白，本人确认为无此项", None,
        scope_confirmed=True) is not None
    # 含整项指向词的用户正文 → 放行
    assert _obligation_evidence_error(
        "not_applicable", "本材料本年度无经营收入业务，该义务整体不涉及，覆盖全部阻断点",
        None, scope_confirmed=True) is None


def test_whole_item_basis_passes_and_generic_note_rejected():
    """正控：整项依据放行；verified_ok 通用表态拒绝。"""
    note = (
        f"{OPERATING_INCOME_DRAFT}\n"
        "本材料无经营活动，收入决算表整张表无经营收入及相关分项，"
        "该收入核对义务对本材料整体不适用，覆盖全部阻断点。")
    assert _obligation_evidence_error("not_applicable", note, None,
                                      scope_confirmed=True) is None
    assert _obligation_evidence_error("verified_ok", "核对无误", None,
                                      scope_confirmed=True) is not None
    assert _obligation_evidence_error(
        "verified_ok", "第12页基本支出决算表，人员经费120.00+公用经费30.00与说明合计150.00一致",
        None, scope_confirmed=True) is None


def test_verified_issue_and_pending_unchanged():
    """既有纪律：verified_issue 必填 note；pending 允许空依据。"""
    assert _obligation_evidence_error("verified_issue", None, None,
                                      scope_confirmed=True) is not None
    assert _obligation_evidence_error("verified_issue", "表文金额与说明不一致", None,
                                      scope_confirmed=True) is None
    assert _obligation_evidence_error("pending", None, None,
                                      scope_confirmed=False) is None


def test_local_draft_marker_stripping():
    """草稿剥离：残余只含标记之外正文；无标记时原样返回。"""
    assert _strip_local_confirm_drafts(OPERATING_INCOME_DRAFT) == ""
    mixed = f"{OPERATING_INCOME_DRAFT}\n整项关系10=8+0已逐项核对一致"
    assert _strip_local_confirm_drafts(mixed) == "整项关系10=8+0已逐项核对一致"
    assert _strip_local_confirm_drafts("普通说明，无草稿标记") == "普通说明，无草稿标记"
