"""Two independent progress tracks with atomic updates across API/worker threads."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from src.services.ai_model_settings import _file_lock

FILENAME = "check_progress.json"
TERMINAL = {"completed", "failed", "degraded", "skipped", "cancelled"}
_generation: ContextVar[tuple[str, str] | None] = ContextVar("check_progress_generation", default=None)


@contextmanager
def progress_scope(job_id: str):
    generation = read_progress(job_dir(job_id)).get("generation")
    token = _generation.set((job_id, generation) if generation else None)
    try:
        yield
    finally:
        _generation.reset(token)


def job_dir(job_id: str) -> Path:
    if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
        raise ValueError("invalid job id")
    return Path(os.getenv("UPLOAD_DIR", "uploads")) / job_id


def read_progress(directory: Path) -> dict:
    try:
        result = json.loads((directory / FILENAME).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def update_progress(job_id: str, track: str, **fields) -> None:
    if track not in {"local", "ai"}:
        raise ValueError("invalid progress track")
    directory = job_dir(job_id)
    # Service-level callers used without a real upload must not create ghost jobs.
    if not directory.exists():
        return
    path = directory / FILENAME
    with _file_lock(path):
        data = read_progress(directory)
        generation = _generation.get()
        if generation and generation[0] == job_id and generation[1] != data.get("generation"):
            return
        data.setdefault("generation", str(uuid.uuid4()))
        current = dict(data.get(track) or {})
        if current.get("state") in TERMINAL:
            return
        current.update(fields)
        current["updated_at"] = time.time()
        if current.get("state") == "running":
            current.setdefault("started_at", time.time())
        if current.get("state") in TERMINAL:
            current["finished_at"] = time.time()
        total, completed = current.get("total"), current.get("completed", 0)
        current["percent"] = round(min(100, max(0, completed / total * 100))) if isinstance(total, int) and total > 0 else None
        data[track] = current
        fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".check-progress-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def initialize_progress(job_id: str, local: bool, ai: bool, model: str | None = None) -> None:
    directory = job_dir(job_id)
    (directory / FILENAME).unlink(missing_ok=True)
    update_progress(job_id, "local", state="waiting" if local else "skipped", completed=0, total=None, current_step="等待文档解析" if local else "未启用", unit="条规则")
    update_progress(job_id, "ai", state="waiting" if ai else "skipped", completed=0, total=None, current_step="等待文档解析" if ai else "未启用", unit="段文本", model=model)


def finish_incomplete(job_id: str, state: str = "failed") -> None:
    for track, progress in read_progress(job_dir(job_id)).items():
        if track not in {"local", "ai"} or not isinstance(progress, dict):
            continue
        if progress.get("state") not in TERMINAL:
            update_progress(job_id, track, state=state, current_step="任务已取消" if state == "cancelled" else "任务中断，检查未完成")
