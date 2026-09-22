"""复核生命周期服务层的纯逻辑验证（不连库）。

为什么这一层还需要独立用例
--------------------------
真库用例（``tests/test_review_lifecycle_pg.py``）验证的是事务、行锁、唯一约束与
SQL 语义；本文件验证的是**判定本身**：在给定事实下，门禁到底给出哪些阻塞码、
问题集合到底包含哪些 id、代际令牌怎么算。两类问题必须分开——把它们混在一起时，
一个"阻塞码少了一条"的缺陷会表现为"真库用例报了个 SQL 错误"，排查成本极高。

夹具驱动的前后端契约（§四十三）
-------------------------------
``tests/fixtures/review_problem_fixture.json`` 同时被后端（本文件）与前端
（``app/tests/reviewWorkbenchContract.test.ts``）读取，两侧必须得到**完全相同的**
问题 id 列表与计数。没有这条契约，前端 ``toUiProblems`` 与后端完成门禁就会
各维护一套"什么算一条待人工处理的问题"，最终出现"页面还挂着待复核、
后端却判定都处理完了"的假完成。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.schemas.review_lifecycle import (
    REVIEW_STATUS_COMPLETED,
    REVIEW_STATUS_IN_PROGRESS,
    REVIEW_STATUS_INVALIDATED,
)
from src.services import review_lifecycle_service, review_session_store
from src.services.analysis_result_store import (
    _upsert_analysis_job,
    compute_analysis_result_fingerprint,
)
from src.services.material_detail_query_service import (
    ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION,
    ANALYSIS_REASON_NO_CURRENT_VERSION,
)
from src.services.review_context_query import ReviewAnalysis
from src.services.review_lifecycle_service import ReviewState
from src.services.review_problem_set import (
    build_issue_counts,
    collect_review_problems,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "review_problem_fixture.json"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _problems_from_fixture(fixture: Dict[str, Any]) -> List[Any]:
    result = fixture["detail"]["result"]
    return collect_review_problems(
        job_uuid=fixture["job_uuid"],
        ai_findings=result["ai_findings"],
        rule_findings=result["rule_findings"],
        merged_result=result["merged"],
        structured_ingest=fixture["detail"]["structured_ingest"],
        ignored_issue_ids=fixture["detail"]["ignored_issue_ids"],
    )


def _slot_row(**overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "slot_id": "slot-1",
        "current_document_version_id": 11,
        "subject_org_id": "unit-1",
        "subject_kind": "unit",
        "material_scope": "unit_self",
        "report_kind": "final",
        "fiscal_year": 2024,
        "mapping_key": "",
        "applicability_status": "applicable",
        "caliber_conflict_candidate": None,
        "status": "reviewing",
        "status_reason": "review_in_progress",
        "due_at": None,
    }
    row.update(overrides)
    return row


def _analysis(**overrides: Any) -> ReviewAnalysis:
    payload: Dict[str, Any] = {
        "job_uuid": "job-1",
        "analysis_revision": 1,
        "document_version_id": 11,
        "status": "done",
        "completed": True,
        "unavailable_reason": None,
        "result_row": {"ai_findings": [], "rule_findings": [], "merged_result": {}},
        "result_meta": {},
        "structured_ingest": {},
        "formal_issue_count": 0,
    }
    payload.update(overrides)
    return ReviewAnalysis(**payload)


def _session(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "review_session_id": "sess-1",
        "slot_id": "slot-1",
        "document_version_id": 11,
        "analysis_job_uuid": "job-1",
        "analysis_basis_token": "job-1:1",
        "status": REVIEW_STATUS_IN_PROGRESS,
    }
    payload.update(overrides)
    return payload


def _state(
    *,
    analysis: Optional[ReviewAnalysis] = None,
    active: Optional[Dict[str, Any]] = None,
    completed: Optional[Dict[str, Any]] = None,
    problems: Optional[List[Any]] = None,
    decisions: Optional[Dict[str, str]] = None,
    coverage_available: bool = True,
    coverage_blocking_total: Optional[int] = 0,
    slot_row: Optional[Dict[str, Any]] = None,
) -> ReviewState:
    analysis = analysis or _analysis()
    return ReviewState(
        slot_row=slot_row or _slot_row(),
        slot_id="slot-1",
        analysis=analysis,
        basis_token=(
            review_session_store.build_analysis_basis_token(
                analysis.job_uuid, analysis.analysis_revision
            )
            if analysis.job_uuid
            else None
        ),
        active_session=active,
        completed_session=completed,
        problems=list(problems or []),
        decisions=dict(decisions or {}),
        coverage_available=coverage_available,
        coverage_blocking_total=coverage_blocking_total,
    )


def _codes(state: ReviewState) -> List[str]:
    return [item.code for item in review_lifecycle_service.evaluate_completion_gate(state).blockers]


# ==== 夹具契约：问题集合的身份与计数 ========================================


def test_problem_set_matches_frontend_contract_fixture():
    """问题 id 列表与顺序必须与夹具逐字一致（前端读同一份夹具）。"""
    fixture = _fixture()
    problems = _problems_from_fixture(fixture)
    assert [item.issue_id for item in problems] == fixture["issue_ids_in_order"]
    assert [
        item.issue_id for item in problems if item.is_blocking_candidate
    ] == fixture["blocking_issue_ids_in_order"]


def test_problem_set_counts_match_fixture():
    """状态计数与阻塞计数必须与夹具一致；total 恒等于五桶之和。"""
    fixture = _fixture()
    problems = _problems_from_fixture(fixture)
    counts = build_issue_counts(problems, fixture["decisions"])
    assert counts == fixture["expected_counts"]
    assert counts["total"] == sum(
        counts[key] for key in ("confirmed", "no_issue", "in_package", "pending", "needs_review")
    )


def test_ignored_issue_ids_are_excluded_like_the_workbench():
    """legacy 忽略清单里的条目不再出现在集合里（页面看不到 ⇒ 不该阻塞）。"""
    fixture = _fixture()
    problems = _problems_from_fixture(fixture)
    assert "rule-2" not in {item.issue_id for item in problems}


def test_degraded_finding_is_listed_but_not_blocking():
    """缺证据降级的条目仍展示（用户有权知道证据不完整），但不参与阻塞统计。"""
    problems = {item.issue_id: item for item in _problems_from_fixture(_fixture())}
    assert problems["ai-degraded"].is_blocking_candidate is False
    assert problems["ai-degraded"].source == "finding"
    assert problems["T1:low_confidence"].source == "structured_ingest"
    assert problems["T1:low_confidence"].is_blocking_candidate is True


def test_unknown_issue_status_counts_as_pending():
    """不认识的状态码按待处理处理：拼错的值不能让复核静默放行。"""
    problems = _problems_from_fixture(_fixture())
    counts = build_issue_counts(
        problems,
        {
            "ai-1": "totally_bogus",
            "rule-1": "no_issue",
            "T1:low_confidence": "in_package",
            "facts:none": "needs_review",
        },
    )
    assert counts["pending"] == 1
    assert counts["needs_review"] == 1


def test_missing_workflow_record_is_pending_not_completed():
    """§四十一：完全没有记录 = pending。数据库没有行不等于已完成。"""
    problems = _problems_from_fixture(_fixture())
    counts = build_issue_counts(problems, {})
    assert counts["pending"] == 4
    assert counts["confirmed"] == 0


def test_merged_ids_narrow_the_problem_set():
    """merged.merged_ids 非空时只取被合并的条目：否则一条问题会被数成两条。"""
    fixture = _fixture()
    result = fixture["detail"]["result"]
    problems = collect_review_problems(
        job_uuid=fixture["job_uuid"],
        ai_findings=result["ai_findings"],
        rule_findings=result["rule_findings"],
        merged_result={"merged_ids": ["ai-1", "rule-1"]},
        structured_ingest={},
    )
    assert [item.issue_id for item in problems] == ["ai-1", "rule-1"]


def test_structured_review_item_id_is_synthesized_like_the_frontend():
    """缺 id 的 review item 按前端同一条合成规则生成 id（否则两端身份对不上）。"""
    problems = collect_review_problems(
        job_uuid="job-x",
        structured_ingest={
            "review_items": [
                {"type": "unknown_table", "table_code": "T5", "page_number": 9},
                {"type": "unknown_table", "table_code": "T5"},
            ]
        },
    )
    assert [item.issue_id for item in problems] == [
        "job-x:structured-review:unknown_table:T5:9:1",
        "job-x:structured-review:unknown_table:T5:unlocated:2",
    ]


# ==== 分析代际令牌 ==========================================================


def test_basis_token_changes_with_generation_and_is_stable_otherwise():
    token_v1 = review_session_store.build_analysis_basis_token("job-a", 1)
    token_v2 = review_session_store.build_analysis_basis_token("job-a", 2)
    assert token_v1 == "job-a:1"
    assert token_v1 != token_v2, "同一 job 换代必须改变令牌"
    assert review_session_store.build_analysis_basis_token("job-a", 1) == token_v1


def test_basis_token_parses_back_and_reports_unknown_revision():
    parsed = review_session_store.parse_analysis_basis_token("job-a:7")
    assert parsed == {"job_uuid": "job-a", "analysis_revision": 7}
    assert review_session_store.parse_analysis_basis_token("garbage") == {
        "job_uuid": "garbage",
        "analysis_revision": None,
    }
    assert review_session_store.parse_analysis_basis_token("job-a:x")["analysis_revision"] is None


def test_fingerprint_depends_on_analysis_content_only():
    """指纹只看被复核的分析内容：进度/时间戳变化不得造成换代。"""
    base = {
        "job_id": "job-1",
        "result": {
            "ai_findings": [{"id": "ai-1"}],
            "rule_findings": [],
            "merged": {"totals": {}},
            "meta": {"elapsed_ms": {"total": 1}},
        },
        "status": "done",
        "ts": 1.0,
        "progress": 100,
    }
    same_content = {**base, "ts": 99.0, "progress": 0, "status": "review_required"}
    different_content = {
        **base,
        "result": {**base["result"], "ai_findings": [{"id": "ai-1"}, {"id": "ai-2"}]},
    }
    coverage_changed = {
        **base,
        "result": {
            **base["result"],
            "meta": {"obligation_coverage": {"blocking_total": 3}},
        },
    }
    assert compute_analysis_result_fingerprint(base) == compute_analysis_result_fingerprint(
        same_content
    )
    assert compute_analysis_result_fingerprint(base) != compute_analysis_result_fingerprint(
        different_content
    )
    # 检查覆盖属于"这次分析的结论"，变化必须换代（否则覆盖被改写而复核不失效）
    assert compute_analysis_result_fingerprint(base) != compute_analysis_result_fingerprint(
        coverage_changed
    )


def test_upsert_analysis_job_sql_keeps_generation_rules():
    """静态守护：代际递增规则一旦被改掉，必须有人显式看到这条断言变红。"""
    import inspect

    source = inspect.getsource(_upsert_analysis_job)
    assert "analysis_revision = CASE" in source
    assert "analysis_jobs.analysis_result_fingerprint IS NULL" in source
    assert "IS DISTINCT FROM EXCLUDED.analysis_result_fingerprint" in source


# ==== 完成门禁：逐条阻塞码 ==================================================


def test_gate_blocks_when_review_not_started():
    """§八十二：没问题也必须先显式开始、显式完成。"""
    assert _codes(_state()) == ["review_not_started"]


def test_gate_passes_with_active_session_and_no_blocking_facts():
    state = _state(active=_session())
    gate = review_lifecycle_service.evaluate_completion_gate(state)
    assert gate.can_complete is True
    assert gate.blockers == []


def test_gate_blocks_when_document_version_changed():
    state = _state(
        active=_session(document_version_id=10),
        slot_row=_slot_row(current_document_version_id=11),
    )
    assert _codes(state)[0] == "document_version_changed"


def test_gate_blocks_when_analysis_basis_changed():
    state = _state(active=_session(analysis_basis_token="job-1:0"))
    assert _codes(state)[0] == "analysis_basis_changed"


def test_gate_blocks_without_current_version():
    state = _state(
        analysis=_analysis(
            job_uuid=None,
            completed=False,
            unavailable_reason=ANALYSIS_REASON_NO_CURRENT_VERSION,
            document_version_id=None,
        ),
        slot_row=_slot_row(current_document_version_id=None),
        coverage_available=False,
    )
    codes = _codes(state)
    assert "analysis_unavailable" in codes


def test_gate_blocks_when_analysis_not_completed():
    state = _state(
        active=_session(),
        analysis=_analysis(
            completed=False, unavailable_reason=ANALYSIS_REASON_NONE_FOR_CURRENT_VERSION
        ),
        coverage_available=False,
    )
    assert "analysis_not_completed" in _codes(state)


def test_gate_blocks_on_identity_and_caliber():
    identity = _state(
        active=_session(), slot_row=_slot_row(fiscal_year=None), coverage_available=True
    )
    assert "identity_unresolved" in _codes(identity)

    caliber = _state(
        active=_session(), slot_row=_slot_row(caliber_conflict_candidate="summary")
    )
    assert "caliber_conflict" in _codes(caliber)


@pytest.mark.parametrize(
    "decisions,expected",
    [
        ({}, ["pending_findings"]),
        ({"ai-1": "needs_review"}, ["needs_review_findings"]),
        ({"ai-1": "confirmed"}, []),
        ({"ai-1": "no_issue"}, []),
        ({"ai-1": "in_package"}, []),
    ],
)
def test_gate_issue_resolution_matrix(decisions, expected):
    problems = collect_review_problems(
        job_uuid="job-1",
        ai_findings=[
            {
                "id": "ai-1",
                "severity": "high",
                "evidence": [{"page": 1, "text": "证据", "bbox": [1, 2, 3, 4]}],
            }
        ],
    )
    state = _state(active=_session(), problems=problems, decisions=decisions)
    assert _codes(state) == expected


def test_gate_blocks_on_unavailable_coverage():
    """§四十六：未知不得当成零阻塞。"""
    state = _state(active=_session(), coverage_available=False, coverage_blocking_total=None)
    assert _codes(state) == ["coverage_unavailable"]


def test_gate_blocks_on_blocking_obligations_with_count():
    state = _state(active=_session(), coverage_available=True, coverage_blocking_total=2)
    gate = review_lifecycle_service.evaluate_completion_gate(state)
    assert [item.code for item in gate.blockers] == ["blocking_obligations"]
    assert gate.blockers[0].count == 2


def test_blocker_order_puts_stale_facts_before_content():
    """事实过期排在内容之前：用户先看到"请重新复核"，那才是他要做的动作。"""
    problems = collect_review_problems(
        job_uuid="job-1",
        ai_findings=[
            {
                "id": "ai-1",
                "severity": "high",
                "evidence": [{"page": 1, "text": "证据", "bbox": [1, 2, 3, 4]}],
            }
        ],
    )
    state = _state(
        active=_session(document_version_id=10),
        problems=problems,
        coverage_available=False,
        coverage_blocking_total=None,
        slot_row=_slot_row(fiscal_year=None),
    )
    assert _codes(state)[:2] == ["document_version_changed", "identity_unresolved"]


# ==== 开启门禁 ==============================================================


def test_start_gate_does_not_require_an_existing_session():
    state = _state(analysis=_analysis(job_uuid=None, completed=False))
    gate = review_lifecycle_service.evaluate_start_gate(state)
    assert gate.can_complete is False
    assert "analysis_unavailable" in [item.code for item in gate.blockers]
    # 开启门禁不涉及"是否已经开过复核"，因此不会出现 review_not_started
    assert "review_not_started" not in [item.code for item in gate.blockers]


def test_start_gate_passes_on_a_reviewable_material():
    gate = review_lifecycle_service.evaluate_start_gate(_state())
    assert gate.can_complete is True


# ==== 会话行转换 ============================================================


def test_session_row_to_dict_parses_jsonb_payload():
    row = {
        "review_session_id": "sess-1",
        "review_result": json.dumps({"issue_counts": {"total": 2}}, ensure_ascii=False),
    }
    parsed = review_session_store.session_row_to_dict(row)
    assert parsed["review_result"] == {"issue_counts": {"total": 2}}
    assert review_session_store.session_row_to_dict(None) is None


def test_to_jsonb_serializes_non_ascii_without_escaping():
    payload = review_session_store.to_jsonb({"message": "当前无法完成复核"})
    assert "当前无法完成复核" in payload


def test_status_constants_are_shared_with_the_schema_module():
    """状态取值域只有一份：schema 与 store 不允许各写一套字面量。"""
    assert REVIEW_STATUS_IN_PROGRESS == "in_progress"
    assert REVIEW_STATUS_COMPLETED == "completed"
    assert REVIEW_STATUS_INVALIDATED == "invalidated"


def test_affected_rows_parsing_is_fail_open():
    assert review_session_store._affected_rows("UPDATE 3") == 3
    assert review_session_store._affected_rows("UPDATE 0") == 0
    assert review_session_store._affected_rows(None) == 0
    assert review_session_store._affected_rows("garbage") == 0


# ==== 分析起点钩子：从队列点「开始分析」也必须失效旧复核 ======================


async def test_analysis_start_invalidates_previous_review(tmp_path, monkeypatch):
    """`start_analysis`（所有分析起点的唯一漏斗）启动前必须让旧复核失效。

    为什么专门测这条：仓库里除了 `/api/jobs/{id}/reanalyze` 与
    `/api/jobs/reanalyze-all`，还有 `POST /api/analyze/{job_id}`
    （处理队列的「开始分析」，`api/routes/analyze.py` 直连 `start_analysis`）。
    钩子只挂在 reanalyze 上时，从队列对一份已复核完成的材料点「开始分析」会
    重跑分析**却不失效旧复核**——页面继续显示「已完成复核」，而它复核的是
    上一代结果。挂在共同起点上，两个入口自动都覆盖。
    """
    from api import runtime

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "job-analyze-hook"
    job_dir.mkdir()
    (job_dir / "sample_2025.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json", {"job_id": "job-analyze-hook", "status": "uploaded"}
    )

    seen: List[str] = []

    async def _fake_invalidate(target, *, reason="analysis_restarted"):
        seen.append(target)
        return {"job_uuid": target, "sessions_invalidated": 1, "slot_id": "slot-1"}

    async def _dummy_runner(_job_dir):
        return None

    class _DummyQueue:
        async def enqueue(self, job_id: str) -> None:
            return None

    async def _fake_persist(payload, *, include_results: bool = False):
        return True

    monkeypatch.setattr(
        review_lifecycle_service,
        "invalidate_reviews_for_analysis_restart",
        _fake_invalidate,
    )
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)

    await runtime.start_analysis("job-analyze-hook", {"mode": "legacy"})

    assert seen == ["job-analyze-hook"], "分析启动前必须失效该任务上的旧复核"


async def test_analysis_start_hook_failure_does_not_block_analysis(tmp_path, monkeypatch):
    """钩子失败不允许阻断分析：维护与上传主流程不能被旁路能力卡住。

    但失败必须大声报错（`logger.error`），因为库里仍会显示"已完成复核"。
    """
    from api import runtime

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "job-analyze-hook-fail"
    job_dir.mkdir()
    (job_dir / "sample_2025.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json", {"job_id": "job-analyze-hook-fail", "status": "uploaded"}
    )

    async def _boom(_target, *, reason="analysis_restarted"):
        raise RuntimeError("simulated database outage")

    async def _dummy_runner(_job_dir):
        return None

    class _DummyQueue:
        async def enqueue(self, job_id: str) -> None:
            return None

    async def _fake_persist(payload, *, include_results: bool = False):
        return True

    monkeypatch.setattr(
        review_lifecycle_service, "invalidate_reviews_for_analysis_restart", _boom
    )
    monkeypatch.setattr(runtime, "_pipeline_runner", _dummy_runner)
    monkeypatch.setattr(runtime, "_job_queue", _DummyQueue())
    monkeypatch.setattr(runtime, "persist_analysis_job_snapshot", _fake_persist)

    payload = await runtime.start_analysis("job-analyze-hook-fail", {"mode": "legacy"})

    assert payload["status"] == "started", "钩子失败不能阻断分析"
