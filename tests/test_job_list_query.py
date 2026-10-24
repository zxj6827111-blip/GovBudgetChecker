"""任务列表服务端筛选/分页/汇总：口径与前端 adapter 一致，且不再让页面下载全量摘要。"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from api import job_list_query as q
from api import runtime


def job(job_id, **fields):
    return {"job_id": job_id, "filename": f"{job_id}.pdf", "status": "done", **fields}


@pytest.mark.parametrize("raw, expected", [
    ("review_required", "review_required"), ("needs_review", "review_required"),
    ("uploaded", "pending_analysis"), ("pending_analysis", "pending_analysis"),
    ("done", "completed"), ("degraded", "completed"),
    ("error", "failed"), ("cancelled", "failed"),
    ("processing", "analyzing"), (None, "analyzing"), ("", "analyzing"),
])
def test_status_normalization_matches_frontend(raw, expected):
    assert q.normalize_ui_task_status(raw) == expected


@pytest.mark.parametrize("fields, expected", [
    ({"review_issue_total": 3, "review_candidate_total": 2, "issue_total": 99}, 5),
    ({"review_issue_total": 3}, 3),
    # JS Number(null) === 0：merged 为 null 时就是 0，不回退到 issue_total。
    ({"merged_issue_total": None, "issue_total": 7}, 0),
    ({"issue_total": 7, "review_item_count": 2}, 9),
    ({"merged_issue_total": "4", "review_item_count": None}, 4),
    ({"merged_issue_total": "x", "issue_total": 6}, 6),
    ({}, 0),
])
def test_display_issue_total_matches_frontend(fields, expected):
    assert q.display_issue_total(job("a", **fields)) == expected


def test_filters_follow_queue_and_report_semantics():
    jobs = [
        job("a", organization_id="dept", organization_name="市教育局", report_year=2025, report_kind="budget",
            stage_progress={"phase": "quality_gate"}),
        job("b", status="processing", organization_id="unit", report_year=2024.0, report_kind="final",
            stage_progress={"phase": "pdf_parse"}),
        job("c", status="uploaded", report_year=None, report_kind=None),
        job("d", status="error", organization_id="other", report_year=0, report_kind="weird"),
    ]
    ids = lambda filters: [item["job_id"] for item in q.filter_jobs(jobs, filters)]
    assert ids(q.JobListFilters(keyword="  教育 ")) == ["a"]
    assert ids(q.JobListFilters(keyword="B.PDF")) == ["b"]
    assert ids(q.JobListFilters(status="analyzing")) == ["b"]
    assert ids(q.JobListFilters(status="pending_analysis")) == ["c"]
    assert ids(q.JobListFilters(year="2024")) == ["b"], "2024.0 按 JS 显示为 2024"
    assert ids(q.JobListFilters(year="unresolved")) == ["c"], "队列：只有缺失年份算未识别"
    assert ids(q.JobListFilters(year="unknown")) == ["c", "d"], "报告页：非正整数都归年度待确认"
    assert ids(q.JobListFilters(kind="unknown")) == ["c", "d"]
    assert ids(q.JobListFilters(stage="pdf_parse")) == ["b"]
    assert ids(q.JobListFilters(stage="unknown")) == ["c", "d"]
    assert ids(q.JobListFilters(organization_ids=frozenset({"dept", "unit"}))) == ["a", "b"]
    assert ids(q.JobListFilters(unassigned=True)) == ["c"]


def test_sort_is_stable_and_unknown_elapsed_sorts_last():
    jobs = [job("a", issue_total=1, elapsed_ms=None), job("b", issue_total=3, elapsed_ms=5),
            job("c", issue_total=1, elapsed_ms=-1), job("d", issue_total=3, elapsed_ms=9)]
    assert [item["job_id"] for item in q.sort_jobs(jobs, "issues_desc")] == ["b", "d", "a", "c"]
    assert [item["job_id"] for item in q.sort_jobs(jobs, "elapsed_desc")] == ["d", "b", "a", "c"]
    assert [item["job_id"] for item in q.sort_jobs(jobs, "updated_desc")] == ["a", "b", "c", "d"]


def test_paginate_clamps_page_and_reports_zero_pages_for_empty_results():
    assert q.paginate(list(range(45)), 9, 20) == (list(range(40, 45)), 3, 3)
    assert q.paginate([], 3, 20) == ([], 1, 0)


def test_page_view_returns_one_page_plus_whole_list_summaries():
    jobs = [job(f"j{i}", status="processing" if i < 2 else "done", organization_id="dept" if i % 2 else "unit",
                report_year=2025 if i % 3 else None) for i in range(25)]
    jobs.append(job("waiting", status="uploaded"))
    view = q.build_page_view(
        jobs, q.JobListFilters(organization_ids=frozenset({"dept"})), sort_key="updated_desc", page=2, page_size=5,
        include_runnable=True, include_organization_counts=True,
    )
    assert view["total"] == 12 and view["page"] == 2 and view["page_count"] == 3
    assert [item["job_id"] for item in view["items"]] == ["j11", "j13", "j15", "j17", "j19"]
    assert view["years"] == [2025] and view["has_unknown_year"] is True
    # 计数、轮询判定与组织统计覆盖全部可见任务，不随筛选和分页变化。
    assert view["job_total"] == 26
    assert view["status_counts"]["analyzing"] == 2 and view["status_counts"]["pending_analysis"] == 1
    assert view["activity"] == {"running": 2, "waiting": 1}
    assert view["organization_report_counts"] == {"dept": 12, "unit": 13}
    assert {item["job_id"] for item in view["runnable"]} == {f"j{i}" for i in range(3, 25, 2)}
    assert set(view["runnable"][0]) == {"job_id", "filename", "status"}


def test_counts_view_is_small():
    view = q.build_counts_view([job("a"), job("b", status="processing"), job("c", status="uploaded")])
    assert view == {
        "total": 3,
        "status_counts": {"analyzing": 1, "review_required": 0, "pending_analysis": 1, "failed": 0, "completed": 1},
        "activity": {"running": 1, "waiting": 1},
    }


def test_descendants_follow_parent_links_without_looping():
    orgs = [SimpleNamespace(id="city", parent_id=None), SimpleNamespace(id="dept", parent_id="city"),
            SimpleNamespace(id="unit", parent_id="dept"), SimpleNamespace(id="loop", parent_id="loop"),
            SimpleNamespace(id="other", parent_id=None)]
    assert q.descendant_organization_ids(orgs, "dept") == frozenset({"dept", "unit"})
    assert q.descendant_organization_ids(orgs, "city") == frozenset({"city", "dept", "unit"})


def test_history_scope_and_low_coverage_filter():
    jobs = [job("done", page_coverage=0.5), job("review", status="review_required", page_coverage=0.95),
            job("failed", status="error"), job("running", status="processing", page_coverage=0.1),
            job("text", page_coverage="0.2"), job("bad", page_coverage=-0.1)]
    finished = q.filter_jobs(jobs, q.JobListFilters(finished_only=True))
    assert [item["job_id"] for item in finished] == ["done", "review", "failed", "text", "bad"]
    # lowCoverageJobs：只认确有数字且在 [0, 阈值) 的任务；缺字段、字符串、负数都不算覆盖率不足。
    low = q.filter_jobs(jobs, q.JobListFilters(coverage_below=0.8))
    assert [item["job_id"] for item in low] == ["done", "running"]
    assert q.parse_ratio("0.8", None) == 0.8
    assert q.parse_ratio("0", None) is None and q.parse_ratio("1.5", 0.8) == 0.8 and q.parse_ratio("nan", None) is None


def test_overview_matches_workbench_and_quality_semantics():
    jobs = [
        job("a", page_coverage=0.9, organization_id="u1"),
        job("b", status="review_required", page_coverage=1.1, organization_id=" "),
        job("c", page_coverage=0.5),
        job("d", status="error", stage_failed_at={"phase": "pdf_parse", "phase_label": "PDF 解析"}),
        job("e", status="failed", stage_failed_at={"phase": "ocr"}),
        job("f", status="failed", stage_failed_at={"phase": "pdf_parse", "phase_label": None}),
        job("g", status="processing", page_coverage="0.3"),
    ]
    view = q.build_overview_view(jobs, coverage_min=0.8, trend_days=7, tz=timezone.utc, now_ms=0)
    assert view["total"] == 7 and view["status_counts"]["failed"] == 3
    # 工作台只认 0～1 的数字；质量页按 Number(raw) 认任何有限值（两处原口径不同，各自保留）。
    assert view["page_coverage"] == {"average": pytest.approx(0.7), "sample_size": 2, "excluded_count": 5}
    assert view["page_coverage_numeric"] == {"average": pytest.approx(0.7), "sample_size": 4, "excluded_count": 3}
    assert view["low_coverage_count"] == 1
    assert view["done_coverage"] == {"sample_size": 2, "low_coverage_count": 1}
    # 同一阶段的展示名取最后一个任务的（与前端 Map.set 覆盖一致）；无法识别的阶段归入"未归因"。
    assert view["failure_stages"] == [
        {"phase": "pdf_parse", "label": "pdf_parse", "count": 2},
        {"phase": "unattributed", "label": "未归因", "count": 1},
    ]
    assert view["unassociated_count"] == 6, "组织 ID 只有空白也算未关联"
    assert view["organization_report_counts"] == {"u1": 1, " ": 1}
    assert q.build_overview_view([], coverage_min=0.8, trend_days=7, tz=None, now_ms=0)["done_coverage"] is None


def test_daily_trend_buckets_events_by_browser_timezone():
    shanghai = ZoneInfo("Asia/Shanghai")

    def at(*parts):
        return datetime(*parts, tzinfo=shanghai).timestamp() * 1000

    now, recent, older = at(2026, 10, 12, 15), at(2026, 10, 12, 9), at(2026, 10, 2, 9)
    jobs = [  # 与 app/tests/workbenchCockpitAdapters.test.ts 的趋势用例相同
        job("old", created_ts=older / 1000, updated_ts=recent / 1000),
        job("current", status="review_required", created_ts=recent, analysis_finished_ts=recent / 1000),
        job("missing", updated_ts=recent),
        job("running", status="processing", created_ts=recent, analysis_finished_ts=recent),
        job("future", status="uploaded", created_ts=now + 500000),
        job("overflow", status="uploaded", created_ts=1e20),
    ]
    week = q.daily_trend(jobs, days=7, tz=shanghai, now_ms=now)
    assert [point["date"] for point in week["points"]][-1] == "2026-10-12" and len(week["points"]) == 7
    assert sum(point["uploaded"] for point in week["points"]) == 2
    assert sum(point["analyzed"] for point in week["points"]) == 1, "只认真实分析结束时间"
    assert (week["missing_upload_times"], week["missing_analysis_times"]) == (1, 2)
    assert sum(point["uploaded"] for point in q.daily_trend(jobs, days=30, tz=shanghai, now_ms=now)["points"]) == 3

    just_after_midnight = [job("x", created_ts=at(2026, 10, 12, 0, 30))]
    local = q.daily_trend(just_after_midnight, days=2, tz=shanghai, now_ms=now)
    utc = q.daily_trend(just_after_midnight, days=2, tz=timezone.utc, now_ms=now)
    assert [p["uploaded"] for p in local["points"]] == [0, 1], "按浏览器所在时区分日"
    assert [p["uploaded"] for p in utc["points"]] == [1, 0]


@pytest.mark.asyncio
async def test_list_endpoint_serves_page_and_counts_views(tmp_path, monkeypatch):
    from starlette.requests import Request
    from api.routes import jobs as jobs_route

    summaries = [job("a", status="processing"), job("b", organization_id="dept"), job("c")]
    monkeypatch.setattr(jobs_route, "require_login", lambda _: (None, "", {"is_admin": True}))
    monkeypatch.setattr(runtime, "iter_job_dirs", lambda: [tmp_path / item["job_id"] for item in summaries])
    by_id = {item["job_id"]: item for item in summaries}
    monkeypatch.setattr(runtime, "collect_job_summary", lambda path: dict(by_id[path.name], ts=0))

    def request(query: str) -> Request:
        return Request({"type": "http", "method": "GET", "path": "/api/jobs", "headers": [],
                        "query_string": query.encode()})

    counts = await jobs_route.list_jobs(request("view=counts"), limit=None, offset=0)
    assert counts["total"] == 3 and counts["status_counts"]["analyzing"] == 1
    page = await jobs_route.list_jobs(request("view=page&org=dept&page_size=1&include=runnable"), limit=None, offset=0)
    assert [item["job_id"] for item in page["items"]] == ["b"] and page["runnable"][0]["job_id"] == "b"
    history = await jobs_route.list_jobs(request("view=page&scope=finished"), limit=None, offset=0)
    assert [item["job_id"] for item in history["items"]] == ["b", "c"] and history["job_total"] == 3
    overview = await jobs_route.list_jobs(request("view=overview&tz=Asia/Shanghai&trend_days=7"), limit=None, offset=0)
    assert overview["total"] == 3 and len(overview["trend"]["points"]) == 7 and "items" not in overview
    bad_tz = await jobs_route.list_jobs(request("view=overview&tz=Mars/Base"), limit=None, offset=0)
    assert len(bad_tz["trend"]["points"]) == 30, "无法识别的时区按服务器本地时间分日，不报错"
    legacy = await jobs_route.list_jobs(request(""), limit=None, offset=0)
    assert isinstance(legacy, list) and len(legacy) == 3, "不带 view 参数时保持原有数组形状"


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)
    runtime._JOB_SUMMARY_CACHE.clear()
    runtime._JOB_ACCESS_FIELDS_CACHE.clear()
    for name in ("job-a", "job-b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "status.json").write_text(json.dumps(
            {"job_id": name, "status": "done", "created_by": "owner", "analysis_revision": "1"}))
    return tmp_path


def test_decision_on_one_job_keeps_other_job_summaries_cached(jobs_root, monkeypatch):
    workflow = jobs_root / ".issue_workflow.json"
    workflow.write_text(json.dumps({"decision_events": []}))
    for name in ("job-a", "job-b"):
        runtime.collect_job_summary(jobs_root / name)
    rebuilt = []
    original_filters = runtime.apply_job_issue_filters
    monkeypatch.setattr(runtime, "apply_job_issue_filters",
                        lambda job_dir, payload: rebuilt.append(job_dir.name) or original_filters(job_dir, payload))
    workflow.write_text(json.dumps({"decision_events": [
        {"job_id": "job-a", "issue_id": "i1", "status": "no_issue", "analysis_revision": "1"}]}))
    for name in ("job-a", "job-b"):
        runtime.collect_job_summary(jobs_root / name)
    assert rebuilt == ["job-a"], "只有被判定的任务需要重建摘要"
    # 旧格式 issues 记录同样参与推导判定，变化也要让对应任务重建。
    workflow.write_text(json.dumps({"decision_events": [], "issues": {
        "job-b::i2": {"job_id": "job-b", "issue_id": "i2", "status": "confirmed"}}}))
    rebuilt.clear()
    for name in ("job-a", "job-b"):
        runtime.collect_job_summary(jobs_root / name)
    assert sorted(rebuilt) == ["job-a", "job-b"]


def test_access_context_rereads_status_only_when_file_changes(jobs_root, monkeypatch):
    runtime.collect_job_summary(jobs_root / "job-a")
    reads = []
    original_read_text = type(jobs_root).read_text
    monkeypatch.setattr(type(jobs_root), "read_text",
                        lambda self, *a, **k: reads.append(self.name) or original_read_text(self, *a, **k))
    context = runtime.get_job_access_context("job-a")
    assert context["created_by"] == "owner" and reads == [], "摘要已解析过的状态文件不再重读"
    (jobs_root / "job-a" / "status.json").write_text(json.dumps({"job_id": "job-a", "created_by": "someone-else"}))
    assert runtime.get_job_access_context("job-a")["created_by"] == "someone-else"
    assert reads == ["status.json"]
    (jobs_root / "job-a" / "status.json").write_text("not json")
    with pytest.raises(runtime.HTTPException):
        runtime.get_job_access_context("job-a")
    with pytest.raises(runtime.HTTPException):
        runtime.get_job_access_context("missing")
