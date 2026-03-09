from __future__ import annotations

from pathlib import Path

from src.schemas.organization import Organization
from src.services import org_matcher as org_matcher_module
from src.services import org_storage as org_storage_module


def _patch_storage_paths(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(org_storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(org_storage_module, "ORG_FILE", data_dir / "organizations.json")
    monkeypatch.setattr(org_storage_module, "LINKS_FILE", data_dir / "job_org_links.json")
    monkeypatch.setattr(org_storage_module, "_storage_instance", None)
    monkeypatch.setattr(org_matcher_module, "_matcher_instance", None)


def test_org_matcher_prefers_unit_when_filename_mentions_unit(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    department = storage.add(
        Organization(
            id="dept-office",
            name="上海市普陀区人民政府办公室",
            level="department",
            parent_id=None,
            code="D-OFFICE",
            keywords=["人民政府办公室"],
        )
    )
    unit = storage.add(
        Organization(
            id="unit-office",
            name="上海市普陀区人民政府办公室",
            level="unit",
            parent_id=department.id,
            code="U-OFFICE",
            keywords=["人民政府办公室"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match("上海市普陀区人民政府办公室单位25年预算.pdf")

    assert matched_org is not None
    assert matched_org.id == unit.id
    assert confidence >= 0.6


def test_org_matcher_prefers_department_when_filename_is_department_report(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    department = storage.add(
        Organization(
            id="dept-office",
            name="上海市普陀区人民政府办公室",
            level="department",
            parent_id=None,
            code="D-OFFICE",
            keywords=["人民政府办公室"],
        )
    )
    storage.add(
        Organization(
            id="unit-office",
            name="上海市普陀区人民政府办公室",
            level="unit",
            parent_id=department.id,
            code="U-OFFICE",
            keywords=["人民政府办公室"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match("上海市普陀区人民政府办公室25年预算.pdf")

    assert matched_org is not None
    assert matched_org.id == department.id
    assert confidence >= 0.6


def test_org_matcher_prefers_street_office_over_generic_government_office_unit(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    district = storage.add(
        Organization(
            id="district-putuo",
            name="普陀区",
            level="district",
            parent_id=None,
            code="DIST-PUTUO",
            keywords=["普陀区"],
        )
    )
    storage.add(
        Organization(
            id="unit-office",
            name="上海市普陀区人民政府办公室",
            level="unit",
            parent_id=district.id,
            code="U-OFFICE",
            keywords=["上海市普陀区人民政府", "上海市普陀区人民政府办公室"],
        )
    )
    street = storage.add(
        Organization(
            id="dept-wanli",
            name="上海市普陀区人民政府万里街道办事处",
            level="department",
            parent_id=district.id,
            code="D-WANLI",
            keywords=["上海市普陀区人民政府万里街道", "上海市普陀区人民政府万里街道办事处"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match(
        "万里街道(本部)_上海市普陀区人民政府万里街道办事处（本部）2026年区级单位预算.pdf"
    )

    assert matched_org is not None
    assert matched_org.id == street.id
    assert confidence >= 0.9


def test_org_matcher_matches_town_budget_table_to_town_government(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    city = storage.add(
        Organization(
            id="city-shanghai",
            name="上海市",
            level="city",
            parent_id=None,
            code="CITY-SH",
            keywords=["上海市"],
        )
    )
    district = storage.add(
        Organization(
            id="district-putuo",
            name="普陀区",
            level="district",
            parent_id=city.id,
            code="DIST-PUTUO",
            keywords=["普陀区"],
        )
    )
    town = storage.add(
        Organization(
            id="dept-changzheng",
            name="上海市普陀区长征镇人民政府",
            level="department",
            parent_id=district.id,
            code="D-CZ",
            keywords=["上海市普陀区长征镇", "上海市普陀区长征镇人民政府"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match(
        "长征镇(本部)_上海市普陀区长征镇2025年预算执行和2026年预算表.pdf"
    )

    assert matched_org is not None
    assert matched_org.id == town.id
    assert confidence >= 0.8


def test_org_matcher_matches_town_report_without_city_prefix(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    district = storage.add(
        Organization(
            id="district-putuo",
            name="普陀区",
            level="district",
            parent_id=None,
            code="DIST-PUTUO",
            keywords=["普陀区"],
        )
    )
    town = storage.add(
        Organization(
            id="dept-changzheng",
            name="上海市普陀区长征镇人民政府",
            level="department",
            parent_id=district.id,
            code="D-CZ",
            keywords=["上海市普陀区长征镇", "上海市普陀区长征镇人民政府"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match(
        "长征镇(本部)_关于普陀区长征镇2025年预算执行情况和2026年预算草案的报告.pdf"
    )

    assert matched_org is not None
    assert matched_org.id == town.id
    assert confidence >= 0.8


def test_org_matcher_does_not_map_service_center_to_government_office_by_region_only(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    district = storage.add(
        Organization(
            id="district-putuo",
            name="普陀区",
            level="district",
            parent_id=None,
            code="DIST-PUTUO",
            keywords=["普陀区"],
        )
    )
    storage.add(
        Organization(
            id="dept-office",
            name="上海市普陀区人民政府办公室",
            level="department",
            parent_id=district.id,
            code="D-OFFICE",
            keywords=["上海市普陀区人民政府", "上海市普陀区人民政府办公室"],
        )
    )
    storage.add(
        Organization(
            id="unit-office",
            name="上海市普陀区人民政府办公室",
            level="unit",
            parent_id="dept-office",
            code="U-OFFICE",
            keywords=["上海市普陀区人民政府", "上海市普陀区人民政府办公室"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match(
        "上海市普陀区残疾人综合服务中心_上海市普陀区残疾人综合服务中心2026年单位预算.pdf"
    )

    assert matched_org is None
    assert confidence == 0


def test_org_matcher_handles_parenthetical_aliases_from_org_master(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    center = storage.add(
        Organization(
            id="unit-shirong",
            name="上海市普陀区市容管理中心（上海市普陀区景观建设中心）",
            level="unit",
            parent_id=None,
            code="U-SRGL",
            keywords=["上海市普陀区市容管理中心（上海市普陀区景观建设中心）"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match(
        "上海市普陀区市容管理中心_上海市普陀区市容管理中心2026年单位预算.pdf"
    )

    assert matched_org is not None
    assert matched_org.id == center.id
    assert confidence >= 0.7


def test_org_matcher_handles_bureau_name_when_master_has_enforcement_brigade_suffix(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    bureau = storage.add(
        Organization(
            id="unit-chengguan",
            name="上海市普陀区城市管理行政执法局执法大队",
            level="unit",
            parent_id=None,
            code="U-CGZF",
            keywords=["上海市普陀区城市管理行政执法局执法大队"],
        )
    )

    matcher = org_matcher_module.OrgMatcher()

    matched_org, confidence = matcher.match(
        "城管执法局_上海市普陀区城市管理行政执法局2026年度单位预算公开.pdf"
    )

    assert matched_org is not None
    assert matched_org.id == bureau.id
    assert confidence >= 0.7
