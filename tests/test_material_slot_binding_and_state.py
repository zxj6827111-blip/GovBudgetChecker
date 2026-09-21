"""PR #42 评审修复轮：绑定安全性、事务原子性、状态不变式。

本文件针对独立评审提出的六类缺陷逐条立反例。命名保持"先写会怎么坏"：
每条断言的目标都是"这件事做不到"，而不只是"正常情况能跑通"。

真库覆盖在 ``tests/test_material_slot_migration_pg.py``：行锁与唯一约束的真实
并发语义、表达式索引作冲突目标，只有真库说了算。本文件验证的是服务层在
正确的事务边界内发起了正确的语句序列。
"""

from __future__ import annotations

import re

import pytest

from src.services.material_slot_service import (
    BINDING_REASON_CONFLICT,
    MaterialSlotService,
    SlotBindingConflict,
    SlotBindingTargetMissing,
    SlotIdentityRowMissing,
    allocate_for_document,
    safe_allocate_for_document,
)
from src.services.material_slot_status import derive_slot_status, infer_progress_state
from support_material_slot_db import FakeSlotConnection

DISTRICT_ID = "833e15c63a38"
DEPT_ID = "8f773936806d"
UNIT_ID = "53f98bebfc3a"

ORG_RECORDS = [
    {"id": DISTRICT_ID, "name": "普陀区", "level": "district", "parent_id": None},
    {
        "id": DEPT_ID,
        "name": "上海市普陀区规划和自然资源局",
        "level": "department",
        "parent_id": DISTRICT_ID,
    },
    {
        "id": UNIT_ID,
        "name": "上海市普陀区规划和自然资源局本级",
        "level": "unit",
        "parent_id": DEPT_ID,
    },
]

def _assigned_columns(sql: str) -> set:
    """取出 UPDATE ... SET 里被赋值的列名。

    不能用"按逗号切分再取等号左边"的写法：``COALESCE($2, jurisdiction_org_id)``
    里的逗号会把一条赋值切成两段，得到的列名集合是错的。
    改用"行首或逗号之后紧接的标识符 + 等号"来匹配，嵌套函数参数不会被误判。
    """
    set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    return {match.group(1) for match in re.finditer(r"(?:^|,)\s*(\w+)\s*=", set_clause)}



CHECKSUM = "a" * 64
VERSION_ID = 100


def _metadata(**overrides):
    base = {
        "organization_id": UNIT_ID,
        "organization_name": "上海市普陀区规划和自然资源局本级",
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
        "filename": "规划和自然资源局本级2024年度单位决算.pdf",
    }
    base.update(overrides)
    return base


def _seed_slot(conn: FakeSlotConnection, **overrides):
    """直接落一条槽位行，用于构造"由人工/历史数据产生的"状态。

    有些不变式（例如 mapping_key 为空但年份缺失）无法通过正常分配路径产生，
    但真实库里可能存在，因此必须能被测到。
    """
    slot_id = f"seed-{len(conn.slots_by_key) + 1}"
    row = {
        "id": slot_id,
        "slot_key": f"seed-key-{slot_id}",
        "subject_org_id": UNIT_ID,
        "subject_org_name": "某单位",
        "subject_org_code": None,
        "subject_kind": "unit",
        "material_scope": "unit_self",
        "report_kind": "final",
        "fiscal_year": 2024,
        "mapping_key": "",
        "caliber": "unknown",
        "caliber_conflict_candidate": None,
        "applicability_status": "applicable",
        "applicability_note": None,
        "due_at": None,
        "current_document_version_id": None,
        "status": "not_due",
        "status_reason": "due_at_unknown",
    }
    row.update(overrides)
    conn.slots_by_key[row["slot_key"]] = row
    conn.slot_key_by_id[slot_id] = row["slot_key"]
    return row


# ==== 1. 同版本同槽位：幂等重放 =============================================


