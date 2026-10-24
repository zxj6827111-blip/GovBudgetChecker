"""读取缺口与补识别路由的工程回归；合成控制不充当业务盲测。"""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.schemas.parse_artifact import (
    LogicalTableInstance,
    OcrTableCandidate,
    ParseArtifact,
    ParsePageState,
    ParsedCell,
    ParsedTable,
    ParsedTextBlock,
)
from src.services.table_reading_assessment import (
    READING_VERSION,
    assess_table_reading,
    collect_page_probe,
    enrich_page_assessment,
    native_region_conflicts,
    recovery_pages,
)

CODE = "FIN_03_expenditure"


def native_artifact():
    rows = [
        ["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
        ["201", "一般公共服务支出", "100.00", "80.00", "20.00"],
    ]
    xs = [10, 70, 200, 300, 400, 500]
    cells = [
        ParsedCell(
            row_idx=r,
            col_idx=c,
            raw_text=text,
            normalized_text=text,
            numeric_value=float(text) if r and c >= 2 else None,
            page_number=1,
            bbox=(xs[c], 100 + r * 20, xs[c + 1], 120 + r * 20),
            is_header=not r,
            confidence=0.99,
            unit_hint="万元",
            extraction_method="lines",
            normalized_value_wan=Decimal(text) if r and c >= 2 else None,
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    table = ParsedTable(
        table_code=CODE,
        title="支出决算表",
        page_number=1,
        table_index=0,
        bbox=(10, 100, 500, 140),
        header_row_count=1,
        confidence=0.99,
        extraction_method="lines",
        unit_hint="万元",
        rows=rows,
        cells=cells,
        fragment_uid="native-table",
    )
    probe = dict(
        page=1,
        width=600,
        height=800,
        blank_verified=False,
        table_hints=[CODE],
        image_regions=[],
        amounts=[
            dict(raw_text=c.raw_text, bbox=list(c.bbox))
            for c in cells
            if c.row_idx and c.col_idx >= 2
        ],
    )
    return ParseArtifact(
        source_file_hash="a" * 64,
        page_count=1,
        page_states=[ParsePageState(page_number=1, has_text_layer=True, text_chars=500)],
        table_fragments=[table],
        physical_fragments=[deepcopy(table)],
        logical_tables=[
            LogicalTableInstance(
                instance_uid="native-table",
                table_code=CODE,
                fragment_uids=["native-table"],
                title="支出决算表",
                page_start=1,
                page_end=1,
                row_count=2,
            )
        ],
        reading_assessment=dict(version=READING_VERSION, pages=[probe]),
    )


def good_pages():
    return dict(
        page_count=1,
        page_coverage=1,
        text_page_count=1,
        scanned_pages=[],
        low_text_pages=[],
        scanned_page_count=0,
        low_text_page_count=0,
    )


def test_source_values_and_probes_roundtrip_without_changing_old_snapshots():
    artifact = native_artifact()
    assert ParseArtifact.from_dict(artifact.to_dict()).to_dict() == artifact.to_dict()
    legacy = ParseArtifact(source_file_hash="old").to_dict()
    assert "reading_assessment" not in legacy
    assert ParseArtifact.from_dict(legacy).to_dict() == legacy


def test_native_complete_table_does_not_schedule_ocr():
    artifact = native_artifact()
    reading = assess_table_reading(artifact)
    assert reading["status"] == "complete_for_detected_regions"
    assert not reading["original_pdf_full_coverage_verified"]
    assert recovery_pages(enrich_page_assessment(good_pages(), artifact), artifact) == []


def test_dropped_or_changed_amount_is_found_and_other_operands_are_blocked():
    artifact = native_artifact()
    table = artifact.table_fragments[0]
    table.cells = [c for c in table.cells if not (c.row_idx == 1 and c.col_idx == 2)]
    enriched = enrich_page_assessment(good_pages(), artifact)
    assert enriched["low_text_pages"] == []
    assert enriched["table_recovery_pages"] == [1]
    gap = enriched["table_reading"]["gaps"][0]
    assert gap["reason"] == "native_amount_not_extracted" and gap["raw_text"] == "100.00"
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    assert (
        context
        and context.facts
        and all(f.recognition_status == "low_confidence" for f in context.facts)
    )
    assert all(
        operand_rejection_reason(replace(f, recognition_status="verified")) for f in context.facts
    )


def test_separate_unaffected_instance_keeps_usable_operands():
    artifact = native_artifact()
    second = deepcopy(artifact.table_fragments[0])
    second.fragment_uid = "other-table"
    second.bbox = (10, 400, 500, 440)
    for c in second.cells:
        c.bbox = tuple(v + 300 if k % 2 else v for k, v in enumerate(c.bbox))
    artifact.table_fragments.append(second)
    artifact.logical_tables.append(
        replace(
            artifact.logical_tables[0], instance_uid="other-table", fragment_uids=["other-table"]
        )
    )
    artifact.table_fragments[0].cells = [
        c for c in artifact.table_fragments[0].cells if c.col_idx != 2
    ]
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    assert context and any(
        f.instance_uid == "other-table" and not operand_rejection_reason(f) for f in context.facts
    )


def test_large_image_in_text_page_is_not_hidden_by_text_coverage():
    artifact = native_artifact()
    artifact.table_fragments = []
    artifact.physical_fragments = []
    artifact.reading_assessment["pages"][0]["image_regions"] = [[20, 200, 500, 500]]
    enriched = enrich_page_assessment(good_pages(), artifact)
    assert enriched["page_coverage"] == 1
    assert enriched["table_recovery_pages"] == [1]
    assert {g["reason"] for g in enriched["table_reading"]["gaps"]} >= {
        "table_title_without_usable_cells",
        "image_region_not_structured",
    }


def test_blank_page_is_distinct_from_scanned_graphics():
    page = SimpleNamespace(
        width=600, height=800, chars=[], images=[], lines=[], rects=[], curves=[]
    )
    blank = collect_page_probe(page, 1, "", [])
    assert blank["blank_verified"]
    artifact = ParseArtifact(
        source_file_hash="x",
        page_count=1,
        reading_assessment=dict(version=READING_VERSION, pages=[blank]),
    )
    enriched = enrich_page_assessment(
        dict(page_count=1, low_text_pages=[1], scanned_pages=[1]), artifact
    )
    assert enriched["blank_pages"] == [1] and enriched["scanned_pages"] == []
    page.curves = [{}]
    assert not collect_page_probe(page, 1, "", [])["blank_verified"]


def test_ocr_page_union_is_bounded_deduplicated_and_excludes_verified_blanks():
    assessment = dict(
        page_count=5,
        low_text_pages=[1, 3],
        scanned_pages=[2],
        table_recovery_pages=[3, 4, 99, -1, True, "2"],
        blank_pages=[1],
    )
    assert recovery_pages(assessment) == [2, 3, 4]


def test_nonoverlapping_image_table_in_native_page_can_be_qualified():
    artifact = native_artifact()
    artifact.table_fragments = []
    artifact.text_blocks = [
        ParsedTextBlock(
            block_uid="heading",
            page_number=1,
            text="测试局2025年决算",
            bbox=(10, 10, 300, 40),
            extraction_method="native_text",
        )
    ]
    candidate = OcrTableCandidate(
        candidate_uid="ocr", page_number=1, rows=[], page_bbox=(20, 200, 500, 500)
    )
    assert not native_region_conflicts(artifact, candidate)
    artifact.text_blocks[0].bbox = (20, 220, 300, 240)
    assert native_region_conflicts(artifact, candidate)
    artifact.text_blocks[0].bbox = None
    assert native_region_conflicts(artifact, candidate)


def test_ocr_cannot_become_a_duplicate_table_over_a_native_grid():
    from src.services.ocr_fact_promotion import promote_ocr_candidates

    artifact = native_artifact()
    artifact.ocr_table_candidates = [
        OcrTableCandidate(
            candidate_uid="ocr",
            page_number=1,
            rows=[["科目名称", "合计"], ["合计", "999"]],
            page_bbox=(10, 100, 500, 140),
        )
    ]
    before = deepcopy(artifact.table_fragments[0].rows)
    result = promote_ocr_candidates(artifact)
    assert result["rejected_tables"][0]["reason"] == "native_table_overlap_requires_review"
    assert len(artifact.table_fragments) == 1 and artifact.table_fragments[0].rows == before


def test_unqualified_ocr_never_closes_a_reading_gap():
    artifact = native_artifact()
    table = artifact.table_fragments[0]
    table.extraction_method = "ocr"
    assert assess_table_reading(artifact)["status"] == "partial"
    for c in table.cells:
        c.ocr_provenance = {"qualification": {"status": "qualified"}}
    assert assess_table_reading(artifact)["status"] == "complete_for_detected_regions"


def test_qualified_header_cannot_hide_unqualified_money_in_an_image():
    artifact = native_artifact()
    table = artifact.table_fragments[0]
    table.extraction_method = "ocr"
    artifact.reading_assessment["pages"][0]["amounts"] = []
    artifact.reading_assessment["pages"][0]["image_regions"] = [list(table.bbox)]
    for cell in table.cells:
        if cell.is_header:
            cell.ocr_provenance = {"qualification": {"status": "qualified"}}
    reading = assess_table_reading(artifact)
    assert reading["status"] == "partial"
    assert "image_region_not_structured" in {gap["reason"] for gap in reading["gaps"]}
    for cell in table.cells:
        cell.ocr_provenance = {"qualification": {"status": "qualified"}}
    assert assess_table_reading(artifact)["status"] == "complete_for_detected_regions"


def test_full_page_scan_counts_as_structured_only_when_the_table_itself_was_fully_read():
    # 整页扫描图含表题、单位行和页边，表格永远盖不满整图 80%；改看整页识别留下的未读区域落在哪里。
    artifact = native_artifact()
    table = artifact.table_fragments[0]
    table.extraction_method = "ocr"
    for cell in table.cells:
        cell.ocr_provenance = {"qualification": {"status": "qualified"}}
    probe = artifact.reading_assessment["pages"][0]
    probe["amounts"] = []
    probe["image_regions"] = [[0, 0, 595, 841]]
    reasons = lambda: {gap["reason"] for gap in assess_table_reading(artifact)["gaps"]}  # noqa: E731
    assert "image_region_not_structured" in reasons()  # 没跑过整页识别：不能宣告读完
    recovery = {"physical_page": 1, "page_recovery": {"unresolved_pdf_regions": [[10, 20, 300, 40]]}}
    artifact.coordinate_transforms = {"tile": recovery}
    assert "image_region_not_structured" not in reasons()  # 只有表外的编制部门行没读出
    recovery["page_recovery"]["unresolved_pdf_regions"].append([210, 125, 290, 135])
    assert "image_region_not_structured" in reasons()  # 表内还有没读出的墨迹
    recovery["page_recovery"]["unresolved_pdf_regions"].pop()
    artifact.text_blocks = [ParsedTextBlock(block_uid="ocr:block-0", page_number=1, text="12,345.60",
                                            bbox=(200, 300, 260, 315), extraction_method="ocr")]
    assert "image_region_not_structured" in reasons()  # 表外还有像金额的文字，可能是第二张表


def test_promoted_ocr_retains_physical_source_cells_for_database_readback():
    from src.services.ocr_fact_promotion import promote_ocr_candidates
    from tests.remediation.test_round11_ocr import fixture

    artifact, _ = fixture()
    result = promote_ocr_candidates(artifact, organization="测试局", year=2025, report_kind="final")
    assert result["candidate_materialized"]
    logical = artifact.table_fragments[-1]
    physical = next(
        t for t in artifact.physical_fragments if t.fragment_uid == logical.fragment_uid
    )
    assert physical is not logical and physical.rows == logical.rows
    assert [(c.raw_text, c.page_number, c.bbox) for c in physical.cells] == [
        (c.raw_text, c.page_number, c.bbox) for c in logical.cells
    ]


@pytest.mark.asyncio
async def test_native_table_gap_on_text_layer_page_is_not_sent_to_ocr(monkeypatch):
    # 文字层页的原生读取缺口由读取门禁报出（见下一条），OCR 读不出比文字层更多的内容。
    from src.services import ocr_pipeline

    artifact = native_artifact()
    artifact.table_fragments = []
    assessment = enrich_page_assessment(good_pages(), artifact)
    assert assessment["table_recovery_pages"] == [1]
    monkeypatch.setattr(ocr_pipeline, "ocr_enabled", lambda: True)
    assert await ocr_pipeline.maybe_ocr_scanned_pages("not-opened.pdf", assessment, artifact) is None


def image_table_artifact():
    artifact = native_artifact()
    artifact.table_fragments = []
    artifact.reading_assessment["pages"][0]["image_regions"] = [[20, 200, 580, 700]]
    return artifact


@pytest.mark.asyncio
async def test_embedded_table_image_routes_to_ocr_even_with_plenty_of_text(monkeypatch):
    from src.services import ocr_pipeline

    artifact = image_table_artifact()
    assessment = enrich_page_assessment(good_pages(), artifact)
    monkeypatch.setattr(ocr_pipeline, "ocr_enabled", lambda: True)
    monkeypatch.setenv("OCR_MODEL_FINGERPRINT", "b" * 64)

    async def unavailable():
        return {"ok": False, "error": "test unavailable"}

    monkeypatch.setattr(ocr_pipeline, "check_ocr_health", unavailable)
    result = await ocr_pipeline.maybe_ocr_scanned_pages("not-opened.pdf", assessment, artifact)
    assert result["requested_pages"] == [1] and result["status"] == "service_unavailable"
    assert result["usable_for_rules"] is False


def test_only_pages_without_a_usable_text_layer_are_ocr_candidates():
    artifact = native_artifact()
    artifact.table_fragments = []
    probe = artifact.reading_assessment["pages"][0]
    covers = dict(good_pages(), low_text_pages=[1], low_text_page_count=1)
    # 有文字层的封面：字少但没有大图，不送 OCR。
    assert recovery_pages(enrich_page_assessment(covers, artifact), artifact) == []
    probe["vector_curves"] = 800  # 表题之下的表格字形被转曲
    assert recovery_pages(enrich_page_assessment(good_pages(), artifact), artifact) == [1]
    probe["vector_curves"] = 0
    probe["image_regions"] = [[20, 200, 580, 700]]
    assert recovery_pages(enrich_page_assessment(covers, artifact), artifact) == [1]


@pytest.mark.asyncio
async def test_disabled_recovery_reports_the_unresolved_gap(monkeypatch):
    from src.services import ocr_pipeline

    artifact = image_table_artifact()
    monkeypatch.setattr(ocr_pipeline, "ocr_enabled", lambda: False)
    result = await ocr_pipeline.maybe_ocr_scanned_pages(
        "not-opened.pdf", enrich_page_assessment(good_pages(), artifact), artifact
    )
    assert result["status"] == "disabled" and result["failed_pages"] == [1]


def test_reading_gate_also_blocks_without_a_database():
    from api.main import _evaluate_quality_gate

    artifact = native_artifact()
    artifact.table_fragments = []
    gate = _evaluate_quality_gate(
        enrich_page_assessment(good_pages(), artifact), "final", 2025, False, False, 0
    )
    assert gate["status"] == "review_required"
    assert "table_reading_incomplete" in {r["code"] for r in gate["review_reasons"]}


def test_unbound_source_money_is_a_reading_gate_reason():
    from api.main import _evaluate_quality_gate

    gate = _evaluate_quality_gate(
        good_pages(),
        "final",
        2025,
        False,
        False,
        0,
        structured_ingest={
            "reading_import": {
                "reading_quality": {
                    "gaps": [],
                    "unbound_money_cells": [{"page": 1, "reason": "source_label_missing"}],
                }
            }
        },
    )
    reason = next(r for r in gate["review_reasons"] if r["code"] == "table_reading_incomplete")
    assert reason["unbound_money_cell_count"] == 1


def test_gap_ids_do_not_shift_when_an_earlier_gap_is_recovered():
    artifact = native_artifact()
    artifact.table_fragments = []
    before = assess_table_reading(artifact)["gaps"]
    expected = next(g for g in before if g.get("raw_text") == "80.00")["id"]
    artifact.reading_assessment["pages"][0]["amounts"] = artifact.reading_assessment["pages"][0][
        "amounts"
    ][1:]
    after = assess_table_reading(artifact)["gaps"]
    assert next(g for g in after if g.get("raw_text") == "80.00")["id"] == expected


def test_split_thousands_word_is_not_treated_as_an_independent_amount():
    from src.services.table_reading_assessment import number

    assert number(",266.43") is None
    assert number("4,266.43") == Decimal("4266.43")


def test_unread_open_tail_also_blocks_the_existing_table():
    artifact = native_artifact()
    artifact.reading_assessment["pages"][0]["amounts"].append(
        {"raw_text": "7.00", "bbox": [210, 145, 240, 155]}
    )
    context = build_fact_context_from_artifact(artifact, report_kind="final")
    assert context and context.facts
    assert all(operand_rejection_reason(fact) for fact in context.facts)


def test_ocr_transform_zero_is_stable_after_jsonb_normalization(tmp_path):
    import json
    import fitz
    from src.services.ocr_pipeline import _render_pages_png
    from src.services.parse_artifact_store import artifact_payload_hash

    pdf = tmp_path / "zero.pdf"
    with fitz.open() as doc:
        doc.new_page(width=100, height=100)
        doc.save(pdf)
    _, transforms = _render_pages_png(str(pdf), [1])
    payload = {"transform": transforms[1]}
    encoded = json.dumps(payload)
    assert "-0.0" not in encoded
    assert artifact_payload_hash(payload) == artifact_payload_hash(
        json.loads(encoded.replace("-0.0", "0.0"))
    )
