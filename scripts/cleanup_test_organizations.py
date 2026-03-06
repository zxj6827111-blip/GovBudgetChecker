#!/usr/bin/env python3
"""Batch cleanup test organizations directly from JSON data files.

This script does not import project runtime modules, so it can run on servers
that do not have optional database dependencies installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence, Set


DEFAULT_PATTERNS = (
    r"^测试部门-[0-9a-f]{8}$",
    r"^范围测试部门-[0-9a-f]{8}$",
    r"^测试单位-[0-9a-f]{8}$",
    r"^范围测试单位-[0-9a-f]{8}$",
    r"^stale-job-org-[0-9a-f]{8}$",
    r"^stats-dept-[0-9a-f]{8}$",
    r"^stats-unit-[0-9a-f]{8}$",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch delete test organizations and optionally linked upload jobs."
    )
    parser.add_argument(
        "--pattern",
        action="append",
        dest="patterns",
        help="Additional regex pattern to match organization names. Can be repeated.",
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("ORG_DATA_DIR", "data"),
        help="Directory containing organizations.json and job_org_links.json.",
    )
    parser.add_argument(
        "--uploads-dir",
        default=os.getenv("UPLOAD_DIR", "uploads"),
        help="Directory containing upload job folders.",
    )
    parser.add_argument(
        "--delete-linked-job-dirs",
        action="store_true",
        help="Also delete upload directories linked to the matched organizations.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist deletions. Without this flag the script only prints a dry-run summary.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="How many matched organizations / linked jobs to print. Default: 30",
    )
    return parser.parse_args()


def compile_patterns(extra_patterns: Sequence[str] | None) -> List[re.Pattern[str]]:
    patterns = list(DEFAULT_PATTERNS)
    for pattern in extra_patterns or ():
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return [re.compile(pattern) for pattern in patterns]


def backup_file(path: Path) -> Path:
    backup_path = path.with_suffix(path.suffix + f".bak.{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup_path)
    return backup_path


def build_children_map(orgs: List[dict]) -> Dict[str | None, List[str]]:
    mapping: Dict[str | None, List[str]] = {}
    for org in orgs:
        parent_id = org.get("parent_id")
        mapping.setdefault(parent_id, []).append(str(org["id"]))
    return mapping


def collect_descendants(root_id: str, children_by_parent: Dict[str | None, List[str]]) -> Set[str]:
    result: Set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children_by_parent.get(current, []))
    return result


def minimal_root_ids(candidate_ids: Set[str], parent_by_id: Dict[str, str | None]) -> List[str]:
    roots: List[str] = []
    for org_id in sorted(candidate_ids):
        parent_id = parent_by_id.get(org_id)
        should_skip = False
        while parent_id:
            if parent_id in candidate_ids:
                should_skip = True
                break
            parent_id = parent_by_id.get(parent_id)
        if not should_skip:
            roots.append(org_id)
    return roots


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    uploads_dir = Path(args.uploads_dir).resolve()
    org_file = data_dir / "organizations.json"
    links_file = data_dir / "job_org_links.json"

    if not org_file.exists() or not links_file.exists():
        print(f"missing data files under {data_dir}", file=sys.stderr)
        return 1

    org_payload = json.loads(org_file.read_text(encoding="utf-8"))
    links_payload = json.loads(links_file.read_text(encoding="utf-8"))
    orgs = list(org_payload.get("organizations", []))
    links = list(links_payload.get("links", []))

    patterns = compile_patterns(args.patterns)
    parent_by_id = {str(org["id"]): org.get("parent_id") for org in orgs}
    children_by_parent = build_children_map(orgs)

    matched_orgs = [
        org for org in orgs if any(pattern.search(str(org.get("name") or "")) for pattern in patterns)
    ]
    matched_org_ids = {str(org["id"]) for org in matched_orgs}
    root_delete_ids = minimal_root_ids(matched_org_ids, parent_by_id)

    delete_scope_ids: Set[str] = set()
    for root_id in root_delete_ids:
        delete_scope_ids.update(collect_descendants(root_id, children_by_parent))

    linked_job_ids = sorted(
        {
            str(link.get("job_id") or "")
            for link in links
            if str(link.get("org_id") or "") in delete_scope_ids and str(link.get("job_id") or "")
        }
    )

    print(f"matched organizations = {len(matched_orgs)}")
    print(f"root deletions        = {len(root_delete_ids)}")
    print(f"cascade org count     = {len(delete_scope_ids)}")
    print(f"linked jobs to unlink = {len(linked_job_ids)}")
    print(f"delete job dirs       = {'yes' if args.delete_linked_job_dirs else 'no'}")

    if matched_orgs:
        print("\nSample matched organizations:")
        for org in matched_orgs[: args.limit]:
            print(
                f"  {org.get('id')} | {org.get('level')} | {org.get('name')} | parent={org.get('parent_id')}"
            )

    if linked_job_ids:
        print("\nSample linked jobs:")
        for job_id in linked_job_ids[: args.limit]:
            print(f"  {job_id}")

    if not args.write:
        print("\nDry run only. Re-run with --write to persist changes.")
        return 0

    org_backup = backup_file(org_file)
    links_backup = backup_file(links_file)
    print("\nBackups created:")
    print(f"  {org_backup}")
    print(f"  {links_backup}")

    org_payload["organizations"] = [
        org for org in orgs if str(org.get("id") or "") not in delete_scope_ids
    ]
    org_payload.setdefault("meta", {})
    org_payload["meta"]["updated_at"] = time.time()

    links_payload["links"] = [
        link for link in links if str(link.get("org_id") or "") not in delete_scope_ids
    ]
    links_payload["updated_at"] = time.time()

    org_file.write_text(json.dumps(org_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    links_file.write_text(json.dumps(links_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDeleted organizations: {len(delete_scope_ids)}")
    print(f"Removed links: {len(links) - len(links_payload['links'])}")

    if args.delete_linked_job_dirs:
        removed_dirs = 0
        missing_dirs = 0
        for job_id in linked_job_ids:
            job_dir = uploads_dir / job_id
            if job_dir.is_dir():
                shutil.rmtree(job_dir)
                removed_dirs += 1
            else:
                missing_dirs += 1
        print(f"Deleted upload dirs: {removed_dirs}")
        print(f"Missing upload dirs: {missing_dirs}")

    print("\nDone. Restart backend to refresh in-memory organization data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
