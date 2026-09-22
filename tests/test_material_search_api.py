"""全局材料搜索接口（WP2-C）契约、鉴权与越权测试。

为什么用假连接
--------------
``tests/support_material_search_db.py`` 的假连接走**完整**路径
（路由 → 权限范围 → 查询解析 → 参数 → 匹配 → 归因 → 响应契约），
它按 SQL 参数里真实传下去的 ``visible_org_ids`` 过滤种子行，因此
"权限谓词漏接线""token 匹配块形状变了""命中归因与 SQL 不一致"这类缺陷会被抓住。

真库上才有的行为（``ILIKE ... ESCAPE``、``COUNT(*) OVER ()``、排序名次、
``= ANY(uuid[])`` 的类型推导、JSONB 存储形态）由
``tests/test_material_search_pg.py`` 覆盖。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from api.routes import materials as materials_routes
from src.services import org_storage as org_storage_module
from src.services.user_store import reset_user_store
from support_material_search_db import (
    FakeSearchConnection,
    make_job_record,
    make_slot_row,
    make_version_record,
)

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "AdminPass123"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=10)
LONG_AGO = NOW - timedelta(days=400)

DEPT_NAME = "上海市普陀区规划和自然资源局"
#: 真实形态：部门与"本级单位"**完全同名**，靠名称后缀认不出来（§十六）。
HEAD_UNIT_NAME = DEPT_NAME
SUB_UNIT_NAME = "上海市普陀区规划和自然资源局执法大队"
SIBLING_UNIT_NAME = "上海市普陀区规划和自然资源局事务中心"
OTHER_UNIT_NAME = "上海市静安区教育局第一小学"

SLOT_SUMMARY = "slot-summary"
SLOT_HEAD = "slot-head"
SLOT_SUB = "slot-sub"
SLOT_SIBLING = "slot-sibling"
SLOT_OTHER = "slot-other"
SLOT_DENIED = "slot-denied"

HEAD_V1 = 11
HEAD_V2 = 22


def _add_org(storage, name: str, level: str, parent_id: str | None = None) -> str:
    org = runtime.Organization(
        id=runtime.Organization.generate_id(name, level, parent_id),
        name=name,
        level=level,
        parent_id=parent_id,
        keywords=[name],
    )
    return str(storage.add(org).id)


@pytest.fixture
def org_tree(tmp_path, monkeypatch):
    """临时组织目录：市 / 两个区 / 部门 + 三个单位（含同名本部）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(org_storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(org_storage_module, "ORG_FILE", data_dir / "organizations.json")
    monkeypatch.setattr(org_storage_module, "LINKS_FILE", data_dir / "job_org_links.json")
    monkeypatch.setattr(org_storage_module, "_storage_instance", None)

    storage = org_storage_module.get_org_storage()
    city = _add_org(storage, "上海市", "city")
    district = _add_org(storage, "上海市普陀区", "district", city)
    other_district = _add_org(storage, "上海市静安区", "district", city)
    dept = _add_org(storage, DEPT_NAME, "department", district)
    head_unit = _add_org(storage, HEAD_UNIT_NAME, "unit", dept)
    sub_unit = _add_org(storage, SUB_UNIT_NAME, "unit", dept)
    sibling_unit = _add_org(storage, SIBLING_UNIT_NAME, "unit", dept)
    other_dept = _add_org(storage, "上海市静安区教育局", "department", other_district)
    other_unit = _add_org(storage, OTHER_UNIT_NAME, "unit", other_dept)
    # 另一个普陀区部门：用来验证"槽位可见、但任务可见性判断不了"的分支。
    local_dept = _add_org(storage, "上海市普陀区教育局", "department", district)
    local_unit = _add_org(storage, "上海市普陀区教育局第一小学", "unit", local_dept)

    return {
        "city": city,
        "district": district,
        "other_district": other_district,
        "dept": dept,
        "head_unit": head_unit,
        "sub_unit": sub_unit,
        "sibling_unit": sibling_unit,
        "other_dept": other_dept,
        "other_unit": other_unit,
        "local_dept": local_dept,
        "local_unit": local_unit,
    }


@pytest.fixture
def orgs(org_tree):
    """组织目录的别名：用例里统一用更短的参数名 orgs。"""
    return org_tree


@pytest.fixture
def fake_db() -> FakeSearchConnection:
    return FakeSearchConnection()


def _seed(fake_db: FakeSearchConnection, orgs: dict) -> None:
    fake_db.slots.extend(
        [
            make_slot_row(
                slot_id=SLOT_SUMMARY,
                slot_key="key-summary",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["dept"],
                subject_org_name=DEPT_NAME,
                subject_kind="department",
                material_scope="department_summary",
                fiscal_year=2024,
                report_kind="final",
                status="completed",
                status_reason="review_completed",
                current_document_version_id=None,
            ),
            make_slot_row(
                slot_id=SLOT_HEAD,
                slot_key="key-head",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["head_unit"],
                subject_org_name=HEAD_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2024,
                report_kind="final",
                status="review_required",
                status_reason="findings_pending",
                current_document_version_id=HEAD_V2,
            ),
            make_slot_row(
                slot_id=SLOT_SUB,
                slot_key="key-sub",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2025,
                report_kind="budget",
                status="uploaded",
                current_document_version_id=31,
            ),
            make_slot_row(
                slot_id=SLOT_SIBLING,
                slot_key="key-sibling",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sibling_unit"],
                subject_org_name=SIBLING_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2024,
                report_kind="final",
                status="missing",
                status_reason="due_reached_missing",
                current_document_version_id=None,
            ),
            make_slot_row(
                slot_id=SLOT_OTHER,
                slot_key="key-other",
                jurisdiction_org_id=orgs["other_district"],
                jurisdiction_name="上海市静安区",
                department_org_id=orgs["other_dept"],
                department_name="上海市静安区教育局",
                subject_org_id=orgs["other_unit"],
                subject_org_name=OTHER_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2024,
                report_kind="final",
                status="uploaded",
                current_document_version_id=None,
            ),
            make_slot_row(
                slot_id=SLOT_DENIED,
                slot_key="key-denied",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["local_dept"],
                department_name="上海市普陀区教育局",
                subject_org_id=orgs["local_unit"],
                subject_org_name="上海市普陀区教育局第一小学",
                subject_kind="unit",
                material_scope="unit_self",
                # 年度/文种刻意避开其它用例的断言组合（2023 预算）。
                fiscal_year=2023,
                report_kind="budget",
                status="uploaded",
                current_document_version_id=51,
            ),
        ]
    )
    fake_db.versions.extend(
        [
            make_version_record(
                document_version_id=HEAD_V1,
                slot_id=SLOT_HEAD,
                original_filename="old-final.pdf",
                created_at=LONG_AGO,
            ),
            make_version_record(
                document_version_id=HEAD_V2,
                slot_id=SLOT_HEAD,
                original_filename="current-final.pdf",
                created_at=EARLIER,
            ),
            make_version_record(
                document_version_id=31,
                slot_id=SLOT_SUB,
                original_filename="sub-budget.pdf",
                created_at=EARLIER,
            ),
            make_version_record(
                document_version_id=41,
                slot_id=SLOT_SIBLING,
                original_filename="sibling-final.pdf",
                created_at=EARLIER,
            ),
            make_version_record(
                document_version_id=51,
                slot_id=SLOT_DENIED,
                original_filename="primary-school-budget.pdf",
                created_at=EARLIER,
            ),
        ]
    )
    fake_db.jobs.extend(
        [
            # 当前版本上的运行：可以成为复核候选。
            make_job_record(
                job_uuid="job-current-001",
                document_version_id=HEAD_V2,
                status="done",
                organization_id=orgs["head_unit"],
                job_id=501,
                completed_at=NOW - timedelta(hours=2),
            ),
            # 历史版本上的运行：能被 job id 搜到，但不是复核候选（§四十七）。
            # 历史版本上的运行刻意"更近完成"：复核候选仍必须是当前版本的那一次。
            make_job_record(
                job_uuid="job-history-001",
                document_version_id=HEAD_V1,
                status="done",
                organization_id=orgs["head_unit"],
                job_id=500,
                completed_at=NOW,
            ),
            # 库里读不出组织归属的运行：槽位可见，但任务可见性判断不了。
            make_job_record(
                job_uuid="job-no-org-001",
                document_version_id=HEAD_V2,
                status="done",
                organization_id=None,
                job_id=502,
                completed_at=NOW - timedelta(hours=3),
            ),
            # 库里读不出组织归属、也没有 created_by：任何非管理员都判断不了可见性。
            make_job_record(
                job_uuid="job-unknown-owner-001",
                document_version_id=51,
                status="done",
                organization_id=None,
                job_id=601,
                completed_at=NOW,
            ),
            # legacy：没有精确关联字段，谁也不许把它猜到某个槽位上。
            make_job_record(
                job_uuid="job-legacy-001",
                document_version_id=None,
                status="done",
                organization_id=orgs["head_unit"],
                job_id=503,
            ),
        ]
    )


@pytest.fixture
def client(tmp_path, monkeypatch, fake_db, org_tree):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("USER_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "false")
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    _seed(fake_db, org_tree)

    async def _open():
        return fake_db

    async def _close(conn):  # noqa: ANN001 - 与生产签名一致
        return None

    monkeypatch.setattr(materials_routes, "_open_connection", _open)
    monkeypatch.setattr(materials_routes, "_close_connection", _close)
    reset_user_store()
    with TestClient(app) as test_client:
        yield test_client
    reset_user_store()


def _headers(session_token: str | None = None) -> dict:
    headers = {"X-API-Key": API_KEY}
    if session_token:
        headers["X-Session-Token"] = session_token
    return headers


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}, headers=_headers()
    )
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


