"""材料台账查询服务（纯逻辑）测试。

覆盖聚合、筛选、排序、槽位代表选择这些不依赖数据库的规则。接口层的契约、
鉴权与 SQL 条数由 ``tests/test_material_ledger_api.py`` 覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.material_ledger_query_service import (
    MaterialLedgerQueryService,
    MaterialSlotFilters,
    UnknownMaterialStatusError,
    is_head_unit_name,
    normalize_org_name,
    relationship_of,
)
from support_material_ledger_db import make_matrix_row, make_slot

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


# ---- 过滤条件 -> WHERE ------------------------------------------------------


def test_filters_emit_expected_where_and_parameter_order():
    """参数顺序必须与 ``$n`` 一一对应：错位会静默查出别的年份的数据。"""
    where, params = MaterialSlotFilters(
        fiscal_year=2025, report_kind="budget", status="missing", jurisdiction_id="d-1"
    ).where()

    assert where == (
        "fiscal_year = $1 AND report_kind = $2 AND status = $3 AND jurisdiction_org_id = $4"
    )
    assert params == [2025, "budget", "missing", "d-1"]


def test_report_kind_all_is_not_a_database_filter():
    """``all`` 只是查询参数：不得变成 ``report_kind = 'all'``（库里没有这个值）。"""
    where, params = MaterialSlotFilters(report_kind="all").where()

    assert where == "TRUE"
    assert params == []


def test_empty_visible_org_scope_matches_nothing():
    """账号一个组织都没授权时，条件必须恒假，而不是退化成"不过滤"。"""
    where, params = MaterialSlotFilters(visible_org_ids=[]).where()

    assert where == (
        "(jurisdiction_org_id = ANY($1::text[])"
        " OR department_org_id = ANY($1::text[])"
        " OR subject_org_id = ANY($1::text[]))"
    )
    assert params == [[]]  # 空数组 → ANY 恒假 → 一条都查不出来


def test_visible_org_scope_is_an_or_across_three_slot_levels():
    """权限条件必须覆盖槽位的三个层次且是 OR。

    写成 AND 会把 department / unit 授权全部误杀：部门级授权下槽位的 subject
    往往是某个单位，单位级授权下 department 列同样不会命中被授权节点。
    """
    where, params = MaterialSlotFilters(visible_org_ids=["dept-a", "unit-a1"]).where()

    assert " OR " in where
    assert " AND department_org_id" not in where
    for column in ("jurisdiction_org_id", "department_org_id", "subject_org_id"):
        assert f"{column} = ANY($1::text[])" in where
    assert params == [["dept-a", "unit-a1"]], "三列共用同一个数组参数"


def test_admin_scope_adds_no_permission_clause():
    where, params = MaterialSlotFilters(fiscal_year=2025).where()

    assert where == "fiscal_year = $1"
    assert params == [2025]


# ---- 状态分桶 ---------------------------------------------------------------


def _aggregate(district_rows, summary_rows):
    return MaterialLedgerQueryService.aggregate_coverage(district_rows, summary_rows)


def test_all_ten_statuses_are_counted_and_sum_to_total():
    statuses = (
        "not_due",
        "missing",
        "uploaded",
        "processing",
        "review_required",
        "reviewing",
        "completed",
        "not_applicable",
        "mapping_required",
        "failed",
    )
    summary_rows = [
        {
            "report_kind": "budget",
            "status": status,
            "slot_count": 1,
            "due_at_unknown_count": 0,
            "jurisdiction_unknown_count": 0,
        }
        for status in statuses
    ]

    data = _aggregate([], summary_rows)
    counts = data.summary.status_counts.model_dump()

    assert set(counts) == set(statuses), "状态计数必须覆盖全部 10 个取值，一个不能少"
    assert all(value == 1 for value in counts.values())
    assert sum(counts.values()) == data.summary.slot_total == 10


def test_unknown_status_is_rejected_instead_of_silently_dropped():
    """取值域之外的状态必须报错：静默丢弃会让分类之和对不上总数。"""
    with pytest.raises(UnknownMaterialStatusError) as raised:
        _aggregate(
            [],
            [
                {
                    "report_kind": "budget",
                    "status": "archived",
                    "slot_count": 1,
                    "due_at_unknown_count": 0,
                    "jurisdiction_unknown_count": 0,
                }
            ],
        )

    # 原始状态码通过属性透出（消息里不带运行时值，避免顺着 {e} 进日志）
    assert raised.value.status_code == "archived"
    assert "archived" not in str(raised.value)


def test_kind_split_keeps_unknown_kind_out_of_both_sides():
    rows = [
        {"report_kind": "budget", "status": "uploaded", "slot_count": 2, "due_at_unknown_count": 0, "jurisdiction_unknown_count": 0},
        {"report_kind": "final", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0, "jurisdiction_unknown_count": 0},
        {"report_kind": "unknown", "status": "mapping_required", "slot_count": 3, "due_at_unknown_count": 3, "jurisdiction_unknown_count": 0},
    ]

    summary = _aggregate([], rows).summary

    assert summary.slot_total == 6
    assert summary.budget_total == 2
    assert summary.final_total == 1
    assert summary.unknown_kind_total == 3
    assert summary.budget_total + summary.final_total + summary.unknown_kind_total == summary.slot_total
    assert summary.due_at_unknown == 3, "截止时间未知必须独立可见"


def test_jurisdiction_less_slots_are_counted_but_not_shown_as_a_district_card():
    """没有行政区划的槽位不进区县卡片，但必须在汇总里有出口（不许凭空消失）。"""
    district_rows = [
        {
            "jurisdiction_org_id": "d-1",
            "jurisdiction_name": "普陀区",
            "report_kind": "budget",
            "status": "uploaded",
            "slot_count": 1,
            "due_at_unknown_count": 0,
        }
    ]
    summary_rows = [
        {"report_kind": "budget", "status": "uploaded", "slot_count": 2, "due_at_unknown_count": 0, "jurisdiction_unknown_count": 1}
    ]

    data = _aggregate(district_rows, summary_rows)

    assert [item.district_id for item in data.districts] == ["d-1"]
    assert data.summary.slot_total == 2, "全局汇总取独立查询，不把区县行相加"
    assert data.summary.jurisdiction_unknown_total == 1


def test_not_due_confirmed_is_counted_separately_from_not_due_total():
    """``not_due`` 是状态机事实，``not_due_confirmed`` 是"确认还没到期"的业务数字。

    两个条件必须同时成立（``status='not_due'`` 且 ``reason='due_not_reached'``）；
    "截止时间未知"的槽位也停在 not_due，因此不能算进"未到期"，
    也不能用 ``not_due - due_at_unknown`` 在前端相减得出（due_at_unknown 可能与
    任意状态并存）。
    """
    rows = [
        {
            "report_kind": "budget",
            "status": "not_due",
            "slot_count": 1,
            "due_at_unknown_count": 0,
            "not_due_confirmed_count": 1,
            "jurisdiction_unknown_count": 0,
        },
        {
            "report_kind": "budget",
            "status": "not_due",
            "slot_count": 1,
            "due_at_unknown_count": 1,
            "not_due_confirmed_count": 0,
            "jurisdiction_unknown_count": 0,
        },
        {
            "report_kind": "final",
            "status": "uploaded",
            "slot_count": 1,
            "due_at_unknown_count": 1,
            "not_due_confirmed_count": 0,
            "jurisdiction_unknown_count": 0,
        },
    ]

    summary = _aggregate([], rows).summary

    assert summary.status_counts.not_due == 2, "状态机事实：两条都停在 not_due"
    assert summary.not_due_confirmed == 1, "只有 due_not_reached 那条才算确认未到期"
    assert summary.due_at_unknown == 2, "due_at IS NULL 是独立的数据质量指标"
    assert summary.status_counts.uploaded == 1


def test_district_order_is_by_name_then_id_regardless_of_input_order():
    rows = [
        {"jurisdiction_org_id": "d-b", "jurisdiction_name": "静安区", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
        {"jurisdiction_org_id": "d-a2", "jurisdiction_name": "普陀区", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
        {"jurisdiction_org_id": "d-a1", "jurisdiction_name": "普陀区", "report_kind": "final", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
    ]

    forward = [item.district_id for item in _aggregate(rows, []).districts]
    backward = [item.district_id for item in _aggregate(list(reversed(rows)), []).districts]

    assert forward == ["d-a1", "d-a2", "d-b"], "同名区划必须用 id 兜底，顺序唯一"
    assert backward == forward, "同一份数据的顺序不能随返回行顺序漂移"


def test_district_with_missing_name_falls_back_to_id():
    rows = [
        {"jurisdiction_org_id": "d-1", "jurisdiction_name": None, "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0}
    ]

    item = _aggregate(rows, []).districts[0]

    assert item.district_name == "d-1", "名字缺失时用 id 兜底，不能是空字符串"


# ---- 部门聚合 ---------------------------------------------------------------


def test_department_aggregate_counts_distinct_subjects_and_splits_kind():
    rows = [
        {"department_org_id": "dept-1", "department_name": "规划局", "subject_org_id": "s-1", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
        {"department_org_id": "dept-1", "department_name": "规划局", "subject_org_id": "s-2", "report_kind": "budget", "status": "missing", "slot_count": 2, "due_at_unknown_count": 0},
        {"department_org_id": "dept-1", "department_name": "规划局", "subject_org_id": "s-2", "report_kind": "final", "status": "review_required", "slot_count": 1, "due_at_unknown_count": 1},
        {"department_org_id": None, "department_name": None, "subject_org_id": "gov", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
    ]

    items = MaterialLedgerQueryService.aggregate_district_departments(rows)

    assert [item.department_id for item in items] == ["dept-1", None], "未归入主管部门的行排在最后"
    dept = items[0]
    assert dept.subject_count == 2
    assert dept.slot_total == 4
    assert dept.budget.slot_total == 3
    assert dept.final.slot_total == 1
    assert dept.budget.status_counts.uploaded == 1
    assert dept.budget.status_counts.missing == 2
    assert dept.final.status_counts.review_required == 1
    assert dept.status_counts.missing == 2
    assert dept.missing == 2 and dept.not_due == 0, "missing/not_due 便捷字段必须来自同一份分桶"
    assert dept.due_at_unknown == 1
    assert items[1].department_name is None


def test_department_order_is_by_name_then_id_regardless_of_input_order():
    rows = [
        {"department_org_id": "dept-2", "department_name": "民政局", "subject_org_id": "s-1", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
        {"department_org_id": "dept-1", "department_name": "民政局", "subject_org_id": "s-2", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
        {"department_org_id": "dept-3", "department_name": "规划局", "subject_org_id": "s-3", "report_kind": "budget", "status": "uploaded", "slot_count": 1, "due_at_unknown_count": 0},
    ]

    forward = [item.department_id for item in MaterialLedgerQueryService.aggregate_district_departments(rows)]
    backward = [
        item.department_id
        for item in MaterialLedgerQueryService.aggregate_district_departments(list(reversed(rows)))
    ]

    assert forward == ["dept-1", "dept-2", "dept-3"]
    assert backward == forward


# ---- 主体关系 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("上海市普陀区规划和自然资源局本级", True),
        ("上海市普陀区规划和自然资源局（本级）", True),
        ("上海市普陀区规划和自然资源局 (本部)", True),
        ("上海市普陀区规划和自然资源局执法大队", False),
        ("", False),
        (None, False),
    ],
)
def test_head_unit_name_detection(name, expected):
    assert is_head_unit_name(name) is expected


def test_relationship_of_treats_same_name_unit_as_head_unit():
    """部门与其本级单位可以**完全同名**：名称里没有"本级/本部"也要判为本部单位。

    真实形态（见 app/tests/uploadCenterAdapters.test.ts 的同名反例）：
    部门「上海市普陀区财政局」与单位「上海市普陀区财政局」，
    名称完全相同、id 与 level 不同。只靠后缀会把本级单位错判成直属单位。
    """
    assert (
        relationship_of(
            subject_org_id="unit-caizheng-head",
            department_id="dept-caizheng",
            subject_kind="unit",
            material_scope="unit_self",
            subject_org_name="上海市普陀区财政局",
            department_name="上海市普陀区财政局",
        )
        == "head_unit"
    )


def test_relationship_of_same_name_requires_department_name_evidence():
    """没有部门名可比对时不猜：仅凭单位名无法证明它是本级，按直属单位处理。"""
    assert (
        relationship_of(
            subject_org_id="unit-x",
            department_id="dept-x",
            subject_kind="unit",
            material_scope="unit_self",
            subject_org_name="上海市普陀区财政局",
            department_name=None,
        )
        == "subordinate_unit"
    )


def test_relationship_of_still_prefers_explicit_marker_over_name_mismatch():
    assert (
        relationship_of(
            subject_org_id="unit-y",
            department_id="dept-y",
            subject_kind="unit",
            material_scope="unit_self",
            subject_org_name="上海市普陀区民政局（本级）",
            department_name="上海市普陀区民政局",
        )
        == "head_unit"
    )


def test_normalize_org_name_matches_frontend_unit_match_semantics():
    """归一化口径与前端 app/lib/unitMatch.ts 的 normalizeOrgName 对齐。"""
    assert normalize_org_name("上海市普陀区财政局（本级）") == "上海市普陀区财政局"
    assert normalize_org_name("上海市普陀区财政局(本部)") == "上海市普陀区财政局"
    assert normalize_org_name(" 上海市普陀区 财政局 ") == "上海市普陀区财政局"
    assert normalize_org_name("上海市普陀区财政局") == "上海市普陀区财政局"
    assert normalize_org_name(None) == ""
    assert is_head_unit_name("上海市普陀区财政局", "上海市普陀区财政局（本级）") is True
    assert is_head_unit_name("上海市普陀区财政局执法大队", "上海市普陀区财政局") is False


def test_relationship_of_never_guesses_subordinate_for_unknown_kind():
    """主体层级未知时不能猜成"直属单位"。"""
    assert (
        relationship_of(
            subject_org_id="s-1",
            department_id="dept-1",
            subject_kind="unknown",
            material_scope="unknown",
            subject_org_name="某个说不清的单位",
        )
        == "relationship_unknown"
    )


def test_relationship_of_marks_department_itself_as_summary():
    assert (
        relationship_of(
            subject_org_id="dept-1",
            department_id="dept-1",
            subject_kind="department",
            material_scope="department_summary",
            subject_org_name="规划局",
        )
        == "department_summary"
    )


def test_same_name_department_and_unit_stay_separate_subjects():
    """同名部门与同名本级单位必须各自成行（禁止按名称归并）。"""
    rows = [
        make_matrix_row(
            id="slot-dept",
            slot_key="key-dept",
            subject_org_id="dept-1",
            subject_org_name="上海市普陀区规划和自然资源局",
            subject_kind="department",
            material_scope="department_summary",
            report_kind="budget",
        ),
        make_matrix_row(
            id="slot-unit",
            slot_key="key-unit",
            subject_org_id="unit-1",
            subject_org_name="上海市普陀区规划和自然资源局本级",
            subject_kind="unit",
            material_scope="unit_self",
            report_kind="budget",
        ),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    assert [row.subject_org_id for row in data.groups.department_summary] == ["dept-1"]
    assert [row.subject_org_id for row in data.groups.head_unit] == ["unit-1"]
    assert data.groups.subordinate_units == []


def test_same_name_head_unit_is_not_grouped_as_subordinate():
    """完全同名的部门 / 本级单位必须分别落在部门汇总与本部单位，且不进入直属单位。"""
    rows = [
        make_matrix_row(
            id="slot-dept",
            slot_key="key-dept",
            subject_org_id="dept-caizheng",
            subject_org_name="上海市普陀区财政局",
            subject_kind="department",
            material_scope="department_summary",
            report_kind="budget",
        ),
        make_matrix_row(
            id="slot-head",
            slot_key="key-head",
            subject_org_id="unit-caizheng-head",
            subject_org_name="上海市普陀区财政局",
            subject_kind="unit",
            material_scope="unit_self",
            report_kind="budget",
        ),
        make_matrix_row(
            id="slot-sub",
            slot_key="key-sub",
            subject_org_id="unit-caizheng-pay",
            subject_org_name="上海市普陀区财政局支付中心",
            subject_kind="unit",
            material_scope="unit_self",
            report_kind="budget",
        ),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows,
        department_id="dept-caizheng",
        fiscal_year=2025,
        department_name="上海市普陀区财政局",
    )

    assert [row.subject_org_id for row in data.groups.department_summary] == ["dept-caizheng"]
    assert [row.subject_org_id for row in data.groups.head_unit] == ["unit-caizheng-head"]
    assert [row.subject_org_id for row in data.groups.subordinate_units] == ["unit-caizheng-pay"]
    assert all(
        row.subject_org_id != "unit-caizheng-head" for row in data.groups.subordinate_units
    ), "同级同名的本级单位绝不能落进直属单位"


def test_subject_with_conflicting_self_declaration_goes_to_relationship_unknown():
    """同一主体的两条材料对"自己属于哪一层"说法不一致时不猜。"""
    rows = [
        make_matrix_row(id="a", slot_key="ka", subject_org_id="unit-9", subject_kind="unit", material_scope="unit_self", report_kind="budget"),
        make_matrix_row(id="b", slot_key="kb", subject_org_id="unit-9", subject_kind="unit", material_scope="department_summary", report_kind="final"),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    assert data.groups.relationship_unknown[0].subject_org_id == "unit-9"
    assert data.groups.subordinate_units == []
    assert data.groups.head_unit == []


def test_subject_rows_are_sorted_by_name_then_id():
    rows = [
        make_matrix_row(id="s2", slot_key="k2", subject_org_id="unit-b", subject_org_name="规划中心", subject_kind="unit"),
        make_matrix_row(id="s1", slot_key="k1", subject_org_id="unit-a", subject_org_name="安全中心", subject_kind="unit"),
        make_matrix_row(id="s3", slot_key="k3", subject_org_id="unit-c", subject_org_name="安全中心", subject_kind="unit"),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    # 排序按 Unicode 码点（安 U+5B89 < 规 U+89C4），刻意不做拼音/locale 排序：
    # 拼音排序会让同一份数据在不同 locale 的机器上得到不同顺序。
    assert [row.subject_org_id for row in data.groups.subordinate_units] == [
        "unit-a",
        "unit-c",
        "unit-b",
    ]


# ---- 代表槽位的选择 ---------------------------------------------------------


def test_resolved_slot_wins_over_document_placeholder():
    """身份已确认的槽位优先于按文档临时安置的占位槽位。"""
    rows = [
        make_matrix_row(
            id="placeholder",
            slot_key="kp",
            mapping_key="doc:abc",
            updated_at=NOW,
            status="mapping_required",
            status_reason="identity_unresolved",
        ),
        make_matrix_row(id="resolved", slot_key="kr", mapping_key="", updated_at=NOW - timedelta(days=3)),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    slot = data.groups.subordinate_units[0].budget.slot
    assert slot is not None and slot.slot_id == "resolved"


def test_newest_slot_wins_between_two_resolved_slots():
    rows = [
        make_matrix_row(id="old", slot_key="ko", updated_at=NOW - timedelta(days=10)),
        make_matrix_row(id="new", slot_key="kn", updated_at=NOW),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    slot = data.groups.subordinate_units[0].budget.slot
    assert slot is not None and slot.slot_id == "new"


def test_slot_without_updated_at_loses_to_one_with_timestamp():
    rows = [
        make_matrix_row(id="no-time", slot_key="k0", updated_at=None),
        make_matrix_row(id="timed", slot_key="k1", updated_at=NOW),
    ]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    slot = data.groups.subordinate_units[0].budget.slot
    assert slot is not None and slot.slot_id == "timed"


def test_budget_and_final_slots_never_swap_places():
    rows = [
        make_matrix_row(id="b", slot_key="kb", report_kind="budget", status="uploaded", updated_at=NOW),
        make_matrix_row(id="f", slot_key="kf", report_kind="final", status="review_required", updated_at=NOW),
    ]

    row = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    ).groups.subordinate_units[0]

    assert row.budget.slot is not None and row.budget.slot.slot_id == "b"
    assert row.budget.slot.status == "uploaded"
    assert row.final.slot is not None and row.final.slot.slot_id == "f"
    assert row.final.slot.status == "review_required"
    assert row.unclassified.exists is False and row.unclassified.slot is None


def test_unknown_kind_slot_is_surfaced_as_unclassified_not_dropped():
    rows = [make_matrix_row(id="u", slot_key="ku", report_kind="unknown", status="mapping_required")]

    row = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    ).groups.subordinate_units[0]

    assert row.unclassified.exists is True
    assert row.unclassified.slot is not None
    assert row.unclassified.slot.report_kind == "unknown"
    assert row.budget.exists is False and row.final.exists is False


def test_formal_issue_count_is_null_not_zero():
    rows = [make_matrix_row(id="a", slot_key="ka")]

    slot = (
        MaterialLedgerQueryService.aggregate_department_matrix(
            rows, department_id="dept-1", fiscal_year=2025
        )
        .groups.subordinate_units[0]
        .budget.slot
    )

    assert slot is not None
    assert slot.formal_issue_count is None, "复核生命周期未接通时必须为 null，禁止用 0 冒充'没有问题'"


def test_empty_department_matrix_keeps_department_header_from_slots():
    """部门存在但该年度没有任何槽位：分组全空，但部门信息仍要给出。"""
    data = MaterialLedgerQueryService.aggregate_department_matrix(
        [],
        department_id="dept-1",
        fiscal_year=2025,
        department_name="规划局",
        jurisdiction_id="d-1",
        jurisdiction_name="普陀区",
    )

    assert data.department.department_name == "规划局"
    assert data.department.jurisdiction_name == "普陀区"
    assert data.fiscal_year == 2025
    assert data.groups.department_summary == []
    assert data.groups.head_unit == []
    assert data.groups.subordinate_units == []
    assert data.groups.relationship_unknown == []


def test_department_matrix_falls_back_to_slot_name_snapshot():
    """组织目录查不到部门名时用槽位里的名称快照，而不是显示一串 id。"""
    rows = [make_matrix_row(id="a", slot_key="ka", department_name="规划局（旧名）")]

    data = MaterialLedgerQueryService.aggregate_department_matrix(
        rows, department_id="dept-1", fiscal_year=2025
    )

    assert data.department.department_name == "规划局（旧名）"
