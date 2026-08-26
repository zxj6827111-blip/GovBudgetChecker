from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "migrate_historical_uploads.py"
_SPEC = importlib.util.spec_from_file_location("migrate_historical_uploads", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def test_inspect_uploads_builds_snapshot_without_writing(tmp_path):
    uploads = tmp_path / "uploads"
    job = uploads / "job-1"
    job.mkdir(parents=True)
    (job / "status.json").write_text('{"status":"done","result":{"issues":{}}}', encoding="utf-8")
    (job / "source.pdf").write_bytes(b"%PDF-1.4")
    (uploads / "missing-status").mkdir()

    snapshots, skipped = migration.inspect_uploads(uploads)

    assert len(snapshots) == 1
    assert snapshots[0]["job_id"] == "job-1"
    assert snapshots[0]["filename"] == "source.pdf"
    assert snapshots[0]["storage_key"] == "job-1/source.pdf"
    assert snapshots[0]["size"] == len(b"%PDF-1.4")
    assert skipped == ["missing-status"]
    assert not (job / "persistence.json").exists()


def test_build_snapshot_keeps_existing_storage_metadata_and_loads_structured_ingest(tmp_path):
    job = tmp_path / "job-2"
    job.mkdir()
    (job / "status.json").write_text(
        '{"job_id":"recorded-job","storage_key":"durable/key.pdf"}', encoding="utf-8"
    )
    (job / "structured_ingest.json").write_text('{"status":"done","document_version_id":12}', encoding="utf-8")
    (job / "original.pdf").write_bytes(b"pdf")

    snapshot = migration.build_snapshot(job)

    assert snapshot is not None
    assert snapshot["job_id"] == "recorded-job"
    assert snapshot["storage_key"] == "durable/key.pdf"
    assert snapshot["structured_ingest"]["document_version_id"] == 12