def _admin(client: TestClient) -> str:
    return _login(client, "admin", ADMIN_PASSWORD)


def _scoped_user(client: TestClient, orgs, org_ids, name: str) -> str:
    admin = _admin(client)
    created = client.post(
        "/api/users",
        headers=_headers(admin),
        json={"username": name, "password": "UserPass123", "organization_ids": org_ids},
    )
    assert created.status_code == 200, created.text
    return _login(client, name, "UserPass123")


def _search(client: TestClient, token: str, query: str, **params):
    return client.get(
        "/api/materials/search",
        params={"q": query, **params},
        headers=_headers(token),
    )


def _slot_ids(payload) -> list:
    return [item["slot_id"] for item in payload["data"]["items"]]


# ==== 基本检索：字段、token 与结构化条件 ====================================


def test_search_by_department_name(client, fake_db):
    """主管部门名称搜索：部门汇总与本部的槽位都能被搜到（同一部门下的两条材料）。"""
    token = _admin(client)
    response = _search(client, token, "规划和自然资源局")
    assert response.status_code == 200, response.text
    body = response.json()
    # 部门名、"本部"单位名与直属单位名都含"规划和自然资源局"，
    # 四条槽位都该命中：搜索是"字段 OR"的模糊匹配，不做层级收窄。
    assert set(_slot_ids(body)) == {SLOT_SUMMARY, SLOT_HEAD, SLOT_SUB, SLOT_SIBLING}
    assert body["meta"]["pagination"]["total"] == 4


