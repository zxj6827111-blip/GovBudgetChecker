import json

import pytest

import src.services.analysis_result_store as analysis_result_store


class _FakeTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.fetchval_calls = []
        self.execute_calls = []
        self.fetchrow_calls = []

    def transaction(self):
        return _FakeTransaction()

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        if "SELECT id FROM organizations" in query:
            return None
        return 17

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "OK"

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return None


@pytest.mark.asyncio
async def test_persist_analysis_job_snapshot_records_dual_results(monkeypatch):
    conn = _FakeConnection()
    released = []

    async def _fake_ready():
        return True

    async def _fake_acquire():
        return conn

    async def _fake_release(connection):
        released.append(connection)

    monkeypatch.setattr(
        analysis_result_store,
        "ensure_analysis_persistence_ready",
        _fake_ready,
    )
    monkeypatch.setattr(
        analysis_result_store.DatabaseConnection,
        "acquire",
        _fake_acquire,
    )
    monkeypatch.setattr(
        analysis_result_store.DatabaseConnection,
        "release",
        _fake_release,
    )

    payload = {
        "job_id": "job-dual-1",
        "status": "done",
        "filename": "sample.pdf",
        "checksum": "abc123",
        "mode": "dual",
        "organization_id": "org-1",
        "organization_name": "Test Org",
        "ts": 1773545794.0239053,
        "result": {
            "ai_findings": [{"id": "ai-1", "source": "ai"}],
            "rule_findings": [{"id": "rule-1", "source": "rule"}],
            "merged": {"totals": {"merged": 2}},
            "meta": {
                "started_at": 1773545730.395834,
                "finished_at": 1773545784.2885084,
            },
        },
    }

    ok = await analysis_result_store.persist_analysis_job_snapshot(
        payload,
        include_results=True,
    )

    assert ok is True
    assert released == [conn]
    assert len(conn.fetchval_calls) == 1
    insert_job_args = conn.fetchval_calls[0][1]
    assert insert_job_args[0] == "job-dual-1"
    assert insert_job_args[1] == "sample.pdf"
    assert insert_job_args[2] == "abc123"
    assert insert_job_args[3] is None
    assert insert_job_args[4] == "done"
    assert insert_job_args[5] == "dual"

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "INSERT INTO analysis_results" in query
    assert args[0] == 17
    assert json.loads(args[1]) == [{"id": "ai-1", "source": "ai"}]
    assert json.loads(args[2]) == [{"id": "rule-1", "source": "rule"}]
    assert json.loads(args[3]) == {"totals": {"merged": 2}}
    assert json.loads(args[4])["job_id"] == "job-dual-1"


@pytest.mark.asyncio
async def test_persist_analysis_job_snapshot_clears_stale_results_for_active_job(monkeypatch):
    conn = _FakeConnection()

    async def _fake_ready():
        return True

    async def _fake_acquire():
        return conn

    async def _fake_release(_connection):
        return None

    monkeypatch.setattr(
        analysis_result_store,
        "ensure_analysis_persistence_ready",
        _fake_ready,
    )
    monkeypatch.setattr(
        analysis_result_store.DatabaseConnection,
        "acquire",
        _fake_acquire,
    )
    monkeypatch.setattr(
        analysis_result_store.DatabaseConnection,
        "release",
        _fake_release,
    )

    payload = {
        "job_id": "job-queued-1",
        "status": "queued",
        "filename": "queued.pdf",
        "mode": "legacy",
        "ts": 1773545794.0239053,
    }

    ok = await analysis_result_store.persist_analysis_job_snapshot(payload)

    assert ok is True
    assert len(conn.fetchval_calls) == 1
    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert query == "DELETE FROM analysis_results WHERE job_id = $1"
    assert args == (17,)


