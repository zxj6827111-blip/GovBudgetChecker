"""ZC-00 阶段验收测试：固定基线，修复假证据和误导性结论。

对应缺陷：SYS-07（未知页码转第 1 页、问题说明充当原文、证据门只查非空）、
SYS-12（Word 零问题写"未发现明显问题"、下载不保留未完成范围），以及
"默认关闭 AI 分析、定位及回退调用"的启动纪律。

必须验证（任务包 §三 ZC-00）：
1. 两页文档引用第 999 页：不能通过证据核验。
2. 只有生成说明、没有原文：页码保持空，不能成为正式证据。
3. 当前已完成检查确实无问题：允许有限范围的无问题结论。
4. 问题为空但检查未完成：页面和下载均显示未完成。
5. 关闭 AI 时，测试拦截器证明没有模型网络请求。

反例来源：outputs/system_assessment_20261001/（评估报告 §四.6 证据反例），
全部以合成输入在测试内重建，不依赖被 .zcodeignore 排除的产物文件。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.services.engine_rule_runner import EngineRuleRunner
from src.engine.rules_v33 import Issue
from src.services.evidence_guard import (
    EVIDENCE_STATUS_DEGRADED,
    EVIDENCE_STATUS_RULE_WARNING,
    REASON_PAGE_OUT_OF_RANGE,
    REASON_TEXT_NOT_FOUND_ON_PAGE,
    apply_evidence_completeness,
    evaluate_finding_evidence,
    is_formal_finding,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 1. 证据门：真实页数边界 + 原文引用核验
# ---------------------------------------------------------------------------

TWO_PAGE_TEXTS = [
    "一、收入支出决算总体情况说明\n本年收入合计 100.00 万元。",
    "二、三公经费支出决算情况说明\n三公经费合计 35.20 万元。",
]
TEST_SOURCE_HASH = hashlib.sha256(b"zc00-evidence-test-source").hexdigest()


def _source_ref(page: int, quote: str, *, source_id: str | None = None) -> Dict[str, Any]:
    source_id = source_id or f"page-{page}-text-block"
    evidence_id = hashlib.sha1(
        f"{TEST_SOURCE_HASH}|{page}|{source_id}|{quote}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "page": page,
        "physical_page": page,
        "text": quote,
        "quote": quote,
        "file_hash": TEST_SOURCE_HASH,
        "source_block_id": source_id,
        "source_identity_status": "verified",
        "evidence_id": evidence_id,
    }


def test_two_page_doc_referencing_page_999_fails_verification() -> None:
    """反例：两页文档引用第 999 页——不能通过证据核验。"""
    finding = {
        "id": "fake-999",
        "source": "rule",
        "rule_id": "C-001",
        "page_number": 999,
        "evidence": [_source_ref(999, "三公经费合计 35.20 万元")],
    }
    complete, missing, verification = evaluate_finding_evidence(
        finding, document_page_count=2, page_texts=TWO_PAGE_TEXTS
    )
    assert complete is False
    assert REASON_PAGE_OUT_OF_RANGE in missing
    assert verification["status"] == "failed"


def test_quote_missing_from_claimed_page_fails_verification() -> None:
    """反例：页面存在但引用文本在该页文本层中不存在——伪造原文，判失败。"""
    finding = {
        "id": "fake-quote",
        "source": "ai",
        "rule_id": "AI-SEM-1",
        "page_number": 2,
        "evidence": [_source_ref(2, "差旅费 88.88 万元")],
    }
    complete, missing, verification = evaluate_finding_evidence(
        finding, document_page_count=2, page_texts=TWO_PAGE_TEXTS
    )
    assert complete is False
    assert REASON_TEXT_NOT_FOUND_ON_PAGE in missing
    assert verification["status"] == "failed"


def test_quote_verified_on_claimed_page_passes() -> None:
    """正例：引用确实出现在所称页面（空白差异允许折叠）——核验通过。"""
    finding = {
        "id": "real-quote",
        "source": "rule",
        "rule_id": "C-001",
        "page_number": 2,
        "evidence": [_source_ref(2, "三公经费合计  35.20 万元。")],
    }
    complete, missing, verification = evaluate_finding_evidence(
        finding, document_page_count=2, page_texts=TWO_PAGE_TEXTS,
        source_file_hash=TEST_SOURCE_HASH,
    )
    assert complete is True
    assert verification["status"] == "verified"
    assert missing == ["missing_bbox"]


def test_scanned_page_without_text_layer_is_unverified_not_failed() -> None:
    """扫描页没有文本层：无法核验要如实记录，但不能判"伪造原文"。"""
    finding = {
        "id": "scan-page",
        "source": "rule",
        "rule_id": "C-001",
        "page_number": 1,
        "evidence": [_source_ref(1, "本年收入合计 100.00 万元")],
    }
    complete, _missing, verification = evaluate_finding_evidence(
        finding, document_page_count=1, page_texts=[""],
        source_file_hash=TEST_SOURCE_HASH,  # 扫描页：无文本层
    )
    assert complete is True
    assert verification["status"] == "unverified"
    assert any("no_text_layer" in reason for reason in verification["reasons"])


def test_no_page_texts_available_records_unverified() -> None:
    """未提供页文本（旧链路）：结构照旧判定，核验状态如实记 unverified。"""
    finding = {
        "id": "no-texts",
        "source": "rule",
        "rule_id": "C-001",
        "page_number": 3,
        "evidence": [_source_ref(3, "合计 35.20")],
    }
    complete, _missing, verification = evaluate_finding_evidence(
        finding, source_file_hash=TEST_SOURCE_HASH
    )
    assert complete is True
    assert verification["status"] == "unverified"


def test_apply_completeness_records_verification_counts() -> None:
    """度量层：verified/unverified/failed 分别记账，越界条目按缺证据处理。"""
    result = {
        "rule_findings": [
            {
                "id": "ok",
                "source": "rule",
                "rule_id": "C-001",
                "page_number": 2,
                "evidence": [_source_ref(2, "三公经费合计 35.20 万元")],
            },
            {
                "id": "beyond",
                "source": "rule",
                "rule_id": "C-001",
                "page_number": 999,
                "evidence": [_source_ref(999, "三公经费合计 35.20 万元")],
            },
        ]
    }
    completeness = apply_evidence_completeness(
        result, document_page_count=2, page_texts=TWO_PAGE_TEXTS,
        source_file_hash=TEST_SOURCE_HASH,
    )
    assert completeness["total"] == 2
    assert completeness["complete"] == 1
    assert completeness["verified_count"] == 1
    assert completeness["failed_count"] == 1
    assert completeness["rule_warning_count"] == 1
    assert result["rule_findings"][0]["evidence_verification"]["status"] == "verified"
    assert result["rule_findings"][1]["evidence_verification"]["status"] == "failed"


# ---------------------------------------------------------------------------
# 2. 规则执行器：未知页码保持空、说明不充当原文（SYS-07）
# ---------------------------------------------------------------------------


def _runner_finding(issue: Issue):
    runner = EngineRuleRunner()
    return runner._issue_to_finding(issue, rule_id="TEST-900", document=None, rule_version=None)


def test_issue_without_page_or_quote_keeps_page_none() -> None:
    """反例（评估 §四.6）：没有原文和定位的规则项——不得转成第 1 页。"""
    issue = Issue(rule="TEST-900", severity="high", message="疑似三公经费口径不一致")
    finding = _runner_finding(issue)
    assert finding.page_number is None
    assert finding.location.get("page") is None
    # 证据列表里没有把 message 当作原文填进去
    for item in finding.evidence:
        assert not str(item.get("text") or "").strip()
    # 证据门按缺证据记账：规则来源告警，不得冒充证据完整
    result = {"rule_findings": [finding.model_dump()]}
    completeness = apply_evidence_completeness(result)
    assert completeness["rule_warning_count"] == 1
    assert result["rule_findings"][0]["evidence_status"] == EVIDENCE_STATUS_RULE_WARNING


def test_issue_without_quote_keeps_message_out_of_evidence() -> None:
    """反例：有页码但没有原文——message 不得成为证据文本。"""
    issue = Issue(
        rule="TEST-900",
        severity="medium",
        message="说明金额与表格不一致",
        location={"page": 4},
    )
    finding = _runner_finding(issue)
    assert finding.page_number == 4
    assert all(not str(item.get("text") or "").strip() for item in finding.evidence)


def test_issue_with_real_quote_keeps_quote_in_evidence() -> None:
    """正例：规则给出真实命中原文时照常保留。"""
    issue = Issue(
        rule="TEST-900",
        severity="medium",
        message="三公经费合计不一致",
        evidence_text="三公经费合计 35.20 万元",
        location={"page": 2},
    )
    finding = _runner_finding(issue)
    assert finding.evidence[0]["text"] == "三公经费合计 35.20 万元"


def test_normalize_page_rejects_invalid_values() -> None:
    """页码归一：0/负数/非数字一律 None，正整数原样保留。"""
    normalize = EngineRuleRunner._normalize_page
    assert normalize(3) == 3
    assert normalize("7") == 7
    assert normalize(0) is None
    assert normalize(-2) is None
    assert normalize(None) is None
    assert normalize("abc") is None
    assert normalize(True) is None


def test_ai_finding_without_page_is_not_coerced_to_page_one() -> None:
    """AI 侧同样不得把缺页码折算成第 1 页。"""
    from src.services.ai_findings import AIFindingsService
    from src.schemas.issues import JobContext

    service = AIFindingsService.__new__(AIFindingsService)
    service.ai_errors = []
    context = JobContext(job_id="j", pdf_path="x.pdf")
    item = service._convert_ai_issue(
        {"title": "疑似口径问题", "message": "没有页码", "severity": "high"},
        context,
        idx=0,
    )
    assert item is not None
    assert item.page_number is None


def test_display_evidence_text_does_not_fall_back_to_message() -> None:
    """展示层"原文摘录"不得回退到 message（说明≠原文）。"""
    from src.utils.issue_display import build_issue_display

    payload = {
        "id": "d1",
        "rule_id": "TEST-900",
        "source": "rule",
        "severity": "medium",
        "title": "疑似口径不一致",
        "message": "这是问题说明，不是原文引用",
        "location": {"page": 2},
        "evidence": [],
    }
    display = build_issue_display(payload)
    assert "问题说明" not in (display.get("evidence_text") or "")


# ---------------------------------------------------------------------------
# 3. 零问题导出措辞（SYS-12）
# ---------------------------------------------------------------------------


def _status_payload(
    *,
    unfinished: List[Dict[str, Any]],
    analysis_conclusion: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "job_id": "job-zc00",
        "filename": "sample.pdf",
        "mode": "legacy",
        "issues": [],
    }
    if analysis_conclusion is not None:
        payload["analysis_conclusion"] = analysis_conclusion
    if unfinished or analysis_conclusion is not None:
        payload["check_coverage"] = {
            "instances": unfinished,
            "applicable_total": len(unfinished) + 10,
        }
    return payload


def _unfinished_item() -> Dict[str, Any]:
    return {
        "obligation_id": "OBL-1",
        "group_title": "三公经费分项核对",
        "title": "公务用车购置费",
        "status": "insufficient_data",
        "reason_label": "数据不足",
        "detail": "文本只披露合计，无法核对分项",
    }


def test_docx_zero_issue_with_unfinished_checks_does_not_claim_no_problem() -> None:
    """反例：问题为空但检查未完成——不得写"未发现明显问题"。"""
    from api.routes.reports import _build_docx_document_xml

    payload = _status_payload(
        unfinished=[_unfinished_item()], analysis_conclusion="incomplete"
    )
    xml = _build_docx_document_xml(job_id="job-zc00", status_payload=payload, issues=[])
    assert "未发现明显问题" not in xml
    assert "未完成" in xml
    assert "三公经费分项核对" in xml  # 未完成清单要列出来


def test_docx_zero_issue_legacy_without_coverage_is_cautious() -> None:
    """旧版记录没有覆盖台账：不得断言"未发现明显问题"。"""
    from api.routes.reports import _build_docx_document_xml

    payload = {"job_id": "job-zc00", "filename": "sample.pdf", "issues": []}
    xml = _build_docx_document_xml(job_id="job-zc00", status_payload=payload, issues=[])
    assert "未发现明显问题" not in xml
    assert "旧版" in xml


def test_docx_zero_issue_all_complete_allows_scoped_conclusion() -> None:
    """正例：检查确实全部完成——允许"已完成检查范围内未发现问题"。"""
    from api.routes.reports import _build_docx_document_xml

    payload = _status_payload(unfinished=[], analysis_conclusion="no_findings")
    xml = _build_docx_document_xml(job_id="job-zc00", status_payload=payload, issues=[])
    assert "未发现明显问题" not in xml  # 旧措辞彻底退出
    assert "未发现问题" in xml
    assert "已完成" in xml


def test_docx_with_issues_and_unfinished_checks_lists_unfinished(tmp_path) -> None:
    """有问题且未完成：问题清单照常，同时必须列出未完成事项。"""
    from api.routes.reports import _build_docx_document_xml

    payload = _status_payload(
        unfinished=[_unfinished_item()], analysis_conclusion="findings_detected"
    )
    issues = [
        {
            "id": "i1",
            "rule_id": "C-001",
            "severity": "high",
            "title": "三公经费合计不一致",
            "message": "35.20 != 33.00",
            "location": {"page": 2},
            "evidence": [{"page": 2, "text": "三公经费合计 35.20 万元"}],
        }
    ]
    xml = _build_docx_document_xml(job_id="job-zc00", status_payload=payload, issues=issues)
    assert "三公经费合计不一致" in xml
    assert "未完成检查事项" in xml
    assert "三公经费分项核对" in xml


def test_csv_zero_issue_has_summary_row_with_limits() -> None:
    """CSV 零问题也要有摘要记录，携带结论限制；问题行带 record_type。"""
    from api.routes.reports import _build_csv_row, _build_csv_summary_row

    payload = _status_payload(
        unfinished=[_unfinished_item()], analysis_conclusion="incomplete"
    )
    summary = _build_csv_summary_row("job-zc00", payload, 0)
    assert summary["record_type"] == "summary"
    assert "未发现明显问题" not in summary["标题"]
    assert "未完成" in summary["标题"]

    issue_row = _build_csv_row(
        {
            "id": "i1",
            "rule_id": "C-001",
            "severity": "high",
            "title": "三公经费合计不一致",
            "message": "35.20 != 33.00",
            "location": {"page": 2},
            "evidence": [{"page": 2, "text": "三公经费合计 35.20 万元"}],
        }
    )
    assert issue_row["record_type"] == "issue"


def test_pdf_zero_issue_incomplete_builds_appendix_copy(tmp_path) -> None:
    """PDF 下载：零问题但检查未完成 → 副本追加结论附录页；不修改原件。"""
    fitz = pytest.importorskip("fitz")
    from api.routes import reports

    job_id = "job-zc00-pdf"
    upload_root = tmp_path / "uploads"
    job_dir = upload_root / job_id
    job_dir.mkdir(parents=True)
    source_path = job_dir / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.save(str(source_path))
    document.close()

    payload = _status_payload(
        unfinished=[_unfinished_item()], analysis_conclusion="incomplete"
    )
    original_bytes = source_path.read_bytes()

    monkey_source = reports.runtime.UPLOAD_ROOT
    try:
        reports.runtime.UPLOAD_ROOT = upload_root
        appendix_path = reports._create_zero_issue_appendix_pdf(job_id, payload)
    finally:
        reports.runtime.UPLOAD_ROOT = monkey_source

    assert appendix_path is not None
    assert appendix_path.exists()
    # 原件字节未动
    assert source_path.read_bytes() == original_bytes

    with fitz.open(str(appendix_path)) as annotated:
        assert annotated.page_count == 2  # 原始 1 页 + 1 页附录
        appendix_text = annotated[-1].get_text()
    assert "系统生成附录" in appendix_text
    assert "未完成" in appendix_text
    assert "三公经费分项核对" in appendix_text


def test_pdf_zero_issue_complete_still_gets_complete_report_appendix(tmp_path, monkeypatch) -> None:
    """完整零问题报告也要有系统生成的完整 PDF 附录。"""
    fitz = pytest.importorskip("fitz")
    from api.routes import reports

    upload_root = tmp_path / "uploads"
    job_dir = upload_root / "job-zc00-pdf"
    job_dir.mkdir(parents=True)
    source_path = job_dir / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.save(str(source_path))
    document.close()
    monkeypatch.setattr(reports.runtime, "UPLOAD_ROOT", upload_root)
    payload = _status_payload(unfinished=[], analysis_conclusion="no_findings")
    appendix_path = reports._create_zero_issue_appendix_pdf("job-zc00-pdf", payload)
    assert appendix_path is not None
    with fitz.open(str(appendix_path)) as annotated:
        assert annotated.page_count == 2
        appendix_text = annotated[-1].get_text()
    assert "系统生成附录" in appendix_text
    assert "检查范围已完成" in appendix_text


# ---------------------------------------------------------------------------
# 4. 默认关闭 AI：配置默认值 + 拦截器证明无模型网络请求
# ---------------------------------------------------------------------------


def test_ai_disabled_by_default_everywhere() -> None:
    """环境变量、app.yaml、AnalysisConfig 三层默认都不启用 AI。"""
    import os

    import yaml

    from api.config import AI_ASSIST_ENABLED
    from src.schemas.issues import AnalysisConfig

    assert AI_ASSIST_ENABLED is False or os.getenv("AI_ASSIST_ENABLED") == "true"
    config = AnalysisConfig()
    assert config.ai_enabled is False
    assert config.enable_ai_analysis is False
    assert config.enable_ai_locator is False
    assert config.ai_fallback_on_error is False

    app_config = yaml.safe_load(
        (REPO_ROOT / "config" / "app.yaml").read_text(encoding="utf-8")
    )
    assert app_config["dual_mode"]["ai_enabled"] is False


def _install_external_connect_guard(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """网络拦截器：记录并阻断全部对外 TCP 连接；localhost 放行不记录。"""
    import socket as socket_module

    attempts: List[str] = []
    original_connect = socket_module.socket.connect

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else str(address)
        normalized = str(host)
        if normalized not in {"127.0.0.1", "::1", "localhost"}:
            attempts.append(normalized)
            raise AssertionError(f"unexpected external connection attempt: {normalized}")
        return original_connect(self, address)

    monkeypatch.setattr(socket_module.socket, "connect", guarded_connect)
    return attempts


def _stub_pipeline_dependencies(
    monkeypatch: pytest.MonkeyPatch, pipeline_mod: Any, page_text: str
) -> None:
    """把流水线的外围 IO 全部替换为内存桩，只保留真实调用链结构。"""
    from unittest.mock import AsyncMock

    fake_pdf = type(
        "_FakePdf",
        (),
        {
            "pages": [object()],
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        },
    )()
    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: fake_pdf)
    monkeypatch.setattr(pipeline_mod, "_extract_visible_text_from_page", lambda _page: page_text)
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": {"all": [], "error": [], "warn": [], "info": []},
                "rule_execution_summary": {},
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
    monkeypatch.setattr(pipeline_mod.settings, "is_dual_mode_enabled", lambda: False)


@pytest.mark.asyncio
async def test_pipeline_default_status_never_requests_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拦截器验证：默认 status（不写 use_ai_assist）跑完整流水线，零模型请求。"""
    from unittest.mock import AsyncMock

    from api import main as pipeline_mod, runtime

    # 本用例验证默认不请求语义 AI；扫描队列另有专门的续跑合同。
    monkeypatch.setenv('OCR_ENABLED', 'false')
    attempts = _install_external_connect_guard(monkeypatch)

    job_dir = tmp_path / "job-ai-off"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    # 关键：status.json 不包含 use_ai_assist —— 上传链路缺省即不请求 AI
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )

    _stub_pipeline_dependencies(
        monkeypatch,
        pipeline_mod,
        "一般公共预算财政拨款支出预算表\n三公经费合计 35.20 万元",
    )
    # 双模式分析入口一旦被触发立即失败——比网络拦截更早暴露问题
    monkeypatch.setattr(
        pipeline_mod.dual_analyzer,
        "analyze",
        AsyncMock(side_effect=AssertionError("AI analysis must not run by default")),
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload.get("status") in {"done", "review_required", "degraded"}
    assert attempts == []

    # 最终 payload 中的 use_ai_assist 必须如实记录为 False
    result_meta = (payload.get("result") or {}).get("meta") or {}
    assert result_meta.get("use_ai_assist") is False


@pytest.mark.asyncio
async def test_dual_analyzer_ai_switch_off_by_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双模式分析器：默认 AnalysisConfig 不创建 AI 任务；显式开启才会调用。

    控制组证明拦截器有效：同一批桩，显式 enable AI 后 _run_ai_analysis 被调用。
    """
    from unittest.mock import AsyncMock

    from src.schemas.issues import AnalysisConfig, JobContext
    from src.services.analyze_dual import DualModeAnalyzer

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    analyzer = DualModeAnalyzer()
    analyzer._load_rules = AsyncMock(
        return_value=[
            {"executor": "ai", "id": "ai-rule-1"},
            {"executor": "engine", "id": "engine-rule-1"},
        ]
    )
    ai_calls: List[Any] = []

    async def _stub_ai(_ctx: Any, _rules: Any, _cfg: Any, *, timeout_seconds: int) -> List[Any]:
        assert timeout_seconds > 0
        ai_calls.append(1)
        return []

    async def _stub_engine(_ctx: Any, _rules: Any, _cfg: Any) -> List[Any]:
        return []

    analyzer._run_ai_analysis = _stub_ai
    analyzer._run_engine_analysis = _stub_engine

    context = JobContext(job_id="job-dual-off", pdf_path="x.pdf")

    # 默认配置：AI 任务不创建
    await analyzer.analyze(context, AnalysisConfig())
    assert ai_calls == []

    # 控制组：显式开启后 AI 任务被创建（同一拦截器能观测到调用）
    explicit = AnalysisConfig(
        ai_enabled=True, enable_ai_analysis=True, enable_ai_locator=False
    )
    await analyzer.analyze(context, explicit)
    assert ai_calls == [1]


# ---------------------------------------------------------------------------
# 5. 评估报告证据反例的链路级重建（merge_repro 证据两例的修复态）
# ---------------------------------------------------------------------------


def test_merge_repro_unknown_page_case_now_fails_guard() -> None:
    """对应 merge_repro.unknown_page_and_generated_message_pass_evidence_guard。

    修复后：无页码 + 只有生成说明 → 缺页/缺原文，不再"字段完整"。
    """
    finding = {
        "id": "case-4",
        "source": "rule",
        "rule_id": "C-001",
        "severity": "high",
        "message": "三公经费口径不一致（这是生成说明）",
        "location": {},
        "evidence": [],
    }
    complete, missing, _verification = evaluate_finding_evidence(finding)
    assert complete is False
    assert "missing_page" in missing
    assert "missing_evidence_text" in missing


def test_merge_repro_page_999_case_now_fails_guard() -> None:
    """对应 merge_repro.out_of_range_page_and_unverified_text_pass_evidence_guard。"""
    finding = {
        "id": "case-5",
        "source": "ai",
        "rule_id": "AI-1",
        "severity": "high",
        "page_number": 999,
        "evidence": [{"page": 999, "text": "任意文本"}],
    }
    complete, missing, verification = evaluate_finding_evidence(
        finding, document_page_count=2, page_texts=TWO_PAGE_TEXTS
    )
    assert complete is False
    assert REASON_PAGE_OUT_OF_RANGE in missing
    assert verification["status"] == "failed"

    # AI 来源：越界证据在 apply 层会被降级为待复核，不再是正式问题
    result = {"ai_findings": [finding]}
    completeness = apply_evidence_completeness(
        result, document_page_count=2, page_texts=TWO_PAGE_TEXTS
    )
    assert completeness["degraded_count"] == 1
    assert completeness["formal_issue_total"] == 0
    assert result["ai_findings"][0]["evidence_status"] == EVIDENCE_STATUS_DEGRADED
    assert is_formal_finding(result["ai_findings"][0]) is False
