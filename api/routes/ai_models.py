"""Administrator model management and safe upload capabilities."""
from __future__ import annotations
import asyncio
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from api.auth_utils import require_admin, require_login
from src.services import ai_model_settings as settings
from src.services.audit_log import append_audit_event

router = APIRouter()


@router.get("/api/ai/capabilities")
async def ai_capabilities(request: Request):
    require_login(request)
    return settings.capabilities()


@router.get("/api/admin/ai-models")
async def get_ai_models(request: Request):
    require_admin(request)
    return settings.get_settings()


@router.put("/api/admin/ai-models")
async def put_ai_models(request: Request):
    _, _, user = require_admin(request)
    try:
        update = settings.ModelSettingsUpdate.model_validate(await request.json())
        saved = settings.save_settings(update)
        append_audit_event(action="ai_models.update", actor=str(user.get("username") or ""), result="success", resource_type="system_setting",
                           details={"revision": saved["revision"], "enabled": saved["enabled"], "active_profile_id": saved["active_profile_id"], "profiles_count": len(saved["profiles"])})
        return saved
    except settings.SettingsConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValidationError as exc:
        # Pydantic errors contain raw input (including API keys); never send them.
        messages = [str(item.get("msg", "配置无效")) for item in exc.errors(include_input=False)]
        raise HTTPException(422, "；".join(messages)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/admin/ai-models/test")
async def test_ai_model(request: Request):
    require_admin(request)
    try:
        profile = settings.ModelProfile.model_validate(await request.json())
        runtime = settings.resolve_runtime(for_test=True, draft=profile)
    except ValidationError:
        raise HTTPException(422, "请填写完整、有效的模型配置")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    started = time.monotonic()
    try:
        # Connection tests should spend their budget on one request. Retrying a
        # slow reasoning model made the old 60s outer deadline expire before the
        # configured 90s request timeout could be returned to the browser.
        test_timeout = runtime["timeout_s"]
        response = await asyncio.wait_for(settings.create_client(runtime, retries=0).chat(
            messages=[{"role": "user", "content": "请只回复 OK，用于验证接口连接。"}],
            preferred_provider="managed", model=runtime["model"], temperature=0, max_tokens=512,
            timeout=test_timeout), timeout=test_timeout + 3)
        if not str(response.get("content") or "").strip():
            raise ValueError("empty_response")
        return {"ok": True, "model": response.get("model"), "latency_ms": round((time.monotonic()-started)*1000), "message": "连接成功，已收到模型响应"}
    except asyncio.TimeoutError:
        return {"ok": False, "error_code": "timeout", "message": f"模型在 {runtime['timeout_s']} 秒内未返回响应，请提高超时设置或确认服务商状态。"}
    except Exception as exc:
        code = str(getattr(getattr(exc, "error_type", None), "value", "connection_failed"))
        messages = {"authentication": "密钥无效或没有访问权限", "model_not_found": "模型名称不存在或没有调用权限", "rate_limit": "服务限流，请稍后重试", "timeout": "连接超时", "quota_exceeded": "服务额度不足"}
        # Remote errors can echo request headers. Expose a fixed message only.
        return {"ok": False, "error_code": code, "message": messages.get(code, "连接失败，请检查 API 地址、密钥、模型名称与网络")}
