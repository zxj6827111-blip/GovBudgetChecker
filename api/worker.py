"""Standalone analysis worker process.

Run with:
    python -m api.worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from dotenv import load_dotenv

load_dotenv()

from api import runtime
from api.job_queue import DurableJobQueue
from api.main import _run_pipeline
from api import queue_runtime

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    role = queue_runtime.get_queue_role()
    if role == "api":
        raise RuntimeError(
            "JOB_QUEUE_ROLE=api disables local workers. "
            "Set JOB_QUEUE_ROLE=worker or JOB_QUEUE_ROLE=all for worker process."
        )
    if not queue_runtime.queue_enabled():
        raise RuntimeError(
            "JOB_QUEUE_ENABLED=false. Worker process has no queue to consume."
        )

    runtime.set_pipeline_runner(_run_pipeline)
    runner = runtime.get_pipeline_runner()
    if runner is None:
        raise RuntimeError("Pipeline runner is not configured")

    max_workers, ai_sequential_mode = queue_runtime.compute_queue_workers()
    if ai_sequential_mode and max_workers < 10:
        logger.warning(
            "AI_SEQUENTIAL_MODE is enabled but JOB_QUEUE_WORKERS=%d; "
            "batch local-stage throughput may be limited (recommend >=10).",
            max_workers,
        )

    queue = DurableJobQueue(
        runner,
        max_workers=max_workers,
        resume_on_start=queue_runtime.queue_resume_on_start(),
    )
    await queue.start()
    runtime.set_job_queue(queue)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows ProactorEventLoop doesn't support add_signal_handler.
            pass

    logger.info(
        "GovBudget worker started (role=%s, workers=%d, resume_on_start=%s)",
        role,
        max_workers,
        queue_runtime.queue_resume_on_start(),
    )
    try:
        await stop_event.wait()
    finally:
        await queue.stop()
        runtime.set_job_queue(None)
        logger.info("GovBudget worker stopped")


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    )
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.error("Worker exited with error: %s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
