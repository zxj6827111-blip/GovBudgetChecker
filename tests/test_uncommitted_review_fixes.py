"""未提交审查的四项回归：缺事实降级、规则终止、映射缓存与主管严重度。"""

import asyncio
from copy import deepcopy
import multiprocessing
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency, CMM002_TextAnomalyRule
from src.engine.fact_context import apply_table_column_mapping_overlays, build_fact_context_from_artifact
from src.schemas.issues import AnalysisConfig, JobContext
from src.schemas.parse_artifact import ParseArtifact
from src.services import analyze_dual, structured_ingest_runner, supervision_query
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.review_problem_set import ReviewProblem
from src.services.rule_process import RuleExecutionTimeout, run_engine_rules_in_process
from src.services.supervision_metrics import normalize_finding, summarize
from tests.remediation.test_rc_batch1 import _artifact_with, _instance_for, _make_fragment
from tests.test_supervision import item


@pytest.mark.asyncio
async def test_no_fiscal_facts_still_runs_text_rules_and_reports_missing_operands(monkeypatch):
    artifact = ParseArtifact(source_file_hash="a" * 64, page_count=1)
    context = JobContext(job_id="text-only", pdf_path="text-only.pdf",
                         page_texts=["2026年测试局预算预算公开说明。"], page_tables=[[]],
                         parse_artifact=artifact.to_dict(), meta={"report_kind": "budget"})
    assert build_fact_context_from_artifact(artifact) is None
    runner = EngineRuleRunner()
    monkeypatch.setattr(runner, "_select_rule_set", lambda *_: [
        CMM002_TextAnomalyRule(), CMM001_ThreePublicNarrativeConsistency(),
    ])
    document = await runner._prepare_document(context)
    assert document.parse_artifact.source_file_hash == artifact.source_file_hash
    assert document.fact_context is None
    findings = await runner.run_rules(context, [], AnalysisConfig())
    assert any(f.rule_id == "CMM-002" for f in findings)
    summary = runner.get_rule_execution_summary()
    assert summary["insufficient_data"] == 1
    assert any(row["rule_id"] == "CMM-001" for row in summary["unresolved_rules"])


@pytest.mark.asyncio
async def test_explicit_corrupted_fact_snapshot_cannot_use_missing_facts_fallback():
    artifact = ParseArtifact(source_file_hash="a" * 64, page_count=1)
    context = JobContext(job_id="corrupted", pdf_path="unused.pdf", page_texts=["正文"],
                         parse_artifact=artifact.to_dict(), fact_context={"facts": ["invalid"]})
    with pytest.raises(ValueError, match="快照损坏"):
        await EngineRuleRunner()._prepare_document(context)


class BlockingRunner(EngineRuleRunner):
    """真实阻塞子进程，支持 spawn；不能用可取消的假协程替代规则 apply。"""

    async def run_rules(self, job_context, rules, config):
        marker = Path(job_context.meta["marker"])
        marker.write_text("started")
        time.sleep(30)
        marker.write_text("unexpectedly finished")
        return []


class TextRulesRunner(EngineRuleRunner):
    def _select_rule_set(self, *_):
        return [CMM002_TextAnomalyRule(), CMM001_ThreePublicNarrativeConsistency()]


