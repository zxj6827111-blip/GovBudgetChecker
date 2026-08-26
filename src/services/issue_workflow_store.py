"""Server-side persistence for issue remediation workflow state."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import asyncio
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException

from api import runtime
from api.auth_utils import user_can_access_job
from src.db.connection import DatabaseConnection
from src.services.analysis_result_store import ensure_analysis_persistence_ready


_LOCK = threading.RLock()
_VALID_STATUSES = {"pending", "confirmed", "no_issue", "needs_review", "in_package"}
logger = logging.getLogger(__name__)
_PERSISTENCE_FILENAME = ".issue_workflow_persistence.json"
# The JSON file is the durable source of truth; the database mirror must not
# stall a reviewer action when PostgreSQL is slow or unreachable.  A short
# bounded wait keeps the workflow endpoint responsive, and the failed mirror
# is retried by sync_workflow_recovery_state on the next opportunity.
_WORKFLOW_DB_TIMEOUT_SECONDS = float(os.getenv("WORKFLOW_DB_TIMEOUT_SECONDS", "3"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _store_path() -> Path:
    # UPLOAD_DIR is a persisted backend volume in the supplied deployment topology.
    return runtime.UPLOAD_ROOT / ".issue_workflow.json"


def _persistence_path() -> Path:
    return runtime.UPLOAD_ROOT / _PERSISTENCE_FILENAME


def _record_persistence_state(status: str, error: str = "") -> None:
    """Leave a durable retry signal without changing reviewer-visible state."""
    try:
        runtime.write_json_file(
            _persistence_path(),
            {
                "status": status,
                "last_attempt_at": _now(),
                "error": error or None,
            },
        )
    except Exception:
        logger.exception("Failed to record workflow persistence state")


@contextmanager
def _state_lock():
    """Serialize read-modify-write operations across all backend workers."""
    with _LOCK:
        lock_path = _store_path().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _workflow_key(job_id: str, issue_id: str) -> str:
    return f"{job_id}::{issue_id}"


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen: Set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalize_state(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    issues: Dict[str, Dict[str, Any]] = {}
    raw_issues = raw.get("issues")
    if isinstance(raw_issues, dict):
        for item in raw_issues.values():
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("job_id") or "").strip()
            issue_id = str(item.get("issue_id") or "").strip()
            if not job_id or not issue_id:
                continue
            key = _workflow_key(job_id, issue_id)
            status = str(item.get("status") or "pending").strip()
            issues[key] = {
                "key": key,
                "job_id": job_id,
                "issue_id": issue_id,
                "status": status if status in _VALID_STATUSES else "pending",
                "title": str(item.get("title") or "").strip() or None,
                "severity": str(item.get("severity") or "").strip() or None,
                "page": item.get("page") if isinstance(item.get("page"), int) else None,
                "organization_id": str(item.get("organization_id") or "").strip() or None,
                "organization_name": str(item.get("organization_name") or "").strip() or None,
                "note": str(item.get("note") or "").strip() or None,
                "updated_at": str(item.get("updated_at") or "").strip() or _now(),
            }

    packages: List[Dict[str, Any]] = []
    raw_packages = raw.get("packages")
    if isinstance(raw_packages, list):
        for item in raw_packages:
            if not isinstance(item, dict):
                continue
            package_id = str(item.get("id") or "").strip()
            if not package_id:
                continue
            status = str(item.get("status") or "draft").strip()
            packages.append(
                {
                    "id": package_id,
                    "name": str(item.get("name") or "Unnamed remediation package").strip() or "Unnamed remediation package",
                    "organization_id": str(item.get("organization_id") or "").strip() or None,
                    "organization_name": str(item.get("organization_name") or "").strip() or None,
                    "job_ids": _strings(item.get("job_ids")),
                    "issue_keys": _strings(item.get("issue_keys")),
                    "status": status if status in {"draft", "ready", "submitted"} else "draft",
                    "created_at": str(item.get("created_at") or "").strip() or _now(),
                    "updated_at": str(item.get("updated_at") or "").strip() or _now(),
                }
            )
    try:
        revision = max(0, int(raw.get("revision") or 0))
    except (TypeError, ValueError):
        revision = 0
    return {
        "issues": issues,
        "packages": packages,
        "updated_at": raw.get("updated_at") or None,
        "revision": revision,
    }


def _read_state() -> Dict[str, Any]:
    return _normalize_state(runtime.read_json_file(_store_path(), default={}))


def _write_state(state: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "issues": state["issues"],
        "packages": state["packages"],
        "updated_at": _now(),
        "revision": int(state.get("revision") or 0) + 1,
    }
    runtime.write_json_file(_store_path(), payload)
    return payload


async def persist_workflow_state(state: Dict[str, Any]) -> bool:
    """Mirror the file-backed workflow state into PostgreSQL when configured.

    The JSON file remains the local recovery source. Database failures must not
    discard a reviewer action that has already been atomically written to it.
    """
    if not (os.getenv("DATABASE_URL") or "").strip():
        _record_persistence_state("disabled", "DATABASE_URL is not configured")
        return False
    if not await _database_ready_within_budget():
        _record_persistence_state("pending_retry", "database is unavailable")
        return False
    try:
        return await asyncio.wait_for(
            _mirror_workflow_state(state),
            timeout=_WORKFLOW_DB_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Workflow state mirror to PostgreSQL timed out after %.1fs; "
            "keeping local file state",
            _WORKFLOW_DB_TIMEOUT_SECONDS,
        )
        _record_persistence_state("pending_retry", "database mirror timed out")
        return False


async def _database_ready_within_budget() -> bool:
    """Check database readiness without blocking the workflow endpoint."""
    try:
        return await asyncio.wait_for(
            ensure_analysis_persistence_ready(),
            timeout=_WORKFLOW_DB_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Database readiness check for workflow mirror timed out")
        return False


async def _mirror_workflow_state(state: Dict[str, Any]) -> bool:
    normalized = _normalize_state(state)
    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        async with conn.transaction():
            # A file write is serialized across workers. Persist its monotonic
            # revision as well, so an older asynchronous mirror cannot replace
            # a newer reviewer action after it acquires the database connection.
            await conn.execute(
                """
                INSERT INTO workflow_state_mirror (mirror_id, revision, updated_at)
                VALUES (TRUE, 0, NOW())
                ON CONFLICT (mirror_id) DO NOTHING
                """
            )
            mirrored_revision = await conn.fetchval(
                "SELECT revision FROM workflow_state_mirror WHERE mirror_id = TRUE FOR UPDATE"
            )
            # Pre-revision recovery files were written before the DB mirror
            # existed and therefore carry revision 0.  Treat their first
            # mirror as revision 1 instead of incorrectly considering an
            # empty DB row at revision 0 as already synchronized.
            source_revision = int(normalized["revision"] or 0)
            target_revision = max(1, source_revision)
            if source_revision > 0 and int(mirrored_revision or 0) >= target_revision:
                # The mirror revision already covers this snapshot.
                if int(mirrored_revision or 0) > source_revision:
                    # A strictly newer mirror must never be replaced by this
                    # older snapshot, even when the row content differs (the
                    # snapshot was captured before a concurrent update landed).
                    return True
                # Same revision: a local workflow file that was recreated from
                # scratch restarts its revision counter at 1, which can collide
                # with an older DB mirror that also holds revision 1. Compare
                # the mirrored rows instead of trusting the revision alone, so
                # a newer local state is never silently dropped.
                mirrored_keys = {
                    str(row["workflow_key"])
                    for row in await conn.fetch(
                        "SELECT workflow_key FROM workflow_issue_records"
                    )
                }
                mirrored_package_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM workflow_remediation_packages"
                )
                if mirrored_keys == set(normalized["issues"].keys()) and (
                    int(mirrored_package_count or 0) == len(normalized["packages"])
                ):
                    return True
                logger.info(
                    "Workflow mirror revision %s equals local revision %s "
                    "but content differs; resyncing %d issues / %d packages",
                    mirrored_revision,
                    source_revision,
                    len(normalized["issues"]),
                    len(normalized["packages"]),
                )

            await conn.execute("DELETE FROM workflow_issue_records")
            for record in normalized["issues"].values():
                await conn.execute(
                    """
                    INSERT INTO workflow_issue_records (
                        workflow_key, job_uuid, issue_id, status, title, severity,
                        page_number, organization_id, organization_name, note, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text::timestamptz)
                    ON CONFLICT (workflow_key)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        title = EXCLUDED.title,
                        severity = EXCLUDED.severity,
                        page_number = EXCLUDED.page_number,
                        organization_id = EXCLUDED.organization_id,
                        organization_name = EXCLUDED.organization_name,
                        note = EXCLUDED.note,
                        updated_at = EXCLUDED.updated_at
                    """,
                    record["key"],
                    record["job_id"],
                    record["issue_id"],
                    record["status"],
                    record["title"],
                    record["severity"],
                    record["page"],
                    record["organization_id"],
                    record["organization_name"],
                    record["note"],
                    record["updated_at"],
                )

            await conn.execute("DELETE FROM workflow_remediation_packages")
            for package in normalized["packages"]:
                await conn.execute(
                    """
                    INSERT INTO workflow_remediation_packages (
                        package_id, name, organization_id, organization_name,
                        job_ids, issue_keys, status, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8::text::timestamptz, $9::text::timestamptz)
                    """,
                    package["id"],
                    package["name"],
                    package["organization_id"],
                    package["organization_name"],
                    json.dumps(package["job_ids"], ensure_ascii=False),
                    json.dumps(package["issue_keys"], ensure_ascii=False),
                    package["status"],
                    package["created_at"],
                    package["updated_at"],
                )
            await conn.execute(
                """
                UPDATE workflow_state_mirror
                SET revision = $1, updated_at = $2::text::timestamptz
                WHERE mirror_id = TRUE
                """,
                target_revision,
                normalized["updated_at"] or _now(),
            )
        _record_persistence_state("synced")
        return True
    except Exception as exc:
        logger.exception("Failed to mirror workflow state to PostgreSQL")
        _record_persistence_state("pending_retry", str(exc) or exc.__class__.__name__)
        return False
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


async def sync_workflow_recovery_state() -> bool:
    """Retry the latest recovery snapshot after a previous database outage."""
    if not _store_path().is_file():
        return False
    with _state_lock():
        state = _read_state()
        # Upgrade a pre-mirror file in place once.  Future restarts can then
        # use normal monotonic-revision semantics instead of repeatedly
        # replaying revision 0.
        if int(state.get("revision") or 0) <= 0:
            state = _write_state(state)
    return await persist_workflow_state(state)


def _find_issue(payload: Dict[str, Any], issue_id: str) -> Optional[Dict[str, Any]]:
    def _find(items: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == issue_id:
                return item
        return None

    containers: Iterable[Any] = (payload, payload.get("result"))
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("ai_findings", "rule_findings"):
            found = _find(container.get(key))
            if found:
                return found
        issues = container.get("issues")
        if isinstance(issues, list):
            found = _find(issues)
            if found:
                return found
        elif isinstance(issues, dict):
            for key in ("all", "error", "warn", "info"):
                found = _find(issues.get(key))
                if found:
                    return found
    return None


def _job_and_issue(user: Dict[str, Any], job_id: str, issue_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    payload = runtime.get_job_status_payload(job_id)
    if not user_can_access_job(user, payload):
        raise HTTPException(status_code=403, detail="job access denied")
    issue = _find_issue(payload, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue_id does not exist")
    return payload, issue


def _organization_context(payload: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    return (
        str(payload.get("organization_id") or "").strip() or None,
        str(payload.get("organization_name") or "").strip() or None,
    )


def _issue_page(issue: Dict[str, Any]) -> Optional[int]:
    page = issue.get("page")
    if isinstance(page, int):
        return page
    location = issue.get("location")
    if isinstance(location, dict) and isinstance(location.get("page"), int):
        return location["page"]
    return None


def get_visible_state(user: Dict[str, Any]) -> Dict[str, Any]:
    with _state_lock():
        state = _read_state()
        visible_issues: Dict[str, Dict[str, Any]] = {}
        visible_job_ids: Set[str] = set()
        for key, record in state["issues"].items():
            try:
                payload = runtime.get_job_status_payload(record["job_id"])
            except HTTPException:
                continue
            if user_can_access_job(user, payload):
                visible_issues[key] = copy.deepcopy(record)
                visible_job_ids.add(record["job_id"])
        packages = [
            copy.deepcopy(package)
            for package in state["packages"]
            if package["job_ids"]
            and all(job_id in visible_job_ids or _can_read_job(user, job_id) for job_id in package["job_ids"])
        ]
        return {"issues": visible_issues, "packages": packages, "updated_at": state.get("updated_at")}


def _can_read_job(user: Dict[str, Any], job_id: str) -> bool:
    try:
        return user_can_access_job(user, runtime.get_job_status_payload(job_id))
    except HTTPException:
        return False


async def update_issue(
    user: Dict[str, Any],
    *,
    job_id: str,
    issue_id: str,
    status: str,
    note: Optional[str],
) -> Dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    normalized_issue_id = str(issue_id or "").strip()
    normalized_status = str(status or "").strip()
    if not normalized_job_id or not normalized_issue_id:
        raise HTTPException(status_code=400, detail="job_id and issue_id are required")
    if normalized_status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail="invalid workflow status")

    payload, issue = _job_and_issue(user, normalized_job_id, normalized_issue_id)
    org_id, org_name = _organization_context(payload)
    with _state_lock():
        state = _read_state()
        key = _workflow_key(normalized_job_id, normalized_issue_id)
        state["issues"][key] = {
            "key": key,
            "job_id": normalized_job_id,
            "issue_id": normalized_issue_id,
            "status": normalized_status,
            "title": str(issue.get("title") or issue.get("message") or "").strip() or None,
            "severity": str(issue.get("severity") or "").strip() or None,
            "page": _issue_page(issue),
            "organization_id": org_id,
            "organization_name": org_name,
            "note": str(note or "").strip() or None,
            "updated_at": _now(),
        }
        next_state = _write_state(state)
    await persist_workflow_state(next_state)
    return next_state


async def create_package(
    user: Dict[str, Any],
    *,
    name: Optional[str],
    job_ids: Any,
    issue_keys: Any,
) -> Dict[str, Any]:
    normalized_job_ids = _strings(job_ids)
    normalized_issue_keys = _strings(issue_keys)
    if not normalized_job_ids or not normalized_issue_keys:
        raise HTTPException(status_code=400, detail="job_ids and issue_keys are required")

    job_payloads: Dict[str, Dict[str, Any]] = {}
    for job_id in normalized_job_ids:
        payload = runtime.get_job_status_payload(job_id)
        if not user_can_access_job(user, payload):
            raise HTTPException(status_code=403, detail="job access denied")
        job_payloads[job_id] = payload

    resolved_issues: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for key in normalized_issue_keys:
        job_id, separator, issue_id = key.partition("::")
        if not separator or not job_id or not issue_id or job_id not in job_payloads:
            raise HTTPException(status_code=400, detail="issue_keys must belong to requested job_ids")
        issue = _find_issue(job_payloads[job_id], issue_id)
        if issue is None:
            raise HTTPException(status_code=404, detail=f"issue_id does not exist: {issue_id}")
        resolved_issues.append((key, job_payloads[job_id], issue))

    org_contexts = {_organization_context(payload) for payload in job_payloads.values()}
    organization_id, organization_name = next(iter(org_contexts)) if len(org_contexts) == 1 else (None, None)
    now = _now()
    package_id = f"pkg-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    with _state_lock():
        state = _read_state()
        for key, payload, issue in resolved_issues:
            job_id, _, issue_id = key.partition("::")
            org_id, org_name = _organization_context(payload)
            state["issues"][key] = {
                "key": key,
                "job_id": job_id,
                "issue_id": issue_id,
                "status": "in_package",
                "title": str(issue.get("title") or issue.get("message") or "").strip() or None,
                "severity": str(issue.get("severity") or "").strip() or None,
                "page": _issue_page(issue),
                "organization_id": org_id,
                "organization_name": org_name,
                "note": state["issues"].get(key, {}).get("note"),
                "updated_at": now,
            }
        record = {
            "id": package_id,
            "name": str(name or "").strip() or "Remediation package",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "job_ids": normalized_job_ids,
            "issue_keys": normalized_issue_keys,
            "status": "ready",
            "created_at": now,
            "updated_at": now,
        }
        state["packages"] = [record, *state["packages"]]
        next_state = _write_state(state)
    await persist_workflow_state(next_state)
    return {"state": next_state, "package": record}