@pytest.mark.asyncio
async def test_rebinding_same_version_to_same_slot_is_idempotent():
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)

    first = await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )
    second = await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )

    assert first["bound"] is True and second["bound"] is True
    assert first["slot_id"] == second["slot_id"]
    assert conn.slot_of_version(VERSION_ID) == first["slot_id"]
    assert len(conn.slots_by_key) == 1


@pytest.mark.asyncio
async def test_bind_is_idempotent_without_reissuing_the_update():
    """已归属同一槽位时不再发 UPDATE——重放不该产生无谓写入。"""
    service = MaterialSlotService(conn := FakeSlotConnection())
    conn.seed_version(VERSION_ID, created_at=10)
    conn.seed_slot = None
    slot = _seed_slot(conn, current_document_version_id=VERSION_ID)
    conn.versions[VERSION_ID]["slot_id"] = slot["id"]

    before = len(conn.executed("UPDATE fiscal_document_versions"))
    await service.bind_document_version(slot["id"], VERSION_ID)
    after = len(conn.executed("UPDATE fiscal_document_versions"))
    assert after == before


# ==== 2 & 3. 跨槽重绑：fail-closed，且库里不留变化 ==========================


@pytest.mark.asyncio
async def test_binding_a_version_to_a_second_slot_is_rejected():
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)

    first = await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )
    assert first["bound"] is True

    service = MaterialSlotService(conn)
    other_slot = _seed_slot(conn, slot_key="other-slot", fiscal_year=2025)

    with pytest.raises(SlotBindingConflict) as excinfo:
        await service.bind_document_version(other_slot["id"], VERSION_ID)

    assert excinfo.value.reason == BINDING_REASON_CONFLICT
    assert excinfo.value.detail["existing_slot_id"] == first["slot_id"]
    assert excinfo.value.detail["target_slot_id"] == other_slot["id"]


@pytest.mark.asyncio
async def test_conflicting_cross_slot_rebind_leaves_database_untouched():
    """冲突之后：版本仍属原槽位，原槽位指针不变，目标槽位不被推进。"""
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)

    first = await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )
    original_slot_id = first["slot_id"]

    # 同一份 PDF 版本被按"预算"重新归属——身份不同，必然指向另一个槽位
    result = await safe_allocate_for_document(
        conn,
        metadata=_metadata(report_kind="budget", doc_type="dept_budget"),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )

    assert result["status"] == "conflict"
    assert result["reason"] == BINDING_REASON_CONFLICT
    assert result["bound"] is False, "冲突绝不允许被写成已绑定"

    # 版本归属未变
    assert conn.slot_of_version(VERSION_ID) == original_slot_id
    # 原槽位指针未变
    original = conn._slot_by_id(original_slot_id)
    assert original["current_document_version_id"] == VERSION_ID
    # 第二个槽位没有留下任何痕迹（事务整体回滚）
    assert len(conn.slots_by_key) == 1
    assert "other-slot" not in conn.slots_by_key


@pytest.mark.asyncio
async def test_binding_nonexistent_version_reports_missing_target():
    conn = FakeSlotConnection()
    slot = _seed_slot(conn)
    service = MaterialSlotService(conn)

    with pytest.raises(SlotBindingTargetMissing):
        await service.bind_document_version(slot["id"], 999999)


# ==== 4. 事务原子性：中途失败必须整体回滚 ===================================


@pytest.mark.asyncio
async def test_failure_after_binding_rolls_back_everything():
    """版本绑定成功、当前版本指针推进失败 —— 不允许留下半个状态。

    这是最容易被忽略的一种部分写入：槽位建好了、版本也挂上了，
    只有"哪个是当前版本"没写。库看起来是正常的，但槽位的当前版本为空，
    台账会把它当成"还没有材料"。
    """
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    conn.fail_on = ["UPDATE material_slots AS s"]

    result = await safe_allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )

    assert result["status"] == "error"
    assert result["bound"] is False
    # 槽位整体回滚：没有留下任何槽位行
    assert conn.slots_by_key == {}
    assert conn.slot_key_by_id == {}
    # 版本绑定也一并回滚
    assert conn.slot_of_version(VERSION_ID) is None


