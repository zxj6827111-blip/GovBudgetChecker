"""材料台账查询服务：只读聚合、筛选与分页。

职责边界（不可越界）
--------------------
本模块**只做四件事**：``read`` / ``aggregate`` / ``filter`` / ``paginate``。
它不建槽位、不改状态、不绑版本、不做人工映射 —— 那些属于
``material_slot_service``（WP1 的唯一写入者）。只读是刻意的：台账页是"看数"的地方，
一旦它也能写，"页面上看到的数"与"谁改的"就再也分不清。

为什么聚合放在 Python 而不是全塞进 SQL
--------------------------------------
SQL 只负责"按身份分组后计数"这一层（每个接口 1-2 条语句，不做 N+1），
行内统计到响应 DTO 的拼装放在纯函数里，理由有两条：

1. **可测。** 纯函数可以用行数据直接喂进测试，覆盖全枚举状态、空表、
   同名隔离这类场景，不需要真库；真库上也就能验得更少但更关键的东西。
2. **口径唯一。** 三个接口共用同一个分桶函数（``_accumulate``），
   不会出现"首页把某状态算进 A、部门页算进 B"。

三条与 WP1 一致的纪律
--------------------
1. **身份按 id，不按名称。** 所有 GROUP BY 只按组织 id；名称一律
   ``MAX(name)`` 取一个快照值用于显示。按名称归并会把"规划和自然资源局（部门）"
   与"规划和自然资源局（本级单位）"合成一条 —— 这正是 WP1 修掉的缺陷。
2. **没有槽位 ≠ 缺失。** ``exists=false`` 只是"当前库里没有这条槽位"；
   缺失必须由状态机在"已到期 + 适用 + 无当前文件"三条同时成立时才给出。
3. **未知不猜。** 状态/原因/文种取值超出 WP1 取值域时直接抛错而不是归档到
   "其他"：静默归档会让分类之和对不上总数，是最难查的一类缺陷。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.schemas.material_ledger import (
    MATERIAL_STATUSES,
    DepartmentMatrixData,
    DepartmentMatrixItem,
    DepartmentRef,
    DepartmentScopeStat,
    DepartmentSubjectRow,
    DistrictCoverageItem,
    DistrictDepartmentMatrixData,
    DistrictRef,
    MaterialCoverageData,
    MaterialCoverageSummary,
    MaterialSlotSummary,
    MaterialStatusCounts,
    SubjectSlotRef,
    empty_status_counts,
)

#: 状态聚合白名单。与 ``material_slots.status`` 的 CHECK 约束同源，
#: 超出取值域即抛错（见模块顶部第 3 条纪律）。
_STATUS_DOMAIN = frozenset(MATERIAL_STATUSES)

#: "本级/本部"单位的名称形态：``…局本级``、``…局（本级）``、``…局本部``。
#: 组织目录里没有"是否本级"的显式字段，名称后缀是目前唯一可用的信号，
#: 且与既有实现同源（``ps_schema_sync`` 用 ``endswith("本级")`` 过滤、
#: ``org_hierarchy_migration`` 用同一后缀反推部门名）。
#: 局限：这是**展示层**的层级归类，不参与任何身份判定；WP9 引入组织主数据后，
#: 应改成显式字段而不是继续依赖名称。
_HEAD_UNIT_SUFFIX_RE = re.compile(r"(?:[（(]\s*(?:本级|本部)\s*[）)]|本级|本部)\s*$")


class UnknownMaterialStatusError(ValueError):
    """数据库里出现了取值域之外的状态。

    抛错而不是归入"其他"：这说明 CHECK 约束与本模块已经不同步，
    继续统计会给出分类之和不等于总数的假数字。

    原始状态码放在 ``status_code`` 属性上，**不**拼进异常消息：异常消息会顺着
    上游的 ``{e}`` 进入日志（``scripts/check_log_message_safety.py`` 规则 4），
    按仓库口径运行时值一律不进 message。排障时读该属性即可，信息没有丢。
    """

    def __init__(self, message: str, *, status_code: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_head_unit_name(name: Any) -> bool:
    """名称是否形如"…局本级 / …局（本部）"。"""
    text = str(name or "").strip()
    if not text:
        return False
    return bool(_HEAD_UNIT_SUFFIX_RE.search(text))


def relationship_of(
    *,
    subject_org_id: Any,
    department_id: Any,
    subject_kind: Any,
    material_scope: Any,
    subject_org_name: Any = None,
) -> str:
    """主体在部门矩阵中的层级关系（**纯展示**，不落库）。

    判定顺序：

    1. 主体就是主管部门自己 -> ``department_summary``（部门汇总材料）；
    2. 主体是单位：名称含"本级/本部"-> ``head_unit``，否则 ``subordinate_unit``；
       但如果材料范围写着"部门汇总"（与"主体是单位"互相矛盾），不猜 -> 待确认；
    3. 主体层级认不出来但材料范围写着部门汇总 -> ``department_summary``；
    4. 其余 -> ``relationship_unknown``（宁可在界面上承认"关系待确认"，也不猜）。

    关系由现有字段推导（§二十四 明确禁止写回数据库制造新的业务真值）。
    """
    subject = str(subject_org_id or "")
    department = str(department_id or "")
    kind = str(subject_kind or "")
    scope = str(material_scope or "")

    if subject and department and subject == department:
        return "department_summary"
    if kind == "unit":
        if scope == "department_summary":
            # 主体是单位、材料范围却写着部门汇总：两个字段互相矛盾，
            # 归到哪一层都会是错的断言，交给人工确认。
            return "relationship_unknown"
        return "head_unit" if is_head_unit_name(subject_org_name) else "subordinate_unit"
    if kind == "department" or scope == "department_summary":
        return "department_summary"
    if scope == "unit_self":
        # 主体层级未知但材料范围明确是"单位本级"：仍不知道它是不是本部，
        # 因此不进 head_unit，落到"直属单位"会过度断言，这里如实标为待确认。
        return "relationship_unknown"
    return "relationship_unknown"


# ---- 过滤器 ----------------------------------------------------------------


class MaterialSlotFilters:
    """三个接口共用的过滤条件。

    构造后交给 ``_build_where`` 生成 WHERE 片段，保证"行是否出现"与
    "行内统计口径"永远用同一组条件 —— 否则会出现"列表里 3 个部门、
    汇总里 5 个部门"这种自相矛盾的响应。
    """

    __slots__ = (
        "fiscal_year",
        "report_kind",
        "status",
        "jurisdiction_id",
        "jurisdiction_ids",
        "department_id",
    )

    def __init__(
        self,
        *,
        fiscal_year: Optional[int] = None,
        report_kind: str = "all",
        status: Optional[str] = None,
        jurisdiction_id: Optional[str] = None,
        jurisdiction_ids: Optional[Sequence[str]] = None,
        department_id: Optional[str] = None,
    ) -> None:
        self.fiscal_year = fiscal_year
        self.report_kind = report_kind
        self.status = status
        self.jurisdiction_id = jurisdiction_id
        #: 授权范围（非管理员）：``None`` 表示不限制；空列表表示一条都不可见。
        self.jurisdiction_ids = None if jurisdiction_ids is None else list(jurisdiction_ids)
        self.department_id = department_id

    def where(self) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []

        def add(fragment: str, value: Any) -> None:
            params.append(value)
            clauses.append(fragment.format(n=len(params)))

        if self.fiscal_year is not None:
            add("fiscal_year = ${n}", int(self.fiscal_year))
        # ``all`` 只是查询参数，不是数据库状态：预算与决算都查时**不**加文种条件，
        # 文种未识别的槽位因此同样计入（它们不会被塞进预算或决算任何一边）。
        if self.report_kind in ("budget", "final"):
            add("report_kind = ${n}", self.report_kind)
        if self.status:
            add("status = ${n}", self.status)
        if self.jurisdiction_id:
            add("jurisdiction_org_id = ${n}", self.jurisdiction_id)
        if self.jurisdiction_ids is not None:
            add("jurisdiction_org_id = ANY(${n}::text[])", self.jurisdiction_ids)
        if self.department_id:
            add("department_org_id = ${n}", self.department_id)

        return (" AND ".join(clauses) if clauses else "TRUE"), params


# ---- 聚合 ------------------------------------------------------------------


def _require_status(status: Any) -> str:
    value = str(status or "")
    if value not in _STATUS_DOMAIN:
        error = UnknownMaterialStatusError(
            "material_slots.status 出现取值域之外的值："
            "数据库 CHECK 约束与 MATERIAL_STATUSES 已不同步"
        )
        # 原始码作为属性透出，不放进异常消息、也不作为构造入参：
        # 消息会顺着上游 {e} 落盘（check_log_message_safety 规则 4），
        # 而属性不参与 __str__，排障时读属性即可，信息不丢。
        error.status_code = value
        raise error
    return value


def _accumulate(bucket: Dict[str, Any], row: Dict[str, Any]) -> None:
    """把一行分组结果累加进一个统计桶。

    桶字段：``slot_total`` / ``status_counts`` / ``budget_total`` / ``final_total`` /
    ``unknown_kind_total`` / ``due_at_unknown`` / ``updated_at``。
    """
    count = int(row.get("slot_count") or 0)
    bucket["slot_total"] += count
    bucket["status_counts"][_require_status(row.get("status"))] += count

    report_kind = str(row.get("report_kind") or "")
    if report_kind == "budget":
        bucket["budget_total"] += count
    elif report_kind == "final":
        bucket["final_total"] += count
    else:
        # 文种未识别（含文种冲突）单独计数：既不算预算也不算决算。
        bucket["unknown_kind_total"] += count

    bucket["due_at_unknown"] += int(row.get("due_at_unknown_count") or 0)
    bucket["updated_at"] = _max_moment(bucket.get("updated_at"), row.get("updated_at"))


def _max_moment(current: Optional[datetime], candidate: Any) -> Optional[datetime]:
    if not isinstance(candidate, datetime):
        return current
    if current is None or candidate > current:
        return candidate
    return current


def new_bucket() -> Dict[str, Any]:
    return {
        "slot_total": 0,
        "status_counts": empty_status_counts(),
        "budget_total": 0,
        "final_total": 0,
        "unknown_kind_total": 0,
        "due_at_unknown": 0,
        "updated_at": None,
    }


def _status_counts(bucket: Dict[str, Any]) -> MaterialStatusCounts:
    return MaterialStatusCounts(**bucket["status_counts"])


def _sort_key(name: Any, identity: Any) -> Tuple[int, str, str]:
    """稳定排序键：先按名称，再按 id 兜底；名称为空的行排在最后。

    只按名称排序时，两个同名主体（例如部门与同名本级单位）的顺序取决于
    数据库返回顺序，页面刷新一次可能就换位置。带上 id 兜底后顺序唯一。
    """
    text = str(name or "").strip()
    if not text:
        return (1, "", str(identity or ""))
    return (0, text, str(identity or ""))


# ---- 服务 ------------------------------------------------------------------


class MaterialLedgerQueryService:
    """材料台账只读查询。构造时传入一个 asyncpg 连接。

    ``self._conn`` 只需要实现 ``fetch(sql, *args)``。
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ---- 接口一：首页口径统计 ----------------------------------------------

    async def coverage(self, *, filters: MaterialSlotFilters) -> MaterialCoverageData:
        """全局 + 分区县的统计。两条 SQL：全局一条、区县一条。"""
        where, params = filters.where()

        district_rows = await self._conn.fetch(
            f"""
            SELECT jurisdiction_org_id,
                   MAX(jurisdiction_name) AS jurisdiction_name,
                   report_kind,
                   status,
                   COUNT(*)::int AS slot_count,
                   COUNT(*) FILTER (WHERE due_at IS NULL)::int AS due_at_unknown_count,
                   MAX(updated_at) AS updated_at
            FROM material_slots
            WHERE {where}
            GROUP BY jurisdiction_org_id, report_kind, status
            """,
            *params,
        )
        summary_rows = await self._conn.fetch(
            f"""
            SELECT report_kind,
                   status,
                   COUNT(*)::int AS slot_count,
                   COUNT(*) FILTER (WHERE due_at IS NULL)::int AS due_at_unknown_count,
                   COUNT(*) FILTER (WHERE jurisdiction_org_id IS NULL)::int AS jurisdiction_unknown_count
            FROM material_slots
            WHERE {where}
            GROUP BY report_kind, status
            """,
            *params,
        )
        return self.aggregate_coverage(district_rows, summary_rows)

    @staticmethod
    def aggregate_coverage(
        district_rows: Iterable[Dict[str, Any]],
        summary_rows: Iterable[Dict[str, Any]],
    ) -> MaterialCoverageData:
        """把分组行拼成首页数据（纯函数，便于不连库地穷举状态枚举）。

        ``district_rows`` 与 ``summary_rows`` 是**两条独立查询**的结果，不是同一份
        数据的两种切法：区县行只覆盖有行政区划的槽位，全局行覆盖全部槽位。
        因此全局汇总不能由区县行相加得出（那样会漏掉未识别行政区划的槽位），
        必须用全局查询自己的结果。
        """
        districts: Dict[str, Dict[str, Any]] = {}
        for row in district_rows:
            district_id = row.get("jurisdiction_org_id")
            if district_id is None or str(district_id) == "":
                # 没有行政区划的槽位不进区县卡片（卡片要能点进区级矩阵），
                # 但它们的数量必须在全局汇总里有出口（jurisdiction_unknown_total），
                # 否则会凭空消失。
                continue
            key = str(district_id)
            bucket = districts.setdefault(
                key,
                {
                    **new_bucket(),
                    "district_id": key,
                    "district_name": str(row.get("jurisdiction_name") or "").strip(),
                },
            )
            _accumulate(bucket, row)

        summary_bucket = new_bucket()
        summary_bucket["jurisdiction_unknown_total"] = 0
        for row in summary_rows:
            _accumulate(summary_bucket, row)
            summary_bucket["jurisdiction_unknown_total"] += int(
                row.get("jurisdiction_unknown_count") or 0
            )

        items = [
            DistrictCoverageItem(
                district_id=bucket["district_id"],
                district_name=bucket["district_name"] or bucket["district_id"],
                slot_total=bucket["slot_total"],
                status_counts=_status_counts(bucket),
                budget_total=bucket["budget_total"],
                final_total=bucket["final_total"],
                unknown_kind_total=bucket["unknown_kind_total"],
                due_at_unknown=bucket["due_at_unknown"],
                updated_at=bucket["updated_at"],
            )
            for bucket in districts.values()
        ]
        items.sort(key=lambda item: _sort_key(item.district_name, item.district_id))

        summary = MaterialCoverageSummary(
            slot_total=summary_bucket["slot_total"],
            status_counts=_status_counts(summary_bucket),
            budget_total=summary_bucket["budget_total"],
            final_total=summary_bucket["final_total"],
            unknown_kind_total=summary_bucket["unknown_kind_total"],
            due_at_unknown=summary_bucket["due_at_unknown"],
            jurisdiction_unknown_total=summary_bucket["jurisdiction_unknown_total"],
        )
        return MaterialCoverageData(summary=summary, districts=items)

    # ---- 接口二：区级主管部门矩阵 ------------------------------------------

    async def district_departments(
        self,
        *,
        district_id: str,
        district_name: Optional[str] = None,
        filters: MaterialSlotFilters,
        page: int = 1,
        page_size: int = 50,
        q: Optional[str] = None,
    ) -> Tuple[DistrictDepartmentMatrixData, int]:
        """区县下的主管部门统计，返回 (本页数据, 过滤后的总行数)。

        一条 SQL：按（部门 × 主体 × 文种 × 状态）分组。分组到**主体**这一层是
        刻意的 —— 一条 SQL 就能同时算出"部门槽位总数"与"部门覆盖主体数"，
        不必为第二个数字再查一次库。

        ``q`` 按主管部门名称做不区分大小写的子串过滤，作用在**聚合之后**：
        名称以组织目录为准，而组织目录的名称与槽位里的名称快照可能不同
        （部门改名会换组织 id）。按聚合后的显示名过滤，用户搜到的是他看见的名字。
        """
        scoped = MaterialSlotFilters(
            fiscal_year=filters.fiscal_year,
            report_kind=filters.report_kind,
            status=filters.status,
            jurisdiction_id=district_id,
            department_id=filters.department_id,
        )
        where, params = scoped.where()
        rows = await self._conn.fetch(
            f"""
            SELECT department_org_id,
                   MAX(department_name) AS department_name,
                   subject_org_id,
                   report_kind,
                   status,
                   COUNT(*)::int AS slot_count,
                   COUNT(*) FILTER (WHERE due_at IS NULL)::int AS due_at_unknown_count,
                   MAX(updated_at) AS updated_at
            FROM material_slots
            WHERE {where}
            GROUP BY department_org_id, subject_org_id, report_kind, status
            """,
            *params,
        )
        items = self.aggregate_district_departments(rows)
        keyword = str(q or "").strip().lower()
        if keyword:
            items = [
                item
                for item in items
                if keyword in str(item.department_name or "").lower()
            ]
        return (
            DistrictDepartmentMatrixData(
                district=DistrictRef(
                    district_id=str(district_id),
                    district_name=str(district_name or district_id),
                ),
                items=_paginate(items, page, page_size),
            ),
            len(items),
        )

    @staticmethod
    def aggregate_district_departments(
        rows: Iterable[Dict[str, Any]],
    ) -> List[DepartmentMatrixItem]:
        """按部门聚合（纯函数）。"""
        departments: Dict[Optional[str], Dict[str, Any]] = {}

        for row in rows:
            department_id = row.get("department_org_id")
            key = str(department_id) if department_id is not None else None
            bucket = departments.setdefault(
                key,
                {
                    **new_bucket(),
                    "department_id": key,
                    "department_name": str(row.get("department_name") or "").strip(),
                    "subjects": set(),
                    # 按文种拆分的统计：预算列 / 决算列各要一份完整状态分桶。
                    "by_kind": {"budget": new_bucket(), "final": new_bucket()},
                },
            )
            _accumulate(bucket, row)
            report_kind = str(row.get("report_kind") or "")
            if report_kind in bucket["by_kind"]:
                _accumulate(bucket["by_kind"][report_kind], row)
            subject_id = str(row.get("subject_org_id") or "")
            if subject_id:
                bucket["subjects"].add(subject_id)

        items: List[DepartmentMatrixItem] = []
        for bucket in departments.values():
            items.append(
                DepartmentMatrixItem(
                    department_id=bucket["department_id"],
                    department_name=bucket["department_name"] or None,
                    subject_count=len(bucket["subjects"]),
                    slot_total=bucket["slot_total"],
                    status_counts=_status_counts(bucket),
                    budget=DepartmentScopeStat(
                        slot_total=bucket["by_kind"]["budget"]["slot_total"],
                        status_counts=_status_counts(bucket["by_kind"]["budget"]),
                    ),
                    final=DepartmentScopeStat(
                        slot_total=bucket["by_kind"]["final"]["slot_total"],
                        status_counts=_status_counts(bucket["by_kind"]["final"]),
                    ),
                    unknown_kind_total=bucket["unknown_kind_total"],
                    missing=int(bucket["status_counts"]["missing"]),
                    not_due=int(bucket["status_counts"]["not_due"]),
                    due_at_unknown=bucket["due_at_unknown"],
                    updated_at=bucket["updated_at"],
                )
            )
        items.sort(key=lambda item: _sort_key(item.department_name, item.department_id))
        return items

    # ---- 接口三：主管部门材料矩阵 ------------------------------------------

    async def department_matrix(
        self,
        *,
        department_id: str,
        fiscal_year: int,
        department_name: Optional[str] = None,
        jurisdiction_id: Optional[str] = None,
        jurisdiction_name: Optional[str] = None,
    ) -> DepartmentMatrixData:
        """某主管部门在指定财政年度的主体矩阵。一条 SQL，不做聚合。

        槽位行数被"部门 + 年度"限死（通常几十条），因此直接取明细再在 Python 里
        按主体归并，比"SQL 里做多层嵌套聚合"更容易读，也更容易验证。

        **排序刻意不写在 SQL 里**：同一（主体 × 文种）下出现多条槽位时该展示哪一条，
        由 ``_slot_preference`` 单独决定。把这条规则放在一个纯函数里，
        "身份已确认的槽位优先于按文档临时安置的占位槽位"就可以被直接测到，
        而 SQL ORDER BY 与 Python 选择规则分居两处必然漂移。
        """
        rows = await self._conn.fetch(
            """
            SELECT id::text AS slot_id,
                   slot_key,
                   mapping_key,
                   jurisdiction_org_id,
                   jurisdiction_name,
                   department_org_id,
                   department_name,
                   subject_org_id,
                   subject_org_name,
                   subject_kind,
                   material_scope,
                   fiscal_year,
                   report_kind,
                   caliber,
                   caliber_conflict_candidate,
                   status,
                   status_reason,
                   applicability_status,
                   applicability_note,
                   due_at,
                   current_document_version_id,
                   updated_at
            FROM material_slots
            WHERE department_org_id = $1 AND fiscal_year = $2
            """,
            department_id,
            int(fiscal_year),
        )
        return self.aggregate_department_matrix(
            rows,
            department_id=department_id,
            fiscal_year=fiscal_year,
            department_name=department_name,
            jurisdiction_id=jurisdiction_id,
            jurisdiction_name=jurisdiction_name,
        )

    @staticmethod
    def aggregate_department_matrix(
        rows: Iterable[Dict[str, Any]],
        *,
        department_id: str,
        fiscal_year: int,
        department_name: Optional[str] = None,
        jurisdiction_id: Optional[str] = None,
        jurisdiction_name: Optional[str] = None,
    ) -> DepartmentMatrixData:
        """按主体归并成三分组矩阵（纯函数）。

        部门名与区县名以组织目录为准，组织目录查不到时回退到槽位里的名称快照
        （部门改名后组织 id 会变，历史槽位仍带着旧快照，有名字总比显示一串 id 好）；
        两者都没有才退化为 id 本身。
        """
        subjects: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        seen_department_name: Optional[str] = None
        seen_jurisdiction_id: Optional[str] = None
        seen_jurisdiction_name: Optional[str] = None

        # 先按"该展示哪一条槽位"排序，再按顺序归并：同一（主体 × 文种）下
        # 第一条即代表槽位。排序规则见 _slot_preference，与 SQL 无关。
        for row in sorted(rows, key=_slot_preference):
            subject_id = str(row.get("subject_org_id") or "")
            if not subject_id:
                # subject_org_id 是 NOT NULL，出现空值说明数据被外部破坏，
                # 直接跳过会让"矩阵少了几个主体"变得无声无息，因此显式失败。
                raise ValueError("material_slots.subject_org_id 为空，无法归入主体矩阵")

            seen_department_name = seen_department_name or _text_or_none(row.get("department_name"))
            seen_jurisdiction_id = seen_jurisdiction_id or _text_or_none(
                row.get("jurisdiction_org_id")
            )
            seen_jurisdiction_name = seen_jurisdiction_name or _text_or_none(
                row.get("jurisdiction_name")
            )

            entry = subjects.get(subject_id)
            if entry is None:
                entry = {
                    "subject_org_id": subject_id,
                    "subject_org_name": str(row.get("subject_org_name") or subject_id),
                    "subject_kind": str(row.get("subject_kind") or "unknown"),
                    "slots": {},
                    "implied": set(),
                }
                subjects[subject_id] = entry
                order.append(subject_id)

            entry["implied"].add(
                relationship_of(
                    subject_org_id=subject_id,
                    department_id=department_id,
                    subject_kind=row.get("subject_kind"),
                    material_scope=row.get("material_scope"),
                    subject_org_name=row.get("subject_org_name"),
                )
            )

            kind = str(row.get("report_kind") or "unknown")
            # 同（主体 × 文种）下的第一条即代表槽位（排序已保证优先级）。
            entry["slots"].setdefault(kind, _slot_summary(row))

        groups: Dict[str, List[DepartmentSubjectRow]] = {
            "department_summary": [],
            "head_unit": [],
            "subordinate_units": [],
            "relationship_unknown": [],
        }

        for subject_id in order:
            entry = subjects[subject_id]
            implied = entry["implied"]
            if len(implied) == 1:
                relationship = next(iter(implied))
            else:
                # 同一主体的多条材料对"自己属于哪一层"说法不一致（例如一条
                # 部门汇总、一条单位本级）。这不猜，落到待确认组。
                relationship = "relationship_unknown"
            group_key = "subordinate_units" if relationship == "subordinate_unit" else relationship
            slots = entry["slots"]
            groups[group_key].append(
                DepartmentSubjectRow(
                    subject_org_id=subject_id,
                    subject_org_name=entry["subject_org_name"],
                    subject_kind=entry["subject_kind"],
                    relationship=relationship,
                    budget=_slot_ref(slots.get("budget")),
                    final=_slot_ref(slots.get("final")),
                    unclassified=_slot_ref(slots.get("unknown")),
                )
            )

        for rows_in_group in groups.values():
            rows_in_group.sort(key=lambda item: _sort_key(item.subject_org_name, item.subject_org_id))

        return DepartmentMatrixData(
            department=DepartmentRef(
                department_id=str(department_id),
                department_name=str(department_name or seen_department_name or department_id),
                jurisdiction_id=jurisdiction_id or seen_jurisdiction_id,
                jurisdiction_name=jurisdiction_name or seen_jurisdiction_name,
            ),
            fiscal_year=int(fiscal_year),
            groups=groups,
        )


