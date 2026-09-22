"""材料详情 API（WP2-B）契约、鉴权与 IDOR 测试。

为什么用假连接
--------------
本仓库测试默认不连数据库（见 ``tests/conftest.py``）。这里用
``tests/support_material_detail_db.py`` 的假连接走**完整**路径
（路由 → 授权 → 服务 → SQL 条件 → 投影 → 响应契约），它真的按 WHERE 过滤、
真的做 LEFT JOIN 的三态，因此"权限谓词漏接线""版本与运行关联错列"
"storage_key 被带出去"这类缺陷都能被发现。

真库上才有的行为（表达式索引、JSONB 存储形态、跨表 JOIN 的 NULL 语义）
由 ``tests/test_material_ledger_detail_pg.py`` 覆盖。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from api.routes import materials as materials_routes
from src.services import org_storage as org_storage_module
from src.services.user_store import reset_user_store
from support_material_detail_db import (
    FakeDetailConnection,
    make_job_row,
    make_result_row,
    make_slot_row,
    make_source_row,
    make_version_row,
)

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
ADMIN_PASSWORD = "AdminPass123"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=2)
PAST_DUE = NOW - timedelta(days=30)

DEPT_NAME = "上海市普陀区规划和自然资源局"
HEAD_UNIT_NAME = "上海市普陀区规划和自然资源局本级"
SUB_UNIT_NAME = "上海市普陀区规划和自然资源局执法大队"
SIBLING_UNIT_NAME = "上海市普陀区规划和自然资源局事务中心"

SLOT_MAIN = "slot-main"          # 本单位（可见）的槽位
SLOT_SIBLING = "slot-sibling"    # 兄弟单位（不可见）的槽位
SLOT_MISSING = "slot-missing"    # 真实 missing 的槽位
SLOT_NOT_DUE = "slot-not-due"    # due_at_unknown 的槽位
SLOT_NO_KIND = "slot-no-kind"    # 文种未识别
SLOT_NO_YEAR = "slot-no-year"    # 财政年度未识别
HEAD_UNIT_ID = "unit-head"
SUB_UNIT_ID = "unit-sub"
SIBLING_UNIT_ID = "unit-sibling"


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
    """临时组织目录：市 / 区 / 部门 / 三个单位。

    三个单位故意同名或带"本级/本部"标志：时间轴的身份判定必须只看 id，
    名称相同不能把两个主体混起来（§十三）。
    """
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

    return {
        "city": city,
        "district": district,
        "other_district": other_district,
        "dept": dept,
        "head_unit": head_unit,
        "sub_unit": sub_unit,
        "sibling_unit": sibling_unit,
        "other_dept": other_dept,
    }


@pytest.fixture
def fake_db() -> FakeDetailConnection:
    return FakeDetailConnection()


def _seed(fake_db: FakeDetailConnection, orgs: dict) -> None:
    """一份覆盖主要形态的数据。

    - ``SLOT_MAIN``：本单位 2024 年度决算，V1/V2 两个版本，V2 是当前版本，
      V2 有分析结果（1 个正式问题 + 1 个信息提示）；V1 也有分析结果（不同的问题数）
      —— 用来验证"历史版本的分析不冒充当前结论"；
    - ``SLOT_SIBLING``：兄弟单位的槽位（用来验证 IDOR 与范围过滤）；
    - ``SLOT_MISSING``：真实 missing（到期未上传、没有当前文件版本、有历史版本）；
    - ``SLOT_NOT_DUE``：``not_due`` + ``due_at_unknown``；
    - ``SLOT_NO_KIND``：文种未识别（必须出现在 unclassified）；
    - ``SLOT_NO_YEAR``：财政年度未识别（必须进 unresolved_year_slots）。
    """
    fake_db.slots.extend(
        [
            make_slot_row(
                id=SLOT_MAIN,
                slot_key="key-main",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2024,
                report_kind="final",
                caliber="self",
                status="review_required",
                status_reason="findings_pending",
                due_at=NOW + timedelta(days=5),
                current_document_version_id=22,
                updated_at=NOW,
            ),
            make_slot_row(
                id=SLOT_SIBLING,
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
                status="uploaded",
                status_reason="awaiting_analysis",
                current_document_version_id=99,
                updated_at=NOW,
            ),
            make_slot_row(
                id=SLOT_MISSING,
                slot_key="key-missing",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2025,
                report_kind="final",
                status="missing",
                status_reason="due_exceeded",
                due_at=PAST_DUE,
                current_document_version_id=None,
                updated_at=NOW,
            ),
            make_slot_row(
                id=SLOT_NOT_DUE,
                slot_key="key-not-due",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2026,
                report_kind="budget",
                status="not_due",
                status_reason="due_at_unknown",
                due_at=None,
                current_document_version_id=None,
                updated_at=NOW,
            ),
            make_slot_row(
                id=SLOT_NO_KIND,
                slot_key="key-no-kind",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=2025,
                report_kind="unknown",
                status="mapping_required",
                status_reason="identity_unresolved",
                mapping_key="sha256:abc",
                updated_at=NOW,
            ),
            make_slot_row(
                id=SLOT_NO_YEAR,
                slot_key="key-no-year",
                jurisdiction_org_id=orgs["district"],
                jurisdiction_name="上海市普陀区",
                department_org_id=orgs["dept"],
                department_name=DEPT_NAME,
                subject_org_id=orgs["sub_unit"],
                subject_org_name=SUB_UNIT_NAME,
                subject_kind="unit",
                material_scope="unit_self",
                fiscal_year=None,
                report_kind="budget",
                status="mapping_required",
                status_reason="identity_unresolved",
                mapping_key="sha256:def",
                updated_at=NOW,
            ),
        ]
    )
    fake_db.sources.extend(
        [
            make_source_row(
                id="source-official",
                source_id="source-official",
                slot_id=SLOT_MAIN,
                source_kind="official_site",
                source_url="https://www.shpt.gov.cn/xxgk/2024-final.html",
                source_page_title="规划和自然资源局（本部）财政信息页",
                source_site="上海普陀",
                published_at=datetime(2025, 8, 20, tzinfo=timezone.utc),
                discovered_at=datetime(2026, 9, 20, 15, 38, tzinfo=timezone.utc),
                status="active",
            ),
            make_source_row(
                id="source-manual",
                source_id="source-manual",
                slot_id=SLOT_MAIN,
                source_kind="manual_upload",
                source_url=None,
                source_page_title=None,
                source_site=None,
                published_at=None,
                discovered_at=datetime(2026, 9, 18, 10, 12, tzinfo=timezone.utc),
                status="active",
            ),
            # 兄弟单位的来源：越权请求里绝不能出现
            make_source_row(
                id="source-sibling",
                source_id="source-sibling",
                slot_id=SLOT_SIBLING,
                source_url="https://www.shpt.gov.cn/xxgk/sibling.html",
            ),
        ]
    )
    fake_db.versions.extend(
        [
            make_version_row(
                document_version_id=11,
                slot_id=SLOT_MAIN,
                file_hash="a" * 64,
                original_filename="v1.pdf",
                version_created_at=EARLIER,
            ),
            make_version_row(
                document_version_id=22,
                slot_id=SLOT_MAIN,
                file_hash="b" * 64,
                original_filename="v2.pdf",
                version_created_at=NOW,
            ),
            # missing 槽位的历史版本：它证明"不是从未上传过"
            make_version_row(
                document_version_id=33,
                slot_id=SLOT_MISSING,
                file_hash="c" * 64,
                original_filename="历史版本.pdf",
                version_created_at=EARLIER,
            ),
            make_version_row(
                document_version_id=99,
                slot_id=SLOT_SIBLING,
                file_hash="d" * 64,
                original_filename="sibling.pdf",
                version_created_at=NOW,
            ),
        ]
    )
    fake_db.jobs.extend(
        [
            make_job_row(
                id=501,
                job_uuid="job-v1",
                status="done",
                filename="v1.pdf",
                metadata={"structured_ingest": {"document_version_id": 11, "status": "done"}},
            ),
            make_job_row(
                id=502,
                job_uuid="job-v2",
                status="done",
                filename="v2.pdf",
                metadata={
                    "structured_ingest": {"document_version_id": 22, "status": "done"},
                    "result_meta": {
                        "elapsed_ms": {"total": 12345},
                        "quality_gate": {
                            "status": "review_required",
                            "quality_status": "review_required",
                            "analysis_conclusion": "findings_detected",
                        },
                        "obligation_coverage": {
                            "catalog_version": "v3.3",
                            "applicable_total": 42,
                            "completed_total": 26,
                            "not_applicable_total": 0,
                            "unresolved_total": 16,
                            "blocking_total": 15,
                            "coverage_rate": 0.619,
                            "auto_completion_rate": 0.6429,
                            "by_reason": {"not_implemented": 12, "insufficient_data": 3},
                        },
                    },
                },
            ),
            # legacy 运行：没有 document_version_id，绝不能出现在任何槽位下
            make_job_row(
                id=900,
                job_uuid="job-legacy",
                status="done",
                filename="v2.pdf",  # 文件名与当前版本完全相同
                metadata={"organization_name": SUB_UNIT_NAME, "report_year": "2024"},
            ),
            # metadata 写坏：非数字的 document_version_id 也不参与关联
            make_job_row(
                id=901,
                job_uuid="job-broken-metadata",
                status="done",
                metadata={"structured_ingest": {"document_version_id": "22-ish"}},
            ),
        ]
    )
    fake_db.results.extend(
        [
            make_result_row(
                job_id=501,
                result_id=9001,
                ai_findings=[
                    {"id": f"v1-f{n}", "severity": "high", "title": f"V1 问题 {n}"}
                    for n in range(1, 4)
                ],
                rule_findings=[],
            ),
            make_result_row(
                job_id=502,
                result_id=9002,
                ai_findings=[
                    {
                        "id": "f-high",
                        "severity": "high",
                        "title": "表内金额勾稽不一致",
                        "rule_id": "V33-121",
                        "page_number": 15,
                        "bbox": [100.0, 220.0, 420.0, 300.0],
                        "evidence": [{"page": 15, "text": "合计 35.20"}],
                        "obligation_ids": ["OBL-T-004"],
                    },
                    {
                        "id": "f-degraded",
                        "severity": "manual_review",
                        "original_severity": "high",
                        "evidence_status": "degraded_missing_evidence",
                        "title": "缺证据的候选问题",
                    },
                ],
                rule_findings=[
                    {"id": "f-info", "severity": "info", "title": "命名不规范"},
                ],
                merged_result={"totals": {"merged": 2}},
            ),
            make_result_row(job_id=900, result_id=9003, ai_findings=[{"id": "legacy"}]),
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


def _headers(session_token: str | None = None) -> dict[str, str]:
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


def _create_user(
    client: TestClient, admin_token: str, username: str, org_ids: list[str]
) -> None:
    response = client.post(
        "/api/users",
        headers=_headers(admin_token),
        json={"username": username, "password": "UserPass123", "organization_ids": org_ids},
    )
    assert response.status_code == 200, response.text


def _admin(client: TestClient) -> str:
    return _login(client, "admin", ADMIN_PASSWORD)


def _unit_user(client: TestClient, orgs: dict, org_ids: list[str], name: str = "unit-user") -> str:
    admin = _admin(client)
    _create_user(client, admin, name, org_ids)
    return _login(client, name, "UserPass123")


# ==== 接口四：单位时间轴 ======================================================


def test_unit_timeline_groups_years_descending_with_all_three_kinds(client, org_tree):
    token = _admin(client)
    response = client.get(
        f"/api/materials/units/{org_tree['sub_unit']}/timeline", headers=_headers(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["meta"]["data_basis"] == "existing_slots_only"
    assert body["meta"]["expected_materials_ready"] is False

    years = body["data"]["years"]
    assert [row["fiscal_year"] for row in years] == [2026, 2025, 2024]
    by_year = {row["fiscal_year"]: row for row in years}
    assert [slot["slot_id"] for slot in by_year[2024]["final_slots"]] == [SLOT_MAIN]
    assert [slot["slot_id"] for slot in by_year[2026]["budget_slots"]] == [SLOT_NOT_DUE]
    # 文种未识别的槽位既不是预算也不是决算，但必须能被看见
    assert [slot["slot_id"] for slot in by_year[2025]["unclassified_slots"]] == [SLOT_NO_KIND]
    # 年度未识别的槽位不塞进任何具体年份
    assert [slot["slot_id"] for slot in body["data"]["unresolved_year_slots"]] == [SLOT_NO_YEAR]
    assert all(
        slot["slot_id"] != SLOT_NO_YEAR
        for row in years
        for key in ("budget_slots", "final_slots", "unclassified_slots")
        for slot in row[key]
    )


def test_unit_timeline_identity_is_subject_org_id_not_name(client, org_tree):
    """不同 subject 的槽位不会因为名称相似而混进来。"""
    token = _admin(client)
    body = client.get(
        f"/api/materials/units/{org_tree['sub_unit']}/timeline", headers=_headers(token)
    ).json()
    ids = {
        slot["slot_id"]
        for row in body["data"]["years"]
        for key in ("budget_slots", "final_slots", "unclassified_slots")
        for slot in row[key]
    }
    assert SLOT_SIBLING not in ids
    assert body["data"]["unit"]["unit_id"] == org_tree["sub_unit"]


def test_unit_timeline_404_for_unknown_unit(client, org_tree):
    token = _admin(client)
    response = client.get(
        "/api/materials/units/unit-does-not-exist/timeline", headers=_headers(token)
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "unit not found"


def test_unit_timeline_requires_unit_in_visible_scope(client, org_tree):
    """㉔ 只授权了某个单位的账号：能开自己的时间轴，别的单位 403。"""
    token = _unit_user(client, org_tree, [org_tree["sub_unit"]])
    own = client.get(
        f"/api/materials/units/{org_tree['sub_unit']}/timeline", headers=_headers(token)
    )
    assert own.status_code == 200, own.text
    assert own.json()["data"]["unit"]["unit_id"] == org_tree["sub_unit"]

    sibling = client.get(
        f"/api/materials/units/{org_tree['sibling_unit']}/timeline", headers=_headers(token)
    )
    assert sibling.status_code == 403
    assert sibling.json()["detail"] == "unit access denied"

    # 部门级授权可覆盖其下级单位（可见集合就是"授权节点的后代闭包"）
    dept_token = _unit_user(client, org_tree, [org_tree["dept"]], name="dept-user")
    for unit in (org_tree["head_unit"], org_tree["sub_unit"], org_tree["sibling_unit"]):
        response = client.get(
            f"/api/materials/units/{unit}/timeline", headers=_headers(dept_token)
        )
        assert response.status_code == 200, response.text


def test_unit_timeline_does_not_leak_other_units_slots(client, org_tree):
    """单位授权账号打开自己的时间轴时，看不到兄弟单位的槽位与来源。"""
    token = _unit_user(client, org_tree, [org_tree["sub_unit"]])
    body = client.get(
        f"/api/materials/units/{org_tree['sub_unit']}/timeline", headers=_headers(token)
    ).json()
    raw = str(body)
    assert SLOT_SIBLING not in raw
    assert SIBLING_UNIT_NAME not in raw


# ==== 接口五：材料详情 ========================================================


def test_slot_detail_current_version_and_analysis(client, org_tree):
    """⑦⑩⑰ 当前版本来自 slot 指针；V1 标历史；正式/人工/信息三桶分明。"""
    token = _admin(client)
    response = client.get(f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token))
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["slot"]["slot_id"] == SLOT_MAIN
    assert data["current_version"]["document_version_id"] == 22
    assert data["current_version"]["is_current"] is True
    assert data["version_total"] == 2
    assert data["historical_version_count"] == 1

    analysis = data["current_analysis"]
    assert analysis["available"] is True
    assert analysis["run"]["job_uuid"] == "job-v2"
    assert analysis["formal_issue_count"] == 1
    assert [item["finding_id"] for item in analysis["formal_findings"]] == ["f-high"]
    assert [item["finding_id"] for item in analysis["manual_review_items"]] == ["f-degraded"]
    assert [item["finding_id"] for item in analysis["info_findings"]] == ["f-info"]
    # 历史版本（V1）的 3 个 finding 绝不能出现在当前结果里
    assert "v1-f1" not in response.text
    # 证据位置可安全定位
    assert analysis["formal_findings"][0]["evidence_page"] == 15
    assert analysis["formal_findings"][0]["evidence_bbox"] == [100.0, 220.0, 420.0, 300.0]


def test_slot_detail_exposes_coverage_for_current_version(client, org_tree):
    """⑲⑳ 覆盖只对应当前文件版本；状态码原样透出，中文由展示层负责。"""
    token = _admin(client)
    data = client.get(
        f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token)
    ).json()["data"]
    coverage = data["current_analysis"]["coverage"]
    assert coverage["available"] is True
    summary = coverage["summary"]
    assert summary["applicable_total"] == 42
    assert summary["completed_total"] == 26
    assert summary["blocking_total"] == 15
    assert summary["by_reason"]["not_implemented"] == 12
    assert summary["catalog_version"] == "v3.3"


def test_slot_detail_without_current_version_reports_null_not_zero(client, org_tree):
    """⑧⑱ missing 槽位：没有当前版本 → 分析不可用，正式问题数必须是 null。"""
    token = _admin(client)
    data = client.get(
        f"/api/materials/slots/{SLOT_MISSING}", headers=_headers(token)
    ).json()["data"]
    assert data["slot"]["status"] == "missing"
    assert data["slot"]["status_reason"] == "due_exceeded"
    assert data["current_version"] is None
    assert data["version_total"] == 1
    assert data["historical_version_count"] == 1
    analysis = data["current_analysis"]
    assert analysis["available"] is False
    assert analysis["reason"] == "no_current_document_version"
    assert analysis["run"] is None
    assert analysis["formal_issue_count"] is None
    assert analysis["formal_findings"] is None
    assert analysis["coverage"] == {
        "available": False,
        "reason": "no_coverage_for_current_document_version",
        "summary": None,
        "items": [],
    }


def test_slot_detail_due_at_unknown_is_not_missing(client, org_tree):
    """㉒ not_due + due_at_unknown 原样透出，没有任何"缺失"结论字段。"""
    token = _admin(client)
    data = client.get(
        f"/api/materials/slots/{SLOT_NOT_DUE}", headers=_headers(token)
    ).json()["data"]
    assert data["slot"]["status"] == "not_due"
    assert data["slot"]["status_reason"] == "due_at_unknown"
    assert data["slot"]["due_at"] is None
    assert str(data["slot"]["status"]) != "missing"


def test_slot_detail_sources_keep_published_at_apart_from_fiscal_year(client, org_tree):
    """⑪⑫ 来源发布日期与财政年度各自独立；manual_upload 无 URL 是正常形态。"""
    token = _admin(client)
    data = client.get(
        f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token)
    ).json()["data"]
    assert data["slot"]["fiscal_year"] == 2024

    sources = {item["source_kind"]: item for item in data["sources"]}
    official = sources["official_site"]
    assert official["published_at"].startswith("2025-08-20")
    manual = sources["manual_upload"]
    assert manual["source_url"] is None
    # 无 URL 不等于来源缺失：source_kind 仍然明确
    assert manual["source_kind"] == "manual_upload"
    assert manual["status"] == "active"


def test_slot_detail_never_exposes_storage_key(client, org_tree, fake_db):
    """㉘ storage_key 是内部存储路径，绝不能出现在响应里。"""
    fake_db.versions[0]["storage_key"] = "job-uuid-1/v1.pdf"
    fake_db.versions[1]["storage_key"] = "C:/uploads/job-uuid-2/v2.pdf"
    token = _admin(client)
    response = client.get(f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token))
    assert response.status_code == 200, response.text
    assert "storage_key" not in response.text
    assert "job-uuid-1" not in response.text
    assert "C:/uploads" not in response.text
    # 版本级预览入口本轮不生成，绝不用 file:// 或 /uploads/ 绕过鉴权
    version = response.json()["data"]["current_version"]
    assert version["preview_url"] is None
    assert version["download_url"] is None


def test_slot_detail_404_for_unknown_slot(client, org_tree):
    token = _admin(client)
    response = client.get("/api/materials/slots/no-such-slot", headers=_headers(token))
    assert response.status_code == 404
    assert response.json()["detail"] == "material slot not found"


# ==== 接口六：版本历史 ========================================================


def test_slot_versions_keep_every_version_and_mark_current(client, org_tree):
    """⑨⑩ v1/v2 都保留；current 只有一个；排序稳定。"""
    token = _admin(client)
    response = client.get(
        f"/api/materials/slots/{SLOT_MAIN}/versions", headers=_headers(token)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["current_document_version_id"] == 22
    items = data["items"]
    assert [item["document_version_id"] for item in items] == [22, 11]
    assert [item["is_current"] for item in items] == [True, False]
    assert all(item["preview_url"] is None and item["download_url"] is None for item in items)
    assert "storage_key" not in response.text


def test_slot_versions_of_missing_slot_are_still_visible(client, org_tree):
    """missing 槽位的历史版本仍然可见：所以不能写"从未上传"。"""
    token = _admin(client)
    data = client.get(
        f"/api/materials/slots/{SLOT_MISSING}/versions", headers=_headers(token)
    ).json()["data"]
    assert data["current_document_version_id"] is None
    assert [item["document_version_id"] for item in data["items"]] == [33]
    assert data["items"][0]["is_current"] is False


# ==== 接口七：处理记录 ========================================================


def test_slot_runs_link_by_document_version_id_only(client, org_tree):
    """⑯ legacy 运行（含同名文件）不会被猜到槽位下。"""
    token = _admin(client)
    response = client.get(
        f"/api/materials/slots/{SLOT_MAIN}/runs", headers=_headers(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["linkage_basis"] == "structured_document_version_id"
    assert body["meta"]["legacy_unlinked_runs_excluded"] is True
    items = body["data"]["items"]
    assert [item["job_uuid"] for item in items] == ["job-v2", "job-v1"]
    assert ["job-legacy", "job-broken-metadata"] != [item["job_uuid"] for item in items]
    assert "job-legacy" not in response.text
    assert "job-broken-metadata" not in response.text


def test_slot_runs_mark_current_and_historical_versions(client, org_tree):
    """⑩⑭ 每条运行标明当前/历史文件版本，且历史运行的 finding 数不冒充当前。"""
    token = _admin(client)
    items = client.get(
        f"/api/materials/slots/{SLOT_MAIN}/runs", headers=_headers(token)
    ).json()["data"]["items"]
    by_uuid = {item["job_uuid"]: item for item in items}
    assert by_uuid["job-v2"]["is_current_document_version"] is True
    assert by_uuid["job-v1"]["is_current_document_version"] is False
    assert by_uuid["job-v2"]["document_version_id"] == 22
    assert by_uuid["job-v1"]["document_version_id"] == 11
    # 运行列表不下结论：正式问题数一律 null，避免与详情的当前分析口径混用
    assert all(item["formal_issue_count"] is None for item in items)
    assert by_uuid["job-v1"]["ai_findings_count"] == 3
    assert by_uuid["job-v2"]["ai_findings_count"] == 2
    assert by_uuid["job-v2"]["rule_findings_count"] == 1
    assert by_uuid["job-v2"]["merged_findings_count"] == 2
    assert by_uuid["job-v2"]["has_results"] is True
    assert by_uuid["job-v2"]["obligation_catalog_version"] == "v3.3"


def test_slot_runs_do_not_carry_raw_response_or_findings(client, org_tree):
    """§四十七 列表页不返回巨大 raw_response 与完整 finding。"""
    token = _admin(client)
    response = client.get(
        f"/api/materials/slots/{SLOT_MAIN}/runs", headers=_headers(token)
    )
    assert response.status_code == 200
    assert "raw_response" not in response.text
    assert "ai_findings\"" not in response.text
    assert "evidence" not in response.text
    body = response.json()
    for item in body["data"]["items"]:
        assert "ai_findings" not in item
        assert "rule_findings" not in item
        assert "merged_result" not in item


def test_run_error_summary_hides_paths_and_stack(client, org_tree, fake_db):
    """§五十六 失败运行允许显示"处理失败"，但不显示堆栈/本地路径/连接串。"""
    for job in fake_db.jobs:
        if job["job_uuid"] == "job-v2":
            job["status"] = "error"
            job["error_message"] = (
                "Traceback (most recent call last):\n"
                '  File "C:\\app\\src\\main.py", line 1\n'
                "psycopg.OperationalError: postgres://user:pass@localhost/db"
            )
    token = _admin(client)
    response = client.get(
        f"/api/materials/slots/{SLOT_MAIN}/runs", headers=_headers(token)
    )
    assert response.status_code == 200, response.text
    assert "Traceback" not in response.text
    assert "C:\\app" not in response.text
    assert "postgres://" not in response.text
    items = response.json()["data"]["items"]
    failed = next(item for item in items if item["job_uuid"] == "job-v2")
    assert failed["status"] == "error"
    assert failed["error_summary"] == "处理失败（详情见任务日志）"


# ==== 鉴权与 IDOR ============================================================


def test_slot_detail_denies_sibling_unit_slot(client, org_tree):
    """㉓㉕ 单位授权账号直接请求兄弟单位的槽位 UUID：三个接口都必须 403。"""
    token = _unit_user(client, org_tree, [org_tree["sub_unit"]])
    base = f"/api/materials/slots/{SLOT_SIBLING}"
    for suffix in ("", "/versions", "/runs"):
        response = client.get(f"{base}{suffix}", headers=_headers(token))
        assert response.status_code == 403, f"{suffix or '/detail'} => {response.status_code}"
        assert response.json()["detail"] == "slot access denied"
    # 自己的槽位仍然可读
    assert (
        client.get(f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token)).status_code
        == 200
    )


def test_versions_and_runs_deny_sibling_slot(client, org_tree):
    """㉖㉗ versions / runs 的 IDOR 反例（分别断言，不合并成一个循环）。"""
    token = _unit_user(client, org_tree, [org_tree["sub_unit"]])
    versions = client.get(
        f"/api/materials/slots/{SLOT_SIBLING}/versions", headers=_headers(token)
    )
    assert versions.status_code == 403
    assert "sibling.pdf" not in versions.text

    runs = client.get(
        f"/api/materials/slots/{SLOT_SIBLING}/runs", headers=_headers(token)
    )
    assert runs.status_code == 403


def test_department_scope_can_read_slots_within_its_units(client, org_tree):
    """㉓ 部门授权：部门下（含自己）三个单位的槽位都可读。"""
    token = _unit_user(client, org_tree, [org_tree["dept"]], name="dept-scope-user")
    assert (
        client.get(f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token)).status_code
        == 200
    )
    assert (
        client.get(f"/api/materials/slots/{SLOT_SIBLING}", headers=_headers(token)).status_code
        == 200
    )


def test_unrelated_district_user_gets_403(client, org_tree):
    """㉓ 完全无关的区划授权：四个新接口都不放行。"""
    token = _unit_user(client, org_tree, [org_tree["other_dept"]], name="other-dept-user")
    for path in (
        f"/api/materials/slots/{SLOT_MAIN}",
        f"/api/materials/slots/{SLOT_MAIN}/versions",
        f"/api/materials/slots/{SLOT_MAIN}/runs",
        f"/api/materials/units/{org_tree['sub_unit']}/timeline",
    ):
        response = client.get(path, headers=_headers(token))
        assert response.status_code == 403, f"{path} => {response.status_code}"


def test_missing_session_token_returns_401(client, monkeypatch):
    # TESTING=true 时仓库的 require_login 会放行一个测试管理员（既有约定），
    # 因此要验未登录契约必须显式关掉它 —— 与 WP2-A 的同类用例同一手法。
    monkeypatch.setenv("TESTING", "false")
    assert client.get(f"/api/materials/slots/{SLOT_MAIN}").status_code == 401
    assert client.get(f"/api/materials/slots/{SLOT_MAIN}/versions").status_code == 401
    assert client.get(f"/api/materials/slots/{SLOT_MAIN}/runs").status_code == 401
    assert client.get("/api/materials/units/whatever/timeline").status_code == 401


def test_database_unavailable_maps_to_503(client, monkeypatch):
    async def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(materials_routes, "_open_connection", _boom)
    token = _admin(client)
    response = client.get(f"/api/materials/slots/{SLOT_MAIN}", headers=_headers(token))
    assert response.status_code == 503
    assert response.json()["detail"] == "material ledger database unavailable"


# ==== 只读性 =================================================================


def test_detail_endpoints_never_write(client, org_tree, fake_db):
    """本轮只读：详情路径上不允许出现任何写语句。"""
    token = _admin(client)
    for path in (
        f"/api/materials/slots/{SLOT_MAIN}",
        f"/api/materials/slots/{SLOT_MAIN}/versions",
        f"/api/materials/slots/{SLOT_MAIN}/runs",
        f"/api/materials/units/{org_tree['sub_unit']}/timeline",
    ):
        assert client.get(path, headers=_headers(token)).status_code == 200
    # 用语句级关键词而不是子串：``updated_at`` 含 "update"，
    # 子串匹配会把每一条 SELECT 都判成写语句。
    write_statement = re.compile(
        r"(insert\s+into|update\s+\w+\s+set|delete\s+from|truncate|alter\s+table|drop\s+table)",
        re.IGNORECASE,
    )
    for sql, _args in fake_db.calls:
        assert write_statement.search(sql) is None, f"只读接口出现写语句: {sql[:120]}"


def test_versions_endpoint_is_pure_read_after_repeat_calls(client, org_tree, fake_db):
    """连续请求不改变库内数据（版本历史绝不因"只展示最新版"而被清理）。"""
    token = _admin(client)
    before = [dict(row) for row in fake_db.versions]
    for _ in range(3):
        client.get(f"/api/materials/slots/{SLOT_MAIN}/versions", headers=_headers(token))
    assert fake_db.versions == before