@pytest.mark.asyncio
async def test_failure_during_status_refresh_rolls_back_binding():
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    conn.fail_on = ["UPDATE material_slots SET status"]

    result = await safe_allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )

    assert result["status"] == "error"
    assert conn.slots_by_key == {}
    assert conn.slot_of_version(VERSION_ID) is None


@pytest.mark.asyncio
async def test_allocation_uses_a_single_transaction():
    """一次分配只开一个事务，全程复用它。"""
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)

    opened = 0
    original = conn.transaction

    def counting_transaction():
        nonlocal opened
        opened += 1
        return original()

    conn.transaction = counting_transaction  # type: ignore[method-assign]

    await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )
    assert opened == 1


@pytest.mark.asyncio
async def test_bind_locks_the_version_row_before_deciding():
    """必须先 FOR UPDATE 再决定，否则并发请求会同时看到 NULL。"""
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    service = MaterialSlotService(conn)
    slot = _seed_slot(conn)

    await service.bind_document_version(slot["id"], VERSION_ID)

    locks = conn.executed("FROM fiscal_document_versions")
    assert locks, "绑定没有对版本行取锁"
    assert "FOR UPDATE" in locks[0][0]


# ==== 4b. refresh_status 必须自带事务与行锁 =================================


@pytest.mark.asyncio
async def test_refresh_status_opens_its_own_transaction():
    """独立调用 refresh_status 时也不能裸奔。

    它当前的两个调用方恰好已经开了事务，但那是调用约定而不是保证——
    WP2/WP3 会直接调它。用例把"自己开事务"钉成行为，而不是靠注释提醒。
    """
    conn = FakeSlotConnection()
    row = _seed_slot(conn)
    service = MaterialSlotService(conn)

    opened = 0
    original = conn.transaction

    def counting_transaction():
        nonlocal opened
        opened += 1
        return original()

    conn.transaction = counting_transaction  # type: ignore[method-assign]
    await service.refresh_status(row["id"])
    assert opened == 1, "refresh_status 没有自己开启事务"


@pytest.mark.asyncio
async def test_refresh_status_locks_the_slot_row():
    conn = FakeSlotConnection()
    row = _seed_slot(conn)
    service = MaterialSlotService(conn)

    await service.refresh_status(row["id"])

    reads = [
        sql
        for sql, _ in conn.calls
        if "FROM material_slots" in sql and "WHERE id = $1" in sql
    ]
    assert reads, "refresh_status 没有读取槽位行"
    assert "FOR UPDATE" in reads[0], "refresh_status 读事实时没有加行锁"


@pytest.mark.asyncio
async def test_refresh_status_reuses_an_existing_transaction():
    """外层已有事务时不再另开一层（避免无谓的 SAVEPOINT）。"""
    conn = FakeSlotConnection()
    row = _seed_slot(conn)
    service = MaterialSlotService(conn)

    opened = 0
    original = conn.transaction

    def counting_transaction():
        nonlocal opened
        opened += 1
        return original()

    conn.transaction = counting_transaction  # type: ignore[method-assign]
    async with conn.transaction():
        await service.refresh_status(row["id"])
    assert opened == 1, "外层已开事务时不应再开一层"


# ==== 4c. 首次建槽：确保存在 → 锁 → 锁内解析 → 写回 =========================


@pytest.mark.asyncio
async def test_slot_row_is_ensured_before_it_is_locked():
    """必须"先确保存在再锁"。

    ``SELECT ... FOR UPDATE`` 锁不住不存在的行。若顺序反过来，
    首次并发创建时两个事务都会读到空行、各自在锁外算好口径，
    随后一个插入、一个覆盖——冲突证据就此丢失。
    """
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    await service.upsert_from_decision(_decision(), caliber="summary")

    statements = [sql for sql, _ in conn.calls]
    ensure_index = next(
        index for index, sql in enumerate(statements) if "INSERT INTO material_slots" in sql
    )
    lock_index = next(
        index
        for index, sql in enumerate(statements)
        if "FROM material_slots" in sql and "FOR UPDATE" in sql
    )
    assert ensure_index < lock_index, "存在性插入必须发生在加锁之前"