# ---- 行 -> DTO -------------------------------------------------------------


#: 时间缺失时用于排序的兜底时刻。取纪元而不是"当前时间"：
#: 排序结果必须只由数据决定，不能随运行时刻变化。
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _updated_rank(row: Dict[str, Any]) -> float:
    """更新时间在排序中的权重：越新越小（排在前面），缺失排最后。

    缺失用 ``inf`` 而不是"当前时间"：排序结果必须只由数据决定，
    不能随运行时刻变化。
    """
    value = row.get("updated_at")
    if not isinstance(value, datetime):
        return float("inf")
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return -moment.timestamp()


def _slot_preference(row: Dict[str, Any]) -> Tuple[str, str, int, float, str]:
    """同一（主体 × 文种）下多条槽位时，谁该被展示。

    优先级（越靠前越优先）：

    1. 身份已确认的槽位（``mapping_key = ''``）优于按具体文档临时安置的占位槽位 ——
       占位槽位是"等人工确认"的临时安置，不能顶掉同名主体已经确认的那条材料；
    2. 更新时间较新的优先（``updated_at`` 缺失的排最后）；
    3. ``slot_id`` 兜底，保证同一份数据每次查询得到同一个顺序。

    这条规则只在 Python 里存在一处，SQL 不做排序 —— 两处各写一份必然漂移。
    """
    subject = str(row.get("subject_org_id") or "")
    report_kind = str(row.get("report_kind") or "")
    identity_resolved = 0 if str(row.get("mapping_key") or "") == "" else 1
    return (
        subject,
        report_kind,
        identity_resolved,
        _updated_rank(row),
        str(row.get("slot_id") or ""),
    )


