"""2026-11-18 系统缺口修复回归测试。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime
from api.routes import jobs
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.fact_context import FactContext
from src.engine.native_empty_operands import verified_blank_zero_by_code
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import build_document
from src.schemas.parse_artifact import (
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from src.schemas.review_report import build_review_report, normalize_manual_review
from src.services.pdf_parser import PDFParser


# ---------------------------------------------------------------------------
# 1. published_job_view 对旧版 v2 报告投影为 v3（不改动原 dict）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_published_job_view_projects_legacy_v2_report_to_v3(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://control.invalid/test")
    monkeypatch.setenv("PDF_PARSE_ISOLATION_ENABLED", "false")
    legacy_published = {
        "job_id": "job-v2",
        "organization_id": "org-1",
        "status": "review_required",
        "analysis_revision": "rev-1",
        "review_reasons": [
            {"code": "table_reading_incomplete", "message": "还有 0 处表格读取缺口", "pages": [7]},
            {"code": "rules_not_executed", "message": "适用规则中仅 82/90 条得出结论"},
        ],
        "structured_ingest": {
            "review_items": [
                {
                    "id": "reading:mapping:bc4af0d603379fb7",
                    "type": "table_mapping_incomplete",
                    "severity": "warn",
                    "table_code": "FIN_02_income",
                    "page_number": 6,
                    "page_numbers": [6, 7],
                    "message": "第 7 页《收入决算表》映射不完整：28 个金额格没有对应到列含义",
                    "recommended_action": "inspect_table_column_mapping",
                }
            ]
        },
        "result": {"rule_findings": [], "ai_findings": [], "meta": {}},
        "review_report": {
            "schema_version": 2,
            "issues": [],
            "tips": [],
            "manual_review": [
                {"record_type": "review_reason", "code": "table_reading_incomplete", "title": "table_reading_incomplete"},
                {"record_type": "structured_review", "id": "reading:mapping:bc4af0d603379fb7", "title": "结构化识别待复核：FIN_02_income"},
            ],
        },
    }
    before = deepcopy(legacy_published)
    monkeypatch.setattr("src.services.analysis_fact_snapshot.read_database_result", AsyncMock(return_value=legacy_published))
    monkeypatch.setattr("src.services.document_scope.pending_reanalysis", AsyncMock(return_value=None))
    monkeypatch.setattr(jobs, "user_can_access_job", lambda user, payload: True)
    monkeypatch.setattr(jobs.runtime, "resolve_organization_level", lambda org: "unit")
    monkeypatch.setattr("src.services.issue_workflow_store.project_job_issue_decisions", lambda job_id, payload: [])

    view = await jobs.published_job_view("job-v2", {"status": "review_required"}, {"is_admin": True})

    assert legacy_published == before
    report = view["review_report"]
    assert report["schema_version"] == 3
    assert report["manual_review"] == []
    diagnostics = report["diagnostics"]
    structured = [item for item in diagnostics if item["record_type"] == "structured_review"]
    notices = [item for item in diagnostics if item["record_type"] == "processing_notice"]
    assert structured[0]["title"] == "表格识别待复核：收入决算表"
    assert "inspect_table_column_mapping" not in structured[0]["suggestion"]
    assert "列头" in structured[0]["suggestion"]
    assert {item["title"] for item in notices} >= {"部分表格读取未完成", "部分规则未执行"}


# ---------------------------------------------------------------------------
# 2. 非最新任务重分析不再以 not_latest_version 抛错；candidate_only 唯一消歧
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_not_latest_version_runs_candidate_ingest_without_crashing(tmp_path, monkeypatch):
    job_dir = tmp_path / "job-old-version"
    job_dir.mkdir()
    pdf_bytes = b"%PDF-1.4\n%%EOF\n"
    pdf_path = job_dir / "report.pdf"
    pdf_path.write_bytes(pdf_bytes)
    checksum = hashlib.sha256(pdf_bytes).hexdigest()

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": "job-old-version",
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "final",
            "doc_type": "dept_final",
            "organization_id": "org-1",
            "organization_name": "测试单位",
            "checksum": checksum,
        },
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://control.invalid/test")
    monkeypatch.setenv("PDF_PARSE_ISOLATION_ENABLED", "false")
    monkeypatch.setattr("src.services.material_writing.enabled", lambda: False)
    monkeypatch.setattr("src.services.material_writing.load", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.analysis_fact_snapshot.current_revision", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.document_scope.load_for_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(pipeline_mod.DatabaseConnection, "is_initialized", classmethod(lambda cls: False))
    monkeypatch.setattr("src.services.analysis_result_store.persist_analysis_job_snapshot", AsyncMock(return_value=True))

    fake_artifact = ParseArtifact(
        source_file_hash=checksum,
        parse_run_uid="run-old-version",
        page_count=1,
    )
    page_text = "测试单位2025年度部门决算\n" * 6

    class _FakePdf:
        def __init__(self):
            self.pages = [SimpleNamespace(width=595, height=842, chars=[])]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf())
    monkeypatch.setattr(pipeline_mod, "_extract_visible_text_from_page", lambda _page: page_text)
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])

    class _FakeParser:
        def __init__(self, _conn):
            pass

        def build_parse_artifact(self, _path, *, config_fingerprint="", existing_ocr=None):
            return fake_artifact

        def build_parse_artifact_from_handle(self, _pdf, *, file_hash="", **_kwargs):
            return fake_artifact

    monkeypatch.setattr("src.services.pdf_parser.PDFParser", _FakeParser)
    monkeypatch.setattr(
        runtime,
        "resolve_latest_structured_ingest_job",
        lambda *_args, **_kwargs: {
            "is_latest": False,
            "reason": "not_latest_version",
            "latest_job_id": "job-newer",
            "latest_filename": "report_v2.pdf",
        },
    )

    captured_calls = []

    async def _fake_ingest(**kwargs):
        captured_calls.append(kwargs)
        return {
            "job_id": kwargs["job_id"],
            "status": "done",
            "review_item_count": 0,
            "review_items": [],
            "fact_snapshot": None,
        }

    monkeypatch.setattr(pipeline_mod, "run_structured_ingest", _fake_ingest)

    await pipeline_mod._run_pipeline_inner(job_dir)

    assert len(captured_calls) == 1
    assert captured_calls[0]["candidate_only"] is True
    final_status = runtime.read_json_file(job_dir / "status.json", default={})
    assert final_status["status"] in {"done", "review_required"}
    ingest = final_status["structured_ingest"]
    assert ingest["slot_sync_skipped"] is True
    assert ingest["slot_sync_skip_reason"] == "not_latest_version"
    assert ingest["latest_job_id"] == "job-newer"


@pytest.mark.asyncio
async def test_candidate_only_disambiguates_duplicate_document_versions_by_job(tmp_path, monkeypatch):
    from src.services import structured_ingest_runner as runner

    pdf_path = tmp_path / "dup.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    checksum = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    class _FakeConn:
        def transaction(self):
            return SimpleNamespace(start=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        async def fetch(self, sql, *args):
            if "FROM fiscal_document_versions" in sql:
                return [
                    {"document_version_id": 101, "document_id": 1, "org_unit_id": 1},
                    {"document_version_id": 202, "document_id": 2, "org_unit_id": 1},
                ]
            return []

        async def fetchval(self, sql, *args):
            if "SELECT structured_document_version_id FROM analysis_jobs" in sql:
                return 202
            return None

    fake_conn = _FakeConn()
    monkeypatch.setattr(runner, "ensure_structured_ingest_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(runner.DatabaseConnection, "acquire", AsyncMock(return_value=fake_conn))
    monkeypatch.setattr(runner.DatabaseConnection, "release", AsyncMock())
    monkeypatch.setattr(runner, "find_cached_parse_run", AsyncMock(return_value=88))
    artifact = ParseArtifact(source_file_hash=checksum, parse_run_uid="run-dup", page_count=1)
    monkeypatch.setattr(runner, "load_parse_artifact", AsyncMock(return_value=artifact))
    monkeypatch.setattr(runner, "persist_facts_and_evidence", AsyncMock(return_value=0))
    monkeypatch.setattr(runner, "verify_persisted_facts", AsyncMock(return_value={"verified": True}))

    class _FakeParser:
        def __init__(self, _conn):
            pass

    monkeypatch.setattr(runner, "PDFParser", _FakeParser)

    res = await runner.run_structured_ingest(
        job_id="job-dup",
        pdf_path=pdf_path,
        metadata={"organization_name": "测试单位", "fiscal_year": 2025, "doc_type": "dept_final"},
        parse_artifact=artifact,
        raw_fact_context=FactContext([], file_hash=checksum, parse_run_uid="run-dup"),
        effective_fact_context=FactContext([], file_hash=checksum, parse_run_uid="run-dup"),
        candidate_only=True,
    )
    assert res["document_version_id"] == 202


# ---------------------------------------------------------------------------
# 3. _recover_open_top_grid_row 恢复顶对齐编码与居中金额（>3pt 差）并拒绝跨行噪声
# ---------------------------------------------------------------------------


def test_recover_open_top_grid_row_handles_top_aligned_code_and_rejects_noise():
    parser = PDFParser(None)
    table = ParsedTable(
        table_code="UNKNOWN_P7_T0",
        title="221 住房保障支出 55.31 33.12 0.00",
        page_number=7,
        table_index=0,
        bbox=(70.0, 112.82, 500.0, 140.0),
        header_row_count=0,
        confidence=0.96,
        extraction_method="lines",
        unit_hint="万元",
        rows=[["221", "住房保障支出", "55.31", "33.12", "0.00"]],
        cells=[
            ParsedCell(0, 0, "221", "221", None, 7, (70.0, 112.82, 123.0, 136.34), False, 0.96, "万元", "lines"),
            ParsedCell(0, 1, "住房保障支出", "住房保障支出", None, 7, (123.0, 112.82, 260.0, 136.34), False, 0.96, "万元", "lines"),
            ParsedCell(0, 2, "55.31", "55.31", 55.31, 7, (260.0, 112.82, 338.0, 136.34), False, 0.96, "万元", "lines"),
            ParsedCell(0, 3, "33.12", "33.12", 33.12, 7, (338.0, 112.82, 416.0, 136.34), False, 0.96, "万元", "lines"),
            ParsedCell(0, 4, "0.00", "0.00", 0.0, 7, (416.0, 112.82, 500.0, 136.34), False, 0.96, "万元", "lines"),
        ],
        fragment_uid="p7-grid",
    )
    # 实测版式：编码 top=92.47, bottom=103.03；金额 top=95.95, bottom=106.51（相差 3.48pt 但纵向重叠）
    words_aligned = [
        {"text": "2101102", "x0": 75.7, "x1": 115.0, "top": 92.47, "bottom": 103.03},
        {"text": "事业单位医疗", "x0": 129.1, "x1": 210.0, "top": 92.47, "bottom": 103.03},
        {"text": "42.92", "x0": 306.9, "x1": 333.0, "top": 95.95, "bottom": 106.51},
        {"text": "42.65", "x0": 384.9, "x1": 411.0, "top": 95.95, "bottom": 106.51},
        {"text": "0.00", "x0": 468.1, "x1": 490.0, "top": 95.95, "bottom": 106.51},
    ]
    recovered = parser._recover_open_top_grid_row(SimpleNamespace(extract_words=lambda: words_aligned), table)
    assert len(recovered.rows) == 2
    assert recovered.rows[0] == ["2101102", "事业单位医疗", "42.92", "42.65", "0.00"]

    # 噪声反例：编码在上方完全不重合的高位（如页眉编号），必须拒绝恢复
    words_detached = [
        {"text": "2101102", "x0": 75.7, "x1": 115.0, "top": 70.0, "bottom": 80.0},
        {"text": "事业单位医疗", "x0": 129.1, "x1": 210.0, "top": 92.47, "bottom": 103.03},
        {"text": "42.92", "x0": 306.9, "x1": 333.0, "top": 95.95, "bottom": 106.51},
        {"text": "42.65", "x0": 384.9, "x1": 411.0, "top": 95.95, "bottom": 106.51},
        {"text": "0.00", "x0": 468.1, "x1": 490.0, "top": 95.95, "bottom": 106.51},
    ]
    untouched = parser._recover_open_top_grid_row(SimpleNamespace(extract_words=lambda: words_detached), table)
    assert len(untouched.rows) == 1


# ---------------------------------------------------------------------------
# 4. verified_blank_zero_by_code 支持 8 列双栏分列 类 | 款 编码
# ---------------------------------------------------------------------------


def _make_fin06_split_code_doc(*, blank_verified: bool = True, duplicate: bool = False):
    rows = [
        ["经济分类科目编码", None, "科目名称", "决算数", "经济分类科目编码", None, "科目名称", "决算数"],
        ["类", "款", None, None, "类", "款", None, None],
        ["301", "01", "基本工资", "100.00", "310", "13", "公务用车购置", ""],
    ]
    if duplicate:
        rows.append(["301", "02", "津贴补贴", "50.00", "310", "13", "公务用车购置", ""])
    xs = [10, 40, 70, 200, 260, 290, 320, 450, 510]
    cells = []
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            raw = val or ""
            is_header = r < 2
            is_blank_target = (not is_header) and c == 7 and not raw
            cells.append(
                ParsedCell(
                    row_idx=r,
                    col_idx=c,
                    raw_text=raw,
                    normalized_text=raw,
                    numeric_value=float(raw) if (not is_header and c == 3 and raw) else None,
                    page_number=14,
                    bbox=(xs[c], 100 + r * 20, xs[c + 1], 120 + r * 20),
                    is_header=is_header,
                    confidence=0.99,
                    unit_hint="万元",
                    extraction_method="lines",
                    native_blank_verified=blank_verified if is_blank_target else False,
                )
            )
    table = ParsedTable(
        table_code="FIN_06_basic_expenditure",
        title="一般公共预算财政拨款基本支出决算表",
        page_number=14,
        table_index=0,
        bbox=(10, 100, 510, 180),
        header_row_count=2,
        confidence=0.99,
        extraction_method="lines",
        unit_hint="万元",
        rows=rows,
        cells=cells,
        fragment_uid="fin06-split",
    )
    artifact = ParseArtifact(
        source_file_hash="b" * 64,
        parse_run_uid="run-fin06",
        page_count=14,
        table_fragments=[table],
        physical_fragments=[deepcopy(table)],
    )
    context = FactContext(
        [],
        file_hash="b" * 64,
        parse_run_uid="run-fin06",
        org_name="上海市人民政府参事室",
        fiscal_year=2024,
    )
    return SimpleNamespace(parse_artifact=artifact, fact_context=context)


def test_verified_blank_zero_by_code_supports_split_class_sub_columns():
    doc_ok = _make_fin06_split_code_doc(blank_verified=True, duplicate=False)
    obs = verified_blank_zero_by_code(doc_ok, "FIN_06_basic_expenditure", 14, "31013")
    assert obs is not None
    assert obs["subject_code"] == "31013"
    assert obs["subject_name"] == "公务用车购置"
    assert obs["calculation"] is True

    doc_unverified = _make_fin06_split_code_doc(blank_verified=False, duplicate=False)
    assert verified_blank_zero_by_code(doc_unverified, "FIN_06_basic_expenditure", 14, "31013") is None

    doc_ambiguous = _make_fin06_split_code_doc(blank_verified=True, duplicate=True)
    assert verified_blank_zero_by_code(doc_ambiguous, "FIN_06_basic_expenditure", 14, "31013") is None


# ---------------------------------------------------------------------------
# 5. 中文标题、动作建议翻译与门禁具体明细展示
# ---------------------------------------------------------------------------


def test_review_report_and_quality_gate_human_readable_chinese():
    report = build_review_report(
        {
            "job_id": "job-cn",
            "status": "review_required",
            "structured_ingest": {
                "review_items": [
                    {
                        "id": "r1",
                        "type": "table_mapping_incomplete",
                        "table_code": "FIN_02_income",
                        "page_number": 6,
                        "message": "映射不完整",
                        "recommended_action": "inspect_table_column_mapping",
                    },
                    {
                        "id": "r2",
                        "type": "table_mapping_incomplete",
                        "table_code": "UNKNOWN_P7_T0",
                        "page_number": 7,
                        "message": "映射不完整",
                        "recommended_action": "review_table_title_and_headers",
                    },
                ]
            },
            "result": {"rule_findings": [], "ai_findings": [], "meta": {}},
        }
    )
    structured = [d for d in report["diagnostics"] if d["record_type"] == "structured_review"]
    assert structured[0]["title"] == "表格识别待复核：收入决算表"
    assert structured[0]["table_code"] == "FIN_02_income"
    assert structured[0]["recommended_action"] == "inspect_table_column_mapping"
    assert "列头" in structured[0]["suggestion"]
    assert structured[1]["title"] == "表格识别待复核：第 7 页未识别表格"
    assert "表头" in structured[1]["suggestion"]

    norm_reason = normalize_manual_review(
        {"record_type": "review_reason", "code": "rules_not_executed", "title": "rules_not_executed"}
    )
    assert norm_reason["title"] == "部分规则未执行"
    assert norm_reason["display_title"] == "部分规则未执行"

    gate = pipeline_mod._evaluate_quality_gate(
        page_assessment={"page_coverage": 1.0, "scanned_page_count": 0},
        report_kind="final",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=0,
        rule_execution_summary={
            "total_rules": 90,
            "executed": 88,
            "insufficient_data": 1,
            "unresolved_rules": [
                {"rule_id": "V33-251", "status": "insufficient_data", "detail": "第21页金额缺少可归一单位"}
            ],
        },
        obligation_coverage={
            "available": True,
            "blocking_total": 1,
            "by_reason": {"insufficient_data": 1},
            "by_reason_labels": {"insufficient_data": "取数不足"},
            "blocking_obligation_ids": ["OBL-TXT-FINAL-DETAIL"],
            "instances": [
                {
                    "obligation_id": "OBL-TXT-FINAL-DETAIL",
                    "title": "支出决算具体情况逐项细化到项、分项加总、不漏列",
                    "status": "insufficient_data",
                    "blocks_gate": True,
                }
            ],
        },
    )
    by_code = {item["code"]: item for item in gate["review_reasons"]}
    assert "V33-251" in by_code["rules_insufficient_data"]["message"]
    assert "支出决算具体情况逐项细化到项" in by_code["check_obligations_incomplete"]["message"]
    assert "V33-251" in by_code["rules_not_executed"]["message"]


# ---------------------------------------------------------------------------
# 6. V33-251 不可归一金额附带页码定位
# ---------------------------------------------------------------------------


def test_v33_251_unusable_amount_includes_page_location():
    p20 = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为100.00万元。其中：\n"
        "1.一般公共服务支出（类）政府办公厅（室）及相关机构事务（款）行政运行（项）。年初预算为50.00万元，支出决算为50.00万元。\n"
    )
    p21 = (
        "2.社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位基本养老保险缴费支出（项）。"
        "年初预算为50.00万元，支出决算65.96 为万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/v33-251-loc.pdf", [p20, p21], [[], []], 1000)
    doc.report_kind = "final"
    signal = None
    try:
        R33251_FunctionalDetailCompleteness().apply(doc)
    except RuleOutcomeSignal as exc:
        signal = exc
    assert signal is not None and signal.status == "insufficient_data"
    assert "第2页" in signal.detail
    assert "65.96" in signal.detail


# ---------------------------------------------------------------------------
# 7. _operand_evidence 不允许 FiscalFact 泄入分析快照（2026-11-19 生产事故）
#
# 上海商业会计学校 2025 决算重跑：V33-251 的 included_items[2].table_unit_basis
# 携带运行期 FiscalFact，dumps_canonical 拒绝 → 分析结果三次入库重试全败 →
# 质量门禁后落库阶段抛 trusted_analysis_result_not_committed: pending_retry，
# 任务终态 error。证据必须转成可序列化摘要，运行期 record 保持 fact 不变。
# ---------------------------------------------------------------------------


def test_operand_evidence_serializes_table_unit_basis_fiscal_fact():
    from src.engine.checklist_final_rules import _operand_evidence
    from src.schemas.parse_artifact import FiscalFact
    from src.schemas.parse_artifact import FiscalFact as _FiscalFactAlias
    from src.services.json_canonical import dumps_canonical

    fact = FiscalFact(
        fact_uid="fact-u1",
        indicator="total_actual",
        raw_value="123.45",
        unit="元",
        normalized_value=Decimal("0.012345"),
        table_code="FIN_05_general_public_expenditure",
        subject_code="2050202",
        subject_name="中专教育",
    )
    record = {
        "raw": "123.45",
        "unit": "元",
        "pos": 10,
        "value_wan": Decimal("0.012345"),
        "kind": "final",
        "table_unit_basis": fact,
    }
    evidence = _operand_evidence(record)

    # 落库同款序列化器必须能吃下完整 issue location。
    dumps_canonical([{"location": {"included_items": [evidence]}}])

    basis = evidence["table_unit_basis"]
    assert not isinstance(basis, _FiscalFactAlias)
    assert basis["fact_uid"] == "fact-u1"
    assert basis["table_code"] == "FIN_05_general_public_expenditure"
    assert basis["subject_code"] == "2050202"
    assert basis["subject_name"] == "中专教育"
    assert basis["unit"] == "元"
    assert basis["normalized_value"] == "0.012345"
    assert evidence["value_wan"] == "0.012345"

    # 运行期 record 的 fact 对象原样保留：回执路径（unit_facts）仍按 fact 记账。
    assert record["table_unit_basis"] is fact
