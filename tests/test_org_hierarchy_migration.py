from __future__ import annotations

from src.services.org_hierarchy_migration import (
    DepartmentMappingRules,
    migrate_organization_hierarchy,
)


def test_migrate_flat_units_to_department_unit_and_remap_links():
    organizations_data = {
        "organizations": [
            {
                "id": "old-unit-1",
                "name": "上海市普陀区民政局本级",
                "level": "unit",
                "parent_id": None,
                "keywords": ["上海市普陀区民政局本级"],
            },
            {
                "id": "old-unit-2",
                "name": "上海市普陀区社会福利院",
                "level": "unit",
                "parent_id": None,
                "keywords": ["上海市普陀区社会福利院"],
            },
        ],
        "meta": {},
    }
    links_data = {
        "links": [
            {"job_id": "job-1", "org_id": "old-unit-1", "match_type": "manual", "confidence": 1.0},
            {"job_id": "job-2", "org_id": "old-unit-2", "match_type": "manual", "confidence": 1.0},
            {"job_id": "job-3", "org_id": "not-exists", "match_type": "manual", "confidence": 1.0},
        ],
        "updated_at": 0,
    }

    rules = DepartmentMappingRules(
        keyword_to_department={"社会福利": "上海市普陀区民政局"},
    )

    result = migrate_organization_hierarchy(
        organizations_data=organizations_data,
        links_data=links_data,
        mapping_rules=rules,
    )

    organizations = result.organizations_data["organizations"]
    levels = [item["level"] for item in organizations]
    assert "department" in levels
    assert levels.count("unit") == 2

    department = next(item for item in organizations if item["level"] == "department")
    units = [item for item in organizations if item["level"] == "unit"]
    assert all(item["parent_id"] == department["id"] for item in units)

    assert result.id_map["old-unit-1"] != "old-unit-1"
    assert result.id_map["old-unit-2"] != "old-unit-2"

    remapped_job_ids = {item["job_id"] for item in result.links_data["links"]}
    assert remapped_job_ids == {"job-1", "job-2"}
    assert len(result.unresolved_links) == 1
    assert result.unresolved_links[0].job_id == "job-3"

    assert result.validation.valid