def test_search_by_district_name(client):
    """区县名称搜索。"""
    token = _admin(client)
    body = _search(client, token, "静安").json()
    assert _slot_ids(body) == [SLOT_OTHER]


def test_search_by_subject_name(client):
    """单位名称搜索。"""
    token = _admin(client)
    body = _search(client, token, "执法大队").json()
    assert _slot_ids(body) == [SLOT_SUB]


def test_search_by_current_filename(client):
    """当前文件名搜索：命中当前版本时明确标记 is_current。"""
    token = _admin(client)
    body = _search(client, token, "current-final.pdf").json()
    assert _slot_ids(body) == [SLOT_HEAD]
    item = body["data"]["items"][0]
    assert item["matched_filename"] == "current-final.pdf"
    assert item["matched_version_is_current"] is True
    assert item["current_filename"] == "current-final.pdf"
    assert item["matched_fields"] == ["current_filename"]


def test_search_by_historical_filename_keeps_current_pointer(client):
    """历史文件名可命中，但当前指针不变（§二十一/§二十二）。"""
    token = _admin(client)
    body = _search(client, token, "old-final.pdf").json()
    assert _slot_ids(body) == [SLOT_HEAD]
    item = body["data"]["items"][0]
    assert item["matched_filename"] == "old-final.pdf"
    assert item["matched_document_version_id"] == HEAD_V1
    assert item["matched_version_is_current"] is False
    assert item["current_document_version_id"] == HEAD_V2
    assert item["current_filename"] == "current-final.pdf"
    assert item["matched_fields"] == ["historical_filename"]


