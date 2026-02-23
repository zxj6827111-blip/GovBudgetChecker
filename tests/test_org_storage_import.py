from __future__ import annotations

from pathlib import Path

from src.services import org_storage as org_storage_module


def _patch_storage_paths(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(org_storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(org_storage_module, "ORG_FILE", data_dir / "organizations.json")
    monkeypatch.setattr(org_storage_module, "LINKS_FILE", data_dir / "job_org_links.json")


def test_import_supports_department_unit_template(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    rows = [
        {"department_name": "上海市普陀区民政局", "unit_name": "上海市普陀区民政局本级"},
        {"department_name": "上海市普陀区民政局", "unit_name": "上海市普陀区社会福利院"},
    ]
    result = storage.import_from_list(rows, clear_existing=True)

    assert result.success
    assert result.imported == 3  # 1 department + 2 units

    departments = storage.get_by_level("department")
    units = storage.get_by_level("unit")
    assert len(departments) == 1
    assert len(units) == 2
    assert all(unit.parent_id == departments[0].id for unit in units)

    validation = storage.validate_hierarchy()
    assert validation["valid"] is True


def test_import_fails_when_parent_not_found(tmp_path: Path, monkeypatch):
    _patch_storage_paths(tmp_path, monkeypatch)
    storage = org_storage_module.OrganizationStorage()

    rows = [{"name": "孤儿单位", "level": "unit", "parent": "不存在部门"}]
    result = storage.import_from_list(rows, clear_existing=True)

    assert result.success is False
    assert result.imported == 0
    assert any("父级未找到" in msg for msg in result.errors)
