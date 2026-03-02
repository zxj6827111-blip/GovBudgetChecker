"""Durable async job queue for analysis pipeline execution.

This queue keeps job dispatch out of request handlers and supports restart
resume by scanning persisted job status files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Set

from api import runtime

logger = logging.getLogger(__name__)

_RESUMABLE_STATES = {"queued", "processing", "running"}


class DurableJobQueue:
    """Simple durable queue backed by persisted status files."""

    def __init__(
        self,
        runner: Callable[[Path], Awaitable[None]],
        *,
        max_workers: int = 2,
        resume_on_start: bool = True,
    ) -> None:
        self._runner = runner
        self._max_workers = max(1, int(max_workers))
        self._resume_on_start = resume_on_start
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._enqueued: Set[str] = set()
        self._active: Set[str] = set()
        self._started = False

    async def start(self) -> None:
        """Start worker tasks and optionally resume pending jobs."""
        if self._started:
            return
        self._started = True
        for idx in range(self._max_workers):
            task = asyncio.create_task(
                self._worker_loop(idx), name=f"govbudget-job-worker-{idx}"
            )
            self._workers.append(task)
        logger.info("Job queue started with %d workers", self._max_workers)

        if self._resume_on_start:
            await self.resume_pending_jobs()

    async def stop(self) -> None:
        """Stop workers gracefully."""
        if not self._started:
            return
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._active.clear()
        self._enqueued.clear()
        self._started = False
        logger.info("Job queue stopped")

    async def enqueue(self, job_id: str) -> None:
        """Enqueue a job once."""
        if not self._started:
            raise RuntimeError("Job queue is not started")
        if not job_id:
            raise ValueError("job_id is required")
        if job_id in self._enqueued or job_id in self._active:
            return
        self._enqueued.add(job_id)
        await self._queue.put(job_id)

    async def resume_pending_jobs(self) -> int:
        """Requeue pending jobs after process restart."""
        resumed = 0
        for job_dir in runtime.iter_job_dirs():
            status_file = job_dir / "status.json"
            status_data = runtime.read_json_file(status_file, default={})
            state = str(status_data.get("status") or "").lower()
            if state not in _RESUMABLE_STATES:
                continue

            self._mark_requeued(job_dir, status_data)
            await self.enqueue(job_dir.name)
            resumed += 1

        if resumed:
            logger.info("Resumed %d pending jobs from persisted state", resumed)
        return resumed

    def _mark_requeued(self, job_dir: Path, status_data: Dict[str, object]) -> None:
        payload = dict(status_data)
        payload.update(
            {
                "status": "queued",
                "message": "analysis task resumed after service restart",
                "ts": time.time(),
            }
        )
        try:
            (job_dir / "status.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to rewrite resumed status for %s", job_dir.name)

    async def _worker_loop(self, worker_idx: int) -> None:
        while True:
            job_id = await self._queue.get()
            self._enqueued.discard(job_id)
            self._active.add(job_id)
            try:
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Worker %d failed while executing job %s", worker_idx, job_id
                )
            finally:
                self._active.discard(job_id)
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job_dir = runtime.UPLOAD_ROOT / job_id
        if not job_dir.exists():
            logger.warning("Queued job %s no longer exists; skipping", job_id)
            return
        await self._runner(job_dir)
