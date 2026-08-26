"""Run synchronous rule evaluation in a process that can be terminated on timeout."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from multiprocessing.connection import Connection
from typing import Any, Dict


class RuleExecutionTimeout(TimeoutError):
    """Raised when rule evaluation exceeds its wall-clock deadline."""


class RuleExecutionError(RuntimeError):
    """Raised when the isolated rule process fails."""


def _rule_worker(
    connection: Connection,
    doc: Any,
    use_ai_assist: bool,
    report_kind: str,
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
) -> Dict[str, Any]:
    start_method = os.getenv("RULES_PROCESS_START_METHOD", "").strip()
    if not start_method:
        start_method = "spawn" if os.name == "nt" else "fork"
    context = multiprocessing.get_context(start_method)
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_rule_worker,
        args=(child_connection, doc, use_ai_assist, report_kind),
        name="govbudget-rule-evaluator",
        daemon=True,
    )
    process.start()
    child_connection.close()
    deadline = time.monotonic() + max(0.01, float(timeout_seconds))

    try:
        remaining = max(0.0, deadline - time.monotonic())
        if not parent_connection.poll(remaining):
            process.terminate()
            process.join(timeout=5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2)
            raise RuleExecutionTimeout(
                f"rule evaluation exceeded {timeout_seconds:g} seconds"
            )

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


async def run_rules_in_process(
    doc: Any,
    use_ai_assist: bool,
    report_kind: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _run_rules_sync,
        doc,
        use_ai_assist,
        report_kind,
        timeout_seconds,
    )