@pytest.mark.asyncio
async def test_identity_advisory_lock_is_taken_before_any_row_lock():
    """首次创建必须先按身份串行化，再碰任何行。

    这是真库死锁的修复机制：``material_slots`` 上有两个唯一约束，
    并发插入同一身份时 PostgreSQL 会在两个唯一索引上各建一个"推测插入"标记，
    双方可能分别等对方的标记而形成环路（真库实测报 DeadlockDetectedError）。
    先取事务级 advisory 锁把同一身份的首次创建串行化，推测插入竞争就不存在了。

    顺序必须是"advisory → 槽位行 → 版本行"这条全序的第一段；
    若哪天它被挪到行锁之后，这条断言会失败。
    """
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )

    assert conn.advisory_locks, "首次创建没有取身份 advisory 锁"
    statements = [sql for sql, _ in conn.calls]
    advisory_index = next(
        index
        for index, sql in enumerate(statements)
        if sql.strip().startswith("SELECT pg_advisory_xact_lock")
    )
    first_row_lock = next(
        index
        for index, sql in enumerate(statements)
        if "FOR UPDATE" in sql
        or "INSERT INTO material_slots" in sql
        or "UPDATE material_slots" in sql
    )
    assert advisory_index < first_row_lock, "身份锁必须早于所有行锁"


@pytest.mark.asyncio
async def test_ensure_step_does_not_overwrite_an_existing_row():
    """第一步的冲突动作必须是 DO NOTHING，不能是 DO UPDATE。

    DO UPDATE 会把锁外算出的结论盖到已有口径上——这正是首次创建并发竞争
    的成因。这条断言守的是修复机制本身。
    """
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    await service.upsert_from_decision(_decision(), caliber="summary")
    await service.upsert_from_decision(_decision(), caliber="self")

    inserts = [sql for sql, _ in conn.executed("INSERT INTO material_slots")]
    assert all("DO NOTHING" in sql for sql in inserts)
    assert not any("DO UPDATE" in sql for sql in inserts)


@pytest.mark.asyncio
async def test_identity_columns_are_not_rewritten_by_later_writes():
    """第二步写回不得改动身份列与适用性——身份一旦确认就不该被重放改写。"""
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    row = await service.upsert_from_decision(_decision(), caliber="summary")
    await service.mark_not_applicable(
        conn._slot_by_id(row["id"])["slot_key"], note="依据 X"
    )
    await service.upsert_from_decision(_decision(), caliber="summary")

    updates = [sql for sql, _ in conn.executed("jurisdiction_org_id = COALESCE")]
    assert updates
    for sql in updates:
        assigned = _assigned_columns(sql)
        for forbidden in (
            "slot_key",
            "subject_org_id",
            "subject_kind",
            "material_scope",
            "fiscal_year",
            "report_kind",
            "mapping_key",
            "applicability_status",
        ):
            assert forbidden not in assigned, f"写回改动了不该改的列: {forbidden}"


@pytest.mark.asyncio
async def test_missing_identity_row_raises_instead_of_returning_none():
    """确保存在之后读不到行，必须抛错回滚，不能静默返回 None。"""
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)

    async def _vanish(sql, *args):
        conn.calls.append((sql, args))
        return None

    conn.fetchrow = _vanish  # type: ignore[method-assign]
    with pytest.raises(SlotIdentityRowMissing):
        await service.upsert_from_decision(_decision(), caliber="summary")


# ==== 5. 身份完整性判定：不能只看 mapping_key ===============================


