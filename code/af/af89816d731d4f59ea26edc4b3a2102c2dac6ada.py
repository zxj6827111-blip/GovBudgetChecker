import copy
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from ocr_service.protocol import validate_tile_context, PROTOCOL_VERSION
from src.engine.fact_context import FactContext
from src.schemas.parse_artifact import FiscalFact, ParseArtifact
from src.services.analysis_fact_snapshot import (
    AnalysisFactSnapshot,
    stage_candidate,
    read_candidate,
)
from src.services.fiscal_input_builder import FiscalInputBuilder, VERSIONS
from src.services.input_geometry import Affine
from src.services.ocr_tiles import make_tile, tile_boxes, recognize_tiles
from src.services.ocr_client import OcrBlock, OcrPageResult


def contexts():
    fact = FiscalFact(
        "physical:r1c1",
        "actual",
        "12.34",
        "万元",
        Decimal("12.34"),
        extra_dims={
            "evidence_refs": [{"file_hash": "a" * 64, "physical_page": 1, "source_cell_id": "r1c1"}]
        },
    )
    raw = FactContext([fact], file_hash="a" * 64, parse_run_uid="run")
    effective = copy.deepcopy(raw)
    effective.facts[0].normalized_value = Decimal("99.99")
    effective.overlay_records = [{"field": "amount", "value": "99.99"}]
    effective.version_token = effective.compute_version_token()
    return raw, effective


def test_source_and_effective_remain_distinct_and_digest_verified(tmp_path):
    raw, effective = contexts()
    bundle = AnalysisFactSnapshot.create(
        revision_id="rev-check", raw=raw, effective=effective, versions=VERSIONS
    )
    assert bundle.to_dict()["raw_context"]["facts"][0]["normalized_value"] == "12.34"
    assert bundle.to_dict()["effective_context"]["facts"][0]["normalized_value"] == "99.99"
    assert bundle.summary()["raw_digest"] != bundle.summary()["effective_digest"]
    payload = {
        "job_id": "test",
        "analysis_revision": "rev-check",
        "result": {"meta": {"fact_snapshot": bundle.summary()}},
    }
    path = stage_candidate(tmp_path, payload, bundle)
    saved = path.read_bytes()
    effective.facts[0].normalized_value = Decimal("55")
    staged_payload, restored = read_candidate(path)
    assert restored.payload_json == bundle.payload_json and path.read_bytes() == saved
    assert staged_payload == payload
    tampered = json.loads(path.read_text())
    tampered["snapshot"]["effective_digest"] = "0" * 64
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError):
        read_candidate(path)


def test_v1_cannot_be_published_as_v2():
    raw, effective = contexts()
    raw.context_version = "fact-context-v1"
    with pytest.raises(ValueError, match="read-only"):
        AnalysisFactSnapshot.create(
            revision_id="rev-history", raw=raw, effective=effective, versions=VERSIONS
        )


def test_tile_protocol_binds_pixels_parent_page_and_inverse():
    image = Image.new("RGB", (500, 400), "white")
    request = make_tile(
        image,
        (20, 40, 300, 350),
        parent_sha="b" * 64,
        file_hash="a" * 64,
        page=9,
        pixel_to_pdf=Affine(a=0.24, d=0.24),
    )
    validate_tile_context(request.context, request.image, (280, 310))
    assert request.context["source_region"] == [4.8, 9.6, 72, 84]
    with pytest.raises(ValueError):
        validate_tile_context(request.context, request.image + b"changed", (280, 310))
    altered = copy.deepcopy(request.context)
    altered["physical_page"] = 10
    with pytest.raises(ValueError):
        validate_tile_context(altered, request.image, (280, 310))


def test_tile_coverage_has_no_holes_and_never_shrinks():
    image = Image.new("RGB", (2500, 3600), "white")
    boxes = tile_boxes(image)
    for y in range(0, image.height, 23):
        for x in range(0, image.width, 23):
            assert any(b[0] <= x < b[2] and b[1] <= y < b[3] for b in boxes)
    assert all(b[2] - b[0] <= 2048 and b[3] - b[1] <= 1536 for b in boxes)


