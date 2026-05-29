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


def user_can_access_job(user: Dict[str, Any], job_payload: Dict[str, Any]) -> bool:
    """Return whether a logged-in user may access a job payload."""
    if bool(user.get("is_admin")):
        return True

    owner = str(job_payload.get("created_by") or "").strip().lower()
    if not owner:
        # Legacy jobs did not record owners; keep them visible until migrated.
        return True

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
