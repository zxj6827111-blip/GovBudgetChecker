"""材料详情查询服务：单位时间轴、槽位详情、版本历史、处理记录。

职责边界（与 WP2-A 同一条线）
-----------------------------
本模块**只做四件事**：``read`` / ``project`` / ``group`` / ``sort``。
不建槽位、不改状态、不绑版本、不写复核结论 —— 写入仍然只有
``material_slot_service``（WP1 的唯一写入者）。

数据从哪来（先审计再写代码，见交付文档 §2）
------------------------------------------
::

    material_slots.current_document_version_id
        └─> fiscal_document_versions.id          （版本历史）
                └─> analysis_jobs.metadata.structured_ingest.document_version_id
                        └─> analysis_results.job_id   （分析结果）
                                └─> analysis_jobs.metadata.result_meta.obligation_coverage
                                                              （检查覆盖）

这是 WP1 就已经存在的**精确**关联链。本模块不发明第二条：
不按文件名、不按 organization_name、不按"创建时间接近"、不按目录名、
不按"最新那个 job"把运行挂到槽位上。猜错归属比少显示几条更糟——
它会看起来完全正常。

三条与 WP2-A 逐字一致的纪律
---------------------------
1. **排序只在 Python 里存在一处。** 同版本多个运行、同一（单位×年度×文种）
   多条槽位，谁该被展示由明确的比较器决定；SQL 里再写一份 ORDER BY 必然漂移。
2. **未知不猜。** 年份未知不兜底成具体年份；当前版本指针为空不去历史里挑一份；
   覆盖记录缺失不返回 0/0 或 100%。
3. **JSONB 一律按"可能是 dict / 可能是 JSON 字符串"读。** 本仓库没有给
   asyncpg 注册 JSON codec，取回来的是字符串；只写 ``isinstance(x, dict)``
   会在真库上静默读到空 dict，然后页面显示"没有覆盖记录"——而数据其实是有的。

为什么版本、运行、详情共用**一条** join 查询
--------------------------------------------
三个接口问的是同一件事的不同侧面："这条槽位有哪些文件、每个文件跑过几次、
哪一次是当前的"。写三条 SQL 就会有三份投影，迟早出现"版本页能看到的字段
详情页看不到"。合并成一条（``fiscal_document_versions`` LEFT JOIN
``analysis_jobs`` LEFT JOIN ``analysis_results``）之后：

- 版本与运行天然对齐（``v.id`` 就是关联键，不是猜出来的）；
- 每个接口各自只有 **1 条**明细查询 + 1 条槽位行查询（授权用），远低于 §七十三 的预算；
- 没有 N+1：运行数由"版本数 × 每版本运行数"决定，实际是个位数行。

关于版本级预览 URL（§四十一）的显式决定
---------------------------------------
本模块**不生成** ``preview_url`` / ``download_url``，一律为 ``null``。理由：

- 仓库现有的 PDF 入口 ``/api/files/{job_id}/source|preview`` 全部是 **job 维度**，
  其鉴权 ``require_job_access`` 依赖 status.json（含 ``created_by`` 与组织归属），
  与"这条槽位你能看吗"不是同一个权限主体；
- 从槽位详情里发一个 job 维度的链接，会让"页面可见"与"链接可用"分属两套判定，
  页面能打开不等于链接能用（反之亦然），还会把 job_uuid 暴露给无权访问该任务的人；
- 本轮不引入第四套权限模型。版本级安全入口应当与 WP3 的复核生命周期一起设计，
  那时权限主体统一到槽位；
- 因此按 §四十一 的 fail-closed 分支取 ``null``，并**不**制造
  ``file://`` 或 ``/uploads/...`` 这类绕过鉴权的地址。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.schemas.material_detail import (
    ANALYSIS_REASON_NO_CURRENT_VERSION,
    ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
    COVERAGE_REASON_NONE_FOR_CURRENT_VERSION,
    AnalysisBlock,
    AnalysisFindingItem,
    CoverageBlock,
    CoverageGroupItem,
    CoverageObligationItem,
    CoverageSummaryBlock,
    MaterialSourceItem,
    MaterialVersionItem,
    RunSummaryItem,
    SlotDetailData,
    TimelineUnitRef,
    TimelineYearRow,
    UnitTimelineData,
)
from src.services.evidence_guard import count_formal_findings, is_formal_finding
from src.services.material_ledger_query_service import _slot_summary

#: 证据文本在详情里的截断长度。证据原文属于 `/api/jobs/{id}` 的职责，
#: 这里只为"证据位置"提供可读片段。
EVIDENCE_TEXT_LIMIT = 240

#: 精确关联字段在 JSONB 里的路径。集中成常量，避免字符串在 SQL 与 Python 里
#: 各写一份（写错一处会静默地关联到别的东西）。
_STRUCTURED_VERSION_JSON_PATH = "(j.metadata -> 'structured_ingest' ->> 'document_version_id')"

#: ``document_version_id`` 必须是纯数字才允许参与 bigint 比较。
#: 没有这个守卫，一条被写坏的 metadata 会让整条查询抛 cast 异常，
#: 而不是"这次运行不参与关联"（fail-closed）。
_NUMERIC_GUARD = f"{_STRUCTURED_VERSION_JSON_PATH} ~ '^[0-9]+$'"

#: 槽位行投影。三个接口共用同一份投影：少写一列会让某个接口悄悄拿到 None，
#: 而不是报错。占位符 ``{where}`` 由调用方给出。
_SLOT_COLUMNS = """
                   id::text AS slot_id,
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
                   updated_at"""


class UnknownRunMetadataError(ValueError):
    """``structured_ingest.document_version_id`` 出现了非数字值。

    只作为**排障信号**使用，不作为查询失败条件：查询侧用 ``_NUMERIC_GUARD``
    把这类行排除在关联之外（fail-closed），并由
    ``collect_legacy_metadata_warnings`` 汇总出来供日志与测试断言。
    原始值放在属性上而不是拼进消息：异常消息会顺着上游 ``{e}`` 落盘
    （``scripts/check_log_message_safety.py`` 规则 4）。
    """

    def __init__(self, message: str, *, raw_value: Any = None) -> None:
        super().__init__(message)
        self.raw_value = raw_value


# ---- 小工具 ----------------------------------------------------------------


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value: Any) -> Optional[int]:
    """整数化。``None`` / 空串 / 非数字一律返回 ``None``（不返回 0）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_value(raw: Any) -> Any:
    """JSONB 列的取值：可能是 dict/list，也可能是 JSON 字符串。

    与 ``analysis_result_store._coerce_json_value`` 同一手法（本仓库没有
    给 asyncpg 注册 JSON codec，真库取回来是字符串）。
    """
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001 - 解析失败按原值处理，由调用方兜底
            return raw
    return raw


