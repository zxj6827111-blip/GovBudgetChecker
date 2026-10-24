"""Synthetic OCR contract tests; not real runtime or independent acceptance."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from ocr_service.protocol import map_table_cells, valid_bbox, valid_score
from src.schemas.parse_artifact import ParseArtifact, OcrTableCandidate, ParsedTextBlock, ParsedCell
from src.services.ocr_qualification import qualification
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.scan_evidence_certification import load_attestations


def raw_table():
    texts = ["科目编码", "科目名称", "本年收入合计", "财政拨款收入", "", "合计", "100", "100"]
    boxes = [[x * 100, y * 50, x * 100 + 100, y * 50 + 50] for y in range(2) for x in range(4)]
    tokens = [(text, box) for text, box in zip(texts, boxes, strict=True) if text]
    return {
        "pred_html": "<table><tr><td>科目编码</td><td>科目名称</td><td>本年收入合计</td><td>财政拨款收入</td></tr><tr><td></td><td>合计</td><td>100</td><td>100</td></tr></table>",
        "cell_box_list": list(reversed(boxes)),
        "table_ocr_pred": {
            "rec_texts": [text for text, box in tokens],
            "rec_scores": [0.995] * len(tokens),
            "rec_boxes": [[box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3] for text, box in tokens],
        },
    }


def fixture():
    raw = {
        "table_res_list": [raw_table()],
        "overall_ocr_res": {
            "rec_texts": ["测试局2025年决算 单位：万元"],
            "rec_scores": [0.995],
            "rec_polys": [[[0, 0], [400, 0], [400, 15], [0, 15]]],
        },
    }
    mapped = map_table_cells(raw["table_res_list"][0])

    def sha(value):
        return hashlib.sha256(value).hexdigest()

    source = {
        "schema_version": "gbc.ocr.cells.v2",
        "source_sha256": sha(b"original"),
        "render_sha256": sha(b"image"),
        "input_sha256": sha(b"image"),
        "source_size_px": [400, 100],
        "model_fingerprint": sha(b"models"),
        "pipeline_fingerprint": sha(b"pipeline"),
        "raw_prediction": raw,
        "raw_prediction_sha256": sha(
            json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ),
    }
    for cell in mapped["cells"]:
        cell["page_bbox"] = cell["render_bbox"]
        if cell["raw_text"].isdigit():
            roi_raw = {
                "overall_ocr_res": {
                    "rec_texts": [cell["raw_text"]],
                    "rec_scores": [0.992],
                    "rec_polys": [[[0, 0], [100, 0], [100, 50], [0, 50]]],
                }
            }
            roi_source = {
                "model_fingerprint": source["model_fingerprint"],
                "pipeline_fingerprint": source["pipeline_fingerprint"],
                "input_sha256": sha(b"crop"),
                "raw_prediction": roi_raw,
                "raw_prediction_sha256": sha(
                    json.dumps(
                        roi_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                ),
            }
            cell["region_recheck"] = {
                "status": "completed",
                "text": cell["raw_text"],
                "scores": [0.992],
                "provenance": roi_source,
                "input_sha256": sha(b"crop"),
            }
    candidate = OcrTableCandidate(
        candidate_uid="synthetic-candidate",
        page_number=1,
        rows=mapped["rows"],
        cells=mapped["cells"],
        provenance=source,
        mapping_status="mapped",
        coordinate_transform_id="ocr_render_page_1",
        page_bbox=(0, 0, 400, 100),
    )
    artifact = ParseArtifact(
        source_file_hash=source["source_sha256"],
        ocr_model_fingerprint=source["model_fingerprint"],
        coordinate_transforms={
            "ocr_render_page_1": {
                "physical_page": 1,
                "source_sha256": source["source_sha256"],
                "ocr_response": copy.deepcopy(source),
                "page_request": {
                    "physical_page": 1,
                    **{
                        key: source[key]
                        for key in (
                            "source_sha256", "render_sha256", "input_sha256",
                            "raw_prediction_sha256", "model_fingerprint",
                            "pipeline_fingerprint", "schema_version",
                        )
                    },
                },
                "bbox_transform_verified": True,
                "render_sha256": source["render_sha256"],
                "scale": 1,
                "render_origin_px": [0, 0],
                "render_size_px": [400, 100],
                "cropbox": [0, 0, 400, 100],
                "region_requests": [
                    {
                        **{
                            key: cell[key]
                            for key in ("row_idx", "col_idx", "row_span", "col_span", "render_bbox")
                        },
                        "input_sha256": cell["region_recheck"]["input_sha256"],
                        "raw_prediction_sha256": cell["region_recheck"]["provenance"][
                            "raw_prediction_sha256"
                        ],
                    }
                    for cell in mapped["cells"]
                    if cell.get("region_recheck")
                ],
            },
        },
        text_blocks=[
            ParsedTextBlock(
                block_uid="identity",
                page_number=1,
                text="测试局2025年决算 单位：万元",
                confidence=0.995,
                extraction_method="ocr",
            )
        ],
        ocr_table_candidates=[candidate],
    )
    from tests.ocr_v3_helpers import bind_fixture_tile
    bind_fixture_tile(candidate, artifact.coordinate_transforms['ocr_render_page_1'])
    return artifact, candidate


def test_geometry_order_is_not_array_order():
    result = map_table_cells(raw_table())
    assert result["mapping_status"] == "mapped"
    cell = next(cell for cell in result["cells"] if (cell["row_idx"], cell["col_idx"]) == (1, 3))
    assert cell["render_bbox"] == [300, 50, 400, 100] and cell["score"] == 0.995


def test_low_score_table_does_not_spend_region_budget_or_claim_recheck():
    from src.services.ocr_pipeline import _table_recheck_eligible

    table = map_table_cells(raw_table())
    assert _table_recheck_eligible(table)
    table["cells"][-1]["fragments"][0]["score"] = 0.979
    assert not _table_recheck_eligible(table)
    table["cells"][-1]["fragments"][0]["score"] = None
    assert not _table_recheck_eligible(table)


@pytest.mark.parametrize(
    "change",
    [
        lambda t: t["table_ocr_pred"]["rec_scores"].pop(),
        lambda t: t["table_ocr_pred"]["rec_scores"].__setitem__(0, float("nan")),
        lambda t: t["table_ocr_pred"]["rec_texts"].__setitem__(-1, "1000"),
        lambda t: t["cell_box_list"].pop(),
    ],
)
def test_missing_or_conflicting_structure_rejects(change):
    table = raw_table()
    change(table)
    assert map_table_cells(table)["mapping_status"] == "ambiguous"


def test_synthetic_complete_protocol_qualifies_without_certifying():
    artifact, candidate = fixture()
    result = qualification(
        candidate, artifact, organization="测试局", year=2025, report_kind="final"
    )
    assert result["status"] == "qualified", result
    assert result["minimum_score"] == 0.992 and result["evidence_verification"] == "unverified"
    summary = promote_ocr_candidates(
        artifact, organization="测试局", year=2025, report_kind="final"
    )
    assert summary["usable_for_rules"]
    context = build_fact_context_from_artifact(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    assert context and context.definite_facts()
    for fact in context.definite_facts():
        assert fact.recognition_status == "unverified"
        assert fact.extra_dims["evidence_refs"][0]["verification_status"] == "unverified"
        assert fact.extra_dims["evidence_refs"][0]["bbox"] != list(candidate.page_bbox)
    restored = ParseArtifact.from_dict(artifact.to_dict())
    assert (
        restored.table_fragments[0].cells[-1].ocr_provenance
        == artifact.table_fragments[0].cells[-1].ocr_provenance
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            lambda a, c: c.provenance.__setitem__("model_fingerprint", "f" * 64),
            "model_hash_mismatch",
        ),
        (lambda a, c: c.provenance.__setitem__("source_sha256", "f" * 64), "source_hash_mismatch"),
        (lambda a, c: c.cells[-1].__setitem__("score", 1.0), "cells_not_bound_to_raw_prediction"),
        (
            lambda a, c: c.cells[-1]["region_recheck"].__setitem__("text", "1000"),
            "region_recheck_failed_or_conflicting",
        ),
        (
            lambda a, c: a.coordinate_transforms["ocr_render_page_1"].__setitem__(
                "bbox_transform_verified", False
            ),
            "coordinate_transform_unverified",
        ),
        (
            lambda a, c: a.text_blocks[0].__setattr__("confidence", 0.979),
            "critical_recognition_score_below_threshold",
        ),
        (
            lambda a, c: a.text_blocks[0].__setattr__("text", "测试局2024年决算 单位：万元"),
            "year_scope_conflict",
        ),
        (
            lambda a, c: a.text_blocks.append(
                ParsedTextBlock(
                    block_uid="foreign",
                    page_number=1,
                    text="编制单位：其他局",
                    confidence=0.999,
                    extraction_method="ocr",
                )
            ),
            "table_subject_conflict",
        ),
        (
            lambda a, c: a.text_blocks.append(
                ParsedTextBlock(
                    block_uid="other-stage",
                    page_number=1,
                    text="收入支出预算表",
                    confidence=0.999,
                    extraction_method="ocr",
                )
            ),
            "table_stage_conflict",
        ),
        (
            lambda a, c: a.coordinate_transforms["ocr_render_page_1"].__setitem__(
                "ocr_response",
                {**c.provenance, "pipeline_fingerprint": "f" * 64},
            ),
            "identity_not_bound_to_raw_prediction",
        ),
    ],
)
def test_operand_qualification_negative_contracts(change, reason):
    artifact, candidate = fixture()
    change(artifact, candidate)
    result = qualification(
        candidate, artifact, organization="测试局", year=2025, report_kind="final"
    )
    assert result["status"] == "rejected" and reason in result["reasons"]


def test_no_attestation_is_created_from_recognizer_output(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "gbc.scan.attestations.v1",
                "frozen_at": "2026-10-06",
                "reviewed_by": "synthetic",
                "generated_by": "ocr",
                "regions": [],
            }
        )
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_attestations(path, digest) is None
    assert valid_score(float("inf")) is None
    assert valid_bbox([0, 0, float("nan"), 1]) is None


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_real_render_affine_matches_rotated_cropbox(tmp_path, rotation, cropped):
    import fitz
    from src.services.ocr_pipeline import _render_pages_png, _pdf_bbox_from_render_bbox

    path = tmp_path / "geometry.pdf"
    with fitz.open() as document:
        page = document.new_page(width=300, height=400)
        page.draw_rect(fitz.Rect(80, 90, 150, 160), color=(1, 0, 0), fill=(1, 0, 0))
        if cropped:
            page.set_cropbox(fitz.Rect(50, 60, 250, 350))
        page.set_rotation(rotation)
        document.save(path)
    images, transforms = _render_pages_png(str(path), [1], dpi=144)
    transform = transforms[1]
    assert transform["bbox_transform_verified"]
    width, height = transform["render_size_px"]
    assert _pdf_bbox_from_render_bbox([0, 0, width, height], transform) == pytest.approx(
        transform["page_rect"]
    )
    with fitz.open(path) as document:
        page = document[0]
        local = fitz.Rect(
            80 - 50 if cropped else 80,
            90 - 60 if cropped else 90,
            150 - 50 if cropped else 150,
            160 - 60 if cropped else 160,
        )
        display = local * page.rotation_matrix
        pixels = [coordinate * 2 for coordinate in display]
        # PyMuPDF 的可见裁剪框原点与原生解析基准不同。以原始对象作为独立参照。
        import pdfplumber
        with pdfplumber.open(path) as source:
            shape=(source.pages[0].rects+source.pages[0].curves)[0]
            expected=[shape['x0'],shape['top'],shape['x1'],shape['bottom']]
        assert _pdf_bbox_from_render_bbox(pixels, transform) == pytest.approx(expected)
        from PIL import Image
        import io

        image = Image.open(io.BytesIO(images[0]))
        centre = ((pixels[0] + pixels[2]) / 2, (pixels[1] + pixels[3]) / 2)
        assert image.getpixel(centre)[0] > 250 and image.getpixel(centre)[1] < 5
    assert _pdf_bbox_from_render_bbox([0, 0, width + 2, height], transform) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 0),
        ("O", None),
        ("1,234.50", 1234.5),
        ("12,34", None),
        ("（20.5）万元", -20.5),
        ("-20.50元", -20.5),
        ("NaN", None),
        ("1.2.3", None),
        ("9" * 1000, None),
    ],
)
def test_numeric_parser_preserves_supported_values(value, expected):
    from src.services.ocr_fact_promotion import _ocr_number

    assert _ocr_number(value) == expected


@pytest.mark.parametrize(
    "tamper", [None, "quote", "bbox", "physical_page", "file_hash", "model", "raw", "review"]
)
def test_independent_registry_contract_rejects_tampering(tmp_path, tamper):
    """Simulated reviewer fixture validates mechanics, not government truth."""
    import fitz
    from src.services.scan_evidence_certification import verify_scan_reference

    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 40), "100")
        document.save(path)
    box = [10.0, 10.0, 100.0, 100.0]
    with fitz.open(path) as document:
        matrix = fitz.Matrix(300 / 72, 300 / 72)
        whole = document[0].get_pixmap(matrix=matrix).tobytes("png")
        crop = document[0].get_pixmap(matrix=matrix, clip=fitz.Rect(box)).tobytes("png")

    def sha(data):
        return hashlib.sha256(data).hexdigest()

    source = {
        "model_fingerprint": sha(b"model"),
        "raw_prediction_sha256": sha(b"raw"),
        "render_sha256": sha(whole),
    }
    reference = dict(
        source_method="ocr",
        file_hash=sha(path.read_bytes()),
        physical_page=1,
        bbox=box,
        quote="100",
        source_cell_id="cell1",
        source_provenance={"source": source},
    )
    review = tmp_path / "review.record"
    review.write_text("simulated reviewer record for contract test only")
    record = {
        **reference,
        **source,
        "reviewed_by": "fixture-reviewer",
        "render_dpi": 300,
        "crop_sha256": sha(crop),
        "review_record_path": review.name,
        "review_record_sha256": sha(review.read_bytes()),
    }
    manifest_path = tmp_path / "attestations.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "gbc.scan.attestations.v1",
                "frozen_at": "fixture-date",
                "reviewed_by": "fixture-reviewer",
                "regions": [record],
            }
        )
    )
    manifest = load_attestations(manifest_path, sha(manifest_path.read_bytes()))
    if tamper in {"quote", "bbox", "physical_page", "file_hash"}:
        reference[tamper] = {
            "quote": "1000",
            "bbox": [11.0, 10.0, 100.0, 100.0],
            "physical_page": 2,
            "file_hash": "f" * 64,
        }[tamper]
    elif tamper in {"model", "raw"}:
        source["model_fingerprint" if tamper == "model" else "raw_prediction_sha256"] = "f" * 64
    elif tamper == "review":
        review.write_text("changed")
    assert verify_scan_reference(reference, path, manifest) is (tamper is None)
