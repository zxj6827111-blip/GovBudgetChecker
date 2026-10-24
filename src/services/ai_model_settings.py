"""Shared, hot-reloaded model profiles. Secrets never enter public task metadata."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    provider_type: str = "openai_compat"
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=150)
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    timeout_s: int = Field(default=90, ge=10, le=300)
    max_tokens: int = Field(default=4000, ge=512, le=64000)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            raise ValueError("模型配置 ID 只能包含字母、数字、下划线和连字符")
        return value

    @field_validator("name", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空")
        return value.strip()

    @field_validator("provider_type")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in {"openai_compat", "zhipu", "doubao"}:
            raise ValueError("不支持的模型接口类型")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("API 地址应为 HTTP/HTTPS 基础地址，不能包含账户、查询参数或片段")
        return value


class ModelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    enabled: StrictBool = False
    default_use_ai: StrictBool = False
    active_profile_id: str | None = None
    profiles: list[ModelProfile] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_profiles(self):
        ids = [profile.id for profile in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("模型配置 ID 重复")
        if self.active_profile_id and self.active_profile_id not in ids:
            raise ValueError("当前模型不在配置列表中")
        if self.enabled and not self.active_profile_id:
            raise ValueError("启用大模型检查前请选择当前模型")
        return self


class SettingsConflict(ValueError):
    pass


_lock = threading.RLock()


def settings_path() -> Path:
    return Path(os.getenv("AI_MODEL_SETTINGS_PATH", str(Path(__file__).resolve().parents[2] / "data" / "ai-model-settings.json")))


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.with_suffix(".lock").open("a+b") as handle:
        if os.name == "posix":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _cipher(path: Path) -> Fernet:
    configured = os.getenv("AI_CONFIG_ENCRYPTION_KEY", "").strip()
    if configured:
        return Fernet(configured.encode())
    key_path = path.with_suffix(".key")
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as handle:
            handle.write(Fernet.generate_key())
            handle.flush()
            os.fsync(handle.fileno())
    return Fernet(key_path.read_bytes())


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"revision": 0, "enabled": False, "default_use_ai": False, "active_profile_id": None, "profiles": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
        raise ValueError("大模型配置文件无效，请管理员修复配置")
    return data


def public_settings(data: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: data.get(key) for key in ("revision", "enabled", "default_use_ai", "active_profile_id", "updated_at")},
        "profiles": [{**{key: p.get(key) for key in ("id", "name", "provider_type", "base_url", "model", "timeout_s", "max_tokens")},
                      "has_api_key": bool(p.get("encrypted_api_key"))} for p in data["profiles"]],
    }


def get_settings() -> dict[str, Any]:
    return public_settings(_read(settings_path()))


def save_settings(update: ModelSettingsUpdate) -> dict[str, Any]:
    path = settings_path()
    with _file_lock(path):
        previous = _read(path)
        if previous["revision"] != update.revision:
            raise SettingsConflict("配置已被其他管理员更新，请刷新后重新保存")
        old = {profile["id"]: profile for profile in previous["profiles"]}
        profiles = []
        for profile in update.profiles:
            item = profile.model_dump(exclude={"api_key"})
            key = (profile.api_key or "").strip()
            item["encrypted_api_key"] = (_cipher(path).encrypt(key.encode()).decode() if key else old.get(profile.id, {}).get("encrypted_api_key"))
            if not item["encrypted_api_key"]:
                raise ValueError(f"请填写模型“{profile.name}”的 API 密钥")
            profiles.append(item)
        data = {**update.model_dump(exclude={"profiles"}), "profiles": profiles,
                "revision": previous["revision"] + 1, "updated_at": time.time()}
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".ai-settings-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return public_settings(data)


def resolve_runtime(profile_id: str | None = None, *, for_test: bool = False, draft: ModelProfile | None = None) -> dict[str, Any] | None:
    """Return a task-local decrypted snapshot; None preserves legacy env configuration."""
    path = settings_path()
    data = _read(path)
    if not path.exists() and not draft:
        if os.getenv("AI_ASSIST_ENABLED", "false").lower() != "true" and not for_test:
            raise ValueError("大模型检查尚未启用，请在系统设置中配置并启用")
        return None
    if not data["enabled"] and not for_test:
        raise ValueError("大模型检查已关闭，请在系统设置中启用")
    selected = draft.id if draft else (profile_id or data.get("active_profile_id"))
    stored = next((p for p in data["profiles"] if p["id"] == selected), {})
    profile = draft.model_dump(exclude={"api_key"}) if draft else dict(stored)
    if not profile:
        raise ValueError("所选模型已删除，请重新选择模型")
    key = (draft.api_key or "").strip() if draft else ""
    if not key:
        encrypted = stored.get("encrypted_api_key")
        if not encrypted:
            raise ValueError("请先填写 API 密钥")
        try:
            key = _cipher(path).decrypt(encrypted.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("模型密钥无法解密，请重新保存 API 密钥") from exc
    return {**{k: profile[k] for k in ("id", "name", "provider_type", "base_url", "model", "timeout_s", "max_tokens")},
            "api_key": key, "enabled": True, "revision": data["revision"]}


def capabilities() -> dict[str, Any]:
    path = settings_path()
    if path.exists():
        data = _read(path)
        ready = [p for p in data["profiles"] if p.get("encrypted_api_key")]
        active = next((p for p in ready if p["id"] == data.get("active_profile_id")), None)
        enabled = bool(data["enabled"] and active)
        return {"enabled": enabled, "configured": bool(active), "default_use_ai": bool(enabled and data["default_use_ai"]),
                "active_profile_id": data.get("active_profile_id"), "model": active.get("model") if active else None,
                "profiles": [{"id": p["id"], "name": p["name"], "model": p["model"]} for p in ready], "source": "settings"}
    enabled = os.getenv("AI_ASSIST_ENABLED", "false").lower() == "true"
    return {"enabled": enabled, "configured": enabled, "default_use_ai": False,
            "active_profile_id": None, "model": os.getenv("AI_MAIN_MODEL") or None, "profiles": [], "source": "environment"}


def create_client(runtime: dict[str, Any], *, retries: int = 1):
    from src.services.ai_client import AIClient, AIClientConfig
    return AIClient(config=AIClientConfig(providers={"managed": {**runtime, "retries": retries}}, fallback_chain=["managed"]))


SNAPSHOT_FILENAME = "ai_config_snapshot.json"


def save_job_snapshot(directory: Path, runtime: dict[str, Any] | None) -> None:
    """Freeze submitted model settings without persisting a plaintext API key."""
    target = directory / SNAPSHOT_FILENAME
    if runtime is None:
        target.unlink(missing_ok=True)
        return
    with _file_lock(settings_path()):
        encrypted = _cipher(settings_path()).encrypt(json.dumps(runtime).encode()).decode()
    fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".ai-snapshot-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"encrypted_config": encrypted}, handle)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def resolve_job_runtime(job_id: str, profile_id: str | None = None) -> dict[str, Any] | None:
    from src.services.check_progress import job_dir
    target = job_dir(job_id) / SNAPSHOT_FILENAME
    if not target.exists():
        return resolve_runtime(profile_id)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return json.loads(_cipher(settings_path()).decrypt(data["encrypted_config"].encode()))
    except Exception as exc:
        raise ValueError("任务模型配置无法读取，请重新保存密钥后重分析") from exc


def audit_time_budget(timeout_s: int) -> int:
    """Allow each audit batch its request timeout and one retry."""
    import math
    def number(name: str, default: int) -> int:
        try:
            return max(1, min(100, int(os.getenv(name, str(default)))))
        except ValueError:
            return default
    batches = math.ceil(number("AI_AUDIT_MAX_WINDOWS", 12) / number("AI_AUDIT_MAX_CONCURRENCY", 2))
    return min(6600, max(120, batches * (timeout_s * 2 + 5) + 30))
