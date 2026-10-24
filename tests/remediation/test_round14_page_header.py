"""Physical OCR identity and structural-row regressions; synthetic contracts only."""

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.schemas.parse_artifact import ParsedTable
from src.services.fiscal_fact_materializer import FiscalFactMaterializer
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.services.ocr_pipeline import _pdf_bbox_from_render_bbox, _render_pages_png
from src.services.ocr_qualification import qualification
from src.services.pdf_parser import PDFParser
from tests.remediation.test_round11_ocr import fixture


def digest(raw):
    return hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def two_pages(tmp_path):
    import fitz

    path = tmp_path / "two-pages.pdf"
    with fitz.open() as pdf:
        for color in ((1, 0, 0), (0, 0, 1)):
            page = pdf.new_page(width=400, height=100)
            page.draw_rect(fitz.Rect(10, 10, 20, 20), fill=color)
        pdf.save(path)
    images, transforms = _render_pages_png(str(path), [1, 2])
    assert images[0] != images[1]
    artifact, first = fixture()
    artifact.source_file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact.page_count = 2
    source_transform = artifact.coordinate_transforms[first.coordinate_transform_id]
    second = copy.deepcopy(first)
    second.page_number = 2
    second.candidate_uid = "page2-candidate"
    second.coordinate_transform_id = "ocr_render_page_2"
    second.rows = json.loads(json.dumps(second.rows).replace('"100"', '"900"'))
    for cell in second.cells:
        if cell["raw_text"] == "100":
            cell["raw_text"] = "900"
            for fragment in cell["fragments"]:
                fragment["text"] = "900"
            recheck = cell["region_recheck"]
            recheck["text"] = "900"
            recheck["provenance"]["raw_prediction"]["overall_ocr_res"]["rec_texts"] = ["900"]
            recheck["provenance"]["raw_prediction_sha256"] = digest(
                recheck["provenance"]["raw_prediction"]
            )
    raw_table = second.provenance["raw_prediction"]["table_res_list"][0]
    raw_table["pred_html"] = raw_table["pred_html"].replace(">100<", ">900<")
    raw_table["table_ocr_pred"]["rec_texts"] = [
        "900" if value == "100" else value for value in raw_table["table_ocr_pred"]["rec_texts"]
    ]
    second.provenance["raw_prediction_sha256"] = digest(second.provenance["raw_prediction"])
    artifact.coordinate_transforms = {}
    for candidate in (first, second):
        page = candidate.page_number
        transform = transforms[page]
        candidate.provenance.update(
            source_sha256=artifact.source_file_hash,
            render_sha256=transform["render_sha256"],
            input_sha256=transform["render_sha256"],
            source_size_px=transform["render_size_px"],
        )
        for cell in candidate.cells:
            cell["page_bbox"] = _pdf_bbox_from_render_bbox(cell["render_bbox"], transform)
        artifact.coordinate_transforms[candidate.coordinate_transform_id] = {
            **transform,
            "ocr_response": copy.deepcopy(candidate.provenance),
            "page_request": {
                "physical_page": page,
                **{
                    key: candidate.provenance[key]
                    for key in (
                        "source_sha256", "render_sha256", "input_sha256", "schema_version",
                        "raw_prediction_sha256", "model_fingerprint", "pipeline_fingerprint",
                    )
                },
            },
            "region_requests": [
                {
                    **{key: cell[key] for key in (
                        "row_idx", "col_idx", "row_span", "col_span", "render_bbox"
                    )},
                    "input_sha256": cell["region_recheck"]["input_sha256"],
                    "raw_prediction_sha256": cell["region_recheck"]["provenance"]["raw_prediction_sha256"],
                }
                for cell in candidate.cells if cell.get("region_recheck")
            ],
        }
        from tests.ocr_v3_helpers import bind_fixture_tile
        bind_fixture_tile(candidate,artifact.coordinate_transforms[candidate.coordinate_transform_id])
    block = copy.deepcopy(artifact.text_blocks[0])
    block.page_number, block.block_uid = 2, "page2-identity"
    artifact.text_blocks.append(block)
    artifact.ocr_table_candidates = [first, second]
    assert source_transform["physical_page"] == 1
    return artifact, first, second


