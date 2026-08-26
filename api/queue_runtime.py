"""Queue runtime policy helpers.

This module centralizes environment-driven queue behavior so API server and
worker process can share one consistent policy.
"""

from __future__ import annotations

import logging
import os
import json
import time
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

_VALID_QUEUE_ROLES = {"all", "api", "worker"}


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def get_queue_role(default: str = "all") -> str:
    role = (os.getenv("JOB_QUEUE_ROLE") or default).strip().lower()
    if role not in _VALID_QUEUE_ROLES:
        logger.warning(
            "Invalid JOB_QUEUE_ROLE=%s, fallback to %s", role or "<empty>", default
        )
        return default
    return role


def queue_enabled(testing_mode: bool | None = None) -> bool:
    if testing_mode is None:
        testing_mode = env_flag("TESTING", False)
    return env_flag("JOB_QUEUE_ENABLED", not testing_mode)


def allow_inline_fallback() -> bool:
    return env_flag("JOB_QUEUE_INLINE_FALLBACK", env_flag("TESTING", False))


def queue_resume_on_start() -> bool:
    return env_flag("JOB_QUEUE_RESUME_ON_START", True)


def has_recent_worker_heartbeat(upload_root: Path) -> bool:
    """Return whether a worker sharing this upload root has reported recently."""
    heartbeat_dir = upload_root / ".worker-heartbeats"
    try:
        max_age = max(5.0, float(os.getenv("JOB_QUEUE_HEARTBEAT_MAX_AGE_SECONDS", "15")))
    except (TypeError, ValueError):
        max_age = 15.0

    now = time.time()
    try:
        candidates = heartbeat_dir.glob("*.json")
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                heartbeat_ts = float(payload.get("ts") or path.stat().st_mtime)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if now - heartbeat_ts <= max_age:
                return True
    except OSError:
        return False
    return False


def should_start_local_queue(testing_mode: bool | None = None) -> bool:
    if not queue_enabled(testing_mode):
        return False
    return get_queue_role() in {"all", "worker"}


def should_enqueue_only() -> bool:
    if not queue_enabled():
        return False
    if get_queue_role() == "api":
        return True
    return not allow_inline_fallback()


def compute_queue_workers() -> Tuple[int, bool]:
    ai_sequential_mode = env_flag("AI_SEQUENTIAL_MODE", True)
    default_workers = 2
    if ai_sequential_mode:
        cpu_count = os.cpu_count() or 4
        default_workers = max(10, min(32, cpu_count * 2))

    workers_raw = os.getenv("JOB_QUEUE_WORKERS")
    try:
        max_workers = int(workers_raw) if workers_raw is not None else default_workers
    except Exception:
        max_workers = default_workers
    max_workers = max(1, max_workers)

    return max_workers, ai_sequential_mode

