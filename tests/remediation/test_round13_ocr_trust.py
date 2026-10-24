"""R12 reproductions use synthetic contracts, never independent human truth."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import pytest

from scripts.run_isolated_service_validation import service_lifetime
from scripts.run_zc07_acceptance import _authenticated_reference, _frozen_scan_manifest
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.evidence_guard import apply_evidence_completeness, evaluate_finding_evidence
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.services.ocr_qualification import qualification
from src.services.scan_evidence_certification import load_attestations
from tests.remediation.test_round11_ocr import fixture


def sha(data):
    return hashlib.sha256(data).hexdigest()


def reference_id(ref):
    ref["evidence_id"] = hashlib.sha1(
        "|".join(
            (ref["file_hash"], str(ref["physical_page"]), ref["source_cell_id"], ref["quote"])
        ).encode()
    ).hexdigest()[:16]


@pytest.fixture
def certificate(tmp_path, monkeypatch):
    import fitz

    directory = tmp_path / "corpus" / "DOC-CONTRACT"
    directory.mkdir(parents=True)
    path = directory / "original.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 40), "100")
        image = page.get_pixmap().tobytes("png")
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=200, height=200)
            page.insert_image(page.rect, stream=image)
        doc.save(path)
    box = [10.0, 10.0, 100.0, 100.0]
    with fitz.open(path) as doc:
        matrix = fitz.Matrix(300 / 72, 300 / 72)
        whole = doc[0].get_pixmap(matrix=matrix).tobytes("png")
        crop = doc[0].get_pixmap(matrix=matrix, clip=fitz.Rect(box)).tobytes("png")
        assert not doc[0].get_text()
    source = dict(
        model_fingerprint=sha(b"model"), raw_prediction_sha256=sha(b"raw"), render_sha256=sha(whole)
    )
    ref = dict(
        source_method="ocr",
        file_hash=sha(path.read_bytes()),
        physical_page=1,
        bbox=box,
        quote="100",
        source_cell_id="cell1",
        source_identity_status="verified",
        verification_status="unverified",
        location_level="exact_cell",
        source_provenance={"source": source},
    )
    reference_id(ref)
    review = directory / "reviews" / "fixture.record"
    review.parent.mkdir()
    review.write_text("synthetic reviewer fixture only, not government truth")
    record = {
        **ref,
        **source,
        "reviewed_by": "fixture-reviewer",
        "render_dpi": 300,
        "crop_sha256": sha(crop),
        "review_record_path": "reviews/fixture.record",
        "review_record_sha256": sha(review.read_bytes()),
    }
    manifest_path = directory / "attestations.json"
    manifest_path.write_text(
        json.dumps(
            dict(
                schema_version="gbc.scan.attestations.v1",
                frozen_at="fixture-date",
                reviewed_by="fixture-reviewer",
                regions=[record],
            )
        )
    )
    digest = sha(manifest_path.read_bytes())
    monkeypatch.setenv("OCR_EVIDENCE_ATTESTATIONS_PATH", str(manifest_path))
    monkeypatch.setenv("OCR_EVIDENCE_ATTESTATIONS_SHA256", digest)
    return path, ref, manifest_path, digest


@pytest.mark.parametrize("page_texts", [None, ["", ""], ["100", "100"]])
@pytest.mark.parametrize("change", ["cell", "bbox", "page", "raw", "model", "file", "id"])
def test_certification_cannot_be_borrowed(certificate, page_texts, change):
    path, good, manifest_path, digest = certificate
    bad = copy.deepcopy(good)
    if change == "cell":
        bad["source_cell_id"] = "unreviewed-cell2"
    elif change == "bbox":
        bad["bbox"] = [150.0, 150.0, 180.0, 180.0]
    elif change == "page":
        bad["physical_page"] = 2
    elif change == "file":
        bad["file_hash"] = "f" * 64
    elif change != "id":
        bad["source_provenance"]["source"][
            "raw_prediction_sha256" if change == "raw" else "model_fingerprint"
        ] = "f" * 64
    reference_id(bad)
    if change == "id":
        bad["evidence_id"] = "f" * 16
    bad["verification_status"] = "verified"  # A stale caller claim cannot authenticate it.
    finding = dict(rule_id="C-001", source="rule", severity="high", evidence=[good, bad])
    payload = {"rule_findings": [finding]}
    summary = apply_evidence_completeness(
        payload,
        document_page_count=2,
        page_texts=page_texts,
        source_file_hash=good["file_hash"],
        source_pdf_path=str(path),
    )
    assert finding["evidence_verification"]["status"] == "unverified"
    assert finding["evidence"][1]["verification_status"] == "unverified"
    assert summary["verified_count"] == 0
    manifest = load_attestations(manifest_path, digest)
    assert [
        _authenticated_reference(ref, good["file_hash"], path, manifest)
        for ref in finding["evidence"]
    ] == [True, False]


def test_same_reference_repeated_is_authenticated(certificate):
    path, ref, _, _ = certificate
    finding = {"evidence": [ref, copy.deepcopy(ref)], "text_snippet": "100"}
    complete, _, verification = evaluate_finding_evidence(
        finding,
        document_page_count=2,
        page_texts=["", ""],
        source_file_hash=ref["file_hash"],
        source_pdf_path=str(path),
    )
    assert complete and verification["status"] == "verified"


def test_native_and_uncertified_scan_stay_partly_unverified(certificate):
    path, ref, _, _ = certificate
    native = copy.deepcopy(ref)
    native.update(source_method="native", source_cell_id="native-cell")
    reference_id(native)
    scan = copy.deepcopy(ref)
    scan["source_cell_id"] = "unreviewed-cell"
    reference_id(scan)
    _, _, verification = evaluate_finding_evidence(
        {"evidence": [native, scan]},
        document_page_count=2,
        page_texts=["100", ""],
        source_file_hash=ref["file_hash"],
        source_pdf_path=str(path),
    )
    assert verification["status"] == "unverified"
    _, _, verification = evaluate_finding_evidence(
        {"evidence": [native]},
        document_page_count=2,
        page_texts=["100", ""],
        source_file_hash=ref["file_hash"],
        source_pdf_path=str(path),
    )
    assert verification["status"] == "verified"


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda c: c.rows[1].__setitem__(2, "900"), "rows_not_bound_to_raw_prediction"),
        (
            lambda c: c.cells[6].__setitem__("page_bbox", [1.0, 1.0, 2.0, 2.0]),
            "cell_page_bbox_not_bound_to_render",
        ),
        (lambda c: c.cells[6].__setitem__("row_idx", 0), "cells_not_bound_to_raw_prediction"),
        (lambda c: c.cells[6].__setitem__("col_idx", 1), "cells_not_bound_to_raw_prediction"),
        (lambda c: c.cells[6].__setitem__("row_span", 2), "cells_not_bound_to_raw_prediction"),
        (lambda c: c.cells[6].__setitem__("col_span", 2), "cells_not_bound_to_raw_prediction"),
        (
            lambda c: c.cells[6]["region_recheck"]["provenance"].__setitem__(
                "raw_prediction_sha256", "f" * 64
            ),
            "region_not_bound_to_raw_prediction",
        ),
    ],
)
def test_tampered_materialisation_has_no_definite_operand(change, reason):
    artifact, candidate = fixture()
    change(candidate)
    result = qualification(
        candidate, artifact, organization="测试局", year=2025, report_kind="final"
    )
    assert result["status"] == "rejected" and reason in result["reasons"]
    promote_ocr_candidates(artifact, organization="测试局", year=2025, report_kind="final")
    context = build_fact_context_from_artifact(
        artifact, org_name="测试局", fiscal_year=2025, report_kind="final"
    )
    if reason == 'region_not_bound_to_raw_prediction':
        # v3逐格裁定：损坏补读属于合计格；独立、完整的财政拨款格仍可用。
        assert all(f.indicator != 'total_actual' for f in context.definite_facts())
    else:
        assert not context.definite_facts()
    assert not any(str(fact.raw_value) in {"900", "900.0"} for fact in context.facts)


def test_recheck_input_changed_in_both_candidate_fields_cannot_change_request_journal():
    artifact, candidate = fixture()
    recheck = candidate.cells[6]["region_recheck"]
    recheck["input_sha256"] = "f" * 64
    recheck["provenance"]["input_sha256"] = "f" * 64
    result = qualification(
        candidate, artifact, organization="测试局", year=2025, report_kind="final"
    )
    assert (
        result["status"] == "rejected" and "region_request_not_bound_to_cell" in result["reasons"]
    )


@pytest.mark.parametrize("failure", [False, True])
def test_active_writer_stops_before_temporary_directory_is_removed(failure):
    processes, handles = [], []
    with pytest.raises(RuntimeError) if failure else tempfile.TemporaryDirectory() as _:
        with (
            tempfile.TemporaryDirectory(prefix="round13-writer-") as work,
            service_lifetime(processes, handles),
        ):
            log = open(Path(work) / "writer.log", "w")
            handles.append(log)
            writer = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                    "exec('while True:\\n p.mkdir(exist_ok=True)\\n (p / \\\"tick\\\").write_text(str(time.time()))\\n time.sleep(.001)')",
                    str(Path(work) / "uploads"),
                ],
                stdout=log,
                stderr=log,
            )
            processes.append(writer)
            time.sleep(0.08)
            assert writer.poll() is None
            if failure:
                raise RuntimeError("validation failed")
    assert writer.poll() is not None and log.closed and not Path(work).exists()


@pytest.mark.parametrize(
    "change", [None, "missing_file", "missing_sha", "wrong_sha", "escape", "absolute"]
)
def test_formal_scan_manifest_assembly(certificate, change):
    path, ref, manifest_path, digest = certificate
    golden = dict(scan_attestations_file=manifest_path.name, scan_attestations_sha256=digest)
    if change == "missing_file":
        golden.pop("scan_attestations_file")
    elif change == "missing_sha":
        golden.pop("scan_attestations_sha256")
    elif change == "wrong_sha":
        golden["scan_attestations_sha256"] = "f" * 64
    elif change == "escape":
        golden["scan_attestations_file"] = "../DOC-CONTRACT/attestations.json/../../escape.json"
    elif change == "absolute":
        golden["scan_attestations_file"] = str(manifest_path)
    manifest = _frozen_scan_manifest(golden, path.parents[2], "DOC-CONTRACT")
    assert bool(manifest) is (change is None)
    if manifest:
        ref["verification_status"] = "verified"
        assert _authenticated_reference(ref, ref["file_hash"], path, manifest)
