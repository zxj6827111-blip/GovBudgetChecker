"""WP1 不破坏既有接口：路由面冻结 + 入库集成只做加法。

本轮新增的是业务数据底座，既有上传/任务/分析/组织接口的行为必须一字不动。
"没有改动"这种话不能只靠 review 声称，所以这里把接口面固化成清单：
少一条、多一条都会失败。清单本身写在下面，改动它必须是一个显式决定。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("TESTING", "true")

from api.main import app  # noqa: E402

#: 上传 / 任务 / 分析 / 文件 / 组织 相关接口的冻结清单（2026-09-21 @ 4a1cabf 基线）。
FROZEN_ROUTES = frozenset(
    {
        ("POST", "/api/documents/preflight"),
        ("POST", "/api/documents/upload"),
        ("POST", "/api/documents/{version_id}/run"),
        ("POST", "/upload"),
        ("POST", "/analyze/{job_id}"),
        ("POST", "/api/analyze/{job_id}"),
        ("POST", "/analyze2/{job_id}"),
        ("POST", "/api/analyze2/{job_id}"),
        ("GET", "/api/jobs"),
        ("GET", "/jobs"),
        ("GET", "/api/jobs/{job_id}"),
        ("GET", "/api/jobs/{job_id}/status"),
        ("GET", "/jobs/{job_id}/status"),
        ("GET", "/api/jobs/{job_id}/review"),
        ("GET", "/api/jobs/{job_id}/structured-ingest"),
        ("GET", "/api/jobs/{job_id}/org-suggestions"),
        ("DELETE", "/api/jobs/{job_id}"),
        ("POST", "/api/jobs/{job_id}/associate"),
        ("POST", "/api/jobs/{job_id}/reanalyze"),
        ("POST", "/api/jobs/{job_id}/issues/ignore"),
        ("POST", "/api/jobs/reanalyze-all"),
        ("POST", "/api/jobs/rematch-organizations"),
        ("POST", "/api/jobs/repair-missing-links"),
        ("POST", "/api/jobs/structured-ingest-cleanup"),
        ("POST", "/api/jobs/batch-delete"),
        ("GET", "/api/files/{job_id}/source"),
        ("GET", "/api/files/{job_id}/preview"),
        ("GET", "/api/organizations"),
        ("GET", "/api/organizations/list"),
        ("POST", "/api/organizations"),
        ("PUT", "/api/organizations/{org_id}"),
        ("DELETE", "/api/organizations/{org_id}"),
        ("GET", "/api/organizations/{org_id}/jobs"),
        ("GET", "/api/organizations/{org_id}/delete-preview"),
        ("POST", "/api/organizations/import"),
        ("GET", "/api/departments"),
        ("GET", "/api/departments/{dept_id}/units"),
        ("GET", "/api/departments/{dept_id}/stats"),
    }
)


def _route_surface() -> set:
    surface = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            surface.add((method, getattr(route, "path", "")))
    return surface


def test_job_upload_org_route_surface_is_unchanged():
    actual = {row for row in _route_surface() if _is_frozen_family(row[1])}
    missing = FROZEN_ROUTES - actual
    added = actual - FROZEN_ROUTES
    assert not missing, f"既有接口被删除: {sorted(missing)}"
    assert not added, f"出现未登记的新接口: {sorted(added)}"


#: 冻结族的前缀。**用前缀而不是子串**：WP2-A 的材料台账路由里含
#: ``/departments`` 子串（``/api/materials/districts/{id}/departments``），
#: 子串匹配会把它误判成"部门接口族出现了未登记的新接口"。
FROZEN_FAMILY_PREFIXES = (
    "/jobs",
    "/api/jobs",
    "/upload",
    "/documents",
    "/api/documents",
    "/analyze",
    "/api/analyze",
    "/api/analyze2",
    "/api/files/",
    "/api/organizations",
    "/api/departments",
)


def _is_frozen_family(path: str) -> bool:
    return path.startswith(FROZEN_FAMILY_PREFIXES)


#: 材料台账只读接口（本清单同样冻结：多一条、少一条都会失败）。
#: WP1：不新增任何 HTTP 接口；WP2-A：新增且**恰好**三条；WP2-B：新增且**恰好**七条。
#: 每轮把它写成精确集合，是为了继续拦住"顺手多加一个接口"——
#: 详情页需要数据不是实现写接口的理由（写入路径属于 WP3/WP9）。
MATERIAL_LEDGER_ROUTES = frozenset(
    {
        ("GET", "/api/materials/coverage"),
        ("GET", "/api/materials/districts/{district_id}/departments"),
        ("GET", "/api/materials/departments/{department_id}/matrix"),
        ("GET", "/api/materials/units/{unit_id}/timeline"),
        ("GET", "/api/materials/slots/{slot_id}"),
        ("GET", "/api/materials/slots/{slot_id}/versions"),
        ("GET", "/api/materials/slots/{slot_id}/runs"),
    }
)


def test_material_http_route_surface_is_exactly_the_planned_set():
    """材料台账的 HTTP 接口面恰好是计划中的七条只读接口。

    WP1 阶段这里是"一条都没有"；WP2-A 变成"精确三条"；WP2-B 变成
    "精确七条"（单位时间轴 + 槽位详情 + 版本历史 + 处理记录）。
    搜索（WP2-C）、复核生命周期（WP3）、应收基线（WP9）的接口**不在本轮
    范围内**，多出来就会被这条测试拦住。
    """
    actual = {row for row in _route_surface() if "/materials" in row[1]}
    assert actual == MATERIAL_LEDGER_ROUTES, (
        f"材料台账接口面与计划不一致：多了 {sorted(actual - MATERIAL_LEDGER_ROUTES)}；"
        f"少了 {sorted(MATERIAL_LEDGER_ROUTES - actual)}"
    )
    assert all(
        method == "GET" for method, _ in actual
    ), "本轮的七个材料接口都是只读 GET：写入路径属于 WP3/WP9"
    assert len(actual) == 7


# ==== 入库集成只做加法，且可关闭 ============================================


@pytest.mark.asyncio
async def test_kill_switch_skips_slot_allocation(monkeypatch):
    """运维需要一条不重新部署就能停掉槽位写入的开关。"""
    from src.services import structured_ingest_runner

    monkeypatch.setenv("MATERIAL_LEDGER_DISABLED", "1")

    class _NeverTouched:
        async def fetchrow(self, *args, **kwargs):
            raise AssertionError("开关打开时不应访问数据库")

        async def execute(self, *args, **kwargs):
            raise AssertionError("开关打开时不应访问数据库")

    result = await structured_ingest_runner._allocate_material_slot(
        _NeverTouched(),
        metadata={"organization_id": "x"},
        checksum="a" * 64,
        document_version_id=1,
    )
    assert result == {"status": "skipped", "reason": "material_ledger_disabled"}


@pytest.mark.asyncio
async def test_slot_failure_is_reported_not_raised():
    """槽位写入失败时，入库返回值里能看到原因，但流程不中断。"""
    from src.services import structured_ingest_runner
    from src.services.material_slot_service import safe_allocate_for_document
    from support_material_slot_db import FailingSlotConnection

    result = await safe_allocate_for_document(
        FailingSlotConnection(),
        metadata={"organization_id": "x", "report_year": "2024", "report_kind": "final"},
        checksum="b" * 64,
        org_records=[],
        document_version_id=1,
    )
    assert result["status"] == "error"
    assert "simulated database outage" in result["error"]
    # 函数确实返回了，而不是把异常抛给调用方
    assert structured_ingest_runner is not None


def test_structured_ingest_payload_shape_gains_only_material_slot():
    """结构化入库结果新增 ``material_slot`` 摘要，既有字段名一个都没动。"""
    import inspect

    from src.services import structured_ingest_runner

    source = inspect.getsource(structured_ingest_runner.run_structured_ingest)
    assert '"material_slot": material_slot,' in source
    # 既有字段仍在
    for key in (
        '"document_id"',
        '"document_version_id"',
        '"tables_count"',
        '"document_profile"',
        '"ps_sync"',
        '"review_items"',
    ):
        assert key in source, f"结构化入库结果字段 {key} 丢失"
