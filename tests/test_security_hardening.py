from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from api.routes import health as health_routes
from src.security import SecurityConfig, SecurityMiddleware


class _StubManager:
    def __init__(self) -> None:
        self.rate_limit_called = False
        self.validate_called = False

    def check_rate_limit(self, _client_id: str) -> tuple[bool, int]:
        self.rate_limit_called = True
        return True, 99

    def validate_key(self, _api_key: str) -> bool:
        self.validate_called = True
        return True


def _build_request(
    method: str = "GET",
    path: str = "/secure",
    headers: dict[str, str] | None = None,
    client_host: str = "198.51.100.2",
) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode("latin-1"),
        "headers": raw_headers,
        "query_string": b"",
        "client": (client_host, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


@pytest.mark.anyio
async def test_security_middleware_bypasses_cors_preflight() -> None:
    manager = _StubManager()
    middleware = SecurityMiddleware(
        FastAPI(),
        config=SecurityConfig(enabled=True, api_key="test-key"),
        manager=manager,
    )
    request = _build_request(
        method="OPTIONS",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    async def _call_next(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    response = await middleware.dispatch(request, _call_next)

    assert response.status_code == 200
    assert not manager.rate_limit_called
    assert not manager.validate_called


def test_get_client_id_ignores_x_forwarded_for_by_default() -> None:
    middleware = SecurityMiddleware(
        FastAPI(),
        config=SecurityConfig(enabled=True, api_key="test-key"),
        manager=_StubManager(),
    )
    request = _build_request(
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.1"},
        client_host="198.51.100.2",
    )

    assert middleware._get_client_id(request) == "198.51.100.2"


def test_get_client_id_uses_x_forwarded_for_for_trusted_proxy() -> None:
    middleware = SecurityMiddleware(
        FastAPI(),
        config=SecurityConfig(
            enabled=True,
            api_key="test-key",
            trust_proxy_headers=True,
            trusted_proxy_ips={"10.0.0.1"},
        ),
        manager=_StubManager(),
    )
    request = _build_request(
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.1"},
        client_host="10.0.0.1",
    )

    assert middleware._get_client_id(request) == "203.0.113.10"


def test_get_client_id_ignores_x_forwarded_for_from_untrusted_proxy() -> None:
    middleware = SecurityMiddleware(
        FastAPI(),
        config=SecurityConfig(
            enabled=True,
            api_key="test-key",
            trust_proxy_headers=True,
            trusted_proxy_ips={"10.0.0.1"},
        ),
        manager=_StubManager(),
    )
    request = _build_request(
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.1"},
        client_host="198.51.100.2",
    )

    assert middleware._get_client_id(request) == "198.51.100.2"


@pytest.mark.anyio
async def test_readiness_ai_check_uses_default_url_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_urls: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str) -> SimpleNamespace:
            captured_urls.append(url)
            return SimpleNamespace(status_code=200)

    monkeypatch.setenv("AI_ASSIST_ENABLED", "true")
    monkeypatch.delenv("AI_EXTRACTOR_URL", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    ok, detail = await health_routes._check_ai_extractor()

    assert ok is True
    assert detail == "reachable:200"
    assert captured_urls == ["http://127.0.0.1:9009/ai/extract/v1"]
