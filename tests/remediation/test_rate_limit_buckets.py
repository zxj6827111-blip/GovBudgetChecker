"""限流额度：网页请求都经前端服务转发，不能让全体用户共用一个来源 IP 的额度；
前端每次转发前的会话校验不计数；没有服务密钥的请求仍按来源 IP 限流。
"""
import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from src.security import APIKeyManager, SecurityConfig, SecurityMiddleware
from tests.test_security_hardening import _build_request


def middleware(limit=3):
    config = SecurityConfig(enabled=True, api_key="test-key", rate_limit=limit)
    return SecurityMiddleware(FastAPI(), config=config, manager=APIKeyManager(config))


async def call(guard, path="/api/jobs", **headers):
    async def ok(_request):
        return PlainTextResponse("ok")

    return (await guard.dispatch(_build_request(path=path, headers=headers), ok)).status_code


@pytest.mark.anyio
async def test_each_signed_in_user_has_its_own_quota_behind_the_frontend_proxy():
    guard = middleware()
    alice = {"X-API-Key": "test-key", "X-Session-Token": "alice"}
    assert [await call(guard, **alice) for _ in range(4)] == [200, 200, 200, 429]
    assert await call(guard, **{"X-API-Key": "test-key", "X-Session-Token": "bob"}) == 200


@pytest.mark.anyio
async def test_session_check_before_each_proxied_call_is_not_counted():
    guard = middleware()
    alice = {"X-API-Key": "test-key", "X-Session-Token": "alice"}
    for _ in range(5):
        assert await call(guard, "/api/auth/me", **alice) == 200
    assert [await call(guard, **alice) for _ in range(3)] == [200, 200, 200]


@pytest.mark.anyio
async def test_requests_without_the_service_key_stay_limited_by_address():
    guard = middleware()
    # 换会话令牌或冒充会话校验都不能绕过按来源 IP 的限流。
    statuses = [await call(guard, "/api/auth/me", **{"X-API-Key": "wrong", "X-Session-Token": f"t{i}"})
                for i in range(4)]
    assert statuses == [403, 403, 403, 429]
