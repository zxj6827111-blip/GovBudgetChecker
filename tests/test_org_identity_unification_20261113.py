"""机构归属整改（2026-11-13）：部门与同名本级单位的统一身份映射。

真实问题（普陀区民政局形态）：组织目录里部门「上海市普陀区民政局」(cfd1c7f6aec1)
与其下属同名单位「上海市普陀区民政局」(7c3a470cbb70, code 002002) 并存，单位名称
不带「（本级）」后缀。原件明确单位/本级口径时，旧实现只认「部门名＋（本级）」
字面组合，错误返回 directory_missing_org 并反复要求人工确认；人工选了正确
单位又会被封面冲突校验拒绝。本文件回归修复后的统一判定。
"""

from api.routes.upload import _cover_benji_matches_selected_unit, _organization_binding_conflict
from src.services.org_binding_decision import decide_auto_binding
from src.services.org_name_identity import compare_org_names


def org(name="甲市财政局", level="department", id="d", parent_id=None):
    return dict(name=name, level=level, id=id, parent_id=parent_id)


def decide(pages, directory, **kwargs):
    return decide_auto_binding(
        filename="甲市财政局2025年度决算.pdf", page_texts=pages, directory=directory, **kwargs
    )


def code(result):
    return result["reasons"][0]["code"]


# ---------------------------------------------------------------------------
# 自动归属：同名无后缀 unit 子记录
# ---------------------------------------------------------------------------


def test_same_name_unit_child_binds_for_unit_scope():
    """原件明确单位口径＋目录有唯一同名 unit 子记录 → 自动绑定本级单位。"""
    directory = [org(), org("甲市财政局", "unit", "u", "d")]
    result = decide(["甲市财政局\n2025年度单位决算"], directory)
    assert result["decision"] == "bound"
    assert result["organization_id"] == "u"
    assert result["match_basis"] == "combined_source_scope"


def test_benji_cover_binds_same_name_unit_child():
    """封面写「部门名（本级）」（民政局本级真实封面形态）→ 绑同名单位。"""
    directory = [org(), org("甲市财政局", "unit", "u", "d")]
    result = decide(["甲市财政局\n（本级）\n2025 年度决算"], directory)
    assert result["organization_id"] == "u"


def test_same_name_unit_with_foreign_parent_is_not_a_benji_candidate():
    """同名 unit 挂在别的部门下：不是本部门的本级候选，保持缺组织待确认。"""
    directory = [
        org(),
        org("乙区财政局", "department", "d2"),
        org("甲市财政局", "unit", "u", "d2"),
    ]
    assert code(decide(["甲市财政局\n2025年度单位决算"], directory)) == "directory_missing_org"


def test_same_name_unit_and_benji_unit_coexist_is_a_tie():
    """同名无后缀 unit 与「（本级）」后缀 unit 并存：两候选等价，转人工。"""
    directory = [
        org(),
        org("甲市财政局（本级）", "unit", "u1", "d"),
        org("甲市财政局", "unit", "u2", "d"),
    ]
    assert code(decide(["甲市财政局\n2025年度单位决算"], directory)) == "equivalent_candidates_tie"


def test_department_scope_still_binds_department_not_same_name_unit():
    """原件明确部门汇总口径 → 绑部门记录；同名单位的存在不改变部门绑定。"""
    directory = [org(), org("甲市财政局", "unit", "u", "d")]
    result = decide(["甲市财政局\n2025年度部门决算"], directory)
    assert result["organization_id"] == "d"


def test_unclear_scope_still_requires_confirmation():
    """范围不明（封面只有部门裸名＋决算）：不猜口径，保持人工确认。"""
    directory = [org(), org("甲市财政局", "unit", "u", "d")]
    assert code(decide(["甲市财政局\n2025年度决算"], directory)) == "scope_undetermined"


def test_cross_region_same_name_departments_stay_ambiguous():
    """两个同名部门（不同区域/父级）：候选不唯一，保持人工确认。"""
    directory = [
        org(),
        org("甲市财政局", "department", "d2", parent_id="root2"),
    ]
    assert code(decide(["甲市财政局\n2025年度部门决算"], directory)) == "equivalent_candidates_tie"


def test_benji_evidence_does_not_make_bare_names_equivalent():
    """同名映射不改变名称等价语义：裸名 ≠ 「（本级）」名，与既有口径一致。"""
    directory = [org(), org("甲市财政局", "unit", "u", "d")]
    assert not compare_org_names("甲市财政局", "甲市财政局（本级）", directory).equivalent


# ---------------------------------------------------------------------------
# 预检/提交共判：封面「（本级）」与所选无后缀同名 unit 的冲突判定
# ---------------------------------------------------------------------------


def _directory():
    return [org(), org("甲市财政局", "unit", "u", "d")]


def test_conflict_check_accepts_same_name_unit_for_benji_cover(monkeypatch):
    """封面「甲市财政局(本级)」＋所选无后缀同名 unit（父部门核实）→ 不算冲突。"""
    class _FakeStorage:
        def get_all(self):
            from types import SimpleNamespace as NS

            return [NS(**entry) for entry in _directory()]

    import api.routes.upload as upload_module

    monkeypatch.setattr(
        upload_module.runtime, "require_org_storage", lambda: _FakeStorage(), raising=True
    )
    monkeypatch.setattr(upload_module.runtime, "ORG_AVAILABLE", True, raising=True)
    conflict = _organization_binding_conflict(
        {"cover_org_name": "甲市财政局（本级）", "cover_org_label": "预算单位", "scope_hint": "unit"},
        {"name": "甲市财政局", "level": "unit", "parent_id": "d"},
    )
    assert conflict is None


def test_conflict_check_rejects_department_for_benji_cover():
    """封面「（本级）」选了部门记录 → 仍是冲突（scope 层面就该拒绝）。"""
    conflict = _organization_binding_conflict(
        {"cover_org_name": "甲市财政局（本级）", "cover_org_label": "预算单位", "scope_hint": "unit"},
        {"name": "甲市财政局", "level": "department"},
    )
    assert conflict is not None


def test_conflict_check_rejects_foreign_unit_for_benji_cover():
    """封面「（本级）」选了另一个单位 → 封面冲突保持。"""
    conflict = _organization_binding_conflict(
        {"cover_org_name": "甲市财政局（本级）", "cover_org_label": "预算单位", "scope_hint": "unit"},
        {"name": "甲市财政局服务中心", "level": "unit", "parent_id": "d"},
    )
    assert conflict is not None
    assert conflict["code"] == "organization_cover_conflict"


def test_benji_pair_helper_requires_directory_parent_proof():
    """helper 必须由目录核实父部门：无 parent_id / 父部门名不符 → 不认。"""
    assert not _cover_benji_matches_selected_unit(
        "甲市财政局(本级)", {"name": "甲市财政局", "level": "unit"}, _directory()
    )
    assert not _cover_benji_matches_selected_unit(
        "甲市财政局(本级)",
        {"name": "甲市财政局", "level": "unit", "parent_id": "d"},
        [org(), org("乙市财政局", "department", "d")],
    )
