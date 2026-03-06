#!/usr/bin/env python3
"""Rebuild job-to-organization links from current upload directories.

This utility is intended for recovery scenarios where `uploads/` and
`data/job_org_links.json` drift apart. It scans current upload job folders,
matches each PDF filename against organizations, and optionally rewrites
`job_org_links.json` with the rebuilt associations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class OrgRecord:
    id: str
    name: str
    level: str
    parent_id: Optional[str]
    keywords: Tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild data/job_org_links.json from current uploads/."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing organizations.json and job_org_links.json.",
    )
    parser.add_argument(
        "--uploads-dir",
        default="uploads",
        help="Directory containing job upload folders.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.6,
        help="Minimum matching score to auto-link a job. Default: 0.6",
    )
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="Drop existing links whose job directory no longer exists.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the rebuilt links back to job_org_links.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many sample matches/unmatched items to print. Default: 20",
    )
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_orgs(path: Path) -> List[OrgRecord]:
    raw = load_json(path).get("organizations", [])
    orgs: List[OrgRecord] = []
    for item in raw:
        org_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        level = str(item.get("level") or "").strip()
        if not org_id or not name:
            continue
        keywords_raw = item.get("keywords") or []
        keywords = tuple(
            str(keyword).strip()
            for keyword in keywords_raw
            if str(keyword).strip()
        )
        orgs.append(
            OrgRecord(
                id=org_id,
                name=name,
                level=level,
                parent_id=item.get("parent_id"),
                keywords=keywords,
            )
        )
    return orgs


def load_links(path: Path) -> List[Dict[str, Any]]:
    return list(load_json(path).get("links", []))


def clean_text(text: str) -> str:
    cleaned = str(text or "")
    for token in (
        ".pdf",
        ".PDF",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
        "2026",
        "年度",
        "公开",
    ):
        cleaned = cleaned.replace(token, " ")
    return " ".join(cleaned.split())


def extract_core_name(name: str) -> str:
    core = name.strip()
    suffixes = (
        "本级",
        "委员会",
        "管理局",
        "人民政府",
        "事务中心",
        "服务中心",
        "保障中心",
        "中心",
        "办公室",
        "财政局",
        "民政局",
        "教育局",
        "体育局",
        "卫健委",
        "委员会",
        "局",
        "委",
        "办",
        "站",
        "所",
        "院",
        "馆",
        "校",
    )
    for suffix in suffixes:
        if core.endswith(suffix) and len(core) > len(suffix):
            return core[: -len(suffix)]
    return core


def fuzzy_match(name: str, text: str, min_chars: int = 3) -> bool:
    if len(name) < min_chars:
        return name in text
    for idx in range(len(name) - min_chars + 1):
        if name[idx : idx + min_chars] in text:
            return True
    return False


def score_org(org: OrgRecord, search_text: str) -> float:
    score = 0.0
    if org.name and org.name in search_text:
        score = max(score, 0.9)
    for keyword in org.keywords:
        if keyword and keyword in search_text:
            score = max(score, 0.8)
    core_name = extract_core_name(org.name)
    if core_name and core_name in search_text:
        score = max(score, 0.7)
    if fuzzy_match(org.name, search_text):
        score = max(score, 0.5)
    return score


def infer_doc_scope(filename: str) -> Optional[str]:
    text = filename or ""
    has_department = "部门" in text
    has_unit = "单位" in text or "本级" in text
    if has_department and not has_unit:
        return "department"
    if has_unit and not has_department:
        return "unit"
    return None


def rank_candidates(orgs: Sequence[OrgRecord], filename: str) -> List[Tuple[OrgRecord, float]]:
    search_text = clean_text(filename)
    doc_scope = infer_doc_scope(filename)
    ranked: List[Tuple[OrgRecord, float]] = []
    for org in orgs:
        score = score_org(org, search_text)
        if score <= 0:
            continue
        if doc_scope == org.level:
            score += 0.08
        elif doc_scope is not None and org.level in {"department", "unit"}:
            score -= 0.03
        ranked.append((org, score))
    ranked.sort(key=lambda item: (item[1], len(item[0].name)), reverse=True)
    return ranked


def first_pdf_name(job_dir: Path) -> Optional[str]:
    pdfs = sorted(job_dir.glob("*.pdf"))
    if pdfs:
        return pdfs[0].name
    status_path = job_dir / "status.json"
    if status_path.exists():
        try:
            payload = load_json(status_path)
            filename = str(payload.get("filename") or "").strip()
            if filename:
                return filename
        except Exception:
            return None
    return None


def backup_file(path: Path) -> Path:
    backup_path = path.with_suffix(path.suffix + f".bak.{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup_path)
    return backup_path


def iter_job_dirs(uploads_dir: Path) -> Iterable[Path]:
    for child in sorted(uploads_dir.iterdir()):
        if child.is_dir():
            yield child


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    uploads_dir = Path(args.uploads_dir).resolve()
    org_file = data_dir / "organizations.json"
    links_file = data_dir / "job_org_links.json"

    if not org_file.exists():
        print(f"organizations.json not found: {org_file}", file=sys.stderr)
        return 1
    if not links_file.exists():
        print(f"job_org_links.json not found: {links_file}", file=sys.stderr)
        return 1
    if not uploads_dir.exists():
        print(f"uploads dir not found: {uploads_dir}", file=sys.stderr)
        return 1

    orgs = load_orgs(org_file)
    links = load_links(links_file)

    uploads = {job_dir.name for job_dir in iter_job_dirs(uploads_dir)}
    existing_links_by_job: Dict[str, Dict[str, Any]] = {}
    stale_links: List[Dict[str, Any]] = []
    kept_links: List[Dict[str, Any]] = []

    for link in links:
        job_id = str(link.get("job_id") or "").strip()
        if not job_id:
            continue
        if job_id in uploads:
            existing_links_by_job[job_id] = link
            kept_links.append(link)
        else:
            stale_links.append(link)

    orphan_job_ids = sorted(uploads - set(existing_links_by_job))
    matched_links: List[Dict[str, Any]] = []
    unmatched: List[Tuple[str, str, List[Tuple[str, str, float]]]] = []

    for job_id in orphan_job_ids:
        job_dir = uploads_dir / job_id
        filename = first_pdf_name(job_dir) or ""
        ranked = rank_candidates(orgs, filename)
        if ranked and ranked[0][1] >= args.min_score:
            best_org, best_score = ranked[0]
            matched_links.append(
                {
                    "job_id": job_id,
                    "org_id": best_org.id,
                    "match_type": "manual",
                    "confidence": round(min(best_score, 1.0), 4),
                    "created_at": time.time(),
                }
            )
        else:
            top = [
                (org.id, org.name, round(score, 4))
                for org, score in ranked[:5]
            ]
            unmatched.append((job_id, filename, top))

    final_links = list(kept_links)
    final_links.extend(matched_links)
    if not args.prune_stale:
        final_links.extend(stale_links)

    print(f"organizations           = {len(orgs)}")
    print(f"upload job dirs         = {len(uploads)}")
    print(f"existing links          = {len(links)}")
    print(f"valid links kept        = {len(kept_links)}")
    print(f"stale links found       = {len(stale_links)}")
    print(f"orphan upload jobs      = {len(orphan_job_ids)}")
    print(f"auto-matched orphan jobs= {len(matched_links)}")
    print(f"unmatched orphan jobs   = {len(unmatched)}")
    print(f"final links             = {len(final_links)}")

    if matched_links:
        print("\nSample rebuilt links:")
        for link in matched_links[: args.limit]:
            org = next((item for item in orgs if item.id == link["org_id"]), None)
            print(
                f"  {link['job_id']} -> {link['org_id']} "
                f"{org.name if org else '<missing org>'} "
                f"(confidence={link['confidence']})"
            )

    if unmatched:
        print("\nSample unmatched jobs:")
        for job_id, filename, top in unmatched[: args.limit]:
            print(f"  {job_id} | {filename or '<no pdf name>'}")
            if top:
                for org_id, name, score in top:
                    print(f"    - {org_id} | {name} | score={score}")
            else:
                print("    - no candidates")

    if args.write:
        backup_path = backup_file(links_file)
        payload = {
            "links": final_links,
            "updated_at": time.time(),
        }
        links_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nBackup written to: {backup_path}")
        print(f"Updated links file: {links_file}")
    else:
        print("\nDry run only. Re-run with --write to persist changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
