"""前置修复 2：队列表"耗时"列的后端数据来源测试。

背景：`WorkbenchQueueTable.tsx` 的列名是"耗时/页数"，但 `formatElapsedAndPages`
只渲染了页数，耗时缺失——因为 `collect_job_summary` 此前从未把 `result.meta`
里的 `started_at`/`finished_at`/`elapsed_ms` 提取到摘要接口。

口径（真实历史数据实测，786 个任务目录，见交付说明）：用 `finished_at - started_at`
计算耗时，在"确实跑过分析"的任务子集（done/review_required/error）里覆盖率
97.6%，明显高于 `elapsed_ms.total` 的覆盖率（约 53%），因此本模块优先用
started_at/finished_at 差值，elapsed_ms.total 仅作兜底。

正反对照：
- 正例：两个时间戳都存在时，必须按差值计算出正确的毫秒数；
- 正例：只有 elapsed_ms.total 而没有 started_at/finished_at 时，用它兜底；
- 反例（核心）：两者都缺失时必须是 None，不得显示 0 或任何猜测值——
  这是"null（未知）与 0（真实数据）必须严格区分"这条红线在耗时字段上的落地。
- 反例：finished_at 小于 started_at（脏数据/时钟回退）时不应算出负数耗时，
  必须视为不可信，回退到 elapsed_ms.total 或 None。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from api import runtime


def _write_job(tmp_path: Path, job_id: str, status_payload: Dict[str, Any]) -> Path:
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "sample.pdf").write_bytes(b"%PDF-1.4 fake")
    (job_dir / "status.json").write_text(
        json.dumps(status_payload, ensure_ascii=False), encoding="utf-8"
    )
    return job_dir


def test_elapsed_ms_computed_from_started_and_finished_at(tmp_path):
    """正例：started_at/finished_at 都存在时，必须按差值算出毫秒数。"""
    job_dir = _write_job(
        tmp_path,
        "job-elapsed-ok",
        {
            "job_id": "job-elapsed-ok",
            "status": "done",
            "progress": 100,
            "result": {
                "meta": {"started_at": 1000.0, "finished_at": 1012.5},
            },
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary["elapsed_ms"] == 12500, (
        f"REGRESSION: 12.5 秒应算出 12500 毫秒，实际 {summary['elapsed_ms']!r}"
    )


def test_elapsed_ms_falls_back_to_elapsed_ms_total_when_timestamps_missing(tmp_path):
    """正例：没有 started_at/finished_at，但双模式分析写了 elapsed_ms.total 时兜底使用它。"""
    job_dir = _write_job(
        tmp_path,
        "job-elapsed-fallback",
        {
            "job_id": "job-elapsed-fallback",
            "status": "done",
            "progress": 100,
            "result": {
                "meta": {"elapsed_ms": {"total": 8000, "rule": 3000, "ai": 5000}},
            },
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary["elapsed_ms"] == 8000, (
        f"REGRESSION: 缺时间戳但有 elapsed_ms.total 时应兜底使用它，实际 {summary['elapsed_ms']!r}"
    )


def test_elapsed_ms_is_none_when_both_sources_missing(tmp_path):
    """反例（核心）：两个数据源都拿不到时必须是 None，不得显示 0 或猜测值。

    这是历史任务的真实分布——真实回放显示大量历史任务（uploaded 状态，
    从未真正跑过分析）完全没有 result 字段，此时耗时"不存在"是真实事实，
    不是缺陷，UI 必须显示"—"。
    """
    job_dir = _write_job(
        tmp_path,
        "job-elapsed-none",
        {
            "job_id": "job-elapsed-none",
            "status": "uploaded",
            "progress": 0,
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary["elapsed_ms"] is None, (
        f"REGRESSION: 没有 started_at/finished_at 也没有 elapsed_ms.total 时必须是 None，"
        f"不得显示 0 或任何猜测值，实际 {summary['elapsed_ms']!r}"
    )


def test_elapsed_ms_ignores_negative_duration_from_bad_clock_data(tmp_path):
    """反例：finished_at 早于 started_at（脏数据）时不应算出负数耗时，
    必须回退到 elapsed_ms.total 兜底（若有）或 None（若无）。
    """
    job_dir = _write_job(
        tmp_path,
        "job-elapsed-negative",
        {
            "job_id": "job-elapsed-negative",
            "status": "done",
            "progress": 100,
            "result": {
                "meta": {
                    "started_at": 2000.0,
                    "finished_at": 1000.0,  # 时钟回退/脏数据
                    "elapsed_ms": {"total": 5000},
                },
            },
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary["elapsed_ms"] == 5000, (
        f"REGRESSION: finished_at < started_at 时不应信任时间戳差值（会算出负数），"
        f"必须回退到 elapsed_ms.total 兜底，实际 {summary['elapsed_ms']!r}"
    )


def test_elapsed_ms_zero_duration_is_real_not_unknown(tmp_path):
    """正例：started_at 与 finished_at 相等（真实的近乎瞬时完成）时应是 0，
    不能因为"0 看起来像空值"而被误判成 None——这是 null 与 0 严格区分的另一面。
    """
    job_dir = _write_job(
        tmp_path,
        "job-elapsed-zero",
        {
            "job_id": "job-elapsed-zero",
            "status": "done",
            "progress": 100,
            "result": {
                "meta": {"started_at": 1000.0, "finished_at": 1000.0},
            },
        },
    )

    summary = runtime.collect_job_summary(job_dir)

    assert summary["elapsed_ms"] == 0, (
        f"REGRESSION: started_at == finished_at 时耗时应是真实的 0，不应被误判为 None，"
        f"实际 {summary['elapsed_ms']!r}"
    )
