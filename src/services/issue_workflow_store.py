"""Server-side persistence for issue remediation workflow state."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException

from api import runtime
from api.auth_utils import user_can_access_job
from src.db.connection import DatabaseConnection
from src.services.analysis_result_store import ensure_analysis_persistence_ready


_LOCK = threading.RLock()
_LOCK_DEPTH = threading.local()
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
        if getattr(_LOCK_DEPTH, "depth", 0):
            _LOCK_DEPTH.depth += 1
            try:
                yield
            finally:
                _LOCK_DEPTH.depth -= 1
            return
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
            _LOCK_DEPTH.depth = 1
            try:
                yield
            finally:
                _LOCK_DEPTH.depth = 0
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


def issue_identity_fingerprint(issue: Dict[str, Any]) -> str:
    """Stable semantic identity for deciding whether review can cross revisions."""
    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    metrics = issue.get("metrics") if isinstance(issue.get("metrics"), dict) else {}
    payload = {
        "rule_id": str(issue.get("rule_id") or issue.get("rule") or ""),
        "title": str(issue.get("title") or ""),
        "severity": str(issue.get("severity") or ""),
        "message": str(issue.get("message") or ""),
        "page": issue.get("page_number") or issue.get("page") or location.get("page"),
        "bbox": issue.get("bbox") or location.get("bbox"),
        "check_relation_id": str(issue.get("check_relation_id") or ""),
        "fact_ids": sorted(_strings(issue.get("fact_ids"))),
        "evidence_ids": sorted(_strings(issue.get("evidence_ids"))),
        "metrics": metrics,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
                "analysis_revision": str(item.get("analysis_revision") or "").strip() or None,
                "decision_revision": str(item.get("decision_revision") or "").strip() or None,
                "decision_actor": str(item.get("decision_actor") or "").strip() or None,
                "hide_from_results": bool(item.get("hide_from_results", False)),
                "decision_origin": str(item.get("decision_origin") or "workflow"),
                "migration_id": item.get("migration_id"),
                "check_relation_id": str(item.get("check_relation_id") or "").strip() or None,
                "fact_ids": _strings(item.get("fact_ids")),
                "evidence_ids": _strings(item.get("evidence_ids")),
                "identity_fingerprint": str(item.get("identity_fingerprint") or "").strip() or None,
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
                    "report_snapshots": copy.deepcopy(item.get("report_snapshots")) if isinstance(item.get("report_snapshots"), dict) else {},
                    "status": status if status in {"draft", "ready", "submitted"} else "draft",
                    "created_at": str(item.get("created_at") or "").strip() or _now(),
                    "updated_at": str(item.get("updated_at") or "").strip() or _now(),
                }
            )
    try:
        revision = max(0, int(raw.get("revision") or 0))
    except (TypeError, ValueError):
        revision = 0

    decision_events: List[Dict[str, Any]] = []
    raw_events = raw.get("decision_events")
    if isinstance(raw_events, list):
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("job_id") or "").strip()
            issue_id = str(item.get("issue_id") or "").strip()
            status = str(item.get("status") or "pending").strip()
            if not job_id or not issue_id or status not in _VALID_STATUSES:
                continue
            event = dict(item)
            event["job_id"] = job_id
            event["issue_id"] = issue_id
            event["status"] = status
            event["decision_revision"] = str(item.get("decision_revision") or "1")
            event["fact_ids"] = _strings(item.get("fact_ids"))
            event["evidence_ids"] = _strings(item.get("evidence_ids"))
            decision_events.append(event)
    # Upgrade pre-event stores without discarding their latest decisions.
    event_keys = {
        (
            item["job_id"], item["issue_id"],
            str(item.get("analysis_revision") or ""),
            str(item.get("identity_fingerprint") or ""),
        )
        for item in decision_events
    }
    for record in issues.values():
        event_key = (
            record["job_id"], record["issue_id"],
            str(record.get("analysis_revision") or ""),
            str(record.get("identity_fingerprint") or ""),
        )
        if event_key in event_keys:
            continue
        event = copy.deepcopy(record)
        event["decision_revision"] = event.get("decision_revision") or "1"
        decision_events.append(event)
        event_keys.add(event_key)

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
        "decision_events": decision_events,
        "packages": packages,
        "review_locks": review_locks,
        "legacy_ignore_migrations": copy.deepcopy(raw.get("legacy_ignore_migrations")) if isinstance(raw.get("legacy_ignore_migrations"), dict) else {},
        "updated_at": raw.get("updated_at") or None,
        "revision": revision,
    }


def _read_state() -> Dict[str, Any]:
    return _normalize_state(runtime.read_json_file(_store_path(), default={}))


def _write_state(state: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "issues": state["issues"],
        "decision_events": state.get("decision_events") or [],
        "packages": state["packages"],
        "review_locks": state.get("review_locks") or {},
        "legacy_ignore_migrations": state.get("legacy_ignore_migrations") or {},
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


#: ``project_job_issue_decisions`` 读取的报告位置（``analysis_revision`` 与下面
#: ``_find_issue`` 及其两个子查找用到的全部容器）。主管端只把这些路径从
#: ``analysis_results.raw_response``（每行展开约 3MB）投影出来再交给它；
#: 查找位置有增减时必须同步改这里，tests/test_supervision_query.py 会比对两者结果。
DECISION_PAYLOAD_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("analysis_revision",),
    ("job_id",),
    ("ai_findings",),
    ("rule_findings",),
    ("issues",),
    ("result", "ai_findings"),
    ("result", "rule_findings"),
    ("result", "issues"),
    ("structured_ingest", "review_items"),
    ("review_report", "manual_review"),
)


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
    return _find_structured_review_item(payload, issue_id) or _find_report_review_item(payload, issue_id)


def _find_report_review_item(payload: Dict[str, Any], issue_id: str) -> Optional[Dict[str, Any]]:
    """定位规范报告里的复核卡片，与前端 collectReviewReportManualIssues 同源。

    系统提示与合并冲突没有原始 finding，必须按当前报告中的真实条目定位，
    不能仅凭客户端提交了 review_reason 前缀就接受。未完成检查只允许走补核接口。
    """
    report = payload.get("review_report")
    if not isinstance(report, dict) or not isinstance(report.get("manual_review"), list):
        return None
    items = [item for item in report["manual_review"] if isinstance(item, dict)]
    reason_titles = {
        "ai_failed": "大模型检查未完整完成",
        "evidence_incomplete_findings": "大模型发现证据不足",
        "rule_evidence_incomplete": "规则问题证据不足",
        "rules_insufficient_data": "部分规则检查数据不足",
        "check_obligations_incomplete": "仍有必查事项未完成",
    }
    for index, item in enumerate(items):
        record_type = str(item.get("record_type") or "review_reason")
        if record_type == "unfinished_check":
            continue
        identity = item.get("id")
        if identity is None:
            # 与前端 ?? 的兜底顺序一致；索引属于过滤后的完整 manual_review 列表，
            # 不能先排除 unfinished_check 再编号，否则历史卡片的 id 会发生偏移。
            key = item.get("code")
            if key is None:
                key = item.get("obligation_id")
            identity = f"{record_type}:{key if key is not None else index}"
        if str(identity).strip() != issue_id:
            continue
        finding: Dict[str, Any] = item.get("finding") if isinstance(item.get("finding"), dict) else {}
        title = item.get("title") or finding.get("title") or item.get("code") or "待人工复核事项"
        if record_type == "review_reason":
            title = reason_titles.get(str(item.get("code") or ""), title)
        return {
            **finding,
            "id": issue_id,
            "source": finding.get("source") or "manual_review",
            "rule_id": finding.get("rule_id") or item.get("code") or item.get("obligation_id") or record_type.upper(),
            "severity": "manual_review",
            "title": str(title),
            "message": str(item.get("message") or finding.get("message") or item.get("detail") or item.get("reason") or "需人工复核后确认。"),
            "location": item.get("location") or finding.get("location") or {},
        }
    return None


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
    payload = runtime.get_job_status_payload(job_id, apply_issue_filters=False)
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


def get_visible_state(user: Dict[str, Any], *, job_id: Optional[str] = None) -> Dict[str, Any]:
    requested_job = str(job_id or "").strip() or None
    if job_id is not None and requested_job is None:
        raise HTTPException(status_code=400, detail="job_id must not be blank")
    if requested_job is not None:
        # 即使该任务尚无决定，也必须鉴权，不能把越权任务伪装成空工作流。
        if not user_can_access_job(user, runtime.get_job_access_payload(requested_job)):
            raise HTTPException(status_code=403, detail="job access denied")
    with _state_lock():
        state = _read_state()
        visible_issues: Dict[str, Dict[str, Any]] = {}
        visible_job_ids: Set[str] = set()
        current_decisions: Dict[str, Dict[str, str]] = {}
        job_payloads: Dict[str, Optional[Dict[str, Any]]] = {}
        for key, record in state["issues"].items():
            record_job = record["job_id"]
            if requested_job is not None and record_job != requested_job:
                continue
            if record_job not in job_payloads:
                try:
                    job_payloads[record_job] = runtime.get_job_status_payload(record_job, apply_issue_filters=False)
                except HTTPException:
                    job_payloads[record_job] = None
            payload = job_payloads[record_job]
            if payload is None:
                continue
            if user_can_access_job(user, payload):
                if _find_issue(payload, record["issue_id"]) is None:
                    continue
                if record_job not in current_decisions:
                    # setdefault 的默认表达式也会每次执行；直接复用同一份详情投影。
                    current_decisions[record_job] = {
                        item["issue_id"]: item["status"]
                        for item in project_job_issue_decisions(record_job, payload, _state=state)
                    }
                visible = copy.deepcopy(record)
                visible["status"] = current_decisions[record_job].get(record["issue_id"], "pending")
                visible_issues[key] = visible
                visible_job_ids.add(record["job_id"])
        packages = [
            copy.deepcopy(package)
            for package in state["packages"]
            if package["job_ids"]
            and (requested_job is None or requested_job in package["job_ids"])
            and all(job_id in visible_job_ids or _can_read_job(user, job_id) for job_id in package["job_ids"])
        ]
        # The UI receives the immutable identity, not all archived report bodies.
        for package in packages:
            snapshots = package.pop("report_snapshots", {})
            package["analysis_revisions"] = {
                job_id: entry.get("payload", {}).get("analysis_revision")
                for job_id, entry in snapshots.items() if isinstance(entry, dict) and isinstance(entry.get("payload"), dict)
            }
        return {"issues": visible_issues, "packages": packages, "updated_at": state.get("updated_at")}


def _package_payload_sha(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def get_package_report_payloads(user: Dict[str, Any], package_id: str) -> Dict[str, Dict[str, Any]]:
    """Read frozen report bodies; never substitute a current analysis for an archive."""
    with _state_lock():
        state = _read_state()
    package = next((item for item in state["packages"] if item["id"] == package_id), None)
    if package is None:
        raise HTTPException(status_code=404, detail="package not found")
    for job_id in package["job_ids"]:
        if not _can_read_job(user, job_id):
            raise HTTPException(status_code=403, detail="job access denied")
    snapshots = package.get("report_snapshots") or {}
    if not snapshots:
        raise HTTPException(status_code=409, detail={"error": "package_snapshot_missing", "message": "旧整改包未记录冻结报告，请从已确认问题重新生成。"})
    if set(snapshots) != set(package["job_ids"]):
        raise HTTPException(status_code=409, detail={"error": "package_snapshot_invalid"})
    result = {}
    for job_id, entry in snapshots.items():
        payload = entry.get("payload") if isinstance(entry, dict) else None
        if not isinstance(payload, dict) or payload.get("job_id") != job_id or not payload.get("analysis_revision") or entry.get("sha256") != _package_payload_sha(payload):
            raise HTTPException(status_code=409, detail={"error": "package_snapshot_invalid"})
        result[job_id] = copy.deepcopy(payload)
    return result


def _can_read_job(user: Dict[str, Any], job_id: str) -> bool:
    try:
        return user_can_access_job(user, runtime.get_job_status_payload(job_id, apply_issue_filters=False))
    except HTTPException:
        return False


# ---- 复核编辑锁（WP3-A） ----------------------------------------------------


def get_job_issue_decisions(job_id: str) -> Dict[str, str]:
    """返回仍绑定到当前问题身份的人工决定 ``{issue_id: status}``。

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
        return {
            issue_id: status
            for issue_id, status in _decision_pairs(state, normalized)
        }


