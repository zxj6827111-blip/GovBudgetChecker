from __future__ import annotations

import asyncio
import time

import pytest

from src.services import issue_workflow_store


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, revision: int):
        self.revision = revision
        self.calls = []

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if "UPDATE workflow_state_mirror" in sql:
            self.revision = int(args[0])

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "workflow_state_mirror" in sql:
            return self.revision
        return None

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


@pytest.mark.asyncio
async def test_persist_workflow_state_replaces_snapshot_and_advances_revision(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    connection = _Connection(revision=1)
    released = []

    async def _ready():
        return True

    async def _acquire():
        return connection

    async def _release(conn):
        released.append(conn)

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "release", _release)

    ok = await issue_workflow_store.persist_workflow_state(
        {
            "revision": 2,
            "updated_at": "2026-07-13T08:00:00Z",
            "issues": {
                "job::issue": {
                    "job_id": "job",
                    "issue_id": "issue",
                    "status": "confirmed",
                }
            },
            "packages": [],
        }
    )

    assert ok is True
    assert connection.revision == 2
    assert any("DELETE FROM workflow_issue_records" in sql for sql, _ in connection.calls)
    assert any("UPDATE workflow_state_mirror" in sql for sql, _ in connection.calls)
    assert released == [connection]


@pytest.mark.asyncio
async def test_persist_workflow_state_ignores_an_older_snapshot(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    connection = _Connection(revision=5)

    async def _ready():
        return True

    async def _acquire():
        return connection

    async def _release(conn):
        assert conn is connection

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "release", _release)

    ok = await issue_workflow_store.persist_workflow_state({"revision": 4, "issues": {}, "packages": []})

    assert ok is True
    assert not any("DELETE FROM workflow_issue_records" in sql for sql, _ in connection.calls)


@pytest.mark.asyncio
async def test_persist_workflow_state_mirrors_legacy_zero_revision(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    connection = _Connection(revision=0)

    async def _ready():
        return True

    async def _acquire():
        return connection

    async def _release(_conn):
        return None

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "release", _release)

    ok = await issue_workflow_store.persist_workflow_state(
        {"issues": {"job::issue": {"job_id": "job", "issue_id": "issue"}}, "packages": []}
    )

    assert ok is True
    assert connection.revision == 1
    assert any("DELETE FROM workflow_issue_records" in sql for sql, _ in connection.calls)


@pytest.mark.asyncio
async def test_persist_workflow_state_records_retry_signal_when_database_is_unavailable(monkeypatch, tmp_path):
    from api import runtime

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    ok = await issue_workflow_store.persist_workflow_state({"revision": 1, "issues": {}, "packages": []})

    assert ok is False
    persistence = runtime.read_json_file(tmp_path / ".issue_workflow_persistence.json", default={})
    assert persistence["status"] == "disabled"


@pytest.mark.asyncio
async def test_persist_workflow_state_resyncs_when_mirror_revision_collides_with_recreated_file(monkeypatch):
    """A recreated local workflow file restarts its revision at 1. If the DB
    mirror also holds revision 1, the older mirror must not suppress a newer
    local snapshot; content comparison triggers a resync."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    connection = _Connection(revision=1)

    async def _ready():
        return True

    async def _acquire():
        return connection

    async def _release(_conn):
        return None

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "release", _release)

    ok = await issue_workflow_store.persist_workflow_state(
        {
            "revision": 1,
            "updated_at": "2026-08-10T04:55:53Z",
            "issues": {
                "job::issue-2": {
                    "job_id": "job",
                    "issue_id": "issue-2",
                    "status": "confirmed",
                }
            },
            "packages": [],
        }
    )

    assert ok is True
    assert any("DELETE FROM workflow_issue_records" in sql for sql, _ in connection.calls)
    assert any("UPDATE workflow_state_mirror" in sql for sql, _ in connection.calls)
    assert connection.revision == 1


@pytest.mark.asyncio
async def test_persist_workflow_state_skips_when_revision_covers_identical_content(monkeypatch):
    """Same revision plus identical mirrored rows is a no-op and keeps the mirror."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    connection = _Connection(revision=1)

    async def _ready():
        return True

    async def _acquire():
        return connection

    async def _release(_conn):
        return None

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "release", _release)

    ok = await issue_workflow_store.persist_workflow_state(
        {"revision": 1, "updated_at": "2026-08-10T04:55:53Z", "issues": {}, "packages": []}
    )

    assert ok is True
    assert not any("DELETE FROM workflow_issue_records" in sql for sql, _ in connection.calls)


@pytest.mark.asyncio
async def test_persist_workflow_state_never_downgrades_a_newer_mirror(monkeypatch):
    """A strictly newer mirror must survive an older snapshot, even when the
    row content differs (e.g. a snapshot captured before a concurrent update
    landed). The older snapshot is skipped without touching the mirror."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    connection = _Connection(revision=2)

    async def _ready():
        return True

    async def _acquire():
        return connection

    async def _release(_conn):
        return None

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "acquire", _acquire)
    monkeypatch.setattr(issue_workflow_store.DatabaseConnection, "release", _release)

    ok = await issue_workflow_store.persist_workflow_state(
        {
            "revision": 1,
            "updated_at": "2026-08-10T04:55:53Z",
            "issues": {
                "job::issue-old": {
                    "job_id": "job",
                    "issue_id": "issue-old",
                    "status": "confirmed",
                }
            },
            "packages": [],
        }
    )

    assert ok is True
    assert not any("DELETE FROM workflow_issue_records" in sql for sql, _ in connection.calls)
    assert not any("UPDATE workflow_state_mirror" in sql for sql, _ in connection.calls)
    assert connection.revision == 2


@pytest.mark.asyncio
async def test_persist_workflow_state_bounds_database_mirror_wait(monkeypatch, tmp_path):
    """A slow or unreachable PostgreSQL must not stall the workflow endpoint:
    the mirror gives up after a short bounded wait, keeps the local file state
    authoritative and leaves a retry signal for the recovery path."""
    from api import runtime

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(issue_workflow_store, "_WORKFLOW_DB_TIMEOUT_SECONDS", 0.05)

    async def _ready():
        return True

    async def _stuck_mirror(_state):
        await asyncio.sleep(30)

    monkeypatch.setattr(issue_workflow_store, "ensure_analysis_persistence_ready", _ready)
    monkeypatch.setattr(issue_workflow_store, "_mirror_workflow_state", _stuck_mirror)

    started = time.monotonic()
    ok = await issue_workflow_store.persist_workflow_state(
        {"revision": 1, "issues": {}, "packages": []}
    )
    elapsed = time.monotonic() - started

    assert ok is False
    assert elapsed < 5, f"mirror wait was not bounded: {elapsed:.2f}s"
    persistence = runtime.read_json_file(tmp_path / ".issue_workflow_persistence.json", default={})
    assert persistence["status"] == "pending_retry"
    assert persistence["error"] == "database mirror timed out"
