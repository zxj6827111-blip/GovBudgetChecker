"""区域覆盖机制控制：页边面积不能替代实际表区，也不能吞掉第二张未知表。"""

from types import SimpleNamespace as NS
from decimal import Decimal

from src.services.table_reading_assessment import (
    assess_table_reading,
    constrain_reading_facts,
    READING_VERSION,
    usable_table_region,
)
from src.services.ocr_grid_reconstruction import GRID_RECONSTRUCTION_VERSION
from src.schemas.parse_artifact import FiscalFact


def scene():
    source = "a" * 64

    def grid(uid, box):
        return NS(
            page_number=2,
            extraction_method="image_grid",
            fragment_uid=uid,
            bbox=box,
            cells=[
                NS(
                    ocr_provenance={
                        "physical_grid": {
                            "file_hash": source,
                            "pixel_to_pdf": [1, 0, 0, 1, 0, 0],
                            "incomplete_regions": [],
                        }
                    }
                )
            ],
        )

    grids = [grid("first", [20, 100, 300, 300]), grid("second", [400, 400, 600, 600])]

    def table(uid, box):
        header = NS(
            raw_text="科目名称",
            is_header=True,
            page_number=2,
            bbox=box,
            native_text_sources=[],
            ocr_provenance={
                "version": GRID_RECONSTRUCTION_VERSION,
                "qualification": {"status": "rejected"},
            },
        )
        amount = NS(
            raw_text="123.45",
            is_header=False,
            page_number=2,
            bbox=box,
            native_text_sources=[],
            ocr_provenance={
                "version": GRID_RECONSTRUCTION_VERSION,
                "qualification": {"status": "qualified"},
            },
        )
        return NS(
            fragment_uid=uid,
            table_code="FIN_02_income",
            extraction_method="ocr",
            bbox=box,
            cells=[header, amount],
        )

    tables = [table("image-logical-first", grids[0].bbox)]
    artifact = NS(
        source_file_hash=source,
        source_grids=grids,
        table_fragments=tables,
        logical_tables=[],
        reading_assessment={
            "version": READING_VERSION,
            "pages": [
                {
                    "page": 2,
                    "width": 1000,
                    "height": 1400,
                    "table_hints": [],
                    "amounts": [],
                    "image_regions": [[0, 0, 1000, 1400]],
                    "blank_verified": False,
                }
            ],
        },
    )
    return artifact, table


def test_scanned_image_margin_does_not_block_a_locally_complete_table():
    artifact, _ = scene()
    artifact.source_grids = artifact.source_grids[:1]
    assert usable_table_region(artifact.table_fragments[0])
    result = assess_table_reading(artifact)
    assert result["gaps"] == [] and result["status"] == "complete_for_detected_regions"
    assert result["original_pdf_full_coverage_verified"] is False


def test_second_unknown_table_keeps_its_own_gap_and_does_not_poison_first_table():
    artifact, _ = scene()
    result = assess_table_reading(artifact)
    assert len(result["gaps"]) == 1 and result["gaps"][0]["bbox"] == artifact.source_grids[1].bbox
    assert result["gaps"][0]["affected_fragment_uids"] == []
    fact = FiscalFact(
        "first-source",
        "actual",
        "123.45",
        "万元",
        Decimal("123.45"),
        instance_uid="image-logical-first",
    )
    constrained = constrain_reading_facts(artifact, [fact])
    assert constrained[0].recognition_status == fact.recognition_status


def test_incomplete_business_amount_still_blocks_its_actual_region():
    artifact, _ = scene()
    artifact.source_grids = artifact.source_grids[:1]
    artifact.table_fragments[0].cells[1].ocr_provenance["qualification"]["status"] = "rejected"
    result = assess_table_reading(artifact)
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["affected_fragment_uids"] == ["image-logical-first"]
