from decimal import Decimal
from copy import deepcopy

import pytest

from src.services.fiscal_temporal_scope import resolve_temporal_scope
from src.schemas.parse_artifact import FiscalFact
from src.engine.fact_context import FactContext, resolve_operand


@pytest.mark.parametrize("header,year,period,point,precision", [
    ("2023年度决算数", 2023, "本年", None, "year"),
    ("上年决算数", 2023, "上年", None, "year"),
    ("２０２４年第一季度执行数", 2024, "第1季度", None, "quarter"),
    ("2024年1—3月执行数", 2024, "1-3月", None, "month_range"),
    ("2024年3月执行数", 2024, "3月", None, "month"),
    ("截至2024年3月余额", 2024, "时点", "2024-03", "month"),
    ("截至2024年2月29日余额", 2024, "时点", "2024-02-29", "day"),
    ("2024-12-31余额", 2024, "时点", "2024-12-31", "day"),
    ("2024年1月1日至2024年3月31日", 2024, "2024-01-01/2024-03-31", None, "day_range"),
])
def test_explicit_column_time_scope_retains_literal_and_precision(header, year, period, point, precision):
    parsed = resolve_temporal_scope(header, report_year=2024, default_amount_year=2024)
    assert (parsed.amount_year, parsed.period, parsed.as_of, parsed.precision) == (year, period, point, precision)
    assert not parsed.reasons and parsed.column_literal == header


@pytest.mark.parametrize("header", [
    "2023年与2024年决算数", "2024年13月余额", "2024年2月30日余额", "2024年第一季度第二季度执行数",
    "2024年3-1月执行数", "2024年1月1日或2024年3月31日余额",
    "本年2023年收入", "上年2024年度决算数",
    "2024-02-300余额", "2024/02-03余额",
])
def test_explicit_time_conflict_never_defaults_to_cover_year(header):
    assert resolve_temporal_scope(header, report_year=2024).reasons


def test_equal_amount_different_quarters_or_timepoint_precision_are_distinct_identities():
    base = FiscalFact("one", "total_actual", "100", "万元", Decimal("100"),
                      org_name="测试局", fiscal_year=2024, subject_code="total", instance_uid="table",
                      period="第1季度", extra_dims={"amount_year": 2024})
    second = deepcopy(base)
    second.fact_uid, second.period = "two", "第2季度"
    context = FactContext([base, second], org_name="测试局", fiscal_year=2024)
    assert not resolve_operand(context, "total_actual").ok
    assert resolve_operand(context, "total_actual", period="第1季度").ok
    base.period = second.period = "时点"
    base.extra_dims["as_of"], second.extra_dims["as_of"] = "2024-03", "2024-03-31"
    assert base.identity_key() != second.identity_key()
    assert resolve_operand(context, "total_actual", as_of="2024-03").ok


def test_relative_year_uses_explicit_table_reference_before_document_year():
    parsed = resolve_temporal_scope("上年决算数", report_year=2025, default_amount_year=2023)
    assert parsed.amount_year == 2022 and not parsed.reasons