def qualify(candidate, artifact):
    return qualification(candidate, artifact, organization="测试局", year=2025, report_kind="final")


def context_for(artifact):
    promote_ocr_candidates(artifact, organization="测试局", year=2025, report_kind="final")
    return build_fact_context_from_artifact(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )


def test_two_legitimate_pages_keep_original_amount_and_cell_coordinates(two_pages):
    artifact, first, second = two_pages
    for candidate in (first, second):
        assert qualify(candidate, artifact)["status"] == "qualified"
    context = context_for(artifact)
    operands = context.definite_facts()
    assert len(operands) == 4
    for fact in operands:
        ref = fact.extra_dims["evidence_refs"][0]
        page = ref["physical_page"]
        candidate = first if page == 1 else second
        assert fact.raw_value == ("100" if page == 1 else "900")
        assert ref["bbox"] in [cell["page_bbox"] for cell in candidate.cells if cell["raw_text"].isdigit()]
        assert ref["source_provenance"]["source"]["input_sha256"] == (
            artifact.coordinate_transforms[f"ocr_render_page_{page}"]["render_sha256"]
        )


@pytest.mark.parametrize("change,reason", [
    ("page", "candidate_page_not_bound_to_transform"),
    ("transform", "candidate_page_not_bound_to_transform"),
    ("response", "candidate_response_not_bound_to_page_request"),
    ("saved_response", "page_request_not_bound_to_response"),
    ("request", "page_request_not_bound_to_response"),
    ("request_missing", "page_request_identity_missing"),
    ("transform_source", "transform_source_hash_mismatch"),
])
def test_page_transform_response_and_request_cannot_be_mixed(two_pages, change, reason):
    artifact, candidate, second = two_pages
    artifact.ocr_table_candidates = [candidate]
    if change == "page":
        candidate.page_number = 2
    elif change == "transform":
        candidate.coordinate_transform_id = "ocr_render_page_2"
    elif change == "response":
        candidate.provenance = copy.deepcopy(second.provenance)
    elif change == "saved_response":
        artifact.coordinate_transforms["ocr_render_page_1"]["ocr_response"] = copy.deepcopy(second.provenance)
    elif change == "request":
        artifact.coordinate_transforms["ocr_render_page_1"]["page_request"]["physical_page"] = 2
    elif change == "request_missing":
        artifact.coordinate_transforms["ocr_render_page_1"].pop("page_request")
    else:
        artifact.coordinate_transforms["ocr_render_page_1"]["source_sha256"] = "f" * 64
    result = qualify(candidate, artifact)
    assert result["status"] == "rejected" and reason in result["reasons"]
    assert context_for(artifact) is None
    assert not artifact.table_fragments and not artifact.logical_tables


