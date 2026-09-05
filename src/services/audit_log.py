from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_audit_log_path() -> Path:
    raw = (os.getenv("AUDIT_LOG_PATH") or "data/audit/admin-actions.jsonl").strip()
    return Path(raw)


def ensure_audit_log_parent() -> Path:
    path = get_audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_audit_event(
    *,
    action: str,
    actor: str,
    result: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    path = ensure_audit_log_parent()
    payload = {
        "ts": time.time(),
        "action": action,
        "actor": actor,
        "result": result,
        "resource_type": resource_type,
        "resource_id": resource_id or "",
        "resource_name": resource_name or "",
        "details": details or {},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


#: 单次扫描审计日志文件的最大行数上限，防止文件异常膨胀后拖垮活动流接口
#: （与 `src/services/metrics.py` 的 `METRICS_MAX_JOBS` 是同一种"扫描量上限"取舍）。
_DEFAULT_MAX_SCAN_LINES = 20000


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def read_audit_events(
    *,
    limit: int = 20,
    offset: int = 0,
    max_scan_lines: Optional[int] = None,
) -> Dict[str, Any]:
    """只读分页读取审计事件，最新的事件排在最前面（倒序）。

    实现取舍：审计日志是按时间顺序 append 的 JSONL，"最新事件在前"要求倒序遍历。
    本函数不引入索引文件或数据库，只是读入后在内存里反转排序——这与
    `src/services/metrics.py` 扫描全部任务目录的取舍一致（管理员操作日志的规模级别，
    不是高频事件流，没有必要为此新增存储或索引结构）。

    `max_scan_lines` 限制单次最多解析多少行（从文件末尾往前数），避免文件被
    异常写爆后一次性把全部内容读进内存；超出该行数的更早历史不参与分页，
    这是有意的截断而非 bug（活动流本来就只需要"最近若干条"）。

    返回的每条记录**不做脱敏**——脱敏留给调用方（API 路由层）用
    `src.utils.logging_config.redact_log_fields` 处理 `details` 字段，
    保持"读取"与"脱敏"两个职责分离：这个函数只负责如实还原文件里写了什么，
    调用方决定要不要脱敏、脱敏到什么程度（不同调用场景——比如内部排障工具——
    可能需要不同的脱敏策略，写死在读取函数里会限制未来的复用）。
    """
    path = get_audit_log_path()
    scan_limit = max_scan_lines if max_scan_lines is not None else _env_int(
        "AUDIT_LOG_MAX_SCAN_LINES", _DEFAULT_MAX_SCAN_LINES
    )

    if not path.is_file():
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    # 只保留文件末尾的 scan_limit 行参与解析（最新事件在文件末尾）。
    scanned_lines = raw_lines[-scan_limit:] if scan_limit > 0 else raw_lines

    events: List[Dict[str, Any]] = []
    for line in scanned_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except (ValueError, TypeError):
            # 单行损坏（例如写入过程中被截断）不应该让整个活动流接口报错，
            # 跳过该行即可——这是"只读展示"场景下合理的容错，不是静默吞掉业务错误。
            continue
        if isinstance(record, dict):
            events.append(record)

    # 最新的事件排在最前面：文件是追加写入的，末尾即最新，反转后从末尾开始。
    events.reverse()

    total = len(events)
    if limit <= 0:
        page = events[offset:]
    else:
        page = events[offset : offset + limit]

    return {"items": page, "total": total, "limit": limit, "offset": offset}
