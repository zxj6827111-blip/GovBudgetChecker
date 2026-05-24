#!/usr/bin/env python3
"""Compare uploaded report folders with the reports visible to the UI.

This script focuses on the two places that matter for the "missing reports"
question:

1. The upload directory on disk (`uploads/` by default).
2. The organization-linked visibility path that department/unit pages use.

Optionally, the script can also query a running Next.js frontend and/or FastAPI
backend so you can compare the live responses with the filesystem snapshot.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class UploadJob:
    job_id: str
    filename: str
    path: str
    has_pdf: bool
    has_status: bool


@dataclass(frozen=True)
class OrganizationRecord:
    id: str
    name: str
    level: str
    parent_id: Optional[str]


@dataclass(frozen=True)
class JobLinkRecord:
    job_id: str
    org_id: str
    match_type: Optional[str]
    confidence: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare uploaded report folders with organization-linked visibility "
            "and optional live API responses."
        )
    )
    parser.add_argument(
        "--uploads-dir",
        default="uploads",
        help="Directory containing uploaded report job folders. Default: uploads",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing organizations.json and job_org_links.json. Default: data",
    )
    parser.add_argument(
        "--next-base",
        default="",
        help=(
            "Optional Next.js base URL, for example http://127.0.0.1:3000 . "
            "The script will query /api/jobs and /api/organizations/{id}/jobs."
        ),
    )
    parser.add_argument(
        "--backend-base",
        default="",
        help=(
            "Optional backend base URL, for example http://127.0.0.1:8000 . "
            "The script will query /api/jobs and /api/organizations/{id}/jobs."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP timeout in seconds for live endpoint checks. Default: 8",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum sample rows per mismatch bucket. Default: 20",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional file path to save the full comparison report as JSON.",
    )
    return parser.parse_args()


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback


def normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def iter_upload_jobs(uploads_dir: Path) -> Dict[str, UploadJob]:
    jobs: Dict[str, UploadJob] = {}
    if not uploads_dir.exists():
        return jobs

    for child in sorted(uploads_dir.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue

        pdfs = sorted(child.glob("*.pdf"))
        has_pdf = bool(pdfs)
        has_status = (child / "status.json").exists()

        filename = pdfs[0].name if pdfs else ""
        if not filename:
            status_payload = load_json(child / "status.json", {})
            filename = str(status_payload.get("filename") or "").strip()

        jobs[child.name] = UploadJob(
            job_id=child.name,
            filename=filename,
            path=str(child),
            has_pdf=has_pdf,
            has_status=has_status,
        )
    return jobs


def load_organizations(data_dir: Path) -> List[OrganizationRecord]:
    payload = load_json(data_dir / "organizations.json", {})
    raw_orgs = payload.get("organizations", [])
    organizations: List[OrganizationRecord] = []
    for item in raw_orgs:
        if not isinstance(item, dict):
            continue
        org_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not org_id:
            continue
        organizations.append(
            OrganizationRecord(
                id=org_id,
                name=name,
                level=str(item.get("level") or "").strip(),
                parent_id=str(item.get("parent_id")).strip() if item.get("parent_id") else None,
            )
        )
    return organizations


def load_links(data_dir: Path) -> List[JobLinkRecord]:
    payload = load_json(data_dir / "job_org_links.json", {})
    raw_links = payload.get("links", [])
    links: List[JobLinkRecord] = []
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or "").strip()
        org_id = str(item.get("org_id") or "").strip()
        if not job_id or not org_id:
            continue
        confidence: Optional[float] = None
        try:
            raw_confidence = item.get("confidence")
            confidence = float(raw_confidence) if raw_confidence is not None else None
        except Exception:
            confidence = None
        links.append(
            JobLinkRecord(
                job_id=job_id,
                org_id=org_id,
                match_type=str(item.get("match_type")).strip() if item.get("match_type") else None,
                confidence=confidence,
            )
        )
    return links


def build_link_index(links: Sequence[JobLinkRecord]) -> Dict[str, JobLinkRecord]:
    link_by_job: Dict[str, JobLinkRecord] = {}
    for link in links:
        link_by_job[link.job_id] = link
    return link_by_job


def summarize_local_visibility(
    uploads: Dict[str, UploadJob],
    organizations: Sequence[OrganizationRecord],
    links: Sequence[JobLinkRecord],
) -> Dict[str, Any]:
    org_by_id = {org.id: org for org in organizations}
    link_by_job = build_link_index(links)

    visible_job_ids = set()
    uploads_without_links: List[Dict[str, Any]] = []
    uploads_linked_to_missing_orgs: List[Dict[str, Any]] = []
    stale_links_to_missing_uploads: List[Dict[str, Any]] = []
    valid_linked_jobs: List[Dict[str, Any]] = []

    for job_id, upload in uploads.items():
        link = link_by_job.get(job_id)
        if link is None:
            uploads_without_links.append(
                {
                    "job_id": job_id,
                    "filename": upload.filename,
                    "path": upload.path,
                    "reason": "missing_link",
                }
            )
            continue

        org = org_by_id.get(link.org_id)
        if org is None:
            uploads_linked_to_missing_orgs.append(
                {
                    "job_id": job_id,
                    "filename": upload.filename,
                    "path": upload.path,
                    "org_id": link.org_id,
                    "match_type": link.match_type,
                    "confidence": link.confidence,
                    "reason": "linked_org_missing",
                }
            )
            continue

        visible_job_ids.add(job_id)
        valid_linked_jobs.append(
            {
                "job_id": job_id,
                "filename": upload.filename,
                "path": upload.path,
                "org_id": org.id,
                "org_name": org.name,
                "org_level": org.level,
                "match_type": link.match_type,
                "confidence": link.confidence,
            }
        )

    for link in links:
        if link.job_id in uploads:
            continue
        org = org_by_id.get(link.org_id)
        stale_links_to_missing_uploads.append(
            {
                "job_id": link.job_id,
                "org_id": link.org_id,
                "org_name": org.name if org else None,
                "org_level": org.level if org else None,
                "match_type": link.match_type,
                "confidence": link.confidence,
                "reason": "upload_missing",
            }
        )

    return {
        "upload_job_ids": sorted(uploads),
        "visible_job_ids": sorted(visible_job_ids),
        "valid_linked_jobs": valid_linked_jobs,
        "upload_dirs_missing_pdf_and_status": [
            {
                "job_id": upload.job_id,
                "filename": upload.filename,
                "path": upload.path,
                "reason": "missing_pdf_and_status",
            }
            for upload in uploads.values()
            if not upload.has_pdf and not upload.has_status
        ],
        "uploads_without_links": uploads_without_links,
        "uploads_linked_to_missing_orgs": uploads_linked_to_missing_orgs,
        "stale_links_to_missing_uploads": stale_links_to_missing_uploads,
    }


def http_get_json(url: str, timeout: float) -> Tuple[Optional[Any], Optional[str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "GovBudgetChecker compare_uploaded_vs_visible_reports.py",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw), None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return None, str(exc)


def extract_job_ids_from_payload(payload: Any) -> List[str]:
    if isinstance(payload, list):
        return extract_job_ids_from_items(payload)
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("jobs"), list):
        return extract_job_ids_from_items(payload["jobs"])
    if isinstance(payload.get("items"), list):
        return extract_job_ids_from_items(payload["items"])
    return []


def extract_job_ids_from_items(items: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or "").strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        result.append(job_id)
    return result


def fetch_live_visibility(base_url: str, timeout: float) -> Dict[str, Any]:
    normalized = normalize_base_url(base_url)
    if not normalized:
        return {"enabled": False}

    jobs_payload, jobs_error = http_get_json(f"{normalized}/api/jobs", timeout)
    orgs_payload, orgs_error = http_get_json(f"{normalized}/api/organizations/list", timeout)

    result: Dict[str, Any] = {
        "enabled": True,
        "base_url": normalized,
        "global_job_ids": [],
        "org_visible_job_ids": [],
        "organization_total": 0,
        "errors": [],
        "org_errors": [],
    }

    if jobs_error:
        result["errors"].append({"endpoint": "/api/jobs", "detail": jobs_error})
    else:
        result["global_job_ids"] = extract_job_ids_from_payload(jobs_payload)

    organizations: List[Dict[str, Any]] = []
    if orgs_error:
        result["errors"].append(
            {"endpoint": "/api/organizations/list", "detail": orgs_error}
        )
    elif isinstance(orgs_payload, dict) and isinstance(orgs_payload.get("organizations"), list):
        organizations = [
            item for item in orgs_payload["organizations"] if isinstance(item, dict)
        ]
    result["organization_total"] = len(organizations)

    visible_ids = set()
    for org in organizations:
        org_id = str(org.get("id") or "").strip()
        if not org_id:
            continue
        encoded_org_id = urllib.parse.quote(org_id, safe="")
        url = (
            f"{normalized}/api/organizations/{encoded_org_id}/jobs"
            "?include_children=true"
        )
        payload, error = http_get_json(url, timeout)
        if error:
            result["org_errors"].append({"org_id": org_id, "detail": error})
            continue
        for job_id in extract_job_ids_from_payload(payload):
            visible_ids.add(job_id)

    result["org_visible_job_ids"] = sorted(visible_ids)
    return result


def bucket_from_ids(
    ids: Iterable[str],
    uploads: Dict[str, UploadJob],
    local_summary: Dict[str, Any],
) -> List[Dict[str, Any]]:
    link_by_job = {
        item["job_id"]: item
        for item in local_summary.get("valid_linked_jobs", [])
        if isinstance(item, dict) and item.get("job_id")
    }
    missing_link = {
        item["job_id"]: item
        for item in local_summary.get("uploads_without_links", [])
        if isinstance(item, dict) and item.get("job_id")
    }
    missing_org = {
        item["job_id"]: item
        for item in local_summary.get("uploads_linked_to_missing_orgs", [])
        if isinstance(item, dict) and item.get("job_id")
    }

    rows: List[Dict[str, Any]] = []
    for job_id in sorted(set(ids)):
        upload = uploads.get(job_id)
        row: Dict[str, Any] = {"job_id": job_id}
        if upload is not None:
            row.update(
                {
                    "filename": upload.filename,
                    "path": upload.path,
                    "has_pdf": upload.has_pdf,
                    "has_status": upload.has_status,
                }
            )
        if job_id in link_by_job:
            row.update(
                {
                    "org_id": link_by_job[job_id].get("org_id"),
                    "org_name": link_by_job[job_id].get("org_name"),
                    "reason": "visible_via_valid_link",
                }
            )
        elif job_id in missing_link:
            row.update({"reason": "missing_link"})
        elif job_id in missing_org:
            row.update(
                {
                    "org_id": missing_org[job_id].get("org_id"),
                    "reason": "linked_org_missing",
                }
            )
        rows.append(row)
    return rows


def print_heading(title: str) -> None:
    print(f"\n== {title} ==")


def print_summary_line(label: str, value: Any) -> None:
    print(f"{label:<34} {value}")


def print_rows(title: str, rows: Sequence[Dict[str, Any]], limit: int) -> None:
    print_heading(title)
    print_summary_line("count", len(rows))
    for row in rows[:limit]:
        job_id = str(row.get("job_id") or "")
        filename = str(row.get("filename") or "")
        reason = str(row.get("reason") or "")
        org_name = str(row.get("org_name") or "")
        org_id = str(row.get("org_id") or "")
        endpoint = str(row.get("endpoint") or "")
        detail = str(row.get("detail") or "")
        parts: List[str] = []
        if job_id:
            parts.append(job_id)
        elif endpoint:
            parts.append(endpoint)
        else:
            parts.append("<item>")
        if filename:
            parts.append(filename)
        if org_name or org_id:
            parts.append(f"org={org_name or org_id}")
        if reason:
            parts.append(f"reason={reason}")
        if detail:
            parts.append(detail)
        print(f"  - {' | '.join(parts)}")
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more")


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    uploads_dir = Path(args.uploads_dir).resolve()
    data_dir = Path(args.data_dir).resolve()

    uploads = iter_upload_jobs(uploads_dir)
    organizations = load_organizations(data_dir)
    links = load_links(data_dir)
    local_summary = summarize_local_visibility(uploads, organizations, links)

    upload_job_ids = set(local_summary["upload_job_ids"])
    local_visible_job_ids = set(local_summary["visible_job_ids"])

    next_summary = fetch_live_visibility(args.next_base, args.timeout)
    backend_summary = fetch_live_visibility(args.backend_base, args.timeout)

    report: Dict[str, Any] = {
        "generated_at": time.time(),
        "paths": {
            "uploads_dir": str(uploads_dir),
            "data_dir": str(data_dir),
        },
        "uploads": {
            "count": len(uploads),
            "jobs": [asdict(job) for job in uploads.values()],
        },
        "organizations": {
            "count": len(organizations),
        },
        "links": {
            "count": len(links),
        },
        "local_visibility": local_summary,
        "diffs": {
            "disk_but_not_org_visible": bucket_from_ids(
                upload_job_ids - local_visible_job_ids,
                uploads,
                local_summary,
            ),
            "org_visible_but_not_on_disk": bucket_from_ids(
                local_visible_job_ids - upload_job_ids,
                uploads,
                local_summary,
            ),
        },
        "live": {
            "next": next_summary,
            "backend": backend_summary,
        },
    }

    for label, summary in (("next", next_summary), ("backend", backend_summary)):
        if not summary.get("enabled"):
            continue
        global_ids = set(summary.get("global_job_ids") or [])
        org_ids = set(summary.get("org_visible_job_ids") or [])
        report["diffs"][f"disk_but_not_{label}_global"] = bucket_from_ids(
            upload_job_ids - global_ids,
            uploads,
            local_summary,
        )
        report["diffs"][f"{label}_global_but_not_on_disk"] = bucket_from_ids(
            global_ids - upload_job_ids,
            uploads,
            local_summary,
        )
        report["diffs"][f"disk_but_not_{label}_org_visible"] = bucket_from_ids(
            upload_job_ids - org_ids,
            uploads,
            local_summary,
        )
        report["diffs"][f"{label}_org_visible_but_not_on_disk"] = bucket_from_ids(
            org_ids - upload_job_ids,
            uploads,
            local_summary,
        )

    return report


def print_report(report: Dict[str, Any], limit: int) -> None:
    print_heading("Paths")
    print_summary_line("uploads_dir", report["paths"]["uploads_dir"])
    print_summary_line("data_dir", report["paths"]["data_dir"])

    print_heading("Local Snapshot")
    print_summary_line("upload jobs on disk", report["uploads"]["count"])
    print_summary_line("organizations", report["organizations"]["count"])
    print_summary_line("job_org_links", report["links"]["count"])
    print_summary_line(
        "valid org-visible uploads",
        len(report["local_visibility"]["visible_job_ids"]),
    )
    print_summary_line(
        "upload dirs missing pdf/status",
        len(report["local_visibility"]["upload_dirs_missing_pdf_and_status"]),
    )
    print_summary_line(
        "uploads without any link",
        len(report["local_visibility"]["uploads_without_links"]),
    )
    print_summary_line(
        "uploads linked to missing org",
        len(report["local_visibility"]["uploads_linked_to_missing_orgs"]),
    )
    print_summary_line(
        "stale links to missing uploads",
        len(report["local_visibility"]["stale_links_to_missing_uploads"]),
    )

    print_rows(
        "Upload Directories Missing PDF And Status",
        report["local_visibility"]["upload_dirs_missing_pdf_and_status"],
        limit,
    )
    print_rows(
        "Disk But Not Organization Visible",
        report["diffs"]["disk_but_not_org_visible"],
        limit,
    )
    print_rows(
        "Organization Visible But Not On Disk",
        report["diffs"]["org_visible_but_not_on_disk"],
        limit,
    )
    print_rows(
        "Uploads Without Any Link",
        report["local_visibility"]["uploads_without_links"],
        limit,
    )
    print_rows(
        "Uploads Linked To Missing Organization",
        report["local_visibility"]["uploads_linked_to_missing_orgs"],
        limit,
    )
    print_rows(
        "Stale Links To Missing Uploads",
        report["local_visibility"]["stale_links_to_missing_uploads"],
        limit,
    )

    live = report.get("live", {})
    for label in ("next", "backend"):
        summary = live.get(label, {})
        if not summary.get("enabled"):
            continue
        print_heading(f"Live Check: {label}")
        print_summary_line("base_url", summary.get("base_url"))
        print_summary_line("global visible jobs", len(summary.get("global_job_ids", [])))
        print_summary_line(
            "org visible union",
            len(summary.get("org_visible_job_ids", [])),
        )
        print_summary_line("organizations queried", summary.get("organization_total", 0))
        print_summary_line("endpoint errors", len(summary.get("errors", [])))
        print_summary_line("org request errors", len(summary.get("org_errors", [])))
        print_rows(
            f"Disk But Not {label.title()} Global",
            report["diffs"].get(f"disk_but_not_{label}_global", []),
            limit,
        )
        print_rows(
            f"{label.title()} Global But Not On Disk",
            report["diffs"].get(f"{label}_global_but_not_on_disk", []),
            limit,
        )
        print_rows(
            f"Disk But Not {label.title()} Org Visible",
            report["diffs"].get(f"disk_but_not_{label}_org_visible", []),
            limit,
        )
        print_rows(
            f"{label.title()} Org Visible But Not On Disk",
            report["diffs"].get(f"{label}_org_visible_but_not_on_disk", []),
            limit,
        )
        print_rows(
            f"{label.title()} Endpoint Errors",
            summary.get("errors", []),
            limit,
        )
        print_rows(
            f"{label.title()} Organization Request Errors",
            summary.get("org_errors", []),
            limit,
        )


def maybe_write_json(report: Dict[str, Any], path_value: str) -> None:
    path_text = str(path_value or "").strip()
    if not path_text:
        return
    output_path = Path(path_text).resolve()
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nJSON report written to: {output_path}")


def main() -> int:
    args = parse_args()
    report = build_report(args)
    print_report(report, limit=max(1, int(args.limit)))
    maybe_write_json(report, args.json_out)

    local_missing = len(report["diffs"]["disk_but_not_org_visible"])
    stale_links = len(report["local_visibility"]["stale_links_to_missing_uploads"])
    if local_missing or stale_links:
        print(
            "\nLikely issue: some uploads still exist on disk but are not linked to a valid "
            "organization, or link data still points to reports that no longer exist."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