@pytest.mark.asyncio
async def test_engine_spawn_preserves_findings_stats_and_unfinished_checks(monkeypatch):
    monkeypatch.setenv("RULES_PROCESS_START_METHOD", "spawn")
    runner = TextRulesRunner()
    context = JobContext(job_id="text-only", pdf_path="text-only.pdf",
        page_texts=["2026年测试局预算预算公开说明。"], page_tables=[[]],
        parse_artifact=ParseArtifact(source_file_hash="a" * 64, page_count=1).to_dict(),
        meta={"report_kind": "budget"})
    findings = await run_engine_rules_in_process(runner, context, [], AnalysisConfig(), 30)
    assert any(f.rule_id == "CMM-002" for f in findings)
    assert runner.get_stats()["total_findings"] == len(findings)
    summary = runner.get_rule_execution_summary()
    assert summary["insufficient_data"] == 1
    assert summary["total_rules"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("start_method", ["fork", "spawn"])
@pytest.mark.parametrize("cause", ["cancel", "timeout"])
async def test_engine_rule_process_is_reaped_before_failure_returns(
    start_method, cause, monkeypatch, tmp_path,
):
    if start_method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{start_method} unavailable")
    monkeypatch.setenv("RULES_PROCESS_START_METHOD", start_method)
    marker = tmp_path / "execution-marker"
    context = JobContext(job_id="blocked", pdf_path="unused.pdf", meta={"marker": str(marker)})
    runner = BlockingRunner()
    stats_before = runner.get_stats()
    task = asyncio.create_task(run_engine_rules_in_process(
        runner, context, [], AnalysisConfig(), 2 if cause == "timeout" else 30,
    ))
    try:
        deadline = time.monotonic() + 8
        while not marker.exists():
            assert not task.done(), "必须实际进入阻塞规则，才能证明执行被终止"
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        if cause == "cancel":
            task.cancel()
        expected = asyncio.CancelledError if cause == "cancel" else RuleExecutionTimeout
        with pytest.raises(expected):
            await asyncio.wait_for(task, 8)
        assert not any(p.name == "govbudget-engine-rule-evaluator" and p.is_alive()
                       for p in multiprocessing.active_children())
        assert marker.read_text() == "started"
        assert runner.get_stats() == stats_before, "失败时不能发布半成品统计"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_dual_rule_timeout_does_not_rerun_or_report_success(monkeypatch):
    analyzer = analyze_dual.DualModeAnalyzer()
    monkeypatch.setattr(analyzer, "_load_rules", AsyncMock(return_value=[{"executor": "rule"}]))
    monkeypatch.setattr(analyzer, "_separate_rules", lambda _: ([], [{}]))
    execute = AsyncMock(side_effect=RuleExecutionTimeout("test deadline"))
    fallback = AsyncMock(return_value=[])
    monkeypatch.setattr(analyzer, "_run_engine_analysis", execute)
    monkeypatch.setattr(analyzer, "_fallback_engine_only", fallback)
    monkeypatch.setattr(analyze_dual, "save_snapshot", lambda *_: None)
    with pytest.raises(RuntimeError, match="local_rules_failed"):
        await analyzer._analyze(JobContext(job_id="timeout", pdf_path="unused.pdf"), AnalysisConfig())
    execute.assert_awaited_once()
    fallback.assert_not_awaited()


def mapping_artifact():
    fragment = _make_fragment("FIN_01_income_expenditure_total", [
        ["项目", "本年收入", "本年支出"], ["合计", "100.00", "90.00"],
    ])
    artifact = _artifact_with([fragment], [_instance_for(fragment)], file_hash="a" * 64)
    artifact.config_fingerprint = "original-config"
    return artifact


def mapping_records(artifact, *, column=2, record_id="human-mapping"):
    # 人工改正列身份；列名仍必须与原始表头相符，金额格不能重写。
    return [{"record_id": record_id, "changes": [{
        "target_type": "table_instance", "target_id": artifact.logical_tables[0].instance_uid,
        "field": "column_mapping", "value": {"canonical_measure": "income_actual", "source_col_idx": column},
    }]}]


def test_mapping_changes_cache_identity_idempotently_and_keeps_original_cells():
    artifact = mapping_artifact()
    original = deepcopy(artifact.to_dict())
    records = mapping_records(artifact)
    assert apply_table_column_mapping_overlays(artifact, records)["applied"] == 1
    identity = (artifact.config_fingerprint, artifact.parse_run_uid)
    assert identity != (original["config_fingerprint"], original["parse_run_uid"])
    assert artifact.to_dict()["table_fragments"] == original["table_fragments"]
    apply_table_column_mapping_overlays(artifact, records)
    assert (artifact.config_fingerprint, artifact.parse_run_uid) == identity
    fresh = ParseArtifact.from_dict(original)
    apply_table_column_mapping_overlays(fresh, records)
    assert (fresh.config_fingerprint, fresh.parse_run_uid) == identity
    apply_table_column_mapping_overlays(artifact, mapping_records(artifact, column=1, record_id="revised"))
    assert (artifact.config_fingerprint, artifact.parse_run_uid) != identity


def test_column_mapping_snapshot_preserves_original_interpretation_and_rejects_mutation():
    from src.services.analysis_fact_snapshot import bind_raw_interpretation, make_snapshot
    artifact = mapping_artifact()
    raw = build_fact_context_from_artifact(artifact, org_name="测试局", fiscal_year=2026, report_kind="final")
    before = deepcopy(raw.to_dict())
    report = apply_table_column_mapping_overlays(artifact, mapping_records(artifact))
    bind_raw_interpretation(artifact, raw, report)
    effective = build_fact_context_from_artifact(artifact, org_name="测试局", fiscal_year=2026, report_kind="final")
    snapshot = make_snapshot(job_uuid='job', revision_id='r', artifact=artifact,
                             raw_context=raw, effective_context=effective)
    assert snapshot['raw_context'] == before
    assert snapshot['raw_facts_hash'] != snapshot['effective_facts_hash']
    assert raw.parse_run_uid != effective.parse_run_uid
    raw.facts[0].raw_value = '999'
    with pytest.raises(ValueError, match='raw fact context'):
        make_snapshot(job_uuid='job', revision_id='r', artifact=artifact,
                      raw_context=raw, effective_context=effective)


@pytest.mark.asyncio
async def test_corrected_mapping_cannot_hit_uncorrected_ingest_cache(monkeypatch, tmp_path):
    runner = structured_ingest_runner
    artifact = mapping_artifact()
    cached = deepcopy(artifact)
    apply_table_column_mapping_overlays(artifact, mapping_records(artifact))
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF")
    artifact.source_file_hash = runner._sha256_file(source)
    cached.source_file_hash = artifact.source_file_hash
    rules = build_fact_context_from_artifact(artifact, org_name="测试局", fiscal_year=2026, report_kind="final")
    stored_facts, persisted_artifacts = [], []

    async def cache_lookup(*_, config_fingerprint, **__):
        return 42 if config_fingerprint == cached.config_fingerprint else None

    async def persist_artifact(_conn, parsed, **_):
        persisted_artifacts.append(parsed)
        return 43

    async def persist_facts(_conn, *, facts, **_):
        stored_facts.extend(facts)
        return len(facts)

    class Parser:
        def __init__(self, _conn): pass
        async def persist_artifact_to_legacy_tables(self, *_):
            return {"success": True, "tables_count": 1}

    class Recognizer:
        def __init__(self, _conn): pass
        async def recognize_tables(self, *_): return []
        async def save_table_instances(self, *_): pass

    class Materializer:
        def __init__(self, _conn): pass
        async def materialize(self, *_): return {"facts_count": 1}

    class Sync:
        def __init__(self, _conn): pass
        async def sync(self, **_): return {"status": "skipped"}

    for name, replacement in {
        "ensure_structured_ingest_ready": AsyncMock(return_value=True),
        "_ensure_document_version": AsyncMock(return_value={"document_id": 1, "document_version_id": 1}),
        "_allocate_material_slot": AsyncMock(return_value={}),
        "find_cached_parse_run": cache_lookup, "load_parse_artifact": AsyncMock(side_effect=lambda _conn, run_id: cached if run_id == 42 else persisted_artifacts[-1]),
        "persist_parse_artifact": persist_artifact, "persist_facts_and_evidence": persist_facts,
        "verify_persisted_facts": AsyncMock(return_value={"verified": True}),
        "PDFParser": Parser, "TableRecognizer": Recognizer, "FiscalFactMaterializer": Materializer,
        "PSSharedSchemaSync": Sync, "_backfill_local_organization_catalog": lambda **_: None,
    }.items():
        monkeypatch.setattr(runner, name, replacement)
    monkeypatch.setattr(runner.DatabaseConnection, "acquire", AsyncMock(return_value=object()))
    monkeypatch.setattr(runner.DatabaseConnection, "release", AsyncMock())
    result = await runner.run_structured_ingest("corrected", source, {
        "checksum": artifact.source_file_hash, "organization_name": "测试局",
        "report_year": 2026, "report_kind": "final",
    }, parse_artifact=artifact)
    assert result["status"] == "done", result
    assert result["parse_cache_hit"] is False
    runner.load_parse_artifact.assert_awaited_once()
    assert runner.load_parse_artifact.await_args.args[1] == 43, "只回读新映射对应的新运行，不能读取旧缓存"
    assert persisted_artifacts == [artifact]
    assert [(f.fact_uid, f.indicator, f.normalized_value) for f in stored_facts] == [
        (f.fact_uid, f.indicator, f.normalized_value) for f in rules.facts
    ]
    assert stored_facts[0].normalized_value == 90, "必须落库人工指定列的金额"


def test_supervisor_normalizes_new_snapshots_and_legacy_stats_without_rewriting():
    problems = [ReviewProblem("a", "finding", "收入合计不一致", "error", True),
                ReviewProblem("b", "finding", "变化原因缺失", "warn", True)]
    findings = supervision_query.confirmed_snapshot(SimpleNamespace(
        problems=problems, decisions={"a": "confirmed", "b": "confirmed"}, obligation_decisions={},
    ))
    assert [f["severity"] for f in findings] == ["high", "medium"]
    legacy = {"issue_id": "old", "title": "收入合计不一致", "severity": "error"}
    assert normalize_finding(legacy)["severity"] == "high"
    assert legacy["severity"] == "error"
    _, departments = summarize([item(confirmed_count=1, findings=[legacy])], None)
    assert departments[0]["severe"] == 1


@pytest.mark.asyncio
async def test_supervisor_normalizes_completed_history_for_detail_and_filter(monkeypatch):
    legacy = {"issue_id": "old", "title": "收入合计不一致", "severity": "error"}
    historical = {"session_id": "review", "document_version_id": 1, "analysis_basis_token": "job:1",
                  "completed_at": "2026-10-13T00:00:00Z", "completed_by": "reviewer",
                  "review_result": {"confirmed_findings": [legacy]}}
    material = item(findings=[], filename="source.pdf", job_id="job", _sessions=[historical], _rounds=[])
    monkeypatch.setattr(supervision_query, "load_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(supervision_query, "build_items", AsyncMock(return_value=[material]))
    conn = SimpleNamespace(fetch=AsyncMock(return_value=[{
        "id": 1, "original_filename": "source.pdf", "created_at": "2026-10-12T00:00:00Z",
    }]))
    detail = await supervision_query.material_detail(conn, "slot-a", None)
    assert detail["reviews"][0]["findings"][0]["severity"] == "high"
    assert historical["review_result"]["confirmed_findings"][0]["severity"] == "error"
    material["findings"] = detail["reviews"][0]["findings"]
    result = await supervision_query.query(conn, visible_org_ids=None, scope_ids=None, scope_id="",
        fiscal_year=2026, report_kind="final", view="issues", severity="high")
    assert result["list"]["total"] == 1

