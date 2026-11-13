"""旧 PDF 入口必须进入统一管线，不能沿用旧 SQL 金额解释。"""

import pytest

from src.services.job_orchestrator import JobOrchestrator
from src.services.legacy_pdf_bridge import canonical_job_id


@pytest.mark.asyncio
async def test_legacy_pdf_entry_passes_original_to_canonical_bridge_and_never_materializes(
    monkeypatch, tmp_path
):
    source = tmp_path / "original.pdf"
    source.write_bytes(b"original source: not opened by this routing control")
    version = {
        "id": 7,
        "storage_key": str(source),
        "original_filename": "original.pdf",
        "file_hash": "source-hash",
    }
    job = {"id": 8, "document_version_id": 7}
    queries = []

    class Connection:
        async def fetchrow(self, query, *args):
            queries.append(query)
            return job if "FROM jobs" in query else version

    called = []

    async def canonical(orchestrator, got_job, got_version, got_source):
        called.append((got_job, got_version, got_source))
        return {"authority": "fiscal-input-v2"}

    monkeypatch.setattr("src.services.legacy_pdf_bridge.execute_canonical_pdf", canonical)
    instance = JobOrchestrator(Connection())

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy SQL runner must not be used for PDF")

    monkeypatch.setattr(instance, "_execute_legacy_import", forbidden)
    result = await instance.execute_pipeline(8)
    assert result["authority"] == "fiscal-input-v2" and called == [(job, version, str(source))]
    assert not any("fact_fiscal_line_items" in q or "fiscal_table_cells" in q for q in queries)
    assert source.read_bytes() == b"original source: not opened by this routing control"


@pytest.mark.asyncio
async def test_missing_original_pdf_cannot_silently_use_old_cells():
    class Connection:
        async def fetchrow(self, query, *args):
            if "FROM jobs" in query:
                return {"id": 8, "document_version_id": 7}
            return {"id": 7, "original_filename": "missing.pdf", "storage_key": "missing.pdf"}

    with pytest.raises(ValueError, match="legacy cell fallback is prohibited"):
        await JobOrchestrator(Connection()).execute_pipeline(8)


def test_canonical_legacy_job_identity_keeps_original_hash_separate_from_amounts():
    assert canonical_job_id(8, "a" * 64) == canonical_job_id(8, "a" * 64)
    assert canonical_job_id(8, "a" * 64) != canonical_job_id(8, "b" * 64)
    assert canonical_job_id(8, "a" * 64) != canonical_job_id(9, "a" * 64)


@pytest.mark.asyncio
async def test_direct_legacy_qc_cannot_read_stale_sql_amounts_for_a_new_pdf():
    from src.qc.runner_v3 import QCRunnerV3

    queries = []

    class Connection:
        async def fetchval(self, query, *args):
            queries.append(query)
            assert "fiscal_document_versions" in query
            return True

        async def execute(self, *args):
            raise AssertionError("must not create an old QC interpretation")

    with pytest.raises(RuntimeError, match="canonical input pipeline"):
        await QCRunnerV3(Connection(), 7).execute()
    assert len(queries) == 1 and "fact_fiscal_line_items" not in queries[0]
