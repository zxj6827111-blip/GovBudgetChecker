"""验证专属19018数据库停机/恢复：重放同一候选并保留旧正式指针。"""

import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from decimal import Decimal

BASE = Path(__file__).resolve().parents[2] / "outage-validation"
BASE.mkdir(exist_ok=True)
os.environ["UPLOAD_DIR"] = str(BASE / "uploads")
os.environ["USER_DATA_DIR"] = str(BASE / "users")
os.environ["ORG_DATA_DIR"] = str(BASE / "orgs")
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["TESTING"] = "true"
os.environ["GOVBUDGET_AUTH_ENABLED"] = "false"  # 仅导入文件存储函数；不启动HTTP服务。
os.environ["DATABASE_URL"] = (
    "postgresql://postgres:isolated_validation_only@127.0.0.1:19018/pdf_input_v2"
)

from src.engine.fact_context import FactContext
from src.schemas.parse_artifact import FiscalFact
from src.services.analysis_fact_snapshot import (
    AnalysisFactSnapshot,
    stage_candidate,
    read_candidate,
)
from src.services.analysis_result_store import (
    persist_analysis_job_snapshot,
    sync_pending_analysis_snapshots,
)
from src.services.fiscal_input_builder import VERSIONS
from src.db.connection import DatabaseConnection
from api import runtime

JOB = "fact-outage-controlled"
job = Path(os.environ["UPLOAD_DIR"]) / JOB
job.mkdir(parents=True, exist_ok=True)


def bundle(revision, value):
    raw = FactContext(
        [FiscalFact("source:r1c1", "actual", "12.34", "万元", Decimal("12.34"))],
        file_hash="d" * 64,
        parse_run_uid="outage-run",
    )
    effective = copy.deepcopy(raw)
    if value != "12.34":
        effective.facts[0].normalized_value = Decimal(value)
        effective.overlay_records = [{"raw_value": "12.34", "normalized_value": value}]
        effective.version_token = effective.compute_version_token()
    snapshot = AnalysisFactSnapshot.create(
        revision_id=revision, raw=raw, effective=effective, versions=VERSIONS
    )
    payload = {
        "job_id": JOB,
        "filename": "controlled.pdf",
        "status": "review_required",
        "analysis_revision": revision,
        "result": {"issues": {}, "meta": {"fact_snapshot": snapshot.summary()}},
    }
    return payload, snapshot


async def main(mode):
    if mode == "prepare":
        await DatabaseConnection.initialize(os.environ["DATABASE_URL"])
        conn = await DatabaseConnection.acquire()
        try:
            await conn.execute(
                "INSERT INTO parse_runs(parse_run_uid,document_version_id,source_file_hash) VALUES($1,1,$2) ON CONFLICT DO NOTHING",
                "outage-run",
                "d" * 64,
            )
        finally:
            await DatabaseConnection.release(conn)
        payload, snapshot = bundle("rev-outage-old", "12.34")
        stage_candidate(job, payload, snapshot)
        assert await persist_analysis_job_snapshot(payload, include_results=True)
        runtime.write_json_file(job / "fact_context.json", snapshot.to_dict()["effective_context"])
        runtime.write_analysis_revision_snapshot(job, payload)
        runtime.write_json_file(job / "status.json", payload)
        new_payload, new_snapshot = bundle("rev-outage-new", "99.99")
        path = stage_candidate(job, new_payload, new_snapshot)
        (BASE / "candidate.sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest())
        (BASE / "old-pointer.json").write_bytes((job / "analysis_current.json").read_bytes()) if (
            job / "analysis_current.json"
        ).exists() else None
        print("prepared old formal revision and a distinct immutable new candidate")
    elif mode == "offline":
        payload, snapshot = read_candidate(job / "pending_fact_publication.json")
        assert not await persist_analysis_job_snapshot(payload, include_results=True)
        pointer = runtime.read_analysis_revision_pointer(job)
        assert pointer["revision_id"] == "rev-outage-old"
        assert (
            hashlib.sha256((job / "pending_fact_publication.json").read_bytes()).hexdigest()
            == (BASE / "candidate.sha256").read_text()
        )
        (BASE / "offline-receipt.json").write_text(
            json.dumps(
                {
                    "database_unavailable": True,
                    "old_formal_preserved": True,
                    "candidate_unchanged": True,
                }
            )
        )
        print("real stopped database: candidate retained and old formal pointer preserved")
    elif mode == "resume":
        # 如果恢复路径误调用物化器，立刻失败。
        from src.services.fiscal_input_builder import FiscalInputBuilder

        def forbidden(*a, **k):
            raise AssertionError("recovery must never rematerialize")

        FiscalInputBuilder.facts = forbidden
        summary = await sync_pending_analysis_snapshots(Path(os.environ["UPLOAD_DIR"]))
        assert summary["synced"] == 1 and summary["failed"] == 0, summary
        assert runtime.read_analysis_revision_pointer(job)["revision_id"] == "rev-outage-new"
        payload, snapshot = read_candidate(job / "pending_fact_publication.json")
        assert (
            hashlib.sha256((job / "pending_fact_publication.json").read_bytes()).hexdigest()
            == (BASE / "candidate.sha256").read_text()
        )
        conn = await DatabaseConnection.acquire()
        try:
            row = await conn.fetchrow(
                "SELECT s.* FROM analysis_fact_snapshots s JOIN analysis_jobs j ON j.id=s.job_id WHERE j.job_uuid=$1 AND s.revision_id=$2",
                JOB,
                "rev-outage-new",
            )
            assert (
                row["raw_digest"] == snapshot.to_dict()["raw_digest"]
                and row["effective_digest"] == snapshot.to_dict()["effective_digest"]
            )
            effective = FactContext.from_dict(json.loads(row["effective_context"]))
            assert effective.facts[0].normalized_value == Decimal("99.99")
        finally:
            await DatabaseConnection.release(conn)
        result = {
            "status": "passed",
            "actual_database_stop_and_restart": True,
            "old_revision_preserved_while_offline": True,
            "candidate_bytes_unchanged": True,
            "rematerialization_calls": 0,
            "raw_effective_digests_readback_match": True,
            "formal_revision_after_recovery": "rev-outage-new",
            "summary": summary,
            "scope": "controlled lifecycle, not PDF accuracy",
        }
        (BASE / "receipt.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    await DatabaseConnection.close()


asyncio.run(main(sys.argv[1]))