@pytest.mark.parametrize(
    "overrides",
    [
        {"fiscal_year": None},
        {"report_kind": "unknown"},
        {"subject_kind": "unknown"},
        {"material_scope": "unknown"},
        {"subject_org_id": ""},
    ],
)
@pytest.mark.asyncio
async def test_incomplete_identity_stays_mapping_required(overrides):
    """``mapping_key`` 为空但身份不完整时，绝不允许滑进 not_due / uploaded。"""
    conn = FakeSlotConnection()
    row = _seed_slot(conn, **overrides)
    assert row["mapping_key"] == "", "用例前提：mapping_key 为空"

    service = MaterialSlotService(conn)
    result = await service.refresh_status(row["id"])

    assert result["status"] == "mapping_required"
    assert result["status_reason"] == "identity_unresolved"


@pytest.mark.asyncio
async def test_incomplete_identity_with_document_still_mapping_required():
    """即使已经绑定了文件版本，身份不完整也不能变成 uploaded。"""
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    row = _seed_slot(conn, fiscal_year=None, current_document_version_id=VERSION_ID)

    service = MaterialSlotService(conn)
    result = await service.refresh_status(row["id"])

    assert result["status"] == "mapping_required"


@pytest.mark.asyncio
async def test_complete_identity_can_advance_beyond_mapping_required():
    conn = FakeSlotConnection()
    row = _seed_slot(conn)  # unit + unit_self + final + 2024 + mapping_key=''

    service = MaterialSlotService(conn)
    result = await service.refresh_status(row["id"])
    assert result["status"] == "not_due"
    assert result["status_reason"] == "due_at_unknown"

    conn.seed_version(VERSION_ID, created_at=10)
    conn.versions[VERSION_ID]["slot_id"] = row["id"]
    row["current_document_version_id"] = VERSION_ID
    result = await service.refresh_status(row["id"])
    assert result["status"] == "uploaded"


def test_status_machine_matches_shared_identity_helper():
    """内存对象与数据库行用的是同一个判定函数。"""
    from src.schemas.material_slot import slot_identity_is_resolved

    assert slot_identity_is_resolved(
        subject_org_id=UNIT_ID,
        subject_kind="unit",
        material_scope="unit_self",
        report_kind="final",
        fiscal_year=2024,
        mapping_key="",
    )
    assert not slot_identity_is_resolved(
        subject_org_id=UNIT_ID,
        subject_kind="unit",
        material_scope="unit_self",
        report_kind="final",
        fiscal_year=None,
        mapping_key="",
    )
    assert not slot_identity_is_resolved(
        subject_org_id=UNIT_ID,
        subject_kind="unit",
        material_scope="unit_self",
        report_kind="final",
        fiscal_year=2024,
        mapping_key="doc:abc",
    )


# ==== 6. mark_not_applicable 不得绕过状态机 =================================


@pytest.mark.asyncio
async def test_mark_not_applicable_cannot_bypass_identity_gate():
    """身份未确认的槽位，标了不适用仍必须停在待确认。"""
    conn = FakeSlotConnection()
    row = _seed_slot(conn, mapping_key="doc:deadbeef", fiscal_year=None)
    service = MaterialSlotService(conn)

    result = await service.mark_not_applicable(row["slot_key"], note="本年度无此材料")

    # 事实写进去了
    assert result["applicability_status"] == "not_applicable"
    # 但状态仍由状态机决定：身份问题没解决，就不能对外说"已确认不适用"
    assert result["status"] == "mapping_required"
    assert result["status_reason"] == "identity_unresolved"


@pytest.mark.asyncio
async def test_mark_not_applicable_applies_when_identity_is_complete():
    conn = FakeSlotConnection()
    row = _seed_slot(conn)
    service = MaterialSlotService(conn)

    result = await service.mark_not_applicable(row["slot_key"], note="本单位本年度无决算")

    assert result["status"] == "not_applicable"
    assert result["status_reason"] == "applicability_marked_not_applicable"
    assert conn.slots_by_key[row["slot_key"]]["applicability_note"] == "本单位本年度无决算"


@pytest.mark.asyncio
async def test_mark_not_applicable_still_requires_a_reason():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    with pytest.raises(ValueError):
        await service.mark_not_applicable("some-key", note="   ")


