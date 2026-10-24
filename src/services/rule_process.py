"""Run synchronous rule evaluation in a process that can be terminated on timeout."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
import threading
from multiprocessing.connection import Connection
from typing import Any, Dict, List, Optional


class RuleExecutionTimeout(TimeoutError):
    """Raised when rule evaluation exceeds its wall-clock deadline."""


class RuleExecutionError(RuntimeError):
    """Raised when the isolated rule process fails."""


def _rule_worker(
    connection: Connection,
    doc: Any,
    use_ai_assist: bool,
    report_kind: str,
    rules: Optional[List[Any]] = None,
) -> None:
    try:
        # Test-only hook: deterministically block the worker so timeout
        # handling can be verified regardless of host OS / start method.
        # Unset in production, so this is a no-op for real traffic.
        _delay = os.getenv("RULES_PROCESS_TEST_DELAY_SECONDS", "").strip()
        if _delay:
            try:
                time.sleep(float(_delay))
            except ValueError:
                pass

        from src.engine.pipeline import build_issues_payload

        payload = build_issues_payload(
            doc,
            use_ai_assist,
            report_kind=report_kind,
            rules=rules,
        )
        connection.send(("ok", payload))
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _run_rules_sync(
    doc: Any,
    use_ai_assist: bool,
    report_kind: str,
    timeout_seconds: float,
    rules: Optional[List[Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    return _evaluate_in_process(
        _rule_worker, (doc, use_ai_assist, report_kind, rules), timeout_seconds,
        cancel_event, name="govbudget-rule-evaluator",
    )


def _engine_rule_worker(connection: Connection, runner: Any, job_context: Any,
                        rules: List[Any], config: Any, generation: Any) -> None:
    try:
        # spawn 不继承 ContextVar；传递代际以避免已取消的旧任务更新新任务进度。
        from src.services.check_progress import _generation

        token = _generation.set(generation)
        try:
            findings = asyncio.run(runner.run_rules(job_context, rules, config))
            connection.send(("ok", {"findings": findings, "stats": runner.get_stats(),
                                    "outcomes": runner._outcomes}))
        finally:
            _generation.reset(token)
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _evaluate_in_process(target: Any, args: tuple, timeout_seconds: float,
                         cancel_event: Optional[threading.Event], *, name: str) -> Dict[str, Any]:
    start_method = os.getenv("RULES_PROCESS_START_METHOD", "").strip()
    if not start_method:
        start_method = "spawn" if os.name == "nt" else "fork"
    context = multiprocessing.get_context(start_method)
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=target,
        args=(child_connection, *args),
        name=name,
        daemon=True,
    )
    process.start()
    child_connection.close()
    deadline = time.monotonic() + max(0.01, float(timeout_seconds))

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuleExecutionError("rule_execution_cancelled")
            remaining = max(0.0, deadline - time.monotonic())
            if parent_connection.poll(min(.05, remaining)):
                break
            if remaining <= 0:
                raise RuleExecutionTimeout(f"rule evaluation exceeded {timeout_seconds:g} seconds")

        status, payload = parent_connection.recv()
        process.join(timeout=5)
        if status != "ok":
            raise RuleExecutionError(str(payload))
        if not isinstance(payload, dict):
            raise RuleExecutionError("rule evaluator returned a non-object payload")
        return payload
    except EOFError as exc:
        raise RuleExecutionError(
            f"rule evaluator exited without a result (exitcode={process.exitcode})"
        ) from exc
    finally:
        parent_connection.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2)
        process.join()


def _run_engine_rules_sync(runner: Any, job_context: Any, rules: List[Any], config: Any,
                           timeout_seconds: float, generation: Any,
                           cancel_event: threading.Event) -> Dict[str, Any]:
    return _evaluate_in_process(
        _engine_rule_worker, (runner, job_context, rules, config, generation),
        timeout_seconds, cancel_event, name="govbudget-engine-rule-evaluator",
    )


async def run_rules_in_process(
    doc: Any,
    use_ai_assist: bool = False,
    report_kind: str = "auto",
    timeout_seconds: float = 60.0,
    rules: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    return await _await_rule_worker(
        _run_rules_sync, doc, use_ai_assist, report_kind, timeout_seconds, rules,
    )


async def run_engine_rules_in_process(runner: Any, job_context: Any, rules: List[Any],
                                      config: Any, timeout_seconds: float) -> List[Any]:
    from src.services.check_progress import _generation

    payload = await _await_rule_worker(
        _run_engine_rules_sync, runner, job_context, rules, config,
        timeout_seconds, _generation.get(),
    )
    # 子进程独占本轮统计；退出并收齐结果后才在父进程发布，取消不留下半成品。
    runner._stats = payload["stats"]
    runner._outcomes = payload["outcomes"]
    return payload["findings"]


async def _await_rule_worker(function: Any, *args: Any) -> Dict[str, Any]:
    cancel_event = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        function, *args, cancel_event,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_event.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
