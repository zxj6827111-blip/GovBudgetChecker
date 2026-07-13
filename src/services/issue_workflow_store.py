"""Server-side persistence for issue remediation workflow state."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException

from api import runtime
from api.auth_utils import user_can_access_job


_LOCK = threading.RLock()
_VALID_STATUSES = {"pending", "confirmed", "no_issue", "needs_review", "in_package"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _store_path() -> Path:
    # UPLOAD_DIR is a persisted backend volume in the supplied deployment topology.
    return runtime.UPLOAD_ROOT / ".issue_workflow.json"


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
    return {"issues": issues, "packages": packages, "updated_at": raw.get("updated_at") or None}


def _read_state() -> Dict[str, Any]:
    return _normalize_state(runtime.read_json_file(_store_path(), default={}))


def _write_state(state: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "issues": state["issues"],
        "packages": state["packages"],
        "updated_at": _now(),
    }
    runtime.write_json_file(_store_path(), payload)
    return payload


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


def update_issue(
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
        return _write_state(state)


def create_package(
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
    return {"state": next_state, "package": record}
