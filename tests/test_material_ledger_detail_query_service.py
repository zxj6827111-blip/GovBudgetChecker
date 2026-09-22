"""材料详情查询服务的纯逻辑测试（WP2-B）。

覆盖范围
--------
本文件只测**纯函数**与**用假连接驱动的服务方法**，不启动 FastAPI：
单位时间轴的年度分组与排序、未知年度的去处、多槽位不丢、当前分析的选择与
不可用语义、正式/人工/信息三桶划分、检查覆盖的可用性判定。

真正的"接口契约 + 鉴权 + IDOR"在 ``test_material_ledger_detail_api.py``；
真库才有的行为（表达式索引、JSONB 存储形态、跨表 JOIN 的 NULL 语义）在
``test_material_ledger_detail_pg.py``。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("TESTING", "true")

from src.services.evidence_guard import EVIDENCE_STATUS_DEGRADED
from src.services.material_detail_query_service import (
    MaterialDetailQueryService,
    build_coverage,
    collect_legacy_metadata_warnings,
    partition_findings,
    run_sort_key,
    select_current_run,
    severity_bucket,
    structured_document_version_id,
    version_items,
)
from support_material_detail_db import (
    FakeDetailConnection,
    make_job_row,
    make_result_row,
    make_slot_row,
    make_source_row,
    make_version_row,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=10)

UNIT_ID = "unit-A1"
OTHER_UNIT_ID = "unit-A2"
DEPT_ID = "dept-A"
DISTRICT_ID = "district-A"


def _slot(**overrides):
    """单位时间轴用的一条槽位行（默认落在 UNIT_ID 上）。"""
    base = {
        "subject_org_id": UNIT_ID,
        "subject_org_name": "某单位",
        "department_org_id": DEPT_ID,
        "department_name": "某局",
        "jurisdiction_org_id": DISTRICT_ID,
        "jurisdiction_name": "某区",
        "updated_at": NOW,
    }
    base.update(overrides)
    return make_slot_row(**base)


# ==== 1-6：单位时间轴的年度口径 =============================================


def test_timeline_groups_by_fiscal_year_descending():
    """① 按 fiscal_year 排列，且是降序。"""
    data = MaterialDetailQueryService.aggregate_unit_timeline(
        [
            _slot(id="s-2023", fiscal_year=2023, report_kind="budget"),
            _slot(id="s-2026", fiscal_year=2026, report_kind="budget"),
            _slot(id="s-2024", fiscal_year=2024, report_kind="budget"),
            _slot(id="s-2025", fiscal_year=2025, report_kind="budget"),
        ],
        unit_id=UNIT_ID,
    )
    assert [row.fiscal_year for row in data.years] == [2026, 2025, 2024, 2023]


def test_published_at_of_source_never_shifts_fiscal_year():
    """② 网页发布日期不参与年度归类。

    形态取自真实场景：2024 年度决算在 2025-08 发布。时间轴必须把它放在 2024，
    而 ``material_sources.published_at`` 只能是"来源信息"，不能改年度。
    页面同时显示两者是**正确**的；用发布时间覆盖财政年度才是缺陷。
    """
    slot_row = _slot(
        id="slot-final-2024",
        fiscal_year=2024,
        report_kind="final",
        current_document_version_id=None,
    )
    service = MaterialDetailQueryService(
        FakeDetailConnection(
            slots=[slot_row],
            sources=[
                make_source_row(
                    slot_id="slot-final-2024",
                    published_at=datetime(2025, 8, 20, tzinfo=timezone.utc),
                    discovered_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
                )
            ],
        )
    )
    data = _run(service.unit_timeline(unit_id=UNIT_ID))
    assert [row.fiscal_year for row in data.years] == [2024]
    assert data.years[0].final_slots[0].fiscal_year == 2024

    loaded = _run(service.load_slot_row("slot-final-2024"))
    detail = _run(service.slot_detail(loaded))
    assert detail.sources[0].published_at == datetime(2025, 8, 20, tzinfo=timezone.utc)
    # 两条信息各自独立：财政年度仍是 2024，没有被发布时间"顶"成 2025。
    assert detail.slot.fiscal_year == 2024


def test_timeline_keeps_budget_final_and_unknown_kind_apart():
    """③④ 预算/决算不串位；文种未识别的槽位不消失。"""
    data = MaterialDetailQueryService.aggregate_unit_timeline(
        [
            _slot(id="s-budget", fiscal_year=2025, report_kind="budget"),
            _slot(id="s-final", fiscal_year=2025, report_kind="final"),
            _slot(id="s-unknown", fiscal_year=2025, report_kind="unknown"),
        ],
        unit_id=UNIT_ID,
    )
    row = data.years[0]
    assert [item.slot_id for item in row.budget_slots] == ["s-budget"]
    assert [item.slot_id for item in row.final_slots] == ["s-final"]
    assert [item.slot_id for item in row.unclassified_slots] == ["s-unknown"]
    # 三条槽位一条都没丢
    assert (
        len(row.budget_slots) + len(row.final_slots) + len(row.unclassified_slots) == 3
    )


def test_timeline_never_invents_a_year_for_unknown_fiscal_year():
    """⑤ fiscal_year IS NULL 不兜底成 2000 / 0 / 当前年份。"""
    data = MaterialDetailQueryService.aggregate_unit_timeline(
        [
            _slot(id="s-known", fiscal_year=2025, report_kind="budget"),
            _slot(id="s-year-unknown", fiscal_year=None, report_kind="budget"),
        ],
        unit_id=UNIT_ID,
    )
    assert [row.fiscal_year for row in data.years] == [2025]
    assert [item.slot_id for item in data.unresolved_year_slots] == ["s-year-unknown"]
    # 任何一个具体年份里都不能出现它
    for row in data.years:
        for bucket in (row.budget_slots, row.final_slots, row.unclassified_slots):
            assert all(item.slot_id != "s-year-unknown" for item in bucket)
    # 未知年度的槽位只出现在 unresolved_year_slots（上面已断言），
    # 且该列表里没有别的槽位混进来。
    assert len(data.unresolved_year_slots) == 1
    # 尤其不能出现被兜底出来的年份
    assert 2000 not in [row.fiscal_year for row in data.years]
    assert NOW.year not in [row.fiscal_year for row in data.years]


def test_timeline_keeps_every_slot_when_one_year_kind_has_several():
    """⑥ 同（年度 × 文种）多条槽位一条都不能静默吃掉。"""
    data = MaterialDetailQueryService.aggregate_unit_timeline(
        [
            _slot(
                id="s-confirmed",
                fiscal_year=2025,
                report_kind="final",
                mapping_key="",
                updated_at=EARLIER,
            ),
            _slot(
                id="s-placeholder",
                fiscal_year=2025,
                report_kind="final",
                mapping_key="sha256:deadbeef",
                status="mapping_required",
                status_reason="identity_unresolved",
                updated_at=NOW,
            ),
        ],
        unit_id=UNIT_ID,
    )
    row = data.years[0]
    assert len(row.final_slots) == 2
    # 身份已确认的排前面，占位槽位不顶掉它（与 WP2-A 的 _slot_preference 同取向）
    assert [item.slot_id for item in row.final_slots] == ["s-confirmed", "s-placeholder"]


def test_timeline_rejects_slots_belonging_to_another_subject():
    """身份只按 subject_org_id：混入别的单位必须显式失败，而不是静默展示。"""
    with pytest.raises(ValueError):
        MaterialDetailQueryService.aggregate_unit_timeline(
            [_slot(id="s-other", subject_org_id=OTHER_UNIT_ID, fiscal_year=2025)],
            unit_id=UNIT_ID,
        )


def test_timeline_sql_filters_by_subject_id_not_name():
    """§十三：按 id 查询，绝不按单位名称。"""
    conn = FakeDetailConnection(
        slots=[
            _slot(id="s-mine", subject_org_id=UNIT_ID, subject_org_name="某单位"),
            _slot(
                id="s-namesake",
                subject_org_id=OTHER_UNIT_ID,
                subject_org_name="某单位",  # 完全同名
                fiscal_year=2025,
                report_kind="final",
            ),
        ]
    )
    data = _run(MaterialDetailQueryService(conn).unit_timeline(unit_id=UNIT_ID))
    ids = [item.slot_id for row in data.years for item in row.budget_slots + row.final_slots]
    assert ids == ["s-mine"]
    assert "subject_org_id = $1" in conn.calls[0][0]
    assert conn.calls[0][1][0] == UNIT_ID


# ==== 7-11：当前版本与当前分析 ===============================================


def _detail_conn(**overrides):
    """一份"V1 有分析、V2 是当前版本且没有分析"的默认真连接替身。

    返回 ``(service, conn)``：断言走 service（被测对象），SQL 计数走 conn。
    """
    kwargs = {
        "slots": [
            make_slot_row(
                id="slot-1",
                subject_org_id=UNIT_ID,
                current_document_version_id=22,
                fiscal_year=2024,
                report_kind="final",
            )
        ],
        "sources": [make_source_row()],
        "versions": [
            make_version_row(document_version_id=11, slot_id="slot-1", file_hash="a" * 64),
            make_version_row(
                document_version_id=22,
                slot_id="slot-1",
                file_hash="b" * 64,
                version_created_at=NOW,
            ),
        ],
        "jobs": [
            make_job_row(
                id=501,
                job_uuid="job-v1",
                metadata={"structured_ingest": {"document_version_id": 11}},
            )
        ],
        "results": [
            make_result_row(
                job_id=501,
                ai_findings=[{"id": "f1", "severity": "high", "title": "V1 的问题"}],
                rule_findings=[],
            )
        ],
    }
    kwargs.update(overrides)
    conn = FakeDetailConnection(**kwargs)
    return MaterialDetailQueryService(conn), conn


def test_slot_detail_current_version_comes_from_slot_pointer_only():
    """⑦ 当前版本真值只有 ``current_document_version_id``。"""
    service, conn = _detail_conn()
    slot = _run(service.load_slot_row("slot-1"))
    detail = _run(service.slot_detail(slot))
    assert detail.current_version is not None
    assert detail.current_version.document_version_id == 22
    assert detail.current_version.is_current is True
    assert detail.version_total == 2
    assert detail.historical_version_count == 1


def test_slot_detail_does_not_guess_current_version_when_pointer_is_null():
    """⑧ 指针为 NULL 时不从历史版本里挑一份冒充当前版本。"""
    service, conn = _detail_conn(
        slots=[make_slot_row(id="slot-1", current_document_version_id=None)],
    )
    slot = _run(service.load_slot_row("slot-1"))
    detail = _run(service.slot_detail(slot))
    assert detail.current_version is None
    assert detail.version_total == 2
    assert detail.historical_version_count == 2
    assert detail.current_analysis.available is False
    assert detail.current_analysis.reason == "no_current_document_version"
    assert detail.current_analysis.formal_issue_count is None


def test_historical_analysis_never_becomes_current_analysis():
    """⑨⑩⑬⑭ V1 有 3 个 finding、V2 是当前版本且没有分析。

    详情必须：两个版本都在；V2 是 current；V1 的分析**不**算当前结论。
    """
    service, conn = _detail_conn(
        results=[
            make_result_row(
                job_id=501,
                ai_findings=[
                    {"id": f"f{n}", "severity": "high", "title": f"V1 问题 {n}"}
                    for n in range(1, 4)
                ],
                rule_findings=[],
            )
        ]
    )
    slot = _run(service.load_slot_row("slot-1"))
    detail = _run(service.slot_detail(slot))

    versions = version_items(
        [
            {"document_version_id": 11, "created_at": EARLIER},
            {"document_version_id": 22, "created_at": NOW},
        ],
        current_document_version_id=22,
    )
    assert [(v.document_version_id, v.is_current) for v in versions] == [
        (22, True),
        (11, False),
    ]

    assert detail.current_analysis.available is False
    assert detail.current_analysis.reason == "no_persisted_analysis_for_current_version"
    # 关键断言：不能显示 3，也不能显示 0
    assert detail.current_analysis.formal_issue_count is None
    assert detail.current_analysis.formal_findings is None
    # V1 的运行仍然出现在处理记录里，并被标成历史版本
    runs = _run(service.run_history(slot))
    assert [(item.job_uuid, item.is_current_document_version) for item in runs] == [
        ("job-v1", False)
    ]


def test_current_analysis_requires_exact_version_match():
    """⑬ 同版本运行才算当前分析：V1 的 run 不会因为"最早/最新"被选中。

    这里 V2 也有运行但**没有**结果，V1 有结果 —— 当前分析必须是"不可用"，
    而不是回退到 V1 的结果（回退会把上一版文件的结论当成这一版没问题）。
    """
    service, conn = _detail_conn(
        jobs=[
            make_job_row(
                id=501,
                job_uuid="job-v1",
                metadata={"structured_ingest": {"document_version_id": 11}},
            ),
            make_job_row(
                id=502,
                job_uuid="job-v2",
                metadata={"structured_ingest": {"document_version_id": 22}},
            ),
        ],
        results=[
            make_result_row(job_id=501, ai_findings=[{"id": "f1", "severity": "high"}]),
        ],
    )
    slot = _run(service.load_slot_row("slot-1"))
    detail = _run(service.slot_detail(slot))
    assert detail.current_analysis.available is False
    assert detail.current_analysis.run is not None
    assert detail.current_analysis.run.job_uuid == "job-v2"
    assert detail.current_analysis.formal_issue_count is None


def test_current_analysis_zero_is_only_allowed_when_fully_computable():
    """⑰⑱ 只有"当前版本 → 精确运行 → 已落库结果 → 正式门禁"全成立才允许 0。"""
    service, conn = _detail_conn(
        jobs=[
            make_job_row(
                id=502,
                job_uuid="job-v2",
                metadata={
                    "structured_ingest": {"document_version_id": 22},
                    "result_meta": {
                        "quality_gate": {"status": "done", "quality_status": "complete"},
                        "obligation_coverage": {
                            "applicable_total": 42,
                            "completed_total": 42,
                            "not_applicable_total": 0,
                            "unresolved_total": 0,
                            "blocking_total": 0,
                            "coverage_rate": 1.0,
                        },
                    },
                },
            )
        ],
        results=[make_result_row(job_id=502, ai_findings=[], rule_findings=[])],
    )
    slot = _run(service.load_slot_row("slot-1"))
    detail = _run(service.slot_detail(slot))
    assert detail.current_analysis.available is True
    # 全链路成立 → 0 是"查过且确实没有正式问题"
    assert detail.current_analysis.formal_issue_count == 0
    assert detail.current_analysis.formal_findings == []
    assert detail.current_analysis.coverage.available is True


def test_formal_gate_excludes_degraded_from_formal_count():
    """⑰ 降级条目不算正式问题：正式门禁（is_formal_finding）是唯一判据。"""
    service, conn = _detail_conn(
        jobs=[
            make_job_row(
                id=502,
                metadata={"structured_ingest": {"document_version_id": 22}},
            )
        ],
        results=[
            make_result_row(
                job_id=502,
                ai_findings=[
                    {"id": "formal-1", "severity": "high", "title": "正式"},
                    {
                        "id": "degraded-1",
                        "severity": "manual_review",
                        "original_severity": "high",
                        "evidence_status": EVIDENCE_STATUS_DEGRADED,
                        "title": "缺证据被降级",
                    },
                ],
                rule_findings=[],
            )
        ],
    )
    slot = _run(service.load_slot_row("slot-1"))
    detail = _run(service.slot_detail(slot))
    assert detail.current_analysis.available is True
    assert detail.current_analysis.formal_issue_count == 1
    assert [item.finding_id for item in detail.current_analysis.formal_findings] == ["formal-1"]
    assert [
        item.finding_id for item in detail.current_analysis.manual_review_items
    ] == ["degraded-1"]


def test_current_run_selection_is_stable_across_calls():
    """⑮ 同版本多 run 的选择必须稳定且可复现（同一份数据任意顺序同一结果）。"""
    rows = [
        {
            "id": 3,
            "job_uuid": "job-c",
            "completed_at": NOW,
            "started_at": EARLIER,
            "created_at": EARLIER,
            "updated_at": NOW,
        },
        {
            "id": 1,
            "job_uuid": "job-a",
            "completed_at": NOW,
            "started_at": EARLIER,
            "created_at": EARLIER,
            "updated_at": NOW,
        },
        {
            "id": 2,
            "job_uuid": "job-b",
            "completed_at": EARLIER,
            "started_at": EARLIER,
            "created_at": EARLIER,
            "updated_at": EARLIER,
        },
    ]
    import random

    picks = set()
    for _ in range(20):
        shuffled = rows[:]
        random.shuffle(shuffled)
        picked = select_current_run(shuffled)
        picks.add(picked["job_uuid"])
    # ① 结果稳定 ② 同一时刻按 id 降序取 job-c
    assert picks == {"job-c"}
    assert run_sort_key(rows[0])[0] == run_sort_key(rows[1])[0]


def test_current_run_selection_prefers_started_when_newer_run_has_no_completion():
    """运行选择复用仓库既有口径（COALESCE(completed_at, started_at, ...)）。

    正在跑的新运行胜出：此时当前分析应显示"还没有结果"，
    **而不是**把上一次已完成运行的结论当成当前结论。
    """
    finished = {
        "id": 1,
        "job_uuid": "job-finished",
        "completed_at": EARLIER,
        "started_at": EARLIER - timedelta(hours=1),
        "created_at": EARLIER - timedelta(hours=1),
        "updated_at": EARLIER,
    }
    running = {
        "id": 2,
        "job_uuid": "job-running",
        "completed_at": None,
        "started_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    assert select_current_run([finished, running])["job_uuid"] == "job-running"


def test_legacy_run_without_document_version_id_is_never_attributed():
    """⑯ 没有 document_version_id 的 legacy 运行不出现在任何槽位下。"""
    service, conn = _detail_conn(
        jobs=[
            make_job_row(
                id=501,
                job_uuid="job-v1",
                metadata={"structured_ingest": {"document_version_id": 22}},
            ),
            make_job_row(
                id=900,
                job_uuid="job-legacy",
                metadata={"organization_name": "某单位", "fiscal_year": 2024},
                filename="2024年度单位决算.pdf",
            ),
            make_job_row(
                id=901,
                job_uuid="job-numeric-broken",
                metadata={"structured_ingest": {"document_version_id": "22-ish"}},
                filename="同名文件.pdf",
            ),
        ],
    )
    slot = _run(service.load_slot_row("slot-1"))
    runs = _run(service.run_history(slot))
    assert [item.job_uuid for item in runs] == ["job-v1"]
    # 写坏的 metadata 要被当成可观测信号，而不是静默丢弃
    warnings = collect_legacy_metadata_warnings(
        [
            {"job_uuid": "job-legacy", "metadata": {"organization_name": "某单位"}},
            {
                "job_uuid": "job-numeric-broken",
                "metadata": {"structured_ingest": {"document_version_id": "22-ish"}},
            },
        ]
    )
    assert warnings == [{"job_uuid": "job-numeric-broken", "raw_value": "22-ish"}]


@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({"structured_ingest": {"document_version_id": 11}}, 11),
        ({"structured_ingest": {"document_version_id": "11"}}, 11),
        ({"structured_ingest": {"document_version_id": 0}}, None),
        ({"structured_ingest": {"document_version_id": -3}}, None),
        ({"structured_ingest": {"document_version_id": None}}, None),
        ({"structured_ingest": {"document_version_id": ""}}, None),
        ({"structured_ingest": {}}, None),
        ({}, None),
        (None, None),
        ('{"structured_ingest": {"document_version_id": 7}}', 7),  # JSONB 以字符串返回
    ],
)
def test_structured_document_version_id_parsing(metadata, expected):
    """精确关联字段的取值语义（含 asyncpg 以字符串返回 JSONB 的形态）。"""
    assert structured_document_version_id(metadata) == expected


# ==== 19-20：检查覆盖 ========================================================


def test_coverage_unavailable_never_reports_zero_or_hundred_percent():
    """⑲ 没有覆盖记录时不得出现 0/0、0% 或 100%。"""
    for raw in (None, {}, [], "", "not json", {"applicable_total": 42}):
        block = build_coverage(raw)
        assert block.available is False
        assert block.summary is None
        assert block.items == []
        assert block.reason == "no_coverage_for_current_document_version"
        assert block.reason != "100%"


def test_coverage_keeps_raw_status_codes_for_the_presentation_layer():
    """⑳ 覆盖的状态码原样透出（中文映射属于展示层，后端不翻译）。

    同时验证 ``by_reason`` 与 ``instances`` 都不丢键：它们是"哪些检查没做完"
    的唯一出口，静默丢一项会让页面上的两种计数对不上。
    """
    block = build_coverage(
        {
            "catalog_version": "v3.3",
            "catalog_fingerprint": "fp-1",
            "applicable_total": 42,
            "completed_total": 26,
            "not_applicable_total": 1,
            "unresolved_total": 16,
            "blocking_total": 15,
            "coverage_rate": 0.619,
            "auto_completion_rate": 0.6429,
            "by_reason": {"not_implemented": 12, "insufficient_data": 3, "ai_not_run": 1},
            "by_group": [
                {
                    "group_id": "table_internal",
                    "group_title": "表内关系",
                    "applicable": 7,
                    "completed": 6,
                    "not_applicable": 0,
                    "unresolved": 1,
                }
            ],
            "instances": [
                {
                    "obligation_id": "OBL-T-004",
                    "group_id": "table_internal",
                    "group_title": "表内关系",
                    "title": "表内合计与分项一致",
                    "status": "insufficient_data",
                    "reason": "insufficient_data",
                    "reason_label": "取数不足",
                    "detail": "未能定位合计行",
                    "blocks_gate": True,
                    "requires_ai": False,
                    "input_gaps": ["table_total"],
                }
            ],
        }
    )
    assert block.available is True
    summary = block.summary
    assert summary is not None
    assert summary.applicable_total == 42
    assert summary.completed_total == 26
    assert summary.blocking_total == 15
    assert summary.by_reason == {
        "not_implemented": 12,
        "insufficient_data": 3,
        "ai_not_run": 1,
    }
    assert summary.by_group[0].group_title == "表内关系"
    assert block.items[0].status == "insufficient_data"
    assert block.items[0].blocks_gate is True
    assert block.items[0].reason_label == "取数不足"


def test_coverage_rate_is_none_when_nothing_is_applicable():
    """0 分母不得算成 100%：引擎给 None，API 不再二次计算。"""
    block = build_coverage(
        {
            "applicable_total": 0,
            "completed_total": 0,
            "not_applicable_total": 5,
            "unresolved_total": 0,
            "blocking_total": 0,
            "coverage_rate": None,
            "auto_completion_rate": None,
        }
    )
    assert block.available is True
    assert block.summary is not None
    assert block.summary.coverage_rate is None
    assert block.summary.auto_completion_rate is None


# ==== finding 三桶划分 ======================================================


def test_partition_findings_is_a_partition():
    """三个桶两两不交且并集完整 —— 分类之和对不上总数是最难查的一类缺陷。"""
    ai = [
        {"id": "a1", "severity": "high"},
        {"id": "a2", "severity": "info"},
        {"id": "a3", "severity": "manual_review", "evidence_status": EVIDENCE_STATUS_DEGRADED},
    ]
    rule = [
        {"id": "r1", "severity": "warn"},
        {"id": "r2", "severity": "critical"},
    ]
    formal, manual, info = partition_findings(ai, rule)
    ids = [item.finding_id for item in [*formal, *manual, *info]]
    assert sorted(ids) == ["a1", "a2", "a3", "r1", "r2"]
    assert len(ids) == len(set(ids))
    assert sorted(item.finding_id for item in manual) == ["a3"]
    assert sorted(item.finding_id for item in info) == ["a2"]
    assert sorted(item.finding_id for item in formal) == ["a1", "r1", "r2"]


def test_partition_findings_accepts_jsonb_as_string():
    """真库可能把 JSONB 以字符串返回，必须照样解析（否则页面显示"没有结果"）。"""
    import json

    formal, manual, info = partition_findings(
        json.dumps([{"id": "x1", "severity": "high"}]), "[]"
    )
    assert [item.finding_id for item in formal] == ["x1"]
    assert manual == [] and info == []


@pytest.mark.parametrize(
    "severity,bucket",
    [
        ("critical", "error"),
        ("high", "error"),
        ("error", "error"),
        ("warn", "warn"),
        ("medium", "warn"),
        ("low", "warn"),
        ("info", "info"),
        (None, "info"),
        ("something_new", "info"),
    ],
)
def test_severity_bucket_matches_pipeline_normalization(severity, bucket):
    assert severity_bucket(severity) == bucket


def test_finding_evidence_pointer_is_extracted_without_inventing_a_page():
    """证据位置缺失时如实为 None，不猜第 1 页。"""
    formal, _, _ = partition_findings(
        [
            {"id": "with-page", "severity": "high", "page_number": 15, "bbox": [1, 2, 3, 4]},
            {"id": "no-page", "severity": "high"},
            {
                "id": "evidence-list",
                "severity": "high",
                "evidence": [{"page": 28, "text": "凭证文本"}],
            },
        ],
        [],
    )
    by_id = {item.finding_id: item for item in formal}
    assert by_id["with-page"].evidence_page == 15
    assert by_id["with-page"].evidence_bbox == [1.0, 2.0, 3.0, 4.0]
    assert by_id["no-page"].evidence_page is None
    assert by_id["no-page"].evidence_bbox is None
    assert by_id["evidence-list"].evidence_page == 28
    assert by_id["evidence-list"].evidence_text == "凭证文本"


# ==== SQL 预算 ==============================================================


def test_slot_detail_uses_three_sql_statements():
    """§七十三：slot detail ≤ 5 条；这里实际 3 条（槽位行 / 来源 / 版本+运行）。

    槽位行查询由路由层做（它还要拿三个组织列判权限），因此端到端的
    明细 SQL 是 3 条。这里不清理调用记录：清掉就等于只数了后两条，
    数字看起来"更漂亮"但不再说明接口的真实成本。
    """
    service, conn = _detail_conn()
    slot = _run(service.load_slot_row("slot-1"))
    _run(service.slot_detail(slot))
    assert conn.sql_count == 3


def test_versions_and_runs_use_one_sql_statement_each():
    service, conn = _detail_conn()
    slot = _run(service.load_slot_row("slot-1"))
    conn.calls.clear()
    _run(service.versions(slot))
    assert conn.sql_count == 1
    conn.calls.clear()
    _run(service.run_history(slot))
    assert conn.sql_count == 1


def test_timeline_uses_one_sql_statement():
    conn = FakeDetailConnection(slots=[_slot(id="s1", fiscal_year=2025)])
    _run(MaterialDetailQueryService(conn).unit_timeline(unit_id=UNIT_ID))
    assert conn.sql_count == 1


def test_versions_include_unbound_analysis_versions():
    """没有关联运行的版本（刚上传、还没分析）必须照样出现在版本历史里。"""
    service, conn = _detail_conn(
        slots=[make_slot_row(id="slot-1", current_document_version_id=None)],
    )
    slot = _run(service.load_slot_row("slot-1"))
    versions = _run(service.versions(slot))
    assert [item.document_version_id for item in versions] == [22, 11]


def _run(coro):
    """在一处驱动协程。

    本文件的用例大多是纯断言（同步），只有"走一遍假连接"的少数几个需要
    事件循环，因此不给整份文件加 async 标记。
    """
    import asyncio

    return asyncio.run(coro)
