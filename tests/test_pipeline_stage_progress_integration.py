"""Task 3：per-job 阶段进度与失败阶段归因的端到端流水线测试。

对照任务书测试要求：
- 阶段推进顺序正确（跑一次真实成功流水线，断言 status.json 落地的 stage_progress
  在最终态确实是 quality_gate 阶段、100%）；
- 失败阶段归因准确（注入一次真实失败，断言 stage_failed_at 记录的阶段与
  失败发生前最后一次成功写入的阶段一致，不是随便一个阶段）；
- null 语义反例：历史遗留任务（status.json 里从未写过 stage_progress 字段）
  必须仍然可读，且新逻辑不会给它们凭空编出一个 stage_progress；
- 回归：新增写入不破坏 M1 质量门禁与四态结论，不改变 final_status 判定。

fixture 复用 `tests/test_structured_logging.py` 的 `_prepare_job` 模式
（同一套 monkeypatch 手法：假 PDF、假 pdfplumber、mock 掉 run_rules_in_process /
run_structured_ingest / persist_analysis_job_snapshot，避免真实网络/文件 IO），
但本文件独立维护一份精简版，因为断言关注点不同（阶段进度而非日志脱敏），
避免两个文件的 fixture 各自演进后互相打架。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime


class _FakePdf:
    def __init__(self, page_count: int) -> None:
        self.pages = [object()] * page_count

    def __enter__(self) -> "_FakePdf":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def _prepare_success_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    """准备一个会顺利跑到"完成"终态的任务目录。"""
    job_dir = tmp_path / name
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )

    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(1))
    monkeypatch.setattr(
        pipeline_mod, "_extract_visible_text_from_page", lambda _page: "一般公共预算财政拨款支出预算表" * 5
    )
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(return_value={"issues": {"all": [], "error": [], "warn": [], "info": []}}),
    )
    monkeypatch.setattr(pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True))
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(return_value={"status": "skipped", "review_item_count": 0, "review_items": []}),
    )
    monkeypatch.setattr(pipeline_mod.settings, "get", lambda *_args: False)
    return job_dir


# ---------------------------------------------------------------------------
# 成功路径：阶段推进顺序正确，最终态落在 quality_gate 阶段 100%
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_pipeline_reaches_quality_gate_phase_at_100_percent(
    tmp_path, monkeypatch
):
    job_dir = _prepare_success_job(tmp_path, monkeypatch, "job-success")

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] in {"done", "review_required", "degraded"}, (
        "成功路径下任务应到达某个终态（M1 四态之一），不应停在 processing"
    )

    stage_progress = payload.get("stage_progress")
    assert stage_progress is not None, "成功跑完的任务必须落地 stage_progress，不能缺失"
    assert stage_progress["phase"] == "quality_gate", (
        f"REGRESSION: 流水线跑到终态时应处于 quality_gate 阶段，实际是 {stage_progress['phase']!r}"
    )
    assert stage_progress["percent"] == 100
    # 失败态字段在成功任务上不应该出现
    assert "stage_failed_at" not in payload or payload.get("stage_failed_at") is None


@pytest.mark.asyncio
async def test_successful_pipeline_stage_progress_history_is_monotonic(tmp_path, monkeypatch):
    """在流水线运行过程中多次快照 status.json，验证阶段顺序索引不会倒退。

    做法：monkeypatch `runtime.write_json_file`，每次真正落盘前都记录一份
    当时的 payload 副本，跑完后重放这些快照检查阶段单调性——这是"阶段推进顺序
    正确"这条要求在真实流水线执行过程中（不只是静态调用 resolve_stage_progress）
    的直接证据。
    """
    from src.services.pipeline_stages import PIPELINE_STAGE_ORDER, resolve_stage_progress

    job_dir = _prepare_success_job(tmp_path, monkeypatch, "job-monotonic")

    snapshots: list[Dict[str, Any]] = []
    original_write_json_file = runtime.write_json_file

    def _capturing_write_json_file(path, payload, *args, **kwargs):
        if Path(path).name == "status.json":
            snapshots.append(dict(payload))
        return original_write_json_file(path, payload, *args, **kwargs)

    monkeypatch.setattr(runtime, "write_json_file", _capturing_write_json_file)

    await pipeline_mod._run_pipeline_inner(job_dir)

    assert len(snapshots) >= 5, "真实流水线应该产生多次状态写入快照"

    previous_order = -1
    for snapshot in snapshots:
        raw_stage = snapshot.get("stage")
        if not raw_stage:
            continue
        result = resolve_stage_progress(str(raw_stage))
        if result.phase is None:
            continue
        current_order = PIPELINE_STAGE_ORDER[result.phase]
        assert current_order >= previous_order, (
            f"REGRESSION: 真实流水线运行中阶段顺序倒退了，stage={raw_stage!r}"
        )
        previous_order = current_order


# ---------------------------------------------------------------------------
# 失败路径：注入真实异常，验证失败阶段归因准确
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_during_rule_check_records_correct_failed_stage(tmp_path, monkeypatch):
    """在"执行规则检查"阶段注入异常，断言 stage_failed_at 归因到该阶段
    （对应的规范阶段是 rule_ai_analysis），不是随便归到别的阶段。
    """
    job_dir = _prepare_success_job(tmp_path, monkeypatch, "job-fail-rules")
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(side_effect=RuntimeError("模拟规则引擎崩溃")),
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error", "M1 不变量：解析/规则失败必须落 error，不得改成 review_required"

    stage_failed_at = payload.get("stage_failed_at")
    assert stage_failed_at is not None, (
        "REGRESSION: 失败任务必须记录失败所在阶段，不能让 status.json 只有 status=error 而不知道败在哪"
    )
    assert stage_failed_at["phase"] == "rule_ai_analysis", (
        f"REGRESSION: 在'执行规则检查'阶段失败应归因到 rule_ai_analysis，"
        f"实际归因到了 {stage_failed_at['phase']!r}"
    )
    assert stage_failed_at["raw_stage"] == "执行规则检查", (
        "失败归因的原始 stage 文本应是失败发生前最后一次成功写入的那个阶段"
    )


@pytest.mark.asyncio
async def test_failure_during_pdf_build_records_correct_failed_stage(tmp_path, monkeypatch):
    """在"构建文档对象"阶段注入异常，断言归因到的阶段确实随失败点变化
    （对应规范阶段 pdf_parse），不是写死同一个阶段（与规则检查失败案例
    归因到 rule_ai_analysis 形成对照）。"""
    job_dir = _prepare_success_job(tmp_path, monkeypatch, "job-fail-build")

    def _raise_on_build_document(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("模拟构建文档对象失败")

    monkeypatch.setattr(pipeline_mod, "build_document", _raise_on_build_document)

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error"

    stage_failed_at = payload.get("stage_failed_at")
    assert stage_failed_at is not None
    assert stage_failed_at["phase"] == "pdf_parse", (
        f"REGRESSION: 在构建文档对象阶段失败应归因到 pdf_parse（而不是与规则检查失败"
        f"归因到同一个 rule_ai_analysis），实际归因到了 {stage_failed_at['phase']!r}"
    )
    assert stage_failed_at["raw_stage"] == "构建文档对象", (
        "'构建文档对象'对应的 _safe_write 先于 build_document() 调用完成写入，"
        "因此该次调用抛出的异常发生在'已经进入构建文档对象阶段之后'，"
        "最后一次成功记录的阶段就是'构建文档对象'本身（不是它的前一步），"
        "这与 test_failure_during_rule_check 里失败点落在'执行规则检查'"
        "阶段本身是同一种因果关系：_safe_write 先落地某阶段名，"
        "该阶段内部的实际工作函数随后才被调用并可能失败。"
    )


# ---------------------------------------------------------------------------
# null 语义反例：历史遗留任务不应被凭空编出 stage_progress
# ---------------------------------------------------------------------------


def test_legacy_job_without_stage_progress_field_stays_absent_not_fabricated(tmp_path):
    """历史任务的 status.json 从未写过 stage_progress 字段（本次改动之前的产物），
    读取时不应该被本次新增逻辑事后编出一个假值——`collect_job_summary` 只应该
    原样传递该字段是否存在，不做任何"没有就补一个"的操作。
    """
    job_dir = tmp_path / "job-legacy"
    job_dir.mkdir()
    (job_dir / "legacy.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": job_dir.name,
            "status": "done",
            "progress": 100,
            "stage": "完成",
            "ts": 1700000000.0,
            # 刻意不写 stage_progress / stage_failed_at，模拟本次改动前产生的历史任务
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary.get("stage_progress") is None, (
        "REGRESSION: 历史任务没有 stage_progress 字段时，摘要接口不得凭空补出一个非 None 的值"
    )
    assert summary.get("stage_failed_at") is None


# ---------------------------------------------------------------------------
# 回归：新增写入不破坏 M1 质量门禁与四态结论，不改变 final_status 判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_progress_addition_does_not_change_final_status_on_success(
    tmp_path, monkeypatch
):
    """M1 回归：新增的 stage_progress/stage_failed_at 写入必须是纯附加字段，
    不得影响 `_evaluate_quality_gate` 计算出的 final_status（即 quality_gate 判定
    的输入完全来自页面覆盖率/证据完整性/AI 降级等既有信号，与本次改动无关）。
    """
    job_dir = _prepare_success_job(tmp_path, monkeypatch, "job-final-status-unchanged")

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    # 干净输入（无扫描页、无证据降级、AI 未启用）下应该走到 done，
    # 这与 Task 3 之前的行为完全一致——如果这里不是 done，说明新增逻辑
    # 意外扰动了质量门禁判定。
    assert payload["status"] == "done", (
        f"REGRESSION: 新增阶段进度写入不应改变质量门禁的 final_status 判定，"
        f"预期 done，实际 {payload['status']!r}"
    )
    assert payload.get("analysis_conclusion") in {"no_findings", "findings_detected"}
    assert payload.get("quality_status") == "complete"


@pytest.mark.asyncio
async def test_stage_progress_addition_does_not_change_final_status_on_failure(
    tmp_path, monkeypatch
):
    """M1 回归：失败任务新增的 stage_failed_at 不改变既有的
    quality_status=review_required / analysis_conclusion=analysis_error 判定。"""
    job_dir = _prepare_success_job(tmp_path, monkeypatch, "job-final-status-unchanged-failure")
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error"
    assert payload.get("quality_status") == "review_required", (
        "REGRESSION: 失败任务的 quality_status 判定不应因新增 stage_failed_at 字段而改变"
    )
    assert payload.get("analysis_conclusion") == "analysis_error"