def test_fiscal_year_and_report_kind_tokens(client):
    """``2024 决算`` 解析为结构化条件，不当作普通文本（§十三/§十四）。"""
    token = _admin(client)
    body = _search(client, token, "2024 决算").json()
    assert set(_slot_ids(body)) == {SLOT_SUMMARY, SLOT_HEAD, SLOT_SIBLING, SLOT_OTHER}
    item = body["data"]["items"][0]
    assert item["matched_fields"] == ["fiscal_year", "report_kind"]

    body = _search(client, token, "2024 预算").json()
    assert _slot_ids(body) == []


def test_budget_and_final_aliases(client):
    """``budget`` / ``final`` 与中文别名同义（§十五）。"""
    token = _admin(client)
    assert set(_slot_ids(_search(client, token, "2025 budget").json())) == {SLOT_SUB}
    assert set(_slot_ids(_search(client, token, "2025 final").json())) == set()


def test_tokens_are_anded_across_fields(client):
    """多个 token 必须**全部**命中（字段之间 OR，token 之间 AND，§十九）。"""
    token = _admin(client)
    assert set(_slot_ids(_search(client, token, "普陀 执法大队").json())) == {SLOT_SUB}
    assert _slot_ids(_search(client, token, "普陀 静安区教育局").json()) == []


def test_same_name_department_and_head_unit_are_separated(client):
    """同名部门与本部单位不串：``规划和自然资源局 本部 2024 决算`` 只命中本部槽位（§十二）。

    反例的核心：部门汇总槽位的 subject 名与本部单位**完全同名**，
    只有"本部"这一维（复用 WP2-A 的 head-unit 语义）能把它们分开。
    """
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局 本部 2024 决算").json()
    assert _slot_ids(body) == [SLOT_HEAD]
    item = body["data"]["items"][0]
    assert item["subject_org_id"] != item["department_org_id"]
    assert item["relationship"] == "head_unit"
    assert "relationship" in item["matched_fields"]


def test_relationship_token_fails_closed_without_org_catalog(client, monkeypatch):
    """组织目录不可用时"本部"不给任何命中，并在 meta 里说明原因（§十八）。"""
    token = _admin(client)

    def _boom():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(materials_routes.runtime, "require_org_storage", _boom)
    body = _search(client, token, "规划和自然资源局 本部 2024 决算").json()
    assert body["data"]["items"] == []
    assert body["meta"]["relationship_resolution"] == "unavailable"


def test_relationship_resolution_is_reported_when_resolved(client):
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局 本部 2024 决算").json()
    assert body["meta"]["relationship_resolution"] == "resolved"


def test_relationship_not_requested_by_default(client):
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局").json()
    assert body["meta"]["relationship_resolution"] == "not_requested"


# ==== Job：精确关联、历史 job、legacy ======================================


