"""Services module for business logic.

Keep heavy dependencies lazy so utility scripts can import lightweight service
modules (for example `org_storage`) without requiring database-only packages
such as `asyncpg`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["JobOrchestrator"]


def __getattr__(name: str) -> Any:
    if name == "JobOrchestrator":
        from src.services.job_orchestrator import JobOrchestrator

        return JobOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
