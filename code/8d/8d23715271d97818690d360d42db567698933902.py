from dataclasses import replace
from decimal import Decimal
import copy
import pytest
from src.engine.fiscal_lexeme import lex_numeric, amount_in_wan, resolve_unit
from src.engine.fact_context import (
    FactContext,
    resolve_operand,
    remap_overlay_targets,
    FactOverlay,
    is_total_role_fact,
)
from src.schemas.parse_artifact import FiscalFact, ParsedCell
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile


@pytest.mark.parametrize(
    "raw,value",
    [
        ("−12.30", "-12.30"),
        ("－12.30", "-12.30"),
        ("（12.30）", "-12.30"),
        ("１，２３４．５６", "1234.56"),
        ("9999999999999999.99", "9999999999999999.99"),
    ],
)
def test_literal_precision_and_sign(raw, value):
    assert amount_in_wan(raw, "万元") == Decimal(value)


@pytest.mark.parametrize(
    "raw",
    ["—12.30", "--12.30", "1,23.45", "12 34", "12.30%", "NaN", "Infinity", "-", "", "2024年1月"],
)
def test_nonamount_never_generates_money(raw):
    assert amount_in_wan(raw, "万元") is None
    assert PDFParser(None)._parse_numeric(raw) is None


def fact(**kwargs):
    base = FiscalFact(
        "f",
        "total_actual",
        "100",
        "万元",
        Decimal(100),
        org_name="单位",
        org_scope="本级",
        fiscal_year=2024,
        period="本年",
        fund_scope="一般公共预算",
        budget_stage="决算",
        subject_code="total",
        subject_name="合计",
        table_code="FIN",
        instance_uid="one",
        extra_dims={
            "evidence_refs": [
                {
                    "file_hash": "source",
                    "physical_page": 1,
                    "source_cell_id": "p1:r1c1",
                    "bbox": [1, 2, 3, 4],
                }
            ]
        },
    )
    return replace(base, **kwargs)


def test_equal_amount_distinct_scope_blocks():
    a = fact()
    b = fact(fact_uid="g", org_scope="部门汇总", instance_uid="two")
    assert resolve_operand(FactContext([a, b]), "total_actual").status == "blocked"


def test_only_identical_physical_projection_deduplicates():
    a = fact()
    b = fact(fact_uid="projection")
    assert resolve_operand(FactContext([a, b]), "total_actual").ok
    b.extra_dims = copy.deepcopy(b.extra_dims)
    b.extra_dims["evidence_refs"][0]["source_cell_id"] = "p1:r2c1"
    assert resolve_operand(FactContext([a, b]), "total_actual").status == "blocked"


def test_uncoded_name_and_row_are_identity():
    assert (
        fact(subject_code=None, subject_name="甲").identity_key()
        != fact(subject_code=None, subject_name="乙").identity_key()
    )


def test_digest_covers_identity_and_original_evidence():
    a = FactContext([fact()], file_hash="source")
    b = FactContext([fact(fiscal_year=2025)], file_hash="source")
    assert len(a.version_token) == 64 and a.version_token != b.version_token
    data = a.to_dict()
    data["facts"][0]["extra_dims"]["evidence_refs"][0]["quote"] = "篡改"
    with pytest.raises(ValueError, match="SHA-256"):
        FactContext.from_dict(data)


def test_v1_remains_v1():
    data = FactContext([fact()], context_version="fact-context-v1").to_dict()
    assert FactContext.from_dict(data).context_version == "fact-context-v1"


def test_reused_uid_needs_identity_and_physical_source():
    a = fact()
    b = fact(subject_code="new", indicator="project_actual")
    accepted, unresolved, _ = remap_overlay_targets(
        [a], FactContext([b], file_hash="source"), [FactOverlay("f", normalized_value=Decimal(12))]
    )
    assert not accepted and unresolved


def test_table_instance_cannot_be_relaxed_for_overlay():
    a = fact()
    b = fact(fact_uid="new", instance_uid="two")
    accepted, unresolved, _ = remap_overlay_targets(
        [a], FactContext([b], file_hash="source"), [FactOverlay("f", normalized_value=Decimal(12))]
    )
    assert not accepted and unresolved


def test_new_file_never_inherits_overlay():
    a = fact()
    b = fact()
    accepted, unresolved, _ = remap_overlay_targets(
        [a],
        FactContext([b], file_hash="new-source"),
        [FactOverlay("f", normalized_value=Decimal(12))],
    )
    assert not accepted and unresolved


def test_substring_is_not_total_role():
    assert not is_total_role_fact(
        fact(subject_code=None, subject_name="其中：所属单位合计补助支出")
    )


def test_units_and_float_never_hide_invalid_source():
    assert amount_in_wan("12元", "万元") is None
    assert resolve_unit(cell="元", table="万元").conflicts
    cell = ParsedCell(1, 1, "1,23", "1,23", 123.0, 1, None, False, 0.99, "万元", "native_text")
    assert cell.normalized_value_wan is None


def test_source_year_precedes_filename():
    profile = resolve_document_profile(
        filename="2025年某局决算.pdf", page_texts=["上海市某局\n2024年度部门决算"]
    )
    assert profile.report_year.value == 2024
    assert profile.report_year.rejected


def test_local_unit_precedes_other_table():
    class Page:
        def extract_text(self):
            return "表A 单位：万元\n表B 单位：元"

    assert PDFParser(None)._detect_unit_hint(Page(), "表B", [["单位：元", ""]]) == "元"