@pytest.mark.asyncio
async def test_mark_not_applicable_does_not_write_status_directly():
    conn = FakeSlotConnection()
    row = _seed_slot(conn)
    service = MaterialSlotService(conn)
    await service.mark_not_applicable(row["slot_key"], note="依据 A")

    statements = [sql for sql, _ in conn.calls]

    # 写"适用性事实"的那条语句只能改适用性字段，不得顺带写 status/status_reason。
    # 按 SET 子句里被赋值的列名判断，而不是搜字符串——"applicability_status"
    # 本身含 "status"，用搜字符串的方式判断会把自己误判成违规。
    for sql in statements:
        if "SET applicability_status" not in sql:
            continue
        assigned = _assigned_columns(sql)
        assert "status" not in assigned, f"绕过状态机直接写 status: {sql}"
        assert "status_reason" not in assigned, f"绕过状态机直接写 status_reason: {sql}"

    # 反向确认：状态确实是由状态机的落库语句写的
    assert any("UPDATE material_slots" in sql and "SET status" in sql for sql in statements), (
        "没有调用状态机落库，状态是从别处写进去的"
    )


# ==== 10. caliber 冲突不静默覆盖 ============================================


def _decision(**overrides):
    from src.services import material_slot_resolver as resolver

    return resolver.decide_slot_allocation(
        metadata=_metadata(**overrides), org_records=ORG_RECORDS, checksum=CHECKSUM
    )


@pytest.mark.asyncio
async def test_caliber_is_adopted_when_previously_unknown():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    row = await service.upsert_from_decision(_decision(), caliber="summary")
    assert conn._slot_by_id(row["id"])["caliber"] == "summary"


@pytest.mark.asyncio
async def test_unknown_observation_does_not_downgrade_confirmed_caliber():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    row = await service.upsert_from_decision(_decision(), caliber="summary")
    await service.upsert_from_decision(_decision(), caliber="unknown")

    stored = conn._slot_by_id(row["id"])
    assert stored["caliber"] == "summary"
    assert stored["caliber_conflict_candidate"] is None


@pytest.mark.asyncio
async def test_contradictory_caliber_is_recorded_not_overwritten():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    row = await service.upsert_from_decision(_decision(), caliber="summary")
    await service.upsert_from_decision(_decision(), caliber="self")

    stored = conn._slot_by_id(row["id"])
    assert stored["caliber"] == "summary", "已确认口径被静默改写"
    assert stored["caliber_conflict_candidate"] == "self"
    assert stored["status"] == "mapping_required"
    assert stored["status_reason"] == "caliber_conflict"


@pytest.mark.asyncio
async def test_caliber_conflict_survives_an_agreeing_observation():
    """后来又一次识别成原值，不能把矛盾洗掉——矛盾还在，仍然要人裁决。"""
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    row = await service.upsert_from_decision(_decision(), caliber="summary")
    await service.upsert_from_decision(_decision(), caliber="self")
    await service.upsert_from_decision(_decision(), caliber="summary")

    stored = conn._slot_by_id(row["id"])
    assert stored["caliber_conflict_candidate"] == "self"
    assert stored["status_reason"] == "caliber_conflict"


@pytest.mark.asyncio
async def test_caliber_conflict_is_not_washed_away_by_status_refresh():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    row = await service.upsert_from_decision(_decision(), caliber="summary")
    await service.upsert_from_decision(_decision(), caliber="self")
    conn.seed_version(VERSION_ID, created_at=10)
    conn.versions[VERSION_ID]["slot_id"] = row["id"]

    # 绑版本 + 刷新状态：冲突必须还在，并且仍然把状态压在待确认
    await service.bind_document_version(row["id"], VERSION_ID)
    result = await service.refresh_status(row["id"])

    assert result["status"] == "mapping_required"
    assert result["status_reason"] == "caliber_conflict"
    assert conn._slot_by_id(row["id"])["caliber_conflict_candidate"] == "self"


