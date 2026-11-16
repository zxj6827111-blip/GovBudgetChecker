"""对当前材料槽位关联的最新分析任务执行受控重分析（沿用真实流水线与 PG 持久化）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from api import main as api_main  # noqa: E402
from api import runtime  # noqa: E402
from src.db.connection import DatabaseConnection  # noqa: E402
from src.services.material_detail_query_service import select_current_run  # noqa: E402


class _NoopQueue:
    """Stub queue during offline controlled batch run (pipeline invoked directly)."""

    async def enqueue(self, _job_id: str) -> None:
        return None


async def _load_current_slot_jobs(conn: Any) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT s.id::text AS slot_id,
               s.status AS slot_status,
               s.status_reason AS slot_status_reason,
               s.subject_org_name,
               s.fiscal_year,
               s.report_kind,
               s.current_document_version_id,
               v.file_hash,
               v.original_filename,
               j.id AS id,
               j.job_uuid,
               j.status,
               j.mode,
               j.analysis_revision,
               j.completed_at,
               j.started_at,
               j.updated_at,
               j.created_at
        FROM material_slots s
        JOIN fiscal_document_versions v
          ON v.id = s.current_document_version_id AND v.slot_id = s.id
        JOIN analysis_jobs j
          ON j.structured_document_version_id = v.id::numeric
        WHERE s.current_document_version_id IS NOT NULL
        """
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(item["slot_id"], []).append(item)
    return [select_current_run(group) for group in grouped.values()]


async def _run_one(
    job_id: str,
    *,
    recover_stale_active: bool,
    ai_policy: str,
) -> Dict[str, Any]:
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.is_dir():
        return {"job_id": job_id, "outcome": "skipped", "reason": "missing_job_dir"}
    if not list(job_dir.glob("*.pdf")):
        return {"job_id": job_id, "outcome": "skipped", "reason": "missing_pdf"}

    status_path = job_dir / "status.json"
    status_data = runtime.read_json_file(status_path, default={})
    if not isinstance(status_data, dict):
        status_data = {}

    is_ai = bool(status_data.get("use_ai_assist"))
    if is_ai and ai_policy == "skip":
        return {"job_id": job_id, "outcome": "skipped", "reason": "ai_assist_enabled"}

    current_status = str(status_data.get("status") or "").strip().lower()
    if current_status in runtime.ACTIVE_ANALYSIS_STATUSES:
        if not recover_stale_active:
            return {"job_id": job_id, "outcome": "skipped", "reason": f"active:{current_status}"}
        status_data["status"] = "error"
        status_data["error"] = "recovered_orphaned_active_state_before_reanalyze"
        runtime.write_json_file(status_path, status_data)

    body: Optional[Dict[str, Any]] = None
    if is_ai and ai_policy == "force_local":
        body = {
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
        }

    t0 = time.monotonic()
    try:
        prep = await runtime.reanalyze_job(job_id, body)
        await api_main._run_pipeline(job_dir)
        after = runtime.get_job_status_payload(job_id)
        return {
            "job_id": job_id,
            "outcome": "completed",
            "status": after.get("status"),
            "analysis_revision": after.get("analysis_revision"),
            "check_method": prep.get("check_method"),
            "review_reasons": [
                r.get("code") for r in (after.get("review_reasons") or []) if isinstance(r, dict)
            ],
            "elapsed_s": round(time.monotonic() - t0, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "job_id": job_id,
            "outcome": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - t0, 2),
        }


async def main_async(args: argparse.Namespace) -> int:
    await DatabaseConnection.initialize()
    orig_queue = runtime._job_queue
    runtime._job_queue = _NoopQueue()
    try:
        if args.job_id:
            targets = list(dict.fromkeys(args.job_id))
        else:
            conn = await DatabaseConnection.acquire()
            try:
                rows = await _load_current_slot_jobs(conn)
            finally:
                await DatabaseConnection.release(conn)
            targets = [str(r["job_uuid"]) for r in rows if r.get("job_uuid")]

        print(f"Target jobs: {len(targets)} (ai_policy={args.ai_policy}, concurrency={args.concurrency})", flush=True)
        sem = asyncio.Semaphore(max(1, args.concurrency))
        results: List[Dict[str, Any]] = []

        async def _worker(idx: int, jid: str) -> Dict[str, Any]:
            async with sem:
                res = await _run_one(
                    jid,
                    recover_stale_active=args.recover_stale_active,
                    ai_policy=args.ai_policy,
                )
                print(
                    f"[{idx}/{len(targets)}] {jid[:8]} -> {res.get('outcome')} "
                    f"status={res.get('status')} reasons={res.get('review_reasons')} "
                    f"err={res.get('error') or res.get('reason') or '-'} ({res.get('elapsed_s', 0)}s)",
                    flush=True,
                )
                return res

        tasks = [asyncio.create_task(_worker(i + 1, jid)) for i, jid in enumerate(targets)]
        results = await asyncio.gather(*tasks)

        summary: Dict[str, Any] = {
            "total": len(results),
            "completed": sum(1 for r in results if r["outcome"] == "completed"),
            "skipped": sum(1 for r in results if r["outcome"] == "skipped"),
            "failed": sum(1 for r in results if r["outcome"] == "failed"),
            "statuses": {},
            "results": results,
        }
        for r in results:
            if r["outcome"] == "completed":
                st = str(r.get("status") or "unknown")
                summary["statuses"][st] = summary["statuses"].get(st, 0) + 1
        print("SUMMARY:", json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False), flush=True)
        if args.report:
            out_path = Path(args.report)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if summary["failed"] == 0 else 1
    finally:
        runtime._job_queue = orig_queue
        await DatabaseConnection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled reanalysis for current material slots")
    parser.add_argument("--job-id", action="append", help="Specific job_id(s) to reanalyze")
    parser.add_argument("--all-current-slots", action="store_true", help="Reanalyze all current slot jobs")
    parser.add_argument("--recover-stale-active", action="store_true", help="Reset orphaned queued/processing status.json states before reanalyze")
    parser.add_argument("--ai-policy", choices=("inherit", "skip", "force_local"), default="skip")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--report", type=str, default="")
    args = parser.parse_args()
    if not args.job_id and not args.all_current_slots:
        parser.error("Provide --job-id or --all-current-slots")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
