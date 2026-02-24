"""Organization hierarchy validation helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set

from src.schemas.organization import Organization

LEVEL_ORDER: Dict[str, int] = {
    "city": 0,
    "district": 1,
    "department": 2,
    "unit": 3,
}


@dataclass
class HierarchyValidationResult:
    """Validation result for organization hierarchy."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings}


def _as_org_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, Organization):
        return item.model_dump()
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    raise TypeError(f"Unsupported organization item type: {type(item)!r}")


def validate_organization_hierarchy(organizations: Iterable[Any]) -> HierarchyValidationResult:
    """Validate hierarchy constraints for organizations."""

    result = HierarchyValidationResult()
    org_list = [_as_org_dict(item) for item in organizations]
    by_id: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[str, List[str]] = defaultdict(list)

    for org in org_list:
        org_id = str(org.get("id") or "").strip()
        level = str(org.get("level") or "").strip()
        parent_id = org.get("parent_id")

        if not org_id:
            result.errors.append("organization id is missing")
            continue
        if org_id in by_id:
            result.errors.append(f"duplicate organization id: {org_id}")
            continue
        if level not in LEVEL_ORDER:
            result.errors.append(f"organization {org_id} has invalid level: {level}")
            continue

        by_id[org_id] = org
        if parent_id:
            children_by_parent[str(parent_id)].append(org_id)

    # Missing parent checks and level relation checks.
    for org_id, org in by_id.items():
        parent_id = org.get("parent_id")
        level = str(org.get("level"))
        if not parent_id:
            continue

        parent_id = str(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            result.errors.append(f"organization {org_id} references missing parent {parent_id}")
            continue

        parent_level = str(parent.get("level"))
        if parent_level == "unit":
            result.errors.append(f"organization {org_id} cannot use unit {parent_id} as parent")

        child_order = LEVEL_ORDER.get(level, -1)
        parent_order = LEVEL_ORDER.get(parent_level, -1)
        if child_order <= parent_order:
            result.errors.append(
                "organization "
                f"{org_id} level={level} is not below parent "
                f"{parent_id} level={parent_level}"
            )

        if level == "unit" and parent_level != "department":
            result.errors.append(
                f"unit organization {org_id} must be under a department, got {parent_level}"
            )

    # Unit must not have children.
    for org_id, org in by_id.items():
        if str(org.get("level")) == "unit" and children_by_parent.get(org_id):
            result.errors.append(f"unit organization {org_id} should not have children")

    # Department should normally contain units.
    for org_id, org in by_id.items():
        if str(org.get("level")) == "department" and not children_by_parent.get(org_id):
            result.warnings.append(f"department organization {org_id} has no child units")

    # Cycle detection.
    visited: Set[str] = set()
    visiting: Set[str] = set()

    def dfs(node_id: str, chain: List[str]) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            cycle = " -> ".join(chain + [node_id])
            result.errors.append(f"cycle detected: {cycle}")
            return

        visiting.add(node_id)
        parent_id = by_id[node_id].get("parent_id")
        if parent_id and str(parent_id) in by_id:
            dfs(str(parent_id), chain + [node_id])
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in by_id:
        if node_id not in visited:
            dfs(node_id, [])

    return result
