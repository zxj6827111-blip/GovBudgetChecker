"""重新分析的检查方式继承契约（2026-11-08 任务书）。

默认以本材料最近一轮已记录的启动配置为准：status.json 中的
mode / use_local_rules / use_ai_assist / ai_profile_id / ai_configuration。
系统当前 AI 默认开关只用于首次分析，不参与继承；历史配置缺失时
标记未知并拒绝猜测，不得静默更换检查方式或模型。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALID_MODES = {"legacy", "dual", "structured"}
EXPLICIT_CHECK_KEYS = ("mode", "use_local_rules", "use_ai_assist", "ai_profile_id")
_MODEL_COMPARE_FIELDS = (
    "name", "model", "base_url", "provider_type",
    "timeout_s", "max_tokens", "reasoning_effort", "thinking",
)


class InheritanceError(Exception):
    """继承无法成立：409 = 历史缺失/矛盾需显式选择；422 = 请求参数非法。"""

    def __init__(self, message: str, *, status_code: int = 409,
                 unknown_fields: list[str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.unknown_fields = list(unknown_fields or [])


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _flag(value: Any, name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    raise InheritanceError(f"参数 {name} 取值无效，请使用布尔值", status_code=422)


def last_start_config(status: dict[str, Any]) -> dict[str, Any]:
    """提取最近一轮已记录的启动配置；缺失字段保持 None（未知），不猜测。"""
    configuration = status.get("ai_configuration")
    configuration = configuration if isinstance(configuration, dict) else None
    profile_id = status.get("ai_profile_id") or (configuration or {}).get("id")
    mode = status.get("mode")
    return {
        "mode": mode if mode in VALID_MODES else None,
        "use_local_rules": _bool_or_none(status.get("use_local_rules")),
        "use_ai_assist": _bool_or_none(status.get("use_ai_assist")),
        "ai_profile_id": profile_id if isinstance(profile_id, str) and profile_id else None,
        "ai_configuration": configuration,
    }


def _inherit_from_last(last: dict[str, Any]) -> dict[str, Any]:
    """把已记录的上轮配置解析为显式启动参数；无法唯一确定时抛错。"""
    mode, ai, local = last["mode"], last["use_ai_assist"], last["use_local_rules"]
    if mode is None and ai is None and local is None:
        raise InheritanceError(
            "该材料没有已记录的上次检查方式，请显式选择本次检查方式",
            unknown_fields=["mode", "use_local_rules", "use_ai_assist"],
        )
    if mode == "structured":
        return {"mode": "structured", "use_local_rules": True if local is None else local,
                "use_ai_assist": False, "ai_profile_id": None}
    if ai is None:
        if mode == "legacy":
            # legacy 契约上不运行 AI，AI 开关缺失不构成未知。
            ai = False
        else:
            unknown = ["use_ai_assist"] + (["mode"] if mode is None else [])
            raise InheritanceError(
                "上次是否启用大模型未记录，请显式选择本次检查方式",
                unknown_fields=unknown,
            )
    if ai is False:
        if local is False:
            raise InheritanceError("上次启动配置矛盾：未启用任何检查，请显式选择本次检查方式")
        # 能启动的历史任务在 AI 关闭时必然启用了本地规则，否则当时会被 422 拒绝。
        return {"mode": mode or "legacy", "use_local_rules": True,
                "use_ai_assist": False, "ai_profile_id": None}
    if mode == "legacy":
        raise InheritanceError("上次启动配置矛盾：legacy 模式记录了大模型启用，请显式选择本次检查方式")
    if local is None:
        raise InheritanceError(
            "上次本地规则开关未记录，请显式选择本次检查方式",
            unknown_fields=["use_local_rules"],
        )
    if not last["ai_profile_id"]:
        raise InheritanceError(
            "上次使用的模型未记录，请选择「使用大模型参与重新分析」并指定本次模型",
            unknown_fields=["ai_profile_id"],
        )
    return {"mode": "dual", "use_local_rules": local,
            "use_ai_assist": True, "ai_profile_id": last["ai_profile_id"]}


def _snapshot_config(job_dir: Path) -> dict[str, Any] | None:
    """读取上轮加密模型快照用于配置变化比对；读不到不阻断（降级为摘要比对）。"""
    from src.services.ai_model_settings import SNAPSHOT_FILENAME, _cipher, settings_path
    target = job_dir / SNAPSHOT_FILENAME
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        config = json.loads(_cipher(settings_path()).decrypt(data["encrypted_config"].encode()))
        return config if isinstance(config, dict) else None
    except Exception:
        return None


def evaluate_model_state(last: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    """评估上轮模型当前可用性：ok/changed/deleted/disabled/no_key/key_invalid/unknown/not_applicable。

    只返回非敏感摘要与变化字段名，绝不返回密钥。
    """
    if last["use_ai_assist"] is not True:
        return {"state": "not_applicable", "message": None, "profile": None}
    profile_id = last["ai_profile_id"]
    if not profile_id:
        return {"state": "unknown", "message": "上次使用的模型未记录，请指定本次模型", "profile": None}
    from src.services.ai_model_settings import resolve_runtime, settings_path
    if not settings_path().exists():
        return {"state": "unknown",
                "message": "上次分析使用环境变量模型配置，没有可继承的模型档案，请指定本次模型",
                "profile": None}
    try:
        runtime = resolve_runtime(profile_id)
    except ValueError as exc:
        text = str(exc)
        name = (last["ai_configuration"] or {}).get("name") or profile_id
        if "已删除" in text:
            return {"state": "deleted",
                    "message": f"上次使用的模型「{name}」已删除，请选择「使用大模型参与重新分析」并指定本次模型",
                    "profile": None}
        if "已关闭" in text or "尚未启用" in text:
            return {"state": "disabled",
                    "message": "大模型检查已在系统设置中停用，请启用后重试或显式选择本次检查方式",
                    "profile": None}
        if "密钥" in text and "解密" in text:
            return {"state": "key_invalid",
                    "message": f"模型「{name}」的密钥无法解密，请重新保存 API 密钥", "profile": None}
        if "密钥" in text:
            return {"state": "no_key",
                    "message": f"模型「{name}」缺少 API 密钥，请重新保存后再按上次方式重新分析",
                    "profile": None}
        return {"state": "unknown", "message": text, "profile": None}
    if runtime is None:
        return {"state": "unknown",
                "message": "上次分析使用环境变量模型配置，没有可继承的模型档案，请指定本次模型",
                "profile": None}
    summary = {key: runtime[key] for key in ("id", "name", "model")}
    previous = _snapshot_config(job_dir) or last["ai_configuration"] or {}
    changed = [field for field in _MODEL_COMPARE_FIELDS
               if field in previous and previous[field] != runtime.get(field)]
    if "api_key" in previous and previous["api_key"] != runtime.get("api_key"):
        changed.append("api_key")
    if changed:
        labels = "、".join("API 密钥" if field == "api_key" else field for field in changed)
        return {"state": "changed", "profile": summary, "changed_fields": changed,
                "message": f"原模型配置已变化（{labels}），按上次方式执行将使用当前配置"}
    return {"state": "ok", "message": None, "profile": summary}


def resolve_check_method(body: dict[str, Any], last_status: dict[str, Any],
                         job_dir: Path | None = None) -> dict[str, Any]:
    """把重分析请求解析为显式启动参数。

    - check_method 缺省或 "inherit"：严格继承上轮记录，不读系统默认开关；
    - check_method="ai"：本次显式使用大模型（本地规则 + AI），模型缺省为
      系统当前模型（调用方界面已向用户展示该选择）；
    - 无 check_method 但带显式检查键：兼容旧调用方，显式键优先，缺失键
      从上轮记录继承（仍不读系统默认开关）。
    """
    check_method = body.get("check_method")
    if check_method is not None and check_method not in {"inherit", "ai"}:
        raise InheritanceError("check_method 仅支持 inherit 或 ai", status_code=422)
    explicit_keys = [key for key in EXPLICIT_CHECK_KEYS if key in body]
    if check_method == "inherit" and explicit_keys:
        raise InheritanceError(
            "check_method=inherit 时不接受显式检查方式参数：" + "、".join(explicit_keys),
            status_code=422,
        )
    if check_method == "ai":
        conflicts = [key for key in explicit_keys if key != "ai_profile_id"]
        if conflicts:
            raise InheritanceError(
                "check_method=ai 时不接受矛盾的检查方式参数：" + "、".join(conflicts),
                status_code=422,
            )
    last = last_start_config(last_status)
    if check_method == "ai":
        return {"mode": "dual", "use_local_rules": True, "use_ai_assist": True,
                "ai_profile_id": body.get("ai_profile_id") or None,
                "check_method": "ai", "warnings": []}
    if check_method == "inherit" or not explicit_keys:
        resolved = _inherit_from_last(last)
        warnings: list[str] = []
        if resolved["use_ai_assist"] and job_dir is not None:
            model_state = evaluate_model_state(last, job_dir)
            if model_state["state"] == "changed" and model_state["message"]:
                warnings.append(model_state["message"])
        return {**resolved, "check_method": "inherit", "warnings": warnings}

    # 兼容路径：显式键覆盖，缺失键继承上轮；继承不可用时显式键必须自足。
    try:
        base = _inherit_from_last(last)
        base_error: InheritanceError | None = None
    except InheritanceError as exc:
        base, base_error = None, exc
    def pick(key: str) -> Any:
        if key not in body:
            return (base or {}).get(key)
        value = body[key]
        return _flag(value, key) if key in {"use_local_rules", "use_ai_assist"} else value
    mode = pick("mode")
    ai = pick("use_ai_assist")
    local = pick("use_local_rules")
    profile_id = body.get("ai_profile_id") or (base or {}).get("ai_profile_id")
    if mode is None:
        if ai is True:
            mode = "dual"
        elif ai is False:
            mode = "legacy"
        else:
            raise base_error or InheritanceError("缺少分析模式，请显式指定", status_code=422)
    if mode not in VALID_MODES:
        raise InheritanceError(f"无效的分析模式 '{mode}'", status_code=422)
    if mode in {"legacy", "structured"}:
        if ai is True:
            raise InheritanceError(
                f"mode='{mode}' 不运行大模型；请使用 check_method='ai' 或 mode='dual'",
                status_code=422,
            )
        ai = False
    if ai is None:
        raise InheritanceError(
            "上次是否启用大模型未记录，请显式指定 use_ai_assist 或 check_method",
            unknown_fields=["use_ai_assist"],
        )
    if local is None:
        local = True
    if ai and not profile_id:
        raise InheritanceError(
            "上次使用的模型未记录，请显式指定 ai_profile_id 或使用 check_method='ai'",
            unknown_fields=["ai_profile_id"],
        )
    return {"mode": mode, "use_local_rules": local, "use_ai_assist": ai,
            "ai_profile_id": profile_id if ai else None,
            "check_method": "explicit", "warnings": []}


def build_options_preview(job_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """单条材料重分析选项预览：上轮配置、执行状态、模型可用性与本次可选模型。"""
    from src.services.ai_model_settings import capabilities
    from src.services.check_progress import read_progress
    last = last_start_config(status)
    try:
        inherited = _inherit_from_last(last)
        inherit = {
            "available": True, "unknown_fields": [], "message": None,
            "mode": inherited["mode"], "use_local_rules": inherited["use_local_rules"],
            "use_ai_assist": inherited["use_ai_assist"],
            "ai_profile_id": inherited["ai_profile_id"],
            "ai_configuration": last["ai_configuration"],
        }
    except InheritanceError as exc:
        inherit = {
            "available": False, "unknown_fields": exc.unknown_fields, "message": str(exc),
            "mode": last["mode"], "use_local_rules": last["use_local_rules"],
            "use_ai_assist": last["use_ai_assist"], "ai_profile_id": last["ai_profile_id"],
            "ai_configuration": last["ai_configuration"],
        }
    progress = read_progress(job_dir)
    local_track = progress.get("local") if isinstance(progress.get("local"), dict) else {}
    ai_track = progress.get("ai") if isinstance(progress.get("ai"), dict) else {}
    return {
        "job_id": job_dir.name,
        "inherit": inherit,
        "last_execution": {
            "status": status.get("status"),
            "analysis_conclusion": status.get("analysis_conclusion"),
            "ai_requested": last["use_ai_assist"],
            "local_state": local_track.get("state"),
            "ai_state": ai_track.get("state"),
        },
        "model": evaluate_model_state(last, job_dir),
        "capabilities": capabilities(),
    }