def _json_dict(raw: Any) -> Dict[str, Any]:
    value = _json_value(raw)
    return value if isinstance(value, dict) else {}


def _json_list(raw: Any) -> List[Any]:
    value = _json_value(raw)
    return value if isinstance(value, list) else []


def structured_document_version_id(metadata: Any) -> Optional[int]:
    """从 ``analysis_jobs.metadata`` 里取精确关联的文件版本 id。

    只有 ``structured_ingest.document_version_id`` 存在且是正整数时才返回整数；
    其它情况（缺失、空串、非数字、0/负数）一律 ``None`` —— 表示
    "这次运行无法精确关联到任何文件版本"。
    调用方必须把它当成"不参与关联"，**不能**退化成按文件名或时间猜。
    """
    structured = _json_dict(metadata).get("structured_ingest")
    if not isinstance(structured, dict):
        return None
    raw = structured.get("document_version_id")
    value = _int_or_none(raw)
    if value is None:
        if raw not in (None, ""):
            raise UnknownRunMetadataError(
                "analysis_jobs.metadata.structured_ingest.document_version_id 不是整数",
                raw_value=raw,
            )
        return None
    return value if value > 0 else None


def collect_legacy_metadata_warnings(
    rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """挑出 metadata 写坏、因而无法参与精确关联的运行（只用于可观测性）。"""
    warnings: List[Dict[str, Any]] = []
    for row in rows:
        try:
            structured_document_version_id(row.get("metadata"))
        except UnknownRunMetadataError as exc:
            warnings.append(
                {"job_uuid": str(row.get("job_uuid") or ""), "raw_value": exc.raw_value}
            )
    return warnings


def _moment(value: Any) -> Optional[datetime]:
    return value if isinstance(value, datetime) else None


def _epoch() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---- 排序（唯一实现处） ------------------------------------------------------


def run_effective_moment(row: Dict[str, Any]) -> datetime:
    """一次运行的"有效完成时刻"。

    复用仓库既有任务列表的排序口径
    （``COALESCE(completed_at, started_at, updated_at, created_at)``，见
    ``analysis_result_store`` 的 jobs 列表查询）：用户在「任务历史」里看到的
    顺序与「处理记录」里看到的顺序因此一致，不会出现两个页面各说一套"最新"。

    ``created_at`` 在表定义里是 ``DEFAULT NOW()``，实践中不会为空；
    时间全缺时退到纪元，保证排序结果**只由数据决定**，不随运行时刻漂移。
    """
    for key in ("completed_at", "started_at", "updated_at", "created_at"):
        moment = _moment(row.get(key))
        if moment is not None:
            return moment
    return _epoch()


def run_sort_key(row: Dict[str, Any]) -> Tuple[float, int]:
    """运行的稳定排序键：有效时刻降序，``analysis_jobs.id`` 降序兜底。

    带 id 兜底是必须的：同一秒内的两次运行只按时间排，顺序取决于数据库
    返回顺序，页面刷新一次可能就换位置，"当前分析是哪一次"会跟着漂。
    """
    moment = run_effective_moment(row)
    return (-moment.timestamp(), -(_int_or_none(row.get("id")) or 0))


def select_current_run(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """在同一文件版本的多次运行里稳定地选出"当前分析"。

    取排序后的第一条。若这次运行还没落结果，上层如实报告 ``available=false``，
    **而不是**回退到上一次运行的结果 —— 回退会把"上一版文件的结论"
    当成"这一版文件没有正式问题"。
    """
    ordered = sorted(rows, key=run_sort_key)
    return ordered[0] if ordered else None


def _version_created_at(row: Dict[str, Any]) -> Optional[datetime]:
    """版本的创建时间。

    join 查询里是 ``v.created_at AS version_created_at``（避免与任务的
    ``j.created_at`` 撞名），纯聚合测试直接给 ``created_at``。
    **两处必须读同一个函数**：排序用 ``created_at``、展示用
    ``version_created_at`` 会让"显示的时间"与"排序依据"不一致，
    表现为版本顺序看起来是乱的。
    """
    return _moment(row.get("version_created_at")) or _moment(row.get("created_at"))


def _version_sort_key(row: Dict[str, Any]) -> Tuple[float, int]:
    """版本排序：``created_at DESC, document_version_id DESC``（§四十二）。

    时间缺失退到纪元（排最后），id 兜底保证顺序唯一。
    """
    moment = _version_created_at(row) or _epoch()
    return (-moment.timestamp(), -(_int_or_none(row.get("document_version_id")) or 0))


# ---- 服务 ------------------------------------------------------------------


class MaterialDetailQueryService:
    """材料详情只读查询。构造时传入一个 asyncpg 连接。

    ``self._conn`` 只需要实现 ``fetch(sql, *args)``。
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ==== 槽位行（三个接口共用的授权与详情入口） ============================

    async def load_slot_row(self, slot_id: str) -> Optional[Dict[str, Any]]:
        """按 id 读一条槽位行。

        单独抽出来是因为三个接口都要先确认"这条槽位存在吗、我能看吗"。
        三处各写一条 SELECT 必然漂移，最后表现为"详情能打开、版本历史 404"。
        """
        rows = await self._conn.fetch(
            f"""
            SELECT {_SLOT_COLUMNS}
            FROM material_slots
            WHERE id = $1
            """,
            slot_id,
        )
        for row in rows:
            return row
        return None

    # ==== 接口四：单位多年度时间轴 ==========================================

    async def unit_timeline(
        self,
        *,
        unit_id: str,
        unit_name: Optional[str] = None,
        department_name: Optional[str] = None,
        jurisdiction_name: Optional[str] = None,
        visible_org_ids: Optional[Sequence[str]] = None,
    ) -> UnitTimelineData:
        """某单位的多年度材料时间轴。一条 SQL。

        身份**只**按 ``subject_org_id = unit_id``，不按单位名称查询：
        组织目录重名（部门与同名本级单位）在真实数据里是常态，
        按名称查会把两个不同的主体混成一串。

        ``visible_org_ids`` 仍然带上（非管理员时）：路由层已经校验过
        ``unit_id`` 在可见范围内，这里的过滤是**第二道**，防止将来有人
        改了路由层而忘了改这里。
        """
        where, params = self._timeline_where(
            unit_id=unit_id, visible_org_ids=visible_org_ids
        )
        rows = await self._conn.fetch(
            f"""
            SELECT {_SLOT_COLUMNS}
            FROM material_slots
            WHERE {where}
            """,
            *params,
        )
        return self.aggregate_unit_timeline(
            rows,
            unit_id=unit_id,
            unit_name=unit_name,
            department_name=department_name,
            jurisdiction_name=jurisdiction_name,
        )

    @staticmethod
    def _timeline_where(
        *,
        unit_id: str,
        visible_org_ids: Optional[Sequence[str]],
    ) -> Tuple[str, List[Any]]:
        clauses = ["subject_org_id = $1"]
        params: List[Any] = [unit_id]
        if visible_org_ids is not None:
            params.append(list(visible_org_ids))
            index = len(params)
            clauses.append(
                "(jurisdiction_org_id = ANY(${index}::text[])"
                " OR department_org_id = ANY(${index}::text[])"
                " OR subject_org_id = ANY(${index}::text[]))".format(index=index)
            )
        return " AND ".join(clauses), params

    @staticmethod
    def aggregate_unit_timeline(
        rows: Iterable[Dict[str, Any]],
        *,
        unit_id: str,
        unit_name: Optional[str] = None,
        department_name: Optional[str] = None,
        jurisdiction_name: Optional[str] = None,
    ) -> UnitTimelineData:
        """把槽位行拼成按财政年度分组的行（纯函数）。

        三条不可让步的规则：

        1. **按 ``fiscal_year`` 排序**（降序），与发布时间无关；
        2. **未知年度不兜底**：``fiscal_year IS NULL`` 的槽位进
           ``unresolved_year_slots``，不塞进 2000 / 0 / 当前年份；
        3. **多个槽位不静默丢弃**：同一（年度 × 文种）下有几条就给几条，
           由界面显示"另有 N 个待确认槽位"，而不是挑一条显示。

        主体名/部门名/区县名优先取调用方传入的组织目录名，其次取槽位里的
        名称快照，最后才退化成 id。
        """
        by_year: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
        unresolved: List[Dict[str, Any]] = []
        seen_subject_name: Optional[str] = None
        seen_subject_kind: Optional[str] = None
        seen_department_name: Optional[str] = None
        seen_jurisdiction_name: Optional[str] = None

        for row in rows:
            subject = str(row.get("subject_org_id") or "")
            if not subject:
                raise ValueError("material_slots.subject_org_id 为空，无法归入单位时间轴")
            if subject != str(unit_id):
                # 身份只按 id：出现别的 subject 说明 SQL 过滤被改坏了。
                # 静默混入会让时间轴看起来正常但内容错位，因此显式失败。
                raise ValueError("单位时间轴混入了其它主体的槽位")

            seen_subject_name = seen_subject_name or _text_or_none(row.get("subject_org_name"))
            seen_subject_kind = seen_subject_kind or _text_or_none(row.get("subject_kind"))
            seen_department_name = seen_department_name or _text_or_none(
                row.get("department_name")
            )
            seen_jurisdiction_name = seen_jurisdiction_name or _text_or_none(
                row.get("jurisdiction_name")
            )

            year = _int_or_none(row.get("fiscal_year"))
            if year is None:
                unresolved.append(row)
                continue
            bucket = by_year.setdefault(year, {"budget": [], "final": [], "unknown": []})
            kind = str(row.get("report_kind") or "unknown")
            # 取值域之外的文种不归入预算/决算任何一边，但也不丢弃：
            # 它恰恰是最需要人工确认的一批。
            bucket.get(kind, bucket["unknown"]).append(row)

        years: List[TimelineYearRow] = []
        for year in sorted(by_year.keys(), reverse=True):
            bucket = by_year[year]
            years.append(
                TimelineYearRow(
                    fiscal_year=year,
                    budget_slots=[_slot_summary(row) for row in _sort_slots(bucket["budget"])],
                    final_slots=[_slot_summary(row) for row in _sort_slots(bucket["final"])],
                    unclassified_slots=[
                        _slot_summary(row) for row in _sort_slots(bucket["unknown"])
                    ],
                )
            )

        return UnitTimelineData(
            unit=TimelineUnitRef(
                unit_id=str(unit_id),
                unit_name=str(unit_name or seen_subject_name or unit_id),
                subject_kind=str(seen_subject_kind or "unknown"),
                department_id=None,
                department_name=_text_or_none(department_name) or seen_department_name,
                jurisdiction_id=None,
                jurisdiction_name=_text_or_none(jurisdiction_name) or seen_jurisdiction_name,
            ),
            years=years,
            unresolved_year_slots=[_slot_summary(row) for row in _sort_slots(unresolved)],
        )

    # ==== 版本 + 运行（一条 join 查询） =====================================

    async def load_version_run_rows(self, slot_id: str) -> List[Dict[str, Any]]:
        """槽位下全部文件版本，以及每个版本**精确关联**的分析运行。一条 SQL。

        ``LEFT JOIN`` 而不是 ``JOIN``：没有关联运行的版本（刚上传、还没分析）
        必须照样出现在版本历史里。用 ``JOIN`` 会让"还没跑过分析"的版本
        从历史里消失，而它正是用户最需要看到的那一条。

        关联谓词写进 ``ON`` 而不是 ``WHERE``：放进 ``WHERE`` 会把
        ``j`` 为 NULL 的行一起滤掉，等价于把 LEFT JOIN 降级成 INNER JOIN。

        没有 ``ORDER BY``：排序只在 ``run_sort_key`` / ``_version_sort_key``
        一处存在。两处各写一份必然漂移，而"哪次运行是当前分析"最不能漂。

        只读：本模块绝不删除、不清理、不改写历史版本。第四轮已经锁死
        "Material Slot 绑定版本不能被 structured cleanup 删除"，
        这里连 UPDATE 语句都不存在。

        ``j.analysis_revision``（WP3-A 分析代际）刻意放在**这一行查询**里，
        而不是让复核服务另查一次：''"当前运行是哪一代"''是这一行的属性，
        分两次查会在两次查询之间留下"运行已经换代"的窗口，
        而窗口期做出的复核结论会绑到错误的代际上。

        注意：投影列表里**不能**写 SQL 注释。WP2-B 的真值校验器逐列检查
        "聚合列是否带表别名"，多一行注释就会被判成没有别名的列——
        这条说明因此只能待在文档字符串里。
        """
        return list(
            await self._conn.fetch(
                f"""
                SELECT v.id AS document_version_id,
                       v.document_id,
                       v.file_hash,
                       v.original_filename,
                       v.file_size_bytes,
                       v.content_type,
                       v.storage_backend,
                       v.created_at AS version_created_at,
                       j.id,
                       j.job_uuid,
                       j.status,
                       j.mode,
                       j.started_at,
                       j.completed_at,
                       j.created_at,
                       j.updated_at,
                       j.error_message,
                       j.metadata,
                       j.analysis_revision,
                       r.id AS result_id,
                       r.ai_findings,
                       r.rule_findings,
                       r.merged_result
                FROM fiscal_document_versions v
                LEFT JOIN analysis_jobs j
                       ON {_NUMERIC_GUARD}
                      AND {_STRUCTURED_VERSION_JSON_PATH}::bigint = v.id
                LEFT JOIN analysis_results r ON r.job_id = j.id
                WHERE v.slot_id = $1
                """,
                slot_id,
            )
        )

    async def load_sources(self, slot_id: str) -> List[MaterialSourceItem]:
        """材料来源。一条 SQL。排序在 Python（``discovered_at`` 降序，id 兜底）。"""
        rows = await self._conn.fetch(
            """
            SELECT id::text AS source_id,
                   source_kind,
                   source_url,
                   source_page_title,
                   source_site,
                   published_at,
                   discovered_at,
                   last_checked_at,
                   source_page_hash,
                   status
            FROM material_sources
            WHERE slot_id = $1
            """,
            slot_id,
        )
        items = [_source_item(row) for row in rows]
        items.sort(key=_source_sort_key)
        return items

    async def slot_detail(self, slot_row: Dict[str, Any]) -> SlotDetailData:
        """槽位详情：槽位 + 当前版本 + 来源 + 当前分析。三条 SQL。

        ``slot_row`` 由调用方传入（路由层为鉴权已经读过一次）：
        同一个投影读两遍只会让"授权时看到的身份"和"展示时用的身份"
        有机会不一致。

        当前分析严格绑定 ``current_document_version_id``：版本被替换后，
        上一版的分析只出现在「版本历史」与「处理记录」里并标注"历史版本"，
        **绝不**冒充当前文件的结论（§二十九/§三十）。
        """
        slot_id = str(slot_row.get("slot_id") or "")
        sources = await self.load_sources(slot_id)
        rows = await self.load_version_run_rows(slot_id)

        version_rows = _version_rows(rows)
        ordered_versions = sorted(version_rows, key=_version_sort_key)
        current_pointer = _int_or_none(slot_row.get("current_document_version_id"))

        current_row: Optional[Dict[str, Any]] = None
        if current_pointer is not None:
            for row in ordered_versions:
                if _int_or_none(row.get("document_version_id")) == current_pointer:
                    current_row = row
                    break

        historical_count = len(
            [
                row
                for row in ordered_versions
                if _int_or_none(row.get("document_version_id")) != current_pointer
            ]
        )

        analysis = self._build_analysis(
            rows=rows,
            current_pointer=current_pointer,
            has_current_version_row=current_row is not None,
        )

        return SlotDetailData(
            slot=_slot_summary(slot_row),
            current_version=(
                _version_item(current_row, is_current=True) if current_row else None
            ),
            historical_version_count=historical_count,
            version_total=len(ordered_versions),
            sources=sources,
            current_analysis=analysis,
        )

    def _build_analysis(
        self,
        *,
        rows: Sequence[Dict[str, Any]],
        current_pointer: Optional[int],
        has_current_version_row: bool,
    ) -> AnalysisBlock:
        """构造当前分析块。不可用时整体置空，绝不部分填充。"""
        if current_pointer is None or not has_current_version_row:
            # 槽位没有当前文件版本（或指针指向的版本行已经不在了）。
            # 这两种情况都不能拿"最近一次分析"顶替：那是别的文件的结论。
            return _empty_analysis(ANALYSIS_REASON_NO_CURRENT_VERSION)

        linked = [
            row
            for row in rows
            if _int_or_none(row.get("document_version_id")) == current_pointer
            and _int_or_none(row.get("id")) is not None
        ]
        current_run = select_current_run(linked)
        if current_run is None:
            return _empty_analysis(ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION)

        run_item = _run_item(current_run, current_version_id=current_pointer)
        metadata = _json_dict(current_run.get("metadata"))
        result_meta = _json_dict(metadata.get("result_meta"))

        if _int_or_none(current_run.get("result_id")) is None:
            # 运行存在但没有持久化结果：可以展示"跑了什么"，但**不能**给出
            # 问题数或覆盖率 —— 没有结果时 0 会被读成"查过、没问题"。
            return AnalysisBlock(
                available=False,
                reason=ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
                run=run_item,
                formal_findings=None,
                manual_review_items=None,
                info_findings=None,
                formal_issue_count=None,
                coverage=_unavailable_coverage(COVERAGE_REASON_NONE_FOR_CURRENT_VERSION),
            )

        formal, manual_review, info_items = partition_findings(
            current_run.get("ai_findings"), current_run.get("rule_findings")
        )

        return AnalysisBlock(
            available=True,
            reason=None,
            run=run_item,
            formal_findings=formal,
            manual_review_items=manual_review,
            info_findings=info_items,
            # 只有走到这里（当前版本 → 精确运行 → 已落库结果 → 正式门禁）
            # 才允许出现整数 0：0 的含义是"算过且确实没有正式 finding"。
            #
            # 计数**必须**走仓库唯一权威函数，不能用 len(formal)：
            # formal 桶是展示分组（把 info 拆了出去），而权威语义是
            # "没有被 evidence guard 降级就是正式 finding"（info 也算）。
            # 用 len(formal) 会让同一份分析在这里与审核工作台给出两个问题数。
            formal_issue_count=count_canonical_formal_findings(
                current_run.get("ai_findings"), current_run.get("rule_findings")
            ),
            coverage=build_coverage(result_meta.get("obligation_coverage")),
        )

    async def versions(self, slot_row: Dict[str, Any]) -> List[MaterialVersionItem]:
        """版本历史。一条 SQL（``slot_row`` 由路由层传入）。"""
        slot_id = str(slot_row.get("slot_id") or "")
        rows = await self.load_version_run_rows(slot_id)
        current_pointer = _int_or_none(slot_row.get("current_document_version_id"))
        return version_items(
            _version_rows(rows), current_document_version_id=current_pointer
        )

    async def run_history(self, slot_row: Dict[str, Any]) -> List[RunSummaryItem]:
        """槽位下全部运行（跨全部历史版本）。一条 SQL。

        每条运行都标明 ``is_current_document_version``：界面必须能把
        "当前文件版本的运行"和"历史文件版本的运行"分开显示，
        混成一列会让用户以为上一版的结论是这一版的（§四十八）。

        排序**先在行上做**（``run_sort_key``），再按同一顺序投影成 DTO：
        DTO 里没有 ``analysis_jobs.id``，如果改成对 DTO 排序，就得换一个
        兜底键，"处理记录"的顺序与"当前分析选的是哪次"就会各有一套规则。
        """
        slot_id = str(slot_row.get("slot_id") or "")
        rows = await self.load_version_run_rows(slot_id)
        current_pointer = _int_or_none(slot_row.get("current_document_version_id"))
        ordered = sorted(
            (row for row in rows if _int_or_none(row.get("id")) is not None),
            key=run_sort_key,
        )
        return [_run_item(row, current_version_id=current_pointer) for row in ordered]


# ---- 行 -> DTO --------------------------------------------------------------


def _version_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 join 结果里取出「一行一个版本」的投影。

    join 后每个版本可能出现多行（多个运行），版本字段在每行上重复。
    这里先按版本去重，再由 ``_version_sort_key`` 排序 —— 去重与排序都只有
    这一处，避免"版本数在详情页和版本页不一样"。
    """
    seen: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        version_id = _int_or_none(row.get("document_version_id"))
        if version_id is None or version_id in seen:
            continue
        seen[version_id] = row
    return list(seen.values())


def _source_sort_key(item: MaterialSourceItem) -> Tuple[float, str]:
    moment = item.discovered_at if isinstance(item.discovered_at, datetime) else None
    return (-(moment.timestamp() if moment else 0.0), item.source_id)


def _sort_slots(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一（年度 × 文种）下多条槽位的展示顺序。

    与 WP2-A 的 ``_slot_preference`` 同一取向：身份已确认的槽位
    （``mapping_key = ''``）排在按具体文档临时安置的占位槽位之前，
    再按更新时间降序，最后用 ``slot_id`` 兜底保证顺序唯一。
    """
    return sorted(
        rows,
        key=lambda row: (
            0 if str(row.get("mapping_key") or "") == "" else 1,
            -(_moment(row.get("updated_at")) or _epoch()).timestamp(),
            str(row.get("slot_id") or ""),
        ),
    )


def _source_item(row: Dict[str, Any]) -> MaterialSourceItem:
    return MaterialSourceItem(
        source_id=str(row.get("source_id") or ""),
        source_kind=str(row.get("source_kind") or "manual_upload"),
        source_url=_text_or_none(row.get("source_url")),
        source_page_title=_text_or_none(row.get("source_page_title")),
        source_site=_text_or_none(row.get("source_site")),
        published_at=_moment(row.get("published_at")),
        discovered_at=_moment(row.get("discovered_at")),
        last_checked_at=_moment(row.get("last_checked_at")),
        source_page_hash=_text_or_none(row.get("source_page_hash")),
        status=str(row.get("status") or "active"),
    )


def _version_item(row: Dict[str, Any], *, is_current: bool) -> MaterialVersionItem:
    return MaterialVersionItem(
        document_version_id=_int_or_none(row.get("document_version_id")) or 0,
        document_id=_int_or_none(row.get("document_id")) or 0,
        file_hash=_text_or_none(row.get("file_hash")),
        original_filename=_text_or_none(row.get("original_filename")),
        file_size_bytes=_int_or_none(row.get("file_size_bytes")),
        content_type=_text_or_none(row.get("content_type")),
        storage_backend=_text_or_none(row.get("storage_backend")),
        created_at=_version_created_at(row),
        is_current=is_current,
        # 版本级安全预览入口不在本轮范围（见模块 docstring 的显式决定）。
        # 保持 None 而不是"留待前端拼一个"，是为了让 fail-closed 只发生在一处。
        preview_url=None,
        download_url=None,
    )


def version_items(
    rows: Sequence[Dict[str, Any]], *, current_document_version_id: Optional[int]
) -> List[MaterialVersionItem]:
    """版本行 → DTO 列表（含 ``is_current`` 标记）。

    ``is_current`` 只是把槽位上的唯一指针投影成每行一个布尔；
    它**不是**第二个真值来源（§二十二）。
    """
    ordered = sorted(rows, key=_version_sort_key)
    items: List[MaterialVersionItem] = []
    for row in ordered:
        version_id = _int_or_none(row.get("document_version_id"))
        items.append(
            _version_item(
                row,
                is_current=(
                    version_id is not None and version_id == current_document_version_id
                ),
            )
        )
    return items


def _safe_error_summary(value: Any) -> Optional[str]:
    """``analysis_jobs.error_message`` → 面向普通用户的固定安全摘要。

    **fail-closed：只要数据库里有非空 error_message，一律返回固定文案，
    不回显原文的任何片段。**

    为什么不做"看起来安全就放行"的判断
    ----------------------------------
    早期版本用一份"坏形态黑名单"（驱动盘符 / 常见 Unix 路径前缀 / 连接串 /
    Traceback）来决定是否脱敏，并放行其余文本。这条路走不通，原因是原理性的：

    - 黑名单永远补不完：``E:\\`` ``F:\\`` ``Z:\\``、``/tmp/`` ``/mnt/`` ``/usr/``
      ``/srv/``、UNC ``\\\\server\\share`` —— 而且本项目的开发环境本身就在
      ``E:\\Software Development\\...`` 下，不是理论风险；
    - 错误文本里能出现的敏感信息远超"路径"：连接串、URL、token、用户名、
      数据库名、内部 host、环境变量值、文件名、业务数据片段；
    - "看起来安全的错误"与"不安全的错误"没有稳定分类标准，
      任何部分脱敏都在赌下一版错误消息里不会出现新形态。

    这个接口是**材料详情**，不是运维日志页。因此这里承担的最小责任是：
    告诉用户"处理失败了、去哪里看详情"，把完整错误留在任务日志、
    运维界面与数据库里（信息没有丢失，只是不向普通用户回显）。
    """
    if not str(value or "").strip():
        return None
    return "处理失败（详情见任务日志）"


def _run_item(row: Dict[str, Any], *, current_version_id: Optional[int]) -> RunSummaryItem:
    """运行行 → DTO。

    ``document_version_id`` 直接取 join 出来的 ``v.id``：它就是关联键本身，
    比再从 metadata 里解析一次更不容易错。``structured_document_version_id``
    仍被调用一次，用于把"metadata 写坏"这种情况暴露成可观测信号。
    """
    metadata = _json_dict(row.get("metadata"))
    result_meta = _json_dict(metadata.get("result_meta"))
    quality_gate = _json_dict(result_meta.get("quality_gate"))
    coverage = _json_dict(result_meta.get("obligation_coverage"))
    elapsed = _json_dict(result_meta.get("elapsed_ms"))
    try:
        structured_document_version_id(metadata)
    except UnknownRunMetadataError:  # pragma: no cover - SQL 守卫已排除这类行
        pass

    version_id = _int_or_none(row.get("document_version_id")) or 0
    ai_findings = _json_list(row.get("ai_findings"))
    rule_findings = _json_list(row.get("rule_findings"))

    return RunSummaryItem(
        job_uuid=str(row.get("job_uuid") or ""),
        document_version_id=version_id,
        is_current_document_version=(
            version_id != 0 and version_id == current_version_id
        ),
        status=str(row.get("status") or ""),
        mode=_text_or_none(row.get("mode")),
        started_at=_moment(row.get("started_at")),
        completed_at=_moment(row.get("completed_at")),
        created_at=_moment(row.get("created_at")),
        updated_at=_moment(row.get("updated_at")),
        # 计数来自已落库的 finding 数组本身（不是 UI 再数一遍），
        # 且数组的成员与正式门禁读的是同一批对象。
        ai_findings_count=len(ai_findings),
        rule_findings_count=len(rule_findings),
        merged_findings_count=_merged_findings_count(row),
        has_results=_int_or_none(row.get("result_id")) is not None,
        structured_ingest_status=_text_or_none(
            _json_dict(metadata.get("structured_ingest")).get("status")
        ),
        elapsed_total_ms=_int_or_none(elapsed.get("total")),
        error_summary=_safe_error_summary(row.get("error_message")),
        # 运行结论只来自落库的质量门；旧任务没有这些字段时如实为 null，
        # 不假装它是 done（"没有记录"与"跑成功"是两件事）。
        analysis_status=_text_or_none(quality_gate.get("status")),
        quality_status=_text_or_none(quality_gate.get("quality_status")),
        analysis_conclusion=_text_or_none(quality_gate.get("analysis_conclusion")),
        obligation_catalog_version=_text_or_none(coverage.get("catalog_version")),
        # 运行列表不解结论：正式问题数只在槽位详情的"当前分析"里给。
        formal_issue_count=None,
    )


def _merged_findings_count(row: Dict[str, Any]) -> Optional[int]:
    """合并后的问题数。

    只取 ``merged_result.totals.merged``（仓库既有口径，见任务列表接口）；
    取不到时**返回 None**，不拿 ``ai + rule`` 相加冒充 —— 合并会去重，
    相加得到的是另一个数，混用会让两个页面的"问题数"对不上。
    """
    totals = _json_dict(_json_dict(row.get("merged_result")).get("totals"))
    return _int_or_none(totals.get("merged"))


# ---- finding 分桶（唯一实现处） ----------------------------------------------

#: 严重度归并桶。与 ``src/engine/pipeline.py`` 的 ``_norm_sev`` 同一口径：
#: 两个模块对同一条 finding 的严重度归并必须一致，否则"正式问题"在
#: 检查结果页与导出报告里会显示成不同的数字。
_ERROR_SEVERITIES = frozenset({"error", "err", "fatal", "critical", "high"})
_WARN_SEVERITIES = frozenset({"warn", "warning", "medium", "low", "manual_review"})


def severity_bucket(severity: Any) -> str:
    value = str(severity or "").strip().lower()
    if value in _ERROR_SEVERITIES:
        return "error"
    if value in _WARN_SEVERITIES:
        return "warn"
    return "info"


def partition_findings(
    ai_findings: Any, rule_findings: Any
) -> Tuple[List[AnalysisFindingItem], List[AnalysisFindingItem], List[AnalysisFindingItem]]:
    """把已落库的 finding 分成三个互不重叠的**展示**桶。

    返回 ``(正式问题, 需人工核验, 信息提示)``。

    **这只用于页面分组，不定义"正式 finding"的业务真值。**
    "有多少条正式 finding"的唯一口径是 ``count_formal_findings``
    （见 ``count_canonical_formal_findings``）：只要没有被 evidence guard
    降级就是正式 finding，``info`` 严重度同样计入。这里的 ``formal`` 桶
    额外把 ``info`` 拆出去只是为了让页面能分开显示——
    **绝不允许**用 ``len(formal)`` 当作 formal finding 数去对外报数，
    那会让同一份分析在"审核工作台 / 质量门禁"与"材料详情"上给出两个问题数。

    判定链只有一条：``is_formal_finding``（``evidence_guard`` 的正式门禁）。
    缺证据被降级的条目（``evidence_status='degraded_missing_evidence'``，
    severity 已被改成 ``manual_review``）**不是**正式 finding；它们必须单独成桶，
    否则页面会把"证据不足、待人工核验"显示成"确认存在的问题"，
    或反过来把它算进"没有问题"的分母里。

    三个桶是**划分**（并集 = 全部 finding，两两不交）：

    - ``formal``：正式 finding 且严重度归并桶不是 ``info`` 的条目；
    - ``info``：正式 finding 且严重度归并桶是 ``info`` 的条目；
    - ``manual_review``：未通过正式门禁（缺证据被降级）的条目。

    数据来源只有 ``analysis_results`` 的 ``ai_findings`` / ``rule_findings``
    两列。它们与 ``count_formal_findings`` 读的 ``result.issues.all`` 是同一批
    对象：legacy 路径落库时 ``rule_findings`` 就是 ``issues`` 的展平去重结果
    （``analysis_result_store._flatten_legacy_issues``），双模式路径则本来
    就没有 ``issues`` 键，走的就是这两列。
    """
    formal: List[AnalysisFindingItem] = []
    manual_review: List[AnalysisFindingItem] = []
    info_items: List[AnalysisFindingItem] = []

    for bucket in (ai_findings, rule_findings):
        for raw in _json_list(bucket):
            if not isinstance(raw, dict):
                continue
            item = _finding_item(raw)
            if not is_formal_finding(raw):
                manual_review.append(item)
            elif item.severity_bucket == "info":
                info_items.append(item)
            else:
                formal.append(item)

    return formal, manual_review, info_items


def count_canonical_formal_findings(ai_findings: Any, rule_findings: Any) -> int:
    """正式 finding 数：**直接**调用仓库唯一权威函数 ``count_formal_findings``。

    为什么必须走这一步而不是 ``len(partition_findings(...)[0])``：

    ``partition_findings`` 的第一桶是**展示**用的"正式问题"（把 ``info``
    拆到另一桶），而权威语义是"没有被 evidence guard 降级就是正式 finding"，
    ``error`` / ``warn`` / ``info`` 三种严重度**都算**。一旦用 ``len(formal)``
    对外报数，同一份分析会在审核工作台/质量门禁与材料详情上给出两个不同的
    问题数——这是禁止的口径分裂。

    这里把已落库的两列拼成 ``count_formal_findings`` 认识的结果形态再交给它，
    保证本接口与工作台用的是同一段判定代码，而不是同一套"看起来一样"的规则。
    """
    return count_formal_findings(
        {
            "ai_findings": _json_list(ai_findings),
            "rule_findings": _json_list(rule_findings),
        }
    )


def _finding_item(finding: Dict[str, Any]) -> AnalysisFindingItem:
    page, bbox, text = _evidence_pointer(finding)
    return AnalysisFindingItem(
        finding_id=_text_or_none(finding.get("id")),
        title=_text_or_none(finding.get("title")),
        message=_text_or_none(finding.get("message") or finding.get("description")),
        severity=_text_or_none(finding.get("severity")),
        severity_bucket=severity_bucket(finding.get("severity")),
        source=_text_or_none(finding.get("source")),
        rule_id=_text_or_none(finding.get("rule_id") or finding.get("rule")),
        obligation_ids=_string_list(finding.get("obligation_ids")),
        evidence_page=page,
        evidence_bbox=bbox,
        evidence_text=text,
        evidence_status=_text_or_none(finding.get("evidence_status")),
        evidence_missing=_string_list(finding.get("evidence_missing")),
        tags=_string_list(finding.get("tags")),
        why_not=_text_or_none(finding.get("why_not")),
    )


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _evidence_pointer(
    finding: Dict[str, Any],
) -> Tuple[Optional[int], Optional[List[float]], Optional[str]]:
    """提取"能定位到 PDF 的"最小证据指针：页码 / bbox / 文本片段。

    复用 ``evidence_guard`` 已有的字段形态（``page_number`` / ``location.page``
    / ``evidence[].page`` 等），不重写一套定位器 —— 证据校验怎么找得到，
    详情页就怎么找得到。页码缺失时如实为 ``None``，不猜第 1 页。
    """
    page = _int_or_none(finding.get("page_number"))
    bbox = _valid_bbox(finding.get("bbox"))
    location = finding.get("location")
    if isinstance(location, dict):
        page = page or _int_or_none(location.get("page"))
        bbox = bbox or _valid_bbox(location.get("bbox"))
        refs = location.get("table_refs")
        if page is None and isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, dict):
                    page = _int_or_none(ref.get("page"))
                    if page is not None:
                        break
    text = _text_or_none(finding.get("text_snippet"))
    evidence = finding.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            page = page or _int_or_none(item.get("page"))
            bbox = bbox or _valid_bbox(item.get("bbox"))
            if text is None:
                for key in ("text", "raw_text", "snippet", "quote", "value"):
                    text = _text_or_none(item.get(key))
                    if text is not None:
                        break
    if text is not None and len(text) > EVIDENCE_TEXT_LIMIT:
        text = text[:EVIDENCE_TEXT_LIMIT] + "…"
    return page, bbox, text


def _valid_bbox(value: Any) -> Optional[List[float]]:
    """与 ``evidence_guard._is_valid_bbox`` 同一判定：四点、有限、面积为正。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if numbers[2] <= numbers[0] or numbers[3] <= numbers[1]:
        return None
    return numbers


# ---- 检查覆盖 ---------------------------------------------------------------


def _unavailable_coverage(reason: str) -> CoverageBlock:
    """不可用的覆盖块：整体为空，绝不返回 0/0 或 100%。"""
    return CoverageBlock(available=False, reason=reason, summary=None, items=[])


def _empty_analysis(reason: str) -> AnalysisBlock:
    return AnalysisBlock(
        available=False,
        reason=reason,
        run=None,
        formal_findings=None,
        manual_review_items=None,
        info_findings=None,
        formal_issue_count=None,
        coverage=_unavailable_coverage(COVERAGE_REASON_NONE_FOR_CURRENT_VERSION),
    )


def build_coverage(raw: Any) -> CoverageBlock:
    """把已落库的 obligation ledger 转成覆盖块。

    数据来源是 ``analysis_jobs.metadata.result_meta.obligation_coverage``：
    引擎在 ``api/main.py`` 里把整份账本写进 ``result['meta']['obligation_coverage']``，
    而整份 ``result.meta`` 又被落进 ``metadata.result_meta``。它天然属于
    "某次运行"，而运行已经精确绑定到当前文件版本，因此这里的数据可以
    合法地称为"**这份材料**的检查覆盖"——不是"系统总共有多少规则"。

    拿不到 ledger（旧任务、或结果里没有这个键）时返回 ``available=false``
    + ``reason``，**不**返回 0/0："没有覆盖记录"与"覆盖完整"在数字上
    不能长得一样。
    """
    payload = _json_dict(raw)
    if not payload:
        return _unavailable_coverage(COVERAGE_REASON_NONE_FOR_CURRENT_VERSION)

    applicable = _int_or_none(payload.get("applicable_total"))
    completed = _int_or_none(payload.get("completed_total"))
    not_applicable = _int_or_none(payload.get("not_applicable_total"))
    unresolved = _int_or_none(payload.get("unresolved_total"))
    blocking = _int_or_none(payload.get("blocking_total"))
    if None in (applicable, completed, not_applicable, unresolved, blocking):
        # 关键计数缺失：整块退回"不可用"，不做部分填充。
        return _unavailable_coverage(COVERAGE_REASON_NONE_FOR_CURRENT_VERSION)

    assert applicable is not None and completed is not None
    assert not_applicable is not None and unresolved is not None and blocking is not None

    by_reason_raw = _json_dict(payload.get("by_reason"))
    by_reason: Dict[str, int] = {}
    for key, value in by_reason_raw.items():
        count = _int_or_none(value)
        if count is not None:
            by_reason[str(key)] = count

    groups: List[CoverageGroupItem] = []
    for row in _json_list(payload.get("by_group")):
        if not isinstance(row, dict):
            continue
        group_id = _text_or_none(row.get("group_id"))
        if group_id is None:
            continue
        groups.append(
            CoverageGroupItem(
                group_id=group_id,
                group_title=_text_or_none(row.get("group_title")),
                applicable=_int_or_none(row.get("applicable")) or 0,
                completed=_int_or_none(row.get("completed")) or 0,
                not_applicable=_int_or_none(row.get("not_applicable")) or 0,
                unresolved=_int_or_none(row.get("unresolved")) or 0,
            )
        )

    items: List[CoverageObligationItem] = []
    for row in _json_list(payload.get("instances")):
        if not isinstance(row, dict):
            continue
        obligation_id = _text_or_none(row.get("obligation_id"))
        if obligation_id is None:
            continue
        items.append(
            CoverageObligationItem(
                obligation_id=obligation_id,
                group_id=_text_or_none(row.get("group_id")),
                group_title=_text_or_none(row.get("group_title")),
                title=_text_or_none(row.get("title")),
                status=str(row.get("status") or ""),
                reason=_text_or_none(row.get("reason")),
                reason_label=_text_or_none(row.get("reason_label")),
                detail=_text_or_none(row.get("detail")),
                blocks_gate=bool(row.get("blocks_gate")),
                requires_ai=bool(row.get("requires_ai")),
                input_gaps=_string_list(row.get("input_gaps")),
            )
        )

    return CoverageBlock(
        available=True,
        reason=None,
        summary=CoverageSummaryBlock(
            catalog_version=_text_or_none(payload.get("catalog_version")),
            catalog_fingerprint=_text_or_none(payload.get("catalog_fingerprint")),
            applicable_total=applicable,
            completed_total=completed,
            not_applicable_total=not_applicable,
            unresolved_total=unresolved,
            blocking_total=blocking,
            # 0 分母时引擎已经给 None（见 check_obligations._build_summary），
            # 这里不再二次计算：多处各算一次"完成率"必然漂移。
            coverage_rate=_float_or_none(payload.get("coverage_rate")),
            auto_completion_rate=_float_or_none(payload.get("auto_completion_rate")),
            by_reason=by_reason,
            by_group=groups,
        ),
        items=items,
    )


__all__ = [
    "EVIDENCE_TEXT_LIMIT",
    "UnknownRunMetadataError",
    "MaterialDetailQueryService",
    "structured_document_version_id",
    "collect_legacy_metadata_warnings",
    "run_effective_moment",
    "run_sort_key",
    "select_current_run",
    "severity_bucket",
    "partition_findings",
    "count_canonical_formal_findings",
    "build_coverage",
    "version_items",
]