def test_job_id_search_finds_material_with_review_candidate(client):
    """精确 job id 命中材料，并给出复核候选。"""
    token = _admin(client)
    body = _search(client, token, "job-current-001").json()
    assert _slot_ids(body) == [SLOT_HEAD]
    item = body["data"]["items"][0]
    assert item["matched_job_uuid"] == "job-current-001"
    assert item["matched_job_version_is_current"] is True
    assert item["review_candidate"] == {"job_uuid": "job-current-001", "status": "done"}


def test_historical_job_is_found_but_not_the_review_candidate(client):
    """历史版本上的 job 能被搜到，但复核候选仍是当前运行（§四十七）。"""
    token = _admin(client)
    body = _search(client, token, "job-history-001").json()
    assert _slot_ids(body) == [SLOT_HEAD]
    item = body["data"]["items"][0]
    assert item["matched_job_uuid"] == "job-history-001"
    assert item["matched_job_version_is_current"] is False
    assert item["review_candidate"]["job_uuid"] == "job-current-001"


def test_job_id_search_is_exact(client):
    """job id 只精确匹配：前缀搜不到（§二十三）。"""
    token = _admin(client)
    body = _search(client, token, "job-current").json()
    assert _slot_ids(body) == []


def test_legacy_unlinked_job_is_never_guessed_onto_a_slot(client):
    """没有精确关联字段的 legacy 任务不参与命中，也不猜槽位（§二十四）。"""
    token = _admin(client)
    body = _search(client, token, "job-legacy-001").json()
    assert body["data"]["items"] == []
    assert body["meta"]["legacy_unlinked_job_matches_excluded"] is True


def test_review_candidate_requires_job_access(client, orgs):
    """任务可见性判断不了时隐藏复核入口，但「打开材料」仍在（§二十六/§二十九）。

    同一份数据、同一个查询，两种账号得到两种结果：

    - 管理员：能判断任务可见（管理员恒可）-> 给出复核候选；
    - 部门范围的账号：库里读不出该任务的组织归属、也没有 ``created_by``，
      ``user_can_access_job`` 只能返回 False -> 隐藏复核入口，
      **但整条搜索结果仍然返回**（不能因为第二个动作不可用就吞掉第一条）。
    """
    admin = _admin(client)
    admin_body = _search(client, admin, "job-unknown-owner-001").json()
    assert _slot_ids(admin_body) == [SLOT_DENIED]
    assert admin_body["data"]["items"][0]["review_candidate"] == {
        "job_uuid": "job-unknown-owner-001",
        "status": "done",
    }

    token = _scoped_user(client, orgs, [orgs["local_dept"]], "local-dept-user")
    body = _search(client, token, "job-unknown-owner-001").json()
    assert _slot_ids(body) == [SLOT_DENIED]
    item = body["data"]["items"][0]
    assert item["matched_job_uuid"] == "job-unknown-owner-001"
    assert item["review_candidate"] is None


def test_review_candidate_reuses_job_access_for_scoped_user(client, orgs):
    """部门范围内的账号能拿到该部门任务的复核入口（权限判定复用既有实现）。"""
    token = _scoped_user(client, orgs, [orgs["dept"]], "dept-user-2")
    body = _search(client, token, "job-current-001").json()
    assert _slot_ids(body) == [SLOT_HEAD]
    assert body["data"]["items"][0]["review_candidate"]["job_uuid"] == "job-current-001"


# ==== 权限：无泄露、403 与空结果分得开 ======================================


def test_unit_scope_sees_only_its_own_slot(client, orgs):
    """单位范围内账号搜公共词：只返回自己的槽位，部门/本部/兄弟单位都不出现。"""
    token = _scoped_user(client, orgs, [orgs["sub_unit"]], "unit-user")
    body = _search(client, token, "规划和自然资源局", page_size=50).json()
    assert _slot_ids(body) == [SLOT_SUB]


