"""
Structured Logging Configuration

Provides consistent logging format across the application with JSON output for production.
"""

import contextlib
import contextvars
import hashlib
import logging
import sys
import json
import os
from datetime import datetime
from typing import Any, Dict, Iterator, Mapping, Optional
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """JSON-structured log formatter for production environments."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        extras: Dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in ('name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'pathname', 'process', 'processName', 'relativeCreated',
                          'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
                          'message', 'taskName'):
                extras[key] = value
        # 兜底脱敏：即使调用点忘了走 redact_log_fields，敏感原值也不会被序列化出去
        log_data.update(redact_log_fields(extras))

        return json.dumps(log_data, ensure_ascii=False, default=str)


class SimpleFormatter(logging.Formatter):
    """Human-readable formatter for development."""
    
    def __init__(self):
        super().__init__(
            fmt='%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


# ---------------------------------------------------------------------------
# 敏感内容防泄漏（Task 10 硬要求：PDF 正文 / 证据原文 / 凭据一律不入普通日志）
#
# 处置方式不是"尽量别写"，而是在写入侧强制替换：命中敏感键时丢掉原值，
# 只保留长度与 sha256 前缀，既能排障对齐（同一段内容哈希一致），又拿不回原文。
# ---------------------------------------------------------------------------
SENSITIVE_LOG_KEYS = frozenset(
    {
        # 凭据类
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "new_password",
        "old_password",
        "secret",
        "session_secret",
        "session_token",
        "token",
        "x-api-key",
        # 材料原文 / 证据原文类
        "content",
        "evidence_text",
        "full_text",
        "page_text",
        "page_texts",
        "pdf_text",
        "prompt",
        "raw_text",
        "snippet",
        "text",
        "text_snippet",
    }
)

#: 只要键名以这些后缀结尾，也一律按敏感处理（覆盖 `xxx_password` / `xxx_token` 之类）
_SENSITIVE_KEY_SUFFIXES = ("_password", "_secret", "_token", "_api_key", "_text", "_prompt")

#: 脱敏后写入的哈希前缀长度，够用于比对同一段内容，又不足以反推原文
_HASH_PREFIX_LEN = 12


def is_sensitive_log_key(key: Any) -> bool:
    """判断字段名是否属于禁止写入日志原值的敏感字段。"""
    name = str(key or "").strip().lower()
    if not name:
        return False
    if name in SENSITIVE_LOG_KEYS:
        return True
    return name.endswith(_SENSITIVE_KEY_SUFFIXES)


def _fingerprint(value: Any) -> Dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return {"len": len(text), "sha256": digest[:_HASH_PREFIX_LEN]}


def fingerprint_for_log(value: Any) -> str:
    """把任意内容压成单行指纹 `len=…,sha256=…`。

    给"必须往 message 里写点什么来定位问题"的场景用（例如 AI 服务返回体格式错误）：
    指纹足以判断两次故障是不是同一份响应，但拿不回原文。
    """
    marker = _fingerprint(value)
    return f"len={marker['len']},sha256={marker['sha256']}"


def redact_log_fields(fields: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """返回脱敏后的日志字段字典：敏感键的原值被长度 + 哈希前缀取代。

    嵌套 dict 递归处理，list/tuple 里的 dict 同样处理，
    避免"把整条 finding 塞进 details"就把证据原文带出去。
    """
    if not isinstance(fields, Mapping):
        return {}

    result: Dict[str, Any] = {}
    for key, value in fields.items():
        if is_sensitive_log_key(key):
            marker = _fingerprint(value)
            result[f"{key}_len"] = marker["len"]
            result[f"{key}_sha256"] = marker["sha256"]
            continue
        if isinstance(value, Mapping):
            result[key] = redact_log_fields(value)
        elif isinstance(value, (list, tuple)):
            result[key] = [
                redact_log_fields(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def describe_exception(exc: BaseException) -> Dict[str, Any]:
    """把异常压缩成可安全写入日志的描述（Task C）。

    背景：`record.getMessage()` 不经过 `redact_log_fields`，所以
    `logger.warning(f"失败: {e}")` 会把异常消息原样落盘。而异常消息并不总是安全的——
    典型的是 pydantic `ValidationError`，它会把校验失败的**输入值**（可能是 PDF 正文
    片段）写进消息里。

    这里的取舍：
      - 异常消息原文降级为 `len + sha256` 指纹，够用来判断"是不是同一类错误"，
        但拿不回原文；
      - 校验类异常额外保留 `loc + type`（字段路径与错误类型，**不含值**），
        这是排障真正需要的信息；
      - 异常类名保留，作为可直接用于告警分组的"错误码"。
    """
    payload: Dict[str, Any] = {"error_type": type(exc).__name__}

    errors_accessor = getattr(exc, "errors", None)
    if callable(errors_accessor):
        try:
            entries = errors_accessor()
        except Exception:
            entries = None
        if isinstance(entries, (list, tuple)):
            locations = []
            for entry in list(entries)[:5]:
                if not isinstance(entry, Mapping):
                    continue
                loc = entry.get("loc")
                if isinstance(loc, (list, tuple)):
                    loc_text = ".".join(str(part) for part in loc)
                else:
                    loc_text = str(loc or "")
                locations.append({"loc": loc_text, "type": str(entry.get("type") or "")})
            if locations:
                payload["error_locations"] = locations

    marker = _fingerprint(str(exc))
    payload["error_message_len"] = marker["len"]
    payload["error_message_sha256"] = marker["sha256"]
    return payload


# ---------------------------------------------------------------------------
# 日志上下文（关联 job_id）
#
# 这里没有直接用下方的 `LogContext`：它通过 `logging.setLogRecordFactory` 装配
# 全局工厂，队列并发跑多个任务时会互相串字段（A 任务的 job_id 出现在 B 任务日志里）。
# 改用 contextvars：asyncio 任务与线程各自持有独立副本，天然隔离。
# ---------------------------------------------------------------------------
_LOG_CONTEXT: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "govbudget_log_context",
    default=None,
)

_context_factory_installed = False


def _context_fields() -> Dict[str, Any]:
    """返回当前上下文字段（未设置时为空 dict）。

    ContextVar 默认值用 None 而不是 {}：可变默认值会在所有上下文间共享同一对象。
    """
    return _LOG_CONTEXT.get() or {}


def current_log_context() -> Dict[str, Any]:
    """返回当前执行上下文中已绑定的日志字段（只读副本）。"""
    return dict(_context_fields())


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[Dict[str, Any]]:
    """在代码块内为所有日志记录附加结构化字段（值为 None 的字段忽略）。

    字段会自动脱敏，防止调用方顺手把证据原文绑进上下文。

    注意：块内如果用 `logger.xxx(extra=...)` 传了与上下文同名的键，
    `logging.makeRecord` 会抛 KeyError，请统一用 `safe_log_extra` 构造 extra。
    """
    merged = dict(_context_fields())
    merged.update(redact_log_fields({k: v for k, v in fields.items() if v is not None}))
    token = _LOG_CONTEXT.set(merged)
    try:
        yield dict(merged)
    finally:
        _LOG_CONTEXT.reset(token)


def install_log_context_factory() -> None:
    """安装 LogRecord 工厂，把当前上下文字段注入每条日志记录。

    幂等；在模块导入时即安装，这样即使调用方没有调用 `setup_logging`
    （例如 pytest 直接用 caplog），`job_id` 关联依然生效。
    """
    global _context_factory_installed
    if _context_factory_installed:
        return

    base_factory = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)
        for key, value in _context_fields().items():
            # 显式 extra 优先，不覆盖调用点传入的同名字段
            if not hasattr(record, key):
                setattr(record, key, value)
        return record

    logging.setLogRecordFactory(factory)
    _context_factory_installed = True


install_log_context_factory()


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    return text in {"1", "true", "yes", "on"}


def resolve_logging_options() -> Dict[str, Any]:
    """从环境变量解析日志配置。纯函数，便于测试。

    `LOG_JSON` 未显式配置时按输出端猜测：
      - stdout 不是 TTY（容器 / 被日志采集管道接走）-> JSON，便于聚合检索；
      - stdout 是 TTY（开发者本地终端）-> 人类可读格式。
    这样生产默认结构化，本地开发体验不变。
    """
    level = str(os.getenv("LOG_LEVEL", "INFO") or "INFO").strip().upper()
    if not hasattr(logging, level):
        level = "INFO"

    json_flag = _env_flag("LOG_JSON")
    if json_flag is None:
        try:
            json_format = not sys.stdout.isatty()
        except Exception:
            # 某些嵌入式环境的 stdout 没有 isatty，按生产默认走 JSON
            json_format = True
    else:
        json_format = json_flag

    log_file = str(os.getenv("LOG_FILE", "") or "").strip() or None
    return {"level": level, "json_format": json_format, "log_file": log_file}


_logging_configured = False


def configure_logging_from_env(
    component: str,
    *,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """应用/worker 启动处调用一次，按环境变量装配结构化日志。

    Args:
        component: 组件名（`api` / `worker`），写入首条日志便于区分来源。
        force: 忽略"测试环境跳过"与幂等保护，强制重新装配（仅测试使用）。

    Returns:
        实际生效的配置；被跳过时返回 None。

    默认在 `TESTING=true` 下跳过：`setup_logging` 会清空 root handler，
    而 pytest 的 caplog / logging 插件依赖自己的 handler，清掉会让测试丢日志。
    """
    global _logging_configured
    testing = str(os.getenv("TESTING", "")).strip().lower() in {"1", "true", "yes"}
    if not force:
        if _logging_configured or testing:
            return None

    options = resolve_logging_options()
    setup_logging(**options)
    _logging_configured = True
    get_logger("govbudget.bootstrap").info(
        "structured logging configured",
        extra={
            "component": component,
            "log_level": options["level"],
            "log_json": options["json_format"],
            "log_file": options["log_file"],
        },
    )
    return options


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[str] = None
) -> None:
    """
    Configure application logging.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON structured logging (for production)
        log_file: Optional file path for log output
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Select formatter
    formatter = StructuredFormatter() if json_format else SimpleFormatter()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding fields to log records."""
    
    def __init__(self, logger: logging.Logger, **fields):
        self.logger = logger
        self.fields = fields
        self.old_factory = None
    
    def __enter__(self):
        old_factory = logging.getLogRecordFactory()
        fields = self.fields
        
        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            for key, value in fields.items():
                setattr(record, key, value)
            return record
        
        self.old_factory = old_factory
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_factory:
            logging.setLogRecordFactory(self.old_factory)


# Quick-access logger for common use
def log_qc_execution(run_id: int, findings_count: int, duration_ms: float):
    """Log QC execution metrics."""
    logger = get_logger("qc.execution")
    logger.info(
        f"QC run {run_id} completed: {findings_count} findings in {duration_ms:.0f}ms",
        extra={"run_id": run_id, "findings_count": findings_count, "duration_ms": duration_ms}
    )


#: logging 保留字段，出现在 details 里会让 `logger.info(extra=...)` 直接抛 KeyError
_RESERVED_LOG_KEYS = frozenset(
    {"message", "asctime", "args", "msg", "levelname", "levelno", "name", "exc_info"}
)


def safe_log_extra(fields: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """构造可安全传给 `logger.xxx(extra=...)` 的字段字典。

    两个必须处理的坑：
      1. `logging.Logger.makeRecord` 会拒绝覆盖 LogRecord 上已有的属性并抛
         `KeyError`。上下文字段是由 LogRecordFactory 提前注入的，所以 extra 里
         出现同名键一定会炸——这里把同名键剔除（上下文值优先）；
         值不一致时改名为 `event_<key>` 保留，避免信息丢失。
      2. `message` / `asctime` 等保留字段同样会触发 KeyError，直接丢弃。

    字段一律先脱敏，防止证据原文或凭据被顺手写进日志。
    """
    context = _context_fields()
    result: Dict[str, Any] = {}
    for key, value in redact_log_fields(fields).items():
        if key in _RESERVED_LOG_KEYS:
            continue
        if key in context:
            if context[key] != value:
                result[f"event_{key}"] = value
            continue
        result[key] = value
    return result


def log_job_stage(
    job_id: Any,
    stage: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
    level: int = logging.INFO,
) -> None:
    """记录任务阶段流转。

    `job_id` 允许字符串：本系统的任务标识是上传目录名（uuid），不是自增整数。
    `details` 会先脱敏再写入，保证证据原文 / 凭据不会通过这里泄漏。
    """
    logger = get_logger("job.orchestrator")
    payload: Dict[str, Any] = {"job_id": job_id, "stage": stage, "job_status": status}
    payload.update(details or {})
    logger.log(
        level,
        "job %s: %s -> %s",
        job_id,
        stage,
        status,
        extra=safe_log_extra(payload),
    )


def log_parse_result(version_id: int, table_count: int, cell_count: int, errors: int):
    """Log PDF parsing result.

    参数刻意命名为 `*_count`：这里写进 message 的必须是**数量**，
    不能是抽取出来的表格/单元格内容本身（见 `scripts/check_log_message_safety.py`）。
    """
    logger = get_logger("parse.pdf")
    logger.info(
        f"Parsed version {version_id}: {table_count} tables, {cell_count} cells, {errors} errors",
        extra={
            "version_id": version_id,
            "tables": table_count,
            "cells": cell_count,
            "errors": errors,
        },
    )
