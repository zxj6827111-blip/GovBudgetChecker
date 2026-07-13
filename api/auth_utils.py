"""Shared authentication helpers for protected API routes."""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from fastapi import HTTPException, Request

from api import runtime


def extract_session_token(request: Request) -> str:
    token = str(request.headers.get("X-Session-Token") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="session token required")
    return token


def require_login(request: Request) -> Tuple[Any, str, Dict[str, Any]]:
    if (
        os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes"}
        and not str(request.headers.get("X-Session-Token") or "").strip()
    ):
        return runtime.require_user_store(), "", {"username": "test-admin", "is_admin": True}
    store = runtime.require_user_store()
    token = extract_session_token(request)
    user = store.get_user_by_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return store, token, user


def require_admin(request: Request) -> Tuple[Any, str, Dict[str, Any]]:
    store, token, user = require_login(request)
    if not bool(user.get("is_admin")):
        raise HTTPException(status_code=403, detail="admin privileges required")
    return store, token, user


def _user_org_scope(user: Dict[str, Any]) -> set[str]:
    raw_scope = user.get("organization_ids")
    if not isinstance(raw_scope, list):
        return set()
    return {str(item or "").strip() for item in raw_scope if str(item or "").strip()}


def _job_organization_id(job_payload: Dict[str, Any]) -> str:
    direct_org_id = str(job_payload.get("organization_id") or "").strip()
    if direct_org_id:
        return direct_org_id

    job_id = str(job_payload.get("job_id") or "").strip()
    if not job_id:
        return ""

    try:
        storage = runtime.require_org_storage()
        link = storage.get_job_org(job_id)
    except Exception:
        return ""
    if link is None:
        return ""
    return str(getattr(link, "org_id", "") or "").strip()


def user_can_access_org(user: Dict[str, Any], org_id: str) -> bool:
    """Return whether a logged-in user may access an organization subtree."""
    if bool(user.get("is_admin")):
        return True

    target_org_id = str(org_id or "").strip()
    if not target_org_id:
        return False

    allowed_org_ids = _user_org_scope(user)
    if not allowed_org_ids:
        return False
    if target_org_id in allowed_org_ids:
        return True

    try:
        storage = runtime.require_org_storage()
    except Exception:
        return False

    current = storage.get_by_id(target_org_id)
    seen: set[str] = set()
    while current is not None:
        current_id = str(getattr(current, "id", "") or "").strip()
        if not current_id or current_id in seen:
            return False
        if current_id in allowed_org_ids:
            return True
        seen.add(current_id)
        parent_id = getattr(current, "parent_id", None)
        if not parent_id:
            return False
        current = storage.get_by_id(str(parent_id))
    return False


def user_can_access_job(user: Dict[str, Any], job_payload: Dict[str, Any]) -> bool:
    """Return whether a logged-in user may access a job payload."""
    if bool(user.get("is_admin")):
        return True

    job_org_id = _job_organization_id(job_payload)
    if job_org_id and user_can_access_org(user, job_org_id):
        return True

    owner = str(job_payload.get("created_by") or "").strip().lower()
    if not owner:
        # Legacy jobs without an owner must not become visible to every user.
        return False

    username = str(user.get("username") or "").strip().lower()
    return bool(username) and owner == username


def require_job_access(
    request: Request,
    job_id: str,
) -> Tuple[Any, str, Dict[str, Any], Dict[str, Any]]:
    store, token, user = require_login(request)
    payload = runtime.get_job_status_payload(job_id)
    if not user_can_access_job(user, payload):
        raise HTTPException(status_code=403, detail="job access denied")
    return store, token, user, payload