def test_sibling_org_search_returns_nothing(client, orgs):
    """搜兄弟单位：0 条，且响应里不出现对方的名字/文件名/年份（§三十五）。"""
    token = _scoped_user(client, orgs, [orgs["sub_unit"]], "unit-user-2")
    response = _search(client, token, "事务中心")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert body["meta"]["pagination"]["total"] == 0
    # 只看 data：meta.query 回显的是用户自己输入的词，不算泄露。
    items_text = json.dumps(body["data"], ensure_ascii=False)
    assert "事务中心" not in items_text
    assert "sibling-final.pdf" not in items_text
    assert SLOT_SIBLING not in items_text


def test_other_district_search_returns_nothing(client, orgs):
    """别的区的内容一条都搜不到（连"存在"都不暴露）。"""
    token = _scoped_user(client, orgs, [orgs["district"]], "district-user")
    body = _search(client, token, "静安").json()
    assert body["data"]["items"] == []
    body = _search(client, token, "普陀 决算 2024").json()
    assert set(_slot_ids(body)) == {SLOT_SUMMARY, SLOT_HEAD, SLOT_SIBLING}


def test_account_without_any_scope_is_403(client, orgs):
    """一个区划都没授权的账号：403，与"有授权但没命中"的 200 空列表分开（§三十六）。"""
    token = _scoped_user(client, orgs, [], "no-scope-user")
    response = _search(client, token, "规划和自然资源局")
    assert response.status_code == 403
    assert "no organization authorized" in response.text


def test_authorized_but_no_match_is_200_empty(client):
    """有授权但没命中：200 + 空列表（不是 403、不是 404）。"""
    token = _admin(client)
    response = _search(client, token, "根本不存在的主体名称")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert body["meta"]["pagination"]["total"] == 0


def test_database_unavailable_is_503(client, monkeypatch):
    """数据库不可用：503（可恢复的运维状态），不是空数据、不是 500。"""
    from fastapi import HTTPException

    async def _boom():
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(materials_routes, "_open_connection", _boom)
    token = _admin(client)
    response = _search(client, token, "规划和自然资源局")
    assert response.status_code == 503
    assert HTTPException is not None


# ==== 参数校验 ==============================================================


@pytest.mark.parametrize(
    "query",
    ["", " ", "a", " a "],
)
def test_too_short_query_is_rejected(client, query):
    """空查询不返回全部材料：trim 后不足 2 个字符一律 422（§八）。"""
    token = _admin(client)
    response = _search(client, token, query)
    assert response.status_code == 422


def test_missing_query_is_rejected(client):
    token = _admin(client)
    response = client.get("/api/materials/search", headers=_headers(token))
    assert response.status_code == 422


def test_overlong_query_is_rejected(client):
    token = _admin(client)
    response = _search(client, token, "普" * 201)
    assert response.status_code == 422


def test_conflicting_year_tokens_are_rejected(client):
    """两个不同年度是自相矛盾的查询：422，而不是"取其一"。"""
    token = _admin(client)
    response = _search(client, token, "2023 2024 决算")
    assert response.status_code == 422
    assert "财政年度" in response.text


def test_conflicting_report_kinds_are_rejected(client):
    token = _admin(client)
    response = _search(client, token, "2024 预算 决算")
    assert response.status_code == 422
    assert "文种" in response.text


def test_too_many_text_tokens_are_rejected(client):
    """token 过多：422，不静默丢弃 token（丢弃会改变 AND 语义）。"""
    token = _admin(client)
    response = _search(client, token, " ".join(f"词{index}" for index in range(9)))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 0}, {"page_size": 51}, {"page": -1}],
)
def test_pagination_bounds(client, params):
    """``page >= 1``、``1 <= page_size <= 50``（§八/§六十六）。"""
    token = _admin(client)
    response = _search(client, token, "普陀", **params)
    assert response.status_code == 422


def test_page_size_upper_bound_is_50(client):
    token = _admin(client)
    assert _search(client, token, "普陀", page_size=50).status_code == 200


# ==== 通配符按普通字符处理 ==================================================


