"""材料台账只读接口测试用的假数据库连接。

为什么需要它
------------
本仓库的测试默认不连数据库（``tests/conftest.py`` 无条件摘掉 ``DATABASE_URL``），
而 WP2-A 的三个接口是"SQL 聚合 + Python 拼装"的组合，两者都必须被测到：

- 聚合与拼装是纯逻辑，可以用行数据直接喂；
- 但"筛选条件有没有真的进 WHERE""SQL 投影有没有少选一列""一个接口跑了几条 SQL"
  这些只有走完整条路径才看得出来。

因此这个假连接做三件事：

1. **真的按 WHERE 过滤。** 它解析服务层生成的 ``WHERE`` 片段并按列名匹配内存行，
   于是"少传了 fiscal_year"这种漏接线会表现为结果集不对，而不是照样通过。
2. **真的做 GROUP BY。** 按 SQL 里写的分组列聚合，行内统计（``slot_count`` /
   ``due_at_unknown_count`` / ``jurisdiction_unknown_count``）与真库口径一致。
3. **投影按 SQL 的 SELECT 列表来。** 服务层少选一列，这里就少给一列，
   聚合侧读不到自然会报错 —— 而不是安静地按默认值算出一个"看起来正常"的数字。

两条刻意的设计（与 WP1 的 ``support_material_slot_db`` 一致）
------------------------------------------------------------
- **不认识的 SQL 直接报错**，不返回空列表：生产 SQL 改了、假连接没跟上时，
  测试必须失败，而不是绿灯。
- **不模拟 URI 级细节**（表达式索引、row-level lock、CHECK 约束），
  因此它替代不了真库验证。真库上的验证由 PG 集成用例承担（需要
  ``GOVBUDGET_TEST_DATABASE_URL``）；未配置时该部分如实标注为未执行。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "UnknownLedgerSqlError",
    "FakeLedgerConnection",
    "make_slot",
    "make_matrix_row",
]


class UnknownLedgerSqlError(AssertionError):
    """假连接收到不认识的语句。必须是失败，不能返回空结果。"""


#: 服务层会生成的谓词形态。``$N`` 是 asyncpg 的位置参数。
_PREDICATE_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"^(?P<column>[a-z_]+) = \$(?P<index>\d+)$"), "eq"),
    (re.compile(r"^(?P<column>[a-z_]+) = ANY\(\$(?P<index>\d+)::text\[\]\)$"), "any"),
)


def make_slot(**overrides: Any) -> Dict[str, Any]:
    """构造一条内存槽位行（字段与真库列一一对应）。"""
    slot: Dict[str, Any] = {
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
    slot.update(overrides)
    return slot


def make_matrix_row(**overrides: Any) -> Dict[str, Any]:
    """构造"与部门矩阵 SQL 投影同形"的明细行。

    纯聚合测试直接用这个形状，而不是直接喂 ``make_slot`` 的结果：
    聚合函数的输入契约就是 SQL 的投影结果（``id::text AS slot_id`` 等），
    两种形状混着用会让"聚合读错列名"这类缺陷在测试里看不出来。
    """
    slot = make_slot(**overrides)
    row = dict(slot)
    row["slot_id"] = slot["id"]
    return row


def _normalize(sql: str) -> str:
    return " ".join(str(sql or "").split())


def _where_clause(sql: str) -> str:
    """取出主查询的 WHERE 片段。

    必须锚在 ``FROM material_slots WHERE`` 上：SELECT 列表里的
    ``COUNT(*) FILTER (WHERE due_at IS NULL)`` 也含 "WHERE"，
    按第一个 WHERE 去匹配会把聚合表达式当成过滤条件。
    """
    match = re.search(
        r"\bFROM material_slots\s+WHERE (.+?)(?:\s+GROUP BY\b|\s+ORDER BY\b|$)", sql
    )
    if match is None:
        raise UnknownLedgerSqlError(f"语句没有可识别的 WHERE 子句: {sql[:160]}")
    return match.group(1).strip()


def _predicates(sql: str, args: Iterable[Any]) -> List[Any]:
    """把 WHERE 片段编译成一组"行是否命中"的判定函数。"""
    clause = _where_clause(sql)
    params = list(args)
    if clause == "TRUE":
        return []
    predicates: List[Any] = []
    for raw in clause.split(" AND "):
        fragment = raw.strip()
        for pattern, kind in _PREDICATE_PATTERNS:
            match = pattern.match(fragment)
            if match is None:
                continue
            column = match.group("column")
            value = params[int(match.group("index")) - 1]
            if kind == "eq":
                predicates.append(lambda row, c=column, v=value: row.get(c) == v)
            else:
                allowed = list(value)
                predicates.append(lambda row, c=column, a=allowed: row.get(c) in a)
            break
        else:
            raise UnknownLedgerSqlError(f"假连接不认识的谓词: {fragment!r}")
    return predicates


def _matches(sql: str, args: Iterable[Any], row: Dict[str, Any]) -> bool:
    return all(predicate(row) for predicate in _predicates(sql, args))


_GROUP_BY_RE = re.compile(r"\bGROUP BY (?P<columns>[a-z_,\s]+?)$")


def _group_columns(sql: str) -> List[str]:
    match = _GROUP_BY_RE.search(sql)
    if match is None:
        return []
    return [column.strip() for column in match.group("columns").split(",") if column.strip()]


def _group_rows(
    rows: List[Dict[str, Any]],
    columns: List[str],
    *,
    include_jurisdiction_unknown: bool,
    include_max_names: Iterable[str],
) -> List[Dict[str, Any]]:
    """按分组列聚合，输出列名与真库 SELECT 别名一致。"""
    max_columns = [name for name in include_max_names]
    buckets: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(column) for column in columns)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {column: row.get(column) for column in columns}
            bucket["slot_count"] = 0
            bucket["due_at_unknown_count"] = 0
            for name in max_columns:
                bucket[name] = None
            if include_jurisdiction_unknown:
                bucket["jurisdiction_unknown_count"] = 0
            buckets[key] = bucket
        bucket["slot_count"] += 1
        if row.get("due_at") is None:
            bucket["due_at_unknown_count"] += 1
        if include_jurisdiction_unknown and row.get("jurisdiction_org_id") is None:
            bucket["jurisdiction_unknown_count"] += 1
        for name in max_columns:
            _apply_max(bucket, row, name)
    return list(buckets.values())


def _apply_max(bucket: Dict[str, Any], row: Dict[str, Any], column: str) -> None:
    """模拟 ``MAX(column)``：NULL 不参与聚合。"""
    value = row.get(column)
    if value is None:
        return
    current = bucket.get(column)
    if current is None or _is_greater(value, current):
        bucket[column] = value


def _is_greater(value: Any, current: Any) -> bool:
    """比较两个值的大小。

    时间列必须按时间比，不能按字符串比：``str()`` 比较在带时区的时间上会得到
    与真库不同的结果（例如 +08:00 与 +00:00 的写法长度不同）。真库是 ``MAX``，
    这里必须一致，否则假连接会掩盖"取错最新时间"这类缺陷。
    """
    try:
        return bool(value > current)
    except TypeError:
        return str(value) > str(current)


_SELECT_RE = re.compile(r"\bSELECT (?P<columns>.+?) FROM ", re.DOTALL)
_COLUMN_RE = re.compile(r"(?P<source>[a-z_]+)(?:::[a-z_]+)?(?: AS (?P<alias>[a-z_]+))?", re.IGNORECASE)


def _project(row: Dict[str, Any], sql: str) -> Dict[str, Any]:
    """按 SQL 的 SELECT 列表投影一条明细行。

    服务层少选一列，这里就少给一列；聚合侧需要它时会拿到 ``None`` 或 KeyError，
    而不是被假连接悄悄补上一个默认值。
    """
    match = _SELECT_RE.search(sql)
    if match is None:
        raise UnknownLedgerSqlError(f"语句没有 SELECT 列表: {sql[:160]}")
    projected: Dict[str, Any] = {}
    for raw in match.group("columns").split(","):
        column_match = _COLUMN_RE.match(raw.strip())
        if column_match is None:
            raise UnknownLedgerSqlError(f"假连接不认识的投影列: {raw.strip()!r}")
        source = column_match.group("source")
        alias = column_match.group("alias") or source
        if source not in row:
            raise UnknownLedgerSqlError(
                f"投影列 {source!r} 在内存槽位行里不存在（假连接与真库列名已不同步）"
            )
        projected[alias] = row[source]
    return projected


class FakeLedgerConnection:
    """记录式假连接：只需要 ``fetch(sql, *args)``。"""

    def __init__(self, slots: Optional[Iterable[Dict[str, Any]]] = None) -> None:
        self.slots: List[Dict[str, Any]] = [dict(slot) for slot in (slots or [])]
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
        if "FROM material_slots" not in normalized:
            raise UnknownLedgerSqlError(f"假连接不认识这条 fetch 语句: {normalized[:200]}")
        self.calls.append((normalized, args))

        hit = [slot for slot in self.slots if _matches(normalized, args, slot)]
        columns = _group_columns(normalized)
        if columns:
            return _group_rows(
                hit,
                columns,
                include_jurisdiction_unknown="jurisdiction_unknown_count" in normalized,
                include_max_names=[
                    name
                    for name in ("jurisdiction_name", "department_name", "updated_at")
                    if f"MAX({name})" in normalized
                ],
            )
        return [_project(slot, normalized) for slot in hit]
