"""Sync tianbaoxitong / PS organization master data into `data/organizations.json`.

Supports two source modes:
1. PostgreSQL via `--database-url`
2. SQL dump parsing via `--sql-dump`
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.schemas.organization import Organization, OrganizationStore
from src.services.org_hierarchy import validate_organization_hierarchy


@dataclass
class PSDepartment:
    id: str
    code: Optional[str]
    name: str
    parent_id: Optional[str]
    sort_order: int = 0


@dataclass
class PSUnit:
    id: str
    department_id: str
    code: Optional[str]
    name: str
    sort_order: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync PS / tianbaoxitong organizations into GovBudgetChecker organizations.json"
    )
    parser.add_argument(
        "--database-url",
        help="PostgreSQL connection URL for the PS / tianbaoxitong database.",
    )
    parser.add_argument(
        "--sql-dump",
        help="Path to a pg_dump SQL file containing org_department / org_unit COPY data.",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "data" / "organizations.json"),
        help="Output organizations.json path.",
    )
    parser.add_argument(
        "--city-name",
        default="",
        help="Optional city node name to prepend, e.g. 上海市.",
    )
    parser.add_argument(
        "--district-name",
        default="",
        help="Optional district node name to prepend, e.g. 普陀区.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Backup the existing output file before overwriting it.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the synced result summary.",
    )
    args = parser.parse_args()
    if not args.database_url and not args.sql_dump:
        parser.error("one of --database-url or --sql-dump is required")
    return args


async def load_from_database(database_url: str) -> Tuple[List[PSDepartment], List[PSUnit]]:
    conn = await asyncpg.connect(database_url)
    try:
        department_rows = await conn.fetch(
            """
            SELECT id, code, name, parent_id, sort_order
            FROM org_department
            ORDER BY sort_order ASC, name ASC, id ASC
            """
        )
        unit_rows = await conn.fetch(
            """
            SELECT id, department_id, code, name, sort_order
            FROM org_unit
            ORDER BY sort_order ASC, name ASC, id ASC
            """
        )
    finally:
        await conn.close()

    departments = [
        PSDepartment(
            id=str(row["id"]),
            code=_text_or_none(row["code"]),
            name=str(row["name"]),
            parent_id=_text_or_none(row["parent_id"]),
            sort_order=int(row["sort_order"] or 0),
        )
        for row in department_rows
    ]
    units = [
        PSUnit(
            id=str(row["id"]),
            department_id=str(row["department_id"]),
            code=_text_or_none(row["code"]),
            name=str(row["name"]),
            sort_order=int(row["sort_order"] or 0),
        )
        for row in unit_rows
    ]
    return departments, units


def load_from_sql_dump(path: Path) -> Tuple[List[PSDepartment], List[PSUnit]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    department_rows = _parse_copy_table(text, "org_department")
    unit_rows = _parse_copy_table(text, "org_unit")

    departments = [
        PSDepartment(
            id=str(row["id"]),
            code=_text_or_none(row.get("code")),
            name=str(row["name"]),
            parent_id=_text_or_none(row.get("parent_id")),
            sort_order=int(row.get("sort_order") or 0),
        )
        for row in department_rows
    ]
    units = [
        PSUnit(
            id=str(row["id"]),
            department_id=str(row["department_id"]),
            code=_text_or_none(row.get("code")),
            name=str(row["name"]),
            sort_order=int(row.get("sort_order") or 0),
        )
        for row in unit_rows
    ]
    return departments, units


def _parse_copy_table(sql_text: str, table_name: str) -> List[Dict[str, Optional[str]]]:
    header_pattern = re.compile(
        rf"^COPY public\.{re.escape(table_name)} \((?P<cols>[^)]+)\) FROM stdin;$"
    )
    lines = sql_text.splitlines()
    header_index: Optional[int] = None
    columns: List[str] = []
    for index, line in enumerate(lines):
        match = header_pattern.match(line.strip())
        if match is None:
            continue
        header_index = index
        columns = [column.strip() for column in match.group("cols").split(",")]
        break

    if header_index is None or not columns:
        return []

    rows: List[Dict[str, Optional[str]]] = []
    for raw_line in lines[header_index + 1 :]:
        stripped = raw_line.strip()
        if stripped == r"\.":
            break
        if not stripped:
            continue
        values = raw_line.split("\t")
        row: Dict[str, Optional[str]] = {}
        for index, column in enumerate(columns):
            value = values[index] if index < len(values) else None
            row[column] = None if value in (None, r"\N") else value
        rows.append(row)
    return rows


def build_store(
    departments: Sequence[PSDepartment],
    units: Sequence[PSUnit],
    *,
    city_name: str = "",
    district_name: str = "",
) -> OrganizationStore:
    organizations: List[Organization] = []
    parent_root_id: Optional[str] = None
    department_name_to_org_id: Dict[str, str] = {}
    department_name_by_source_id: Dict[str, str] = {
        department.id: department.name for department in departments
    }

    city_name = city_name.strip()
    district_name = district_name.strip()
    if city_name:
        city = Organization(
            id=Organization.generate_id(city_name, "city", None),
            name=city_name,
            level="city",
            parent_id=None,
            code=None,
            keywords=_keyword_variants(city_name),
        )
        organizations.append(city)
        parent_root_id = city.id

    if district_name:
        district = Organization(
            id=Organization.generate_id(district_name, "district", parent_root_id),
            name=district_name,
            level="district",
            parent_id=parent_root_id,
            code=None,
            keywords=_keyword_variants(district_name),
        )
        organizations.append(district)
        parent_root_id = district.id

    dept_org_ids: Dict[str, str] = {}
    sorted_departments = sorted(
        departments,
        key=lambda item: (item.sort_order, item.name, item.id),
    )
    for department in sorted_departments:
        org_id = Organization.generate_id(department.name, "department", parent_root_id)
        dept_org_ids[department.id] = org_id
        department_name_to_org_id[department.name] = org_id
        organizations.append(
            Organization(
                id=org_id,
                name=department.name,
                level="department",
                parent_id=parent_root_id,
                code=department.code,
                keywords=_keyword_variants(department.name),
            )
        )

    sorted_units = sorted(
        units,
        key=lambda item: (item.sort_order, item.name, item.id),
    )
    for unit in sorted_units:
        source_department_name = department_name_by_source_id.get(unit.department_id, "")
        department_org_id = dept_org_ids.get(unit.department_id)
        if department_org_id is None and not source_department_name:
            continue
        if _should_promote_unit_to_department(unit.name, source_department_name):
            promoted_department_id = department_name_to_org_id.get(unit.name)
            if promoted_department_id is None:
                promoted_department_id = Organization.generate_id(unit.name, "department", parent_root_id)
                department_name_to_org_id[unit.name] = promoted_department_id
                organizations.append(
                    Organization(
                        id=promoted_department_id,
                        name=unit.name,
                        level="department",
                        parent_id=parent_root_id,
                        code=None,
                        keywords=_keyword_variants(unit.name),
                    )
                )
            department_org_id = promoted_department_id
        if department_org_id is None:
            continue
        organizations.append(
            Organization(
                id=Organization.generate_id(unit.name, "unit", department_org_id),
                name=unit.name,
                level="unit",
                parent_id=department_org_id,
                code=unit.code,
                keywords=_keyword_variants(unit.name),
            )
        )

    validation = validate_organization_hierarchy(organizations)
    if validation.errors:
        raise ValueError("organization hierarchy invalid: " + "; ".join(validation.errors))

    return OrganizationStore(
        organizations=organizations,
        meta={
            "source": "tianbaoxitong",
            "synced_at": time.time(),
            "department_count": len(departments),
            "unit_count": len(units),
        },
    )


def write_store(store: OrganizationStore, output_path: Path, backup: bool = False) -> Optional[Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Optional[Path] = None
    if backup and output_path.exists():
        backup_path = output_path.with_suffix(output_path.suffix + f".bak.{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(output_path, backup_path)
    output_path.write_text(
        json.dumps(store.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_path


def _keyword_variants(name: str) -> List[str]:
    clean_name = str(name or "").strip()
    variants = {clean_name}
    stripped = clean_name
    for suffix in ("单位", "部门", "委员会", "人民政府", "办公室", "办事处"):
        if stripped.endswith(suffix) and len(stripped) > len(suffix):
            variants.add(stripped[: -len(suffix)])
    return sorted(item for item in variants if item)


def _should_promote_unit_to_department(unit_name: str, parent_department_name: str) -> bool:
    clean_unit_name = str(unit_name or "").strip()
    clean_parent_name = str(parent_department_name or "").strip()
    if not clean_unit_name or clean_unit_name.endswith("本级"):
        return False
    if not _is_department_like_name(clean_unit_name):
        return False
    if not clean_parent_name:
        return True
    return not _names_are_related(clean_unit_name, clean_parent_name)


def _is_department_like_name(name: str) -> bool:
    clean_name = str(name or "").strip()
    if not clean_name or clean_name.endswith("本级"):
        return False
    if clean_name.endswith("局"):
        return True
    patterns = (
        "人民政府办公室",
        "街道办事处",
        "镇人民政府",
        "委员会",
        "总工会",
        "人民法院",
        "人民检察院",
    )
    return any(clean_name.endswith(pattern) for pattern in patterns)


def _names_are_related(left: str, right: str) -> bool:
    left_norm = _normalize_match_text(left)
    right_norm = _normalize_match_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_core = _strip_admin_prefix(left_norm)
    right_core = _strip_admin_prefix(right_norm)
    if not left_core or not right_core:
        return False
    return left_core in right_core or right_core in left_core


def _normalize_match_text(text: object) -> str:
    value = str(text or "").strip()
    value = re.sub(r"20\d{2}(?:年度|年)?", "", value)
    value = re.sub(r"\d{2}(?:年度|年)", "", value)
    value = re.sub(r"(预算|决算|报告|公开|年度|pdf)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[\s（）()【】\[\]<>《》·,，、.\-_/]+", "", value)
    return value


def _strip_admin_prefix(text: str) -> str:
    value = str(text or "")
    for token in (
        "上海市普陀区",
        "上海市",
        "普陀区",
        "上海",
        "人民政府",
        "中国共产党",
        "中共",
    ):
        value = value.replace(token, "")
    return value


def _text_or_none(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


async def _load_source(args: argparse.Namespace) -> Tuple[List[PSDepartment], List[PSUnit]]:
    if args.database_url:
        return await load_from_database(args.database_url)
    return load_from_sql_dump(Path(args.sql_dump).resolve())


async def async_main(args: argparse.Namespace) -> int:
    departments, units = await _load_source(args)
    store = build_store(
        departments,
        units,
        city_name=str(args.city_name or ""),
        district_name=str(args.district_name or ""),
    )
    output_path = Path(args.output).resolve()
    backup_path = write_store(store, output_path, backup=bool(args.backup))

    summary = {
        "output": str(output_path),
        "backup": str(backup_path) if backup_path else None,
        "departments": len(departments),
        "units": len(units),
        "organizations": len(store.organizations),
    }
    if args.pretty:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