@pytest.mark.asyncio
async def test_caliber_conflict_reaches_the_allocation_summary():
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    first = await allocate_for_document(
        conn,
        metadata={**_metadata(), "document_profile": {"caliber": {"value": "summary"}}},
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )
    assert first["slot_caliber"] == "summary"
    assert first["caliber_conflict_candidate"] is None

    # 第二次分析识别出相反口径：冲突必须出现在分配摘要里，供调用方与运维看到
    summary = await allocate_for_document(
        conn,
        metadata={**_metadata(), "document_profile": {"caliber": {"value": "self"}}},
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID,
    )
    assert summary["slot_caliber"] == "summary", "已确认口径被改写"
    assert summary["caliber_conflict_candidate"] == "self"
    assert summary["slot_status"] == "mapping_required"
    assert summary["slot_status_reason"] == "caliber_conflict"


# ==== 11. 版本排序：同 created_at 时用 id 兜底 ==============================


@pytest.mark.asyncio
async def test_same_timestamp_versions_order_by_id():
    conn = FakeSlotConnection()
    conn.seed_version(9, created_at=500)
    conn.seed_version(10, created_at=500)
    service = MaterialSlotService(conn)
    slot = _seed_slot(conn)

    # 先绑 10 再绑 9：数值上 9 < 10，指针不应被 9 抢走。
    # 若按字符串比较（"10" < "9"），结果会反过来——这条用例正是要钉住这一点。
    await service.bind_document_version(slot["id"], 10)
    await service.bind_document_version(slot["id"], 9)
    assert conn._slot_by_id(slot["id"])["current_document_version_id"] == 10

    # 反向顺序也应得到同一个结果（版本不可跨槽，因此用另一对版本）
    conn.seed_version(11, created_at=500)
    conn.seed_version(12, created_at=500)
    other = _seed_slot(conn, slot_key="other")
    await service.bind_document_version(other["id"], 11)
    await service.bind_document_version(other["id"], 12)
    assert conn._slot_by_id(other["id"])["current_document_version_id"] == 12


@pytest.mark.asyncio
async def test_newer_timestamp_wins_regardless_of_id():
    conn = FakeSlotConnection()
    conn.seed_version(3, created_at=900)
    conn.seed_version(50, created_at=100)
    service = MaterialSlotService(conn)
    slot = _seed_slot(conn)

    await service.bind_document_version(slot["id"], 50)
    await service.bind_document_version(slot["id"], 3)
    assert conn._slot_by_id(slot["id"])["current_document_version_id"] == 3


# ==== 进度反推：刷新不能把待办打回未开始 ====================================


@pytest.mark.parametrize(
    "status",
    ["uploaded", "processing", "failed", "review_required", "reviewing", "completed"],
)
def test_progress_inference_round_trips(status):
    """反推出的进度状态，用同一批事实再推一次仍得到原状态。"""
    analysis, review = infer_progress_state(status)
    result = derive_slot_status(
        identity_resolved=True,
        has_current_document=True,
        analysis_state=analysis,
        review_state=review,
    )
    assert result.status == status


@pytest.mark.asyncio
async def test_refresh_preserves_review_progress():
    """一次无关刷新不能把"待人工复核"打回"已上传"。"""
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    row = _seed_slot(
        conn,
        status="review_required",
        status_reason="findings_pending",
        current_document_version_id=VERSION_ID,
    )
    conn.versions[VERSION_ID]["slot_id"] = row["id"]

    service = MaterialSlotService(conn)
    result = await service.refresh_status(row["id"])
    assert result["status"] == "review_required"


@pytest.mark.asyncio
async def test_new_analysis_resets_review_progress_deliberately():
    """重分析之后回到"已上传待分析"：此前的人工结论针对的是上一次分析结果。"""
    conn = FakeSlotConnection()
    conn.seed_version(VERSION_ID, created_at=10)
    conn.seed_version(VERSION_ID + 1, created_at=20)
    row = _seed_slot(
        conn,
        status="review_required",
        current_document_version_id=VERSION_ID,
    )
    conn.versions[VERSION_ID]["slot_id"] = row["id"]

    summary = await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=VERSION_ID + 1,
    )
    assert summary["bound"] is True
    assert summary["slot_status"] == "uploaded"
