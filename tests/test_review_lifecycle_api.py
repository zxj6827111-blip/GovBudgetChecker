"""复核生命周期 API 的契约、鉴权与 IDOR 测试（WP3-A）。

验证范围与分层
--------------
本文件用**最小假连接**跑通路由层的授权路径（``_require_slot_readable`` 真的会走
``MaterialDetailQueryService.load_slot_row`` 与共享的 ``MaterialAccessScope``），
其余业务判定用桩替换服务函数——因为"门禁算得对不对"已经由
``tests/test_review_lifecycle_service.py``（纯逻辑）与
``tests/test_review_lifecycle_pg.py``（真库）覆盖，本文件只回答四件事：

1. **鉴权口径**：未登录 401、越权 403、不存在 404、参数问题 422、数据库不可用 503；
2. **IDOR**：拿着别人的槽位 UUID 请求读/开/完成/重开一律 403；
3. **job/slot 不匹配**：两个资源分别有权也不能组合成功（服务端 409 原样透出）；
4. **错误体契约**：409 的 ``detail`` 是对象，带 ``error`` 与 ``blockers``。

为什么用最小假连接而不是完整假库
--------------------------------
仓储里已有三个"完整假库"（slot / ledger / detail），再加一个覆盖复核全部 SQL 的
假库，维护成本会超过它发现问题的能力——而复核的 SQL 语义（行锁、唯一约束、
``IS DISTINCT FROM``）恰好只有真库能判定，那部分已经在 PG 用例里逐条立了反例。
这里只保留授权路径需要的那一条 SELECT，并且**对不认识的 SQL 直接报错**：
静默返回空结果会让"权限谓词漏接线"表现为"用例通过"。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from api.routes import reviews as reviews_routes
from src.schemas.review_lifecycle import (
    CompletionGate,
    CurrentAnalysisRef,
    ReviewBlocker,
    ReviewContextData,
    ReviewByJobData,
    ReviewLifecycleData,
    ReviewMutationData,
    ReviewSessionSummary,
)
from src.services import org_storage as org_storage_module
from src.services import review_lifecycle_service
from src.services.user_store import reset_user_store

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "AdminPass123"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)

DEPT_NAME = "上海市普陀区规划和自然资源局"
HEAD_UNIT_NAME = "上海市普陀区规划和自然资源局本级"
SUB_UNIT_NAME = "上海市普陀区规划和自然资源局执法大队"
SIBLING_UNIT_NAME = "上海市普陀区规划和自然资源局事务中心"

SLOT_MINE = "slot-mine"
SLOT_SIBLING = "slot-sibling"
SLOT_MISSING = "slot-missing"


class UnknownReviewSqlError(AssertionError):
    """假连接收到不认识的语句。

    必须是失败而不是"查不到"：否则生产 SQL 改了、假连接没跟上时，
    授权用例会安静地拿到"槽位不存在"，测试照样绿。
    """


class MinimalSlotConnection:
    """只认识 ``SELECT <槽位列> FROM material_slots WHERE id = $1`` 的假连接。"""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: List[tuple] = []

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        normalized = " ".join(str(sql or "").split())
        self.calls.append((normalized, args))
        if "FROM material_slots" not in normalized or "WHERE id = $1" not in normalized:
            raise UnknownReviewSqlError(f"假连接不认识这条 fetch 语句: {normalized[:160]}")
        slot_id = args[0]
        return [dict(row) for row in self.rows if str(row.get("slot_id")) == str(slot_id)]


def _slot_row(slot_id: str, *, subject_org_id: str) -> Dict[str, Any]:
    return {
        "slot_id": slot_id,
        "slot_key": f"key-{slot_id}",
        "jurisdiction_org_id": "district",
        "department_org_id": "dept",
        "subject_org_id": subject_org_id,
        "current_document_version_id": 11,
        "status": "review_required",
        "status_reason": "findings_pending",
    }


@pytest.fixture
def org_tree(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(org_storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(org_storage_module, "ORG_FILE", data_dir / "organizations.json")
    monkeypatch.setattr(org_storage_module, "LINKS_FILE", data_dir / "job_org_links.json")
    monkeypatch.setattr(org_storage_module, "_storage_instance", None)

    storage = org_storage_module.get_org_storage()

    def _add(name: str, level: str, parent_id: Optional[str] = None) -> str:
        org = runtime.Organization(
            id=runtime.Organization.generate_id(name, level, parent_id),
            name=name,
            level=level,
            parent_id=parent_id,
            keywords=[name],
        )
        return str(storage.add(org).id)

    city = _add("上海市", "city")
    district = _add("上海市普陀区", "district", city)
    dept = _add(DEPT_NAME, "department", district)
    return {
        "city": city,
        "district": district,
        "dept": dept,
        "sub_unit": _add(SUB_UNIT_NAME, "unit", dept),
        "sibling_unit": _add(SIBLING_UNIT_NAME, "unit", dept),
    }


@pytest.fixture
def client(tmp_path, monkeypatch, org_tree):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("USER_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "false")
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)

    # 任务目录：``user_can_access_job`` 会读它，没有目录时任何账号都拿不到
    # 任务权限，用例就会在"任务越权"那一层 403，测不到下面的槽位判定。
    for job_id, owner in (
        ("job-1", "unit-user"),
        ("job-other", "unit-user"),
        ("job-legacy", "unit-user"),
    ):
        job_dir = upload_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        runtime.write_json_file(
            job_dir / "status.json",
            {"job_id": job_id, "status": "done", "created_by": owner},
        )

    fake_db = MinimalSlotConnection(
        [
            _slot_row(SLOT_MINE, subject_org_id=org_tree["sub_unit"]),
            _slot_row(SLOT_SIBLING, subject_org_id=org_tree["sibling_unit"]),
        ]
    )

    async def _open():
        return fake_db

    async def _close(conn):  # noqa: ANN001 - 与生产签名一致
        return None

    monkeypatch.setattr(reviews_routes, "_open_connection", _open)
    monkeypatch.setattr(reviews_routes, "_close_connection", _close)
    reset_user_store()
    with TestClient(app) as test_client:
        yield test_client
    reset_user_store()


def _headers(session_token: Optional[str] = None) -> Dict[str, str]:
    headers = {"X-API-Key": API_KEY}
    if session_token:
        headers["X-Session-Token"] = session_token
    return headers


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


def _admin(client: TestClient) -> str:
    return _login(client, "admin", ADMIN_PASSWORD)


def _unit_user(client: TestClient, org_ids: List[str], name: str = "unit-user") -> str:
    admin = _admin(client)
    response = client.post(
        "/api/users",
        headers=_headers(admin),
        json={"username": name, "password": "UserPass123", "organization_ids": org_ids},
    )
    assert response.status_code == 200, response.text
    return _login(client, name, "UserPass123")


def _session(**overrides: Any) -> ReviewSessionSummary:
    payload: Dict[str, Any] = {
        "review_session_id": "sess-1",
        "status": "in_progress",
        "slot_id": SLOT_MINE,
        "document_version_id": 11,
        "analysis_job_uuid": "job-1",
        "analysis_basis_token": "job-1:1",
        "started_by": "unit-user",
        "started_at": NOW.isoformat(),
    }
    payload.update(overrides)
    return ReviewSessionSummary(**payload)


def _lifecycle_data(gate: Optional[CompletionGate] = None) -> ReviewLifecycleData:
    return ReviewLifecycleData(
        slot_id=SLOT_MINE,
        current_document_version_id=11,
        current_analysis=CurrentAnalysisRef(
            job_uuid="job-1",
            analysis_revision=1,
            analysis_basis_token="job-1:1",
            document_version_id=11,
            status="done",
            completed=True,
            formal_issue_count=0,
        ),
        current_session=_session(),
        history=[],
        completion_gate=gate or CompletionGate(can_complete=False, blockers=[]),
    )


def _mutation_data() -> ReviewMutationData:
    return ReviewMutationData(
        slot_id=SLOT_MINE,
        current_document_version_id=11,
        session=_session(),
        completion_gate=CompletionGate(can_complete=True, blockers=[]),
        blockers=[],
    )


# ==== 鉴权口径 ==============================================================


def test_reviews_require_login(client, monkeypatch):
    """未登录：401（不是 403、不是空数据）。

    必须显式关掉 ``TESTING``：该开关会放行一个测试管理员（仓库既有约定），
    开着它时所有接口都返回 200，未登录契约根本测不到。与 WP2-A/B 同类用例同一手法。
    """
    monkeypatch.setenv("TESTING", "false")
    assert client.get(f"/api/reviews/{SLOT_MINE}").status_code == 401
    assert client.post(f"/api/reviews/{SLOT_MINE}/start").status_code == 401
    assert client.post(f"/api/reviews/{SLOT_MINE}/complete").status_code == 401
    assert client.post(f"/api/reviews/{SLOT_MINE}/reopen").status_code == 401
    assert client.get("/api/reviews", params={"job_uuid": "job-1"}).status_code == 401


def test_missing_slot_returns_404(client):
    token = _admin(client)
    response = client.get(f"/api/reviews/{SLOT_MISSING}", headers=_headers(token))
    assert response.status_code == 404
    assert response.json()["detail"] == "material slot not found"


def test_sibling_slot_is_forbidden_for_unit_scope(client, org_tree):
    """IDOR：知道别人的槽位 UUID 也不能读、不能开、不能完成、不能重开。"""
    token = _unit_user(client, [org_tree["sub_unit"]])
    for method, path in (
        ("get", f"/api/reviews/{SLOT_SIBLING}"),
        ("post", f"/api/reviews/{SLOT_SIBLING}/start"),
        ("post", f"/api/reviews/{SLOT_SIBLING}/complete"),
        ("post", f"/api/reviews/{SLOT_SIBLING}/reopen"),
    ):
        response = getattr(client, method)(path, headers=_headers(token))
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == "slot access denied"


def test_own_slot_is_readable(client, org_tree, monkeypatch):
    token = _unit_user(client, [org_tree["sub_unit"]])

    async def _load(conn, slot_id, *, expected_job_uuid=None):
        assert slot_id == SLOT_MINE
        return _lifecycle_data()

    monkeypatch.setattr(review_lifecycle_service, "load_review_by_slot", _load)
    response = client.get(f"/api/reviews/{SLOT_MINE}", headers=_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["current_analysis"]["analysis_basis_token"] == "job-1:1"
    assert body["data"]["current_session"]["status"] == "in_progress"


def test_database_unavailable_returns_503(client, monkeypatch):
    token = _admin(client)

    async def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(reviews_routes, "_open_connection", _boom)
    response = client.get(f"/api/reviews/{SLOT_MINE}", headers=_headers(token))
    assert response.status_code == 503
    assert response.json()["detail"] == "material ledger database unavailable"


def test_missing_job_uuid_query_param_is_422(client):
    token = _admin(client)
    assert client.get("/api/reviews", headers=_headers(token)).status_code == 422


# ==== 业务 409：结构必须可被前端分派 ========================================


def test_completion_blocked_returns_structured_409(client, monkeypatch):
    """§一百零五：detail 是对象，带 error 与 blockers，不是一句字符串。"""
    token = _admin(client)

    async def _blocked(conn, slot_id, *, actor, job_uuid=None):
        raise review_lifecycle_service.ReviewLifecycleError(
            409,
            "review_completion_blocked",
            "当前无法完成复核",
            blockers=[
                ReviewBlocker(code="pending_findings", count=2),
                ReviewBlocker(code="blocking_obligations", count=4),
                ReviewBlocker(code="document_version_changed"),
            ],
        )

    monkeypatch.setattr(review_lifecycle_service, "complete_review", _blocked)
    response = client.post(f"/api/reviews/{SLOT_MINE}/complete", headers=_headers(token))
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "review_completion_blocked"
    assert detail["message"] == "当前无法完成复核"
    assert detail["blockers"][0] == {"code": "pending_findings", "count": 2}
    assert detail["blockers"][2]["count"] is None


def test_job_slot_mismatch_is_rejected_with_409(client, monkeypatch):
    """§七十一：两个资源分别有权也组合不出一次合法复核。"""
    token = _admin(client)

    async def _mismatch(conn, slot_id, *, actor, job_uuid=None):
        raise review_lifecycle_service.ReviewLifecycleError(
            409,
            "review_context_mismatch",
            "该任务不是这条材料当前的复核对象",
            job_uuid=job_uuid,
        )

    monkeypatch.setattr(review_lifecycle_service, "start_review", _mismatch)
    response = client.post(
        f"/api/reviews/{SLOT_MINE}/start",
        headers=_headers(token),
        json={"job_uuid": "job-other"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "review_context_mismatch"
    assert response.json()["detail"]["job_uuid"] == "job-other"


def test_malformed_body_is_422(client):
    token = _admin(client)
    response = client.post(
        f"/api/reviews/{SLOT_MINE}/start",
        headers={**_headers(token), "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert response.status_code == 422


def test_empty_body_is_accepted(client, monkeypatch):
    """请求体是可选的：工作台只带 job_uuid，也可以什么都不带。"""
    token = _admin(client)

    async def _start(conn, slot_id, *, actor, job_uuid=None):
        assert job_uuid is None
        return _mutation_data()

    monkeypatch.setattr(review_lifecycle_service, "start_review", _start)
    response = client.post(f"/api/reviews/{SLOT_MINE}/start", headers=_headers(token))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["session"]["review_session_id"] == "sess-1"


# ==== 按任务解析上下文 ======================================================


def test_legacy_unlinked_job_returns_200_with_unavailable_context(client, monkeypatch):
    """§七十三：没有槽位链路是**正常但不可复核**，不是错误。"""
    token = _admin(client)

    async def _by_job(conn, job_uuid):
        assert job_uuid == "job-legacy"
        return ReviewByJobData(
            review_context=ReviewContextData(
                available=False, reason="review_context_unavailable"
            ),
            review=None,
        )

    monkeypatch.setattr(review_lifecycle_service, "load_review_by_job", _by_job)
    response = client.get("/api/reviews", params={"job_uuid": "job-legacy"}, headers=_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["review_context"] == {
        "available": False,
        "reason": "review_context_unavailable",
        "slot_id": None,
        "job_uuid": None,
    }
    assert body["review"] is None


def test_job_without_read_permission_is_403(client, org_tree):
    """任务不是本人创建的 → 403（任务授权面）。

    "能看任务"与"能看材料"是两个不同的授权面，缺任何一个都必须拒绝。
    """
    token = _unit_user(client, [org_tree["sibling_unit"]], name="outsider")
    response = client.get("/api/reviews", params={"job_uuid": "job-1"}, headers=_headers(token))
    assert response.status_code == 403
    assert response.json()["detail"] == "job access denied"


def test_slot_without_read_permission_is_403_for_by_job(client, org_tree, monkeypatch):
    """反向 IDOR：任务有权、材料无权时，按任务解析出来的上下文也不许返回。"""
    token = _unit_user(client, [org_tree["sibling_unit"]])

    async def _by_job(conn, job_uuid):
        return ReviewByJobData(
            review_context=ReviewContextData(available=True, slot_id=SLOT_MINE, job_uuid=job_uuid),
            review=_lifecycle_data(),
        )

    monkeypatch.setattr(review_lifecycle_service, "load_review_by_job", _by_job)
    response = client.get("/api/reviews", params={"job_uuid": "job-1"}, headers=_headers(token))
    assert response.status_code == 403
    assert response.json()["detail"] == "slot access denied"


# ==== 审计留痕 ==============================================================


def test_successful_mutation_writes_audit_event(client, monkeypatch, tmp_path):
    """成功的动作要留审计：action / actor / 资源与安全字段。"""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    token = _admin(client)

    async def _start(conn, slot_id, *, actor, job_uuid=None):
        return _mutation_data()

    monkeypatch.setattr(review_lifecycle_service, "start_review", _start)
    response = client.post(f"/api/reviews/{SLOT_MINE}/start", headers=_headers(token))
    assert response.status_code == 200, response.text

    import json as _json

    events = [
        _json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    review_events = [item for item in events if item["action"].startswith("review.")]
    assert review_events, events
    event = review_events[-1]
    assert event["action"] == "review.start"
    assert event["result"] == "success"
    assert event["resource_type"] == "review_session"
    assert event["resource_id"] == "sess-1"
    assert event["details"]["slot_id"] == SLOT_MINE
    assert event["details"]["document_version_id"] == 11
    # 审计里不允许出现 finding 全文 / PDF 原文 / raw_response
    assert "findings" not in event["details"]
    assert "raw_response" not in event["details"]


def test_rejected_mutation_is_also_audited(client, monkeypatch, tmp_path):
    """被门禁拒绝也要留痕：否则"为什么这份材料一直完不成"无从追查。"""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    token = _admin(client)

    async def _blocked(conn, slot_id, *, actor, job_uuid=None):
        raise review_lifecycle_service.ReviewLifecycleError(
            409,
            "review_completion_blocked",
            "当前无法完成复核",
            blockers=[ReviewBlocker(code="pending_findings", count=1)],
        )

    monkeypatch.setattr(review_lifecycle_service, "complete_review", _blocked)
    response = client.post(f"/api/reviews/{SLOT_MINE}/complete", headers=_headers(token))
    assert response.status_code == 409

    import json as _json

    events = [
        _json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rejected = [item for item in events if item["result"] == "rejected"]
    assert rejected
    assert rejected[-1]["action"] == "review.complete"
    assert rejected[-1]["details"]["error"] == "review_completion_blocked"
    assert rejected[-1]["details"]["blocker_codes"] == ["pending_findings"]
