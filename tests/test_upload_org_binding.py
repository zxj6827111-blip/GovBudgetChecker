from __future__ import annotations

from api.routes.upload import _organization_binding_conflict


def test_manual_org_binding_rejects_specific_cover_name_mismatch() -> None:
    conflict = _organization_binding_conflict(
        {
            "cover_org_name": "规划和自然资源局",
            "cover_org_label": "主管部门",
            "scope_hint": "department",
        },
        {"name": "人民政府办公室", "level": "department"},
    )

    assert conflict is not None
    assert conflict["code"] == "organization_cover_conflict"


def test_manual_org_binding_accepts_admin_prefix_variant() -> None:
    conflict = _organization_binding_conflict(
        {
            "cover_org_name": "人民政府办公室",
            "cover_org_label": "预算单位",
            "scope_hint": "unit",
        },
        {"name": "上海市普陀区人民政府办公室", "level": "unit"},
    )

    assert conflict is None


def test_manual_org_binding_rejects_scope_level_mismatch() -> None:
    conflict = _organization_binding_conflict(
        {
            "cover_org_name": "规划和自然资源局",
            "cover_org_label": "主管部门",
            "scope_hint": "department",
        },
        {"name": "规划和自然资源局", "level": "unit"},
    )

    assert conflict is not None
    assert conflict["code"] == "organization_scope_conflict"


def test_missing_cover_name_does_not_guess_a_conflict() -> None:
    assert _organization_binding_conflict(
        {"cover_org_name": "", "scope_hint": ""},
        {"name": "任意组织", "level": "unit"},
    ) is None