def test_wildcard_characters_are_literal(client):
    """``%`` / ``_`` 不作为用户自定义通配符：搜 ``%%`` 不得变成全库（§二十）。"""
    token = _admin(client)
    body = _search(client, token, "%%", page_size=50).json()
    assert body["data"]["items"] == []
    body = _search(client, token, "__", page_size=50).json()
    assert body["data"]["items"] == []


# ==== 响应契约与 meta =======================================================


def test_response_contract(client):
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局 本部 2024 决算").json()
    assert body["ok"] is True
    assert set(body) == {"ok", "data", "meta"}
    item = body["data"]["items"][0]
    for key in (
        "slot_id",
        "slot_key",
        "jurisdiction_id",
        "jurisdiction_name",
        "department_org_id",
        "department_name",
        "subject_org_id",
        "subject_org_name",
        "subject_kind",
        "material_scope",
        "relationship",
        "fiscal_year",
        "report_kind",
        "caliber",
        "status",
        "status_reason",
        "current_document_version_id",
        "current_filename",
        "matched_fields",
        "matched_filename",
        "matched_document_version_id",
        "matched_version_is_current",
        "matched_job_uuid",
        "matched_job_version_is_current",
        "review_candidate",
        "updated_at",
    ):
        assert key in item, f"搜索结果字段 {key} 缺失"

    meta = body["meta"]
    assert meta["data_basis"] == "existing_slots_only"
    assert meta["expected_materials_ready"] is False
    assert meta["linkage_basis"] == "structured_document_version_id"
    assert meta["legacy_unlinked_job_matches_excluded"] is True
    assert meta["query"] == "规划和自然资源局 本部 2024 决算"
    assert meta["pagination"] == {
        "page": 1,
        "page_size": 20,
        "total": 1,
        "total_pages": 1,
    }


def test_query_echo_is_trimmed(client):
    token = _admin(client)
    body = _search(client, token, "  执法大队  ").json()
    assert body["meta"]["query"] == "执法大队"


def test_pagination_meta_counts_pages(client):
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局", page=2, page_size=2).json()
    meta = body["meta"]["pagination"]
    assert meta["total"] == 4
    assert meta["total_pages"] == 2
    assert meta["page"] == 2
    assert len(body["data"]["items"]) == 2


def test_no_filesystem_access_for_search(client, fake_db, monkeypatch):
    """搜索不读文件系统：任何一次请求都不允许出现 job 目录访问（§二十七）。

    用一个"一旦被调用就失败"的 UPLOAD_ROOT 替身证明这一点。
    """

    class _ForbiddenRoot:
        def __truediv__(self, other):  # pragma: no cover - 命中即失败
            raise AssertionError("搜索请求不得访问 uploads/ 目录")

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", _ForbiddenRoot())
    token = _admin(client)
    assert _search(client, token, "规划和自然资源局").status_code == 200


# ==== SQL 预算：没有 N+1 ====================================================


def test_search_statement_count_is_two(client, fake_db):
    """主查询 + 当前页明细，一共两条语句；结果条数不影响语句数（§三十二）。"""
    fake_db.statements.clear()
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局", page_size=50).json()
    assert len(body["data"]["items"]) == 4
    assert len(fake_db.statements) == 2
    assert "material_search:main" in fake_db.statements[0][0]
    assert "material_search:page" in fake_db.statements[1][0]


def test_out_of_range_page_uses_one_extra_count(client, fake_db):
    """越界页：补一条计数以给出真实 total（这是唯一允许的额外 SQL）。

    两条语句 = 主查询 + 兜底计数：本页没有任何槽位，因此**不发**当前页明细
    （明细按槽位数组取数，空数组不必打扰数据库）。
    """
    fake_db.statements.clear()
    token = _admin(client)
    body = _search(client, token, "规划和自然资源局", page=9, page_size=2).json()
    assert body["data"]["items"] == []
    assert body["meta"]["pagination"]["total"] == 4
    assert len(fake_db.statements) == 2
    assert "material_search:main" in fake_db.statements[0][0]
    assert "material_search:count" in fake_db.statements[1][0]
