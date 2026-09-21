"""材料槽位身份与状态：本轮整改的正反例回归。

每条用例对应 WP1 验收清单里的一项真实风险。命名刻意保持"反例优先"：
断言的重点不是"正常情况下能跑通"，而是**识别不到、互相冲突、同名不同层级时
系统会不会自作聪明**。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.schemas.material_slot import (
    SlotIdentity,
    SlotIdentityError,
    build_slot_identity,
    mapping_key_for_document,
    resolve_subject_org,
)
from src.services import material_slot_resolver as resolver
from src.services.material_slot_service import (
    MaterialSlotService,
    safe_allocate_for_document,
)
from src.services.material_slot_status import (
    counts_as_missing,
    derive_slot_status,
    status_is_blocking,
)
from support_material_slot_db import FakeSlotConnection, FailingSlotConnection

# ---- 组织目录样例 -----------------------------------------------------------
# 直接取自 data/organizations.json 的真实记录（含真实 id 与 code），
# 其中"规划和自然资源局"的部门与本级单位同名——这正是必须隔离的一对。

CITY_ID = "187f76b6686f"
DISTRICT_ID = "833e15c63a38"
DEPT_ID = "8f773936806d"
UNIT_ID = "53f98bebfc3a"
DEPT_CODE = "DEPT_MLYYK9KC_46694122"
UNIT_CODE = "UNIT_MLYYK9MC_1C503660"
DEPT_NAME = "上海市普陀区规划和自然资源局"
UNIT_NAME = "上海市普陀区规划和自然资源局本级"

ORG_RECORDS = [
    {"id": CITY_ID, "name": "上海市", "level": "city", "parent_id": None, "code": None},
    {"id": DISTRICT_ID, "name": "普陀区", "level": "district", "parent_id": CITY_ID, "code": None},
    {
        "id": DEPT_ID,
        "name": DEPT_NAME,
        "level": "department",
        "parent_id": DISTRICT_ID,
        "code": DEPT_CODE,
    },
    {
        "id": UNIT_ID,
        "name": UNIT_NAME,
        "level": "unit",
        "parent_id": DEPT_ID,
        "code": UNIT_CODE,
    },
]

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _metadata(**overrides):
    base = {
        "organization_id": UNIT_ID,
        "organization_name": UNIT_NAME,
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
        "filename": "上海市普陀区规划和自然资源局本级 2024 年度单位决算.pdf",
    }
    base.update(overrides)
    return base


def _decide(metadata, checksum="a" * 64):
    return resolver.decide_slot_allocation(
        metadata=metadata, org_records=ORG_RECORDS, checksum=checksum
    )


# ==== 1. 同名部门与本部单位必须是两个槽位 ====================================


def test_same_name_department_and_unit_get_different_slots():
    dept = _decide(_metadata(organization_id=DEPT_ID, organization_name=DEPT_NAME))
    unit = _decide(_metadata(organization_id=UNIT_ID, organization_name=UNIT_NAME))

    assert dept.ok and unit.ok
    # 名称前缀相同（一个是另一个的前缀），按名称归并就会合成一条——
    # 断言两者身份键不同，等于守住"绝不按名称合并"。
    assert dept.identity.slot_key != unit.identity.slot_key
    assert dept.identity.subject_kind == "department"
    assert dept.identity.material_scope == "department_summary"
    assert unit.identity.subject_kind == "unit"
    assert unit.identity.material_scope == "unit_self"


def test_unit_slot_records_its_parent_department():
    unit = _decide(_metadata(organization_id=UNIT_ID))
    assert unit.department_org_id == DEPT_ID
    assert unit.department_name == DEPT_NAME
    assert unit.jurisdiction_org_id == DISTRICT_ID
    assert unit.jurisdiction_name == "普陀区"


def test_resolve_subject_org_refuses_name_only_lookup():
    """目录里没有该 id 时必须返回 None，而不是退回按名称猜。"""
    assert resolve_subject_org("not-an-org-id", ORG_RECORDS) is None
    assert resolve_subject_org("", ORG_RECORDS) is None
    assert resolve_subject_org(None, ORG_RECORDS) is None


# ==== 2. 同一主体的预算与决算是两个槽位 ======================================


def test_budget_and_final_are_separate_slots():
    budget = _decide(_metadata(report_kind="budget", doc_type="dept_budget"))
    final = _decide(_metadata(report_kind="final", doc_type="dept_final"))

    assert budget.ok and final.ok
    assert budget.identity.slot_key != final.identity.slot_key
    assert budget.identity.fiscal_year == final.identity.fiscal_year == 2024


def test_different_fiscal_years_are_separate_slots():
    y2024 = _decide(_metadata(report_year="2024"))
    y2025 = _decide(_metadata(report_year="2025"))
    assert y2024.identity.slot_key != y2025.identity.slot_key


# ==== 3. 财政年度与发布日期分离 ==============================================


def test_final_published_in_later_year_keeps_fiscal_year():
    """2024 年度决算 2025 年发布，槽位的财政年度仍必须是 2024。

    这是"按网页发布时间推导年度会造成整份材料归错年"的直接回归：
    元数据里同时存在发布日期与财政年度时，年度只能取自材料本身。
    """
    decision = _decide(
        _metadata(
            report_year="2024",
            published_at="2025-08-20T00:00:00+08:00",
            source_published_at="2025-08-20",
        )
    )
    assert decision.ok
    assert decision.identity.fiscal_year == 2024
    # 发布日期根本不参与身份判定：它进的是 material_sources.published_at，
    # 与槽位的 fiscal_year 是两张表两列，物理上不可能混用


@pytest.mark.asyncio
async def test_published_at_does_not_override_fiscal_year():
    decision = _decide(_metadata(report_year="2024"))
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    slot = await service.upsert_from_decision(decision)
    await service.record_source(
        slot["id"],
        source_kind="official_site",
        source_url="https://example.gov.cn/xxgk/2024-final",
        published_at=datetime(2025, 8, 20, tzinfo=timezone.utc),
    )

    stored = conn.slots_by_key[decision.identity.slot_key]
    assert stored["fiscal_year"] == 2024
    assert "INSERT INTO material_sources" in " ".join(sql for sql, _ in conn.calls)


# ==== 4/5. 版本与运行不得生成新业务材料 ======================================


@pytest.mark.asyncio
async def test_two_pdf_hashes_for_one_slot_become_two_versions():
    decision = _decide(_metadata())
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    slot = await service.upsert_from_decision(decision)

    conn.seed_version(1, created_at=100, file_hash="b" * 64)
    conn.seed_version(2, created_at=200, file_hash="c" * 64)

    await service.bind_document_version(slot["id"], 1)
    await service.bind_document_version(slot["id"], 2)

    assert conn.versions[1]["slot_id"] == slot["id"]
    assert conn.versions[2]["slot_id"] == slot["id"]
    # 槽位只有一个，版本有两个
    assert len(conn.slots_by_key) == 1
    # 当前版本指针前进到较新的那个
    assert conn._slot_by_id(slot["id"])["current_document_version_id"] == 2


@pytest.mark.asyncio
async def test_out_of_order_binding_does_not_rewind_current_version():
    """乱序回填不能把"当前版本"退回旧文件。"""
    decision = _decide(_metadata())
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    slot = await service.upsert_from_decision(decision)

    conn.seed_version(1, created_at=100)
    conn.seed_version(2, created_at=200)

    await service.bind_document_version(slot["id"], 2)
    await service.bind_document_version(slot["id"], 1)

    assert conn._slot_by_id(slot["id"])["current_document_version_id"] == 2


@pytest.mark.asyncio
async def test_reanalysis_of_same_version_does_not_create_new_material():
    """同一份 PDF 的同一版本被分析两次，仍然只有一个槽位。"""
    decision = _decide(_metadata())
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)

    first = await service.upsert_from_decision(decision)
    conn.seed_version(7, created_at=100)
    await service.bind_document_version(first["id"], 7)

    second = await service.upsert_from_decision(decision)
    await service.bind_document_version(second["id"], 7)

    assert first["id"] == second["id"]
    assert len(conn.slots_by_key) == 1
    stored = conn.slots_by_key[decision.identity.slot_key]
    # 关键是"只真正插入过一次"。更新次数会随调用次数增长，拿它当幂等证据
    # 等于把断言绑在实现细节上——三次重放就该是 3，说明不了任何事。
    assert stored["insert_count"] == 1


# ==== 6/7. 未到期与缺失 =====================================================


def test_not_due_when_due_at_is_in_the_future():
    result = derive_slot_status(
        due_at=NOW + timedelta(days=30),
        identity_resolved=True,
        has_current_document=False,
        now=NOW,
    )
    assert result.status == "not_due"
    assert counts_as_missing(result.status, result.reason) is False


def test_missing_only_when_due_passed_and_no_document():
    result = derive_slot_status(
        due_at=NOW - timedelta(days=1),
        identity_resolved=True,
        has_current_document=False,
        now=NOW,
    )
    assert result.status == "missing"
    assert counts_as_missing(result.status, result.reason) is True


def test_unknown_due_date_is_not_counted_as_missing():
    """截止时间未知时无法证明逾期，必须停在未到期并注明成因。"""
    result = derive_slot_status(
        due_at=None, identity_resolved=True, has_current_document=False, now=NOW
    )
    assert result.status == "not_due"
    assert result.reason == "due_at_unknown"
    # 关键断言：绝不能因为"没有 PDF"就算成缺失
    assert counts_as_missing(result.status, result.reason) is False


def test_document_present_overrides_missing_even_after_due_date():
    result = derive_slot_status(
        due_at=NOW - timedelta(days=365),
        identity_resolved=True,
        has_current_document=True,
        now=NOW,
    )
    assert result.status == "uploaded"


def test_identity_unresolved_beats_everything_else():
    result = derive_slot_status(
        due_at=NOW - timedelta(days=1), identity_resolved=False, has_current_document=False
    )
    assert result.status == "mapping_required"
    assert result.reason == "identity_unresolved"
    assert counts_as_missing(result.status, result.reason) is False


def test_not_applicable_is_not_blocking_and_not_missing():
    result = derive_slot_status(applicability_status="not_applicable")
    assert result.status == "not_applicable"
    assert status_is_blocking(result.status) is False
    assert counts_as_missing(result.status, result.reason) is False


def test_blocking_predicate_covers_unfinished_states():
    for status in ("missing", "uploaded", "processing", "review_required", "reviewing",
                   "mapping_required", "failed"):
        assert status_is_blocking(status) is True, status
    for status in ("not_due", "not_applicable", "completed"):
        assert status_is_blocking(status) is False, status


# ==== 8/9/10. 不可靠数据必须停在 mapping_required ============================


def test_unknown_year_goes_to_mapping_required():
    decision = _decide(_metadata(report_year=None, fiscal_year=None))
    assert decision.status == resolver.DECISION_MAPPING_REQUIRED
    assert decision.reason == resolver.REASON_YEAR_UNKNOWN
    assert decision.identity.fiscal_year is None
    assert decision.identity.is_resolved is False
    # 必须留痕：用文档校验和区分，避免所有未知年份材料挤进同一条
    assert decision.identity.mapping_key.startswith("doc:")


def test_two_unknown_year_documents_do_not_collapse_into_one_slot():
    a = _decide(_metadata(report_year=None), checksum="a" * 64)
    b = _decide(_metadata(report_year=None), checksum="b" * 64)
    assert a.identity.slot_key != b.identity.slot_key


def test_kind_conflict_goes_to_mapping_required():
    """doc_type 说决算、report_kind 说预算——不挑一个，报冲突。"""
    decision = _decide(_metadata(report_kind="budget", doc_type="dept_final"))
    assert decision.status == resolver.DECISION_MAPPING_REQUIRED
    assert decision.reason == resolver.REASON_KIND_CONFLICT
    assert decision.identity.report_kind == "unknown"


def test_profile_declared_kind_conflict_is_respected():
    decision = _decide(
        _metadata(
            report_kind="final",
            doc_type="dept_final",
            document_profile={
                "report_kind": {"value": "final", "source": "cover_title"},
                "conflicts": [{"field": "report_kind", "values": ["budget", "final"]}],
            },
        )
    )
    assert decision.reason == resolver.REASON_KIND_CONFLICT


def test_unknown_kind_goes_to_mapping_required():
    decision = _decide(_metadata(report_kind=None, doc_type=None))
    assert decision.reason == resolver.REASON_KIND_UNKNOWN


def test_year_conflict_goes_to_mapping_required():
    decision = _decide(_metadata(report_year="2024", fiscal_year="2025"))
    assert decision.reason == resolver.REASON_YEAR_CONFLICT


def test_org_not_in_catalog_goes_to_mapping_required():
    decision = _decide(_metadata(organization_id="deadbeef0000"))
    assert decision.status == resolver.DECISION_MAPPING_REQUIRED
    assert decision.reason == resolver.REASON_ORGANIZATION_UNRESOLVED
    assert decision.identity.subject_kind == "unknown"


def test_missing_org_id_never_falls_back_to_name_matching():
    """只给名称不给 id 时不允许按名称归属——同名部门/单位无法区分。"""
    decision = _decide(_metadata(organization_id=None, organization_name=DEPT_NAME))
    assert decision.status == resolver.DECISION_MAPPING_REQUIRED
    assert decision.reason == resolver.REASON_ORGANIZATION_MISSING
    # 目录里确实只有一个同名记录，也仍然不自动映射
    assert decision.detail["same_name_match_count"] == 1


def test_same_name_department_and_unit_is_flagged_ambiguous():
    # 同名部门 + 同名单位：目录里两条记录名字完全一样，只靠名称无法区分，
    # 必须被标成歧义（这正是 §14.3 要求覆盖的"同名部门/本部"场景）
    records = ORG_RECORDS + [
        {
            "id": "0000dupdept0",
            "name": DEPT_NAME,
            "level": "department",
            "parent_id": DISTRICT_ID,
            "code": None,
        },
        {
            "id": "0000dupunit0",
            "name": DEPT_NAME,
            "level": "unit",
            "parent_id": "0000dupdept0",
            "code": None,
        },
    ]
    decision = resolver.decide_slot_allocation(
        metadata=_metadata(organization_id=None, organization_name=DEPT_NAME),
        org_records=records,
        checksum="d" * 64,
    )
    assert decision.detail["same_name_ambiguous"] is True


def test_no_checksum_is_unallocatable_not_a_shared_slot():
    """连文档校验和都没有时宁可不建槽，也不让多条材料挤进同一个槽位。"""
    decision = _decide(_metadata(report_year=None), checksum=None)
    assert decision.status == resolver.DECISION_UNALLOCATABLE
    assert decision.reason == resolver.REASON_NO_DOCUMENT_KEY
    assert decision.identity is None


# ==== 身份键本身的性质 ======================================================


def test_slot_key_is_stable_across_equivalent_inputs():
    a = build_slot_identity(
        subject_org_id=UNIT_ID,
        subject_kind="unit",
        material_scope="unit_self",
        report_kind="final",
        fiscal_year="2024",
    )
    b = build_slot_identity(
        subject_org_id=UNIT_ID,
        subject_kind="unit",
        material_scope="unit_self",
        report_kind="final",
        fiscal_year=2024,
    )
    assert a.slot_key == b.slot_key


def test_slot_identity_rejects_impossible_year_and_scope():
    with pytest.raises(SlotIdentityError):
        SlotIdentity(UNIT_ID, "unit", "unit_self", "final", 1999)
    with pytest.raises(SlotIdentityError):
        SlotIdentity(UNIT_ID, "unit", "unit_self", "final", 2100)
    with pytest.raises(SlotIdentityError):
        SlotIdentity("", "unit", "unit_self", "final", 2024)
    with pytest.raises(SlotIdentityError):
        SlotIdentity(UNIT_ID, "ministry", "unit_self", "final", 2024)


def test_mapping_key_helper_is_prefixed():
    assert mapping_key_for_document("abc") == "doc:abc"
    assert mapping_key_for_document(None) == ""
    assert mapping_key_for_document("  ") == ""


# ==== 写入路径的契约：幂等与故障隔离 ========================================


@pytest.mark.asyncio
async def test_allocate_is_idempotent_on_slot_key():
    conn = FakeSlotConnection()
    first = await safe_allocate_for_document(
        conn, metadata=_metadata(), checksum="e" * 64, org_records=ORG_RECORDS
    )
    second = await safe_allocate_for_document(
        conn, metadata=_metadata(), checksum="e" * 64, org_records=ORG_RECORDS
    )
    assert first["slot_key"] == second["slot_key"]
    assert len(conn.slots_by_key) == 1


@pytest.mark.asyncio
async def test_idempotency_is_enforced_by_the_database_on_slot_key():
    """幂等由数据库在 slot_key 上保证，不是靠调用方自觉。

    写入拆成"确保存在 → 锁内解析 → 写回"三步之后，第一步的冲突动作是
    ``DO NOTHING``：已存在的行一个字段都不动。这正是首次创建并发竞争
    被消除的机制——锁外算出的结论没有机会覆盖别人写下的口径。
    """
    conn = FakeSlotConnection()
    await safe_allocate_for_document(
        conn, metadata=_metadata(), checksum="f" * 64, org_records=ORG_RECORDS
    )
    inserts = conn.executed("INSERT INTO material_slots")
    assert len(inserts) == 1, "一次分配只应发出一条确保存在的插入语句"
    assert "ON CONFLICT (slot_key) DO NOTHING" in inserts[0][0]


@pytest.mark.asyncio
async def test_slot_failure_never_breaks_the_caller():
    """数据库故障时返回 error 摘要，不抛异常——槽位是旁路能力。"""
    result = await safe_allocate_for_document(
        FailingSlotConnection(),
        metadata=_metadata(),
        checksum="1" * 64,
        org_records=ORG_RECORDS,
    )
    assert result["status"] == "error"
    assert result["reason"] == "slot_allocation_failed"


@pytest.mark.asyncio
async def test_unallocatable_decision_writes_nothing():
    conn = FakeSlotConnection()
    result = await safe_allocate_for_document(
        conn,
        metadata=_metadata(report_year=None),
        checksum=None,
        org_records=ORG_RECORDS,
    )
    assert result["status"] == resolver.DECISION_UNALLOCATABLE
    assert conn.calls == []


@pytest.mark.asyncio
async def test_refresh_status_uses_stored_facts():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    decision = _decide(_metadata())
    slot = await service.upsert_from_decision(decision, due_at=NOW - timedelta(days=5))

    # 尚无当前版本 -> 已过期 -> missing
    stored = conn._slot_by_id(slot["id"])
    stored["due_at"] = NOW - timedelta(days=5)
    result = await service.refresh_status(slot["id"], now=NOW)
    assert result["status"] == "missing"

    # 绑定版本后重算 -> 不再缺失
    conn.seed_version(11, created_at=10)
    await service.bind_document_version(slot["id"], 11)
    result = await service.refresh_status(slot["id"], now=NOW)
    assert result["status"] == "uploaded"


@pytest.mark.asyncio
async def test_mark_not_applicable_requires_a_reason():
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    with pytest.raises(ValueError):
        await service.mark_not_applicable("some-key", note="   ")
