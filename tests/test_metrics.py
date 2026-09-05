"""Task 11 / 缺口 B-06：关键指标采集与告警钩子。

覆盖的指标口径（PLAN 要求的六项）：
阶段耗时、队列积压（复用 `.worker-heartbeats`）、AI 失败率、
unknown 类型比例、review_required 比例、report_id 冲突计数。

断言意图（每组都有正反对照）：
1. 有数据：各项计数/比例按构造的产物精确匹配（不是"非空即通过"）。
2. 无数据：空目录返回结构完整、计数为 0 的骨架，且不抛异常、不返回 None。
3. 队列积压：新鲜心跳算存活、过期心跳算 stale——用固定 `now` 消除时序 flaky。
4. 端点鉴权：未授权访问被拒（401/403），抓取令牌与管理员会话都能放行，
   令牌错误时不得放行；`METRICS_ENABLED=false` 时端点消失。
5. 与回放脚本共用同一 report_id 冲突口径（防止两套事实）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import metrics as metrics_route
from src.services.metrics import (
    collect_metrics,
    collect_metrics_cached,
    collect_worker_heartbeats,
    render_prometheus,
    report_id_uniqueness,
    reset_metrics_cache,
)

FIXED_NOW = 1_800_000_000.0


def _write_job(
    uploads: Path,
    job_id: str,
    *,
    status: str,
    report_kind: str = "budget",
    report_year: Optional[int] = 2025,
    checksum: Optional[str] = None,
    report_id: Optional[str] = None,
    use_ai_assist: bool = True,
    quality_status: str = "complete",
    analysis_conclusion: Optional[str] = None,
    rule_ms: Optional[int] = None,
    ai_ms: Optional[int] = None,
    started_at: Optional[float] = None,
    finished_at: Optional[float] = None,
    provider_fell_back: bool = False,
    degraded_findings: int = 0,
    ts: Optional[float] = None,
    evidence: Optional[Dict[str, Any]] = None,
    omit_evidence_field: bool = False,
    findings: Optional[List[Dict[str, Any]]] = None,
) -> None:
    job_dir = uploads / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    meta: Dict[str, Any] = {
        "report_kind": report_kind,
        "use_ai_assist": use_ai_assist,
        "elapsed_ms": {
            key: value
            for key, value in (("rule", rule_ms), ("ai", ai_ms))
            if value is not None
        },
    }
    # evidence=None 且不省略字段时保持原有默认形态（只带 degraded_count，
    # 模拟"有留痕但未写 total/complete"的最小产物）；显式传入 evidence dict
    # 时原样写入（新任务的完整留痕）；omit_evidence_field=True 模拟历史任务
    # 完全没有 evidence_completeness 字段。
    if not omit_evidence_field:
        meta["evidence_completeness"] = evidence or {"degraded_count": degraded_findings}
    if started_at is not None:
        meta["started_at"] = started_at
    if finished_at is not None:
        meta["finished_at"] = finished_at
    if provider_fell_back:
        meta["provider_stats"] = [{"fell_back": True, "provider_used": "engine"}]

    payload: Dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "ts": FIXED_NOW - 30 if ts is None else ts,
        "filename": f"{job_id}.pdf",
        "report_kind": report_kind,
        "report_year": report_year,
        "quality_status": quality_status,
        "use_ai_assist": use_ai_assist,
        "result": {
            "issues": {
                "all": findings if findings is not None else [],
                "error": [],
                "warn": [],
                "info": [],
            },
            "meta": meta,
        },
    }
    if analysis_conclusion is not None:
        payload["analysis_conclusion"] = analysis_conclusion
    if checksum is not None:
        payload["checksum"] = checksum
    if report_id is not None:
        payload["structured_ingest"] = {"ps_sync": {"report_id": report_id}}

    (job_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _write_heartbeat(
    uploads: Path, worker_id: str, *, age_seconds: float, active: int, queued: int
) -> None:
    heartbeat_dir = uploads / ".worker-heartbeats"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    (heartbeat_dir / f"{worker_id}.json").write_text(
        json.dumps(
            {
                "worker_id": worker_id,
                "pid": 1234,
                "ts": FIXED_NOW - age_seconds,
                "active_jobs": active,
                "queued_jobs": queued,
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. 无数据：结构完整的骨架
# ---------------------------------------------------------------------------
def test_metrics_on_empty_uploads_returns_zero_skeleton(tmp_path):
    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["jobs"]["total"] == 0
    assert metrics["queue"]["backlog"] == 0
    assert metrics["queue"]["live_workers"] == 0
    assert metrics["queue"]["oldest_queued_age_seconds"] is None
    assert metrics["ai"]["failure_rate"] == 0.0
    assert metrics["quality"]["unknown_report_kind"]["ratio"] == 0.0
    assert metrics["quality"]["review_required"]["ratio"] == 0.0
    assert metrics["report_id"]["collision_count"] == 0
    assert metrics["report_id"]["unique"] is True
    # 无样本时耗时统计给 None，而不是用 0 冒充"很快"
    assert metrics["stage_durations"]["rule"] == {
        "count": 0,
        "mean_ms": None,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }


def test_metrics_on_missing_directory_does_not_raise(tmp_path):
    metrics = collect_metrics(tmp_path / "not-exists", now=FIXED_NOW)
    assert metrics["jobs"]["total"] == 0


def test_metrics_skips_dirs_without_status_json(tmp_path):
    (tmp_path / "job-empty").mkdir()
    _write_job(tmp_path, "job-ok", status="done")

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["jobs"]["total"] == 1
    assert metrics["jobs"]["skipped_dirs"] == 1


# ---------------------------------------------------------------------------
# 2. 有数据：各指标精确匹配
# ---------------------------------------------------------------------------
def test_metrics_with_data_matches_constructed_fixtures(tmp_path):
    # done + 正常
    _write_job(
        tmp_path,
        "job-done",
        status="done",
        rule_ms=1200,
        ai_ms=3400,
        started_at=FIXED_NOW - 10,
        finished_at=FIXED_NOW - 4,
        checksum="cs-1",
        report_id="rep-1",
    )
    # review_required + unknown 类型 + 年度未识别 + 证据降级
    _write_job(
        tmp_path,
        "job-review",
        status="review_required",
        report_kind="unknown",
        report_year=None,
        analysis_conclusion="incomplete",
        rule_ms=900,
        degraded_findings=2,
        checksum="cs-2",
        report_id="rep-2",
    )
    # AI 回退
    _write_job(
        tmp_path,
        "job-degraded",
        status="degraded",
        quality_status="degraded",
        provider_fell_back=True,
        rule_ms=1500,
        ai_ms=8000,
        checksum="cs-3",
    )
    # 不请求 AI，不该进 AI 失败率分母
    _write_job(tmp_path, "job-no-ai", status="done", use_ai_assist=False, rule_ms=700)
    # 排队中
    _write_job(tmp_path, "job-queued", status="queued", ts=FIXED_NOW - 600)
    _write_job(tmp_path, "job-processing", status="processing", ts=FIXED_NOW - 60)
    _write_job(tmp_path, "job-error", status="error")

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["jobs"]["total"] == 7

    # 队列积压：queued + processing
    assert metrics["queue"]["queued_jobs"] == 1
    assert metrics["queue"]["processing_jobs"] == 1
    assert metrics["queue"]["backlog"] == 2
    assert metrics["queue"]["oldest_queued_age_seconds"] == 600.0

    # AI 失败率：请求 AI 的 6 个任务里 1 个降级/回退
    assert metrics["ai"]["jobs_requested"] == 6
    assert metrics["ai"]["jobs_failed"] == 1
    assert metrics["ai"]["failure_rate"] == round(1 / 6, 4)
    assert metrics["ai"]["provider_fallback_total"] == 1

    # unknown 类型比例、review_required 比例、年度未识别比例
    assert metrics["quality"]["unknown_report_kind"]["count"] == 1
    assert metrics["quality"]["unknown_report_kind"]["ratio"] == round(1 / 7, 4)
    assert metrics["quality"]["review_required"]["count"] == 1
    assert metrics["quality"]["review_required"]["ratio"] == round(1 / 7, 4)
    assert metrics["quality"]["unresolved_report_year"]["count"] == 1
    assert metrics["quality"]["error_jobs"]["count"] == 1
    assert metrics["quality"]["evidence_degraded_findings"] == 2

    # 阶段耗时：4 个 rule 样本、2 个 ai 样本、1 个 total 样本
    assert metrics["stage_durations"]["rule"]["count"] == 4
    assert metrics["stage_durations"]["rule"]["max_ms"] == 1500.0
    assert metrics["stage_durations"]["ai"]["count"] == 2
    assert metrics["stage_durations"]["ai"]["max_ms"] == 8000.0
    assert metrics["stage_durations"]["total"]["count"] == 1
    assert metrics["stage_durations"]["total"]["max_ms"] == 6000.0

    # report_id：两个不同 report_id、各自单一 checksum -> 无冲突
    assert metrics["report_id"]["distinct_report_ids"] == 2
    assert metrics["report_id"]["collision_count"] == 0


def test_report_id_collision_is_counted(tmp_path):
    """反例：同一 report_id 下出现两个不同 checksum，必须计为冲突。"""
    _write_job(tmp_path, "job-a", status="done", checksum="cs-a", report_id="rep-shared")
    _write_job(tmp_path, "job-b", status="done", checksum="cs-b", report_id="rep-shared")

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["report_id"]["collision_count"] == 1
    assert metrics["report_id"]["unique"] is False
    assert metrics["report_id"]["collisions"][0]["report_id"] == "rep-shared"
    assert metrics["report_id"]["collisions"][0]["job_ids"] == ["job-a", "job-b"]


def test_same_checksum_under_one_report_id_is_not_a_collision(tmp_path):
    """对照：同一原件的多次任务共用 report_id 是正常的，不能误报冲突。"""
    _write_job(tmp_path, "job-a", status="done", checksum="cs-same", report_id="rep-shared")
    _write_job(tmp_path, "job-b", status="done", checksum="cs-same", report_id="rep-shared")

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["report_id"]["collision_count"] == 0
    assert metrics["report_id"]["unique"] is True


def test_replay_script_shares_the_same_collision_definition():
    """回放脚本与运行时指标必须共用一个 report_id 冲突口径。"""
    from scripts import replay_analysis

    records: List[Dict[str, Any]] = [
        {"job_id": "j1", "report_id": "r1", "checksum": "c1"},
        {"job_id": "j2", "report_id": "r1", "checksum": "c2"},
    ]
    assert replay_analysis._report_id_uniqueness(records) == report_id_uniqueness(records)


# ---------------------------------------------------------------------------
# 2.5 证据完整率与正式问题聚合（UI 重建第四批 Task 7.2 补充）
# ---------------------------------------------------------------------------
def test_evidence_completeness_rate_aggregates_across_jobs(tmp_path):
    """正例：多个任务的 total/complete 分别累加后计算比率。

    顺带覆盖 B1 可定位类子口径的聚合：job-a 有 2 条文档级 finding（单列），
    job-b 有 1 条可定位类缺证据；门禁口径 locatable_completeness_rate
    按可定位类分子分母独立计算。
    """
    _write_job(
        tmp_path,
        "job-a",
        status="done",
        evidence={
            "total": 10,
            "complete": 9,
            "degraded_count": 1,
            "locatable_total": 8,
            "locatable_complete": 8,
            "document_level_total": 2,
        },
    )
    _write_job(
        tmp_path,
        "job-b",
        status="done",
        evidence={
            "total": 5,
            "complete": 5,
            "degraded_count": 0,
            "locatable_total": 5,
            "locatable_complete": 4,
            "document_level_total": 0,
        },
    )

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["quality"]["evidence_completeness"] == {
        "findings_total": 15,
        "findings_complete": 14,
        "completeness_rate": round(14 / 15, 4),
        "jobs_without_field": 0,
        "locatable_findings_total": 13,
        "locatable_findings_complete": 12,
        "locatable_completeness_rate": round(12 / 13, 4),
        "document_level_findings_total": 2,
        "jobs_without_locatable_field": 0,
    }


def test_evidence_completeness_rate_is_none_when_denominator_is_zero(tmp_path):
    """反例（红线）：分母为 0 时必须是 None，绝不能算成 100% 或 0。

    场景一：任务带 evidence_completeness 留痕但没有任何 finding（total=0）。
    场景二：完全没有任务。
    两种都是"没有可判定的样本"，语义是未知，不是"全部完整"。
    """
    _write_job(
        tmp_path,
        "job-clean",
        status="done",
        evidence={"total": 0, "complete": 0, "degraded_count": 0},
    )

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)
    assert metrics["quality"]["evidence_completeness"]["findings_total"] == 0
    assert metrics["quality"]["evidence_completeness"]["completeness_rate"] is None

    empty_metrics = collect_metrics(tmp_path / "not-used", now=FIXED_NOW)
    assert empty_metrics["quality"]["evidence_completeness"]["completeness_rate"] is None


def test_evidence_completeness_excludes_jobs_without_field(tmp_path):
    """历史任务（无 evidence_completeness 留痕）不参与分子分母，只计入缺口计数。

    这是与 replay 脚本"recorded/recomputed"区分同一件事的指标侧表达：
    运行时指标不做深拷贝重算（性能原因），所以拿不到留痕的任务
    必须显式报告为样本缺口，而不是按 0 拉低或抬高比率。
    """
    _write_job(
        tmp_path,
        "job-new",
        status="done",
        evidence={"total": 4, "complete": 4, "degraded_count": 0},
    )
    # 两个历史任务：没有 evidence_completeness 字段，也没有任何 finding
    _write_job(tmp_path, "job-legacy-1", status="done", omit_evidence_field=True)
    _write_job(tmp_path, "job-legacy-2", status="done", omit_evidence_field=True)

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    assert metrics["quality"]["evidence_completeness"] == {
        "findings_total": 4,
        "findings_complete": 4,
        "completeness_rate": 1.0,
        "jobs_without_field": 2,
        # job-new 的留痕是旧格式（无可定位类字段）：只计入样本缺口，不冒充 0
        "locatable_findings_total": 0,
        "locatable_findings_complete": 0,
        "locatable_completeness_rate": None,
        "document_level_findings_total": 0,
        "jobs_without_locatable_field": 1,
    }


def test_formal_issue_total_aggregates_with_count_formal_findings(tmp_path):
    """正例：formal_issue_total 聚合 = 各任务 count_formal_findings 之和。

    带一条降级 finding（evidence_status=degraded_missing_evidence）验证
    降级条目不计入正式问题——这是 count_formal_findings 的唯一口径，
    聚合层不得另算一套。
    """
    from src.services.evidence_guard import EVIDENCE_STATUS_DEGRADED

    _write_job(
        tmp_path,
        "job-a",
        status="done",
        findings=[
            {"id": "f1", "evidence_status": "complete"},
            {"id": "f2", "evidence_status": "complete"},
        ],
    )
    _write_job(
        tmp_path,
        "job-b",
        status="done",
        findings=[
            {"id": "f3", "evidence_status": EVIDENCE_STATUS_DEGRADED},
            {"id": "f4"},
        ],
    )

    metrics = collect_metrics(tmp_path, now=FIXED_NOW)

    # job-a 2 条正式 + job-b 1 条正式（f3 已降级不计入，f4 无状态按正式处理）
    assert metrics["quality"]["formal_issue_total"] == 3


def test_prometheus_renders_evidence_and_formal_metrics(tmp_path):
    """Prometheus 输出包含新指标；空样本时完整率指标必须整行省略。"""
    # 有样本：渲染完整率
    _write_job(
        tmp_path,
        "job-a",
        status="done",
        evidence={"total": 10, "complete": 10, "degraded_count": 0},
    )
    text = render_prometheus(collect_metrics(tmp_path, now=FIXED_NOW))
    assert "govbudget_formal_issue_total 0" in text
    assert "govbudget_evidence_completeness_rate 1.0" in text

    # 空样本：完整率省略（不是 0，不是 1），formal_issue_total 如实输出 0
    empty_text = render_prometheus(collect_metrics(tmp_path / "none", now=FIXED_NOW))
    assert "govbudget_evidence_completeness_rate" not in empty_text
    assert "govbudget_formal_issue_total 0" in empty_text


# ---------------------------------------------------------------------------
# 3. 队列积压与心跳（固定 now，避免时序 flaky）
# ---------------------------------------------------------------------------
def test_heartbeats_split_live_and_stale(tmp_path):
    _write_heartbeat(tmp_path, "worker-live", age_seconds=3, active=2, queued=5)
    _write_heartbeat(tmp_path, "worker-stale", age_seconds=600, active=9, queued=9)

    heartbeats = collect_worker_heartbeats(
        tmp_path, now=FIXED_NOW, max_age_seconds=15.0
    )

    assert heartbeats["live_workers"] == 1
    assert heartbeats["stale_heartbeats"] == 1
    # 过期 worker 自报的数字不得计入积压，否则死掉的 worker 会一直虚报
    assert heartbeats["reported_active_jobs"] == 2
    assert heartbeats["reported_queued_jobs"] == 5
    assert heartbeats["oldest_heartbeat_age_seconds"] == 600.0


def test_heartbeats_absent_directory(tmp_path):
    heartbeats = collect_worker_heartbeats(tmp_path, now=FIXED_NOW)
    assert heartbeats["live_workers"] == 0
    assert heartbeats["oldest_heartbeat_age_seconds"] is None


def test_metrics_cache_returns_cached_payload(tmp_path):
    reset_metrics_cache()
    try:
        _write_job(tmp_path, "job-1", status="done")
        first = collect_metrics_cached(tmp_path, ttl_seconds=60, now=FIXED_NOW)
        assert first["cached"] is False
        assert first["jobs"]["total"] == 1

        # 新增任务后立刻再取：TTL 未过期，必须命中缓存（证明缓存生效）
        _write_job(tmp_path, "job-2", status="done")
        second = collect_metrics_cached(tmp_path, ttl_seconds=60, now=FIXED_NOW + 1)
        assert second["cached"] is True
        assert second["jobs"]["total"] == 1

        # TTL 过期后必须重新扫盘（对照：缓存不能永久生效）
        third = collect_metrics_cached(tmp_path, ttl_seconds=60, now=FIXED_NOW + 120)
        assert third["cached"] is False
        assert third["jobs"]["total"] == 2
    finally:
        reset_metrics_cache()


# ---------------------------------------------------------------------------
# 4. Prometheus 渲染
# ---------------------------------------------------------------------------
def test_prometheus_render_contains_key_metrics(tmp_path):
    _write_job(tmp_path, "job-queued", status="queued", ts=FIXED_NOW - 100)
    _write_job(tmp_path, "job-review", status="review_required", report_kind="unknown")

    text = render_prometheus(collect_metrics(tmp_path, now=FIXED_NOW))

    assert "govbudget_queue_backlog 1" in text
    assert "govbudget_queue_oldest_queued_age_seconds 100.0" in text
    assert "govbudget_review_required_ratio 0.5" in text
    assert "govbudget_unknown_report_kind_ratio 0.5" in text
    assert "govbudget_report_id_collision_count 0" in text


def test_prometheus_render_omits_missing_values(tmp_path):
    """缺失值必须省略，不能写 0 冒充"耗时很短"。"""
    text = render_prometheus(collect_metrics(tmp_path, now=FIXED_NOW))
    assert "govbudget_stage_duration_p95_ms_rule" not in text
    assert "govbudget_queue_oldest_queued_age_seconds" not in text


# ---------------------------------------------------------------------------
# 5. 端点鉴权（正反对照）
# ---------------------------------------------------------------------------
def _client(monkeypatch, tmp_path) -> TestClient:
    """独立挂载 metrics 路由，避免拉起整个 app 的 lifespan 与队列。"""
    monkeypatch.setattr(metrics_route.runtime, "UPLOAD_ROOT", tmp_path)
    reset_metrics_cache()
    app = FastAPI()
    app.include_router(metrics_route.router)
    return TestClient(app)


def _deny_admin(monkeypatch) -> None:
    """把 require_admin 换成"一律拒绝"，模拟非管理员/未登录。"""

    def _raise(_request):
        raise HTTPException(status_code=401, detail="session token required")

    monkeypatch.setattr(metrics_route, "require_admin", _raise)


def _allow_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        metrics_route,
        "require_admin",
        lambda _request: (None, "tok", {"username": "admin", "is_admin": True}),
    )


def test_metrics_endpoint_rejects_unauthorized(monkeypatch, tmp_path):
    _write_job(tmp_path, "job-1", status="done")
    _deny_admin(monkeypatch)
    monkeypatch.delenv("METRICS_API_TOKEN", raising=False)

    response = _client(monkeypatch, tmp_path).get("/metrics")

    assert response.status_code == 401


def test_metrics_endpoint_allows_admin_session(monkeypatch, tmp_path):
    _write_job(tmp_path, "job-1", status="done")
    _allow_admin(monkeypatch)
    monkeypatch.delenv("METRICS_API_TOKEN", raising=False)

    response = _client(monkeypatch, tmp_path).get("/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["auth_mode"] == "admin_session"
    assert body["jobs"]["total"] == 1


def test_metrics_endpoint_allows_scrape_token(monkeypatch, tmp_path):
    _write_job(tmp_path, "job-1", status="done")
    _deny_admin(monkeypatch)
    monkeypatch.setenv("METRICS_API_TOKEN", "scrape-secret")

    client = _client(monkeypatch, tmp_path)
    ok = client.get("/metrics", headers={"X-Metrics-Token": "scrape-secret"})
    assert ok.status_code == 200
    assert ok.json()["auth_mode"] == "scrape_token"

    # 反例：令牌错误时不得放行（回落到管理员校验，被拒）
    bad = client.get("/metrics", headers={"X-Metrics-Token": "wrong-secret"})
    assert bad.status_code == 401


def test_metrics_endpoint_can_be_disabled(monkeypatch, tmp_path):
    _allow_admin(monkeypatch)
    monkeypatch.setenv("METRICS_ENABLED", "false")

    response = _client(monkeypatch, tmp_path).get("/metrics")

    assert response.status_code == 404


def test_metrics_endpoint_prometheus_format(monkeypatch, tmp_path):
    _write_job(tmp_path, "job-queued", status="queued", ts=FIXED_NOW - 10)
    _allow_admin(monkeypatch)
    monkeypatch.delenv("METRICS_API_TOKEN", raising=False)

    response = _client(monkeypatch, tmp_path).get("/metrics?format=prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "govbudget_queue_backlog 1" in response.text


def test_metrics_path_is_not_auth_exempt():
    """指标路径不得被加入免认证白名单，否则 API Key 中间件会直接放行。"""
    from src.security import SecurityConfig

    exempt = SecurityConfig().exempt_paths
    assert "/metrics" not in exempt
    assert "/api/metrics" not in exempt


@pytest.mark.parametrize("path", ["/metrics", "/api/metrics"])
def test_both_metrics_paths_are_registered(monkeypatch, tmp_path, path):
    _allow_admin(monkeypatch)
    monkeypatch.delenv("METRICS_API_TOKEN", raising=False)
    response = _client(monkeypatch, tmp_path).get(path)
    assert response.status_code == 200
