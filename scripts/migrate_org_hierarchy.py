"""CLI tool for migrating organization data into department->unit hierarchy."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

def _default_data_path(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "data" / name


def _build_parser() -> argparse.ArgumentParser:
    from src.services.org_hierarchy_migration import DEFAULT_FALLBACK_DEPARTMENT

    parser = argparse.ArgumentParser(description="Migrate organizations into hierarchy structure")
    parser.add_argument(
        "--org-file",
        type=Path,
        default=_default_data_path("organizations.json"),
        help="source organizations.json path",
    )
    parser.add_argument(
        "--links-file",
        type=Path,
        default=_default_data_path("job_org_links.json"),
        help="source job_org_links.json path",
    )
    parser.add_argument(
        "--mapping-file",
        type=Path,
        default=None,
        help="optional mapping file (.json or .csv)",
    )
    parser.add_argument(
        "--fallback-department",
        type=str,
        default=DEFAULT_FALLBACK_DEPARTMENT,
        help="fallback department name when no mapping rule matches",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="write back to source files (creates .bak backup)",
    )
    parser.add_argument(
        "--output-org-file",
        type=Path,
        default=None,
        help="output organizations file when not --in-place",
    )
    parser.add_argument(
        "--output-links-file",
        type=Path,
        default=None,
        help="output links file when not --in-place",
    )
    parser.add_argument(
        "--output-id-map-file",
        type=Path,
        default=None,
        help="optional output path for old->new id map json",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="optional output path for migration report json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run migration and print report only, no file writes",
    )
    return parser


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
    backup = path.with_suffix(path.suffix + f".bak.{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def _resolve_output_path(args: argparse.Namespace, source: Path, explicit: Optional[Path]) -> Path:
    if args.in_place:
        return source
    if explicit:
        return explicit
    return source.with_name(source.stem + ".migrated" + source.suffix)


def main() -> int:
    from src.services.org_hierarchy_migration import (
        DepartmentMappingRules,
        load_json,
        migrate_organization_hierarchy,
        write_json,
    )

    parser = _build_parser()
    args = parser.parse_args()

    organizations_data = load_json(args.org_file)
    links_data = load_json(args.links_file)

    mapping = DepartmentMappingRules()
    if args.mapping_file:
        mapping = DepartmentMappingRules.from_path(args.mapping_file)

    result = migrate_organization_hierarchy(
        organizations_data=organizations_data,
        links_data=links_data,
        mapping_rules=mapping,
        fallback_department=args.fallback_department,
    )
    report: Dict[str, Any] = result.to_report()
    report["org_file"] = str(args.org_file)
    report["links_file"] = str(args.links_file)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    output_org = _resolve_output_path(args, args.org_file, args.output_org_file)
    output_links = _resolve_output_path(args, args.links_file, args.output_links_file)

    if args.in_place:
        _backup_file(args.org_file)
        _backup_file(args.links_file)

    _ensure_parent(output_org)
    _ensure_parent(output_links)
    write_json(output_org, result.organizations_data)
    write_json(output_links, result.links_data)

    if args.output_id_map_file:
        _ensure_parent(args.output_id_map_file)
        write_json(args.output_id_map_file, {"id_map": result.id_map})
    if args.report_file:
        _ensure_parent(args.report_file)
        write_json(args.report_file, report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