@pytest.mark.asyncio
async def test_get_persisted_analysis_job_detail_parses_jsonb_strings(monkeypatch):
    conn = _FakeConnection()
    released = []

    async def _fake_ready():
        return True

    async def _fake_acquire():
        return conn

    async def _fake_release(connection):
        released.append(connection)

    async def _fake_fetchrow(query, *args):
        conn.fetchrow_calls.append((query, args))
        return {
            "job_uuid": "job-dual-1",
            "filename": "sample.pdf",
            "file_hash": "abc123",
            "status": "done",
            "mode": "dual",
            "started_at": None,
            "completed_at": None,
            "created_at": None,
            "updated_at": None,
            "error_message": "",
            "metadata": json.dumps(
                {
                    "organization_name": "Test Org",
                    "structured_ingest": {
                        "status": "completed",
                        "document_version_id": 42,
                        "ps_sync": {"report_id": "ps-1"},
                    },
                    "result_meta": {"elapsed_ms": {"total": 3210}},
                }
            ),
            "ai_findings_count": 1,
            "rule_findings_count": 2,
            "merged_findings_count": 3,
            "ai_findings": json.dumps([{"id": "ai-1", "source": "ai"}]),
            "rule_findings": json.dumps([{"id": "rule-1"}, {"id": "rule-2"}]),
            "merged_result": json.dumps({"totals": {"merged": 3}}),
            "raw_response": json.dumps({"job_id": "job-dual-1"}),
        }

    conn.fetchrow = _fake_fetchrow

    monkeypatch.setattr(
        analysis_result_store,
        "ensure_analysis_persistence_ready",
        _fake_ready,
    )
    monkeypatch.setattr(
        analysis_result_store.DatabaseConnection,
        "acquire",
        _fake_acquire,
    )
    monkeypatch.setattr(
        analysis_result_store.DatabaseConnection,
        "release",
        _fake_release,
    )

    payload = await analysis_result_store.get_persisted_analysis_job_detail("job-dual-1")

    assert payload is not None
    assert payload["ai_findings"] == [{"id": "ai-1", "source": "ai"}]
    assert payload["rule_findings"] == [{"id": "rule-1"}, {"id": "rule-2"}]
    assert payload["merged_result"] == {"totals": {"merged": 3}}
    assert payload["raw_response"] == {"job_id": "job-dual-1"}
    assert payload["structured_ingest"]["status"] == "completed"
    assert payload["structured_ingest"]["document_version_id"] == 42
    assert payload["result_meta"]["elapsed_ms"]["total"] == 3210
    assert released == [conn]


def test_resolve_filename_prefers_uploaded_pdf_name(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    job_dir = upload_dir / "job-dual-1"
    job_dir.mkdir(parents=True)
    (job_dir / "sample_budget_2025.pdf").write_text("pdf", encoding="utf-8")

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    filename = analysis_result_store._resolve_filename(
        {
            "job_id": "job-dual-1",
            "filename": "",
            "saved_path": "",
        }
    )

    assert filename == "sample_budget_2025.pdf"


def test_serialize_job_summary_row_uses_display_labels(monkeypatch):
    monkeypatch.setattr(
        analysis_result_store,
        "_resolve_uploaded_pdf_name",
        lambda _job_uuid: "sample_budget_2025.pdf",
    )

    payload = analysis_result_store._serialize_job_summary_row(
        {
            "job_uuid": "11eb52da2464f248bede7ac6412df00e",
            "filename": "11eb52da2464f248bede7ac6412df00e.pdf",
            "file_hash": "",
            "status": "done",
            "mode": "legacy",
            "started_at": None,
            "completed_at": None,
            "created_at": None,
            "updated_at": None,
            "error_message": "",
            "metadata": json.dumps(
                {
                    "organization_name": "上海市普陀区规划和自然资源局",
                    "report_year": 2025,
                    "doc_type": "dept_budget",
                    "report_kind": "budget",
                }
            ),
            "ai_findings_count": 0,
            "rule_findings_count": 13,
            "merged_findings_count": 13,
            "has_results": True,
        }
    )

    assert payload["filename"] == "sample_budget_2025.pdf"
    assert payload["display_title"] == "上海市普陀区规划和自然资源局2025年部门预算"
    assert payload["display_subtitle"] == "sample_budget_2025.pdf"
