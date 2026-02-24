"""Migration helpers for organization hierarchy refactor."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.organization import Organization, OrganizationLevel
from src.services.org_hierarchy import HierarchyValidationResult, validate_organization_hierarchy

DEFAULT_FALLBACK_DEPARTMENT = "未分类部门"


@dataclass
class DepartmentMappingRules:
    """Optional mapping rules for assigning units to departments."""

    unit_id_to_department: Dict[str, str] = field(default_factory=dict)
    unit_name_to_department: Dict[str, str] = field(default_factory=dict)
    keyword_to_department: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DepartmentMappingRules":
        def _normalize_map(data: Any) -> Dict[str, str]:
            if not isinstance(data, dict):
                return {}
            normalized: Dict[str, str] = {}
            for key, value in data.items():
                k = str(key).strip()
                v = str(value).strip()
                if k and v:
                    normalized[k] = v
            return normalized

        return cls(
            unit_id_to_department=_normalize_map(payload.get("unit_id_to_department")),
            unit_name_to_department=_normalize_map(payload.get("unit_name_to_department")),
            keyword_to_department=_normalize_map(payload.get("keyword_to_department")),
        )

    @classmethod
    def from_path(cls, path: Path) -> "DepartmentMappingRules":
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("mapping json must be an object")
            return cls.from_dict(payload)
        if suffix == ".csv":
            text = path.read_text(encoding="utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            rules = cls()
            for row in reader:
                if not row:
                    continue
                unit_id = str(row.get("unit_id") or "").strip()
                unit_name = str(row.get("unit_name") or "").strip()
                keyword = str(row.get("keyword") or "").strip()
                department_name = str(
                    row.get("department_name")
                    or row.get("department")
                    or row.get("parent")
                    or ""
                ).strip()
                if not department_name:
                    continue
                if unit_id:
                    rules.unit_id_to_department[unit_id] = department_name
                if unit_name:
                    rules.unit_name_to_department[unit_name] = department_name
                if keyword:
                    rules.keyword_to_department[keyword] = department_name
            return rules
        raise ValueError(f"unsupported mapping file suffix: {path.suffix}")


@dataclass
class UnresolvedLink:
    job_id: str
    org_id: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"job_id": self.job_id, "org_id": self.org_id, "reason": self.reason}


@dataclass
class OrganizationMigrationResult:
    """Final migration artifacts."""

    organizations_data: Dict[str, Any]
    links_data: Dict[str, Any]
    id_map: Dict[str, str]
    unresolved_links: List[UnresolvedLink]
    validation: HierarchyValidationResult
    warnings: List[str] = field(default_factory=list)

    def to_report(self) -> Dict[str, Any]:
        return {
            "organizations_total": len(self.organizations_data.get("organizations", [])),
            "links_total": len(self.links_data.get("links", [])),
            "id_map_size": len(self.id_map),
            "unresolved_links": [item.to_dict() for item in self.unresolved_links],
            "warnings": self.warnings,
            "validation": self.validation.to_dict(),
        }


def _infer_department_from_name(name: str) -> Optional[str]:
    """Infer department name from unit name when it ends with 本级."""

    match = re.match(r"^(.*?(?:局|委员会|委|办|厅|署|管理局))本级$", name)
    if match:
        inferred = match.group(1).strip()
        if inferred:
            return inferred
    return None


def _resolve_department_name(
    org: Dict[str, Any],
    parent: Optional[Dict[str, Any]],
    rules: DepartmentMappingRules,
    fallback_department: str,
) -> str:
    org_id = str(org.get("id") or "").strip()
    name = str(org.get("name") or "").strip()
    if parent and str(parent.get("level")) == OrganizationLevel.DEPARTMENT:
        return str(parent.get("name") or fallback_department)
    if org_id and org_id in rules.unit_id_to_department:
        return rules.unit_id_to_department[org_id]
    if name and name in rules.unit_name_to_department:
        return rules.unit_name_to_department[name]

    for keyword, department_name in rules.keyword_to_department.items():
        if keyword and keyword in name:
            return department_name

    inferred = _infer_department_from_name(name)
    if inferred:
        return inferred
    return fallback_department


def _build_department(
    name: str,
    existing: Dict[str, Organization],
    created_at: float,
    warnings: List[str],
) -> Organization:
    department_id = Organization.generate_id(name, OrganizationLevel.DEPARTMENT, None)
    department = existing.get(department_id)
    if department:
        return department

    department = Organization(
        id=department_id,
        name=name,
        level=OrganizationLevel.DEPARTMENT,
        parent_id=None,
        code="",
        keywords=[name],
        created_at=created_at,
        updated_at=created_at,
    )
    existing[department_id] = department
    if name == DEFAULT_FALLBACK_DEPARTMENT:
        warnings.append("some units fell back to default department assignment")
    return department


def remap_job_links(
    links: List[Dict[str, Any]],
    id_map: Dict[str, str],
    valid_org_ids: set[str],
) -> Tuple[List[Dict[str, Any]], List[UnresolvedLink]]:
    """Remap link org IDs and return unresolved links."""

    remapped_by_job: Dict[str, Dict[str, Any]] = {}
    unresolved: List[UnresolvedLink] = []

    for link in links:
        job_id = str(link.get("job_id") or "").strip()
        old_org_id = str(link.get("org_id") or "").strip()
        if not job_id or not old_org_id:
            unresolved.append(
                UnresolvedLink(job_id=job_id, org_id=old_org_id, reason="missing field")
            )
            continue

        new_org_id = id_map.get(old_org_id, old_org_id)
        if new_org_id not in valid_org_ids:
            unresolved.append(
                UnresolvedLink(job_id=job_id, org_id=old_org_id, reason="target org_id not found")
            )
            continue

        new_link = dict(link)
        new_link["org_id"] = new_org_id
        remapped_by_job[job_id] = new_link

    return list(remapped_by_job.values()), unresolved


def migrate_organization_hierarchy(
    organizations_data: Dict[str, Any],
    links_data: Dict[str, Any],
    mapping_rules: Optional[DepartmentMappingRules] = None,
    fallback_department: str = DEFAULT_FALLBACK_DEPARTMENT,
) -> OrganizationMigrationResult:
    """Migrate organization data from flat units to department->unit hierarchy."""

    mapping_rules = mapping_rules or DepartmentMappingRules()
    warnings: List[str] = []

    organizations_raw = list(organizations_data.get("organizations", []))
    links_raw = list(links_data.get("links", []))

    old_org_by_id: Dict[str, Dict[str, Any]] = {}
    for raw in organizations_raw:
        org_id = str(raw.get("id") or "").strip()
        if org_id:
            old_org_by_id[org_id] = raw

    # Keep non-unit nodes and existing departments first.
    migrated_by_id: Dict[str, Organization] = {}
    id_map: Dict[str, str] = {}

    for raw in organizations_raw:
        org_id = str(raw.get("id") or "").strip()
        level = str(raw.get("level") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not org_id or not level or not name:
            warnings.append(f"skip malformed org row: {raw}")
            continue

        if level == OrganizationLevel.UNIT:
            continue

        parent_id = raw.get("parent_id")
        code = str(raw.get("code") or "").strip()
        keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else [name]
        created_at = float(raw.get("created_at") or 0.0) or 0.0
        updated_at = float(raw.get("updated_at") or 0.0) or created_at

        org = Organization(
            id=org_id,
            name=name,
            level=level,  # type: ignore[arg-type]
            parent_id=parent_id,
            code=code,
            keywords=[str(item) for item in keywords if str(item).strip()],
            created_at=created_at,
            updated_at=updated_at,
        )
        migrated_by_id[org.id] = org
        id_map[org.id] = org.id

    # Migrate units into department->unit hierarchy.
    for raw in organizations_raw:
        org_id = str(raw.get("id") or "").strip()
        level = str(raw.get("level") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not org_id or level != OrganizationLevel.UNIT:
            continue
        if not name:
            warnings.append(f"skip unit with empty name: {org_id}")
            continue

        parent_id = raw.get("parent_id")
        parent = old_org_by_id.get(str(parent_id)) if parent_id else None
        created_at = float(raw.get("created_at") or 0.0) or 0.0
        updated_at = float(raw.get("updated_at") or 0.0) or created_at
        code = str(raw.get("code") or "").strip()
        keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else [name]

        department_name = _resolve_department_name(raw, parent, mapping_rules, fallback_department)
        department = _build_department(
            department_name,
            existing=migrated_by_id,
            created_at=created_at,
            warnings=warnings,
        )

        new_unit_id = Organization.generate_id(name, OrganizationLevel.UNIT, department.id)
        if new_unit_id in migrated_by_id:
            existing = migrated_by_id[new_unit_id]
            id_map[org_id] = existing.id
            warnings.append(
                f"unit '{name}' (old id={org_id}) deduplicated to existing id={existing.id}"
            )
            continue

        unit = Organization(
            id=new_unit_id,
            name=name,
            level=OrganizationLevel.UNIT,
            parent_id=department.id,
            code=code,
            keywords=[str(item) for item in keywords if str(item).strip()],
            created_at=created_at,
            updated_at=updated_at,
        )
        migrated_by_id[unit.id] = unit
        id_map[org_id] = unit.id

    migrated_orgs = [org.model_dump() for org in migrated_by_id.values()]
    validation = validate_organization_hierarchy(migrated_orgs)

    valid_org_ids = {org["id"] for org in migrated_orgs if org.get("id")}
    remapped_links, unresolved_links = remap_job_links(links_raw, id_map, valid_org_ids)

    organizations_meta = dict(organizations_data.get("meta") or {})
    organizations_meta["migrated"] = True
    organizations_meta["migration_type"] = "department_unit_hierarchy"
    organizations_meta["id_map_size"] = len(id_map)

    links_payload = {"links": remapped_links, "updated_at": links_data.get("updated_at")}
    organizations_payload = {"organizations": migrated_orgs, "meta": organizations_meta}

    return OrganizationMigrationResult(
        organizations_data=organizations_payload,
        links_data=links_payload,
        id_map=id_map,
        unresolved_links=unresolved_links,
        validation=validation,
        warnings=warnings,
    )


def load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
