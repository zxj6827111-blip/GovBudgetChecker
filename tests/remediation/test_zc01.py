"""ZC-01 阶段验收测试：统一解析事实，修复单位和表实例存储。

对应缺陷：SYS-01（主链与结构化入库两次解析）、SYS-15（入库事实保存原
数字、单位只作提示，等值不同单位被比较成不同金额）、SYS-16（以表种和
行列号作为唯一键，同种表互相覆盖）。

必须验证（任务包 §三 ZC-01）：
- ``10000元 = 1万元 = 0.0001亿元``；
- 同文档同种表、不同主体或年度不互相覆盖；
- 无数据库和有数据库运行得到一致的业务事实（无库路径全测；有库路径
  需 GOVBUDGET_TEST_DATABASE_URL，本机未配置时按任务纪律记待验收）；
- 持久化不触发第二次 PDF 解析；
- 同版本重复执行幂等；旧记录可读但不自动获得"证据已验证"状态。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.engine.amount_math import normalize_amount_to_wan
from src.schemas.parse_artifact import (
    CellPresence,
    CheckReceipt,
    EvidenceRef,
    FiscalFact,
    LogicalTableInstance,
    ParseArtifact,
    ParsePageState,
    ParsedCell,
    ParsedTable,
)
from src.services.pdf_parser import PDFParser, PARSER_FINGERPRINT
from src.services.fiscal_fact_materializer import FiscalFactMaterializer, infer_fund_scope
from src.db.migrations import MIGRATIONS

# ---------------------------------------------------------------------------
# 1. 单位归一：10000元 = 1万元 = 0.0001亿元
# ---------------------------------------------------------------------------


def test_unit_normalization_equality() -> None:
    """反例（评估 §四.9）：10000 元、1 万元、0.0001 亿元归一后同为 1 万元。"""
    yuan = normalize_amount_to_wan("10000", "元")
    wan = normalize_amount_to_wan("1", "万元")
    yi = normalize_amount_to_wan("0.0001", "亿元")
    assert yuan == Decimal("1")
    assert wan == Decimal("1")
    assert yi == Decimal("1")
    assert yuan == wan == yi

    qian = normalize_amount_to_wan("10", "千元")
    assert qian == Decimal("1")
    baiwan = normalize_amount_to_wan("0.01", "百万元")
    assert baiwan == Decimal("1")  # 0.01 百万元 = 1 万元


def test_normalization_refuses_to_guess_without_unit() -> None:
    """无单位信息时不猜口径：返回 None，绝不默认万元。"""
    assert normalize_amount_to_wan("100") is None
    assert normalize_amount_to_wan(None) is None


def test_normalization_reads_embedded_unit_suffix() -> None:
    """原文自带单位后缀时直接解析（'1,234.56万元'）。"""
    assert normalize_amount_to_wan("1,234.56万元") == Decimal("1234.56")
    assert normalize_amount_to_wan("2亿元") == Decimal("20000")


def test_parsed_cell_carries_raw_and_normalized_values() -> None:
    """单元格：numeric_value 保留页面字面值，normalized_value_wan 承载万元归一。"""
    cell = ParsedCell(
        row_idx=1,
        col_idx=2,
        raw_text="10000",
        normalized_text="10000",
        numeric_value=10000.0,
        page_number=3,
        bbox=(1.0, 2.0, 3.0, 4.0),
        is_header=False,
        confidence=0.98,
        unit_hint="元",
        extraction_method="lines",
    )
    assert cell.numeric_value == 10000.0          # 原始字面值不动
    assert cell.normalized_value_wan == Decimal("1")

    wan_cell = ParsedCell(
        row_idx=1,
        col_idx=2,
        raw_text="1",
        normalized_text="1",
        numeric_value=1.0,
        page_number=3,
        bbox=None,
        is_header=False,
        confidence=0.98,
        unit_hint="万元",
        extraction_method="lines",
    )
    assert wan_cell.numeric_value == 1.0
    assert wan_cell.normalized_value_wan == Decimal("1")
    # 等值不同单位 → 归一值相同（SYS-15 的核心诉求）
    assert cell.normalized_value_wan == wan_cell.normalized_value_wan


def test_cell_presence_detection() -> None:
    """空白/零/破折号/无此项分别处理，不统一补 0。"""
    assert CellPresence.detect("—") == CellPresence.DASH
    assert CellPresence.detect("0.00") == CellPresence.ZERO
    assert CellPresence.detect("") == CellPresence.BLANK
    assert CellPresence.detect("无此项") == CellPresence.NOT_APPLICABLE
    assert CellPresence.detect("35.20") == CellPresence.VALUE


def test_parse_artifact_roundtrip_preserves_decimal() -> None:
    """JSON 序列化用十进制字符串承载 Decimal，往返不丢精度。"""
    artifact = ParseArtifact(
        source_file_hash="abc123",
        page_count=1,
        page_states=[ParsePageState(page_number=1, has_text_layer=True, text_chars=120)],
        table_fragments=[
            ParsedTable(
                table_code="FIN_01_income_expenditure_total",
                title="收入支出决算总表",
                page_number=1,
                table_index=0,
                bbox=(0.0, 0.0, 595.0, 842.0),
                header_row_count=1,
                confidence=0.92,
                extraction_method="lines",
                unit_hint="万元",
                rows=[["合计", "100.00"]],
                cells=[
                    ParsedCell(
                        row_idx=0,
                        col_idx=1,
                        raw_text="100.00",
                        normalized_text="100.00",
                        numeric_value=100.0,
                        page_number=1,
                        bbox=None,
                        is_header=False,
                        confidence=0.95,
                        unit_hint="万元",
                        extraction_method="lines",
                    )
                ],
            )
        ],
        logical_tables=[
            LogicalTableInstance(
                instance_uid="inst-1",
                table_code="FIN_01_income_expenditure_total",
                title="收入支出决算总表",
                fragment_uids=["inst-1"],
                page_start=1,
                page_end=1,
                row_count=1,
                confidence=0.92,
            )
        ],
    )
    restored = ParseArtifact.from_dict(artifact.to_dict())
    cell = restored.table_fragments[0].cells[0]
    assert cell.normalized_value_wan == Decimal("100.00")
    assert restored.parse_run_uid == artifact.parse_run_uid
    assert restored.logical_tables[0].instance_uid == "inst-1"


# ---------------------------------------------------------------------------
# 2. 表实例身份：同种表不同主体/年度不互相覆盖（SYS-16）
# ---------------------------------------------------------------------------


def test_same_table_code_different_org_year_are_independent() -> None:
    """部门汇总表与本级表：实例身份独立，事实身份键不同。"""
    materializer = FiscalFactMaterializer(None)
    table_a = _make_table()
    table_b = _make_table()

    facts_a = materializer.build_fiscal_facts_for_table(
        table_a,
        [{"source_col_idx": 1, "canonical_measure": "total_actual", "confidence": 0.9}],
        org_name="上海市普陀区教育局",
        org_scope="部门汇总",
        fiscal_year=2024,
        instance_uid="inst-summary",
        file_hash="h" * 64,
    )
    facts_b = materializer.build_fiscal_facts_for_table(
        table_b,
        [{"source_col_idx": 1, "canonical_measure": "total_actual", "confidence": 0.9}],
        org_name="上海市普陀区教育局",
        org_scope="本级",
        fiscal_year=2024,
        instance_uid="inst-self",
        file_hash="h" * 64,
    )

    assert facts_a and facts_b
    assert facts_a[0].identity_key() != facts_b[0].identity_key(), (
        "同金额、同表种但不同主体范围的事实不得共享身份"
    )
    # 金额相同也不覆盖：身份键不含金额
    assert facts_a[0].normalized_value == facts_b[0].normalized_value

    facts_other_year = materializer.build_fiscal_facts_for_table(
        _make_table(),
        [{"source_col_idx": 1, "canonical_measure": "total_actual", "confidence": 0.9}],
        org_name="上海市普陀区教育局",
        org_scope="部门汇总",
        fiscal_year=2025,
        instance_uid="inst-2025",
        file_hash="h" * 64,
    )
    assert facts_other_year[0].identity_key() != facts_a[0].identity_key()


def test_logical_table_instances_do_not_collapse_by_table_code() -> None:
    """同文档同表种两实例：LogicalTableInstance 各自持有独立 uid。"""
    inst_a = LogicalTableInstance(
        instance_uid="a", table_code="FIN_02_income", title="收入决算表",
        fragment_uids=["a"], page_start=3, page_end=3,
        org_name="某局", fiscal_year=2024,
    )
    inst_b = LogicalTableInstance(
        instance_uid="b", table_code="FIN_02_income", title="收入决算表",
        fragment_uids=["b"], page_start=8, page_end=8,
        org_name="某局", fiscal_year=2024,
    )
    assert inst_a.instance_uid != inst_b.instance_uid
    assert inst_a.table_code == inst_b.table_code  # 表种相同，身份不同


def test_new_storage_unique_keys_include_parse_run_and_instance() -> None:
    """静态锁定迁移契约：唯一键必须包含解析运行与表实例身份。"""
    migration = next(m for m in MIGRATIONS if m["id"] == "2026-10-01_0022_unified_parse_storage")
    sql_text = "\n".join(migration["sql"])
    assert "UNIQUE (parse_run_id, instance_uid)" in sql_text
    assert "UNIQUE (parse_run_id, instance_uid, row_idx, col_idx)" in sql_text
    assert "UNIQUE (parse_run_id, fact_uid)" in sql_text
    # 缓存键绑定原件 + 解析器 + 配置 + OCR 指纹
    assert "uq_parse_runs_cache_key" in sql_text
    assert "source_file_hash" in sql_text and "ocr_model_fingerprint" in sql_text
    # 证据默认未验证，不随落库自动变成"已验证"
    assert "verification_status TEXT NOT NULL DEFAULT 'unverified'" in sql_text


# ---------------------------------------------------------------------------
# 3. 无数据库路径：解析 → 事实一致、幂等
# ---------------------------------------------------------------------------


def _make_table() -> ParsedTable:
    return ParsedTable(
        table_code="FIN_01_income_expenditure_total",
        title="收入支出决算总表",
        page_number=1,
        table_index=0,
        bbox=(0.0, 0.0, 595.0, 842.0),
        header_row_count=1,
        confidence=0.95,
        extraction_method="lines",
        unit_hint="万元",
        rows=[
            ["项目", "合计"],
            ["合计", "123.45"],
        ],
        cells=[
            ParsedCell(
                row_idx=0, col_idx=0, raw_text="项目", normalized_text="项目",
                numeric_value=None, page_number=1, bbox=None, is_header=True,
                confidence=1.0, unit_hint="万元", extraction_method="lines",
            ),
            ParsedCell(
                row_idx=0, col_idx=1, raw_text="合计", normalized_text="合计",
                numeric_value=None, page_number=1, bbox=None, is_header=True,
                confidence=1.0, unit_hint="万元", extraction_method="lines",
            ),
            ParsedCell(
                row_idx=1, col_idx=0, raw_text="合计", normalized_text="合计",
                numeric_value=None, page_number=1, bbox=None, is_header=False,
                confidence=1.0, unit_hint="万元", extraction_method="lines",
            ),
            ParsedCell(
                row_idx=1, col_idx=1, raw_text="123.45", normalized_text="123.45",
                numeric_value=123.45, page_number=1, bbox=(10.0, 20.0, 60.0, 30.0),
                is_header=False, confidence=0.99, unit_hint="万元",
                extraction_method="lines",
            ),
        ],
    )


def test_fact_materialization_from_artifact_is_pure() -> None:
    """无数据库：同一 artifact 重复物化得到一致事实（幂等、无库不依赖）。"""
    materializer = FiscalFactMaterializer(None)
    table = _make_table()
    mappings = [{"source_col_idx": 1, "canonical_measure": "total_actual", "confidence": 0.9}]

    facts_first = materializer.build_fiscal_facts_for_table(
        table, mappings, org_name="某局", fiscal_year=2024, file_hash="h" * 64
    )
    facts_second = materializer.build_fiscal_facts_for_table(
        table, mappings, org_name="某局", fiscal_year=2024, file_hash="h" * 64
    )
    assert [f.to_dict() for f in facts_first] == [f.to_dict() for f in facts_second]
    fact = facts_first[0]
    assert fact.raw_value == "123.45"                    # 原文不改写
    assert fact.normalized_value == Decimal("123.45")    # 万元归一
    assert fact.evidence_ids, "事实必须携带证据引用"


def test_evidence_default_is_unverified() -> None:
    """旧记录/新解析都不自动获得"证据已验证"状态。"""
    evidence = EvidenceRef(file_hash="h" * 64, quote="123.45", physical_page=1)
    assert evidence.verification_status == "unverified"
    assert evidence.location_level == "unresolved"


def test_fund_scope_inference_from_table_semantics() -> None:
    """资金范围按表种语义映射，其余不猜。"""
    assert infer_fund_scope("FIN_08_gov_fund") == "government_fund"
    assert infer_fund_scope("FIN_09_state_capital") == "state_owned"
    assert infer_fund_scope("BUD_T2", "一般公共预算支出表") == "general_public_budget"
    assert infer_fund_scope("FIN_01_income_expenditure_total") is None


def test_check_receipt_six_state_contract() -> None:
    """回执六态固定：非法状态直接拒绝（ZC-03 消费此契约）。"""
    receipt = CheckReceipt(rule_id="C-001", sub_check_id="total_vs_parts", status="pass")
    assert receipt.to_dict()["status"] == "pass"
    with pytest.raises(ValueError):
        CheckReceipt(rule_id="C-001", sub_check_id="x", status="partially_done")


# ---------------------------------------------------------------------------
# 4. 真实 PDF 单次解析产物（pdfplumber 实测）
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path: Path, lines: List[str]) -> Path:
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "artifact.pdf"
    document = fitz.open()
    page = document.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12, fontname="china-s")
        y += 18
    document.save(str(path))
    document.close()
    return path


def test_build_parse_artifact_single_pass(tmp_path: Path) -> None:
    """一次打开产出完整 artifact：页状态 + 片段 + 实例 + 归一值。"""
    pdf_path = _make_pdf(
        tmp_path,
        [
            "收入支出决算总表",
            "单位：万元",
            "项目 合计",
            "合计 123.45",
        ],
    )
    parser = PDFParser(None)
    artifact = parser.build_parse_artifact(str(pdf_path))

    assert artifact.page_count == 1
    assert artifact.page_states[0].has_text_layer is True
    assert artifact.parser_fingerprint == PARSER_FINGERPRINT
    assert artifact.source_file_hash
    assert artifact.table_fragments, "文本层 PDF 必须产出表片段"

    # 序列化往返：子进程 IPC 契约
    restored = ParseArtifact.from_dict(artifact.to_dict())
    assert restored.page_count == artifact.page_count
    assert len(restored.table_fragments) == len(artifact.table_fragments)


@pytest.mark.asyncio
async def test_legacy_adapter_writes_from_artifact(tmp_path: Path, monkeypatch) -> None:
    """旧表适配器：同一 artifact 重复写入幂等（不抛错、计数一致）。"""
    pdf_path = _make_pdf(
        tmp_path,
        ["收入支出决算总表", "单位：万元", "项目 合计", "合计 123.45"],
    )
    parser = PDFParser(None)
    artifact = parser.build_parse_artifact(str(pdf_path))

    saved: List[Any] = []

    async def fake_clear(version_id):
        saved.clear()

    async def fake_save(version_id, table):
        saved.append(table)

    monkeypatch.setattr(parser, "_clear_existing_cells", fake_clear)
    monkeypatch.setattr(parser, "_save_table_cells", fake_save)

    summary_first = await parser.persist_artifact_to_legacy_tables(33, artifact)
    assert summary_first["success"] is True
    assert summary_first["tables_count"] == len(artifact.table_fragments)

    summary_second = await parser.persist_artifact_to_legacy_tables(33, artifact)
    assert summary_second["tables_count"] == summary_first["tables_count"]
    # 第二次写入前旧数据被清空（幂等重放，不叠加）
    assert saved == list(artifact.table_fragments)


# ---------------------------------------------------------------------------
# 5. 入库消费解析对象：持久化不触发第二次 PDF 解析
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_consumes_provided_artifact_without_reparse(monkeypatch, tmp_path):
    """传入 artifact 后：不得再打开 PDF 解析，统一存储幂等落库。"""
    import json
    from src.services import structured_ingest_runner as runner

    events: List[str] = []
    artifact = ParseArtifact(source_file_hash="f" * 64, page_count=1)

    async def _ready():
        return True

    class _FakeConn:
        async def fetchrow(self, *_a, **_k):
            return {"id": 55}

    async def _acquire():
        return _FakeConn()

    async def _release(_conn):
        return None

    async def _ensure_document_version(**_kwargs):
        events.append("ensure_document_version")
        return {"org_unit_id": 1, "document_id": 2, "document_version_id": 33}

    async def _allocate_material_slot(**_kwargs):
        events.append("allocate_material_slot")
        return {"status": "resolved", "bound": True, "slot_id": "slot-1"}

    async def _find_cached(*_args, **_kwargs):
        events.append("parse_cache_lookup")
        return None

    async def _persist_parse_artifact(_conn, _artifact, **_kwargs):
        events.append("persist_unified_storage")
        return 88

    async def _persist_facts(*_args, **_kwargs):
        events.append("persist_unified_facts")
        return 0

    class _Parser:
        def __init__(self, _conn):
            pass

        def build_parse_artifact(self, _path, **_k):  # pragma: no cover
            events.append("build_parse_artifact")
            raise AssertionError("传入 artifact 后不得再次解析 PDF")

        async def persist_artifact_to_legacy_tables(self, _v, _a):
            events.append("persist_legacy_cells")
            return {"success": True, "tables_count": 0, "unknown_tables": []}

    class _Recognizer:
        def __init__(self, _conn):
            pass

        async def recognize_tables(self, _v):
            return []

        async def save_table_instances(self, _v, _i):
            return None

    class _Materializer:
        def __init__(self, _conn):
            pass

        async def materialize(self, _v):
            return {"facts_count": 0, "low_confidence_tables": []}

    class _PsSync:
        def __init__(self, _conn):
            pass

        async def sync(self, **_k):
            return {"status": "skipped"}

    monkeypatch.setattr(runner, "ensure_structured_ingest_ready", _ready)
    monkeypatch.setattr(runner, "DatabaseConnection", type("DB", (), {"acquire": staticmethod(_acquire), "release": staticmethod(_release)}))
    monkeypatch.setattr(runner, "_ensure_document_version", _ensure_document_version)
    monkeypatch.setattr(runner, "_allocate_material_slot", _allocate_material_slot)
    monkeypatch.setattr(runner, "_detect_document_profile", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_backfill_local_organization_catalog", lambda **_k: {})
    monkeypatch.setattr(runner, "find_cached_parse_run", _find_cached)
    monkeypatch.setattr(runner, "persist_parse_artifact", _persist_parse_artifact)
    monkeypatch.setattr(runner, "persist_facts_and_evidence", _persist_facts)
    monkeypatch.setattr(runner, "PDFParser", _Parser)
    monkeypatch.setattr(runner, "TableRecognizer", _Recognizer)
    monkeypatch.setattr(runner, "FiscalFactMaterializer", _Materializer)
    monkeypatch.setattr(runner, "PSSharedSchemaSync", _PsSync)

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    payload = await runner.run_structured_ingest(
        job_id="job-zc01-artifact",
        pdf_path=pdf_path,
        metadata={
            "organization_name": "某局",
            "report_year": "2024",
            "doc_type": "dept_final",
            "checksum": "f" * 64,
            "filename": pdf_path.name,
        },
        parse_artifact=artifact,
    )

    assert payload["status"] == "done"
    assert "build_parse_artifact" not in events, "结构化入库不得重开 PDF 第二遍"
    assert events == [
        "ensure_document_version",
        "allocate_material_slot",
        "parse_cache_lookup",
        "persist_unified_storage",
        "persist_legacy_cells",
        "persist_unified_facts",
    ]


# ---------------------------------------------------------------------------
# 6. 真实 PostgreSQL 验证（需要 GOVBUDGET_TEST_DATABASE_URL；未配置则跳过）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("os").getenv("GOVBUDGET_TEST_DATABASE_URL"),
    reason="需要 GOVBUDGET_TEST_DATABASE_URL（任务包：真实库验证缺环境时保持未验收）",
)
@pytest.mark.asyncio
async def test_pg_roundtrip_artifact_and_fact_identity() -> None:
    """真实库：空库迁移 → 产物落库 → 缓存命中 → 幂等重放 → 实例独立。"""
    import os

    import asyncpg

    from src.db.connection import DatabaseConnection
    from src.db.migrations import run_migrations
    from src.services.parse_artifact_store import (
        find_cached_parse_run,
        load_parse_artifact,
        persist_facts_and_evidence,
        persist_parse_artifact,
    )

    url = os.environ["GOVBUDGET_TEST_DATABASE_URL"]
    conn = await asyncpg.connect(url)
    try:
        await run_migrations()

        await conn.execute(
            """
            INSERT INTO fiscal_documents (org_name, fiscal_year, doc_type)
            VALUES ('PG验证局', 2024, 'dept_final')
            ON CONFLICT DO NOTHING
            """
        )
        doc = await conn.fetchrow(
            "SELECT id FROM fiscal_documents WHERE org_name='PG验证局' LIMIT 1"
        )
        await conn.execute(
            """
            INSERT INTO fiscal_document_versions (document_id, file_hash)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            doc["id"],
            "pg" * 32,
        )
        version = await conn.fetchrow(
            "SELECT id FROM fiscal_document_versions WHERE file_hash=$1 LIMIT 1",
            "pg" * 32,
        )

        artifact = ParseArtifact(source_file_hash="pg" * 32, page_count=1)
        artifact.table_fragments = [_make_table()]
        artifact.logical_tables = [
            LogicalTableInstance(
                instance_uid=_make_table().fragment_uid,
                table_code="FIN_01_income_expenditure_total",
                title="收入支出决算总表",
                fragment_uids=[artifact.table_fragments[0].fragment_uid],
                page_start=1,
                page_end=1,
                row_count=2,
                confidence=0.95,
            )
        ]

        run_id = await persist_parse_artifact(
            conn, artifact, document_version_id=version["id"], org_name="PG验证局", fiscal_year=2024
        )
        # 幂等：同 parse_run_uid 重复执行不产生新运行
        run_id_again = await persist_parse_artifact(
            conn, artifact, document_version_id=version["id"], org_name="PG验证局", fiscal_year=2024
        )
        assert run_id == run_id_again

        # 缓存命中
        cached = await find_cached_parse_run(
            conn,
            document_version_id=version["id"],
            source_file_hash="pg" * 32,
            parser_fingerprint=artifact.parser_fingerprint,
            config_fingerprint="",
            ocr_model_fingerprint=None,
        )
        assert cached == run_id

        # 重建产物一致性
        restored = await load_parse_artifact(conn, run_id)
        assert restored.page_count == artifact.page_count
        assert len(restored.table_fragments) == len(artifact.table_fragments)

        # 同表种不同主体：事实身份独立
        facts_a = FiscalFactMaterializer(None).build_fiscal_facts_for_table(
            _make_table(),
            [{"source_col_idx": 1, "canonical_measure": "total_actual", "confidence": 0.9}],
            org_name="部门汇总", fiscal_year=2024, instance_uid="i-a", file_hash="pg" * 32,
        )
        facts_b = FiscalFactMaterializer(None).build_fiscal_facts_for_table(
            _make_table(),
            [{"source_col_idx": 1, "canonical_measure": "total_actual", "confidence": 0.9}],
            org_name="本级", fiscal_year=2024, instance_uid="i-b", file_hash="pg" * 32,
        )
        written = await persist_facts_and_evidence(
            conn, parse_run_id=run_id, facts=facts_a + facts_b, file_hash="pg" * 32
        )
        assert written == len(facts_a) + facts_b
        rows = await conn.fetch(
            "SELECT org_name, instance_uid FROM parse_facts WHERE parse_run_id=$1",
            run_id,
        )
        orgs = {row["org_name"] for row in rows}
        assert orgs == {"部门汇总", "本级"}, "不同主体的事实不得互相覆盖"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 7. 分析结果版本化目录与原子当前指针
