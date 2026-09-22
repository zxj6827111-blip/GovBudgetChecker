"""Server-side persistence for issue remediation workflow state."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import asyncio
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException

from api import runtime
from api.auth_utils import user_can_access_job
from src.db.connection import DatabaseConnection
from src.services.analysis_result_store import ensure_analysis_persistence_ready


_LOCK = threading.RLock()
_VALID_STATUSES = {"pending", "confirmed", "no_issue", "needs_review", "in_package"}
logger = logging.getLogger(__name__)
_PERSISTENCE_FILENAME = ".issue_workflow_persistence.json"
# The JSON file is the durable source of truth; the database mirror must not
# stall a reviewer action when PostgreSQL is slow or unreachable.  A short
# bounded wait keeps the workflow endpoint responsive, and the failed mirror
# is retried by sync_workflow_recovery_state on the next opportunity.
_WORKFLOW_DB_TIMEOUT_SECONDS = float(os.getenv("WORKFLOW_DB_TIMEOUT_SECONDS", "3"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _store_path() -> Path:
    # UPLOAD_DIR is a persisted backend volume in the supplied deployment topology.
    return runtime.UPLOAD_ROOT / ".issue_workflow.json"


def _persistence_path() -> Path:
    return runtime.UPLOAD_ROOT / _PERSISTENCE_FILENAME


def _record_persistence_state(status: str, error: str = "") -> None:
    """Leave a durable retry signal without changing reviewer-visible state."""
    try:
        runtime.write_json_file(
            _persistence_path(),
            {
                "status": status,
                "last_attempt_at": _now(),
                "error": error or None,
            },
        )
    except Exception:
        logger.exception("Failed to record workflow persistence state")


@contextmanager
def _state_lock():
    """Serialize read-modify-write operations across all backend workers."""
    with _LOCK:
        lock_path = _store_path().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _workflow_key(job_id: str, issue_id: str) -> str:
    return f"{job_id}::{issue_id}"


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen: Set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalize_state(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    issues: Dict[str, Dict[str, Any]] = {}
    raw_issues = raw.get("issues")
    if isinstance(raw_issues, dict):
        for item in raw_issues.values():
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("job_id") or "").strip()
            issue_id = str(item.get("issue_id") or "").strip()
            if not job_id or not issue_id:
                continue
            key = _workflow_key(job_id, issue_id)
            status = str(item.get("status") or "pending").strip()
            issues[key] = {
                "key": key,
                "job_id": job_id,
                "issue_id": issue_id,
                "status": status if status in _VALID_STATUSES else "pending",
                "title": str(item.get("title") or "").strip() or None,
                "severity": str(item.get("severity") or "").strip() or None,
                "page": item.get("page") if isinstance(item.get("page"), int) else None,
                "organization_id": str(item.get("organization_id") or "").strip() or None,
                "organization_name": str(item.get("organization_name") or "").strip() or None,
                "note": str(item.get("note") or "").strip() or None,
                "updated_at": str(item.get("updated_at") or "").strip() or _now(),
            }

    packages: List[Dict[str, Any]] = []
    raw_packages = raw.get("packages")
    if isinstance(raw_packages, list):
        for item in raw_packages:
            if not isinstance(item, dict):
                continue
            package_id = str(item.get("id") or "").strip()
            if not package_id:
                continue
            status = str(item.get("status") or "draft").strip()
            packages.append(
                {
                    "id": package_id,
                    "name": str(item.get("name") or "Unnamed remediation package").strip() or "Unnamed remediation package",
                    "organization_id": str(item.get("organization_id") or "").strip() or None,
                    "organization_name": str(item.get("organization_name") or "").strip() or None,
                    "job_ids": _strings(item.get("job_ids")),
                    "issue_keys": _strings(item.get("issue_keys")),
                    "status": status if status in {"draft", "ready", "submitted"} else "draft",
                    "created_at": str(item.get("created_at") or "").strip() or _now(),
                    "updated_at": str(item.get("updated_at") or "").strip() or _now(),
                }
            )
    try:
        revision = max(0, int(raw.get("revision") or 0))
    except (TypeError, ValueError):
        revision = 0

    # 复核编辑锁（WP3-A）：{job_id: {...}}。
    #
    # 锁与"决定"放在**同一份文件、同一把文件锁**之下是有意的：复核完成与
    # 问题被修改之间不能有真实窗口。若把锁放进另一处存储（例如复核所在的
    # PostgreSQL），"复核刚完成、锁还没落下"的那一瞬间，一次 issue 修改会静默
    # 穿过去，而两边都以为自己是对的。跨存储做不到原子事务，所以让锁跟着
    # 它保护的数据走（§五十七）。
    review_locks: Dict[str, Dict[str, Any]] = {}
    raw_locks = raw.get("review_locks")
    if isinstance(raw_locks, dict):
        for job_id, item in raw_locks.items():
            normalized_job_id = str(job_id or "").strip()
            if not normalized_job_id or not isinstance(item, dict):
                continue
            review_locks[normalized_job_id] = {
                "job_id": normalized_job_id,
                "slot_id": str(item.get("slot_id") or "").strip() or None,
                "review_session_id": str(item.get("review_session_id") or "").strip() or None,
                "analysis_basis_token": str(item.get("analysis_basis_token") or "").strip() or None,
                "completed_by": str(item.get("completed_by") or "").strip() or None,
                "completed_at": str(item.get("completed_at") or "").strip() or _now(),
            }

    return {
        "issues": issues,
        "packages": packages,
        "review_locks": review_locks,
        "updated_at": raw.get("updated_at") or None,
        "revision": revision,
    }


def _read_state() -> Dict[str, Any]:
    return _normalize_state(runtime.read_json_file(_store_path(), default={}))


def _write_state(state: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "issues": state["issues"],
        "packages": state["packages"],
        "review_locks": state.get("review_locks") or {},
        "updated_at": _now(),
        "revision": int(state.get("revision") or 0) + 1,
    }
    runtime.write_json_file(_store_path(), payload)
    return payload


async def persist_workflow_state(state: Dict[str, Any]) -> bool:
    """Mirror the file-backed workflow state into PostgreSQL when configured.

    The JSON file remains the local recovery source. Database failures must not
    discard a reviewer action that has already been atomically written to it.
    """
    if not (os.getenv("DATABASE_URL") or "").strip():
        _record_persistence_state("disabled", "DATABASE_URL is not configured")
        return False
    if not await _database_ready_within_budget():
        _record_persistence_state("pending_retry", "database is unavailable")
        return False
    try:
        return await asyncio.wait_for(
            _mirror_workflow_state(state),
            timeout=_WORKFLOW_DB_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Workflow state mirror to PostgreSQL timed out after %.1fs; "
            "keeping local file state",
            _WORKFLOW_DB_TIMEOUT_SECONDS,
        )
        _record_persistence_state("pending_retry", "database mirror timed out")
        return False


async def _database_ready_within_budget() -> bool:
    """Check database readiness without blocking the workflow endpoint."""
    try:
        return await asyncio.wait_for(
            ensure_analysis_persistence_ready(),
            timeout=_WORKFLOW_DB_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Database readiness check for workflow mirror timed out")
        return False


async def _mirror_workflow_state(state: Dict[str, Any]) -> bool:
    normalized = _normalize_state(state)
    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        async with conn.transaction():
            # A file write is serialized across workers. Persist its monotonic
            # revision as well, so an older asynchronous mirror cannot replace
            # a newer reviewer action after it acquires the database connection.
            await conn.execute(
                """
                INSERT INTO workflow_state_mirror (mirror_id, revision, updated_at)
                VALUES (TRUE, 0, NOW())
                ON CONFLICT (mirror_id) DO NOTHING
                """
            )
            mirrored_revision = await conn.fetchval(
                "SELECT revision FROM workflow_state_mirror WHERE mirror_id = TRUE FOR UPDATE"
            )
            # Pre-revision recovery files were written before the DB mirror
            # existed and therefore carry revision 0.  Treat their first
            # mirror as revision 1 instead of incorrectly considering an
            # empty DB row at revision 0 as already synchronized.
            source_revision = int(normalized["revision"] or 0)
            target_revision = max(1, source_revision)
            if source_revision > 0 and int(mirrored_revision or 0) >= target_revision:
                # The mirror revision already covers this snapshot.
                if int(mirrored_revision or 0) > source_revision:
                    # A strictly newer mirror must never be replaced by this
                    # older snapshot, even when the row content differs (the
                    # snapshot was captured before a concurrent update landed).
                    return True
                # Same revision: a local workflow file that was recreated from
                # scratch restarts its revision counter at 1, which can collide
                # with an older DB mirror that also holds revision 1. Compare
                # the mirrored rows instead of trusting the revision alone, so
                # a newer local state is never silently dropped.
                mirrored_keys = {
                    str(row["workflow_key"])
                    for row in await conn.fetch(
                        "SELECT workflow_key FROM workflow_issue_records"
                    )
                }
                mirrored_package_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM workflow_remediation_packages"
                )
                if mirrored_keys == set(normalized["issues"].keys()) and (
                    int(mirrored_package_count or 0) == len(normalized["packages"])
                ):
                    return True
                logger.info(
                    "Workflow mirror revision %s equals local revision %s "
                    "but content differs; resyncing %d issues / %d packages",
                    mirrored_revision,
                    source_revision,
                    len(normalized["issues"]),
                    len(normalized["packages"]),
                )

            await conn.execute("DELETE FROM workflow_issue_records")
            for record in normalized["issues"].values():
                await conn.execute(
                    """
                    INSERT INTO workflow_issue_records (
                        workflow_key, job_uuid, issue_id, status, title, severity,
                        page_number, organization_id, organization_name, note, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::text::timestamptz)
                    ON CONFLICT (workflow_key)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        title = EXCLUDED.title,
                        severity = EXCLUDED.severity,
                        page_number = EXCLUDED.page_number,
                        organization_id = EXCLUDED.organization_id,
                        organization_name = EXCLUDED.organization_name,
                        note = EXCLUDED.note,
                        updated_at = EXCLUDED.updated_at
                    """,
                    record["key"],
                    record["job_id"],
                    record["issue_id"],
                    record["status"],
                    record["title"],
                    record["severity"],
                    record["page"],
                    record["organization_id"],
                    record["organization_name"],
                    record["note"],
                    record["updated_at"],
                )

            await conn.execute("DELETE FROM workflow_remediation_packages")
            for package in normalized["packages"]:
                await conn.execute(
                    """
                    INSERT INTO workflow_remediation_packages (
                        package_id, name, organization_id, organization_name,
                        job_ids, issue_keys, status, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8::text::timestamptz, $9::text::timestamptz)
                    """,
                    package["id"],
                    package["name"],
                    package["organization_id"],
                    package["organization_name"],
                    json.dumps(package["job_ids"], ensure_ascii=False),
                    json.dumps(package["issue_keys"], ensure_ascii=False),
                    package["status"],
                    package["created_at"],
                    package["updated_at"],
                )
            await conn.execute(
                """
                UPDATE workflow_state_mirror
                SET revision = $1, updated_at = $2::text::timestamptz
                WHERE mirror_id = TRUE
                """,
                target_revision,
                normalized["updated_at"] or _now(),
            )
        _record_persistence_state("synced")
        return True
    except Exception as exc:
        logger.exception("Failed to mirror workflow state to PostgreSQL")
        _record_persistence_state("pending_retry", str(exc) or exc.__class__.__name__)
        return False
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


async def sync_workflow_recovery_state() -> bool:
    """Retry the latest recovery snapshot after a previous database outage."""
    if not _store_path().is_file():
        return False
    with _state_lock():
        state = _read_state()
        # Upgrade a pre-mirror file in place once.  Future restarts can then
        # use normal monotonic-revision semantics instead of repeatedly
        # replaying revision 0.
        if int(state.get("revision") or 0) <= 0:
            state = _write_state(state)
    return await persist_workflow_state(state)


def _find_issue(payload: Dict[str, Any], issue_id: str) -> Optional[Dict[str, Any]]:
    def _find(items: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == issue_id:
                return item
        return None

    containers: Iterable[Any] = (payload, payload.get("result"))
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("ai_findings", "rule_findings"):
            found = _find(container.get(key))
            if found:
                return found
        issues = container.get("issues")
        if isinstance(issues, list):
            found = _find(issues)
            if found:
                return found
        elif isinstance(issues, dict):
            for key in ("all", "error", "warn", "info"):
                found = _find(issues.get(key))
                if found:
                    return found
    return _find_structured_review_item(payload, issue_id)


def _find_structured_review_item(
    payload: Dict[str, Any], issue_id: str
) -> Optional[Dict[str, Any]]:
    """在结构化待复核项里定位问题（WP3-A）。

    为什么必须补这一条：审核工作台会把 ``structured_ingest.review_items``
    渲染成问题卡片（"结构化识别待复核：T1"），用户点「确认/忽略」时提交的
    ``issue_id`` 就是这些条目的 id。而 ``ai_findings`` / ``rule_findings`` /
    ``issues`` 里**没有**这些 id，于是更新请求会以 404 结束、界面上什么也没发生：
    用户以为处理过了，实际状态仍是待处理。

    这条路径缺失时，完成门禁若要按"结构化待复核也必须有人表态"来判定，
    这份材料就永远完不成复核——前端挂着一条谁也处理不了的问题。
    因此定位与门禁必须同时覆盖同一批问题（``review_problem_set`` 是唯一权威）。

    id 的合成规则与 ``review_problem_set`` / 前端 ``toUiProblems`` 逐字同源：
    真数据里 ``review_items`` 都自带 id，合成只用于缺 id 的历史数据。
    """
    from src.services.review_problem_set import collect_review_problems

    job_id = str(payload.get("job_id") or "").strip()
    for problem in collect_review_problems(
        job_uuid=job_id,
        ai_findings=None,
        rule_findings=None,
        merged_result=None,
        structured_ingest=payload.get("structured_ingest"),
    ):
        if problem.issue_id != issue_id:
            continue
        # 复用既有更新路径：写入的记录字段（title/severity/page）与普通 finding 一致，
        # 不需要为结构化条目发明第二套记录形态。
        return {
            "id": problem.issue_id,
            "source": problem.source,
            "rule_id": f"STRUCTURED-{problem.issue_id.split(':')[0].upper()}",
            "severity": problem.severity or "manual_review",
            "title": problem.title,
            "message": problem.title,
        }
    return None


def _job_and_issue(user: Dict[str, Any], job_id: str, issue_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    payload = runtime.get_job_status_payload(job_id)
    if not user_can_access_job(user, payload):
        raise HTTPException(status_code=403, detail="job access denied")
    issue = _find_issue(payload, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue_id does not exist")
    return payload, issue


def _organization_context(payload: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    return (
        str(payload.get("organization_id") or "").strip() or None,
        str(payload.get("organization_name") or "").strip() or None,
    )


def _issue_page(issue: Dict[str, Any]) -> Optional[int]:
    page = issue.get("page")
    if isinstance(page, int):
        return page
    location = issue.get("location")
    if isinstance(location, dict) and isinstance(location.get("page"), int):
        return location["page"]
    return None


def get_visible_state(user: Dict[str, Any]) -> Dict[str, Any]:
    with _state_lock():
        state = _read_state()
        visible_issues: Dict[str, Dict[str, Any]] = {}
        visible_job_ids: Set[str] = set()
        for key, record in state["issues"].items():
            try:
                payload = runtime.get_job_status_payload(record["job_id"])
            except HTTPException:
                continue
            if user_can_access_job(user, payload):
                visible_issues[key] = copy.deepcopy(record)
                visible_job_ids.add(record["job_id"])
        packages = [
            copy.deepcopy(package)
            for package in state["packages"]
            if package["job_ids"]
            and all(job_id in visible_job_ids or _can_read_job(user, job_id) for job_id in package["job_ids"])
        ]
        return {"issues": visible_issues, "packages": packages, "updated_at": state.get("updated_at")}


def _can_read_job(user: Dict[str, Any], job_id: str) -> bool:
    try:
        return user_can_access_job(user, runtime.get_job_status_payload(job_id))
    except HTTPException:
        return False


# ---- 复核编辑锁（WP3-A） ----------------------------------------------------


def get_job_issue_decisions(job_id: str) -> Dict[str, str]:
    """该任务上全部已表态的问题状态 ``{issue_id: status}``（只读）。

    复核完成门禁的输入之一。**不做权限过滤**：调用方（复核服务）已经完成授权
    （路由层先判槽位可访问再进来），在这里再判一次只会引入第二套授权语义。

    注意返回值里**只有表过态的条目**：没有出现的 issue_id 表示"没有人表过态"，
    调用方必须按 pending 处理，绝不能把"不在字典里"当成"已完成"。
    """
    normalized = str(job_id or "").strip()
    if not normalized:
        return {}
    with _state_lock():
        state = _read_state()
    result: Dict[str, str] = {}
    for record in state["issues"].values():
        if str(record.get("job_id") or "") != normalized:
            continue
        result[str(record.get("issue_id") or "")] = str(record.get("status") or "pending")
    result.pop("", None)
    return result


def get_review_lock(job_id: str) -> Optional[Dict[str, Any]]:
    """该任务当前的复核编辑锁（没有则 ``None``）。"""
    normalized = str(job_id or "").strip()
    if not normalized:
        return None
    with _state_lock():
        state = _read_state()
    lock = state.get("review_locks", {}).get(normalized)
    return copy.deepcopy(lock) if isinstance(lock, dict) else None


def set_review_lock(
    job_id: str,
    *,
    slot_id: Optional[str],
    review_session_id: Optional[str],
    analysis_basis_token: Optional[str],
    completed_by: Optional[str],
) -> Dict[str, Any]:
    """复核完成时落下编辑锁：该任务的问题在重新开始复核前不可再改。

    锁与 issue 决定写在**同一份文件、同一把文件锁**下，因此"写决定"与"判锁"
    之间不存在窗口——这正是把它放在这里而不是放到复核库里的原因（§五十七）。
    """
    normalized = str(job_id or "").strip()
    if not normalized:
        return {}
    with _state_lock():
        state = _read_state()
        locks = dict(state.get("review_locks") or {})
        locks[normalized] = {
            "job_id": normalized,
            "slot_id": str(slot_id or "").strip() or None,
            "review_session_id": str(review_session_id or "").strip() or None,
            "analysis_basis_token": str(analysis_basis_token or "").strip() or None,
            "completed_by": str(completed_by or "").strip() or None,
            "completed_at": _now(),
        }
        state["review_locks"] = locks
        return _write_state(state)


def clear_review_lock(job_id: str) -> Dict[str, Any]:
    """解除编辑锁（重新开始复核 / 复核失效 / 重新分析时调用）。

    幂等：没有锁时也照样写回一次状态（revision 递增），调用方不需要先判断。
    """
    normalized = str(job_id or "").strip()
    if not normalized:
        return {}
    with _state_lock():
        state = _read_state()
        locks = dict(state.get("review_locks") or {})
        locks.pop(normalized, None)
        state["review_locks"] = locks
        return _write_state(state)


def _require_issue_mutable(state: Dict[str, Any], job_id: str) -> None:
    """复核已完成的任务：拒绝静默修改问题（§五十五/§五十六）。

    为什么推荐"锁定"而不是"自动失效"：``issue_workflow_store`` 是文件存储、
    Review Session 是 PostgreSQL，一次 issue 更新同时写 JSON + 失效 DB 记录
    无法构成真正的跨存储原子事务；半成功时"复核说完成、问题却变了"没人能解释。
    因此这里只做一件事：**先显式重新开始复核，再允许改问题**。

    409 而不是 403：有权限，但当前业务状态不允许（§一百零六）。
    """
    lock = (state.get("review_locks") or {}).get(job_id)
    if not isinstance(lock, dict):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "error": "review_completed_locked",
            "message": "该材料的复核已完成，请先重新开始复核再修改问题",
            "review_session_id": lock.get("review_session_id"),
            "completed_by": lock.get("completed_by"),
            "completed_at": lock.get("completed_at"),
        },
    )


async def update_issue(
    user: Dict[str, Any],
    *,
    job_id: str,
    issue_id: str,
    status: str,
    note: Optional[str],
) -> Dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    normalized_issue_id = str(issue_id or "").strip()
    normalized_status = str(status or "").strip()
    if not normalized_job_id or not normalized_issue_id:
        raise HTTPException(status_code=400, detail="job_id and issue_id are required")
    if normalized_status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail="invalid workflow status")

    payload, issue = _job_and_issue(user, normalized_job_id, normalized_issue_id)
    org_id, org_name = _organization_context(payload)
    with _state_lock():
        state = _read_state()
        # 判锁必须在**同一把文件锁之内**：放在锁外就等于"查过之后、写入之前"
        # 有一个窗口，复核可能正好在这个窗口里完成。
        _require_issue_mutable(state, normalized_job_id)
        key = _workflow_key(normalized_job_id, normalized_issue_id)
        state["issues"][key] = {
            "key": key,
            "job_id": normalized_job_id,
            "issue_id": normalized_issue_id,
            "status": normalized_status,
            "title": str(issue.get("title") or issue.get("message") or "").strip() or None,
            "severity": str(issue.get("severity") or "").strip() or None,
            "page": _issue_page(issue),
            "organization_id": org_id,
            "organization_name": org_name,
            "note": str(note or "").strip() or None,
            "updated_at": _now(),
        }
        next_state = _write_state(state)
    await persist_workflow_state(next_state)
    return next_state


async def create_package(
    user: Dict[str, Any],
    *,
    name: Optional[str],
    job_ids: Any,
    issue_keys: Any,
) -> Dict[str, Any]:
    normalized_job_ids = _strings(job_ids)
    normalized_issue_keys = _strings(issue_keys)
    if not normalized_job_ids or not normalized_issue_keys:
        raise HTTPException(status_code=400, detail="job_ids and issue_keys are required")

    job_payloads: Dict[str, Dict[str, Any]] = {}
    for job_id in normalized_job_ids:
        payload = runtime.get_job_status_payload(job_id)
        if not user_can_access_job(user, payload):
            raise HTTPException(status_code=403, detail="job access denied")
        job_payloads[job_id] = payload

    resolved_issues: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for key in normalized_issue_keys:
        job_id, separator, issue_id = key.partition("::")
        if not separator or not job_id or not issue_id or job_id not in job_payloads:
            raise HTTPException(status_code=400, detail="issue_keys must belong to requested job_ids")
        issue = _find_issue(job_payloads[job_id], issue_id)
        if issue is None:
            raise HTTPException(status_code=404, detail=f"issue_id does not exist: {issue_id}")
        resolved_issues.append((key, job_payloads[job_id], issue))

    org_contexts = {_organization_context(payload) for payload in job_payloads.values()}
    organization_id, organization_name = next(iter(org_contexts)) if len(org_contexts) == 1 else (None, None)
    now = _now()
    package_id = f"pkg-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    with _state_lock():
        state = _read_state()
        # 组包同样会改写问题的处理状态（→ in_package），因此受同一把编辑锁约束：
        # 复核完成后还能把问题改成"已进整改包"，等于"复核完成后问题不会被静默
        # 修改"这条规则只挡住了两条路径中的一条。归档/导出的业务逻辑本身不动
        # （§一百零三），这里只是要求"先重新开始复核"。
        for job_id in normalized_job_ids:
            _require_issue_mutable(state, job_id)
        for key, payload, issue in resolved_issues:
            job_id, _, issue_id = key.partition("::")
            org_id, org_name = _organization_context(payload)
            state["issues"][key] = {
                "key": key,
                "job_id": job_id,
                "issue_id": issue_id,
                "status": "in_package",
                "title": str(issue.get("title") or issue.get("message") or "").strip() or None,
                "severity": str(issue.get("severity") or "").strip() or None,
                "page": _issue_page(issue),
                "organization_id": org_id,
                "organization_name": org_name,
                "note": state["issues"].get(key, {}).get("note"),
                "updated_at": now,
            }
        record = {
            "id": package_id,
            "name": str(name or "").strip() or "Remediation package",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "job_ids": normalized_job_ids,
            "issue_keys": normalized_issue_keys,
            "status": "ready",
            "created_at": now,
            "updated_at": now,
        }
        state["packages"] = [record, *state["packages"]]
        next_state = _write_state(state)
    await persist_workflow_state(next_state)
    return {"state": next_state, "package": record}
