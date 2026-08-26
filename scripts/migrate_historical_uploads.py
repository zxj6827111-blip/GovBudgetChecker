#!/usr/bin/env python3
"""Import durable upload snapshots into PostgreSQL.

The command is deliberately dry-run by default. It reads the existing upload
volume and reports what can be restored; ``--apply`` is required before any
database write is attempted. Original PDFs are never copied or deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from dotenv import load_dotenv


# The script is normally executed as ``python scripts/...``. In that mode
# Python only adds ``scripts/`` to sys.path, so make the repository packages
# and the local development configuration available explicitly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
load_dotenv(_REPO_ROOT / ".env")


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import historical upload snapshots into PostgreSQL (dry-run by default)."
    )
    parser.add_argument("--uploads-dir", default=os.getenv("UPLOAD_DIR", "uploads"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write snapshots to PostgreSQL. Omit this flag to only inspect the upload volume.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Maximum job folders to inspect (0 = all).")
    return parser.parse_args(argv)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def iter_job_directories(uploads_dir: Path) -> Iterable[Path]:
    if not uploads_dir.is_dir():
        return []
    return (
        child
        for child in sorted(uploads_dir.iterdir())
        if child.is_dir() and not child.name.startswith(".")
    )


def build_snapshot(job_dir: Path) -> Dict[str, Any] | None:
    """Build a database-compatible snapshot from a historical job directory."""
    payload = _read_json(job_dir / "status.json")
    if not payload:
        return None

    job_id = str(payload.get("job_id") or job_dir.name).strip()
    if not job_id:
        return None
    payload["job_id"] = job_id

    structured_ingest = _read_json(job_dir / "structured_ingest.json")
    if structured_ingest and not isinstance(payload.get("structured_ingest"), dict):
        payload["structured_ingest"] = structured_ingest

    pdfs = sorted(job_dir.glob("*.pdf"))
    if pdfs:
        pdf = pdfs[0]
        payload.setdefault("filename", pdf.name)
        payload.setdefault("size", pdf.stat().st_size)
        existing_storage_key = str(payload.get("storage_key") or payload.get("saved_path") or "").replace("\\", "/")
        if not existing_storage_key or existing_storage_key.startswith("/") or ":/" in existing_storage_key:
            payload["storage_key"] = f"{job_dir.name}/{pdf.name}"
            payload["saved_path"] = payload["storage_key"]
        payload.setdefault("storage_backend", "filesystem")
        payload.setdefault("content_type", "application/pdf")
        correction = _derive_metadata_correction(payload, pdf)
        if correction:
            payload.update(correction["patch"])
            payload["historical_metadata_correction"] = correction["audit"]
            payload["metadata_reanalysis_required"] = True
    payload["_source_dir"] = str(job_dir)
    return payload


def _derive_metadata_correction(payload: Dict[str, Any], pdf_path: Path) -> Dict[str, Any] | None:
    """Infer historical metadata without overriding explicit human choices."""
    try:
        # Reuse the same cover and organization matcher used by live uploads so
        # migration does not have a second, inconsistent classifier.
        from api.routes.upload import _inspect_document_preflight

        detected = _inspect_document_preflight(filename=pdf_path.name, pdf_path=pdf_path)
    except Exception:
        return None

    patch: Dict[str, Any] = {}
    prior: Dict[str, Any] = {}
    confirmed = payload.get("metadata_confirmed")
    confirmed_fields = confirmed if isinstance(confirmed, dict) else {}

    def is_confirmed(field: str) -> bool:
        if bool(confirmed_fields.get(field)):
            return True
        source = str(payload.get(f"{field}_source") or "").strip().lower()
        return source in {"manual", "confirmed", "human"}

    year = detected.get("report_year")
    if year and not (is_confirmed("report_year") or is_confirmed("fiscal_year")):
        year_text = str(year)
        if str(payload.get("report_year") or "") != year_text or str(payload.get("fiscal_year") or "") != year_text:
            prior["report_year"] = payload.get("report_year")
            prior["fiscal_year"] = payload.get("fiscal_year")
            patch.update({"report_year": int(year), "fiscal_year": year_text, "report_year_source": "historical_cover_recheck"})

    doc_type = str(detected.get("doc_type") or "").strip()
    report_kind = str(detected.get("report_kind") or "").strip()
    if doc_type and not is_confirmed("doc_type") and str(payload.get("doc_type") or "") != doc_type:
        prior["doc_type"] = payload.get("doc_type")
        patch["doc_type"] = doc_type
    if report_kind and report_kind != "unknown" and not is_confirmed("report_kind") and str(payload.get("report_kind") or "") != report_kind:
        prior["report_kind"] = payload.get("report_kind")
        patch["report_kind"] = report_kind

    match = detected.get("current") if isinstance(detected.get("current"), dict) else {}
    if (
        match
        and str(payload.get("organization_match_type") or "").strip().lower() not in {"manual", "confirmed", "human"}
        and not is_confirmed("organization")
    ):
        detected_org_id = str(match.get("organization_id") or "").strip()
        detected_org_name = str(match.get("organization_name") or "").strip()
        if detected_org_id and (
            detected_org_id != str(payload.get("organization_id") or "")
            or detected_org_name != str(payload.get("organization_name") or "")
        ):
            prior["organization_id"] = payload.get("organization_id")
            prior["organization_name"] = payload.get("organization_name")
            patch.update(
                {
                    "organization_id": detected_org_id,
                    "organization_name": detected_org_name,
                    "organization_match_type": "historical_cover_recheck",
                    "organization_match_confidence": match.get("confidence"),
                }
            )

    if not patch:
        return None
    return {
        "patch": patch,
        "audit": {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prior": prior,
            "detected": {
                "report_year": detected.get("report_year"),
                "doc_type": detected.get("doc_type"),
                "report_kind": detected.get("report_kind"),
                "organization": match,
            },
            "reason": "historical_cover_recheck",
        },
    }


def inspect_uploads(uploads_dir: Path, limit: int = 0) -> Tuple[List[Dict[str, Any]], List[str]]:
    snapshots: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for job_dir in iter_job_directories(uploads_dir):
        if limit and len(snapshots) + len(skipped) >= limit:
            break
        payload = build_snapshot(job_dir)
        if payload is None:
            skipped.append(job_dir.name)
        else:
            snapshots.append(payload)
    return snapshots, skipped


async def apply_migration(uploads_dir: Path, snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Persist snapshots and the workflow recovery file after a DB preflight."""
    from src.services.analysis_result_store import ensure_analysis_persistence_ready, persist_analysis_job_snapshot
    from src.services.issue_workflow_store import persist_workflow_state

    if not (os.getenv("DATABASE_URL") or "").strip():
        raise RuntimeError("DATABASE_URL is required when --apply is used")
    if not await ensure_analysis_persistence_ready():
        raise RuntimeError("PostgreSQL is unavailable or migrations could not be applied")

    report: Dict[str, Any] = {
        "eligible_job_count": len(snapshots),
        "migrated_job_count": 0,
        "failed_job_ids": [],
        "corrected_job_ids": [],
        "reanalysis_required_job_ids": [],
        "workflow_mirrored": False,
        "workflow_failed": False,
    }
    for raw_payload in snapshots:
        payload = dict(raw_payload)
        source_dir = Path(str(payload.pop("_source_dir", "") or ""))
        has_result = isinstance(payload.get("result"), dict)
        if await persist_analysis_job_snapshot(payload, include_results=has_result):
            report["migrated_job_count"] += 1
            if payload.get("historical_metadata_correction") and source_dir.is_dir():
                status = _read_json(source_dir / "status.json")
                if status:
                    status.update({
                        key: value
                        for key, value in payload.items()
                        if key in {
                            "organization_id", "organization_name", "organization_match_type",
                            "organization_match_confidence", "fiscal_year", "report_year",
                            "report_year_source", "doc_type", "report_kind",
                            "historical_metadata_correction", "metadata_reanalysis_required",
                        }
                    })
                    _write_json(source_dir / "status.json", status)
                report["corrected_job_ids"].append(str(payload.get("job_id") or source_dir.name))
                report["reanalysis_required_job_ids"].append(str(payload.get("job_id") or source_dir.name))
        else:
            report["failed_job_ids"].append(str(payload.get("job_id") or source_dir.name))

    workflow_state = _read_json(uploads_dir / ".issue_workflow.json")
    if workflow_state:
        report["workflow_mirrored"] = await persist_workflow_state(workflow_state)
        report["workflow_failed"] = not report["workflow_mirrored"]
    return report


async def _main_async(args: argparse.Namespace) -> int:
    uploads_dir = Path(args.uploads_dir).resolve()
    if not uploads_dir.is_dir():
        print(f"uploads directory not found: {uploads_dir}", file=sys.stderr)
        return 2

    snapshots, skipped = inspect_uploads(uploads_dir, args.limit)
    workflow_exists = (uploads_dir / ".issue_workflow.json").is_file()
    mode = "apply" if args.apply else "dry-run"
    print(json.dumps({
        "mode": mode,
        "uploads_dir": str(uploads_dir),
        "eligible_job_count": len(snapshots),
        "skipped_without_status_count": len(skipped),
        "workflow_recovery_file": workflow_exists,
        "sample_job_ids": [item["job_id"] for item in snapshots[:10]],
        "metadata_correction_candidate_count": sum(
            1 for item in snapshots if item.get("historical_metadata_correction")
        ),
    }, ensure_ascii=False, indent=2))

    if not args.apply:
        return 0

    report = await apply_migration(uploads_dir, snapshots)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed_job_ids"] or report["workflow_failed"] else 0


def main(argv: List[str] | None = None) -> int:
    return asyncio.run(_main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
