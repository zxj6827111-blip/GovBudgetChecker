"""共享投影只取封存有效值，保留完整身份与每个物理来源。"""

from copy import deepcopy
from decimal import Decimal

import pytest

from test_fiscal_input_v2_lifecycle import contexts
from src.services.analysis_fact_snapshot import AnalysisFactSnapshot
from src.services.fiscal_input_builder import VERSIONS
from src.services.effective_fact_projection import (
    snapshot_projection,
    projection_identity,
    publish_effective_projection,
)


def bundle():
    raw, effective = contexts()
    first = effective.facts[0]
    first.table_code = "FIN_02_income"
    first.instance_uid = "table-instance-A"
    first.subject_name = "合计"
    first.fiscal_year = 2024
    second = deepcopy(first)
    second.fact_uid = "second-source"
    second.instance_uid = "table-instance-B"
    second.extra_dims["evidence_refs"][0]["source_cell_id"] = "r9c9"
    effective.facts.append(second)
    effective.version_token = effective.compute_version_token()
    return AnalysisFactSnapshot.create(
        revision_id="rev-view", raw=raw, effective=effective, versions=VERSIONS
    )


def test_shared_view_keeps_decimal_effective_value_and_same_amount_different_instances():
    snapshot = bundle()
    context, tables, rows = snapshot_projection(snapshot)
    assert len(tables) == 1 and len(rows) == 2
    assert [r["values_json"]["actual"] for r in rows] == ["99.99", "99.99"]
    assert {r["row_index"] for r in rows} == {0, 1}
    assert len({r["values_json"]["_meta"]["identity_sha256"] for r in rows}) == 2
    assert all(r["values_json"]["_meta"]["fact_snapshot"] == snapshot.summary() for r in rows)
    assert tables[0]["data_json"]["instance_uids"] == ["table-instance-A", "table-instance-B"]
    assert snapshot.to_dict()["raw_context"]["facts"][0]["normalized_value"] == "12.34"
    assert context.facts[0].normalized_value == Decimal("99.99")


def test_public_revision_determines_immutable_compatibility_view_identity():
    first = projection_identity(bundle(), "job-1")
    assert first == projection_identity(bundle(), "job-1")
    assert first["report_id"] != projection_identity(bundle(), "job-2")["report_id"]
    snapshot = bundle().to_dict()
    snapshot["revision_id"] = "rev-next"
    other = projection_identity(AnalysisFactSnapshot.from_dict(snapshot), "job-1")
    assert first["report_id"] != other["report_id"]
    assert first["scope_key"] == "v2:job-1:rev-view"


@pytest.mark.asyncio
async def test_projection_refuses_to_write_outside_result_transaction():
    class Connection:
        def is_in_transaction(self):
            return False

    with pytest.raises(ValueError, match="publication transaction"):
        await publish_effective_projection(Connection(), bundle(), {"job_id": "job-1"})


def test_projection_rejects_tampered_unsealed_context():
    data = bundle().to_dict()
    data["effective_context"]["facts"][0]["normalized_value"] = "0"
    with pytest.raises(ValueError):
        snapshot_projection(AnalysisFactSnapshot(__import__("json").dumps(data)))


@pytest.mark.asyncio
async def test_shared_reader_uses_sealed_snapshot_and_does_not_query_old_amount_tables(monkeypatch):
    from src.services.ps_shared_query_service import PSSharedQueryService
    from src.services import analysis_fact_snapshot

    snapshot = bundle()
    view = projection_identity(snapshot, "job-1")
    queries = []

    class Connection:
        async def fetchrow(self, query, *args):
            queries.append(query)
            assert "org_dept_annual_report" in query
            return {
                "scope_key": view["scope_key"],
                "file_hash": snapshot.summary()["source_file_hash"],
            }

        async def fetchval(self, query, *args):
            queries.append(query)
            assert args == ("job-1",)
            return 17

    async def bound(conn, *, job_db_id, revision_id, expected):
        assert job_db_id == 17 and revision_id == "rev-view"
        assert expected["source_file_hash"] == snapshot.summary()["source_file_hash"]
        return snapshot

    monkeypatch.setattr(analysis_fact_snapshot, "read_bound_snapshot", bound)
    service = PSSharedQueryService(Connection())
    lines = await service.list_report_line_items(view["report_id"], limit=1, offset=1)
    assert lines["total"] == 2 and len(lines["items"]) == 1
    assert lines["items"][0]["values_json"]["actual"] == "99.99"
    assert lines["fact_snapshot"] == snapshot.summary()
    tables = await service.list_report_tables(view["report_id"], include_data=False)
    assert tables["total"] == 1 and tables["items"][0]["data_json"] is None
    assert not any("fact_fiscal_line_items" in q or "fiscal_table_cells" in q for q in queries)


@pytest.mark.asyncio
async def test_v2_raw_ingest_defers_all_legacy_and_shared_amount_writes(monkeypatch, tmp_path):
    from test_structured_ingest_runner import _install_runner_stubs
    from test_ocr_grid_reconstruction import fixture
    from src.services import structured_ingest_runner as runner
    from src.services.fiscal_input_builder import FiscalInputBuilder
    from src.engine.fact_context import FACT_CONTEXT_VERSION

    events = []
    _install_runner_stubs(monkeypatch, events=events)
    artifact = fixture()
    context = FiscalInputBuilder().facts(artifact)
    assert context.context_version == FACT_CONTEXT_VERSION and len(context.facts) == 4
    path = tmp_path / "synthetic-mechanism.pdf"
    path.write_bytes(b"not opened: supplied frozen parse artifact")

    async def persist_facts(conn, **kwargs):
        assert kwargs["file_hash"] == artifact.source_file_hash
        return len(kwargs["facts"])

    monkeypatch.setattr(runner, "persist_facts_and_evidence", persist_facts)

    class Forbidden:
        def __init__(self, *args):
            raise AssertionError("legacy amount materialization is forbidden")

    monkeypatch.setattr(runner, "FiscalFactMaterializer", Forbidden)
    monkeypatch.setattr(runner, "TableRecognizer", Forbidden)
    monkeypatch.setattr(runner, "PSSharedSchemaSync", Forbidden)
    result = await runner.run_structured_ingest(
        job_id="mechanism",
        pdf_path=path,
        metadata={
            "checksum": artifact.source_file_hash,
            "organization_name": "错误参数",
            "fiscal_year": 2030,
        },
        parse_artifact=artifact,
        effective_fact_context=context,
    )
    assert result["status"] == "done", result
    assert result["facts_count"] == result["unified_facts_count"] == 4
    assert result["fiscal_year"] == 2025 and result["organization_name"] == "测试局"
    assert result["ps_sync"]["status"] == "pending_snapshot_publication"
    assert "persist_legacy_cells" not in events
