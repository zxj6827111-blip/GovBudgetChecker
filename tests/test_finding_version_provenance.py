"""Task 7 / 缺口 P2-02：规则、模型、提示词、引擎版本随 finding 留痕。

断言意图分三层：
1. 版本值的来源必须真实（引擎版本=仓库声明版本；提示词版本随模板内容变化；
   模型版本取调用返回的实际提供商/模型，而不是配置里"打算用"的那个）；
2. 规则来源与 AI 来源分别只写自己能负责的字段，来源不明必须留 None 而不是占位值；
3. 版本字段能随结果快照落库读回，且历史（无这些字段的）快照仍可正常反序列化。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Dict, List

import pytest

import src.services.analysis_result_store as analysis_result_store
from src.engine.ai import extractor_client as extractor_module
from src.engine.ai.extractor_client import (
    FULL_REPORT_AUDIT_PROMPT_ID,
    ExtractorClient,
    full_report_audit_prompt_version,
    render_full_report_audit_instructions,
)
from src.engine.pipeline import _issue_to_dict
from src.engine.rules_v33 import Issue
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.engine_rule_runner import EngineRuleRunner
from src.utils.provenance import (
    DEFAULT_RULE_SET_VERSION,
    ENGINE_VERSION,
    build_model_version,
    prompt_version_from_template,
    read_finding_provenance,
    summarize_finding_versions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 版本值来源真实性
# --------------------------------------------------------------------------
def test_engine_version_matches_declared_project_version() -> None:
    """引擎版本必须等于仓库声明版本，而不是写死的字面量。"""
    declared = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert ENGINE_VERSION == declared
    assert ENGINE_VERSION not in {None, "", "unknown"}


def test_prompt_version_is_derived_from_template_content() -> None:
    """提示词版本由模板内容哈希派生：同模板稳定，改一个字就变。"""
    template = "审校提示词模板 A"
    first = prompt_version_from_template("demo", template)
    assert first == prompt_version_from_template("demo", template)
    assert first.startswith("demo@sha1:")
    # 反例：模板变化后版本必须变化，否则等于没有留痕
    assert first != prompt_version_from_template("demo", template + "。补充一句")


def test_full_report_audit_prompt_version_tracks_real_prompt() -> None:
    """全量审查提示词版本与实际渲染出的指令文本一致。"""
    instructions = render_full_report_audit_instructions()
    assert "中国政府预决算公开材料审校助手" in instructions
    # 指令部分不应包含待审文档正文占位符，否则版本会随文档变化
    assert "{section_text}" not in instructions
    assert full_report_audit_prompt_version() == prompt_version_from_template(
        FULL_REPORT_AUDIT_PROMPT_ID, instructions
    )


def test_build_model_version_never_fabricates() -> None:
    """模型标识：能拼就拼，两边都缺就留 None，不写 unknown 之类占位值。"""
    assert build_model_version("gemini_main", "gemini-2.5-pro") == "gemini_main/gemini-2.5-pro"
    assert build_model_version("", "gpt-4o-mini") == "gpt-4o-mini"
    assert build_model_version("gemini_main", "  ") == "gemini_main"
    assert build_model_version(None, None) is None
    assert build_model_version("", "") is None


@pytest.mark.asyncio
async def test_direct_semantic_audit_records_actual_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """直连审计记录的是响应里实际生效的提供商/模型，而不是配置值。

    客户端有熔断回退，配置的模型与实际用的模型可能不同；留痕必须反映实际。
    """
    client = ExtractorClient()
    monkeypatch.setattr(client.config, "audit_provider", "configured_provider")
    monkeypatch.setattr(client.config, "audit_model", "configured-model")

    payload = [
        {
            "problem_type": "ratio_recalc",
            "original": "同比增长12.5%",
            "suggestion": "复算同比",
            "span": [0, 8],
            "context": "同比增长12.5%",
            "severity": "high",
            "confidence": 0.9,
        }
    ]

    class _FakeAIClient:
        async def chat(self, **_kwargs: Any) -> Dict[str, Any]:
            # 实际生效的是回退后的提供商/模型
            return {
                "content": json.dumps(payload, ensure_ascii=False),
                "model": "actual-model",
                "provider_used": "actual_provider",
            }

    monkeypatch.setattr(client, "_get_direct_ai_client", lambda: _FakeAIClient())

    issues = await client._direct_semantic_audit("同比增长12.5%，实际应为8.3%。")

    assert len(issues) == 1
    provenance = read_finding_provenance(issues[0])
    assert provenance["model_version"] == "actual_provider/actual-model"
    assert provenance["model_version"] != "configured_provider/configured-model"
    assert provenance["prompt_version"] == full_report_audit_prompt_version()
    assert provenance["source_channel"] == "direct_llm"


@pytest.mark.asyncio
async def test_semantic_audit_service_path_records_task_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """走抽取服务时，提示词在远端，本地如实记录任务契约与服务回传模型。"""
    client = ExtractorClient()
    monkeypatch.setattr(client.config, "main_model", "requested-model")

    class _FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> Dict[str, Any]:
            return {
                "model": "service-model",
                "hits": [
                    {
                        "semantic_issues": [
                            {"original": "占位符残留", "context": "XX部门", "severity": "low"}
                        ]
                    }
                ],
            }

    class _FakeHTTPClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeHTTPClient":
            return self

        async def __aexit__(self, *_args: Any) -> bool:
            return False

        async def post(self, *_args: Any, **kwargs: Any) -> Any:
            assert kwargs["json"]["task"] == extractor_module.SEMANTIC_AUDIT_SERVICE_TASK
            return _FakeResponse()

    monkeypatch.setattr(extractor_module.httpx, "AsyncClient", _FakeHTTPClient)

    issues = await client._call_semantic_audit("XX部门", "hash-1")

    assert len(issues) == 1
    provenance = read_finding_provenance(issues[0])
    assert provenance["prompt_version"] == extractor_module.SEMANTIC_AUDIT_SERVICE_TASK
    assert provenance["model_version"] == "service-model"
    assert provenance["source_channel"] == "extractor_service"


# --------------------------------------------------------------------------
# 两类来源分别写入自己的版本字段
# --------------------------------------------------------------------------
def test_rule_finding_carries_rule_and_engine_version() -> None:
    """规则 finding：写规则集版本 + 引擎版本，不写模型/提示词版本。"""
    runner = EngineRuleRunner()
    issue = Issue(
        rule="V33-002",
        severity="high",
        message="三公经费合计与分项之和不一致",
        evidence_text="合计 35.20，其中因公出国 10.00",
        location={"page": 12},
    )

    finding = runner._issue_to_finding(issue, rule_id="V33-002", rule_version="v3_3")

    assert finding.source == "rule"
    assert finding.rule_version == "v3_3"
    assert finding.engine_version == ENGINE_VERSION
    # 反例：规则来源不得凭空带上模型/提示词版本
    assert finding.model_version is None
    assert finding.prompt_version is None


@pytest.mark.asyncio
async def test_engine_runner_uses_configured_rules_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """规则版本取自本次分析配置，而不是硬编码的默认值。"""
    runner = EngineRuleRunner()
    job_context = JobContext(
        job_id="job-version-1",
        pdf_path="budget.pdf",
        page_texts=["2024年部门预算公开"],
        page_tables=[[]],
    )
    config = AnalysisConfig(rules_version="v9_9_test")

    captured: List[Dict[str, Any]] = []
    original = runner._issue_to_finding

    def _spy(issue: Any, **kwargs: Any) -> IssueItem:
        captured.append(dict(kwargs))
        return original(issue, **kwargs)

    monkeypatch.setattr(runner, "_issue_to_finding", _spy)

    findings = await runner.run_rules(job_context=job_context, rules=[], config=config)

    assert captured, "规则集应当至少产出一条 finding 供断言"
    assert all(item["rule_version"] == "v9_9_test" for item in captured)
    assert all(finding.rule_version == "v9_9_test" for finding in findings)


def test_ai_finding_carries_model_and_prompt_version() -> None:
    """AI finding：写模型 + 提示词版本 + 引擎版本，不写规则集版本。"""
    service = AIFindingsService(AnalysisConfig())
    context = JobContext(job_id="job-ai-1", pdf_path="final.pdf")

    finding = service._build_ai_issue_item(
        raw_issue={
            "original": "同比增长12.5%",
            "context": "一般公共预算支出情况说明：同比增长12.5%。",
            "severity": "high",
            "confidence": 0.9,
            "provenance": {
                "model_version": "gemini_main/gemini-2.5-pro",
                "prompt_version": "full_report_audit_direct@sha1:deadbeef",
                "source_channel": "direct_llm",
            },
        },
        context=context,
        idx=0,
        page_number=3,
    )

    assert finding.source == "ai"
    assert finding.model_version == "gemini_main/gemini-2.5-pro"
    assert finding.prompt_version == "full_report_audit_direct@sha1:deadbeef"
    assert finding.engine_version == ENGINE_VERSION
    # AI 语义审计不是规则集命中，硬填规则版本会造成误导性归因
    assert finding.rule_version is None


def test_ai_finding_without_provenance_leaves_versions_empty() -> None:
    """来源不明时留 None，不伪造模型/提示词版本。"""
    service = AIFindingsService(AnalysisConfig())
    context = JobContext(job_id="job-ai-2", pdf_path="final.pdf")

    finding = service._build_ai_issue_item(
        raw_issue={"original": "占位符残留", "context": "XX部门", "severity": "low"},
        context=context,
        idx=1,
        page_number=1,
    )

    assert finding.model_version is None
    assert finding.prompt_version is None
    # 引擎版本与 AI 调用无关，始终可知
    assert finding.engine_version == ENGINE_VERSION


def test_legacy_pipeline_issue_dict_carries_versions() -> None:
    """legacy 分桶结构的 finding 也带版本，历史模式不留盲区。"""
    issue = Issue(
        rule="C-001",
        severity="high",
        message="三公经费合计不等于分项之和",
        evidence_text="合计 35.20",
        location={"page": 12},
    )

    data = _issue_to_dict(issue, 1)

    assert data["rule_version"] == DEFAULT_RULE_SET_VERSION
    assert data["engine_version"] == ENGINE_VERSION
    assert data["model_version"] is None
    assert data["prompt_version"] is None


# --------------------------------------------------------------------------
# 汇总、落库与向后兼容
# --------------------------------------------------------------------------
def test_summarize_finding_versions_deduplicates_and_skips_empty() -> None:
    summary = summarize_finding_versions(
        [
            {"rule_version": "v3_3", "engine_version": "0.1.0"},
            {"rule_version": "v3_3", "engine_version": "0.1.0"},
            {"model_version": "p/m2", "prompt_version": "pid@sha1:1", "engine_version": "0.1.0"},
            {"model_version": "p/m1", "prompt_version": None},
            {},
        ]
    )

    assert summary["rule_versions"] == ["v3_3"]
    assert summary["model_versions"] == ["p/m1", "p/m2"]
    assert summary["prompt_versions"] == ["pid@sha1:1"]
    assert summary["engine_versions"] == ["0.1.0"]
    assert summary["engine_version"] == ENGINE_VERSION


def test_issue_item_accepts_legacy_payload_without_version_fields() -> None:
    """向后兼容：历史快照没有版本字段时照样能反序列化，字段为 None。"""
    legacy = {
        "id": "rule-legacy-1",
        "source": "rule",
        "rule_id": "C-001",
        "severity": "high",
        "title": "历史问题",
        "message": "历史快照没有版本字段",
        "page_number": 5,
    }

    item = IssueItem.model_validate(legacy)

    assert item.rule_version is None
    assert item.model_version is None
    assert item.prompt_version is None
    assert item.engine_version is None
    # 展示字段仍可正常生成，旧任务详情页不会因缺字段报错
    assert item.display is not None


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: Any) -> bool:
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_calls: List[Any] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetchval(self, query: str, *_args: Any) -> Any:
        if "SELECT id FROM organizations" in query:
            return None
        return 42

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    async def fetchrow(self, _query: str, *_args: Any) -> Any:
        return None


@pytest.mark.asyncio
async def test_snapshot_persists_version_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """结果快照落库后能原样读回版本字段（findings 以 jsonb 整体存储）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    conn = _FakeConnection()

    async def _fake_ready() -> bool:
        return True

    async def _fake_acquire() -> _FakeConnection:
        return conn

    async def _fake_release(_connection: Any) -> None:
        return None

    monkeypatch.setattr(analysis_result_store, "ensure_analysis_persistence_ready", _fake_ready)
    monkeypatch.setattr(analysis_result_store.DatabaseConnection, "acquire", _fake_acquire)
    monkeypatch.setattr(analysis_result_store.DatabaseConnection, "release", _fake_release)

    payload = {
        "job_id": "job-versions-1",
        "status": "done",
        "filename": "sample.pdf",
        "mode": "dual",
        "result": {
            "ai_findings": [
                {
                    "id": "ai-1",
                    "source": "ai",
                    "model_version": "gemini_main/gemini-2.5-pro",
                    "prompt_version": "full_report_audit_direct@sha1:deadbeef",
                    "engine_version": "0.1.0",
                    "rule_version": None,
                }
            ],
            "rule_findings": [
                {
                    "id": "rule-1",
                    "source": "rule",
                    "rule_version": "v3_3",
                    "engine_version": "0.1.0",
                }
            ],
            "merged": {"totals": {"merged": 2}},
            "meta": {"versions": {"engine_version": "0.1.0", "rule_versions": ["v3_3"]}},
        },
    }

    assert await analysis_result_store.persist_analysis_job_snapshot(
        payload, include_results=True
    )

    result_calls = [
        call for call in conn.execute_calls if "INSERT INTO analysis_results" in call[0]
    ]
    assert len(result_calls) == 1
    _query, args = result_calls[0]

    stored_ai = json.loads(args[1])
    stored_rule = json.loads(args[2])
    stored_raw = json.loads(args[4])

    assert stored_ai[0]["model_version"] == "gemini_main/gemini-2.5-pro"
    assert stored_ai[0]["prompt_version"] == "full_report_audit_direct@sha1:deadbeef"
    assert stored_ai[0]["engine_version"] == "0.1.0"
    assert stored_rule[0]["rule_version"] == "v3_3"
    assert stored_raw["result"]["meta"]["versions"]["rule_versions"] == ["v3_3"]


