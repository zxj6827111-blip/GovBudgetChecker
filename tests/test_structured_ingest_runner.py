"""
Tests for structured ingest runner safeguards.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

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
