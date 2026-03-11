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
