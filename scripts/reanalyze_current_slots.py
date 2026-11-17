"""对当前材料槽位关联的最新分析任务执行受控重分析（沿用真实流水线与 PG 持久化）。

本轮补齐只读预览、冻结选集、逐项处置原因与断点续跑：

- ``--preview`` / ``--dry-run``：只读列出每个槽位的当前版本、原件哈希、
  AI 模式、状态、人工记录数和结果指纹，并给出明确处置；
- ``--freeze-out``：把预览结果写成冻结选集，后续按精确版本/哈希关联；
- ``--freeze-list``：实际重分析只处理冻结选集，冻结项缺失或版本变化会留回执；
- ``--checkpoint``：每完成一份追加 JSONL，重跑时跳过已完成项。

断点身份纪律（2026-11-17 第二次独立复核 RR-05A）：

- 完成与否按最终任务状态分类：``status=error``（流水线异常被捕获后正常
  返回）或非终态一律记 failed，不能把“没抛异常”当 completed；
- 断点回执记录批次指纹（选集身份 + AI 策略）、输入/结果 revision、原件
  哈希与文档版本；只有“本批次确已完成、且当前行仍是该批次产物”的条目
  才允许跳过，旧批次或其他配置的断点不静默复用；
- 同批次恢复时先按断点识别“本批已产生的新 revision”，不先报
  ``frozen_revision_changed``；不同批次或版本变化仍按处置原因跳过。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
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


def _result_fingerprint(status_path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(status_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _slot_preview(row: Dict[str, Any], *, ai_policy: str, recover_stale_active: bool) -> Dict[str, Any]:
    """只读判定一个槽位的处置；不重分析、不写 status.json。"""
    job_id = str(row.get("job_uuid") or "").strip()
    job_dir = runtime.UPLOAD_ROOT / job_id if job_id else None
    status_data: Dict[str, Any] = {}
    status_path = job_dir / "status.json" if job_dir else None
    if status_path and status_path.is_file():
        loaded = runtime.read_json_file(status_path, default={})
        status_data = loaded if isinstance(loaded, dict) else {}
    is_ai = bool(status_data.get("use_ai_assist"))
    current_status = str(status_data.get("status") or "").strip().lower()
    pdf_exists = bool(job_dir and list(job_dir.glob("*.pdf")))

    disposition = "ready"
    reason = ""
    if not job_id:
        disposition, reason = "missing_job_uuid", "任务没有 job_uuid"
    elif not job_dir or not job_dir.is_dir():
        disposition, reason = "missing_job_dir", "任务目录不存在"
    elif not pdf_exists:
        disposition, reason = "missing_pdf", "任务目录没有原件 PDF"
    elif status_path is None or not status_path.is_file():
        disposition, reason = "missing_status", "任务没有 status.json"
    elif current_status in runtime.ACTIVE_ANALYSIS_STATUSES:
        if recover_stale_active:
            disposition, reason = "ready_recover_active", "先恢复孤儿 active 状态再重分析"
        else:
            disposition, reason = "skip_active", f"任务仍在 {current_status}"
    elif is_ai and ai_policy == "skip":
        disposition, reason = "skip_ai_assist_enabled", "AI 模式任务按当前策略跳过"
    elif not str(row.get("subject_org_name") or "").strip():
        # 组织归属不明不是“文件不存在”；保留材料信息待完善，不猜归属。
        disposition, reason = "missing_org", "槽位缺少组织归属"

    return {
        "slot_id": str(row.get("slot_id") or ""),
        "job_uuid": job_id,
        "document_version_id": row.get("current_document_version_id"),
        "file_hash": row.get("file_hash"),
        "analysis_revision": row.get("analysis_revision"),
        "mode": row.get("mode"),
        "ai_mode": "ai" if is_ai else "local",
        "status": current_status or row.get("status"),
        "human_decision_count": len(
            status_data.get("human_decisions")
            if isinstance(status_data.get("human_decisions"), list)
            else []
        ),
        "result_fingerprint": _result_fingerprint(status_path) if status_path else None,
        "document_exists": bool(job_dir and job_dir.is_dir()),
        "pdf_exists": pdf_exists,
        "organization": row.get("subject_org_name"),
        "fiscal_year": row.get("fiscal_year"),
        "report_kind": row.get("report_kind"),
        "disposition": disposition,
        "reason": reason,
    }


def _frozen_slot(preview: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "slot_id", "job_uuid", "document_version_id", "file_hash",
        "analysis_revision", "ai_mode", "result_fingerprint",
    )
    return {key: preview.get(key) for key in keys}


def _load_freeze_list(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    slots = payload.get("slots") if isinstance(payload, dict) else payload
    if not isinstance(slots, list):
        raise SystemExit(f"{path}: 冻结选集缺少 slots 数组")
    return [dict(item) for item in slots if isinstance(item, dict)]


#: 有有效结果、可记“完成执行”的终态；与覆盖完整性分开统计。
_TERMINAL_OK_STATUSES = frozenset({"done", "review_required", "degraded"})
TOOL_VERSION = "reanalyze_current_slots/2"


def _selection_fingerprint(
    entries: List[Dict[str, Any]], *, ai_policy: str, recover_stale_active: bool
) -> str:
    """同一批次（选集身份 + 运行策略）的指纹；跨批次断点不得静默复用。"""
    canonical = [
        {
            "job_uuid": str(item.get("job_uuid") or ""),
            "slot_id": str(item.get("slot_id") or ""),
            "document_version_id": item.get("document_version_id"),
            "file_hash": item.get("file_hash"),
            "analysis_revision": item.get("analysis_revision"),
        }
        for item in entries
    ]
    canonical.sort(key=lambda entry: entry["job_uuid"])
    blob = json.dumps(
        {
            "tool": TOOL_VERSION,
            "ai_policy": ai_policy,
            "recover_stale_active": bool(recover_stale_active),
            "targets": canonical,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _load_checkpoint(path: Path) -> set[tuple[str, str, str, str, str]]:
    """断点中“已完成执行”条目的身份元组集合。

    元组形态 ``(job_id, 批次指纹, 结果 revision, 原件哈希, 文档版本)``：
    失败/跳过回执**永不**进入该集合（outcome != completed 的行一律忽略，
    连同无法解析的行），因此失败任务不可能仅因 job_id 相同而被跳过；
    跨批次、结果 revision 不匹配或原件身份变化的复用拒绝，由
    ``_checkpoint_resumable`` 在运行时结合当前行身份完成。
    """
    completed: set[tuple[str, str, str, str, str]] = set()
    if not path.is_file():
        return completed
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("outcome") != "completed":
            continue
        job_id = str(item.get("job_id") or "").strip()
        if not job_id:
            continue
        completed.add((
            job_id,
            str(item.get("batch_fingerprint") or ""),
            str(item.get("result_revision") or ""),
            str(item.get("file_hash") or ""),
            str(item.get("document_version_id")
                if item.get("document_version_id") is not None else ""),
        ))
    return completed


def _checkpoint_resumable(
    completed: set[tuple[str, str, str, str, str]],
    *,
    batch_fingerprint: str,
    job_id: str,
    file_hash: Any,
    document_version_id: Any,
    result_revision: Any,
) -> bool:
    """仅当“本批次确已完成、且当前行仍是该批次产物”时才允许跳过。

    - 断点集合本身只含已完成条目（失败任务不可能被跳过）；
    - 批次指纹必须一致（旧批次/其他配置的断点不静默复用）；
    - 结果 revision 必须等于当前行的 analysis_revision：中间有人重跑过、
      或断点属于旧版本批次时，当前状态已不是该断点描述的状态。
    """
    if not result_revision:
        return False
    key = (
        str(job_id or ""),
        str(batch_fingerprint or ""),
        str(result_revision),
        str(file_hash or ""),
        str(document_version_id)
        if document_version_id is not None else "",
    )
    return key in completed


async def _append_checkpoint(path: Path, record: Dict[str, Any], lock: asyncio.Lock) -> None:
    async with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


async def _run_one(
    job_id: str,
    *,
    recover_stale_active: bool,
    ai_policy: str,
    batch_fingerprint: str = "",
    row: Optional[Dict[str, Any]] = None,
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

    row = row or {}
    record: Dict[str, Any] = {
        "job_id": job_id,
        "batch_fingerprint": batch_fingerprint,
        "input_revision": str(row.get("analysis_revision") or status_data.get("analysis_revision") or ""),
        "file_hash": row.get("file_hash"),
        "document_version_id": row.get("current_document_version_id"),
        "ai_mode": "ai" if is_ai else "local",
    }

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
        final_status = str(after.get("status") or "").strip().lower()
        record.update({
            "result_revision": after.get("analysis_revision"),
            "status": final_status,
        })
        # 流水线异常会被捕获并写成 status=error 后正常返回：
        # 完成与否必须按最终任务状态分类，不能把“没抛异常”当成功。
        if final_status in {"error", "failed", "cancelled"}:
            record.update({
                "outcome": "failed",
                "error": str(after.get("error") or "") or f"final_status={final_status}",
            })
        elif final_status not in _TERMINAL_OK_STATUSES:
            record.update({
                "outcome": "failed",
                "error": f"final_status_not_terminal:{final_status or 'missing'}",
            })
        else:
            record.update({
                "outcome": "completed",
                "coverage_status": str(after.get("quality_status") or ""),
                "review_reasons": [
                    r.get("code") for r in (after.get("review_reasons") or []) if isinstance(r, dict)
                ],
                "check_method": prep.get("check_method"),
            })
        record["elapsed_s"] = round(time.monotonic() - t0, 2)
        return record
    except Exception as exc:  # noqa: BLE001
        record.update({
            "outcome": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
        return record


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


async def _preview(args: argparse.Namespace) -> int:
    await DatabaseConnection.initialize()
    try:
        conn = await DatabaseConnection.acquire()
        try:
            rows = await _load_current_slot_jobs(conn)
        finally:
            await DatabaseConnection.release(conn)
        previews = [
            _slot_preview(row, ai_policy=args.ai_policy, recover_stale_active=args.recover_stale_active)
            for row in rows
        ]
        by_disposition: Dict[str, int] = {}
        for item in previews:
            by_disposition[item["disposition"]] = by_disposition.get(item["disposition"], 0) + 1
        payload = {
            "mode": "preview",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_slots": len(previews),
            "by_disposition": by_disposition,
            "slots": previews,
        }
        output = Path(args.output or args.report or "reanalyze_preview.json")
        _write_json(output, payload)
        if args.freeze_out:
            _write_json(
                Path(args.freeze_out),
                {
                    "frozen_at": datetime.now(timezone.utc).isoformat(),
                    "slots": [_frozen_slot(item) for item in previews
                              if item["disposition"] in {"ready", "ready_recover_active"}],
                },
            )
        print(
            f"PREVIEW: {len(previews)} slots "
            f"{json.dumps(by_disposition, ensure_ascii=False)} -> {output}",
            flush=True,
        )
        return 0
    finally:
        await DatabaseConnection.close()


async def _run(args: argparse.Namespace) -> int:
    await DatabaseConnection.initialize()
    orig_queue = runtime._job_queue
    runtime._job_queue = _NoopQueue()
    checkpoint_path = Path(args.checkpoint) if args.checkpoint else None
    checkpoint_lock = asyncio.Lock()
    checkpoint_completed = _load_checkpoint(checkpoint_path) if checkpoint_path else set()
    try:
        conn = await DatabaseConnection.acquire()
        try:
            rows = await _load_current_slot_jobs(conn)
        finally:
            await DatabaseConnection.release(conn)
        by_job = {str(row.get("job_uuid") or ""): row for row in rows}
        by_slot = {str(row.get("slot_id") or ""): row for row in rows}
        selection_errors: List[Dict[str, Any]] = []
        skipped_results: List[Dict[str, Any]] = []

        def _identity(row: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "job_uuid": str(row.get("job_uuid") or ""),
                "slot_id": str(row.get("slot_id") or ""),
                "document_version_id": row.get("current_document_version_id"),
                "file_hash": row.get("file_hash"),
                "analysis_revision": row.get("analysis_revision"),
            }

        targets: List[str] = []
        target_rows: Dict[str, Dict[str, Any]] = {}
        if args.freeze_list:
            frozen = _load_freeze_list(Path(args.freeze_list))
            batch_fingerprint = _selection_fingerprint(
                frozen, ai_policy=args.ai_policy, recover_stale_active=args.recover_stale_active
            )
            for entry in frozen:
                job_id = str(entry.get("job_uuid") or "").strip()
                slot_id = str(entry.get("slot_id") or "").strip()
                row = by_job.get(job_id) or by_slot.get(slot_id)
                if row is None:
                    selection_errors.append({
                        "slot_id": slot_id or None, "job_id": job_id or None,
                        "outcome": "skipped", "reason": "frozen_slot_missing",
                    })
                    continue
                # 同批次已完成且当前行仍是其产物 → 直接跳过；
                # 本批产生的新 revision 不能先报 frozen_revision_changed。
                if _checkpoint_resumable(
                    checkpoint_completed,
                    batch_fingerprint=batch_fingerprint,
                    job_id=str(row.get("job_uuid") or ""),
                    file_hash=row.get("file_hash"),
                    document_version_id=row.get("current_document_version_id"),
                    result_revision=row.get("analysis_revision"),
                ):
                    skipped_results.append({
                        "slot_id": row.get("slot_id"), "job_id": row.get("job_uuid"),
                        "outcome": "skipped", "reason": "checkpoint_completed",
                    })
                    continue
                mismatch = None
                if entry.get("document_version_id") not in (None, row.get("current_document_version_id")):
                    mismatch = "frozen_document_version_changed"
                elif entry.get("file_hash") and entry["file_hash"] != row.get("file_hash"):
                    mismatch = "frozen_file_hash_changed"
                elif entry.get("analysis_revision") and entry["analysis_revision"] != row.get("analysis_revision"):
                    mismatch = "frozen_revision_changed"
                if mismatch:
                    selection_errors.append({
                        "slot_id": row.get("slot_id"), "job_id": row.get("job_uuid"),
                        "outcome": "skipped", "reason": mismatch,
                    })
                    continue
                targets.append(str(row.get("job_uuid")))
                target_rows[str(row.get("job_uuid"))] = row
        else:
            wanted = list(dict.fromkeys(args.job_id)) if args.job_id else None
            if wanted is not None:
                # 指定 job_id 不在当前槽位行里时必须留显式回执，不能静默丢弃。
                resolved_ids = {str(row.get("job_uuid") or "") for row in rows}
                for missing in wanted:
                    if missing not in resolved_ids:
                        selection_errors.append({
                            "job_id": missing, "slot_id": None,
                            "outcome": "skipped", "reason": "job_not_in_current_slots",
                        })
                rows = [row for row in rows if str(row.get("job_uuid") or "") in wanted]
            batch_fingerprint = _selection_fingerprint(
                [_identity(row) for row in rows],
                ai_policy=args.ai_policy,
                recover_stale_active=args.recover_stale_active,
            )
            for row in rows:
                job_id = str(row.get("job_uuid") or "")
                if not job_id:
                    continue
                if _checkpoint_resumable(
                    checkpoint_completed,
                    batch_fingerprint=batch_fingerprint,
                    job_id=job_id,
                    file_hash=row.get("file_hash"),
                    document_version_id=row.get("current_document_version_id"),
                    result_revision=row.get("analysis_revision"),
                ):
                    skipped_results.append({
                        "slot_id": row.get("slot_id"), "job_id": job_id,
                        "outcome": "skipped", "reason": "checkpoint_completed",
                    })
                    continue
                targets.append(job_id)
                target_rows[job_id] = row

        targets = list(dict.fromkeys(targets))
        pending_targets = targets
        print(
            f"Target jobs: {len(targets)} (resume_skip={len(skipped_results)}, "
            f"ai_policy={args.ai_policy}, concurrency={args.concurrency})",
            flush=True,
        )
        sem = asyncio.Semaphore(max(1, args.concurrency))
        results: List[Dict[str, Any]] = list(selection_errors)
        results.extend(skipped_results)

        async def _worker(idx: int, jid: str) -> Dict[str, Any]:
            async with sem:
                res = await _run_one(
                    jid,
                    recover_stale_active=args.recover_stale_active,
                    ai_policy=args.ai_policy,
                    batch_fingerprint=batch_fingerprint,
                    row=target_rows.get(jid),
                )
                print(
                    f"[{idx}/{len(pending_targets)}] {jid[:8]} -> {res.get('outcome')} "
                    f"status={res.get('status')} reasons={res.get('review_reasons')} "
                    f"err={res.get('error') or res.get('reason') or '-'} ({res.get('elapsed_s', 0)}s)",
                    flush=True,
                )
                if checkpoint_path:
                    await _append_checkpoint(checkpoint_path, res, checkpoint_lock)
                return res

        tasks = [
            asyncio.create_task(_worker(i + 1, jid))
            for i, jid in enumerate(pending_targets)
        ]
        results.extend(await asyncio.gather(*tasks))

        completed_results = [r for r in results if r["outcome"] == "completed"]
        summary: Dict[str, Any] = {
            "total": len(results),
            "completed": len(completed_results),
            # 覆盖完整性与“完成执行”分开统计：review_required/degraded
            # 属于完成执行，但仍有未完成覆盖或降级事项。
            "coverage_incomplete": sum(
                1 for r in completed_results if r.get("status") != "done"
            ),
            "skipped": sum(1 for r in results if r["outcome"] == "skipped"),
            "failed": sum(1 for r in results if r["outcome"] == "failed"),
            "statuses": {},
            "results": results,
        }
        for r in completed_results:
            st = str(r.get("status") or "unknown")
            summary["statuses"][st] = summary["statuses"].get(st, 0) + 1
        print(
            "SUMMARY:",
            json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False),
            flush=True,
        )
        report_path = args.report or args.output
        if report_path:
            _write_json(Path(report_path), summary)
        return 0 if summary["failed"] == 0 else 1
    finally:
        runtime._job_queue = orig_queue
        await DatabaseConnection.close()


async def main_async(args: argparse.Namespace) -> int:
    if args.preview:
        return await _preview(args)
    return await _run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled reanalysis for current material slots")
    parser.add_argument("--job-id", action="append", help="Specific job_id(s) to reanalyze")
    parser.add_argument("--all-current-slots", action="store_true", help="Reanalyze all current slot jobs")
    parser.add_argument("--preview", "--dry-run", dest="preview", action="store_true",
                        help="只读预览：列出每个槽位的处置，不重分析、不写 status.json")
    parser.add_argument("--freeze-list", default="", help="实际重分析只处理该冻结选集")
    parser.add_argument("--freeze-out", default="", help="预览后写出冻结选集 JSON")
    parser.add_argument("--checkpoint", default="", help="逐份完成回执 JSONL；重跑时跳过已完成项")
    parser.add_argument("--recover-stale-active", action="store_true",
                        help="Reset orphaned queued/processing status.json states before reanalyze")
    parser.add_argument("--ai-policy", choices=("inherit", "skip", "force_local"), default="inherit")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=str, default="", help="预览或运行报告输出路径")
    parser.add_argument("--report", type=str, default="", help="运行报告输出路径（旧别名）")
    args = parser.parse_args()
    if not args.job_id and not args.all_current_slots and not args.freeze_list:
        parser.error("Provide --job-id, --all-current-slots or --freeze-list")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
