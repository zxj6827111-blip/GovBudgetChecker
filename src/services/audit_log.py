from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


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
