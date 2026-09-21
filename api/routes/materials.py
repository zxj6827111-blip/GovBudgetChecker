"""材料台账只读 API（WP2-A）。

本轮只实现三个 GET：

    1. ``GET /api/materials/coverage``                          首页口径统计
    2. ``GET /api/materials/districts/{id}/departments``         区级主管部门矩阵
    3. ``GET /api/materials/departments/{id}/matrix``            部门材料矩阵

三条纪律
--------
1. **全部服务端鉴权。** 管理员、被授权的区县用户、未授权用户三种情况都在服务端判定，
   不依赖前端隐藏入口：未授权访问具体区县/部门返回 403；首页聚合按账号授权范围过滤，
   账号一个区划都没授权时返回 403（"看不到东西"必须能解释清楚，不能是一片空白）。
2. **不扫文件系统、不聚合 /api/jobs。** 数据全部来自 ``material_slots`` 一次（或两次）
   聚合查询；台账页的速度不取决于 uploads 目录有多大。
3. **错误沿用仓库既有契约。** 校验错误交给 FastAPI（422），未登录 401、越权 403、
   数据库不可用 503；不为 Materials 发明第二套错误格式。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request

from api import runtime
from api.auth_utils import require_login, user_can_access_org
from src.db.connection import DatabaseConnection
from src.schemas.material_ledger import (
    MATERIAL_STATUSES,
    DepartmentMatrixData,
    DepartmentMatrixResponse,
    DistrictDepartmentMatrixResponse,
    MaterialCoverageResponse,
    MaterialStatus,
    ReportKindFilter,
    build_list_meta,
    build_meta,
)
from src.services.material_ledger_query_service import (
    MaterialLedgerQueryService,
    MaterialSlotFilters,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: 区县级矩阵的默认/最大分页。上限 100 与仓库既有列表接口一致。
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

_JURISDICTION_LEVELS = ("district", "city")


# ---- 数据库连接（测试可替换） ------------------------------------------------


async def _open_connection() -> Any:
    """获取一条数据库连接。

    单独抽成函数是为了让测试可以替换成假连接：接口契约（鉴权、筛选、分页、
    字段名）与聚合逻辑因此可以在不连真库的情况下被完整验证。
    """
    return await DatabaseConnection.acquire()


async def _close_connection(conn: Any) -> None:
    await DatabaseConnection.release(conn)


async def _run_query(
    callback: Callable[[MaterialLedgerQueryService], Awaitable[Any]],
) -> Any:
    try:
        conn = await _open_connection()
    except Exception as exc:  # noqa: BLE001 - 连接失败的形态由驱动决定
        # 与 ps_shared 的 503 口径一致：数据库没起来是可恢复的运维状态，
        # 不是"这个接口不存在"，也不该伪装成空数据。
        logger.warning("Material ledger database unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="material ledger database unavailable") from exc
    try:
        return await callback(MaterialLedgerQueryService(conn))
    finally:
        await _close_connection(conn)


# ---- 授权范围 ---------------------------------------------------------------


def _is_admin(user: Any) -> bool:
    try:
        return bool(user.get("is_admin"))
    except AttributeError:  # pragma: no cover - 用户记录始终是 dict
        return False


class MaterialAccessScope:
    """当前账号在材料台账上的访问范围。

    两个集合，**职责严格分开**（这是本轮最重要的一条设计）：

    ``visible_org_ids``
        可以读到哪些**数据**。判定复用仓库既有的 ``user_can_access_org``
        （已授权节点可访问其后代），本模块不重新实现祖先/后代判断。
        ``None`` = 管理员，不加权限过滤。

    ``container_org_ids``
        可以打开哪些**页面容器**。= 可见组织的**祖先闭包**。
        用途只有一个：让"只授权了 dept-A / unit-A1"的账号仍能打开其所属区县或
        部门页面做导航（否则他们连自己的材料都点不进去）。

    容器可打开 ≠ 容器下数据可见：页面里的每一行仍然由 ``visible_org_ids``
    在 SQL 层过滤。只放开容器而不加范围过滤，会从"过度拒绝"直接变成"越权泄露"。
    """

    __slots__ = ("visible_org_ids", "container_org_ids")

    def __init__(
        self,
        *,
        visible_org_ids: Optional[List[str]] = None,
        container_org_ids: Optional[set] = None,
    ) -> None:
        self.visible_org_ids = visible_org_ids
        self.container_org_ids = container_org_ids or set()

    @property
    def unrestricted(self) -> bool:
        return self.visible_org_ids is None

    @property
    def has_any_visible_org(self) -> bool:
        return bool(self.visible_org_ids)

    def can_open_container(self, org_id: str) -> bool:
        """能否打开该组织对应的页面（管理员恒可）。"""
        if self.unrestricted:
            return True
        return str(org_id or "") in self.container_org_ids


def _build_access_scope(user: Any) -> MaterialAccessScope:
    """按既有 RBAC 语义算出可见范围与容器范围。

    - 管理员：``visible_org_ids=None``，不加任何权限条件；
    - 非管理员：遍历组织目录，凡 ``user_can_access_org`` 为真的节点都是可见节点
      （层级不限：区县、部门、单位一视同仁）。这样 department / unit 授权不会被
      错误折算成"必须能访问区县"，也不会把单位授权放大成整个部门；
    - 组织目录不可用：返回空可见集合（fail-closed，宁可看不到也不越权）。
    """
    if _is_admin(user):
        return MaterialAccessScope(visible_org_ids=None)

    try:
        storage = runtime.require_org_storage()
    except Exception:  # noqa: BLE001 - 组织目录不可用时按"无授权"处理（fail-closed）
        logger.warning("Organization catalog unavailable while resolving material scope")
        return MaterialAccessScope(visible_org_ids=[])

    records = list(storage.get_all())
    by_id = {str(getattr(org, "id", "") or ""): org for org in records}

    visible: List[str] = []
    for org in records:
        org_id = str(getattr(org, "id", "") or "")
        if org_id and user_can_access_org(user, org_id):
            visible.append(org_id)

    # 容器集合 = 可见节点的祖先闭包（含可见节点自身）。
    containers = set(visible)
    for org_id in visible:
        current = by_id.get(org_id)
        seen: set = set()
        while current is not None:
            parent_id = str(getattr(current, "parent_id", "") or "")
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            containers.add(parent_id)
            current = by_id.get(parent_id)

    return MaterialAccessScope(visible_org_ids=visible, container_org_ids=containers)


def _require_container_access(scope: MaterialAccessScope, org_id: str, detail: str) -> None:
    if not scope.can_open_container(org_id):
        raise HTTPException(status_code=403, detail=detail)


# ---- 组织目录显示名（纯展示，查不到就回退） ---------------------------------


def _org_storage_or_none() -> Any:
    try:
        return runtime.require_org_storage()
    except Exception:  # noqa: BLE001 - 显示名不是关键路径，查不到用快照兜底
        return None


def _org_name(storage: Any, org_id: str) -> Optional[str]:
    if storage is None:
        return None
    org = storage.get_by_id(org_id)
    if org is None:
        return None
    name = str(getattr(org, "name", "") or "").strip()
    return name or None


def _jurisdiction_of(storage: Any, org_id: str) -> Tuple[Optional[str], Optional[str]]:
    """沿组织目录向上找行政区划（区县/市），返回 (id, name)。"""
    if storage is None:
        return (None, None)
    seen: set = set()
    current = storage.get_by_id(org_id)
    while current is not None:
        current_id = str(getattr(current, "id", "") or "")
        if not current_id or current_id in seen:
            return (None, None)
        seen.add(current_id)
        if str(getattr(current, "level", "") or "") in _JURISDICTION_LEVELS:
            name = str(getattr(current, "name", "") or "").strip()
            return (current_id, name or None)
        parent_id = str(getattr(current, "parent_id", "") or "")
        current = storage.get_by_id(parent_id) if parent_id else None
    return (None, None)


# ---- 接口一：首页口径统计 ---------------------------------------------------


@router.get("/api/materials/coverage", response_model=MaterialCoverageResponse)
async def get_material_coverage(
    request: Request,
    fiscal_year: Optional[int] = Query(default=None, ge=2000, le=2099),
    report_kind: ReportKindFilter = Query(default="all"),
    status: Optional[MaterialStatus] = Query(default=None),
    jurisdiction_id: Optional[str] = Query(default=None, min_length=1),
) -> MaterialCoverageResponse:
    """首页 KPI 与区县卡片的数据。

    口径（本轮唯一允许的取值）：``data_basis=existing_slots_only`` ——
    只统计 ``material_slots`` 中已存在的槽位；应收基线未建立，因此
    ``expected_*`` / ``*_coverage_rate`` 一律为 ``null``，不猜"应该有但缺失"。
    """
    _, _, user = require_login(request)

    scope = _build_access_scope(user)
    if not scope.unrestricted:
        if not scope.has_any_visible_org:
            raise HTTPException(
                status_code=403,
                detail="no organization authorized for this account",
            )
        if jurisdiction_id:
            # 指定区县时它必须是"可打开的容器"：授权部门/单位的账号也要能按
            # 自己所属区县筛选（否则首页无法按区县下钻）。数据范围仍由下面的
            # visible_org_ids 过滤，容器放开不等于范围放开。
            _require_container_access(scope, jurisdiction_id, "jurisdiction access denied")

    filters = MaterialSlotFilters(
        fiscal_year=fiscal_year,
        report_kind=report_kind,
        status=status,
        jurisdiction_id=jurisdiction_id,
        visible_org_ids=scope.visible_org_ids,
    )
    data = await _run_query(lambda service: service.coverage(filters=filters))
    return MaterialCoverageResponse(ok=True, data=data, meta=build_meta())


# ---- 接口二：区级主管部门矩阵 ------------------------------------------------


@router.get(
    "/api/materials/districts/{district_id}/departments",
    response_model=DistrictDepartmentMatrixResponse,
)
async def get_district_departments(
    district_id: str,
    request: Request,
    fiscal_year: Optional[int] = Query(default=None, ge=2000, le=2099),
    report_kind: ReportKindFilter = Query(default="all"),
    status: Optional[MaterialStatus] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> DistrictDepartmentMatrixResponse:
    """某区县下的主管部门矩阵（分页）。"""
    _, _, user = require_login(request)
    scope = _build_access_scope(user)
    _require_container_access(scope, district_id, "jurisdiction access denied")

    storage = _org_storage_or_none()
    district_name = _org_name(storage, district_id)

    filters = MaterialSlotFilters(
        fiscal_year=fiscal_year,
        report_kind=report_kind,
        status=status,
        # 容器里的每一行仍按可见组织过滤：unit 授权的账号打开父部门页时，
        # 只会看到自己那个单位所在的那一行，且该行的数字只由自己产生。
        visible_org_ids=scope.visible_org_ids,
    )
    data, total = await _run_query(
        lambda service: service.district_departments(
            district_id=district_id,
            district_name=district_name,
            filters=filters,
            page=page,
            page_size=page_size,
            q=q,
        )
    )

    if district_name is None and total == 0:
        # 组织目录里没有这个区划，库中也没有任何它的材料 —— 给不出任何可信信息。
        # 注意：区划存在、只是当前筛选查不到，不算 404（那是合法的空结果）。
        raise HTTPException(status_code=404, detail="district not found")

    return DistrictDepartmentMatrixResponse(
        ok=True,
        data=data,
        meta=build_list_meta(page=page, page_size=page_size, total=total),
    )


# ---- 接口三：主管部门材料矩阵 ------------------------------------------------


@router.get(
    "/api/materials/departments/{department_id}/matrix",
    response_model=DepartmentMatrixResponse,
)
async def get_department_matrix(
    department_id: str,
    request: Request,
    fiscal_year: int = Query(..., ge=2000, le=2099),
) -> DepartmentMatrixResponse:
    """某主管部门在指定财政年度的主体矩阵（部门汇总 / 本部单位 / 直属单位）。

    ``fiscal_year`` 必填：不传由 FastAPI 直接返回 422，**不**回退到"当前自然年"——
    财政年度与发布日期是两件事，用今年的年份猜一份历史材料属于哪一年，
    会让整页数字悄悄错位（2024 年度决算在 2025 年发布是常态）。
    """
    _, _, user = require_login(request)
    scope = _build_access_scope(user)
    _require_container_access(scope, department_id, "department access denied")

    storage = _org_storage_or_none()
    department_name = _org_name(storage, department_id)
    jurisdiction_id, jurisdiction_name = _jurisdiction_of(storage, department_id)

    data: DepartmentMatrixData = await _run_query(
        lambda service: service.department_matrix(
            department_id=department_id,
            fiscal_year=fiscal_year,
            department_name=department_name,
            jurisdiction_id=jurisdiction_id,
            jurisdiction_name=jurisdiction_name,
            visible_org_ids=scope.visible_org_ids,
        )
    )

    has_any_subject = bool(
        data.groups.department_summary
        or data.groups.head_unit
        or data.groups.subordinate_units
        or data.groups.relationship_unknown
    )
    if department_name is None and not has_any_subject:
        # 组织目录里没有这个部门，库里也没有它任何年度的材料 —— 连名字都给不出来。
        # 部门存在但该年度没有槽位不算 404：那是"暂无已建立的材料槽位"空态。
        raise HTTPException(status_code=404, detail="department not found")

    return DepartmentMatrixResponse(ok=True, data=data, meta=build_meta())


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MATERIAL_STATUSES",
    "router",
]
