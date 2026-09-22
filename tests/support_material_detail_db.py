"""材料详情只读接口测试用的假数据库连接（WP2-B）。

与 ``support_material_ledger_db`` 的关系
----------------------------------------
WP2-A 的假连接服务"单表 + GROUP BY 聚合"；WP2-B 的查询是
"槽位行 + 来源 + 版本 LEFT JOIN 运行 LEFT JOIN 结果"，形态完全不同。
强行塞进一个类会让两边的解析都含混，因此单独一个假连接，但**沿用同一套纪律**：

1. **不认识的 SQL 直接报错**，不返回空列表 —— 生产 SQL 改了而假连接没跟上时，
   测试必须失败而不是绿灯。
2. **投影按 SQL 的 SELECT 列表来。** 服务层少选一列，这里就少给一列，
   读取侧会拿到 KeyError 或 None，而不是被悄悄补上默认值。
3. **真的做 JOIN 与过滤。** ``LEFT JOIN`` 的三态（有版本没运行 / 有运行没结果 /
   有结果）都会真实发生，因此"版本还没跑过分析"这类场景能被测到。
4. **不模拟 URI 级细节**（表达式索引、CHECK 约束、JSONB 的存储形态），
   因此它替代不了真库验证；真库验证由 ``test_material_ledger_detail_pg.py``
   承担（需要 ``GOVBUDGET_TEST_DATABASE_URL``）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "UnknownDetailSqlError",
    "FakeDetailConnection",
    "make_slot_row",
    "make_source_row",
    "make_version_row",
    "make_job_row",
    "make_result_row",
]


class UnknownDetailSqlError(AssertionError):
    """假连接收到不认识的语句。必须是失败，不能返回空结果。"""


#: 精确关联字段的 JSON 路径。与服务的 SQL 逐字对应；写错这里
#: 会让假连接"永远关联不上"，测试立刻暴露。
_JSON_PATH_RE = re.compile(
    r"\(j\.metadata\s*->\s*'structured_ingest'\s*->>\s*'document_version_id'\)"
)
_NUMERIC_GUARD_RE = re.compile(rf"{_JSON_PATH_RE.pattern}\s*~\s*'\^\[0-9\]\+\$'")

_SLOT_WHERE_RE = re.compile(r"FROM material_slots\s+WHERE\s+(?P<column>[a-z_]+)\s*=\s*\$1")
_SOURCE_WHERE_RE = re.compile(r"FROM material_sources\s+WHERE\s+(?P<column>[a-z_]+)\s*=\s*\$1")
_JOIN_WHERE_RE = re.compile(r"WHERE\s+v\.slot_id\s*=\s*\$1")


# ---- 行构造器 --------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def make_slot_row(**overrides: Any) -> Dict[str, Any]:
    """一条槽位行（字段与 ``material_slots`` 列一一对应）。

    同时带 ``id``（真库列名）与 ``slot_id``（查询里的 ``id::text AS slot_id``
    别名）：纯聚合测试直接喂这个形状，走假连接时按 ``id`` 过滤。
    两者默认同值 —— 只给一个会让"测试里喂了一个列、服务读另一个列"
    变成假绿。
    """
    if "slot_id" in overrides and "id" not in overrides:
        overrides = {**overrides, "id": overrides["slot_id"]}
    row: Dict[str, Any] = {
        "id": "slot-1",
        "slot_key": "slot-key-1",
        "mapping_key": "",
        "jurisdiction_org_id": None,
        "jurisdiction_name": None,
        "department_org_id": None,
        "department_name": None,
        "subject_org_id": "org-1",
        "subject_org_name": "主体一",
        "subject_kind": "unit",
        "material_scope": "unit_self",
        "fiscal_year": 2025,
        "report_kind": "budget",
        "caliber": "self",
        "caliber_conflict_candidate": None,
        "status": "uploaded",
        "status_reason": "awaiting_analysis",
        "applicability_status": "applicable",
        "applicability_note": None,
        "due_at": None,
        "current_document_version_id": None,
        "updated_at": None,
    }
    row.update(overrides)
    row.setdefault("slot_id", row["id"])
    return row


def make_source_row(**overrides: Any) -> Dict[str, Any]:
    """一条来源行（字段与 ``material_sources`` 列一致）。

    真库列名是 ``id``，SQL 投影成别名 ``source_id``；这里两者同时给且同值，
    避免"测试喂了一个列、服务读另一个列"变成假绿。
    """
    if "source_id" in overrides and "id" not in overrides:
        overrides = {**overrides, "id": overrides["source_id"]}
    row: Dict[str, Any] = {
        "id": "source-1",
        "slot_id": "slot-1",
        "source_kind": "official_site",
        "source_url": "https://www.shpt.gov.cn/xxgk/1.html",
        "source_page_title": "某栏目页",
        "source_site": "上海普陀",
        "published_at": _utc(2025, 8, 20),
        "discovered_at": _utc(2026, 9, 20, 15, 38),
        "last_checked_at": None,
        "source_page_hash": None,
        "status": "active",
    }
    row.update(overrides)
    return row


def make_version_row(**overrides: Any) -> Dict[str, Any]:
    """一条文件版本行（列名与 ``fiscal_document_versions`` 一致）。

    ``version_created_at`` 是 join 查询里的别名（``v.created_at``）：
    直接叫 ``created_at`` 会与任务的 ``j.created_at`` 撞名。
    """
    row: Dict[str, Any] = {
        "document_version_id": 11,
        "document_id": 1,
        "file_hash": "a" * 64,
        "original_filename": "2024年度单位决算.pdf",
        "file_size_bytes": 1024,
        "content_type": "application/pdf",
        "storage_backend": "filesystem",
        "version_created_at": _utc(2026, 9, 20, 15, 38),
        "slot_id": "slot-1",
    }
    row.update(overrides)
    return row


def make_job_row(**overrides: Any) -> Dict[str, Any]:
    """一条分析任务行。

    ``metadata`` 默认带 ``structured_ingest.document_version_id``，
    因为"有精确关联"才是正常形态；构造 legacy 任务时显式传
    ``metadata={}`` 或删掉该字段。
    """
    row: Dict[str, Any] = {
        "id": 501,
        "job_uuid": "job-501",
        "status": "done",
        "mode": "dual",
        "started_at": _utc(2026, 9, 20, 15, 40),
        "completed_at": _utc(2026, 9, 20, 15, 42),
        "created_at": _utc(2026, 9, 20, 15, 39),
        "updated_at": _utc(2026, 9, 20, 15, 42),
        "error_message": None,
        "metadata": {
            "structured_ingest": {"document_version_id": 11, "status": "done"},
            "result_meta": {"elapsed_ms": {"total": 12345}},
        },
    }
    row.update(overrides)
    return row


def make_result_row(**overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "result_id": 9001,
        "ai_findings": [],
        "rule_findings": [],
        "merged_result": {"totals": {"merged": 0}},
    }
    row.update(overrides)
    return row


# ---- SQL 解析 --------------------------------------------------------------

_SELECT_RE = re.compile(r"\bSELECT (?P<columns>.+?)\s+FROM ", re.DOTALL)
#: 表别名必须**带点**才算限定符：写成 ``[vjr]?\.?`` 会把 ``jurisdiction_org_id``
#: 的 ``j`` 当成别名吃掉，剩下的列名自然找不到 —— 这类"看起来像解析好了"的
#: 假连接缺陷比直接报错更难查，所以限定符与点绑在一起。
_JOIN_COLUMN_RE = re.compile(
    r"^(?:(?P<qualifier>[vjr])\.)?(?P<source>[a-z_]+)(?:::[a-z_]+)?(?:\s+AS\s+(?P<alias>[a-z_]+))?$"
)
#: join 查询的投影**必须**带表别名。不带别名时 ``v.id`` 与 ``j.id`` 会在
#: 扁平的行字典里互相覆盖 —— 假连接若容忍这种写法，测试就会在
#: "document_version_id 取到了 job id"这种缺陷上给绿灯。
_JOIN_QUALIFIED_RE = re.compile(
    r"^(?P<qualifier>[vjr])\.(?P<source>[a-z_]+)(?:::[a-z_]+)?(?:\s+AS\s+(?P<alias>[a-z_]+))?$"
)


def _normalize(sql: str) -> str:
    return " ".join(str(sql or "").split())


def _project_columns(sql: str) -> List[Tuple[str, str]]:
    """解析 SELECT 列表，返回 ``[(列名, 输出别名)]``。"""
    match = _SELECT_RE.search(sql)
    if match is None:
        raise UnknownDetailSqlError(f"语句没有 SELECT 列表: {sql[:160]}")
    columns: List[Tuple[str, str]] = []
    for raw in match.group("columns").split(","):
        item = raw.strip()
        item = _JSON_PATH_RE.sub("structured_document_version_id", item)
        parsed = _JOIN_COLUMN_RE.match(item)
        if parsed is None:
            raise UnknownDetailSqlError(f"假连接不认识的投影列: {item!r}")
        source = parsed.group("source")
        alias = parsed.group("alias") or source
        columns.append((source, alias))
    return columns


def _project(row: Dict[str, Any], sql: str) -> Dict[str, Any]:
    projected: Dict[str, Any] = {}
    for source, alias in _project_columns(sql):
        if source not in row:
            raise UnknownDetailSqlError(
                f"投影列 {source!r} 在内存行里不存在（假连接与真库列名已不同步）"
            )
        projected[alias] = row[source]
    return projected


def _project_join(joined: Dict[str, Optional[Dict[str, Any]]], sql: str) -> Dict[str, Any]:
    """按表别名分命名空间投影一行 join 结果。

    ``LEFT JOIN`` 未命中的那张表是 ``None``：它的列一律给 ``None``
    （不补 0 也不补空串）—— "没有运行"必须与"运行了但字段为空"可分。
    """
    match = _SELECT_RE.search(sql)
    if match is None:
        raise UnknownDetailSqlError(f"语句没有 SELECT 列表: {sql[:160]}")
    projected: Dict[str, Any] = {}
    for raw in match.group("columns").split(","):
        item = raw.strip()
        parsed = _JOIN_QUALIFIED_RE.match(item)
        if parsed is None:
            raise UnknownDetailSqlError(
                f"join 查询的投影列必须带表别名（v./j./r.）: {item!r}"
            )
        source = parsed.group("source")
        alias = parsed.group("alias") or source
        table = joined.get(parsed.group("qualifier") or "")
        if table is None:
            projected[alias] = None
            continue
        if source not in table:
            raise UnknownDetailSqlError(
                f"投影列 {source!r} 在内存行里不存在（假连接与真库列名已不同步）"
            )
        projected[alias] = table[source]
    return projected


def _structured_version_id(metadata: Any) -> Optional[int]:
    """按 SQL 的守卫语义取版本 id：非纯数字视为"不参与关联"。"""
    payload = metadata
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    structured = payload.get("structured_ingest")
    if not isinstance(structured, dict):
        return None
    raw = structured.get("document_version_id")
    if isinstance(raw, bool) or raw is None:
        return None
    text = str(raw)
    if not re.fullmatch(r"[0-9]+", text):
        return None
    return int(text)


class FakeDetailConnection:
    """记录式假连接：只需要 ``fetch(sql, *args)``。"""

    def __init__(
        self,
        *,
        slots: Optional[Iterable[Dict[str, Any]]] = None,
        sources: Optional[Iterable[Dict[str, Any]]] = None,
        versions: Optional[Iterable[Dict[str, Any]]] = None,
        jobs: Optional[Iterable[Dict[str, Any]]] = None,
        results: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        self.slots: List[Dict[str, Any]] = [dict(row) for row in (slots or [])]
        self.sources: List[Dict[str, Any]] = [dict(row) for row in (sources or [])]
        self.versions: List[Dict[str, Any]] = [dict(row) for row in (versions or [])]
        self.jobs: List[Dict[str, Any]] = [dict(row) for row in (jobs or [])]
        self.results: List[Dict[str, Any]] = [dict(row) for row in (results or [])]
        #: 全部被执行的 (归一化 SQL, 参数)
        self.calls: List[Tuple[str, tuple]] = []

    # ---- 断言辅助 ----------------------------------------------------------

    def executed(self, fragment: str) -> List[Tuple[str, tuple]]:
        return [(sql, args) for sql, args in self.calls if fragment in sql]

    @property
    def sql_count(self) -> int:
        return len(self.calls)

    # ---- asyncpg 兼容接口 --------------------------------------------------

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        normalized = _normalize(sql)
        self.calls.append((normalized, args))

        if "FROM material_slots" in normalized:
            return self._fetch_slots(normalized, args)
        if "FROM material_sources" in normalized:
            return self._fetch_sources(normalized, args)
        if "FROM fiscal_document_versions v" in normalized:
            return self._fetch_version_run_join(normalized, args)
        raise UnknownDetailSqlError(f"假连接不认识这条 fetch 语句: {normalized[:200]}")

    def _fetch_slots(self, sql: str, args: tuple) -> List[Dict[str, Any]]:
        match = _SLOT_WHERE_RE.search(sql)
        if match is None:
            raise UnknownDetailSqlError(f"槽位查询的 WHERE 形态不认识: {sql[:200]}")
        column = match.group("column")
        value = args[0]
        hit = [row for row in self.slots if row.get(column) == value]
        return [_project(row, sql) for row in hit]

    def _fetch_sources(self, sql: str, args: tuple) -> List[Dict[str, Any]]:
        match = _SOURCE_WHERE_RE.search(sql)
        if match is None:
            raise UnknownDetailSqlError(f"来源查询的 WHERE 形态不认识: {sql[:200]}")
        column = match.group("column")
        value = args[0]
        hit = [row for row in self.sources if row.get(column) == value]
        return [_project(row, sql) for row in hit]

    def _fetch_version_run_join(self, sql: str, args: tuple) -> List[Dict[str, Any]]:
        if _JOIN_WHERE_RE.search(sql) is None:
            raise UnknownDetailSqlError(f"版本 join 查询的 WHERE 形态不认识: {sql[:200]}")
        if _NUMERIC_GUARD_RE.search(sql) is None:
            raise UnknownDetailSqlError(
                "版本 join 查询缺少 document_version_id 的数字守卫："
                "非数字 metadata 会在真库上抛 cast 异常"
            )
        slot_id = args[0]
        rows: List[Dict[str, Any]] = []
        for version in self.versions:
            if version.get("slot_id") != slot_id:
                continue
            linked = [
                job
                for job in self.jobs
                if _structured_version_id(job.get("metadata"))
                == version.get("document_version_id")
            ]
            if not linked:
                # LEFT JOIN 的"没有运行"分支：版本必须照样出现。
                rows.append(self._join_row(version, None, None))
                continue
            for job in linked:
                result = next(
                    (item for item in self.results if item.get("job_id") == job.get("id")),
                    None,
                )
                rows.append(self._join_row(version, job, result))
        return [_project_join(row, sql) for row in rows]

    @staticmethod
    def _join_row(
        version: Dict[str, Any],
        job: Optional[Dict[str, Any]],
        result: Optional[Dict[str, Any]],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """拼一行 join 结果（按表别名分命名空间，与真库的列限定同名）。

        ``v`` 是版本行、``j`` 是任务行、``r`` 是结果行；未命中的表是 ``None``。
        分命名空间是必须的：``v.id``（版本 id）与 ``j.id``（任务 id）在真库里
        是两个不同的列，拍平成一个字典就会互相覆盖 —— 那样假连接反而会
        掩盖"把 job id 当成 version id"这类最危险的关联缺陷。
        """
        return {
            "v": {
                "id": version.get("document_version_id"),
                "document_id": version.get("document_id"),
                "file_hash": version.get("file_hash"),
                "original_filename": version.get("original_filename"),
                "file_size_bytes": version.get("file_size_bytes"),
                "content_type": version.get("content_type"),
                "storage_backend": version.get("storage_backend"),
                "created_at": version.get("version_created_at"),
            },
            "j": (
                None
                if job is None
                else {
                    "id": job.get("id"),
                    "job_uuid": job.get("job_uuid"),
                    "status": job.get("status"),
                    "mode": job.get("mode"),
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "created_at": job.get("created_at"),
                    "updated_at": job.get("updated_at"),
                    "error_message": job.get("error_message"),
                    "metadata": job.get("metadata"),
                }
            ),
            "r": (
                None
                if result is None
                else {
                    "id": result.get("result_id"),
                    "ai_findings": result.get("ai_findings"),
                    "rule_findings": result.get("rule_findings"),
                    "merged_result": result.get("merged_result"),
                }
            ),
        }
