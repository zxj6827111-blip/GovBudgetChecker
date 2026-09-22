"""全局材料搜索的纯函数与组合逻辑单测（WP2-C）。

覆盖的是"不需要数据库就能穷举"的那一层：查询解析、通配符转义、WHERE/ORDER 的
参数化形态、关系解析（本部集合）、命中归因、命中版本/任务的确定性选择、
以及"当前运行复用 canonical helper"。真库语义与排序名次见
``tests/test_material_search_pg.py``；接口契约与越权见 ``tests/test_material_search_api.py``。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.services.material_search_query_service import (
    MAX_JOB_UUID_LENGTH,
    MaterialSearchFilters,
    MaterialSearchQuery,
    MaterialSearchQueryError,
    MaterialSearchQueryService,
    _ParamBag,
    build_search_item,
    build_search_order,
    build_search_where,
    escape_like,
    group_page_details,
    head_unit_subject_ids,
    ilike_literal,
    job_access_payload,
    matched_field_codes,
    parse_search_query,
    select_matched_job,
    select_matched_version,
)
from support_material_search_db import (
    FakeSearchConnection,
    make_job_record,
    make_slot_row,
    make_version_record,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=10)
LONG_AGO = NOW - timedelta(days=400)


# ---- 查询解析 ---------------------------------------------------------------


def test_parse_target_query():
    """§十三 的目标查询：年度/文种/关系被消费掉，剩下的才是文本 token。"""
    query = parse_search_query("规划和自然资源局 本部 2024 决算")
    assert query.fiscal_year == 2024
    assert query.report_kind == "final"
    assert query.relationship == "head_unit"
    assert query.text_tokens == ("规划和自然资源局",)


@pytest.mark.parametrize(
    "raw,expected_kind",
    [
        ("预算", "budget"),
        ("预算报告", "budget"),
        ("BUDGET", "budget"),
        ("决算", "final"),
        ("决算报告", "final"),
        ("FINAL", "final"),
    ],
)
def test_report_kind_aliases(raw, expected_kind):
    """§十五 的文种别名（大小写不敏感）。"""
    assert parse_search_query(f"某单位 {raw}").report_kind == expected_kind


def test_year_token_requires_four_explicit_digits():
    """只有明确 4 位年份才是财政年度：其它数字形态按普通文本处理（§十四）。"""
    assert parse_search_query("2024").fiscal_year == 2024
    assert parse_search_query("1899").fiscal_year is None
    assert parse_search_query("20245").fiscal_year is None
    assert parse_search_query("2024年").fiscal_year is None
    assert parse_search_query("1899").text_tokens == ("1899",)


def test_full_width_space_is_a_separator():
    assert parse_search_query("普陀\u3000财政局").text_tokens == ("普陀", "财政局")


def test_text_tokens_are_deduplicated_case_insensitively():
    query = parse_search_query("putuo Putuo PUTUO 普陀")
    assert query.text_tokens == ("putuo", "普陀")


def test_conflicting_years_are_rejected():
    with pytest.raises(MaterialSearchQueryError):
        parse_search_query("2023 2024 决算")


def test_conflicting_report_kinds_are_rejected():
    with pytest.raises(MaterialSearchQueryError):
        parse_search_query("预算 决算")


def test_too_many_tokens_are_rejected():
    """token 过多直接拒绝：静默丢弃会改变 AND 语义。"""
    with pytest.raises(MaterialSearchQueryError):
        parse_search_query(" ".join(f"词{index}" for index in range(9)))


def test_relationship_aliases():
    for raw in ("本部", "本级", "head_unit"):
        assert parse_search_query(f"财政局 {raw}").relationship == "head_unit"
    assert parse_search_query("财政局 直属").relationship is None
    assert parse_search_query("财政局 直属").text_tokens == ("财政局", "直属")


# ---- 匹配语义：转义与子串 ---------------------------------------------------


def test_escape_like_treats_wildcards_as_literals():
    """§二十：``%`` ``_`` 反斜杠都按普通字符处理。"""
    assert escape_like("100%") == "%100\\%%"
    assert escape_like("a_b") == "%a\\_b%"
    assert escape_like("x\\y") == "%x\\\\y%"
    # 顺序很重要：反斜杠先转义，否则用户输入的 \% 会绕过转义。
    assert escape_like("\\%") == "%\\\\\\%%"


@pytest.mark.parametrize(
    "field,token,expected",
    [
        ("上海市普陀区", "普陀", True),
        ("上海市普陀区", "静安", False),
        ("Plan PDF.PDF", "pdf.pdf", True),
        (None, "普陀", False),
        ("某单位", "", False),
    ],
)
def test_ilike_literal_mirrors_sql_semantics(field, token, expected):
    assert ilike_literal(field, token) is expected


# ---- WHERE / ORDER 的参数化形态 --------------------------------------------


def test_where_uses_escaped_pattern_for_every_field():
    """一个 token 生成一个模式参数，被四个字段共用；参数值已转义。"""
    query = parse_search_query("100%")
    where, params = build_search_where(query, filters=MaterialSearchFilters())
    # 参数顺序：token 的 ILIKE 模式、token 的原始文本（job uuid 等值比较用）。
    assert params == ["%100\\%%", "100%"]
    assert where.count("ILIKE $1") == 4
    assert where.count("ESCAPE '\\'") == 4


def test_where_has_no_permission_clause_for_admin():
    query = parse_search_query("普陀")
    where, params = build_search_where(query, filters=MaterialSearchFilters())
    assert "jurisdiction_org_id = ANY" not in where
    assert len(params) == 2  # 模式参数 + job uuid 参数


def test_where_permission_clause_covers_three_columns_or():
    query = parse_search_query("普陀")
    where, params = build_search_where(
        query, filters=MaterialSearchFilters(visible_org_ids=["org-a"])
    )
    assert params[0] == ["org-a"]
    assert (
        "s.jurisdiction_org_id = ANY($1::text[])"
        " OR s.department_org_id = ANY($1::text[])"
        " OR s.subject_org_id = ANY($1::text[])" in where
    )


def test_where_empty_visible_scope_matches_nothing_by_construction():
    """空可见范围是**空数组**（不是 NULL）：``ANY('{}')`` 命中不了任何行。"""
    query = parse_search_query("普陀")
    where, params = build_search_where(
        query, filters=MaterialSearchFilters(visible_org_ids=[])
    )
    assert params[0] == []


def test_where_skips_job_subquery_for_overlong_tokens():
    """超过 ``VARCHAR(36)`` 的 token 不可能等于任何 job_uuid，因此不生成子查询。"""
    long_token = "x" * (MAX_JOB_UUID_LENGTH + 1)
    query = parse_search_query(long_token)
    where, params = build_search_where(query, filters=MaterialSearchFilters())
    assert "analysis_jobs tj" not in where
    assert params == [escape_like(long_token)]

    short_query = parse_search_query("job-1")
    short_where, short_params = build_search_where(
        short_query, filters=MaterialSearchFilters()
    )
    assert "analysis_jobs tj" in short_where
    assert short_params == ["%job-1%", "job-1"]


def test_where_structured_conditions_come_from_query_not_filters():
    """年度/文种/关系只由 query 决定（filters 里根本没有这三个字段）。"""
    query = parse_search_query("本部 2024 决算")
    where, params = build_search_where(
        query, filters=MaterialSearchFilters(head_unit_org_ids=["u1"])
    )
    assert params == [2024, "final", ["u1"]]
    assert "s.fiscal_year = $1" in where
    assert "s.report_kind = $2" in where
    assert "s.subject_org_id = ANY($3::text[])" in where


def test_where_head_unit_without_ids_matches_nothing():
    """目录不可用 -> 空 id 集合 -> 关系命中不了任何槽位（fail-closed）。"""
    query = parse_search_query("财政局 本部")
    where, params = build_search_where(
        query, filters=MaterialSearchFilters(head_unit_org_ids=[])
    )
    assert params[0] == []
    assert "s.subject_org_id = ANY($1::text[])" in where


def test_order_by_priority_columns():
    """排序：job 精确 > 文件名精确 > 单位名精确 > 部门名精确 > 更新时间 > id。"""
    query = parse_search_query("普陀 财政局")
    bag = _ParamBag()
    order = build_search_order(query, bag)
    assert order.count("CASE WHEN") == 4
    assert order.index("analysis_jobs oj") < order.index("fiscal_document_versions fv")
    assert order.index("fiscal_document_versions fv") < order.index("s.subject_org_name")
    assert order.index("s.subject_org_name") < order.index("s.department_name")
    assert order.endswith("s.updated_at DESC NULLS LAST, s.id")
    # token 数组与 job token 数组各占一个参数。
    assert bag.params == [["普陀", "财政局"], ["普陀", "财政局"]]


def test_order_without_tokens_has_no_exactness_columns():
    query = parse_search_query("2024 决算")
    bag = _ParamBag()
    assert build_search_order(query, bag) == "s.updated_at DESC NULLS LAST, s.id"
    assert bag.params == []


def test_order_job_tokens_exclude_overlong_tokens():
    long_token = "y" * (MAX_JOB_UUID_LENGTH + 1)
    query = parse_search_query(f"{long_token} 普陀")
    bag = _ParamBag()
    build_search_order(query, bag)
    assert bag.params[0] == [long_token, "普陀"]
    assert bag.params[1] == ["普陀"]


# ---- 关系解析：本部集合 -----------------------------------------------------


class _Org:
    def __init__(self, org_id, name, level, parent_id=None):
        self.id = org_id
        self.name = name
        self.level = level
        self.parent_id = parent_id


def test_head_unit_ids_cover_same_name_marker_and_orphan():
    """三条判定：同名本级 = 本部；带标志 = 本部；孤立单位只认标志（不猜层级）。"""
    records = [
        _Org("district", "上海市普陀区", "district"),
        _Org("dept", "上海市普陀区财政局", "department", "district"),
        _Org("head-by-name", "上海市普陀区财政局", "unit", "dept"),
        _Org("head-by-marker", "上海市普陀区财政局（本级）", "unit", "dept"),
        _Org("subordinate", "上海市普陀区财政局监督所", "unit", "dept"),
        _Org("orphan", "某孤立单位", "unit", None),
        _Org("dept-itself", "上海市普陀区教育局", "department", "district"),
    ]
    assert head_unit_subject_ids(records) == ["head-by-marker", "head-by-name"]


def test_head_unit_ids_never_include_departments():
    """部门自己是 department_summary，不是本部单位。"""
    records = [
        _Org("dept", "上海市普陀区财政局", "department", "district"),
        _Org("district", "上海市普陀区", "district"),
    ]
    assert head_unit_subject_ids(records) == []


def test_head_unit_ids_are_sorted_and_deterministic():
    records = [
        _Org("d", "部门", "department", None),
        _Org("u2", "部门（本部）", "unit", "d"),
        _Org("u1", "部门（本级）", "unit", "d"),
    ]
    assert head_unit_subject_ids(records) == ["u1", "u2"]


# ---- 明细归并 --------------------------------------------------------------


def _detail_row(
    *,
    slot_id: str,
    version_id,
    filename=None,
    job_uuid=None,
    job_id=None,
    metadata=None,
    status="done",
    completed_at=None,
) -> dict:
    return {
        "slot_id": slot_id,
        "current_document_version_id": None,
        "document_version_id": version_id,
        "original_filename": filename,
        "version_created_at": EARLIER,
        "id": job_id,
        "job_uuid": job_uuid,
        "status": status,
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        "completed_at": completed_at or NOW,
    }


def test_group_page_details_deduplicates_versions_across_runs():
    """一个版本跑过两次 -> 版本只出现一次，运行出现两次。"""
    rows = [
        _detail_row(slot_id="s1", version_id=1, filename="a.pdf", job_uuid="job-1", job_id=1),
        _detail_row(slot_id="s1", version_id=1, filename="a.pdf", job_uuid="job-2", job_id=2),
        _detail_row(slot_id="s1", version_id=2, filename="b.pdf"),
    ]
    grouped = group_page_details(rows)
    assert [row["document_version_id"] for row in grouped["s1"]["versions"]] == [1, 2]
    assert len(grouped["s1"]["runs"]) == 2


def test_group_page_details_skips_unknown_slots():
    assert group_page_details([_detail_row(slot_id="", version_id=1)]) == {}


# ---- 命中版本 / 命中任务的选择 ---------------------------------------------


def test_select_matched_version_prefers_current_then_newest():
    versions = [
        {"document_version_id": 1, "original_filename": "final.pdf", "version_created_at": LONG_AGO},
        {"document_version_id": 2, "original_filename": "final.pdf", "version_created_at": EARLIER},
    ]
    # 两个都含 token：优先当前版本（2）。
    assert select_matched_version(versions, tokens=["final"], current_pointer=2)["document_version_id"] == 2
    # 当前版本是 1：即使 2 更新，也先给当前版本。
    assert select_matched_version(versions, tokens=["final"], current_pointer=1)["document_version_id"] == 1
    # 没有当前指针：按创建时间新的优先。
    assert select_matched_version(versions, tokens=["final"], current_pointer=None)["document_version_id"] == 2


def test_select_matched_version_returns_none_without_token_match():
    versions = [{"document_version_id": 1, "original_filename": "final.pdf"}]
    assert select_matched_version(versions, tokens=["预算"], current_pointer=1) is None


def test_select_matched_job_is_exact_and_newest():
    runs = [
        {"id": 1, "job_uuid": "job-a"},
        {"id": 2, "job_uuid": "job-b"},
        {"id": 3, "job_uuid": "job-a"},
    ]
    assert select_matched_job(runs, tokens=["job-a"])["id"] == 3
    assert select_matched_job(runs, tokens=["job-a-lookalike"]) is None
    # 大小写敏感（与 SQL 的等值比较一致）。
    assert select_matched_job(runs, tokens=["JOB-A"]) is None


# ---- 命中归因 --------------------------------------------------------------


def _query(raw: str) -> MaterialSearchQuery:
    return parse_search_query(raw)


def test_matched_field_codes_for_department_year_kind():
    row = make_slot_row(
        slot_id="s1",
        jurisdiction_name="上海市普陀区",
        department_name="上海市普陀区财政局",
        subject_org_name="上海市普陀区财政局（本级）",
        current_document_version_id=9,
    )
    codes = matched_field_codes(
        query=_query("财政局 本部 2024 决算"),
        row=row,
        matched_version=None,
        matched_job=None,
        detail={},
    )
    assert codes == ["unit", "department", "fiscal_year", "report_kind", "relationship"]


def test_matched_field_codes_distinguish_current_and_historical_filename():
    row = make_slot_row(slot_id="s1", current_document_version_id=9)
    version = {"document_version_id": 8, "original_filename": "old.pdf"}
    codes = matched_field_codes(
        query=_query("old.pdf"), row=row, matched_version=version, matched_job=None, detail={}
    )
    assert codes == ["historical_filename"]

    current_version = {"document_version_id": 9, "original_filename": "old.pdf"}
    codes = matched_field_codes(
        query=_query("old.pdf"),
        row=row,
        matched_version=current_version,
        matched_job=None,
        detail={},
    )
    assert codes == ["current_filename"]


def test_matched_field_codes_only_report_fields_that_actually_matched():
    """没命中的字段不出现（避免"看起来毫无关系"或"看起来哪都命中了"）。"""
    row = make_slot_row(
        slot_id="s1",
        jurisdiction_name="上海市静安区",
        department_name="上海市静安区教育局",
        subject_org_name="某小学",
    )
    codes = matched_field_codes(
        query=_query("普陀"), row=row, matched_version=None, matched_job=None, detail={}
    )
    assert codes == []


# ---- 结果组装（含 review candidate） ---------------------------------------


def _item(slot_row, detail_rows, raw_query, *, job_access=None):
    return build_search_item(
        slot_row,
        query=_query(raw_query),
        detail=group_page_details(detail_rows).get(str(slot_row["slot_id"]), {}),
        job_access=job_access,
    )


def test_build_search_item_without_current_version_has_no_filename_and_no_candidate():
    slot = make_slot_row(slot_id="s1", current_document_version_id=None)
    rows = [_detail_row(slot_id="s1", version_id=1, filename="old.pdf")]
    item = _item(slot, rows, "old.pdf", job_access=lambda payload: True)
    assert item.matched_filename == "old.pdf"
    assert item.matched_version_is_current is False
    assert item.current_filename is None
    assert item.review_candidate is None


def test_build_search_item_review_candidate_uses_current_run_helper():
    """同一当前版本两次运行：候选取"当前运行"（与处理详情同一判定）。"""
    slot = make_slot_row(slot_id="s1", current_document_version_id=5)
    metadata = {"structured_ingest": {"document_version_id": 5}, "organization_id": "org-a"}
    rows = [
        _detail_row(
            slot_id="s1",
            version_id=5,
            filename="final.pdf",
            job_uuid="job-old",
            job_id=1,
            metadata=metadata,
            completed_at=LONG_AGO,
        ),
        _detail_row(
            slot_id="s1",
            version_id=5,
            filename="final.pdf",
            job_uuid="job-new",
            job_id=2,
            metadata=metadata,
            completed_at=NOW,
        ),
    ]
    item = _item(slot, rows, "job-old", job_access=lambda payload: True)
    assert item.matched_job_uuid == "job-old"
    assert item.review_candidate is not None
    assert item.review_candidate.job_uuid == "job-new"


def test_build_search_item_candidate_requires_job_access():
    slot = make_slot_row(slot_id="s1", current_document_version_id=5)
    rows = [
        _detail_row(
            slot_id="s1",
            version_id=5,
            filename="final.pdf",
            job_uuid="job-x",
            job_id=1,
            metadata={"structured_ingest": {"document_version_id": 5}},
        )
    ]
    assert _item(slot, rows, "final.pdf", job_access=None).review_candidate is None
    assert _item(slot, rows, "final.pdf", job_access=lambda payload: False).review_candidate is None
    assert (
        _item(slot, rows, "final.pdf", job_access=lambda payload: True).review_candidate.job_uuid
        == "job-x"
    )


def test_build_search_item_never_looks_at_history_for_current_filename():
    """指针指向的版本行不在明细里时，current_filename 为 null（不挑历史版本顶上）。"""
    slot = make_slot_row(slot_id="s1", current_document_version_id=99)
    rows = [_detail_row(slot_id="s1", version_id=1, filename="old.pdf")]
    item = _item(slot, rows, "old.pdf")
    assert item.current_filename is None


def test_job_access_payload_uses_database_metadata_only():
    payload = job_access_payload(
        {
            "job_uuid": "job-1",
            "metadata": json.dumps({"organization_id": "org-a"}),
        }
    )
    assert payload == {"job_id": "job-1", "organization_id": "org-a", "created_by": None}


def test_job_access_payload_tolerates_broken_metadata():
    assert job_access_payload({"job_uuid": "job-1", "metadata": "not-json"}) == {
        "job_id": "job-1",
        "organization_id": None,
        "created_by": None,
    }


# ---- 服务：语句数量与 pagination -------------------------------------------


async def test_service_runs_two_statements_for_a_normal_page():
    fake = FakeSearchConnection(
        slots=[
            make_slot_row(slot_id="s1", subject_org_name="上海市普陀区财政局", current_document_version_id=1),
            make_slot_row(slot_id="s2", subject_org_name="上海市普陀区财政局（本级）"),
        ],
        versions=[make_version_record(document_version_id=1, slot_id="s1", original_filename="a.pdf")],
        jobs=[],
    )
    service = MaterialSearchQueryService(fake)
    items, total = await service.search(
        query=parse_search_query("财政局"),
        filters=MaterialSearchFilters(),
        page=1,
        page_size=20,
    )
    assert total == 2
    assert {item.slot_id for item in items} == {"s1", "s2"}
    assert len(fake.statements) == 2


async def test_service_applies_pagination_parameters():
    fake = FakeSearchConnection(
        slots=[
            make_slot_row(slot_id=f"s{index}", subject_org_name=f"某单位{index}")
            for index in range(5)
        ]
    )
    service = MaterialSearchQueryService(fake)
    items, total = await service.search(
        query=parse_search_query("某单位"),
        filters=MaterialSearchFilters(),
        page=2,
        page_size=2,
    )
    assert total == 5
    assert [item.slot_id for item in items] == ["s2", "s3"]
    assert fake.statements[0][1][-2:] == [2, 2]


def test_parse_returns_frozen_dataclass_with_original_raw():
    query = parse_search_query("  财政局 本部  ")
    assert query.raw == "财政局 本部"
    assert isinstance(query, MaterialSearchQuery)
