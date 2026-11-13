"""调度完成不等于识别完成；运行统计不能再次写同一历史版本。"""

import pytest


@pytest.mark.asyncio
async def test_runtime_timing_does_not_rewrite_immutable_analysis_revision(
    monkeypatch, tmp_path, caplog
):
    from api import main, runtime

    saved = []

    async def body(directory):
        main._safe_write(
            directory,
            {
                "job_id": directory.name,
                "status": "review_required",
                "analysis_revision": "rev-exact",
                "result": {"rule_findings": []},
            },
        )
        saved.append((directory / "analysis_versions/rev-exact/status.json").read_bytes())

    monkeypatch.setattr(main, "_run_pipeline_inner", body)
    runtime.write_json_file(tmp_path / "status.json", {"status": "processing"})
    await main._run_pipeline(tmp_path)
    assert (tmp_path / "analysis_versions/rev-exact/status.json").read_bytes() == saved[0]
    assert (
        runtime.read_json_file(tmp_path / "status.json")["pipeline_budget"]["outcome"]
        == "completed"
    )
    assert "analysis revision snapshot failed" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publication,outcome", [(None, "checkpointed"), ("pending_retry", "publication_pending")]
)
async def test_ocr_or_database_checkpoint_is_not_labeled_completed(
    monkeypatch, tmp_path, publication, outcome
):
    from api import main, runtime

    async def body(directory):
        runtime.write_json_file(
            directory / "status.json", {"status": "processing", "input_retry_at": 1}
        )
        if publication:
            runtime.write_json_file(directory / "fact_publication.json", {"status": publication})

    monkeypatch.setattr(main, "_run_pipeline_inner", body)
    runtime.write_json_file(tmp_path / "status.json", {"status": "queued"})
    await main._run_pipeline(tmp_path)
    status = runtime.read_json_file(tmp_path / "status.json")
    assert status["status"] == "processing" and status["pipeline_budget"]["outcome"] == outcome
    assert not (tmp_path / "analysis_versions/current.json").exists()