@pytest.mark.parametrize("label", ["栏次", "栏 次", "行次", "行次："])
def test_structural_indices_and_headers_never_materialize_but_real_values_do(label):
    parser = PDFParser(AsyncMock())
    rows = [
        ["科目编码", "科目名称", "本年收入合计", "财政拨款收入"],
        ["", label, "1", "2"],
        ["", "合计", "746.62", "746.62"],
        ["201", "合法整数金额", "7", "0"],
        ["", label, "1", "2"],
    ]
    header_count = parser._detect_header_row_count(rows)
    assert header_count == 2
    raw_table = SimpleNamespace(rows=[SimpleNamespace(cells=[(0, 0, 10, 10)] * 4) for row in rows])
    cells = parser._build_cells(7, rows, raw_table, header_count, "万元", "lines", .99)
    assert all(cell.is_header for cell in cells if cell.row_idx in {0, 1, 4})
    table = ParsedTable(
        table_code="FIN_02_income", title="收入决算表", page_number=7, table_index=0,
        bbox=(0, 0, 100, 100), header_row_count=header_count, confidence=.99,
        extraction_method="lines", unit_hint="万元", rows=rows, cells=cells,
    )
    mappings = [
        {"source_col_idx": 2, "canonical_measure": "total_actual", "confidence": .99},
        {"source_col_idx": 3, "canonical_measure": "fiscal_allocation", "confidence": .99},
    ]
    materializer = FiscalFactMaterializer(AsyncMock())
    facts = materializer.build_fiscal_facts_for_table(table, mappings, fiscal_year=2024)
    assert [fact.raw_value for fact in facts] == ["746.62", "746.62", "7", "0"]
    assert {fact.row_order for fact in facts} == {2, 3}
    # The legacy cached view may lack is_header; explicit index labels still protect it.
    legacy = [cell.to_dict() for cell in cells]
    for cell in legacy:
        cell.pop("is_header")
    legacy_facts = materializer.build_facts_for_table("FIN_02_income", legacy, mappings, .99)
    assert [fact.amount for fact in legacy_facts] == [746.62, 746.62, 7, 0]


def test_explicit_numeric_header_cannot_materialize_without_index_label():
    materializer = FiscalFactMaterializer(AsyncMock())
    cells = [
        {"row_idx": 0, "col_idx": 0, "raw_text": "项目", "is_header": True},
        {"row_idx": 0, "col_idx": 1, "raw_text": "2024", "numeric_value": 2024, "is_header": True},
        {"row_idx": 1, "col_idx": 0, "raw_text": "合计", "is_header": False},
        {"row_idx": 1, "col_idx": 1, "raw_text": "0", "numeric_value": 0, "is_header": False},
    ]
    facts = materializer.build_facts_for_table(
        "FIN_02_income", cells, [{"source_col_idx": 1, "canonical_measure": "total_actual"}], .99
    )
    assert len(facts) == 1 and facts[0].amount == 0 and facts[0].row_order == 1


@pytest.mark.parametrize("amount", ["7", "0", "746.62"])
def test_sparse_single_amount_column_is_data_despite_text_majority(amount):
    parser = PDFParser(AsyncMock())
    rows = [
        ["部门", "科目名称", "项目名称", "本年收入合计"],
        ["测试局", "事业收入", "合法金额", amount],
    ]
    header_count = parser._detect_header_row_count(rows)
    assert header_count == 1
    raw_table = SimpleNamespace(rows=[SimpleNamespace(cells=[(0, 0, 10, 10)] * 4) for row in rows])
    cells = parser._build_cells(7, rows, raw_table, header_count, "万元", "lines", .99)
    facts = FiscalFactMaterializer(AsyncMock()).build_facts_for_table(
        "FIN_02_income", [cell.to_dict() for cell in cells],
        [{"source_col_idx": 3, "canonical_measure": "total_actual"}], .99,
    )
    assert len(facts) == 1 and facts[0].amount == float(amount)


def test_non_monetary_header_numbers_do_not_trigger_sparse_data_start():
    parser = PDFParser(AsyncMock())
    rows = [
        ["功能分类科目编码", "年度", "项目名称", "本年收入合计"],
        ["201", "2024", "科目名称", "金额"],
        ["栏次", "1", "2", "3"],
        ["201", "2024", "合法金额", "7"],
    ]
    assert parser._detect_header_row_count(rows) == 3


@pytest.mark.asyncio
async def test_database_materialization_loads_persisted_header_roles():
    conn = AsyncMock()
    materializer = FiscalFactMaterializer(conn)
    materializer._table_columns_cache["fiscal_table_cells"] = {"is_header", "numeric_value"}
    await materializer._load_cells(1, "FIN_02_income")
    assert "is_header" in conn.fetch.call_args.args[0]
