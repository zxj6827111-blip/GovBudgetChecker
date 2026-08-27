"""Durable async job queue for analysis pipeline execution.

This queue keeps job dispatch out of request handlers and supports restart
resume by scanning persisted job status files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Set

from api import runtime
from src.utils.logging_config import log_context, safe_log_extra

logger = logging.getLogger(__name__)

_RESUMABLE_STATES = {"queued", "processing", "running"}
_CLAIM_FILENAME = ".worker-claim.json"


class DurableJobQueue:
    """Simple durable queue backed by persisted status files."""

    def __init__(
        self,
        runner: Callable[[Path], Awaitable[None]],
        *,
        max_workers: int = 2,
        resume_on_start: bool = True,
        poll_interval_seconds: Optional[float] = None,
        claim_ttl_seconds: Optional[float] = None,
    ) -> None:
        self._runner = runner
        self._max_workers = max(1, int(max_workers))
        self._resume_on_start = resume_on_start
        self._poll_interval_seconds = max(
            0.05,
            float(
                poll_interval_seconds
                if poll_interval_seconds is not None
                else os.getenv("JOB_QUEUE_POLL_INTERVAL_SECONDS", "2")
            ),
        )
        self._claim_ttl_seconds = max(
            30.0,
            float(
                claim_ttl_seconds
                if claim_ttl_seconds is not None
                else os.getenv("JOB_QUEUE_CLAIM_TTL_SECONDS", "900")
            ),
        )
        self._instance_id = f"{os.getpid()}-{secrets.token_hex(8)}"
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._poller: Optional[asyncio.Task[None]] = None
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
        self._poller = asyncio.create_task(
            self._poll_loop(), name="govbudget-job-discovery"
        )

    async def stop(self) -> None:
        """Stop workers gracefully."""
        if not self._started:
            return
        if self._poller is not None:
            self._poller.cancel()
            await asyncio.gather(self._poller, return_exceptions=True)
            self._poller = None
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
        return await self.discover_pending_jobs(mark_requeued=True)

    async def discover_pending_jobs(self, *, mark_requeued: bool = False) -> int:
        """Discover persisted jobs, including jobs submitted after worker startup."""
        resumed = 0
        for job_dir in runtime.iter_job_dirs():
            status_file = job_dir / "status.json"
            status_data = runtime.read_json_file(status_file, default={})
            state = str(status_data.get("status") or "").lower()
            if state not in _RESUMABLE_STATES:
                continue

            if mark_requeued:
                self._mark_requeued(job_dir, status_data)
            await self.enqueue(job_dir.name)
            resumed += 1

        if resumed:
            logger.info("Resumed %d pending jobs from persisted state", resumed)
        return resumed

    async def _poll_loop(self) -> None:
        while True:
            try:
                self._write_worker_heartbeat()
                await self.discover_pending_jobs()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to poll persisted job queue")
            await asyncio.sleep(self._poll_interval_seconds)

    def _write_worker_heartbeat(self) -> None:
        heartbeat_dir = runtime.UPLOAD_ROOT / ".worker-heartbeats"
        heartbeat_dir.mkdir(parents=True, exist_ok=True)
        runtime.write_json_file(
            heartbeat_dir / f"{self._instance_id}.json",
            {
                "worker_id": self._instance_id,
                "pid": os.getpid(),
                "ts": time.time(),
                "active_jobs": len(self._active),
                "queued_jobs": len(self._enqueued),
            },
        )

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
            runtime.write_json_file(job_dir / "status.json", payload)
        except Exception:
            logger.exception("Failed to rewrite resumed status for %s", job_dir.name)

    async def _worker_loop(self, worker_idx: int) -> None:
        while True:
            job_id = await self._queue.get()
            self._enqueued.discard(job_id)
            self._active.add(job_id)
            try:
                # 队列侧日志（认领、跳过、执行失败）同样要能按 job_id 检索
                with log_context(job_id=job_id, worker_index=worker_idx):
                    await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Worker %d failed while executing job %s",
                    worker_idx,
                    job_id,
                    extra=safe_log_extra(
                        {"job_id": job_id, "stage": "queue_worker_error"}
                    ),
                )
            finally:
                self._active.discard(job_id)
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job_dir = runtime.UPLOAD_ROOT / job_id
        if not job_dir.exists():
            logger.warning("Queued job %s no longer exists; skipping", job_id)
            return
        claim_token = self._acquire_claim(job_dir)
        if claim_token is None:
            logger.debug("Job %s is already claimed by another worker", job_id)
            return

        heartbeat = asyncio.create_task(
            self._claim_heartbeat(job_dir, claim_token),
            name=f"govbudget-claim-heartbeat-{job_id}",
        )
        try:
            status_data = runtime.read_json_file(job_dir / "status.json", default={})
            if str(status_data.get("status") or "").lower() not in _RESUMABLE_STATES:
                return
            await self._runner(job_dir)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._release_claim(job_dir, claim_token)

    def _acquire_claim(self, job_dir: Path) -> Optional[str]:
        claim_path = job_dir / _CLAIM_FILENAME
        token = f"{self._instance_id}-{secrets.token_hex(8)}"
        payload = json.dumps(
            {"token": token, "worker_id": self._instance_id, "ts": time.time()},
            ensure_ascii=False,
        )
        for _ in range(2):
            try:
                with claim_path.open("x", encoding="utf-8") as claim_file:
                    claim_file.write(payload)
                return token
            except FileExistsError:
                try:
                    age = time.time() - claim_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age <= self._claim_ttl_seconds:
                    return None
                try:
                    claim_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    return None
        return None

    async def _claim_heartbeat(self, job_dir: Path, token: str) -> None:
        interval = max(5.0, min(30.0, self._claim_ttl_seconds / 3.0))
        claim_path = job_dir / _CLAIM_FILENAME
        while True:
            await asyncio.sleep(interval)
            try:
                current = runtime.read_json_file(claim_path, default={})
                if current.get("token") != token:
                    return
                os.utime(claim_path, None)
            except FileNotFoundError:
                return
            except OSError:
                logger.warning("Failed to refresh claim for job %s", job_dir.name)

    @staticmethod
    def _release_claim(job_dir: Path, token: str) -> None:
        claim_path = job_dir / _CLAIM_FILENAME
        try:
            current = runtime.read_json_file(claim_path, default={})
            if current.get("token") == token:
                claim_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to release claim for job %s", job_dir.name)
