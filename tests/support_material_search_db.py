"""全局搜索接口测试用的假数据库连接（WP2-C）。

这一层证明什么、不证明什么（先说清楚，避免"假绿"）
--------------------------------------------------
**证明**（这些是接口层独有的问题）：

1. 路由接线：登录 / 权限范围 / 403 与空结果的区分 / 参数校验 / 响应契约；
2. 权限谓词**真的接线了**：``visible_org_ids`` 是从 scope 传下来的参数，
   假连接从查询里取出这个参数并据此过滤种子行 —— 少传一次就会越权返回；
3. 命中归因与 review candidate 的组装：从 SQL 参数里还原出的 token
   去驱动 Python 侧的匹配与归因（复用生产代码的
   ``ilike_literal`` / ``escape_like``，不另写一套语义）；
4. 语句形态：主查询 + 当前页明细一共两条，不存在"每条结果再查一次"。

**不证明**（由 ``tests/test_material_search_pg.py`` 在真库上证明）：

- ``ILIKE ... ESCAPE`` 的实际行为、排序表达式的优先级、``COUNT(*) OVER ()``
  与越界页兜底、``= ANY(uuid[])`` 的类型推导、JSONB 的存储形态、
  写坏的 metadata 不会打断查询。

假连接**不实现 ORDER BY**：排序名次的断言放在真库用例里（那里才是真相）。
本文件只保证"返回哪些行"与"行怎么被装配成响应"。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.services.material_search_query_service import escape_like, ilike_literal

__all__ = [
    "UnknownSearchSqlError",
    "FakeSearchConnection",
    "make_slot_row",
    "make_version_record",
    "make_job_record",
]


class UnknownSearchSqlError(AssertionError):
    """假连接收到不认识的语句。必须是失败，不能返回空结果。

    生产 SQL 改了而这里没跟上时，测试要**红**：否则"查询形状变了、
    权限谓词没了"这类改动会被一路放过。
    """


#: 主查询里的权限谓词（与 WP2-A 的三列 OR 同源）。三个位置共用一个参数。
_PERMISSION_RE = re.compile(
    r"\(s\.jurisdiction_org_id = ANY\(\$(?P<index>\d+)::text\[\]\)"
    r"\s*OR s\.department_org_id = ANY\(\$\1::text\[\]\)"
    r"\s*OR s\.subject_org_id = ANY\(\$\1::text\[\]\)\)"
)
_FISCAL_YEAR_RE = re.compile(r"s\.fiscal_year = \$(?P<index>\d+)")
_REPORT_KIND_RE = re.compile(r"s\.report_kind = \$(?P<index>\d+)")
#: 关系条件（本部/本级）：单独一条 ``s.subject_org_id = ANY(...)``。
#: 匹配前会先把权限子句整段摘掉，因此这里不必（也不能）用变长后顾断言区分两者。
_RELATION_RE = re.compile(r"s\.subject_org_id = ANY\(\$(?P<index>\d+)::text\[\]\)")
#: 一个 token 块的起始：区县名 ILIKE。
_TOKEN_START = "(s.jurisdiction_name ILIKE "
_PATTERN_PARAM_RE = re.compile(r"\$(?P<index>\d+) ESCAPE '\\'")
_JOB_PARAM_RE = re.compile(r"tj\.job_uuid = \$(?P<index>\d+)")
#: 每个 token 块必须包含的字段片段（生产侧少写一个字段 -> 这里直接报错）。
_TOKEN_FIELD_MARKERS = (
    "s.department_name ILIKE ",
    "s.subject_org_name ILIKE ",
    "fiscal_document_versions tv",
    "tv.original_filename ILIKE ",
)
_PAGE_MARKER = "material_search:page"
_MAIN_MARKER = "material_search:main"
_COUNT_MARKER = "material_search:count"


# ---- 行构造器 --------------------------------------------------------------


def make_slot_row(**overrides: Any) -> Dict[str, Any]:
    """一条槽位种子（字段与 ``material_slots`` 列一一对应）。"""
    row: Dict[str, Any] = {
        "slot_id": "slot-1",
        "slot_key": "slot-key-1",
        "jurisdiction_org_id": None,
        "jurisdiction_name": None,
        "department_org_id": None,
        "department_name": None,
        "subject_org_id": "org-1",
        "subject_org_name": "主体一",
        "subject_kind": "unit",
        "material_scope": "unit_self",
        "fiscal_year": 2024,
        "report_kind": "final",
        "caliber": "self",
        "status": "uploaded",
        "status_reason": "awaiting_analysis",
        "applicability_status": "applicable",
        "current_document_version_id": None,
        "updated_at": datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def make_version_record(
    *,
    document_version_id: int,
    slot_id: str,
    original_filename: str,
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    return {
        "document_version_id": document_version_id,
        "slot_id": slot_id,
        "original_filename": original_filename,
        "created_at": created_at or datetime(2026, 9, 1, tzinfo=timezone.utc),
    }


def make_job_record(
    *,
    job_uuid: str,
    document_version_id: Optional[int],
    status: str = "done",
    organization_id: Optional[str] = None,
    job_id: int = 1,
    completed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """一条分析任务种子。

    ``document_version_id=None`` 就是 legacy unlinked 形态（metadata 里没有
    精确关联字段）：它必须能被构造出来，因为"猜不猜 Slot"正是要测的反例。
    """
    metadata: Dict[str, Any] = {}
    if organization_id:
        metadata["organization_id"] = organization_id
    if document_version_id is not None:
        metadata["structured_ingest"] = {"document_version_id": document_version_id}
    return {
        "id": job_id,
        "job_uuid": job_uuid,
        "status": status,
        "metadata": json.dumps(metadata, ensure_ascii=False),
        "document_version_id": document_version_id,
        "completed_at": completed_at or datetime(2026, 9, 20, tzinfo=timezone.utc),
    }


def _unescape_like(pattern: str) -> str:
    """``escape_like`` 的逆运算。

    假连接必须从参数里还原出"用户实际输入的 token"才能做匹配断言。
    还原后立刻用 ``escape_like`` 回代校验 —— 逆运算一旦写错，
    这里会直接抛错而不是悄悄匹配到别的东西。
    """
    body = str(pattern)
    if body.startswith("%") and body.endswith("%") and len(body) >= 2:
        body = body[1:-1]
    out: List[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            out.append(body[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    token = "".join(out)
    if escape_like(token) != pattern:
        raise UnknownSearchSqlError(f"无法还原 token 模式: {pattern!r}")
    return token


class FakeSearchConnection:
    """只认搜索三条语句的假连接（不认识的 SQL 直接报错）。

    种子数据分两层，与真库的两条语句一一对应：

    - ``slots``：主查询的行；
    - ``versions`` / ``jobs``：当前页明细 join 出来的行（版本 × 运行）。

    ``statements`` 记录每次执行的语句，供"没有 N+1"的断言使用。
    """

    def __init__(
        self,
        *,
        slots: Iterable[Dict[str, Any]] = (),
        versions: Iterable[Dict[str, Any]] = (),
        jobs: Iterable[Dict[str, Any]] = (),
    ) -> None:
        self.slots: List[Dict[str, Any]] = [dict(row) for row in slots]
        self.versions: List[Dict[str, Any]] = [dict(row) for row in versions]
        self.jobs: List[Dict[str, Any]] = [dict(row) for row in jobs]
        self.statements: List[Tuple[str, List[Any]]] = []

    # ---- 假连接的"执行" ---------------------------------------------------

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        self.statements.append((sql, list(args)))
        if _MAIN_MARKER in sql:
            return self._search(sql, list(args))
        if _PAGE_MARKER in sql:
            return self._page_details(list(args))
        if _COUNT_MARKER in sql:
            count, _ = self._match(sql, list(args), apply_page=False)
            return [{"total_count": count}]
        raise UnknownSearchSqlError(f"假连接不认识的搜索语句: {sql[:120]}")

    # ---- 主查询 ------------------------------------------------------------

    def _search(self, sql: str, params: List[Any]) -> List[Dict[str, Any]]:
        count, matched = self._match(sql, params, apply_page=True)
        rows: List[Dict[str, Any]] = []
        for slot in matched:
            row = dict(slot)
            row["total_count"] = count
            rows.append(row)
        return rows

    def _match(
        self, sql: str, params: List[Any], *, apply_page: bool
    ) -> Tuple[int, List[Dict[str, Any]]]:
        def param(index: str) -> Any:
            return params[int(index) - 1]

        permission = _PERMISSION_RE.search(sql)
        if permission is not None:
            # 先摘掉权限子句，避免它的 ANY(...) 被当成关系条件。
            sql = sql.replace(permission.group(0), "")
        # 关系条件与管理员的权限子句互不影响：**无论有无权限子句都要找关系条件**，
        # 否则"管理员搜本部"会静默退化成"不过滤关系"。
        relation = _RELATION_RE.search(sql)

        fiscal_year = _FISCAL_YEAR_RE.search(sql)
        report_kind = _REPORT_KIND_RE.search(sql)
        tokens = self._tokens(sql, params)

        matched: List[Dict[str, Any]] = []
        for slot in self.slots:
            if permission is not None:
                visible = set(param(permission.group("index")))
                columns = (
                    slot.get("jurisdiction_org_id"),
                    slot.get("department_org_id"),
                    slot.get("subject_org_id"),
                )
                if not any(value in visible for value in columns if value):
                    continue
            if relation is not None and slot.get("subject_org_id") not in set(
                param(relation.group("index"))
            ):
                continue
            if fiscal_year is not None and slot.get("fiscal_year") != param(fiscal_year.group("index")):
                continue
            if report_kind is not None and slot.get("report_kind") != param(report_kind.group("index")):
                continue
            if tokens and not all(self._token_matches(slot, token) for token in tokens):
                continue
            matched.append(slot)

        if not apply_page:
            return len(matched), matched

        limit = int(params[-2])
        offset = int(params[-1])
        return len(matched), matched[offset : offset + limit]

    def _tokens(self, sql: str, params: List[Any]) -> List[str]:
        """从主查询里还原出用户 token（顺序即 SQL 里的顺序）。

        解析方式刻意**不**逐字匹配整段 SQL：只按 token 块的起点切片，
        再对每个切片做两条检查 ——
        模式参数位（``$n ESCAPE '\\'``）必须在最前面，
        且四个可搜索字段片段必须都在块里。生产侧少写一个字段、
        或把参数顺序改了，这里会直接报错而不是悄悄当过。
        """
        where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
        chunks = where.split(_TOKEN_START)
        if len(chunks) == 1:
            if "ILIKE" in where:
                raise UnknownSearchSqlError("WHERE 里有 ILIKE 但没有 token 匹配块")
            return []

        tokens: List[str] = []
        for chunk in chunks[1:]:
            head = _PATTERN_PARAM_RE.match(chunk)
            if head is None:
                raise UnknownSearchSqlError(f"token 块开头不是模式参数: {chunk[:60]!r}")
            for marker in _TOKEN_FIELD_MARKERS:
                if marker not in chunk:
                    raise UnknownSearchSqlError(f"token 块缺少可搜索字段 {marker!r}")
            pattern = params[int(head.group("index")) - 1]
            tokens.append(_unescape_like(pattern))
        return tokens

    def _token_matches(self, slot: Dict[str, Any], token: str) -> bool:
        """一个 token 是否命中某个允许搜索的字段（字段 OR，语义同 SQL）。"""
        for field in ("jurisdiction_name", "department_name", "subject_org_name"):
            if ilike_literal(slot.get(field), token):
                return True
        for version in self._versions_of(str(slot.get("slot_id") or "")):
            if ilike_literal(version.get("original_filename"), token):
                return True
        for job in self._jobs_of(str(slot.get("slot_id") or "")):
            if str(job.get("job_uuid") or "") == token:
                return True
        return False

    # ---- 当前页明细 --------------------------------------------------------

    def _page_details(self, params: List[Any]) -> List[Dict[str, Any]]:
        wanted = {str(value) for value in (params[0] if params else [])}
        rows: List[Dict[str, Any]] = []
        for slot in self.slots:
            slot_id = str(slot.get("slot_id") or "")
            if slot_id not in wanted:
                continue
            versions = self._versions_of(slot_id)
            if not versions:
                rows.append(self._page_row(slot, None, None))
                continue
            for version in versions:
                linked = [
                    job
                    for job in self._jobs_of(slot_id)
                    if job.get("document_version_id") == version.get("document_version_id")
                ]
                if not linked:
                    rows.append(self._page_row(slot, version, None))
                    continue
                for job in linked:
                    rows.append(self._page_row(slot, version, job))
        return rows

    @staticmethod
    def _page_row(
        slot: Dict[str, Any], version: Optional[Dict[str, Any]], job: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "slot_id": slot.get("slot_id"),
            "current_document_version_id": slot.get("current_document_version_id"),
            "document_version_id": (version or {}).get("document_version_id"),
            "original_filename": (version or {}).get("original_filename"),
            "version_created_at": (version or {}).get("created_at"),
            "id": (job or {}).get("id"),
            "job_uuid": (job or {}).get("job_uuid"),
            "status": (job or {}).get("status"),
            "metadata": (job or {}).get("metadata"),
            "created_at": (job or {}).get("created_at"),
            "updated_at": (job or {}).get("completed_at"),
        }
        return row

    def _versions_of(self, slot_id: str) -> List[Dict[str, Any]]:
        return [row for row in self.versions if str(row.get("slot_id") or "") == slot_id]

    def _jobs_of(self, slot_id: str) -> List[Dict[str, Any]]:
        version_ids = {row.get("document_version_id") for row in self._versions_of(slot_id)}
        return [row for row in self.jobs if row.get("document_version_id") in version_ids]
