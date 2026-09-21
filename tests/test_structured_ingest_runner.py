"""
Tests for structured ingest runner safeguards.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.services import structured_ingest_runner
from src.services.structured_ingest_runner import (
    _build_review_items,
    _normalize_org_name,
    _strip_report_words,
    run_structured_ingest,
)


@pytest.mark.asyncio
async def test_run_structured_ingest_skips_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    payload = await run_structured_ingest(
        job_id="job-x",
        pdf_path=Path("missing.pdf"),
        metadata={},
    )

    assert payload["job_id"] == "job-x"
    assert payload["status"] == "skipped"
    assert payload["reason"] == "database_unavailable"


def test_resolve_storage_key_uses_stable_upload_relative_path(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    pdf_path = upload_root / "job-storage-1" / "sample.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_root))

    assert (
        structured_ingest_runner._resolve_storage_key("job-storage-1", pdf_path, {})
        == "job-storage-1/sample.pdf"
    )


def test_build_review_items_focuses_on_exceptions():
    instances = [
        SimpleNamespace(
            table_code="FIN_01_income_expenditure_total",
            confidence=0.72,
            page_number=3,
        ),
        SimpleNamespace(
            table_code="FIN_02_income",
            confidence=0.91,
            page_number=5,
        ),
        SimpleNamespace(
            table_code="FIN_03_expenditure",
            confidence=0.94,
            page_number=6,
        ),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": ["UNKNOWN_P3_T0"]},
        table_instances=instances,
        materialize_result={"low_confidence_tables": ["FIN_01_income_expenditure_total"], "facts_count": 12},
    )

    assert any(item["type"] == "unknown_table" for item in review_items)
    assert any(item["type"] == "low_confidence_table" for item in review_items)
    assert any(item["type"] == "missing_core_table" for item in review_items)


def test_build_review_items_skips_noise_when_core_tables_are_present():
    instances = [
        SimpleNamespace(table_code="FIN_01_income_expenditure_total", confidence=0.92, page_number=9),
        SimpleNamespace(table_code="FIN_02_income", confidence=0.92, page_number=10),
        SimpleNamespace(table_code="FIN_03_expenditure", confidence=0.92, page_number=14),
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.92, page_number=19),
        SimpleNamespace(table_code="FIN_05_general_public_expenditure", confidence=0.92, page_number=24),
        SimpleNamespace(table_code="FIN_06_basic_expenditure", confidence=0.93, page_number=27),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=29),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": ["UNKNOWN_P2_T0", "UNKNOWN_P7_T0"]},
        table_instances=instances,
        materialize_result={"low_confidence_tables": [], "facts_count": 224},
    )

    assert review_items == []


def test_build_review_items_suppresses_sparse_fin02_gap_with_strong_coverage():
    instances = [
        SimpleNamespace(table_code="FIN_01_income_expenditure_total", confidence=0.92, page_number=9),
        SimpleNamespace(table_code="FIN_03_expenditure", confidence=0.92, page_number=10),
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.92, page_number=14),
        SimpleNamespace(table_code="FIN_05_general_public_expenditure", confidence=0.92, page_number=19),
        SimpleNamespace(table_code="FIN_06_basic_expenditure", confidence=0.92, page_number=24),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=27),
        SimpleNamespace(table_code="FIN_08_gov_fund", confidence=0.92, page_number=29),
        SimpleNamespace(table_code="FIN_09_state_capital", confidence=0.92, page_number=31),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": []},
        table_instances=instances,
        materialize_result={"low_confidence_tables": [], "facts_count": 56},
        pdf_path=Path("万里街道（本部）2026年区级单位预算.pdf"),
    )

    assert review_items == []


def test_build_review_items_suppresses_unknown_noise_when_only_one_core_table_missing():
    instances = [
        SimpleNamespace(table_code="FIN_01_income_expenditure_total", confidence=0.92, page_number=9),
        SimpleNamespace(table_code="FIN_03_expenditure", confidence=0.92, page_number=10),
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.92, page_number=14),
        SimpleNamespace(table_code="FIN_05_general_public_expenditure", confidence=0.92, page_number=19),
        SimpleNamespace(table_code="FIN_06_basic_expenditure", confidence=0.92, page_number=24),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=27),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": ["UNKNOWN_P7_T0", "UNKNOWN_P8_T0"]},
        table_instances=instances,
        materialize_result={"low_confidence_tables": [], "facts_count": 96},
        pdf_path=Path("万里街道（本部）2026年区级单位预算.pdf"),
    )

    assert [item["type"] for item in review_items] == ["missing_core_table"]
    assert review_items[0]["table_code"] == "FIN_02_income"


def test_build_review_items_suppresses_fin04_low_confidence_when_other_tables_are_stable():
    instances = [
        SimpleNamespace(table_code="FIN_01_income_expenditure_total", confidence=0.92, page_number=3),
        SimpleNamespace(table_code="FIN_02_income", confidence=0.92, page_number=5),
        SimpleNamespace(table_code="FIN_03_expenditure", confidence=0.94, page_number=6),
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.78, page_number=7),
        SimpleNamespace(table_code="FIN_05_general_public_expenditure", confidence=0.92, page_number=8),
        SimpleNamespace(table_code="FIN_06_basic_expenditure", confidence=0.92, page_number=9),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=10),
        SimpleNamespace(table_code="FIN_08_gov_fund", confidence=0.92, page_number=11),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": []},
        table_instances=instances,
        materialize_result={
            "low_confidence_tables": ["FIN_04_fiscal_grant_total"],
            "facts_count": 13,
        },
        pdf_path=Path("上海市普陀区人民防空工程管理所2026年单位预算.pdf"),
    )

    assert review_items == []


def test_build_review_items_suppresses_sparse_fin02_and_fin05_gaps_with_stable_facts():
    instances = [
        SimpleNamespace(table_code="FIN_01_income_expenditure_total", confidence=0.92, page_number=8),
        SimpleNamespace(table_code="FIN_03_expenditure", confidence=0.92, page_number=12),
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.92, page_number=15),
        SimpleNamespace(table_code="FIN_06_basic_expenditure", confidence=0.92, page_number=22),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=23),
        SimpleNamespace(table_code="FIN_08_gov_fund", confidence=0.92, page_number=18),
        SimpleNamespace(table_code="FIN_09_state_capital", confidence=0.92, page_number=19),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": ["UNKNOWN_P2_T0", "UNKNOWN_P29_T0"]},
        table_instances=instances,
        materialize_result={"low_confidence_tables": [], "facts_count": 149},
        pdf_path=Path("房管局_2026年部门预算.pdf"),
    )

    assert review_items == []


def test_build_review_items_suppresses_empty_facts_when_only_fin04_is_sparse():
    instances = [
        SimpleNamespace(table_code="FIN_01_income_expenditure_total", confidence=0.92, page_number=3),
        SimpleNamespace(table_code="FIN_02_income", confidence=0.92, page_number=5),
        SimpleNamespace(table_code="FIN_03_expenditure", confidence=0.94, page_number=6),
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.78, page_number=7),
        SimpleNamespace(table_code="FIN_05_general_public_expenditure", confidence=0.92, page_number=8),
        SimpleNamespace(table_code="FIN_06_basic_expenditure", confidence=0.92, page_number=9),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=10),
        SimpleNamespace(table_code="FIN_08_gov_fund", confidence=0.92, page_number=11),
        SimpleNamespace(table_code="FIN_09_state_capital", confidence=0.92, page_number=12),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": ["UNKNOWN_P6_T0"]},
        table_instances=instances,
        materialize_result={
            "low_confidence_tables": ["FIN_04_fiscal_grant_total"],
            "facts_count": 0,
        },
        pdf_path=Path("上海市普陀区民防事务管理中心2026年单位预算.pdf"),
    )

    assert review_items == []


def test_build_review_items_marks_execution_budget_packet_as_profile_notice():
    instances = [
        SimpleNamespace(table_code="FIN_04_fiscal_grant_total", confidence=0.92, page_number=6),
        SimpleNamespace(table_code="FIN_05_general_public_expenditure", confidence=0.92, page_number=11),
        SimpleNamespace(table_code="FIN_07_three_public", confidence=0.92, page_number=12),
    ]

    review_items = _build_review_items(
        parse_result={"unknown_tables": ["UNKNOWN_P1_T0", "UNKNOWN_P4_T0"]},
        table_instances=instances,
        materialize_result={"low_confidence_tables": [], "facts_count": 64},
        pdf_path=Path("长征镇_上海市普陀区长征镇2025年预算执行和2026年预算表.pdf"),
    )

    assert review_items == []


def test_build_review_items_marks_narrative_report_as_profile_notice():
    review_items = _build_review_items(
        parse_result={"unknown_tables": []},
        table_instances=[],
        materialize_result={"low_confidence_tables": [], "facts_count": 0},
        pdf_path=Path("长征镇_关于普陀区长征镇2025年预算执行情况和2026年预算草案的报告.pdf"),
    )

    assert review_items == []


def test_normalize_org_name_removes_year_and_report_suffix():
    normalized = _normalize_org_name(
        "上海市普陀区商务委员会单位25年预算",
        Path("上海市普陀区商务委员会单位25年预算.pdf"),
    )

    assert normalized == "上海市普陀区商务委员会"


def test_strip_report_words_removes_duplicate_budget_suffixes():
    cleaned = _strip_report_words("上海市普陀区残疾人综合服务中心2026年单位预算公开")

    assert cleaned == "上海市普陀区残疾人综合服务中心"


def test_normalize_org_name_prefers_full_segment_after_underscore():
    normalized = _normalize_org_name(
        "",
        Path("城管执法局_上海市普陀区城市管理行政执法局2026年度单位预算公开.pdf"),
    )

    assert normalized == "上海市普陀区城市管理行政执法局"


# ==== 主链路编排：版本 → 槽位 → 解析 =========================================
#
# 这段守的是一条**顺序约束**：文档版本建立之后立刻分配材料槽位，然后才解析。
# 顺序错了不会报错，只会让"解析失败的材料在台账里不存在"这个缺陷重新长回来，
# 所以这里断言的是调用顺序与调用次数，而不是读源码确认。


def _install_runner_stubs(
    monkeypatch,
    *,
    events: List[str],
    slot_result: Optional[Dict[str, Any]] = None,
    parse_error: Optional[BaseException] = None,
) -> Dict[str, int]:
    """把主链路的外部协作者全部换成可控替身，返回调用计数。

    只替换"本用例不关心其内部实现"的环节：数据库句柄、PDF 解析、表识别、
    事实物化、PS 同步。被验证的是 ``run_structured_ingest`` 自己的编排顺序，
    这些替身不参与断言，也不改变被测代码。
    """
    from src.services import structured_ingest_runner as runner

    calls = {"allocate": 0}
    fake_conn = SimpleNamespace(name="fake-conn")
    resolved_slot = dict(
        slot_result
        if slot_result is not None
        else {"status": "resolved", "reason": "ok", "bound": True, "slot_id": "slot-1"}
    )

    async def _acquire():
        return fake_conn

    async def _release(_conn):
        return None

    async def _ready():
        return True

    async def _ensure_document_version(**_kwargs):
        events.append("ensure_document_version")
        return {"org_unit_id": 11, "document_id": 22, "document_version_id": 33}

    async def _allocate_material_slot(**_kwargs):
        calls["allocate"] += 1
        events.append("allocate_material_slot")
        return dict(resolved_slot)

    class _Parser:
        def __init__(self, _conn):
            pass

        async def parse_pdf(self, _path, _version_id):
            events.append("parse_pdf")
            if parse_error is not None:
                raise parse_error
            return {"success": True, "tables_count": 2, "unknown_tables": []}

    class _Recognizer:
        def __init__(self, _conn):
            pass

        async def recognize_tables(self, _version_id):
            return [
                SimpleNamespace(
                    table_code="FIN_01_income_expenditure_total",
                    confidence=0.93,
                    page_number=3,
                )
            ]

        async def save_table_instances(self, _version_id, _instances):
            return None

    class _Materializer:
        def __init__(self, _conn):
            pass

        async def materialize(self, _version_id):
            return {"facts_count": 9, "low_confidence_tables": []}

    class _PsSync:
        def __init__(self, _conn):
            pass

        async def sync(self, **_kwargs):
            return {"status": "skipped", "reason": "stub"}

    monkeypatch.setattr(runner, "ensure_structured_ingest_ready", _ready)
    monkeypatch.setattr(
        runner,
        "DatabaseConnection",
        SimpleNamespace(acquire=_acquire, release=_release),
    )
    monkeypatch.setattr(runner, "_ensure_document_version", _ensure_document_version)
    monkeypatch.setattr(runner, "_allocate_material_slot", _allocate_material_slot)
    monkeypatch.setattr(runner, "PDFParser", _Parser)
    monkeypatch.setattr(runner, "TableRecognizer", _Recognizer)
    monkeypatch.setattr(runner, "FiscalFactMaterializer", _Materializer)
    monkeypatch.setattr(runner, "PSSharedSchemaSync", _PsSync)
    return calls


def _ingest_metadata(pdf_path: Path) -> Dict[str, Any]:
    return {
        "organization_name": "上海市普陀区规划和自然资源局本级",
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
        "checksum": "d" * 64,
        "filename": pdf_path.name,
    }


async def test_material_slot_is_allocated_before_pdf_parsing(monkeypatch, tmp_path):
    """版本建立 → 槽位分配 → PDF 解析：分配必须在解析之前，且只做一次。"""
    events: List[str] = []
    calls = _install_runner_stubs(monkeypatch, events=events)

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    payload = await run_structured_ingest(
        job_id="job-order",
        pdf_path=pdf_path,
        metadata=_ingest_metadata(pdf_path),
    )

    assert events[:3] == [
        "ensure_document_version",
        "allocate_material_slot",
        "parse_pdf",
    ], f"主链路顺序不对: {events}"
    assert calls["allocate"] == 1, "一份分析只允许分配一次槽位"
    assert payload["status"] == "done"
    assert payload["material_slot"]["slot_id"] == "slot-1"


async def test_parser_failure_keeps_material_slot_context(monkeypatch, tmp_path):
    """解析抛错时，已建立的版本与槽位必须出现在错误结果里。"""
    events: List[str] = []
    calls = _install_runner_stubs(
        monkeypatch,
        events=events,
        slot_result={"status": "resolved", "reason": "ok", "bound": True, "slot_id": "slot-9"},
        parse_error=RuntimeError("parser exploded"),
    )

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    payload = await run_structured_ingest(
        job_id="job-parser-fail",
        pdf_path=pdf_path,
        metadata=_ingest_metadata(pdf_path),
    )

    assert payload["status"] == "error"
    assert "parser exploded" in payload["error"]
    assert payload["document_id"] == 22
    assert payload["document_version_id"] == 33
    assert payload["material_slot"]["bound"] is True
    assert payload["material_slot"]["slot_id"] == "slot-9"
    # 解析失败不得触发第二次分配：槽位不依赖解析结果
    assert calls["allocate"] == 1
    assert events.count("allocate_material_slot") == 1
    assert events.count("parse_pdf") == 1


async def test_material_slot_failure_does_not_block_structured_ingest(monkeypatch, tmp_path):
    """槽位分配自身失败只是旁路故障，结构化入库主流程必须照常完成。"""
    events: List[str] = []
    calls = _install_runner_stubs(
        monkeypatch,
        events=events,
        slot_result={
            "status": "error",
            "reason": "slot_allocation_failed",
            "bound": False,
            "error": "RuntimeError: slot backend down",
        },
    )

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    payload = await run_structured_ingest(
        job_id="job-slot-fail",
        pdf_path=pdf_path,
        metadata=_ingest_metadata(pdf_path),
    )

    assert payload["status"] == "done"
    assert payload["facts_count"] == 9
    assert payload["material_slot"]["status"] == "error"
    assert payload["material_slot"]["bound"] is False
    assert calls["allocate"] == 1