def _slot_summary(row: Dict[str, Any]) -> MaterialSlotSummary:
    return MaterialSlotSummary(
        slot_id=str(row.get("slot_id") or ""),
        slot_key=str(row.get("slot_key") or ""),
        jurisdiction_id=_text_or_none(row.get("jurisdiction_org_id")),
        jurisdiction_name=_text_or_none(row.get("jurisdiction_name")),
        department_org_id=_text_or_none(row.get("department_org_id")),
        department_name=_text_or_none(row.get("department_name")),
        subject_org_id=str(row.get("subject_org_id") or ""),
        subject_org_name=str(row.get("subject_org_name") or ""),
        subject_kind=str(row.get("subject_kind") or "unknown"),
        material_scope=str(row.get("material_scope") or "unknown"),
        fiscal_year=row.get("fiscal_year"),
        report_kind=str(row.get("report_kind") or "unknown"),
        caliber=str(row.get("caliber") or "unknown"),
        caliber_conflict_candidate=_text_or_none(row.get("caliber_conflict_candidate")),
        status=str(row.get("status")),
        status_reason=_text_or_none(row.get("status_reason")),
        applicability_status=str(row.get("applicability_status") or "applicable"),
        applicability_note=_text_or_none(row.get("applicability_note")),
        due_at=row.get("due_at"),
        current_document_version_id=row.get("current_document_version_id"),
        # WP3 接通复核生命周期前恒为 null：0 会被读成"查过且没有问题"。
        formal_issue_count=None,
        updated_at=row.get("updated_at"),
    )


def _slot_ref(slot: Optional[MaterialSlotSummary]) -> SubjectSlotRef:
    return SubjectSlotRef(exists=slot is not None, slot=slot)


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _paginate(items: Sequence[Any], page: int, page_size: int) -> List[Any]:
    safe_page = max(1, int(page or 1))
    safe_size = max(1, int(page_size or 1))
    start = (safe_page - 1) * safe_size
    return list(items[start : start + safe_size])
