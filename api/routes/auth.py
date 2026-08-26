"""Username login and admin user management endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from api import runtime
from api.auth_utils import enforce_password_change

router = APIRouter()


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HTTPException(status_code=400, detail=f"{field_name} must be boolean")


def _coerce_organization_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="organization_ids must be a list")

    result: list[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _validate_organization_ids_exist(organization_ids: list[str]) -> None:
    if not organization_ids:
        return

    storage = runtime.require_org_storage()
    missing = [org_id for org_id in organization_ids if storage.get_by_id(org_id) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"unknown organization_ids: {', '.join(missing)}",
        )


def _coerce_password(value: Any, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise HTTPException(status_code=400, detail="password is required")
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="password must be string")
    if not value:
        raise HTTPException(status_code=400, detail="password is required")
    return value


async def _read_json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid request body")
    return body


def _extract_session_token(request: Request) -> str:
    token = str(request.headers.get("X-Session-Token") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="session token required")
    return token


def _require_login(request: Request) -> Tuple[Any, str, Dict[str, Any]]:
    store = runtime.require_user_store()
    token = _extract_session_token(request)
    user = store.get_user_by_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    # 首登强制改密同样覆盖用户管理类端点（/api/users 等），
    # 否则未改密的默认管理员仍能创建账号（缺口 B-07）。
    # 改密相关路径在 PASSWORD_CHANGE_EXEMPT_PATHS 里被放行。
    enforce_password_change(request, user)
    return store, token, user


def _require_admin(request: Request) -> Tuple[Any, str, Dict[str, Any]]:
    store, token, user = _require_login(request)
    if not bool(user.get("is_admin")):
        raise HTTPException(status_code=403, detail="admin privileges required")
    return store, token, user


@router.post("/api/auth/login")
async def auth_login(request: Request):
    body = await _read_json_body(request)
    username = str(body.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    password = _coerce_password(body.get("password"), required=True)
    if password is None:
        raise HTTPException(status_code=400, detail="password is required")

    store = runtime.require_user_store()
    try:
        token, user = store.login(username, password)
    except PermissionError as e:
        msg = str(e)
        if msg == "user is disabled":
            raise HTTPException(status_code=403, detail="user is disabled")
        if "locked" in msg:
            raise HTTPException(status_code=429, detail=msg)
        raise HTTPException(status_code=401, detail="invalid username or password")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"token": token, "user": user}


@router.get("/api/auth/me")
async def auth_me(request: Request):
    _, _, user = _require_login(request)
    return {"user": user}


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    store, token, _ = _require_login(request)
    store.revoke_session(token)
    return {"success": True}


@router.post("/api/auth/change-password")
async def auth_change_password(request: Request):
    store, token, user = _require_login(request)
    body = await _read_json_body(request)
    old_password = _coerce_password(body.get("old_password"), required=True)
    new_password = _coerce_password(body.get("new_password"), required=True)
    if old_password is None or new_password is None:
        raise HTTPException(status_code=400, detail="password is required")

    try:
        store.change_password(
            username=str(user["username"]),
            old_password=old_password,
            new_password=new_password,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="user not found")
    except PermissionError as e:
        if str(e) == "user is disabled":
            raise HTTPException(status_code=403, detail="user is disabled")
        raise HTTPException(status_code=400, detail="old password is incorrect")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    store.revoke_session(token)
    return {"success": True, "message": "password updated, please login again"}


@router.get("/api/users")
async def list_users(request: Request):
    store, _, _ = _require_admin(request)
    return {"users": store.list_users()}


@router.post("/api/users")
async def create_user(request: Request):
    store, _, _ = _require_admin(request)
    body = await _read_json_body(request)
    username = str(body.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    password = _coerce_password(body.get("password"), required=True)
    if password is None:
        raise HTTPException(status_code=400, detail="password is required")

    raw_is_admin = body.get("is_admin", False)
    is_admin = _coerce_bool(raw_is_admin, "is_admin") if "is_admin" in body else False
    organization_ids = _coerce_organization_ids(body.get("organization_ids"))
    _validate_organization_ids_exist(organization_ids)

    try:
        user = store.add_user(
            username=username,
            password=password,
            is_admin=is_admin,
            organization_ids=organization_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return user


@router.patch("/api/users/{username}")
async def update_user(username: str, request: Request):
    store, _, _ = _require_admin(request)
    body = await _read_json_body(request)

    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    organization_ids: Optional[list[str]] = None
    if "is_admin" in body:
        is_admin = _coerce_bool(body.get("is_admin"), "is_admin")
    if "is_active" in body:
        is_active = _coerce_bool(body.get("is_active"), "is_active")
    if "password" in body:
        password = _coerce_password(body.get("password"), required=True)
    if "organization_ids" in body:
        organization_ids = _coerce_organization_ids(body.get("organization_ids"))
        _validate_organization_ids_exist(organization_ids)

    if (
        is_admin is None
        and is_active is None
        and password is None
        and organization_ids is None
    ):
        raise HTTPException(status_code=400, detail="no update fields provided")

    try:
        user = store.update_user(
            username=username,
            is_admin=is_admin,
            is_active=is_active,
            password=password,
            organization_ids=organization_ids,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="user not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return user


@router.delete("/api/users/{username}")
async def delete_user(username: str, request: Request):
    store, _, current_user = _require_admin(request)

    try:
        store.delete_user(username=username, actor_username=str(current_user["username"]))
    except KeyError:
        raise HTTPException(status_code=404, detail="user not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True}