def get_job_issue_decision_records(job_id: str) -> Dict[str, Dict[str, Any]]:
    """Return the persisted, version-bound decision records for one job."""
    normalized = str(job_id or "").strip()
    if not normalized:
        return {}
    with _state_lock():
        state = _read_state()
    result: Dict[str, Dict[str, Any]] = {}
    for record in state["issues"].values():
        if str(record.get("job_id") or "") != normalized:
            continue
        issue_id = str(record.get("issue_id") or "")
        if issue_id:
            result[issue_id] = copy.deepcopy(record)
    return result


def project_job_issue_decisions(
    job_id: str, payload: Dict[str, Any], *, _state: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Project only decisions that still match this report revision or issue identity."""
    revision = str(payload.get("analysis_revision") or "")
    normalized_job_id = str(job_id or "").strip()
    if not revision:
        return []
    if _state is None:
        with _state_lock():
            state = _read_state()
            _migrate_legacy_ignored_locked(state, normalized_job_id)
    else:
        # The package writer already holds the inter-process file lock.
        state = _state
    events_by_issue: Dict[str, List[Dict[str, Any]]] = {}
    for event in state.get("decision_events", []):
        if str(event.get("job_id") or "") == normalized_job_id:
            events_by_issue.setdefault(str(event.get("issue_id") or ""), []).append(event)
    projected: List[Dict[str, Any]] = []
    for issue_id in sorted(events_by_issue):
        issue = _find_issue(payload, issue_id)
        if issue is None:
            continue
        issue_fingerprint = issue_identity_fingerprint(issue)
        facts = sorted(_strings(issue.get("fact_ids")))
        evidence = sorted(_strings(issue.get("evidence_ids")))
        matching_events = []
        for event in events_by_issue[issue_id]:
            same_revision = bool(revision and str(event.get("analysis_revision") or "") == revision)
            same_identity = bool(
                event.get("check_relation_id")
                and event.get("check_relation_id") == issue.get("check_relation_id")
                and facts
                and sorted(_strings(event.get("fact_ids"))) == facts
                and evidence
                and sorted(_strings(event.get("evidence_ids"))) == evidence
                and event.get("identity_fingerprint") == issue_fingerprint
            )
            # An ignore is an explicit judgment for one analysis. Reusing an ID,
            # or even its evidence identity, cannot silently carry that judgment.
            if str(event.get("status")) == "no_issue":
                applies = same_revision and (
                    not event.get("identity_fingerprint")
                    or event.get("identity_fingerprint") == issue_fingerprint
                )
            else:
                applies = same_revision or same_identity
            if applies:
                matching_events.append((same_revision, event))
        if not matching_events:
            continue
        # Prefer the decision recorded for this exact analysis. Otherwise the
        # latest identity-compatible event may be inherited by a rerun.
        _, record = max(
            matching_events,
            key=lambda pair: (
                pair[0],
                int(str(pair[1].get("decision_revision") or "0"))
                if str(pair[1].get("decision_revision") or "0").isdigit() else 0,
                str(pair[1].get("updated_at") or ""),
            ),
        )
        same_revision = bool(revision and str(record.get("analysis_revision") or "") == revision)
        projected.append(
            {
                "issue_id": issue_id,
                "status": str(record.get("status") or "pending"),
                "note": record.get("note"),
                "analysis_revision": revision or str(record.get("analysis_revision") or ""),
                "decision_revision": str(record.get("decision_revision") or "1"),
                "inherited": not same_revision,
                "actor": record.get("decision_actor"),
                "hide_from_results": bool(record.get("hide_from_results", False)),
                "decision_origin": record.get("decision_origin") or "workflow",
                "migration_id": record.get("migration_id"),
                "check_relation_id": issue.get("check_relation_id"),
                "fact_ids": facts,
                "evidence_ids": evidence,
                "identity_fingerprint": issue_identity_fingerprint(issue),
            }
        )
    return projected


def _append_decision_event(state: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    """Append one immutable workflow decision and return it with a per-issue revision."""
    events = state.setdefault("decision_events", [])
    key = _workflow_key(str(record.get("job_id") or ""), str(record.get("issue_id") or ""))
    prior = [
        event for event in events
        if isinstance(event, dict)
        and str(event.get("job_id") or "") == str(record.get("job_id") or "")
        and str(event.get("issue_id") or "") == str(record.get("issue_id") or "")
    ]
    revisions = [
        int(str(event.get("decision_revision") or "0"))
        for event in prior if str(event.get("decision_revision") or "0").isdigit()
    ]
    event = copy.deepcopy(record)
    event["decision_revision"] = str(max(revisions, default=0) + 1)
    event["event_id"] = f"{key}::{event['decision_revision']}"
    events.append(event)
    return event


#: 问题集合快照的算法版本。改变快照覆盖范围（例如新增一类来源）时必须改动它，
#: 否则老版本写下的 token 会被误判成"仍然匹配"。
PROBLEM_SNAPSHOT_ALGORITHM = "sha256(version_bound_workflow_decisions + hidden_no_issue_ids):v2"


def _problem_snapshot_components(state: Dict[str, Any], job_id: str) -> Dict[str, Any]:
    """在同一把锁内只投影一次，保持原报告的版本与问题指纹匹配规则。"""
    if not any(str(event.get("job_id") or "") == job_id for event in state.get("decision_events", [])):
        # 没有人工决定时无需加载报告；待处理问题仍由调用方按分析结果计算。
        return {"decisions": [], "ignored_issue_ids": []}
    try:
        payload = runtime.get_job_status_payload(job_id, apply_issue_filters=False)
    except HTTPException:
        return {"decisions": [], "ignored_issue_ids": []}
    projected = project_job_issue_decisions(job_id, payload, _state=state)
    return {
        "decisions": sorted([[item["issue_id"], item["status"]] for item in projected], key=lambda pair: pair[0]),
        "ignored_issue_ids": sorted(item["issue_id"] for item in projected
                                    if item["status"] == "no_issue" and item["hide_from_results"]),
    }


def _decision_pairs(state: Dict[str, Any], job_id: str) -> List[List[str]]:
    """Current-version decisions, using the raw report to retain hidden findings."""
    return _problem_snapshot_components(state, job_id)["decisions"]


def _ignored_issue_ids_for_job(state: Dict[str, Any], job_id: str) -> List[str]:
    return _problem_snapshot_components(state, job_id)["ignored_issue_ids"]


def _snapshot_token_from_components(components: Dict[str, Any]) -> str:
    payload = {"algorithm": PROBLEM_SNAPSHOT_ALGORITHM, **components}
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _problem_snapshot_token(state: Dict[str, Any], job_id: str) -> str:
    """问题集合快照的指纹：**必须在 ``_state_lock()`` 之内调用。**

    覆盖两项，正是 Completion Gate 判定所消费的全部外部状态：

    - ``(issue_id, status)``：问题被怎么处理了（confirmed / no_issue / ...）；
    - ``ignored_issue_ids``：同一工作流中当前版本的隐藏决定。

    不含 ``revision``：那是全局的，别的任务的修改会让当前复核被无意义地打断
    （fail-closed 但噪声大）。按 job 计算即可表达"这个 job 的问题集合变没变"。
    """
    return _snapshot_token_from_components(_problem_snapshot_components(state, job_id))


def get_job_problem_snapshot(job_id: str) -> Dict[str, Any]:
    """读取"该任务当前的问题集合"及其指纹（同一把文件锁之下的**一致**快照）。

    复核服务用它做两件事：判定完成门禁、并在落编辑锁时用 ``token`` 做 CAS。
    分成两次独立读取就会重新引入 TOCTOU：门禁看到的是一份、落锁时写的是另一份。
    """
    normalized = str(job_id or "").strip()
    if not normalized:
        return {"decisions": {}, "ignored_issue_ids": [], "token": ""}
    with _state_lock():
        state = _read_state()
        _migrate_legacy_ignored_locked(state, normalized)
        components = _problem_snapshot_components(state, normalized)
        return {
            "decisions": dict(components["decisions"]),
            "ignored_issue_ids": components["ignored_issue_ids"],
            "token": _snapshot_token_from_components(components),
        }


def set_review_lock_if_problem_snapshot_matches(
    job_id: str,
    *,
    expected_token: str,
    slot_id: Optional[str],
    review_session_id: Optional[str],
    analysis_basis_token: Optional[str],
    completed_by: Optional[str],
) -> Dict[str, Any]:
    """**比较后再落锁**：问题集合自门禁判定以来没变才写编辑锁。

    为什么不能"先 get 再 set"：两个操作之间有真实窗口。反例——

        T1 complete：读到 issue-1 = confirmed → 门禁 PASS
        T2 update_issue：把 issue-1 改回 pending（此刻还没有编辑锁，成功）
        T1 set_review_lock → 提交

    终态是"复核已完成，但它据以完成的那条问题已经被改回未处理"，
    而任务书要求复核完成时锁住的问题状态必须就是门禁判定时的那一份。

    比较与写入在**同一把 ``_state_lock()`` 之内**完成，因此窗口不存在：
    要么 T2 的写入发生在比较之前（token 不同 → 冲突，本次复核失败并回滚），
    要么发生在比较之后（那时锁已经在文件里，T2 会被 ``review_completed_locked`` 拒绝）。

    返回 ``{"status": "written" | "conflict", ...}``；冲突时**不写锁**。
    """
    normalized = str(job_id or "").strip()
    if not normalized:
        return {"status": "conflict", "reason": "job_id_required"}
    with _state_lock():
        state = _read_state()
        _migrate_legacy_ignored_locked(state, normalized)
        current_token = _problem_snapshot_token(state, normalized)
        if not expected_token or str(expected_token) != current_token:
            # fail-closed：不为"读不到期望 token"或"token 不同"做任何妥协。
            return {
                "status": "conflict",
                "expected_decision_token": str(expected_token or ""),
                "current_decision_token": current_token,
            }
        locks = dict(state.get("review_locks") or {})
        locks[normalized] = {
            "job_id": normalized,
            "slot_id": str(slot_id or "").strip() or None,
            "review_session_id": str(review_session_id or "").strip() or None,
            "analysis_basis_token": str(analysis_basis_token or "").strip() or None,
            "completed_by": str(completed_by or "").strip() or None,
            "problem_snapshot_token": current_token,
            "completed_at": _now(),
        }
        state["review_locks"] = locks
        _write_state(state)
        return {"status": "written", "current_decision_token": current_token}


def _decision_record(
    payload: Dict[str, Any], issue: Dict[str, Any], *, status: str,
    actor: Optional[str], note: Optional[str], hide_from_results: bool = False,
    origin: str = "workflow", migration_id: Optional[str] = None,
) -> Dict[str, Any]:
    revision = str(payload.get("analysis_revision") or "").strip()
    if not revision:
        raise HTTPException(status_code=409, detail={"error": "analysis_identity_required", "message": "当前分析未记录版本，不能认定问题已处理"})
    job_id, issue_id = str(payload["job_id"]), str(issue["id"])
    org_id, org_name = _organization_context(payload)
    return {
        "key": _workflow_key(job_id, issue_id), "job_id": job_id, "issue_id": issue_id,
        "status": status, "title": str(issue.get("title") or issue.get("message") or "").strip() or None,
        "severity": str(issue.get("severity") or "").strip() or None, "page": _issue_page(issue),
        "organization_id": org_id, "organization_name": org_name, "note": str(note or "").strip() or None,
        "analysis_revision": revision, "decision_actor": actor,
        "check_relation_id": str(issue.get("check_relation_id") or "").strip() or None,
        "fact_ids": _strings(issue.get("fact_ids")), "evidence_ids": _strings(issue.get("evidence_ids")),
        "identity_fingerprint": issue_identity_fingerprint(issue), "hide_from_results": hide_from_results,
        "decision_origin": origin, "migration_id": migration_id, "updated_at": _now(),
    }


def _record_decision(state: Dict[str, Any], record: Dict[str, Any]) -> None:
    event = _append_decision_event(state, record)
    record["decision_revision"] = event["decision_revision"]
    state["issues"][record["key"]] = record


def _migrate_legacy_ignored_locked(state: Dict[str, Any], job_id: str) -> bool:
    """Import explicitly version-bound legacy IDs once; quarantine unbound IDs.

    The hash receipt and original bytes survive the rename. A crash after the
    workflow commit simply retries the rename, without appending decisions twice.
    """
    path = runtime.get_ignored_issues_path(runtime.UPLOAD_ROOT / job_id)
    if not path.is_file():
        return False
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    migration_id = f"{job_id}::{digest}"
    archive_path = path.with_name(f"ignored_issues.migrated.{digest}.json")
    receipts = state.setdefault("legacy_ignore_migrations", {})
    if migration_id not in receipts:
        try:
            legacy = json.loads(source)
        except (ValueError, UnicodeDecodeError):
            legacy = None
        revision = str(legacy.get("analysis_revision") or "").strip() if isinstance(legacy, dict) else ""
        raw_ids = _strings(legacy.get("issue_ids")) if isinstance(legacy, dict) else []
        bound = None
        if revision:
            try:
                current = runtime.get_job_status_payload(job_id, apply_issue_filters=False)
            except HTTPException:
                current = {}
            if str(current.get("analysis_revision") or "") == revision:
                bound = current
            else:
                for snapshot in (runtime.UPLOAD_ROOT / job_id / "analysis_versions").glob("*/status.json"):
                    candidate = runtime.read_json_file(snapshot, default={})
                    if isinstance(candidate, dict) and candidate.get("job_id") == job_id and candidate.get("analysis_revision") == revision:
                        bound = candidate
                        break
        outcomes = []
        for issue_id in raw_ids:
            issue = _find_issue(bound, issue_id) if isinstance(bound, dict) else None
            reason = None
            if not revision:
                reason = "analysis_identity_missing"
            elif bound is None:
                reason = "analysis_snapshot_unavailable"
            elif issue is None:
                reason = "issue_not_in_bound_analysis"
            elif (state.get("review_locks") or {}).get(job_id):
                reason = "review_completed_locked"
            elif any(event.get("job_id") == job_id and event.get("issue_id") == issue_id and event.get("analysis_revision") == revision for event in state.get("decision_events", [])):
                reason = "authoritative_decision_already_present"
            if reason:
                outcomes.append({"issue_id": issue_id, "status": "retained_for_audit", "reason": reason})
                continue
            record = _decision_record(bound, issue, status="no_issue", actor=None,
                                      note=legacy.get("note"), hide_from_results=True,
                                      origin="legacy_migration", migration_id=migration_id)
            _record_decision(state, record)
            outcomes.append({"issue_id": issue_id, "status": "imported"})
        receipts[migration_id] = {"job_id": job_id, "source_path": str(path), "archive_path": str(archive_path),
                                  "sha256": digest, "analysis_revision": revision or None,
                                  "source_payload": legacy, "migrated_at": _now(), "outcomes": outcomes}
        written = _write_state(state)
        state.update(written)
        runtime.invalidate_job_summary_cache(job_id)
        _record_persistence_state("pending_retry", "legacy workflow migration requires mirror sync")
    if archive_path.exists():
        if archive_path.read_bytes() != source:
            raise HTTPException(status_code=409, detail={"error": "legacy_ignore_archive_conflict"})
        path.unlink()
    else:
        path.rename(archive_path)
    return True


def get_job_ignored_issue_ids(job_id: str, *, payload: Optional[Dict[str, Any]] = None) -> Set[str]:
    normalized = str(job_id or "").strip()
    with _state_lock():
        state = _read_state()
        _migrate_legacy_ignored_locked(state, normalized)
        if payload is None:
            try:
                payload = runtime.get_job_status_payload(normalized, apply_issue_filters=False)
            except HTTPException:
                return set()
        return {item["issue_id"] for item in project_job_issue_decisions(normalized, payload, _state=state)
                if item["status"] == "no_issue" and item["hide_from_results"]}


def set_job_ignored_issue_ids(
    job_id: str, issue_ids: Set[str], *, user: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None, append: bool = False,
) -> Dict[str, Any]:
    """Compatibility mutation with one authority, version binding and completion lock."""
    normalized = str(job_id or "").strip()
    requested = {str(item).strip() for item in issue_ids if str(item).strip()}
    with _state_lock():
        state = _read_state()
        _require_issue_mutable(state, normalized)
        payload = runtime.get_job_status_payload(normalized, apply_issue_filters=False)
        if user is not None and not user_can_access_job(user, payload):
            raise HTTPException(status_code=403, detail="job access denied")
        _migrate_legacy_ignored_locked(state, normalized)
        current = {item["issue_id"] for item in project_job_issue_decisions(normalized, payload, _state=state)
                   if item["status"] == "no_issue" and item["hide_from_results"]}
        target = current | requested if append else requested
        # Validate the whole batch before the first write.
        resolved = {}
        for issue_id in current | target:
            issue = _find_issue(payload, issue_id)
            if issue is None:
                raise HTTPException(status_code=404, detail="issue_id does not exist")
            resolved[issue_id] = issue
        actor = str((user or {}).get("username") or (user or {}).get("user_id") or "").strip() or None
        for issue_id in sorted((target - current) | (current - target)):
            record = _decision_record(payload, resolved[issue_id], status="no_issue" if issue_id in target else "pending",
                                      actor=actor, note=note, hide_from_results=issue_id in target, origin="legacy_api")
            _record_decision(state, record)
        if target != current:
            _write_state(state)
            _record_persistence_state("pending_retry", "workflow decision requires mirror sync")
            runtime.invalidate_job_summary_cache(normalized)
        return {"issue_ids": sorted(target), "authority": "workflow", "updated_at": _now()}


def add_ignored_issue_id(
    job_id: str, issue_id: str, *, user: Optional[Dict[str, Any]] = None, note: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_issue = str(issue_id or "").strip()
    if not str(job_id or "").strip() or not normalized_issue:
        raise HTTPException(status_code=400, detail="job_id and issue_id are required")
    return set_job_ignored_issue_ids(job_id, {normalized_issue}, user=user, note=note, append=True)


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


def clear_review_lock_if_matches(
    job_id: str,
    *,
    review_session_id: Optional[str],
    analysis_basis_token: Optional[str],
) -> bool:
    """**比较后再清**：只有当前锁确实是这条会话留下的才删除。

    存在的唯一理由是"PostgreSQL 提交失败后的补偿"：复核完成的顺序是
    "事务内写下编辑锁 → 提交"。提交失败时数据库已经回滚（会话不是 completed），
    那条文件锁就成了孤儿，必须清掉；但**不能无条件按 job_id 清**——
    并发下另一个会话可能已经写下它自己的锁，无条件清会把别人的安全约束一起删掉。

    比较键取 ``(job_id, review_session_id, analysis_basis_token)``：
    session id 唯一标识"哪一次复核"，basis token 额外确认"是哪一代分析"。
    两者都不匹配（或文件里已是别的会话）时**不动它**，返回 ``False``。

    整个判断与删除在 ``_state_lock()`` 之内完成，因此与并发写锁互斥——
    放到锁外做"先读、再比、后删"会留下"读完之后被别人换掉"的窗口。

    返回是否真的删除了锁。
    """
    normalized = str(job_id or "").strip()
    if not normalized:
        return False
    expected_session = str(review_session_id or "").strip()
    expected_basis = str(analysis_basis_token or "").strip()
    with _state_lock():
        state = _read_state()
        locks = dict(state.get("review_locks") or {})
        current = locks.get(normalized)
        if not isinstance(current, dict):
            return False
        if expected_session and str(current.get("review_session_id") or "").strip() != expected_session:
            # 文件里已经是别的会话的锁：不删。删了就等于把"复核已完成、问题不可改"
            # 这条安全约束从后来那次复核身上摘掉。
            return False
        if expected_basis and str(current.get("analysis_basis_token") or "").strip() != expected_basis:
            return False
        locks.pop(normalized, None)
        state["review_locks"] = locks
        _write_state(state)
        return True


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

    with _state_lock():
        state = _read_state()
        _require_issue_mutable(state, normalized_job_id)
        payload, issue = _job_and_issue(user, normalized_job_id, normalized_issue_id)
        _migrate_legacy_ignored_locked(state, normalized_job_id)
        record = _decision_record(payload, issue, status=normalized_status,
                                  actor=str(user.get("username") or user.get("user_id") or user.get("id") or "").strip() or None,
                                  note=note)
        _record_decision(state, record)
        next_state = _write_state(state)
        runtime.invalidate_job_summary_cache(normalized_job_id)
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
        payload = runtime.get_job_status_payload(job_id, apply_issue_filters=False)
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
        snapshots = {}
        for job_id in normalized_job_ids:
            current = runtime.get_job_status_payload(job_id, apply_issue_filters=False)
            if not current.get("analysis_revision") or current.get("analysis_revision") != job_payloads[job_id].get("analysis_revision"):
                raise HTTPException(status_code=409, detail={"error": "package_revision_changed"})
            job_payloads[job_id] = current
        for key, old_payload, old_issue in resolved_issues:
            job_id, _, issue_id = key.partition("::")
            current_issue = _find_issue(job_payloads[job_id], issue_id)
            record = state["issues"].get(key, {})
            decisions = dict(_decision_pairs(state, job_id))
            if current_issue is None or issue_identity_fingerprint(current_issue) != issue_identity_fingerprint(old_issue) or decisions.get(issue_id) != "confirmed" or record.get("identity_fingerprint") != issue_identity_fingerprint(current_issue):
                raise HTTPException(status_code=409, detail={"error": "package_requires_confirmed_findings", "message": "仅可打包当前分析中已确认的问题。"})
        from src.schemas.review_report import build_review_report

        for job_id, payload in job_payloads.items():
            frozen = copy.deepcopy(payload)
            frozen["human_decisions"] = project_job_issue_decisions(job_id, frozen, _state=state)
            frozen["review_report"] = build_review_report(frozen)
            snapshots[job_id] = {"payload": frozen, "sha256": _package_payload_sha(frozen)}
        for key, payload, issue in resolved_issues:
            job_id, _, issue_id = key.partition("::")
            org_id, org_name = _organization_context(payload)
            record = {
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
                "analysis_revision": str(payload.get("analysis_revision") or "").strip() or None,
                "decision_actor": str(user.get("username") or user.get("user_id") or user.get("id") or "").strip() or None,
                "check_relation_id": str(issue.get("check_relation_id") or "").strip() or None,
                "fact_ids": _strings(issue.get("fact_ids")),
                "evidence_ids": _strings(issue.get("evidence_ids")),
                "identity_fingerprint": issue_identity_fingerprint(issue),
                "updated_at": now,
            }
            event = _append_decision_event(state, record)
            record["decision_revision"] = event["decision_revision"]
            state["issues"][key] = record
        record = {
            "id": package_id,
            "name": str(name or "").strip() or "Remediation package",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "job_ids": normalized_job_ids,
            "issue_keys": normalized_issue_keys,
            "report_snapshots": snapshots,
            "status": "ready",
            "created_at": now,
            "updated_at": now,
        }
        state["packages"] = [record, *state["packages"]]
        next_state = _write_state(state)
    await persist_workflow_state(next_state)
    return {"state": next_state, "package": record}