class _FakePdf:
    def __init__(self, page_count: int) -> None:
        self.pages = [object()] * page_count

    def __enter__(self) -> "_FakePdf":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_pipeline_result_meta_aggregates_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：一次分析的结果 JSON 的 meta.versions 汇总各条 finding 的实际版本。"""
    from unittest.mock import AsyncMock

    from api import main as pipeline_mod
    from api import runtime

    job_dir = tmp_path / "job-versions-e2e"
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

    full_text = "一般公共预算财政拨款支出预算表" * 10
    issue_item = {
        "id": "C-001-1",
        "source": "rule",
        "rule_id": "C-001",
        "severity": "error",
        "rule_version": "v3_3",
        "model_version": None,
        "prompt_version": None,
        "engine_version": ENGINE_VERSION,
    }

    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(1))
    monkeypatch.setattr(
        pipeline_mod, "_extract_visible_text_from_page", lambda _page: full_text
    )
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": {"all": [issue_item], "error": [issue_item], "warn": [], "info": []}
            }
        ),
    )
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(
            return_value={"status": "skipped", "review_item_count": 0, "review_items": []}
        ),
    )
    monkeypatch.setattr(pipeline_mod.settings, "get", lambda *_args: False)

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    versions = payload["result"]["meta"]["versions"]

    assert versions["engine_version"] == ENGINE_VERSION
    assert versions["rule_versions"] == ["v3_3"]
    assert versions["engine_versions"] == [ENGINE_VERSION]
    # 本次未启用 AI，模型/提示词版本必须为空列表而不是占位值
    assert versions["model_versions"] == []
    assert versions["prompt_versions"] == []

