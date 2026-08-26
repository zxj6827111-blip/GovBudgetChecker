"""Task 12 / 缺口 B-07（首登强制改密）+ B-08（安全响应头）。

整改前的事实：
- `src/services/user_store.py` 没有 `must_change_password` 字段，
  默认管理员用部署配置里的口令可以一直用下去；
- 全仓没有任何 HSTS / CSP / X-Content-Type-Options / X-Frame-Options。

断言意图（每组都有正反对照）：
1. 首登改密：开关打开时默认管理员被置位 `must_change_password`；
   未改密访问业务接口被 403 拦；改密后放行（反例 + 正例成对出现）。
2. 放行清单：改密期间 `/api/auth/me`、`/api/auth/change-password`、`/api/auth/logout` 必须可用，
   否则用户没有任何途径完成改密。
3. 向后兼容：历史 users.json 没有该字段时按 False，旧账号不被锁。
4. 安全响应头：四个头都存在且值正确；免认证端点（/health、/ready）仍可访问且同样带头；
   HSTS 只在 https 下发（http 下**不得**出现）；`/docs` 用宽松 CSP。
5. 不破坏既有鉴权/限流：未带 API Key 仍是 401，且 401 响应也带安全头。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.auth_utils import PASSWORD_CHANGE_EXEMPT_PATHS, enforce_password_change
from src.security import SecurityHeadersMiddleware
from src.services.user_store import (
    UserStore,
    require_first_login_password_change,
    reset_user_store,
)

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
SEEDED_PASSWORD = "Seeded-Admin-Pass-2026"
NEW_PASSWORD = "Rotated-Admin-Pass-2026"


# ---------------------------------------------------------------------------
# 1. 策略开关
# ---------------------------------------------------------------------------
def test_policy_defaults_on_outside_testing(monkeypatch):
    """生产（非 TESTING）默认开启；显式配置优先。"""
    monkeypatch.delenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", raising=False)
    monkeypatch.setenv("TESTING", "false")
    assert require_first_login_password_change() is True

    monkeypatch.setenv("TESTING", "true")
    assert require_first_login_password_change() is False

    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    assert require_first_login_password_change() is True

    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "false")
    assert require_first_login_password_change() is False


# ---------------------------------------------------------------------------
# 2. user_store 层：置位、清除、向后兼容
# ---------------------------------------------------------------------------
def _store(tmp_path: Path) -> UserStore:
    return UserStore(users_file=tmp_path / "users.json")


def test_seeded_admin_requires_password_change(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED_PASSWORD)

    store = _store(tmp_path)
    _token, user = store.login("admin", SEEDED_PASSWORD)

    assert user["must_change_password"] is True
    # 落盘也要带上该字段，重启后不能丢
    persisted = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    admin_row = next(row for row in persisted["users"] if row["username"] == "admin")
    assert admin_row["must_change_password"] is True


def test_seeded_admin_not_flagged_when_policy_disabled(tmp_path, monkeypatch):
    """对照：策略关闭时不置位，行为与整改前一致。"""
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "false")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED_PASSWORD)

    store = _store(tmp_path)
    _token, user = store.login("admin", SEEDED_PASSWORD)

    assert user["must_change_password"] is False


def test_change_password_clears_the_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED_PASSWORD)
    store = _store(tmp_path)

    store.change_password("admin", SEEDED_PASSWORD, NEW_PASSWORD)

    _token, user = store.login("admin", NEW_PASSWORD)
    assert user["must_change_password"] is False


def test_legacy_users_file_without_field_is_not_flagged(tmp_path, monkeypatch):
    """向后兼容：历史记录没有该字段（且口令哈希有效）时不得被锁。"""
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED_PASSWORD)

    # 先用策略关闭的方式造一份"历史"文件，再手工删掉新字段
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "false")
    seeded = _store(tmp_path)
    seeded.change_password("admin", SEEDED_PASSWORD, NEW_PASSWORD)
    users_file = tmp_path / "users.json"
    payload = json.loads(users_file.read_text(encoding="utf-8"))
    for row in payload["users"]:
        row.pop("must_change_password", None)
    users_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # 现在打开策略重新加载：已有账号不该被要求改密
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    reloaded = _store(tmp_path)
    _token, user = reloaded.login("admin", NEW_PASSWORD)
    assert user["must_change_password"] is False


def test_admin_created_user_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED_PASSWORD)
    store = _store(tmp_path)

    created = store.add_user("auditor_01", "AuditorPass2026")

    assert created["must_change_password"] is False


# ---------------------------------------------------------------------------
# 3. 拦截逻辑（纯函数层，正反对照）
# ---------------------------------------------------------------------------
def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_enforce_password_change_blocks_business_paths():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        enforce_password_change(_request("/api/jobs"), {"must_change_password": True})

    assert excinfo.value.status_code == 403
    assert "password change required" in str(excinfo.value.detail)
    assert excinfo.value.headers == {"X-Password-Change-Required": "1"}


@pytest.mark.parametrize("path", sorted(PASSWORD_CHANGE_EXEMPT_PATHS))
def test_enforce_password_change_allows_exempt_paths(path):
    """放行清单：不放开这几个，用户就无法完成改密。"""
    enforce_password_change(_request(path), {"must_change_password": True})


def test_enforce_password_change_is_noop_without_flag():
    """对照：没有置位时不得拦任何路径。"""
    enforce_password_change(_request("/api/jobs"), {"must_change_password": False})
    enforce_password_change(_request("/api/jobs"), {})


# ---------------------------------------------------------------------------
# 4. 端到端：首登未改密被拦 / 改密后放行
# ---------------------------------------------------------------------------
@pytest.fixture
def first_login_client(tmp_path, monkeypatch):
    from api.main import app

    monkeypatch.setenv("USER_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED_PASSWORD)
    monkeypatch.setenv("REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE", "true")
    monkeypatch.setattr("api.runtime.UPLOAD_ROOT", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    reset_user_store()
    with TestClient(app) as client:
        yield client
    reset_user_store()


def _headers(token: str | None = None) -> Dict[str, str]:
    headers = {"X-API-Key": API_KEY}
    if token:
        headers["X-Session-Token"] = token
    return headers


def _login(client: TestClient, password: str) -> Dict[str, Any]:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": password},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_first_login_is_blocked_until_password_changed(first_login_client):
    payload = _login(first_login_client, SEEDED_PASSWORD)
    token = payload["token"]
    # 登录响应必须告诉客户端需要改密，否则前端无法引导
    assert payload["user"]["must_change_password"] is True

    # 反例：未改密访问业务接口被 403 拦
    blocked = first_login_client.get("/api/jobs", headers=_headers(token))
    assert blocked.status_code == 403
    assert "password change required" in blocked.text
    assert blocked.headers.get("X-Password-Change-Required") == "1"

    # 未改密也不能用管理员用户管理接口（走的是 auth 路由自己的登录校验）
    blocked_users = first_login_client.get("/api/users", headers=_headers(token))
    assert blocked_users.status_code == 403

    # 放行清单可用：拿得到自己的状态
    me = first_login_client.get("/api/auth/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["user"]["must_change_password"] is True

    # 改密
    changed = first_login_client.post(
        "/api/auth/change-password",
        json={"old_password": SEEDED_PASSWORD, "new_password": NEW_PASSWORD},
        headers=_headers(token),
    )
    assert changed.status_code == 200

    # 正例：改密后重新登录即可访问业务接口
    new_payload = _login(first_login_client, NEW_PASSWORD)
    assert new_payload["user"]["must_change_password"] is False
    allowed = first_login_client.get("/api/jobs", headers=_headers(new_payload["token"]))
    assert allowed.status_code == 200
    allowed_users = first_login_client.get("/api/users", headers=_headers(new_payload["token"]))
    assert allowed_users.status_code == 200


# ---------------------------------------------------------------------------
# 5. 安全响应头
# ---------------------------------------------------------------------------
def _headers_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/ping")
    async def ping() -> Dict[str, str]:
        return {"ok": "1"}

    @app.get("/docs")
    async def docs() -> Dict[str, str]:
        return {"ok": "docs"}

    return app


def test_security_headers_present_on_api_response(monkeypatch):
    monkeypatch.delenv("SECURITY_CSP", raising=False)
    monkeypatch.delenv("SECURITY_HSTS_ALWAYS", raising=False)
    client = TestClient(_headers_app())

    response = client.get("/api/ping")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp


def test_hsts_only_on_https(monkeypatch):
    monkeypatch.delenv("SECURITY_HSTS_ALWAYS", raising=False)
    client = TestClient(_headers_app())

    # 反例：http 请求不得下发 HSTS（否则本地开发会被浏览器强制升级到 https）
    plain = client.get("/api/ping")
    assert "Strict-Transport-Security" not in plain.headers

    # 正例：反代标记为 https 时下发
    forwarded = client.get("/api/ping", headers={"X-Forwarded-Proto": "https"})
    assert forwarded.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_hsts_can_be_forced(monkeypatch):
    monkeypatch.setenv("SECURITY_HSTS_ALWAYS", "true")
    client = TestClient(_headers_app())
    assert "Strict-Transport-Security" in client.get("/api/ping").headers


def test_docs_path_uses_relaxed_csp(monkeypatch):
    monkeypatch.delenv("SECURITY_CSP_DOCS", raising=False)
    client = TestClient(_headers_app())

    csp = client.get("/docs").headers["Content-Security-Policy"]

    # Swagger UI 需要 CDN 脚本与内联初始化脚本，否则文档页白屏
    assert "https://cdn.jsdelivr.net" in csp
    assert "script-src" in csp
    # 但仍然禁止被嵌套框架
    assert "frame-ancestors 'none'" in csp


def test_security_headers_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SECURITY_HEADERS_ENABLED", "false")
    client = TestClient(_headers_app())
    assert "Content-Security-Policy" not in client.get("/api/ping").headers


def test_security_headers_are_configurable(monkeypatch):
    monkeypatch.setenv("SECURITY_CSP", "default-src 'self'")
    monkeypatch.setenv("SECURITY_FRAME_OPTIONS", "SAMEORIGIN")
    client = TestClient(_headers_app())

    response = client.get("/api/ping")

    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


# ---------------------------------------------------------------------------
# 6. 与既有鉴权/免认证端点的兼容
# ---------------------------------------------------------------------------
def test_exempt_endpoints_still_accessible_and_carry_headers(tmp_path, monkeypatch):
    from api.main import app

    monkeypatch.setattr("api.runtime.UPLOAD_ROOT", tmp_path)
    with TestClient(app) as client:
        for path in ("/health", "/api/health", "/ready", "/api/ready"):
            response = client.get(path)
            assert response.status_code in {200, 503}, path
            assert response.headers["X-Content-Type-Options"] == "nosniff", path
            assert "Content-Security-Policy" in response.headers, path


def test_openapi_schema_still_served(tmp_path, monkeypatch):
    from api.main import app

    monkeypatch.setattr("api.runtime.UPLOAD_ROOT", tmp_path)
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert "paths" in response.json()
        # 文档类路径使用宽松 CSP
        assert "cdn.jsdelivr.net" in response.headers["Content-Security-Policy"]
