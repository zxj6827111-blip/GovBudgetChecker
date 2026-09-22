"""材料台账只读 API（WP2-A + WP2-B + WP2-C）。

接口面**恰好八条只读 GET**（多一条就会被 ``test_material_slot_api_regression``
拦住，写入路径属于 WP3/WP9）：

    1. ``GET /api/materials/coverage``                          首页口径统计
    2. ``GET /api/materials/districts/{id}/departments``         区级主管部门矩阵
    3. ``GET /api/materials/departments/{id}/matrix``            部门材料矩阵
    4. ``GET /api/materials/units/{id}/timeline``                单位多年度时间轴
    5. ``GET /api/materials/slots/{slot_id}``                    材料详情
    6. ``GET /api/materials/slots/{slot_id}/versions``           版本历史
    7. ``GET /api/materials/slots/{slot_id}/runs``               处理记录
    8. ``GET /api/materials/search``                             全局材料搜索（WP2-C）

四条纪律
--------
1. **全部服务端鉴权。** 管理员、被授权的区县用户、未授权用户三种情况都在服务端判定，
   不依赖前端隐藏入口：未授权访问具体区县/部门/单位/槽位返回 403；首页聚合按账号
   授权范围过滤，账号一个区划都没授权时返回 403（"看不到东西"必须能解释清楚，
   不能是一片空白）。搜索同样：无任何授权 -> 403，有授权但没命中 -> 200 + 空列表，
   两者语义不得混（§三十六）。
2. **不扫文件系统、不聚合 /api/jobs。** 数据全部来自台账四张表的 SQL；
   详情页的速度不取决于 uploads 目录有多大。搜索也一样：它按槽位查库，
   任务只是"精确关联到文件版本"的一条路径（§十/§二十七）。
3. **错误沿用仓库既有契约。** 校验错误交给 FastAPI（422），未登录 401、越权 403、
   数据库不可用 503；不为 Materials 发明第二套错误格式。
4. **"容器"与"数据"分开授权。** 区县/部门是**导航容器**（授权其下级即可打开），
   单位与槽位是**数据目标**（必须自己就在可见范围内），见 ``_require_unit_access``
   与 ``_require_slot_access``。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api import material_access, runtime
from api.auth_utils import require_login, user_can_access_job
from src.schemas.material_detail import (
    SlotDetailResponse,
    SlotRunListData,
    SlotRunListResponse,
    SlotVersionListData,
    SlotVersionListResponse,
    UnitTimelineResponse,
    build_detail_meta,
)
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
from src.schemas.material_search import (
    DEFAULT_SEARCH_PAGE_SIZE,
    MAX_SEARCH_PAGE_SIZE,
    MaterialSearchData,
    MaterialSearchMeta,
    MaterialSearchResponse,
    RelationshipResolution,
    build_search_meta,
)
from src.services.material_detail_query_service import MaterialDetailQueryService
from src.services.material_ledger_query_service import (
    MaterialLedgerQueryService,
    MaterialSlotFilters,
)
from src.services.material_search_query_service import (
    MAX_QUERY_LENGTH,
    MAX_TEXT_TOKENS,
    MIN_QUERY_LENGTH,
    MaterialSearchFilters,
    MaterialSearchQueryError,
    MaterialSearchQueryService,
    head_unit_subject_ids,
    parse_search_query,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: 区县级矩阵的默认/最大分页。上限 100 与仓库既有列表接口一致。
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

_JURISDICTION_LEVELS = ("district", "city")


# ---- 数据库连接（测试可替换） ------------------------------------------------
#
# 共享实现已搬到 ``api/material_access.py``（复核接口与台账接口共用同一套
# "取连接 / 503 口径 / 归还"）。这里保留同名模块级函数是为了让既有用例继续
# 通过替换 ``materials_routes._open_connection`` 注入假连接——那是 WP2 接口契约
# 能在无真库时被完整验证的前提，不因为搬运而失效。
_open_connection = material_access.open_connection
_close_connection = material_access.close_connection


async def _with_connection(callback: Callable[[Any], Awaitable[Any]]) -> Any:
    return await material_access.with_connection(
        callback,
        # 用 lambda 而非直接传函数对象：模块级名字必须在**调用时**再查一次，
        # 否则测试的 monkeypatch 替换不会被看到。
        opener=lambda: _open_connection(),
        closer=lambda conn: _close_connection(conn),
    )


async def _run_query(
    callback: Callable[[MaterialLedgerQueryService], Awaitable[Any]],
) -> Any:
    return await _with_connection(
        lambda conn: callback(MaterialLedgerQueryService(conn))
    )


async def _run_detail_query(
    callback: Callable[[MaterialDetailQueryService], Awaitable[Any]],
) -> Any:
    return await _with_connection(
        lambda conn: callback(MaterialDetailQueryService(conn))
    )


async def _run_search_query(
    callback: Callable[[MaterialSearchQueryService], Awaitable[Any]],
) -> Any:
    return await _with_connection(
        lambda conn: callback(MaterialSearchQueryService(conn))
    )


# ---- 授权范围 ---------------------------------------------------------------
#
# 判定实现已搬到 ``api/material_access.py``：复核接口的授权目标是槽位，与材料详情
# 完全同一套判定。两份实现做不到长期一致——漏掉一个组织列就是越权口子，
# 而漏掉的那一处永远不会报错。这里保留原名（含前导下划线）只为不动既有调用点。

_is_admin = material_access.is_admin
MaterialAccessScope = material_access.MaterialAccessScope


def _build_access_scope(user: Any) -> material_access.MaterialAccessScope:
    return material_access.build_access_scope(user)


def _require_container_access(scope: Any, org_id: str, detail: str) -> None:
    material_access.require_container_access(scope, org_id, detail)


def _require_unit_access(scope: Any, unit_id: str, detail: str) -> None:
    material_access.require_unit_access(scope, unit_id, detail)


slot_row_is_visible = material_access.slot_row_is_visible
_require_slot_access = material_access.require_slot_access


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


def _department_of(storage: Any, org_id: str) -> Tuple[Optional[str], Optional[str]]:
    """单位所属主管部门（沿组织目录向上找 ``department`` 层级）。

    单位时间轴的面包屑要显示"区县 > 主管部门 > 单位"，因此需要它。
    查不到就返回 ``(None, None)``：面包屑少一级，而不是猜一个部门。
    """
    if storage is None:
        return (None, None)
    seen: set = set()
    current = storage.get_by_id(org_id)
    while current is not None:
        current_id = str(getattr(current, "id", "") or "")
        if not current_id or current_id in seen:
            return (None, None)
        seen.add(current_id)
        if str(getattr(current, "level", "") or "") == "department":
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


# ---- 接口四：单位多年度时间轴 ------------------------------------------------


@router.get(
    "/api/materials/units/{unit_id}/timeline",
    response_model=UnitTimelineResponse,
)
async def get_unit_timeline(unit_id: str, request: Request) -> UnitTimelineResponse:
    """某单位的多年度材料时间轴。

    两条身份纪律：

    - **按 ``subject_org_id`` 查询，不按单位名称**。组织目录里"部门"与
      "同名本级单位"可以完全同名，按名称查会把两个主体混成一串；
    - **必须按 ``material_slots.fiscal_year`` 排列**。按 ``published_at`` /
      ``uploaded_at`` / ``created_at`` / job 年份排，会把"2024 年度决算
      （2025 年 8 月发布）"挪到 2025 那一行 —— 这正是本项目历史上把年份
      判错的主要成因。

    年度未知的槽位不兜底成任何具体年份，单独进 ``unresolved_year_slots``。
    """
    _, _, user = require_login(request)
    scope = _build_access_scope(user)
    # 单位是数据目标而不是导航容器：必须自己就在可见范围内（§六十八）。
    _require_unit_access(scope, unit_id, "unit access denied")

    storage = _org_storage_or_none()
    unit_name = _org_name(storage, unit_id)
    jurisdiction_id, jurisdiction_name = _jurisdiction_of(storage, unit_id)
    department_id, department_name = _department_of(storage, unit_id)

    data = await _run_detail_query(
        lambda service: service.unit_timeline(
            unit_id=unit_id,
            unit_name=unit_name,
            department_name=department_name,
            jurisdiction_name=jurisdiction_name,
            visible_org_ids=scope.visible_org_ids,
        )
    )

    has_any_slot = bool(
        data.years or data.unresolved_year_slots
    )
    if unit_name is None and not has_any_slot:
        # 组织目录里没有这个单位，库里也没有它任何年度的材料 —— 连名字都给不出来。
        # 单位存在但还没有槽位不算 404：那是"尚无已建立材料"空态。
        raise HTTPException(status_code=404, detail="unit not found")

    if data.unit.jurisdiction_id is None:
        data.unit.jurisdiction_id = jurisdiction_id
    if data.unit.department_id is None:
        data.unit.department_id = department_id

    return UnitTimelineResponse(ok=True, data=data, meta=build_detail_meta())


# ---- 接口五：材料详情 --------------------------------------------------------


@router.get("/api/materials/slots/{slot_id}", response_model=SlotDetailResponse)
async def get_slot_detail(slot_id: str, request: Request) -> SlotDetailResponse:
    """材料详情：槽位 + 当前文件版本 + 来源 + 当前分析（含检查覆盖）。

    ``current_analysis`` 严格绑定 ``current_document_version_id``：
    版本被替换后，上一版的分析只出现在「版本历史」与「处理记录」里并标注
    "历史版本"，**绝不**冒充当前文件的结论。算不出来时
    ``formal_issue_count`` 为 ``null`` 而不是 0 —— 0 的含义是
    "已确认当前版本的分析结果里没有正式问题"。
    """
    _, _, user = require_login(request)
    scope = _build_access_scope(user)

    async def _load(service: MaterialDetailQueryService) -> Any:
        row = _require_slot_access(scope, await service.load_slot_row(slot_id), slot_id)
        return await service.slot_detail(row)

    data = await _run_detail_query(_load)
    return SlotDetailResponse(ok=True, data=data, meta=build_detail_meta())


# ---- 接口六：版本历史 --------------------------------------------------------


@router.get(
    "/api/materials/slots/{slot_id}/versions",
    response_model=SlotVersionListResponse,
)
async def get_slot_versions(slot_id: str, request: Request) -> SlotVersionListResponse:
    """槽位下的文件版本历史（只读，不删除任何历史版本）。

    每个版本显式带 ``is_current``：真值只有
    ``material_slots.current_document_version_id`` 一个，
    ``is_current`` 只是把它投影成每行一个布尔，**不是**第二个真值来源。
    """
    _, _, user = require_login(request)
    scope = _build_access_scope(user)

    async def _load(service: MaterialDetailQueryService) -> Any:
        row = _require_slot_access(scope, await service.load_slot_row(slot_id), slot_id)
        items = await service.versions(row)
        return SlotVersionListData(
            slot_id=str(row.get("slot_id") or slot_id),
            items=items,
            current_document_version_id=(
                int(row["current_document_version_id"])
                if row.get("current_document_version_id") is not None
                else None
            ),
        )

    data = await _run_detail_query(_load)
    return SlotVersionListResponse(ok=True, data=data, meta=build_detail_meta())


# ---- 接口七：处理记录 --------------------------------------------------------


@router.get("/api/materials/slots/{slot_id}/runs", response_model=SlotRunListResponse)
async def get_slot_runs(slot_id: str, request: Request) -> SlotRunListResponse:
    """槽位下的分析运行记录。

    关联依据**唯一**：
    ``analysis_jobs.metadata.structured_ingest.document_version_id`` 等于该槽位
    某个文件版本的 id。没有这个字段的 legacy 运行不会被猜到任何槽位下
    （``meta.legacy_unlinked_runs_excluded = true``）。

    只返回摘要：列表里不含 ``raw_response`` / 完整 finding / 完整 evidence，
    避免"打开处理记录 Tab 等于下载 N 份完整分析 JSON"。
    """
    _, _, user = require_login(request)
    scope = _build_access_scope(user)

    async def _load(service: MaterialDetailQueryService) -> Any:
        row = _require_slot_access(scope, await service.load_slot_row(slot_id), slot_id)
        return SlotRunListData(
            slot_id=str(row.get("slot_id") or slot_id),
            items=await service.run_history(row),
        )

    data = await _run_detail_query(_load)
    return SlotRunListResponse(ok=True, data=data, meta=build_detail_meta())


# ---- 接口八：全局材料搜索（WP2-C） ------------------------------------------


def _validated_search_query(
    q: str = Query(..., min_length=MIN_QUERY_LENGTH, max_length=MAX_QUERY_LENGTH),
) -> str:
    """``q`` 的校验依赖：**trim 之后**再判长度。

    只靠 FastAPI 的 ``min_length`` 不够：``"  a  "`` 原始长度 5 会过闸，
    去空白后只剩 1 个字符。查询串的长度口径必须与"实际参与匹配的字符串"
    同一份，否则会出现"校验通过、匹配为空"这种无从解释的空结果。
    空查询**不返回全部材料**（§八）——它连校验都过不去。
    """
    value = str(q or "").strip()
    if len(value) < MIN_QUERY_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"q must be at least {MIN_QUERY_LENGTH} characters after trimming",
        )
    return value


@router.get("/api/materials/search", response_model=MaterialSearchResponse)
async def search_materials(
    request: Request,
    q: str = Depends(_validated_search_query),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=DEFAULT_SEARCH_PAGE_SIZE, ge=1, le=MAX_SEARCH_PAGE_SIZE
    ),
) -> MaterialSearchResponse:
    """全局材料搜索：**返回 Material Slot，不是 Job**。

    可以搜的字段（§十一）：PDF 文件名（含历史版本）、区县、主管部门、单位、
    财政年度、预算/决算、job id，以及"本部/本级"这一层关系修饰词。

    三条对外承诺：

    - **搜索式与台账式是两条并存的找材料路径**：这条接口不替代
      ``/api/materials/coverage`` 那条下钻链，它只解决"我知道一个词"的场景；
    - **越权材料不出现，也不以禁用态出现**：权限过滤在 SQL 里完成
      （``visible_org_ids`` 三列 OR），无权访问的材料连"存在"这件事都看不到
      （§三十五）。没命中与没权限在响应上完全同形；
    - **"进入复核"是两段判定的合取**：后端只回答"这个任务你有没有权限看"
      （复用 ``user_can_access_job``），任务状态是否允许进复核由前端既有的
      ``resolveReviewEntryDecision()`` 决定（§二十八）。判定不了时
      ``review_candidate`` 为 null，界面只保留「打开材料」（§二十九）。
    """
    _, _, user = require_login(request)

    scope = _build_access_scope(user)
    if not scope.unrestricted and not scope.has_any_visible_org:
        # 与 WP2-A 首页聚合同一口径：一个区划都没授权的账号是 403 而不是空列表。
        # 空列表会被读成"这些材料不存在"，而真相是"这个账号看不到任何东西"。
        raise HTTPException(
            status_code=403,
            detail="no organization authorized for this account",
        )

    try:
        parsed = parse_search_query(q)
    except MaterialSearchQueryError as exc:
        # 异常消息是静态文案（不含用户输入），可以安全地作为 detail 透出。
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    relationship_resolution: RelationshipResolution = "not_requested"
    head_unit_ids: List[str] = []
    if parsed.relationship == "head_unit":
        storage = _org_storage_or_none()
        if storage is None:
            # fail-closed（§十八）：解析不出"本部"时不给关系命中，
            # 而不是把"主体是单位"当成"本部"（直属单位也是单位）。
            relationship_resolution = "unavailable"
        else:
            relationship_resolution = "resolved"
            head_unit_ids = head_unit_subject_ids(list(storage.get_all()))

    filters = MaterialSearchFilters(
        # 年度/文种/关系只在 parsed 里（解析结果的唯一持有者），
        # 这里只传"解析出来的东西"与权限范围。
        head_unit_org_ids=head_unit_ids,
        # 管理员是 None（不加权限条件）；非管理员是可见组织列表，
        # 空列表在此分支不会出现（上面已经 403 拦掉）。
        visible_org_ids=scope.visible_org_ids,
    )

    def _job_access(payload: Dict[str, Any]) -> bool:
        """任务可见性判定：复用仓库既有实现，不另写一套 job 权限（§二十六）。"""
        return user_can_access_job(user, payload)

    items, total = await _run_search_query(
        lambda service: service.search(
            query=parsed,
            filters=filters,
            page=page,
            page_size=page_size,
            job_access=_job_access,
        )
    )

    meta: MaterialSearchMeta = build_search_meta(
        query=parsed.raw,
        page=page,
        page_size=page_size,
        total=total,
        relationship_resolution=relationship_resolution,
    )
    return MaterialSearchResponse(ok=True, data=MaterialSearchData(items=items), meta=meta)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MATERIAL_STATUSES",
    "MAX_TEXT_TOKENS",
    "router",
]