# ---------------------------------------------------------------------------


def test_analysis_revision_snapshot_and_atomic_pointer(tmp_path) -> None:
    """终态结果写入版本目录；重分析追加新版本；current.json 原子更新。"""
    from api import runtime

    job_dir = tmp_path / "job-rev"
    job_dir.mkdir()

    first = {"job_id": "job-rev", "status": "done", "analysis_conclusion": "findings_detected"}
    pointer_first = runtime.write_analysis_revision_snapshot(job_dir, first)
    assert pointer_first is not None
    pointer_path = job_dir / "analysis_versions" / "current.json"
    assert pointer_path.exists()
    current = runtime.read_json_file(pointer_path, default={})
    assert current["status"] == "done"

    # 处理中状态不产生版本快照
    assert runtime.write_analysis_revision_snapshot(
        job_dir, {"job_id": "job-rev", "status": "processing"}
    ) is None

    # 重分析：追加新版本目录，旧快照保留
    second = {"job_id": "job-rev", "status": "review_required", "analysis_conclusion": "incomplete"}
    pointer_second = runtime.write_analysis_revision_snapshot(job_dir, second)
    assert pointer_second is not None
    assert pointer_second["revision_id"] != pointer_first["revision_id"]
    assert pointer_second["status"] == "review_required"
    assert pointer_first["revision_id"] in [rev["revision_id"] for rev in runtime.list_analysis_revisions(job_dir)]
    assert len(runtime.list_analysis_revisions(job_dir)) == 2

    current = runtime.read_json_file(pointer_path, default={})
    assert current["revision_id"] == pointer_second["revision_id"]
