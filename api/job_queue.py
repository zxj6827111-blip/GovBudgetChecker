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
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

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
        # job_id -> ((mtime_ns, size), status, retry_not_before)：未变化的状态文件不重复解析。
        self._status_probe_cache: Dict[str, Tuple[Tuple[int, int, int], str, float]] = {}
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

    def _scan_resumable_jobs(self) -> List[Tuple[Path, float]]:
        """找出可续跑的任务目录与最早重试时间。

        冷启动用持久化索引读 status 和 retry_not_before，轮询再复用进程内缓存。
        历史任务首次读取完整状态并回填索引；报告与审查结果仍保存在原状态文件。
        """
        found: List[Tuple[Path, float]] = []
        seen: Set[str] = set()
        for job_dir in runtime.iter_job_dirs():
            seen.add(job_dir.name)
            status_file = job_dir / "status.json"
            try:
                signature = runtime.queue_status_signature(status_file.stat())
            except OSError:
                continue
            cached = self._status_probe_cache.get(job_dir.name)
            if cached is not None and cached[0] == signature:
                state, retry_not_before = cached[1], cached[2]
            else:
                indexed = runtime.read_queue_status_index(status_file, signature)
                if indexed is None:
                    status_data = runtime.read_json_file(status_file, default={})
                    try:
                        if runtime.queue_status_signature(status_file.stat()) != signature:
                            continue
                    except OSError:
                        continue
                    state = str(status_data.get("status") or "").lower()
                    retry_not_before = float(status_data.get("retry_not_before") or 0)
                    runtime.backfill_queue_status_index(status_file, signature, status_data)
                else:
                    state, retry_not_before = indexed
                self._status_probe_cache[job_dir.name] = (signature, state, retry_not_before)
            if state in _RESUMABLE_STATES:
                found.append((job_dir, retry_not_before))
        for job_id in set(self._status_probe_cache) - seen:
            del self._status_probe_cache[job_id]
        return found

    async def discover_pending_jobs(self, *, mark_requeued: bool = False) -> int:
        """Discover persisted jobs, including jobs submitted after worker startup."""
        from src.services.document_scope import dispatch_pending
        await dispatch_pending()
        from src.services.document_scope import dispatch_writing_pending
        await dispatch_writing_pending()
        resumed = 0
        # 读文件放到线程里，不占用事件循环。
        for job_dir, retry_not_before in await asyncio.to_thread(self._scan_resumable_jobs):
            if retry_not_before > time.time():
                continue

            if mark_requeued and not await asyncio.to_thread(self._mark_requeued, job_dir):
                continue
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

    def _mark_requeued(self, job_dir: Path) -> bool:
        if not job_dir.is_dir():
            return False
        try:
            with runtime.job_state_lock(job_dir):
                status_file = job_dir / "status.json"
                payload = runtime.read_json_file(status_file, default={})
                # 扫描之后任务可能已经被其他进程完成或延后，锁内重新确认才允许改写。
                if str(payload.get("status") or "").lower() not in _RESUMABLE_STATES:
                    return False
                now = time.time()
                if float(payload.get("retry_not_before") or 0) > now:
                    return False
                payload.update(
                    status="queued",
                    message="analysis task resumed after service restart",
                    ts=now,
                )
                runtime.write_json_file(status_file, payload)
                return True
        except Exception:
            logger.exception("Failed to rewrite resumed status for %s", job_dir.name)
            return False

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
            {
                "token": token,
                "worker_id": self._instance_id,
                "pid": os.getpid(),
                "ts": time.time(),
            },
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
                if age <= self._claim_ttl_seconds and not self._claim_owner_gone(claim_path):
                    return None
                try:
                    claim_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    return None
        return None

    def _claim_owner_gone(self, claim_path: Path) -> bool:
        """SIGKILL 后残留的僵尸锁：属主进程已退出就立即回收，不等 TTL。

        看门狗强杀后任务锁留在盘上，旧逻辑要干等 900 秒才过期，重启的队列
        对这批 queued 任务静默跳过，形成"干等 15 分钟→集中涌入→再次强杀"
        的死循环（2026-11-18 事故）。判定依据：锁里记录的属主 PID 已不存在。
        PID 复用等无法确认属主死亡的情形一律视为活着，回退 TTL 过期路径。
        """
        claim = runtime.read_json_file(claim_path, default={})
        raw_pid = claim.get("pid")
        if isinstance(raw_pid, int) and raw_pid > 0:
            pid: Optional[int] = raw_pid
        elif isinstance(raw_pid, str) and raw_pid.isdigit():
            pid = int(raw_pid)
        else:
            # 兼容未记录 pid 的旧锁：worker_id 形如 "PID-hex"，前缀就是属主 PID。
            worker_id = str(claim.get("worker_id") or "")
            head = worker_id.split("-", 1)[0]
            pid = int(head) if head.isdigit() else None
        if pid is None:
            return False
        if pid == os.getpid():
            # 本进程内其他 worker 持有（role=all 单进程部署）：活性由其协程保证。
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            logger.warning(
                "Reclaiming stale claim for job %s: owner pid %d no longer exists",
                claim_path.parent.name,
                pid,
            )
            return True
        except PermissionError:
            return False
        except OSError:
            return False
        return False

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
