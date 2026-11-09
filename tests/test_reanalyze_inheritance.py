"""重新分析检查方式继承契约测试（2026-11-08 任务书场景）。

覆盖：系统默认开关变化、原模型删除/停用/配置变化、上轮 AI 失败/部分完成、
混合批量逐条继承、历史配置缺失、模型校验失败不清除已有结果。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api import runtime
from src.services import ai_model_settings as settings
from src.services import reanalyze_inheritance as inheritance


def _profile(profile_id: str = "model-a", **overrides) -> settings.ModelProfile:
    base = {
        "id": profile_id,
        "name": f"模型{profile_id}",
        "provider_type": "openai_compat",
        "base_url": "https://example.com/v1",
        "model": "test-model",
        "api_key": "sk-test",
    }
    base.update(overrides)
    return settings.ModelProfile.model_validate(base)


def _save_settings(tmp_path, monkeypatch, *, enabled=True, default_use_ai=False,
                   active="model-a", profiles=None) -> None:
    monkeypatch.setenv("AI_MODEL_SETTINGS_PATH", str(tmp_path / "ai-settings.json"))
    settings.save_settings(settings.ModelSettingsUpdate(
        enabled=enabled, default_use_ai=default_use_ai,
        active_profile_id=active if enabled else None,
        profiles=profiles if profiles is not None else [_profile()],
    ))


def _make_job(tmp_path, name="job-1", **status_fields) -> Path:
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    (directory / "source.pdf").write_bytes(b"%PDF-1.4 fake")
    payload = {"job_id": name, "status": "done", "result": {"summary": "preserved"}}
    payload.update(status_fields)
    (directory / "status.json").write_text(json.dumps(payload, ensure_ascii=False))
    return directory


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", root)
    return root


@pytest.fixture
def captured_starts(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_start(job_id, body=None):
        calls.append((job_id, dict(body or {})))
        return {"job_id": job_id, "status": "started", "dispatch": "local_queue"}

    monkeypatch.setattr(runtime, "start_analysis", fake_start)
    return calls


# ---------------------------------------------------------------------------
# 系统默认开关变化不影响继承
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inherit_ignores_system_default_on(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, default_use_ai=True)
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=False)
    await runtime.reanalyze_job("job-1", {})
    body = captured_starts[0][1]
    assert body["use_ai_assist"] is False
    assert body["mode"] == "dual"
    assert body["use_local_rules"] is True


@pytest.mark.asyncio
async def test_inherit_keeps_ai_on_when_system_default_off(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, default_use_ai=False)
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True,
              ai_profile_id="model-a", ai_configuration={"id": "model-a", "name": "模型model-a", "model": "test-model", "revision": 1})
    result = await runtime.reanalyze_job("job-1", {})
    body = captured_starts[0][1]
    assert body["use_ai_assist"] is True
    assert body["ai_profile_id"] == "model-a"
    assert result["check_method"] == "inherit"


@pytest.mark.asyncio
async def test_inherit_keeps_original_model_not_system_active(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, active="model-b",
                   profiles=[_profile("model-a"), _profile("model-b")])
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True, ai_profile_id="model-a")
    await runtime.reanalyze_job("job-1", {"check_method": "inherit"})
    assert captured_starts[0][1]["ai_profile_id"] == "model-a"


# ---------------------------------------------------------------------------
# 原模型删除 / 停用 / 配置变化
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deleted_model_blocks_and_preserves_results(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, active="model-b", profiles=[_profile("model-b")])
    directory = _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True,
                          ai_profile_id="model-a", ai_configuration={"id": "model-a", "name": "模型model-a", "model": "test-model", "revision": 1})
    (directory / "fact_context.json").write_text('{"preserved":true}')
    before_status = (directory / "status.json").read_bytes()
    before_fact = (directory / "fact_context.json").read_bytes()
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job("job-1", {})
    assert exc.value.status_code == 422
    assert "模型model-a" in str(exc.value.detail) and "已删除" in str(exc.value.detail)
    assert (directory / "status.json").read_bytes() == before_status
    assert (directory / "fact_context.json").read_bytes() == before_fact
    assert captured_starts == []


@pytest.mark.asyncio
async def test_disabled_ai_blocks_and_preserves_results(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, enabled=False, profiles=[_profile()])
    directory = _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True, ai_profile_id="model-a")
    before = (directory / "status.json").read_bytes()
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job("job-1", {})
    assert exc.value.status_code == 422
    assert "停用" in str(exc.value.detail)
    assert (directory / "status.json").read_bytes() == before
    assert captured_starts == []


@pytest.mark.asyncio
async def test_changed_model_config_warns_but_runs(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, profiles=[_profile("model-a", model="new-model")])
    directory = _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True,
                          ai_profile_id="model-a",
                          ai_configuration={"id": "model-a", "name": "模型model-a", "model": "test-model", "revision": 1})
    result = await runtime.reanalyze_job("job-1", {})
    assert captured_starts[0][1]["ai_profile_id"] == "model-a"
    assert result["inheritance_warnings"] and "配置已变化" in result["inheritance_warnings"][0]
    preview = inheritance.build_options_preview(directory, json.loads((directory / "status.json").read_text()))
    assert preview["model"]["state"] == "changed"
    assert "model" in preview["model"]["changed_fields"]


# ---------------------------------------------------------------------------
# 上轮 AI 失败 / 部分完成：保留启用选择并展示执行状态
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_ai_run_still_inherits_ai_choice(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch)
    directory = _make_job(upload_root, status="error", mode="dual", use_local_rules=True,
                          use_ai_assist=True, ai_profile_id="model-a")
    (directory / "check_progress.json").write_text(json.dumps({
        "local": {"state": "completed"}, "ai": {"state": "failed", "current_step": "AI分析超时"},
    }))
    preview = inheritance.build_options_preview(directory, json.loads((directory / "status.json").read_text()))
    assert preview["inherit"]["available"] is True
    assert preview["inherit"]["use_ai_assist"] is True
    assert preview["last_execution"]["ai_state"] == "failed"
    assert preview["last_execution"]["ai_requested"] is True
    await runtime.reanalyze_job("job-1", {})
    assert captured_starts[0][1]["use_ai_assist"] is True


def test_partial_ai_execution_visible_in_preview(upload_root, tmp_path, monkeypatch):
    _save_settings(tmp_path, monkeypatch)
    directory = _make_job(upload_root, status="review_required", mode="dual", use_local_rules=True,
                          use_ai_assist=True, ai_profile_id="model-a")
    (directory / "check_progress.json").write_text(json.dumps({
        "local": {"state": "completed"}, "ai": {"state": "degraded"},
    }))
    preview = inheritance.build_options_preview(directory, json.loads((directory / "status.json").read_text()))
    assert preview["last_execution"]["ai_state"] == "degraded"
    assert preview["inherit"]["use_ai_assist"] is True


# ---------------------------------------------------------------------------
# 混合批量逐条继承
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_inherits_per_job(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, default_use_ai=True)
    _make_job(upload_root, name="job-legacy", mode="legacy", use_local_rules=True, use_ai_assist=False)
    _make_job(upload_root, name="job-ai", mode="dual", use_local_rules=True, use_ai_assist=True, ai_profile_id="model-a")
    result = await runtime.reanalyze_all_jobs({"latest_per_department": False})
    assert result["created_count"] == 2
    bodies = {job_id: body for job_id, body in captured_starts}
    assert bodies["job-legacy"]["use_ai_assist"] is False
    assert bodies["job-legacy"]["mode"] == "legacy"
    assert bodies["job-ai"]["use_ai_assist"] is True
    assert bodies["job-ai"]["ai_profile_id"] == "model-a"
    assert all(item["check_method"] == "inherit" for item in result["created"])


@pytest.mark.asyncio
async def test_batch_reports_per_item_inheritance_failure(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, active="model-b", profiles=[_profile("model-b")])
    _make_job(upload_root, name="job-ok", mode="legacy", use_local_rules=True, use_ai_assist=False)
    _make_job(upload_root, name="job-deleted", mode="dual", use_local_rules=True, use_ai_assist=True,
              ai_profile_id="model-a", ai_configuration={"id": "model-a", "name": "模型model-a", "model": "test-model", "revision": 1})
    before = (upload_root / "job-deleted" / "status.json").read_bytes()
    result = await runtime.reanalyze_all_jobs({"latest_per_department": False})
    assert result["created_count"] == 1
    assert result["failed_count"] == 1
    assert result["failed"][0]["source_job_id"] == "job-deleted"
    assert "已删除" in str(result["failed"][0]["detail"])
    assert (upload_root / "job-deleted" / "status.json").read_bytes() == before
    assert [job_id for job_id, _ in captured_starts] == ["job-ok"]


# ---------------------------------------------------------------------------
# 历史配置缺失：标未知，不假装继承
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_history_rejects_inherit(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch)
    directory = _make_job(upload_root)  # 无 mode/use_ai_assist/use_local_rules 记录
    before = (directory / "status.json").read_bytes()
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job("job-1", {})
    assert exc.value.status_code == 409
    assert "没有已记录" in str(exc.value.detail)
    assert (directory / "status.json").read_bytes() == before
    assert captured_starts == []
    preview = inheritance.build_options_preview(directory, {})
    assert preview["inherit"]["available"] is False
    assert set(preview["inherit"]["unknown_fields"]) == {"mode", "use_local_rules", "use_ai_assist"}


@pytest.mark.asyncio
async def test_missing_model_record_rejects_inherit(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch)
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True)  # 无模型记录
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job("job-1", {})
    assert exc.value.status_code == 409
    assert "模型未记录" in str(exc.value.detail)
    assert captured_starts == []


def test_legacy_history_without_ai_flag_inherits_as_local_only(upload_root, tmp_path, monkeypatch):
    _save_settings(tmp_path, monkeypatch, default_use_ai=True)
    directory = _make_job(upload_root, mode="legacy")  # 老数据只有 mode
    preview = inheritance.build_options_preview(directory, json.loads((directory / "status.json").read_text()))
    assert preview["inherit"]["available"] is True
    assert preview["inherit"]["use_ai_assist"] is False
    assert preview["inherit"]["use_local_rules"] is True


# ---------------------------------------------------------------------------
# 显式选择与兼容路径
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_ai_choice_uses_selected_model(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, active="model-a",
                   profiles=[_profile("model-a"), _profile("model-b")])
    _make_job(upload_root, mode="legacy", use_local_rules=True, use_ai_assist=False)
    result = await runtime.reanalyze_job("job-1", {"check_method": "ai", "ai_profile_id": "model-b"})
    body = captured_starts[0][1]
    assert body["mode"] == "dual" and body["use_ai_assist"] is True
    assert body["ai_profile_id"] == "model-b"
    assert result["check_method"] == "ai"


@pytest.mark.asyncio
async def test_explicit_ai_choice_defaults_to_system_model(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, active="model-a")
    _make_job(upload_root, mode="legacy", use_local_rules=True, use_ai_assist=False)
    await runtime.reanalyze_job("job-1", {"check_method": "ai"})
    assert captured_starts[0][1]["ai_profile_id"] is None  # start_analysis 解析为系统当前模型


@pytest.mark.asyncio
async def test_legacy_explicit_body_inherits_ai_flag_not_system_default(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, default_use_ai=True)
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=False)
    # 旧 UI（队列/审核台/工作台）发送的参数：AI 开关必须继承上轮而非系统默认。
    await runtime.reanalyze_job("job-1", {"mode": "dual", "use_local_rules": True})
    assert captured_starts[0][1]["use_ai_assist"] is False


@pytest.mark.asyncio
async def test_empty_body_correction_path_inherits(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch, default_use_ai=True)
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True, ai_profile_id="model-a")
    # 校正后自动重查调 reanalyze_job(job_id) 空 body。
    await runtime.reanalyze_job("job-1")
    body = captured_starts[0][1]
    assert body["use_ai_assist"] is True and body["ai_profile_id"] == "model-a"


@pytest.mark.asyncio
async def test_inherit_rejects_conflicting_explicit_keys(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch)
    _make_job(upload_root, mode="legacy", use_local_rules=True, use_ai_assist=False)
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job("job-1", {"check_method": "inherit", "use_ai_assist": True})
    assert exc.value.status_code == 422
    assert captured_starts == []


@pytest.mark.asyncio
async def test_invalid_explicit_request_preserves_results(upload_root, tmp_path, monkeypatch, captured_starts):
    _save_settings(tmp_path, monkeypatch)
    directory = _make_job(upload_root, mode="legacy", use_local_rules=True, use_ai_assist=False)
    before = (directory / "status.json").read_bytes()
    with pytest.raises(HTTPException) as exc:
        await runtime.reanalyze_job("job-1", {"mode": "legacy", "use_local_rules": False, "use_ai_assist": False})
    assert exc.value.status_code == 422
    assert (directory / "status.json").read_bytes() == before
    assert captured_starts == []


# ---------------------------------------------------------------------------
# 预览路由（薄封装，鉴权放行）
# ---------------------------------------------------------------------------

def test_reanalyze_options_route(tmp_path, upload_root, monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routes import jobs as jobs_routes

    _save_settings(tmp_path, monkeypatch)
    _make_job(upload_root, mode="dual", use_local_rules=True, use_ai_assist=True, ai_profile_id="model-a")
    monkeypatch.setattr(jobs_routes, "require_job_access", lambda *a, **k: (None, "", {"username": "tester"}, {}))
    client = TestClient(app)
    response = client.get("/api/jobs/job-1/reanalyze-options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == "job-1"
    assert payload["inherit"]["available"] is True
    assert payload["inherit"]["ai_profile_id"] == "model-a"
    assert payload["model"]["state"] == "ok"
    assert isinstance(payload["capabilities"]["profiles"], list)

# ---------------------------------------------------------------------------
# 校正后的重分析：内联回退也必须先过继承与模型可用性预检（复验 RI-03）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_correction_inline_fallback_rejects_deleted_model_before_dispatch(
    upload_root, tmp_path, monkeypatch, captured_starts
):
    from starlette.requests import Request

    from api import queue_runtime
    from api.routes import corrections

    _save_settings(tmp_path, monkeypatch, active="model-b", profiles=[_profile("model-b")])
    directory = _make_job(
        upload_root,
        status="done",
        mode="dual",
        use_local_rules=True,
        use_ai_assist=True,
        ai_profile_id="model-a",
        ai_configuration={
            "id": "model-a",
            "name": "模型model-a",
            "model": "test-model",
            "revision": 1,
        },
    )
    payload = json.loads((directory / "status.json").read_text())
    before = (directory / "status.json").read_bytes()

    monkeypatch.setattr(
        corrections, "require_job_access",
        lambda *a, **k: (None, "", {"username": "reviewer"}, payload),
    )
    monkeypatch.setattr(corrections, "_job_dir", lambda _job_id: directory)
    monkeypatch.setattr(corrections, "_current_basis_or_409", lambda *a, **k: "job-1:rev-1")
    monkeypatch.setattr(corrections, "report_evidence_ids", lambda _payload: {"ev-1"})
    monkeypatch.setattr(
        corrections, "append_record_with_basis_cas", lambda *a, **k: {"accepted": True}
    )
    # 队列关闭且允许内联回退：修复前这条分支会直接调用流水线 runner。
    monkeypatch.setattr(queue_runtime, "queue_enabled", lambda: False)
    monkeypatch.setattr(queue_runtime, "allow_inline_fallback", lambda: True)

    body = corrections.CorrectionRequest(
        analysis_basis_token="job-1:rev-1",
        reason="按原件复核后修正金额",
        changes=[
            {
                "target_type": "fact",
                "target_id": "fact-1",
                "field": "raw_value",
                "value": "123.45",
                "evidence_ids": ["ev-1"],
            }
        ],
    )
    response = await corrections.create_job_correction("job-1", body, Request({"type": "http"}))

    assert captured_starts == [], "模型不可用时不得启动流水线"
    assert response["reanalysis_status"] == "not_executed_dispatch_rejected"
    assert "已删除" in response["reanalysis_detail"]
    assert response["review_decisions_status"] == "unchanged_until_new_analysis_is_published"
    assert (directory / "status.json").read_bytes() == before