def test_inverse_uses_four_corners_for_skew():
    transform = Affine(0.96, 0.1, -0.2, 0.98, 25, 16)
    for p in [(0, 0), (30, 40), (300, 900)]:
        assert transform.inverse().point(*transform.point(*p)) == pytest.approx(p)
    box = transform.box([1, 2, 3, 4])
    assert box[0] < box[2] and box[1] < box[3]


@pytest.mark.asyncio
async def test_deleted_first_pass_region_can_be_recovered(monkeypatch, tmp_path):
    image = Image.new("RGB", (180, 100), "white")
    ImageDraw.Draw(image).text((20, 20), "123.45", fill="black")
    calls = []

    async def infer(client, **kwargs):
        calls.append(kwargs)
        blocks = (
            []
            if kwargs["tile_context"]["attempt"] == 0
            else [OcrBlock("123.45", 0.999, [0, 0, 100, 40], 1)]
        )
        return OcrPageResult(1, blocks=blocks, provenance={"schema_version": PROTOCOL_VERSION})

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    async with httpx.AsyncClient() as client:
        _, rechecks, state = await recognize_tiles(
            image,
            file_hash="a" * 64,
            page=1,
            pixel_to_pdf=Affine(a=0.24, d=0.24),
            client=client,
            model="b" * 64,
            pipeline="c" * 64,
            checkpoint_path=tmp_path / "checkpoint.json",
        )
    assert rechecks and any(c["tile_context"]["attempt"] > 0 for c in calls)
    assert state["status"] == "completed"
    assert all(c["timeout_seconds"] <= 120 for c in calls)


@pytest.mark.asyncio
async def test_budget_exhaustion_is_pending_and_resumable(monkeypatch, tmp_path):
    image = Image.new("RGB", (1500, 100), "white")
    calls = []

    async def infer(client, **kwargs):
        calls.append(kwargs)
        return OcrPageResult(1)

    monkeypatch.setattr("src.services.ocr_client.ocr_page", infer)
    checkpoint = tmp_path / "checkpoint.json"
    async with httpx.AsyncClient() as client:
        _, _, first = await recognize_tiles(
            image,
            file_hash="a" * 64,
            page=1,
            pixel_to_pdf=Affine(),
            client=client,
            model="b" * 64,
            pipeline="c" * 64,
            checkpoint_path=checkpoint,
            soft_budget=0,
        )
        assert first["status"] == "pending" and first["pending"] and not calls
        _, _, second = await recognize_tiles(
            image,
            file_hash="a" * 64,
            page=1,
            pixel_to_pdf=Affine(),
            client=client,
            model="b" * 64,
            pipeline="c" * 64,
            checkpoint_path=checkpoint,
        )
        assert second["status"] == "completed"
        count = len(calls)
        await recognize_tiles(
            image,
            file_hash="a" * 64,
            page=1,
            pixel_to_pdf=Affine(),
            client=client,
            model="b" * 64,
            pipeline="c" * 64,
            checkpoint_path=checkpoint,
        )
        assert len(calls) == count


def test_reconstruction_roundtrip_and_idempotence():
    artifact = ParseArtifact("a" * 64, parse_run_uid="raw")
    builder = FiscalInputBuilder()
    builder.reconstruct(artifact)
    state = artifact.to_dict()
    builder.reconstruct(artifact)
    assert artifact.to_dict() == state
    assert ParseArtifact.from_dict(state).to_dict() == state


@pytest.mark.asyncio
async def test_database_bound_snapshot_read_checks_raw_effective_and_summary():
    from src.services.analysis_fact_snapshot import read_bound_snapshot
    raw,effective=contexts()
    bundle=AnalysisFactSnapshot.create(revision_id='rev-db-read',raw=raw,effective=effective,versions=VERSIONS)
    data=bundle.to_dict()
    row={**data,'snapshot_sha256':bundle.digest}
    class Connection:
        async def fetchrow(self,query,*args):
            assert 'analysis_fact_snapshots' in query and args==(17,'rev-db-read')
            return row
    restored=await read_bound_snapshot(Connection(),job_db_id=17,revision_id='rev-db-read',expected=bundle.summary())
    assert restored.payload_json==bundle.payload_json
    row['effective_context']['facts'][0]['raw_value']='999.99'
    with pytest.raises(ValueError,match='SHA-256'):
        await read_bound_snapshot(Connection(),job_db_id=17,revision_id='rev-db-read',expected=bundle.summary())
