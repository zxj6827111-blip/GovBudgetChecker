"""材料台账 API（WP2-A）契约、隔离与鉴权测试。

为什么用假连接而不是真库
------------------------
本仓库测试默认不连数据库（见 ``tests/conftest.py``）。这里用
``tests/support_material_ledger_db.py`` 的假连接走**完整**路径
（路由 → 服务 → SQL 条件 → 聚合 → 响应契约），它按 WHERE 真过滤、按 GROUP BY
真聚合，因此"筛选条件漏接线""SQL 少选一列""一个接口跑了几条 SQL"都能被发现。
真库上才有的行为（表达式索引、CHECK 约束、并发）不在本文件的覆盖范围内。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from api.routes import materials as materials_routes
from src.schemas.material_ledger import MATERIAL_STATUSES
from src.services import org_storage as org_storage_module
from src.services.user_store import reset_user_store
from support_material_ledger_db import FakeLedgerConnection, make_slot

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "AdminPass123"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=30)

DEPT_NAME = "上海市普陀区规划和自然资源局"
HEAD_UNIT_NAME = "上海市普陀区规划和自然资源局本级"
SUB_UNIT_NAME = "上海市普陀区规划和自然资源局执法大队"


# ---- 夹具 -------------------------------------------------------------------


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
    """临时组织目录：一个市、两个区，其中普陀区下有两个部门。"""
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
    dept2 = _add_org(storage, "上海市普陀区民政局", "department", district)
    other_dept = _add_org(storage, "上海市静安区教育局", "department", other_district)

    return {
        "city": city,
        "district": district,
        "other_district": other_district,
        "dept": dept,
        "head_unit": head_unit,
        "sub_unit": sub_unit,
        "dept2": dept2,
        "other_dept": other_dept,
    }


@pytest.fixture
def fake_db() -> FakeLedgerConnection:
    return FakeLedgerConnection()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_db, org_tree):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setenv("USER_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "false")
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)

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


def _headers(session_token: str | None = None) -> dict[str, str]:
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


def _create_user(
    client: TestClient, admin_token: str, username: str, password: str, organization_ids: list[str]
) -> None:
    response = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={"username": username, "password": password, "organization_ids": organization_ids},
    )
    assert response.status_code == 200, response.text


def _seed_baseline(fake_db: FakeLedgerConnection, orgs: dict) -> None:
    """一份覆盖主要形态的数据：部门汇总 / 本部 / 直属 / 另一部门 / 另一区 / 无区划。"""
    fake_db.slots.extend(
        [
            make_slot(
                id="slot-summary-budget",
                slot_key="key-summary-budget",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["dept"],
                subject_org_name=DEPT_NAME,
                subject_kind="department",
                material_scope="department_summary",
                report_kind="budget",
                status="completed",
                status_reason="review_completed",
                caliber="summary",
                due_at=NOW + timedelta(days=10),
                current_document_version_id=11,
                updated_at=NOW,
            ),
            make_slot(
                id="slot-head-final",
                slot_key="key-head-final",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["head_unit"],
                subject_org_name=HEAD_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                report_kind="final",
                status="review_required",
                status_reason="findings_pending",
                updated_at=EARLIER,
            ),
            make_slot(
                id="slot-sub-budget",
                slot_key="key-sub-budget",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                report_kind="budget",
                status="missing",
                status_reason="due_exceeded",
                due_at=NOW - timedelta(days=1),
                updated_at=EARLIER,
            ),
            make_slot(
                id="slot-dept2-budget",
                slot_key="key-dept2-budget",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept2"],
                department_name="上海市普陀区民政局",
                subject_org_id=orgs["dept2"],
                subject_org_name="上海市普陀区民政局",
                subject_kind="department",
                material_scope="department_summary",
                report_kind="budget",
                status="uploaded",
                status_reason="awaiting_analysis",
                updated_at=EARLIER,
            ),
            make_slot(
                id="slot-other-district",
                slot_key="key-other-district",
                jurisdiction_org_id=orgs["other_district"],
                jurisdiction_name="上海市静安区",
                department_org_id=orgs["other_dept"],
                department_name="上海市静安区教育局",
                subject_org_id=orgs["other_dept"],
                subject_org_name="上海市静安区教育局",
                subject_kind="department",
                material_scope="department_summary",
                report_kind="final",
                status="uploaded",
                status_reason="awaiting_analysis",
                updated_at=EARLIER,
            ),
            make_slot(
                id="slot-no-jurisdiction",
                slot_key="key-no-jurisdiction",
                mapping_key="doc:deadbeef",
                subject_org_id="unresolved:abc",
                subject_org_name="某份未识别材料",
                subject_kind="unknown",
                material_scope="unknown",
                report_kind="unknown",
                status="mapping_required",
                status_reason="identity_unresolved",
                updated_at=EARLIER,
            ),
        ]
    )


@pytest.fixture
def ledger(fake_db, org_tree) -> FakeLedgerConnection:
    _seed_baseline(fake_db, org_tree)
    return fake_db


# ---- 接口一：首页 coverage --------------------------------------------------


def test_coverage_on_empty_database_reports_explicit_zeros_and_no_districts(client):
    body = client.get("/api/materials/coverage").json()

    assert body["ok"] is True
    assert body["meta"]["data_basis"] == "existing_slots_only"
    assert body["meta"]["expected_materials_ready"] is False
    summary = body["data"]["summary"]
    assert summary["slot_total"] == 0
    assert set(summary["status_counts"]) == set(MATERIAL_STATUSES)
    assert all(count == 0 for count in summary["status_counts"].values()), "空表是已计算的零"
    assert summary["due_at_unknown"] == 0
    assert summary["jurisdiction_unknown_total"] == 0
    assert body["data"]["districts"] == []


def test_coverage_counts_every_status_and_sums_to_slot_total(client, fake_db, org_tree):
    for index, status in enumerate(MATERIAL_STATUSES):
        fake_db.slots.append(
            make_slot(
                id=f"slot-{status}",
                slot_key=f"key-{status}",
                jurisdiction_org_id=org_tree["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=org_tree["dept"],
                department_name=DEPT_NAME,
                status=status,
                report_kind="budget",
                updated_at=NOW,
            )
        )

    summary = client.get("/api/materials/coverage").json()["data"]["summary"]

    assert summary["status_counts"] == {status: 1 for status in MATERIAL_STATUSES}
    assert sum(summary["status_counts"].values()) == summary["slot_total"] == len(MATERIAL_STATUSES)


def test_coverage_separates_budget_final_and_unknown_kind(client, ledger):
    summary = client.get("/api/materials/coverage").json()["data"]["summary"]

    assert summary["slot_total"] == 6
    assert summary["budget_total"] == 3
    assert summary["final_total"] == 2
    assert summary["unknown_kind_total"] == 1, "文种未识别的槽位既不算预算也不算决算"
    assert summary["budget_total"] + summary["final_total"] + summary["unknown_kind_total"] == 6
    assert summary["status_counts"]["missing"] == 1
    assert summary["status_counts"]["completed"] == 1
    assert summary["status_counts"]["mapping_required"] == 1


def test_coverage_isolates_fiscal_years(client, fake_db, org_tree):
    _seed_baseline(fake_db, org_tree)
    fake_db.slots.append(
        make_slot(
            id="slot-2024",
            slot_key="key-2024",
            jurisdiction_org_id=org_tree["district"],
            jurisdiction_name="上海市普陀区",
            department_org_id=org_tree["dept"],
            department_name=DEPT_NAME,
            fiscal_year=2024,
            status="uploaded",
            updated_at=EARLIER,
        )
    )

    only_2025 = client.get("/api/materials/coverage?fiscal_year=2025").json()["data"]["summary"]
    only_2024 = client.get("/api/materials/coverage?fiscal_year=2024").json()["data"]["summary"]
    all_years = client.get("/api/materials/coverage").json()["data"]["summary"]

    assert only_2025["slot_total"] == 6
    assert only_2024["slot_total"] == 1
    assert all_years["slot_total"] == 7, "不带年度筛选时跨年度累计"


def test_coverage_rejects_out_of_range_fiscal_year(client):
    assert client.get("/api/materials/coverage?fiscal_year=1999").status_code == 422
    assert client.get("/api/materials/coverage?fiscal_year=2100").status_code == 422


def test_coverage_district_cards_exclude_and_count_jurisdiction_less_slots(client, ledger):
    data = client.get("/api/materials/coverage").json()["data"]

    assert [item["district_name"] for item in data["districts"]] == ["上海市普陀区", "上海市静安区"]
    assert data["summary"]["jurisdiction_unknown_total"] == 1
    assert sum(item["slot_total"] for item in data["districts"]) == 5


def test_coverage_district_ordering_is_stable_when_rows_come_back_shuffled(client, fake_db, org_tree):
    _seed_baseline(fake_db, org_tree)
    first = client.get("/api/materials/coverage").json()["data"]

    fake_db.slots.reverse()
    second = client.get("/api/materials/coverage").json()["data"]

    assert first == second, "同一份数据无论返回顺序如何，响应体必须完全一致"


def test_coverage_due_at_unknown_is_counted_separately_from_not_due(client, fake_db, org_tree):
    _seed_baseline(fake_db, org_tree)
    fake_db.slots.append(
        make_slot(
            id="slot-not-due-unknown-due",
            slot_key="key-not-due-unknown-due",
            jurisdiction_org_id=org_tree["district"],
            jurisdiction_name="上海市普陀区",
            department_org_id=org_tree["dept"],
            department_name=DEPT_NAME,
            status="not_due",
            status_reason="due_at_unknown",
            due_at=None,
            updated_at=EARLIER,
        )
    )

    summary = client.get("/api/materials/coverage").json()["data"]["summary"]

    assert summary["status_counts"]["not_due"] == 1
    assert summary["due_at_unknown"] == 5, "截止时间未知单独计数，不能被读成'尚未到期'"


def test_coverage_filters_by_status_and_report_kind(client, ledger):
    summary = client.get("/api/materials/coverage?status=missing&report_kind=budget").json()["data"][
        "summary"
    ]

    assert summary["slot_total"] == 1
    assert summary["status_counts"]["missing"] == 1
    assert summary["budget_total"] == 1
    assert summary["final_total"] == 0


def test_coverage_meta_timestamps_are_iso8601_utc(client):
    meta = client.get("/api/materials/coverage").json()["meta"]

    assert meta["generated_at"].endswith("Z"), "时间字段统一 UTC ISO 8601，不混用本地格式"
    datetime.fromisoformat(meta["generated_at"].replace("Z", "+00:00"))


def test_coverage_uses_at_most_two_sql_statements(client, ledger, fake_db):
    fake_db.calls.clear()

    client.get("/api/materials/coverage")

    assert fake_db.sql_count == 2, "全局汇总与区县分组各一条，不做 N+1"


# ---- 接口二：区级主管部门矩阵 ------------------------------------------------


def test_district_departments_keeps_same_name_department_and_unit_separate(client, ledger, org_tree):
    body = client.get(f"/api/materials/districts/{org_tree['district']}/departments").json()

    assert body["data"]["district"]["district_name"] == "上海市普陀区"
    departments = {item["department_id"]: item for item in body["data"]["items"]}
    assert set(departments) == {org_tree["dept"], org_tree["dept2"]}
    dept = departments[org_tree["dept"]]
    assert dept["subject_count"] == 3, "部门本身 + 本部单位 + 直属单位，按 id 计数不按名称归并"
    assert dept["slot_total"] == 3
    assert dept["budget"]["slot_total"] == 2
    assert dept["final"]["slot_total"] == 1
    assert dept["budget"]["status_counts"]["missing"] == 1
    assert dept["final"]["status_counts"]["review_required"] == 1
    assert dept["missing"] == 1 and dept["not_due"] == 0
    assert dept["due_at_unknown"] == 1


def test_district_departments_isolates_other_districts(client, org_tree):
    body = client.get(f"/api/materials/districts/{org_tree['district']}/departments").json()

    department_ids = {item["department_id"] for item in body["data"]["items"]}
    assert org_tree["other_dept"] not in department_ids
    assert "上海市静安区教育局" not in {item["department_name"] for item in body["data"]["items"]}


def test_district_departments_pagination_contract(client, ledger, org_tree):
    base = f"/api/materials/districts/{org_tree['district']}/departments"
    first = client.get(f"{base}?page=1&page_size=1").json()
    second = client.get(f"{base}?page=2&page_size=1").json()

    assert first["meta"]["pagination"] == {"page": 1, "page_size": 1, "total": 2, "total_pages": 2}
    assert second["meta"]["pagination"] == {"page": 2, "page_size": 1, "total": 2, "total_pages": 2}
    # 排序按 Unicode 码点：民(U+6C11) 在 规(U+89C4) 之前，因此第一页是民政局。
    assert first["data"]["items"][0]["department_name"] == "上海市普陀区民政局"
    assert len(second["data"]["items"]) == 1
    assert second["data"]["items"][0]["department_name"] == DEPT_NAME
    assert first["data"]["items"][0]["department_id"] != second["data"]["items"][0]["department_id"]


def test_district_departments_rejects_invalid_pagination(client, org_tree):
    base = f"/api/materials/districts/{org_tree['district']}/departments"
    assert client.get(f"{base}?page=0").status_code == 422
    assert client.get(f"{base}?page_size=0").status_code == 422
    assert client.get(f"{base}?page_size=101").status_code == 422


def test_district_departments_search_matches_department_name(client, ledger, org_tree):
    base = f"/api/materials/districts/{org_tree['district']}/departments"

    matched = client.get(f"{base}?q=民政").json()
    empty = client.get(f"{base}?q=不存在的部门").json()

    assert [item["department_name"] for item in matched["data"]["items"]] == ["上海市普陀区民政局"]
    assert matched["meta"]["pagination"]["total"] == 1
    assert empty["data"]["items"] == []
    assert empty["meta"]["pagination"]["total"] == 0
    assert empty["meta"]["pagination"]["total_pages"] == 0


def test_district_departments_rejects_invalid_status(client, ledger, org_tree):
    response = client.get(
        f"/api/materials/districts/{org_tree['district']}/departments?status=pending_review"
    )

    assert response.status_code == 422, "非法状态必须校验失败，不能静默忽略"


def test_district_departments_known_but_empty_is_200_with_null_coverage(client, ledger, org_tree):
    """区划存在、该年度没有材料：200 + 空列表 + 完整率 null（不是 404，也不是 0）。"""
    response = client.get(
        f"/api/materials/districts/{org_tree['district']}/departments?fiscal_year=2024"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert body["data"]["district"]["district_name"] == "上海市普陀区"
    assert body["meta"]["expected_materials_ready"] is False


def test_district_departments_unknown_district_is_404(client, ledger):
    response = client.get("/api/materials/districts/not-a-district/departments")

    assert response.status_code == 404


def test_district_departments_annual_and_kind_filters_are_isolated(client, org_tree, fake_db):
    _seed_baseline(fake_db, org_tree)
    base = f"/api/materials/districts/{org_tree['district']}/departments"

    only_budget = client.get(f"{base}?report_kind=budget").json()
    only_2024 = client.get(f"{base}?fiscal_year=2024").json()

    assert {item["department_id"] for item in only_budget["data"]["items"]} == {
        org_tree["dept"],
        org_tree["dept2"],
    }
    # 只看预算时，部门行的决算列必须是真实的零，而不是被算进预算里
    dept = next(
        item for item in only_budget["data"]["items"] if item["department_id"] == org_tree["dept"]
    )
    assert dept["final"]["slot_total"] == 0
    assert dept["budget"]["slot_total"] == 2
    assert only_2024["data"]["items"] == []


def test_coverage_district_updated_at_reflects_newest_slot(client, fake_db, org_tree):
    """区县卡片的"更新时间"必须真的来自槽位（曾经漏选 MAX(updated_at) 会恒为 null）。

    这条断言由真库用例先发现（tests/test_material_ledger_pg.py），这里补上同口径的
    快速回归：一旦 SQL 再漏掉这一列，无需连库即可失败。
    """
    _seed_baseline(fake_db, org_tree)
    fake_db.slots.append(
        make_slot(
            id="slot-newer",
            slot_key="key-newer",
            jurisdiction_org_id=org_tree["district"],
            jurisdiction_name="上海市普陀区",
            department_org_id=org_tree["dept"],
            department_name=DEPT_NAME,
            status="uploaded",
            updated_at=NOW + timedelta(days=3),
        )
    )

    districts = client.get("/api/materials/coverage").json()["data"]["districts"]
    putuo = next(item for item in districts if item["district_id"] == org_tree["district"])

    assert putuo["updated_at"] is not None
    assert putuo["updated_at"].startswith("2026-09-24"), "取的是该区最新一次更新时间"


def test_district_departments_updated_at_reflects_newest_slot(client, ledger, org_tree):
    items = client.get(f"/api/materials/districts/{org_tree['district']}/departments").json()["data"][
        "items"
    ]

    assert items, "测试数据非空"
    for item in items:
        assert item["updated_at"] is not None, "部门行的更新时间不能恒为 null"


def test_district_departments_uses_one_sql_statement(client, ledger, fake_db, org_tree):
    fake_db.calls.clear()

    client.get(f"/api/materials/districts/{org_tree['district']}/departments")

    assert fake_db.sql_count == 1


# ---- 接口三：部门材料矩阵 ----------------------------------------------------


def test_department_matrix_groups_summary_head_unit_and_subordinate(client, ledger, org_tree):
    body = client.get(f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025").json()

    assert body["data"]["department"]["department_name"] == DEPT_NAME
    assert body["data"]["department"]["jurisdiction_name"] == "上海市普陀区"
    assert body["data"]["fiscal_year"] == 2025

    groups = body["data"]["groups"]
    assert [row["subject_org_id"] for row in groups["department_summary"]] == [org_tree["dept"]]
    assert [row["subject_org_id"] for row in groups["head_unit"]] == [org_tree["head_unit"]]
    assert [row["subject_org_id"] for row in groups["subordinate_units"]] == [org_tree["sub_unit"]]
    assert groups["relationship_unknown"] == []
    assert groups["head_unit"][0]["relationship"] == "head_unit"
    assert groups["head_unit"][0]["subject_kind"] == "unit"


def test_department_matrix_keeps_budget_and_final_in_their_own_columns(client, ledger, org_tree):
    groups = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]

    summary_row = groups["department_summary"][0]
    assert summary_row["budget"]["exists"] is True
    assert summary_row["budget"]["slot"]["report_kind"] == "budget"
    assert summary_row["budget"]["slot"]["status"] == "completed"
    assert summary_row["final"]["exists"] is False and summary_row["final"]["slot"] is None

    head_row = groups["head_unit"][0]
    assert head_row["final"]["exists"] is True
    assert head_row["final"]["slot"]["report_kind"] == "final"
    assert head_row["final"]["slot"]["status"] == "review_required"
    assert head_row["budget"]["exists"] is False, "决算槽位不得出现在预算列"


def test_department_matrix_surfaces_status_reason_and_caliber_conflict(client, ledger, fake_db, org_tree):
    _seed_baseline(fake_db, org_tree)
    fake_db.slots.append(
        make_slot(
            id="slot-caliber-conflict",
            slot_key="key-caliber-conflict",
            jurisdiction_org_id=org_tree["district"],
            jurisdiction_name="上海市普陀区",
            department_org_id=org_tree["dept"],
            department_name=DEPT_NAME,
            subject_org_id=org_tree["sub_unit"],
            subject_org_name=SUB_UNIT_NAME,
            subject_kind="unit",
            material_scope="unit_self",
            report_kind="final",
            status="mapping_required",
            status_reason="caliber_conflict",
            caliber="summary",
            caliber_conflict_candidate="self",
            updated_at=NOW,
        )
    )

    row = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]["subordinate_units"][0]

    slot = row["final"]["slot"]
    assert slot["status"] == "mapping_required"
    assert slot["status_reason"] == "caliber_conflict"
    assert slot["caliber"] == "summary"
    assert slot["caliber_conflict_candidate"] == "self", "口径冲突必须透出，不能只显示当前口径"


def test_department_matrix_isolates_fiscal_year_and_reports_empty_state(client, ledger, org_tree):
    response = client.get(f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2024")

    assert response.status_code == 200
    groups = response.json()["data"]["groups"]
    assert groups == {
        "department_summary": [],
        "head_unit": [],
        "subordinate_units": [],
        "relationship_unknown": [],
    }, "部门存在但该年度没有槽位：空态而不是 404，也不是别的年度的数据"


def test_department_matrix_requires_fiscal_year(client, ledger, org_tree):
    response = client.get(f"/api/materials/departments/{org_tree['dept']}/matrix")

    assert response.status_code == 422, "财政年度必填，不得暗中使用当前自然年"


def test_department_matrix_unknown_department_is_404(client, ledger):
    assert (
        client.get("/api/materials/departments/not-a-department/matrix?fiscal_year=2025").status_code
        == 404
    )


def test_department_matrix_unknown_kind_slot_is_not_dropped(client, ledger, org_tree):
    groups = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]

    assert groups["relationship_unknown"] == [], "无区划的未识别槽位不属于任何部门，不进本矩阵"
    for rows in groups.values():
        for row in rows:
            assert row["unclassified"]["exists"] is False


def test_department_matrix_unknown_kind_slot_appears_as_unclassified(client, fake_db, org_tree):
    _seed_baseline(fake_db, org_tree)
    fake_db.slots.append(
        make_slot(
            id="slot-kind-unknown",
            slot_key="key-kind-unknown",
            mapping_key="doc:cafe",
            jurisdiction_org_id=org_tree["district"],
            jurisdiction_name="上海市普陀区",
            department_org_id=org_tree["dept"],
            department_name=DEPT_NAME,
            subject_org_id=org_tree["sub_unit"],
            subject_org_name=SUB_UNIT_NAME,
            subject_kind="unit",
            material_scope="unit_self",
            report_kind="unknown",
            fiscal_year=2025,
            status="mapping_required",
            status_reason="identity_unresolved",
            updated_at=NOW,
        )
    )

    row = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]["subordinate_units"][0]

    assert row["unclassified"]["exists"] is True
    assert row["unclassified"]["slot"]["report_kind"] == "unknown"
    assert row["unclassified"]["slot"]["status"] == "mapping_required"


def test_department_matrix_formal_issue_count_is_null(client, ledger, org_tree):
    groups = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]

    for rows in groups.values():
        for row in rows:
            for key in ("budget", "final", "unclassified"):
                slot = row[key]["slot"]
                if slot is not None:
                    assert slot["formal_issue_count"] is None, "复核闭环未接通前为 null，禁止用 0"


def test_department_matrix_applicability_note_is_exposed(client, fake_db, org_tree):
    _seed_baseline(fake_db, org_tree)
    fake_db.slots.append(
        make_slot(
            id="slot-not-applicable",
            slot_key="key-not-applicable",
            jurisdiction_org_id=org_tree["district"],
            jurisdiction_name="上海市普陀区",
            department_org_id=org_tree["dept"],
            department_name=DEPT_NAME,
            subject_org_id=org_tree["sub_unit"],
            subject_org_name=SUB_UNIT_NAME,
            subject_kind="unit",
            material_scope="unit_self",
            report_kind="final",
            status="not_applicable",
            status_reason="applicability_marked_not_applicable",
            applicability_status="not_applicable",
            applicability_note="本单位当年度无三公经费预算",
            updated_at=NOW,
        )
    )

    row = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]["subordinate_units"][0]

    slot = row["final"]["slot"]
    assert slot["applicability_status"] == "not_applicable"
    assert slot["applicability_note"] == "本单位当年度无三公经费预算"


def test_department_matrix_uses_one_sql_statement(client, ledger, fake_db, org_tree):
    fake_db.calls.clear()

    client.get(f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025")

    assert fake_db.sql_count == 1


def test_department_matrix_sql_does_not_sort_in_database(client, ledger, fake_db, org_tree):
    """代表槽位的选取规则只在 Python 一处（``_slot_preference``），SQL 不做排序。"""
    fake_db.calls.clear()

    client.get(f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025")

    sql = fake_db.calls[0][0]
    assert "ORDER BY" not in sql


# ---- 期望字段语义 -----------------------------------------------------------


EXPECTED_KEYS = (
    "expected_total",
    "expected_budget_total",
    "expected_final_total",
    "coverage_rate",
    "budget_coverage_rate",
    "final_coverage_rate",
    "missing_expected_total",
)


def test_expected_and_coverage_fields_are_null_never_zero(client, ledger, org_tree):
    coverage = client.get("/api/materials/coverage").json()["data"]
    district = client.get(
        f"/api/materials/districts/{org_tree['district']}/departments"
    ).json()["data"]

    for payload in [coverage["summary"], *coverage["districts"], *district["items"]]:
        for key in EXPECTED_KEYS:
            assert key in payload, f"{key} 必须以显式 null 出现，避免被读成 0"
            assert payload[key] is None


def test_department_matrix_has_no_expected_fields_in_slot_summary(client, ledger, org_tree):
    groups = client.get(
        f"/api/materials/departments/{org_tree['dept']}/matrix?fiscal_year=2025"
    ).json()["data"]["groups"]

    slot = groups["department_summary"][0]["budget"]["slot"]
    # 槽位摘要描述一份材料，不承载"区域完整率"这类统计字段：
    # 混进来会让同一个数字在两个层级出现两种口径。
    for key in EXPECTED_KEYS:
        assert key not in slot


# ---- 鉴权 -------------------------------------------------------------------


def test_admin_sees_all_districts(client, ledger):
    data = client.get("/api/materials/coverage").json()["data"]

    assert {item["district_name"] for item in data["districts"]} == {"上海市普陀区", "上海市静安区"}


def test_authorized_district_user_sees_only_own_district(client, ledger, org_tree):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    _create_user(
        client, admin_token, "pt_viewer", "PtViewer123", [org_tree["district"]]
    )
    token = _login(client, "pt_viewer", "PtViewer123")

    body = client.get("/api/materials/coverage", headers=_headers(token)).json()

    assert [item["district_name"] for item in body["data"]["districts"]] == ["上海市普陀区"]
    assert body["data"]["summary"]["slot_total"] == 4, "统计范围必须与可见区划一致"
    assert body["data"]["summary"]["jurisdiction_unknown_total"] == 0


def test_authorized_district_user_can_open_own_district_matrix(client, ledger, org_tree):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    _create_user(client, admin_token, "pt_reader", "PtReader123", [org_tree["district"]])
    token = _login(client, "pt_reader", "PtReader123")

    response = client.get(
        f"/api/materials/districts/{org_tree['district']}/departments", headers=_headers(token)
    )

    assert response.status_code == 200


def test_out_of_scope_district_is_403(client, ledger, org_tree):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    _create_user(client, admin_token, "pt_only", "PtOnly12345", [org_tree["district"]])
    token = _login(client, "pt_only", "PtOnly12345")

    response = client.get(
        f"/api/materials/districts/{org_tree['other_district']}/departments",
        headers=_headers(token),
    )

    assert response.status_code == 403


def test_out_of_scope_department_is_403(client, ledger, org_tree):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    _create_user(client, admin_token, "pt_dept", "PtDept12345", [org_tree["district"]])
    token = _login(client, "pt_dept", "PtDept12345")

    response = client.get(
        f"/api/materials/departments/{org_tree['other_dept']}/matrix?fiscal_year=2025",
        headers=_headers(token),
    )

    assert response.status_code == 403


def test_user_without_any_scope_is_403_on_coverage(client, ledger):
    admin_token = _login(client, "admin", ADMIN_PASSWORD)
    _create_user(client, admin_token, "nobody", "Nobody12345", [])
    token = _login(client, "nobody", "Nobody12345")

    response = client.get("/api/materials/coverage", headers=_headers(token))

    assert response.status_code == 403, "没有授权范围时必须明确拒绝，而不是返回一片空白"


def test_missing_session_token_returns_401(client, ledger, monkeypatch):
    monkeypatch.setenv("TESTING", "false")

    assert client.get("/api/materials/coverage").status_code == 401
    assert (
        client.get("/api/materials/districts/whatever/departments").status_code == 401
    )


# ---- 可用性 -----------------------------------------------------------------


def test_database_unavailable_returns_503(client, ledger, monkeypatch):
    async def _boom():
        raise RuntimeError("no pool")

    monkeypatch.setattr(materials_routes, "_open_connection", _boom)

    response = client.get("/api/materials/coverage")

    assert response.status_code == 503
