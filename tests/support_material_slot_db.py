"""材料槽位服务层测试用的假数据库连接。

为什么需要它
------------
本仓库的测试默认不连真实数据库（``tests/conftest.py`` 无条件摘掉
``DATABASE_URL``，连库必须显式 opt-in ``GOVBUDGET_TEST_DATABASE_URL``）。
CI 与开发机通常没有可用的 PostgreSQL，但仍必须能验证服务层的写入契约。

这个假连接不解析 SQL、不模拟 Postgres，只做三件有价值的事：

1. **按语句形态分派**，并维护最小内存状态——用于抓"参数顺序错位、
   冲突目标写错"这类纯函数测试发现不了的手写 SQL 缺陷；
2. **支持事务语义**：``transaction()`` 进入时快照、异常时整体回滚，
   于是"部分写入"可以在不连库的情况下被断言出来；
3. **故障注入**：``fail_on`` 让指定语句抛异常，用来验证回滚真的发生。

两条刻意的设计
--------------
- **不认识的 SQL 直接报错**，不返回 ``None``。静默返回 None 会让"生产 SQL
  改了、假连接没跟上"表现为测试通过而线上出错，这比假连接本身更危险。
- **不认 URI 级别的细节**，因此它替代不了真库验证：唯一约束、表达式索引作
  冲突目标、``FOR UPDATE`` 的真实并发行为、CHECK 约束，只能由
  ``tests/test_material_slot_migration_pg.py`` 在配置了
  ``GOVBUDGET_TEST_DATABASE_URL`` 时验证。这一点在交付说明里如实标注。
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple


class UnknownSqlError(AssertionError):
    """假连接收到不认识的语句。

    这**必须**是失败而不是返回 None：否则生产代码换了一条 SQL 之后，
    假连接会安静地给出"查不到"的答案，测试照样绿。
    """


class _FakeTransaction:
    def __init__(self, conn: "FakeSlotConnection") -> None:
        self._conn = conn
        self._snapshot: Optional[dict] = None

    async def __aenter__(self) -> "FakeSlotConnection":
        self._snapshot = self._conn._snapshot()
        self._conn._tx_depth += 1
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._conn._tx_depth -= 1
        if exc_type is not None and self._snapshot is not None:
            self._conn._restore(self._snapshot)
        return False


class FakeSlotConnection:
    """记录式假连接：够用来验证服务层的写入契约与事务性。"""

    def __init__(self) -> None:
        #: 全部被执行的 (sql, args)
        self.calls: List[Tuple[str, tuple]] = []
        #: slot_key -> 槽位行
        self.slots_by_key: Dict[str, Dict[str, Any]] = {}
        #: slot id -> slot_key
        self.slot_key_by_id: Dict[str, str] = {}
        #: fiscal_document_versions.id -> 行
        self.versions: Dict[Any, Dict[str, Any]] = {}
        #: 被取过的身份 advisory 锁键，用于断言"首次创建前先按身份串行化"
        self.advisory_locks: List[str] = []
        #: 指定 SQL 片段命中时抛异常，用于故障注入
        self.fail_on: List[str] = []
        self.fail_exception: Exception = RuntimeError("injected failure")
        self._next_slot = 1
        self._tx_depth = 0

    # ---- 断言辅助 ----------------------------------------------------------

    def executed(self, fragment: str) -> List[Tuple[str, tuple]]:
        """返回所有 SQL 里包含该片段的调用。"""
        return [(sql, args) for sql, args in self.calls if fragment in sql]

    def executed_once(self, fragment: str) -> bool:
        return len(self.executed(fragment)) == 1

    def slot_of_version(self, version_id: Any) -> Any:
        return (self.versions.get(version_id) or {}).get("slot_id")

    # ---- 事务 --------------------------------------------------------------

    def is_in_transaction(self) -> bool:
        return self._tx_depth > 0

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def _snapshot(self) -> dict:
        return {
            "slots_by_key": copy.deepcopy(self.slots_by_key),
            "slot_key_by_id": dict(self.slot_key_by_id),
            "versions": copy.deepcopy(self.versions),
            "next_slot": self._next_slot,
        }

    def _restore(self, snapshot: dict) -> None:
        self.slots_by_key = snapshot["slots_by_key"]
        self.slot_key_by_id = snapshot["slot_key_by_id"]
        self.versions = snapshot["versions"]
        self._next_slot = snapshot["next_slot"]

    # ---- 故障注入 ----------------------------------------------------------

    def _maybe_fail(self, sql: str) -> None:
        for fragment in self.fail_on:
            if fragment in sql:
                raise self.fail_exception

    # ---- asyncpg 兼容接口 --------------------------------------------------

    async def fetchrow(self, sql: str, *args: Any) -> Optional[Dict[str, Any]]:
        normalized = _normalize(sql)
        self._maybe_fail(normalized)
        self.calls.append((sql, args))

        if "INSERT INTO material_slots" in normalized:
            return self._upsert_slot(args)
        if "INSERT INTO material_sources" in normalized:
            return {"id": f"source-{len(self.calls)}", "slot_id": args[0], "source_url": args[2]}
        # 分派顺序有讲究：越具体的语句必须越先匹配。
        # 例如"取整行返回"与"取若干列供刷新"都以 `FROM material_slots` 开头，
        # 顺序写反会让前者拿到后者的列集合，测试就会因为缺字段而报 KeyError——
        # 那是测试辅助的错误，不是生产代码的错误，会浪费排查时间。
        if normalized.startswith("SELECT id FROM material_slots WHERE id = $1 FOR UPDATE"):
            return self._select_slot_id_for_lock(args)
        if normalized.startswith("SELECT id, caliber, caliber_conflict_candidate"):
            return self._select_for_caliber(args)
        if normalized.startswith("SELECT id, slot_id FROM fiscal_document_versions"):
            return self._lock_version(args)
        if normalized.startswith("SELECT subject_org_id, subject_kind, material_scope"):
            return self._select_for_refresh(args)
        if normalized.startswith("SELECT id, slot_key, status, status_reason"):
            return self._select_slot(by_id=args[0])
        if normalized.startswith("UPDATE material_slots SET jurisdiction_org_id"):
            return self._update_slot_resolved(args)
        if normalized.startswith("INSERT INTO material_sources"):
            return {"id": f"source-{len(self.calls)}", "slot_id": args[0], "source_url": args[2]}
        if normalized.startswith("UPDATE material_slots SET applicability_status"):
            return self._mark_not_applicable(args)
        if normalized.startswith("SELECT s.*"):
            return self._select_slot(
                by_id=args[0] if "WHERE s.id" in normalized else None,
                by_key=str(args[0]) if "WHERE s.slot_key" in normalized else None,
            )
        raise UnknownSqlError(f"假连接不认识这条 fetchrow 语句: {normalized[:160]}")

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        normalized = _normalize(sql)
        self._maybe_fail(normalized)
        self.calls.append((sql, args))
        if "FROM fiscal_document_versions" in normalized and "WHERE slot_id" in normalized:
            return [v for v in self.versions.values() if v.get("slot_id") == args[0]]
        raise UnknownSqlError(f"假连接不认识这条 fetch 语句: {normalized[:160]}")

    async def execute(self, sql: str, *args: Any) -> str:
        normalized = _normalize(sql)
        self._maybe_fail(normalized)
        self.calls.append((sql, args))

        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            # 事务级 advisory 锁在假连接里只是一个记录点：它的作用是让真库上
            # 同一身份的首次创建串行化，单连接测试无并发语义可言。
            self.advisory_locks.append(str(args[0]) if args else "")
            return "SELECT 1"
        if normalized.startswith("INSERT INTO material_slots"):
            self._insert_slot_if_absent(args)
            return "INSERT 0 1"
        if "UPDATE fiscal_document_versions" in normalized and "SET slot_id" in normalized:
            version_id, slot_id = args[0], args[1]
            version = self.versions.setdefault(version_id, {"id": version_id})
            if version.get("slot_id") is None:
                version["slot_id"] = slot_id
            return "UPDATE 1"
        if "UPDATE material_slots AS s" in normalized:
            self._advance_current_version(args[0], args[1])
            return "UPDATE 1"
        if "UPDATE material_slots" in normalized and "SET status" in normalized:
            self._set_status(args[0], args[1], args[2])
            return "UPDATE 1"
        if "UPDATE material_slots" in normalized and "SET applicability_status" in normalized:
            self._set_not_applicable(args[0], args[1])
            return "UPDATE 1"
        raise UnknownSqlError(f"假连接不认识这条 execute 语句: {normalized[:160]}")

    # ---- 内部状态 ----------------------------------------------------------

    def seed_version(self, version_id: Any, *, created_at: int = 0, **extra: Any) -> None:
        payload = {"id": version_id, "created_at": created_at, "slot_id": None}
        payload.update(extra)
        self.versions[version_id] = payload

    def _select_slot_id_for_lock(self, args: tuple) -> Optional[Dict[str, Any]]:
        """``SELECT id FROM material_slots WHERE id = $1 FOR UPDATE``。

        锁不到行时返回 None——真实 Postgres 对不存在的行也是这个行为
        （不报错、不取锁），调用方随后会由外键约束兜住。
        """
        slot = self._slot_by_id(args[0])
        return {"id": slot["id"]} if slot is not None else None

    def _select_for_caliber(self, args: tuple) -> Optional[Dict[str, Any]]:
        key = str(args[0])
        slot = self.slots_by_key.get(key)
        if slot is None:
            return None
        return {
            "id": slot["id"],
            "caliber": slot.get("caliber", "unknown"),
            "caliber_conflict_candidate": slot.get("caliber_conflict_candidate"),
            "current_document_version_id": slot.get("current_document_version_id"),
            "applicability_status": slot.get("applicability_status", "applicable"),
            "due_at": slot.get("due_at"),
        }

    def _select_for_refresh(self, args: tuple) -> Optional[Dict[str, Any]]:
        slot = self._slot_by_id(args[0])
        if slot is None:
            return None
        return {
            "subject_org_id": slot["subject_org_id"],
            "subject_kind": slot.get("subject_kind"),
            "material_scope": slot.get("material_scope"),
            "report_kind": slot.get("report_kind"),
            "fiscal_year": slot.get("fiscal_year"),
            "mapping_key": slot.get("mapping_key", ""),
            "applicability_status": slot.get("applicability_status", "applicable"),
            "due_at": slot.get("due_at"),
            "current_document_version_id": slot.get("current_document_version_id"),
            "caliber_conflict_candidate": slot.get("caliber_conflict_candidate"),
            "status": slot.get("status"),
        }

    def _lock_version(self, args: tuple) -> Optional[Dict[str, Any]]:
        version = self.versions.get(args[0])
        if version is None:
            return None
        return {"id": version["id"], "slot_id": version.get("slot_id")}

    def _select_slot(self, *, by_id: Any = None, by_key: Optional[str] = None):
        slot = self._slot_by_id(by_id) if by_id is not None else None
        if slot is None and by_key is not None:
            slot = self.slots_by_key.get(by_key)
        return dict(slot) if slot else None

    def _insert_slot_if_absent(self, args: tuple) -> bool:
        """模拟 ``INSERT ... ON CONFLICT (slot_key) DO NOTHING``。

        **DO NOTHING 的语义是关键**：已存在的行一个字段都不改。
        假连接必须如实模拟这一点，否则"首次创建的口径竞态"在测试里
        根本复现不出来——那正是本轮要修的缺陷。

        参数顺序与 ``material_slot_service._ensure_slot_row`` 的 VALUES
        一一对应；对不上就说明 SQL 与 Python 已经漂移。

        返回是否真正插入了新行。
        """
        (
            slot_key,
            jurisdiction_org_id,
            jurisdiction_name,
            department_org_id,
            department_name,
            subject_org_id,
            subject_org_name,
            subject_org_code,
            subject_kind,
            _subject_level,
            material_scope,
            caliber,
            fiscal_year,
            report_kind,
            due_at,
            expected_source_url,
            mapping_key,
        ) = args

        if slot_key in self.slots_by_key:
            return False

        slot_id = f"slot-{self._next_slot}"
        self._next_slot += 1
        self.slots_by_key[slot_key] = {
            "id": slot_id,
            "slot_key": slot_key,
            "jurisdiction_org_id": jurisdiction_org_id,
            "jurisdiction_name": jurisdiction_name,
            "department_org_id": department_org_id,
            "department_name": department_name,
            "subject_org_id": subject_org_id,
            "subject_org_name": subject_org_name,
            "subject_org_code": subject_org_code,
            "subject_kind": subject_kind,
            "material_scope": material_scope,
            "caliber": caliber,
            # 这两列在第一步不写，走建表默认值；第二步计算、第三步落库。
            "caliber_conflict_candidate": None,
            "fiscal_year": fiscal_year,
            "report_kind": report_kind,
            "applicability_status": "applicable",
            "applicability_note": None,
            "due_at": due_at,
            "expected_source_url": expected_source_url,
            # 与建表默认值一致；紧随其后的第三步会写入真正推导出的状态。
            "status": "mapping_required",
            "status_reason": None,
            "mapping_key": mapping_key,
            "current_document_version_id": None,
            # 两个计数分别记录"真正插入过几次"和"锁内解析更新过几次"。
            # 只有 insert_count 能证明"没有多建材料"；更新次数会随调用次数增长，
            # 拿它当幂等证据会把断言绑死在实现细节上。
            "insert_count": 1,
            "upsert_count": 0,
        }
        self.slot_key_by_id[slot_id] = slot_key
        return True

    def _update_slot_resolved(self, args: tuple) -> Optional[Dict[str, Any]]:
        """第三步：把锁内算出的权威值写回。

        identity 列（slot_key / subject_org_id / subject_kind /
        material_scope / fiscal_year / report_kind / mapping_key）与适用性
        都不在 UPDATE 列表里，因此**不可能**被这一步改写——
        假连接同样不碰它们，两边行为保持一致。
        """
        (
            slot_id,
            jurisdiction_org_id,
            jurisdiction_name,
            department_org_id,
            department_name,
            subject_org_name,
            subject_org_code,
            caliber,
            caliber_conflict_candidate,
            expected_source_url,
            due_at,
            status,
            status_reason,
        ) = args

        slot = self._slot_by_id(slot_id)
        if slot is None:
            return None
        slot["jurisdiction_org_id"] = jurisdiction_org_id or slot.get("jurisdiction_org_id")
        slot["jurisdiction_name"] = jurisdiction_name or slot.get("jurisdiction_name")
        slot["department_org_id"] = department_org_id or slot.get("department_org_id")
        slot["department_name"] = department_name or slot.get("department_name")
        slot["subject_org_name"] = subject_org_name or slot.get("subject_org_name")
        slot["subject_org_code"] = subject_org_code or slot.get("subject_org_code")
        slot["caliber"] = caliber
        slot["caliber_conflict_candidate"] = caliber_conflict_candidate
        slot["expected_source_url"] = expected_source_url or slot.get("expected_source_url")
        slot["due_at"] = slot.get("due_at") or due_at
        slot["status"] = status
        slot["status_reason"] = status_reason
        slot["upsert_count"] = int(slot.get("upsert_count") or 0) + 1
        return {
            "id": slot["id"],
            "slot_key": slot["slot_key"],
            "status": slot["status"],
            "status_reason": slot["status_reason"],
        }

    def _slot_by_id(self, slot_id: Any) -> Optional[Dict[str, Any]]:
        key = self.slot_key_by_id.get(slot_id)
        return self.slots_by_key.get(key) if key else None

    def _advance_current_version(self, slot_id: Any, version_id: Any) -> None:
        """与生产 SQL 同一套排序规则：``(created_at, id)`` 字典序。"""
        slot = self._slot_by_id(slot_id)
        if slot is None:
            return
        current = slot.get("current_document_version_id")
        if current is None:
            slot["current_document_version_id"] = version_id
            return
        current_version = self.versions.get(current) or {}
        new_version = self.versions.get(version_id) or {}
        if _version_order(current_version) <= _version_order(new_version):
            slot["current_document_version_id"] = version_id

    def _set_status(self, slot_id: Any, status: Any, status_reason: Any) -> None:
        slot = self._slot_by_id(slot_id)
        if slot is not None:
            slot["status"] = status
            slot["status_reason"] = status_reason

    def _set_not_applicable(self, slot_key: Any, note: Any) -> None:
        slot = self.slots_by_key.get(str(slot_key))
        if slot is not None:
            slot["applicability_status"] = "not_applicable"
            slot["applicability_note"] = note

    def _mark_not_applicable(self, args: tuple) -> Optional[Dict[str, Any]]:
        """``UPDATE ... SET applicability_status ... RETURNING id``。"""
        slot = self.slots_by_key.get(str(args[0]))
        if slot is None:
            return None
        slot["applicability_status"] = "not_applicable"
        slot["applicability_note"] = args[1]
        return {"id": slot["id"]}


class FailingSlotConnection(FakeSlotConnection):
    """任何写入都抛异常，用于验证"槽位故障不阻断主流程"。"""

    async def fetchrow(self, sql: str, *args: Any) -> Optional[Dict[str, Any]]:
        raise RuntimeError("simulated database outage")

    async def execute(self, sql: str, *args: Any) -> str:
        raise RuntimeError("simulated database outage")

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        raise RuntimeError("simulated database outage")


def _version_order(version: Dict[str, Any]) -> tuple:
    """与生产 SQL 的 ``(cur.created_at, cur.id)`` 字典序保持一致。

    id 在真库里是 INTEGER，必须按数值比较：按字符串比的话 ``10 < 9``，
    同时间戳的版本会得到与线上相反的顺序，测试反而会掩盖真实缺陷。
    非整数 id（测试里可能用字符串）退化为按字符串比较，仍然确定。
    """
    raw_id = version.get("id")
    try:
        ident = (0, int(raw_id), "")
    except (TypeError, ValueError):
        ident = (1, 0, str(raw_id))
    return (int(version.get("created_at") or 0), ident)


def _normalize(sql: str) -> str:
    return " ".join(str(sql or "").split())
