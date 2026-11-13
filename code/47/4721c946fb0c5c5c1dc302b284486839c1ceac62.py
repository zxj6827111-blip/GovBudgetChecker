"""旧作业 API 的 PDF 兼容入口；实际分析仍由唯一输入管线完成。"""

import asyncio
import hashlib
import json
from pathlib import Path
import time
import uuid


def decode_log(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def canonical_job_id(legacy_job_id, source_hash):
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"govbudgetchecker:legacy-pdf:{legacy_job_id}:{source_hash}")
    )


def read_bound_legacy_result(log):
    from api import runtime
    from api.routes.reports import _load_revision_payload

    job_id = log["analysis_job_id"]
    payload = (
        _load_revision_payload(job_id, log["revision_id"])
        if log.get("revision_id")
        else runtime.read_json_file(runtime.UPLOAD_ROOT / job_id / "status.json")
    )
    binding = ((payload.get("result") or {}).get("meta") or {}).get("fact_snapshot") or {}
    if binding.get("compatibility") == "v2":
        if binding.get("source_file_hash") != log["source_file_hash"]:
            raise ValueError("legacy PDF result original source mismatch")
        if log.get("fact_snapshot") and binding != log["fact_snapshot"]:
            raise ValueError("legacy PDF result revision binding mismatch")
    return payload


async def read_legacy_snapshot(conn, log):
    from src.services.analysis_fact_snapshot import read_bound_snapshot

    database_job_id = await conn.fetchval(
        "SELECT id FROM analysis_jobs WHERE job_uuid=$1", log["analysis_job_id"]
    )
    if database_job_id is None:
        raise ValueError("canonical PDF result has no published database binding")
    return await read_bound_snapshot(
        conn,
        job_db_id=database_job_id,
        revision_id=log["fact_snapshot"]["revision_id"],
        expected=log["fact_snapshot"],
    )


async def execute_canonical_pdf(orchestrator, job, version, source):
    from api import runtime
    from api.job_queue import DurableJobQueue
    from api.main import _run_pipeline
    from src.engine.fact_context import FactContext

    source = Path(source)
    content = source.read_bytes()
    source_hash = hashlib.sha256(content).hexdigest()
    if version.get("file_hash") and version["file_hash"] != source_hash:
        raise ValueError("legacy PDF original differs from its stored document version")
    job_id = int(job["id"])
    analysis_id = canonical_job_id(job_id, source_hash)
    log = decode_log(job.get("parse_log"))
    if log.get("authority") == "fiscal-input-v2" and log.get("analysis_job_id") != analysis_id:
        raise ValueError("legacy PDF job is already bound to another original")
    log = {
        **log,
        "authority": "fiscal-input-v2",
        "analysis_job_id": analysis_id,
        "source_file_hash": source_hash,
        "document_version_id": version["id"],
    }
    directory = runtime.UPLOAD_ROOT / analysis_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "source.pdf"
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != source_hash:
            raise ValueError("canonical PDF source collision; existing file preserved")
    else:
        temporary = directory / "source.pdf.copying"
        temporary.write_bytes(content)
        temporary.replace(target)
    status_path = directory / "status.json"
    status = runtime.read_json_file(status_path)
    if not status:
        runtime.write_json_file(
            status_path,
            {
                "job_id": analysis_id,
                "status": "queued",
                "filename": source.name,
                "saved_path": str(target),
                "checksum": source_hash,
                "organization_name": version.get("org_name"),
                "report_year": version.get("fiscal_year"),
                "fiscal_year": version.get("fiscal_year"),
                "doc_type": version.get("doc_type"),
                "mode": "dual",
                "use_local_rules": True,
                "use_ai_assist": False,
                "ts": time.time(),
            },
        )
    await orchestrator.update_job_status(job_id, "running")
    await orchestrator.update_job_stage(job_id, "parse", log)
    # 与公开 Worker 共用物理文件租约。已有 Worker 抢到租约时只等待其结果，不能双跑。
    queue = DurableJobQueue(_run_pipeline, max_workers=1, resume_on_start=False)
    while True:
        payload = read_bound_legacy_result(log)
        state = str(payload.get("status") or "queued")
        binding = ((payload.get("result") or {}).get("meta") or {}).get("fact_snapshot") or {}
        publication = runtime.read_json_file(directory / "fact_publication.json")
        if (
            state in {"done", "review_required", "degraded"}
            and binding.get("compatibility") == "v2"
            and publication.get("status") == "published"
        ):
            break
        if state == "error":
            raise RuntimeError(str(payload.get("error") or "canonical PDF analysis failed"))
        await queue._run_job(analysis_id)
        payload = read_bound_legacy_result(log)
        if str(payload.get("status")) in {"queued", "processing", "running"}:
            await asyncio.sleep(1)
    snapshot = await read_legacy_snapshot(orchestrator.conn, {**log, "fact_snapshot": binding})
    context = FactContext.from_dict(snapshot.to_dict()["effective_context"])
    # 兼容报告也使用同一公开导出实现，不再通过旧 cell/fact SQL 重新取数。
    from api.routes.reports import _create_annotated_pdf, _extract_issues

    report = await asyncio.to_thread(
        _create_annotated_pdf, analysis_id, payload, _extract_issues(payload)
    )
    log.update(
        {
            "revision_id": binding["revision_id"],
            "fact_snapshot": binding,
            "quality_status": payload.get("quality_status"),
            "canonical_status": state,
        }
    )
    async with orchestrator.conn.transaction():
        await orchestrator.update_job_stage(job_id, "parse", log)
        await orchestrator.update_job_stage(
            job_id,
            "materialize",
            {
                "authority": "fiscal-input-v2",
                "analysis_job_id": analysis_id,
                "fact_snapshot": binding,
                "facts_count": len(context.facts),
                "definite_facts_count": len(context.definite_facts()),
            },
        )
        await orchestrator.update_job_stage(job_id, "report")
        await orchestrator.complete_job(job_id, str(report))
    return {"analysis_job_id": analysis_id, "fact_snapshot": binding, "report_path": str(report)}
