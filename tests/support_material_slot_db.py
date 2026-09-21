"""材料槽位服务层测试用的假数据库连接。

为什么需要它
------------
本仓库的测试默认不连真实数据库（``tests/conftest.py`` 无条件摘掉
``DATABASE_URL``，连库必须显式 opt-in ``GOVBUDGET_TEST_DATABASE_URL``）。
CI 与开发机通常没有可用的 PostgreSQL，但仍必须能验证服务层的写入契约。

这个假连接不解析 SQL、不模拟 Postgres，只做一件有价值的事：
**按语句形态分派，记录调用，并维护最小内存状态**。它能抓住手写 SQL 里
最容易犯且最难被纯函数测试发现的错误——参数顺序错位、幂等键用错、
绑定方向写反。

它替代不了真库验证：唯一约束、表达式索引冲突目标、CHECK 约束是否真的
生效，只能由 ``tests/test_material_slot_migration_pg.py`` 在配置了
``GOVBUDGET_TEST_DATABASE_URL`` 时验证。这一点在交付说明里必须如实标注。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class FakeSlotConnection:
    """记录式假连接：够用来验证服务层的写入契约。"""

    def __init__(self) -> None:
        #: 全部被执行的 (sql, args)
        self.calls: List[Tuple[str, tuple]] = []
        #: slot_key -> 槽位行
        self.slots_by_key: Dict[str, Dict[str, Any]] = {}
        #: slot id -> slot_key
        self.slot_key_by_id: Dict[str, str] = {}
        #: fiscal_document_versions.id -> 行
        self.versions: Dict[Any, Dict[str, Any]] = {}
        self._next_slot = 1

    # ---- 断言辅助 ----------------------------------------------------------

    def executed(self, fragment: str) -> List[Tuple[str, tuple]]:
        """返回所有 SQL 里包含该片段的调用。"""
        return [(sql, args) for sql, args in self.calls if fragment in sql]

    def executed_once(self, fragment: str) -> bool:
        return len(self.executed(fragment)) == 1

    # ---- asyncpg 兼容接口 --------------------------------------------------

    async def fetchrow(self, sql: str, *args: Any) -> Optional[Dict[str, Any]]:
        self.calls.append((sql, args))
        normalized = _normalize(sql)

        if "INSERT INTO material_slots" in normalized:
            return self._upsert_slot(args)
        if "INSERT INTO material_sources" in normalized:
            return {"id": f"source-{len(self.calls)}", "slot_id": args[0], "source_url": args[2]}
        if "SELECT applicability_status" in normalized:
            slot = self._slot_by_id(args[0])
            return slot
        if "SELECT s.*" in normalized:
            slot = self._slot_by_id(args[0]) if "WHERE s.id" in normalized else None
            if slot is None and "WHERE s.slot_key" in normalized:
                slot = self.slots_by_key.get(str(args[0]))
            return dict(slot) if slot else None
        if "UPDATE material_slots" in normalized and "RETURNING id" in normalized:
            return self._mark_not_applicable(args)
        return None

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        self.calls.append((sql, args))
        normalized = _normalize(sql)
        if "FROM fiscal_document_versions" in normalized and "WHERE slot_id" in normalized:
            return [v for v in self.versions.values() if v.get("slot_id") == args[0]]
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        normalized = _normalize(sql)
        if "UPDATE fiscal_document_versions" in normalized and "SET slot_id" in normalized:
            version_id, slot_id = args[0], args[1]
            version = self.versions.setdefault(version_id, {"id": version_id})
            version["slot_id"] = slot_id
            return "UPDATE 1"
        if "UPDATE material_slots AS s" in normalized:
            self._advance_current_version(args[0], args[1])
            return "UPDATE 1"
        if "UPDATE material_slots" in normalized and "SET status" in normalized:
            slot = self._slot_by_id(args[0])
            if slot is not None:
                slot["status"] = args[1]
                slot["status_reason"] = args[2]
            return "UPDATE 1"
        return "OK"

    # ---- 内部状态 ----------------------------------------------------------

    def seed_version(self, version_id: Any, *, created_at: int = 0, **extra: Any) -> None:
        payload = {"id": version_id, "created_at": created_at}
        payload.update(extra)
        self.versions[version_id] = payload

    def _upsert_slot(self, args: tuple) -> Dict[str, Any]:
        """模拟 INSERT ... ON CONFLICT (slot_key) DO UPDATE 的净效果。

        参数顺序必须与 ``material_slot_service.upsert_from_decision`` 的
        VALUES 一一对应；对不上就说明 SQL 与 Python 已经漂移，
        这条断言本身就有价值。
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
            status,
            status_reason,
            mapping_key,
        ) = args

        existing = self.slots_by_key.get(slot_key)
        if existing is not None:
            # DO UPDATE 分支：只刷新允许刷新的列，identity 列保持不变。
            existing["jurisdiction_org_id"] = jurisdiction_org_id or existing.get(
                "jurisdiction_org_id"
            )
            existing["subject_org_name"] = subject_org_name or existing.get("subject_org_name")
            existing["caliber"] = existing.get("caliber") if caliber == "unknown" else caliber
            existing["due_at"] = existing.get("due_at") or due_at
            if existing.get("current_document_version_id") is None:
                existing["status"] = status
                existing["status_reason"] = status_reason
            existing["upsert_count"] = int(existing.get("upsert_count") or 1) + 1
            return dict(existing)

        slot_id = f"slot-{self._next_slot}"
        self._next_slot += 1
        row = {
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
            "fiscal_year": fiscal_year,
            "report_kind": report_kind,
            "applicability_status": "applicable",
            "due_at": due_at,
            "expected_source_url": expected_source_url,
            "status": status,
            "status_reason": status_reason,
            "mapping_key": mapping_key,
            "current_document_version_id": None,
            "upsert_count": 1,
        }
        self.slots_by_key[slot_key] = row
        self.slot_key_by_id[slot_id] = slot_key
        return dict(row)

    def _slot_by_id(self, slot_id: Any) -> Optional[Dict[str, Any]]:
        key = self.slot_key_by_id.get(slot_id)
        return self.slots_by_key.get(key) if key else None

    def _advance_current_version(self, slot_id: Any, version_id: Any) -> None:
        slot = self._slot_by_id(slot_id)
        if slot is None:
            return
        current = slot.get("current_document_version_id")
        if current is None:
            slot["current_document_version_id"] = version_id
            return
        current_version = self.versions.get(current) or {}
        new_version = self.versions.get(version_id) or {}
        # 与 SQL 里的保护一致：只有不早于当前版本才前进指针
        if int(current_version.get("created_at") or 0) <= int(new_version.get("created_at") or 0):
            slot["current_document_version_id"] = version_id

    def _mark_not_applicable(self, args: tuple) -> Optional[Dict[str, Any]]:
        slot_key, note = args[0], args[1]
        slot = self.slots_by_key.get(slot_key)
        if slot is None:
            return None
        slot["applicability_status"] = "not_applicable"
        slot["applicability_note"] = note
        slot["status"] = "not_applicable"
        slot["status_reason"] = "applicability_marked_not_applicable"
        return {
            "id": slot["id"],
            "slot_key": slot_key,
            "status": slot["status"],
            "status_reason": slot["status_reason"],
        }


class FailingSlotConnection(FakeSlotConnection):
    """任何写入都抛异常，用于验证"槽位故障不阻断主流程"。"""

    async def fetchrow(self, sql: str, *args: Any) -> Optional[Dict[str, Any]]:
        raise RuntimeError("simulated database outage")

    async def execute(self, sql: str, *args: Any) -> str:
        raise RuntimeError("simulated database outage")

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        raise RuntimeError("simulated database outage")


def _normalize(sql: str) -> str:
    return " ".join(str(sql or "").split())
