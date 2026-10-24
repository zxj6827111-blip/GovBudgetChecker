"""ZC-07 发布门禁：缺少真实真值或服务证据必须 fail-closed。"""

import hashlib
import json
from pathlib import Path
import pytest

from scripts import run_zc07_acceptance as zc07


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_default_preflight_writes_nonpassing_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        zc07,
        "_environment",
        lambda _root: {
            "test_database": {"status": "missing"},
            "docker": {"cli_available": True, "daemon_reachable": False, "status": "missing"},
            "ocr": {"model_manifest_exists": False, "status": "missing"},
        },
    )
    report_path = tmp_path / "acceptance.json"

    exit_code = zc07.main(["--output", str(report_path)])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["business_acceptance"] == "not_passed"
    assert report["metrics"]["status"] == "not_computable"
    assert report["dataset"]["status"] == "missing"
    assert report["e2e"]["status"] == "missing"
    assert report["exit_code"] == 2


def test_receipt_gate_rejects_a_document_missing_its_frozen_denominator():
    rows = [
        {"doc_id": "DOC-1", "golden": {"applicable_sub_checks": ["R-1:S-1"]}, "run": {"meta": {}}},
        {"doc_id": "DOC-2", "golden": {}, "run": {"meta": {}}},
    ]
    result = zc07._evaluate_receipts(rows)
    assert result["status"] == "not_computable"
    assert any("DOC-2" in item for item in result["denominator_errors"])


def test_receipt_gate_rejects_conflicting_duplicate_relation_receipts():
    rows = [{
        "doc_id": "DOC-1",
        "golden": {"applicable_sub_checks": ["R-1:S-1"]},
        "run": {"meta": {"check_receipts": [
            {"rule_id": "R-1", "sub_check_id": "S-1", "status": "pass"},
            {"rule_id": "R-1", "sub_check_id": "S-1", "status": "execution_error"},
        ]}},
    }]
    result = zc07._evaluate_receipts(rows)
    assert result["status"] == "not_computable"
    assert result["conflicting_receipts"] == ["DOC-1:R-1:S-1"]


def test_exact_location_denominator_keeps_annotations_missing_truth_regions(tmp_path):
    rows = [{
        "doc_id": "DOC-1",
        "golden": {"labels": [
            {"label": "defect", "annotation_id": "A-1", "truth_region": {"page": 1, "bbox": [1, 1, 5, 5]}},
            {"label": "defect", "annotation_id": "A-2"},
        ]},
        "report": {"matched": []},
    }]
    result = zc07._evaluate_exact_location(rows, tmp_path)
    assert result["status"] == "not_computable"
    assert result["truth_total"] == 2
    assert result["missing_truth_regions"] == ["DOC-1:A-2"]


@pytest.mark.parametrize("change", ["tolerance", "quote"])
def test_repeatability_compares_full_receipt_and_evidence_semantics(tmp_path, change):
    source_sha = "a" * 64
    basis = {
        "source_sha256": source_sha,
        "configuration_fingerprint": "config-v1",
        "version_fingerprint": "engine-v1",
    }
    first = {
        "run_id": "isolated-first", "executed_at": "2026-10-06T01:00:00+08:00",
        "repeatability_basis": basis,
        "legacy": {
            "meta": {"check_receipts": [{
                "rule_id": "R-1", "sub_check_id": "S-1", "status": "pass",
                "tolerance": {"amount_wan": "0.5"}, "operand_fact_ids": ["F-1"],
            }]},
            "issues": [{"id": "I-1", "evidence": [{"quote": "金额 100.00"}]}],
        },
    }
    repeat = json.loads(json.dumps(first))
    repeat.update(run_id="isolated-repeat", executed_at="2026-10-06T02:00:00+08:00")
    if change == "tolerance":
        repeat["legacy"]["meta"]["check_receipts"][0]["tolerance"] = {"amount_wan": "99"}
    else:
        repeat["legacy"]["issues"][0]["evidence"][0]["quote"] = "金额 101.00"
    _write_json(tmp_path / "first.json", first)
    _write_json(tmp_path / "repeat.json", repeat)
    result = zc07._evaluate_repeatability({"documents": [{
        "doc_id": "DOC-1", "golden_sha256": source_sha,
        "replay_path": "first.json", "repeat_replay_path": "repeat.json",
    }]}, tmp_path, "legacy")
    assert result["status"] == "computed"
    assert result["repeatable"] is False


def test_dataset_requires_frozen_reviewed_source_pdf_and_strata(tmp_path):
    root = tmp_path
    doc_id = "DOC-TEST-001"
    doc_dir = root / "corpus" / doc_id
    pdf_path = doc_dir / "sample.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"real-pdf-placeholder-for-validator")
    pdf_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    _write_json(
        doc_dir / "golden.json",
        {
            "doc_id": doc_id,
            "sha256": pdf_sha,
            "truth_frozen_at": "2026-10-01",
            "reviewed_by": ["reviewer-1"],
            "labels": [
                {"label": "defect", "truth_id": "D-1", "expected_severity": "high"}
            ],
        },
    )
    manifest_path = root / "dataset.json"
    _write_json(
        manifest_path,
        {
            "schema_version": "zc07.dataset.v1",
            "documents": [
                {
                    "doc_id": doc_id,
                    "split": "development",
                    "document_type": "budget",
                    "organization_type": "department",
                    "source_type": "native",
                    "mixed": False,
                }
            ],
        },
    )

    result = zc07.validate_dataset_manifest(manifest_path, root)

    assert result["status"] == "incomplete"
    assert result["sample_counts"] == {"development": 1}
    assert result["input_hashes"] == [{"doc_id": doc_id, "pdf_sha256": pdf_sha}]
    assert any("blind: 材料数 0/32" in error for error in result["errors"])
    assert any("盲测真实缺陷组 0/100" in error for error in result["errors"])
    assert not result["metrics_ready"]


def test_dataset_rejects_unfrozen_truth_and_pdf_hash_mismatch(tmp_path):
    root = tmp_path
    doc_id = "DOC-TEST-002"
    doc_dir = root / "corpus" / doc_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "sample.pdf").write_bytes(b"pdf bytes")
    _write_json(
        doc_dir / "golden.json",
        {
            "doc_id": doc_id,
            "sha256": "0" * 64,
            "reviewed_by": ["reviewer-1"],
            "labels": [],
        },
    )
    manifest_path = root / "dataset.json"
    _write_json(
        manifest_path,
        {
            "schema_version": "zc07.dataset.v1",
            "documents": [
                {
                    "doc_id": doc_id,
                    "split": "development",
                    "document_type": "final",
                    "organization_type": "unit",
                    "source_type": "scanned",
                }
            ],
        },
    )

    result = zc07.validate_dataset_manifest(manifest_path, root)

    assert any("真值未冻结" in error for error in result["errors"])
    assert any("原件 PDF 与 golden SHA-256" in error for error in result["errors"])


def test_dataset_binds_replay_to_same_document_hash_and_mode(tmp_path):
    root = tmp_path
    doc_id = "DOC-TEST-003"
    doc_dir = root / "corpus" / doc_id
    doc_dir.mkdir(parents=True)
    pdf_path = doc_dir / "source.pdf"
    pdf_path.write_bytes(b"source PDF")
    pdf_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    _write_json(
        doc_dir / "golden.json",
        {
            "doc_id": doc_id,
            "sha256": pdf_sha,
            "report_kind": "budget",
            "truth_frozen_at": "2026-10-01",
            "reviewed_by": ["reviewer-1"],
            "labels": [{"label": "defect", "annotation_id": "A-1", "truth_id": "T-1"}],
        },
    )
    replay_path = root / "replay.json"
    _write_json(replay_path, {"doc_id": "DOC-WRONG", "sha256": "0" * 64})
    manifest_path = root / "dataset.json"
    _write_json(
        manifest_path,
        {
            "schema_version": "zc07.dataset.v1",
            "documents": [
                {
                    "doc_id": doc_id,
                    "split": "development",
                    "document_type": "budget",
                    "organization_type": "department",
                    "source_type": "native",
                    "replay_path": str(replay_path),
                }
            ],
        },
    )

    result = zc07.validate_dataset_manifest(manifest_path, root)

    assert any("replay doc_id 不匹配" in error for error in result["errors"])
    assert any("replay SHA-256" in error for error in result["errors"])
    assert any("replay 缺少 legacy 生产链结果" in error for error in result["errors"])
    assert not result["metrics_ready"]


def _real_e2e_files(tmp_path, job_id: str) -> dict:
    """AC-14：E2E 证据夹具必须可核验——真实源 PDF/导出/非空 trace 及其哈希。"""
    source = tmp_path / f"source-{job_id}.pdf"
    source.write_bytes(b"%PDF-1.4 real source bytes\n")
    export = tmp_path / f"export-{job_id}.json"
    export.write_text('{"issues": []}\n', encoding="utf-8")
    trace = tmp_path / f"trace-{job_id}.json"
    trace.write_text(
        json.dumps({"job_id": job_id, "stages": ["upload", "worker", "ingest", "review"]}),
        encoding="utf-8",
    )
    return {
        "source_pdf_path": str(source),
        "source_pdf_sha256": zc07.sha256_file(source),
        "export_path": str(export),
        "export_sha256": zc07.sha256_file(export),
        "trace_path": str(trace),
    }


def test_e2e_evidence_requires_all_real_chain_flags_and_artifact_hash(tmp_path):
    artifact = tmp_path / "trace.json"
    artifact.write_text("{}\n", encoding="utf-8")
    artifact_sha = zc07.sha256_file(artifact)
    path = tmp_path / "e2e.json"
    _write_json(
        path,
        {
            "schema_version": "zc07.e2e-evidence.v1",
            "scenarios": {
                name: {
                    "run_id": f"run-{name}",
                    "doc_id": f"DOC-{name}",
                    "job_id": f"job-{name}",
                    "document_type": zc07.E2E_SCENARIO_SHAPES[name]["document_type"],
                    "source_type": zc07.E2E_SCENARIO_SHAPES[name].get("source_type", "scanned"),
                    "mixed": zc07.E2E_SCENARIO_SHAPES[name]["mixed"],
                    "api_build_revision": "test-revision",
                    "worker_build_revision": "test-revision",
                    "postgres_identity": "isolated-test-db",
                    **_real_e2e_files(tmp_path, f"job-{name}"),
                    "reviewed_by": ["reviewer-1"],
                    "status": "passed",
                    "known_issue_detected": True,
                    "known_issue_truth_id": f"TRUTH-{name}",
                    "api": True,
                    "worker_consumed": True,
                    "postgres": True,
                    "original_evidence": True,
                    "downloads_succeeded": True,
                    "ocr": True,
                    "manual_correction": True,
                    "reanalyzed_after_correction": True,
                    "artifact_path": str(artifact),
                    "artifact_sha256": artifact_sha,
                    "ocr_model_sha256": "c" * 64,
                    "correction_id": "correction-1",
                    "analysis_revision_before": "analysis-1",
                    "analysis_revision_after": "analysis-2",
                    "reanalysis_status": "completed",
                }
                for name in zc07.REQUIRED_E2E_SCENARIOS
            },
        },
    )

    result = zc07.validate_e2e_evidence(path, tmp_path)

    assert result["status"] == "ready"
    assert set(result["scenarios"]) == zc07.REQUIRED_E2E_SCENARIOS

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenarios"]["scanned_budget"]["artifact_sha256"] = "f" * 64
    _write_json(path, payload)
    result = zc07.validate_e2e_evidence(path, tmp_path)
    assert result["status"] == "incomplete"
    assert any("artifact SHA-256 mismatch" in error for error in result["errors"])


def test_e2e_rejects_correction_without_completed_reanalysis(tmp_path):
    artifact = tmp_path / "trace.json"
    artifact.write_text("{}\n", encoding="utf-8")
    artifact_sha = zc07.sha256_file(artifact)
    path = tmp_path / "e2e.json"
    scenarios = {}
    for name in zc07.REQUIRED_E2E_SCENARIOS:
        shape = zc07.E2E_SCENARIO_SHAPES[name]
        scenarios[name] = {
            **shape,
            "source_type": shape.get("source_type", "scanned"),
            "run_id": f"run-{name}",
            "doc_id": f"DOC-{name}",
            "job_id": f"job-{name}",
            "api_build_revision": "test-revision",
            "worker_build_revision": "test-revision",
            "postgres_identity": "isolated-test-db",
            **_real_e2e_files(tmp_path, f"job-{name}"),
            "reviewed_by": ["reviewer-1"],
            "status": "passed",
            "known_issue_detected": True,
            "known_issue_truth_id": f"TRUTH-{name}",
            "api": True,
            "worker_consumed": True,
            "postgres": True,
            "original_evidence": True,
            "downloads_succeeded": True,
            "ocr": True,
            "manual_correction": True,
            "reanalyzed_after_correction": True,
            "artifact_path": str(artifact),
            "artifact_sha256": artifact_sha,
            "ocr_model_sha256": "c" * 64,
            "correction_id": "correction-1",
            "analysis_revision_before": "analysis-1",
            "analysis_revision_after": "analysis-2",
            "reanalysis_status": "not_executed_fact_overlay_not_consumed_by_production_rules",
        }
    _write_json(path, {"schema_version": "zc07.e2e-evidence.v1", "scenarios": scenarios})

    result = zc07.validate_e2e_evidence(path, tmp_path)

    assert result["status"] == "incomplete"
    assert any("reanalysis_status=completed" in error for error in result["errors"])


def test_blind_metrics_are_independent_from_development_metrics(tmp_path, monkeypatch):
    import scripts.evaluate_golden_corpus as evaluator

    root = tmp_path
    documents = []
    for doc_id, split, kind, tp, fp, fn, missed in [
        ("DOC-DEV-1", "development", "budget", 0, 1, 1, "DEV-TRUTH"),
        ("DOC-BLIND-1", "blind", "final", 1, 0, 0, ""),
    ]:
        doc_dir = root / "corpus" / doc_id
        doc_dir.mkdir(parents=True)
        pdf = doc_dir / "source.pdf"
        pdf.write_bytes(doc_id.encode("utf-8"))
        pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        _write_json(
            doc_dir / "golden.json",
            {
                "doc_id": doc_id,
                "sha256": pdf_sha,
                "report_kind": kind,
                "labels": [
                    {
                        "label": "defect",
                        "annotation_id": f"A-{doc_id}",
                        "truth_id": "BLIND-TRUTH" if split == "blind" else "DEV-TRUTH",
                        "expected_severity": "high",
                    }
                ],
            },
        )
        replay = root / "replays" / f"{doc_id}.json"
        _write_json(replay, {"doc_id": doc_id, "sha256": pdf_sha})
        documents.append(
            {
                "doc_id": doc_id,
                "split": split,
                "document_type": kind,
                "organization_type": "department",
                "source_type": "native",
                "replay_path": str(replay),
            }
        )

    manifest_path = root / "dataset.json"
    _write_json(manifest_path, {"schema_version": "zc07.dataset.v1", "documents": documents})

    def fake_evaluate(doc_id, _replay_path, mode):
        assert mode == "legacy"
        item = next(row for row in documents if row["doc_id"] == doc_id)
        if item["split"] == "blind":
            return {
                "doc_id": doc_id,
                "tp": 1,
                "fp": 0,
                "fn": 0,
                "page_accuracy": 1.0,
                "locatable_evidence_rate": 1.0,
                "missed": [],
                "finding_total": 1,
            }
        return {
            "doc_id": doc_id,
            "tp": 0,
            "fp": 1,
            "fn": 1,
            "page_accuracy": None,
            "locatable_evidence_rate": 0.0,
            "missed": [{"truth_group": "DEV-TRUTH"}],
            "finding_total": 1,
        }

    monkeypatch.setattr(evaluator, "evaluate", fake_evaluate)
    dataset = {"metrics_ready": True}

    result = zc07._evaluate_replays(dataset, manifest_path, root, "legacy")

    assert result["acceptance_split"] == "blind"
    assert result["tp"] == 1 and result["fp"] == 0 and result["fn"] == 0
    assert result["precision"] == 1.0 and result["recall"] == 1.0
    assert result["split_metrics"]["development"]["precision"] == 0.0
    assert result["blind_subgroup_metrics"]["document_type"]["final"]["document_count"] == 1
    assert result["precision_wilson_95_ci"] == [0.2065, 1.0]


def test_report_writer_returns_non_overwriting_path(tmp_path):
    requested = tmp_path / "acceptance.json"
    requested.write_text("prior report\n", encoding="utf-8")

    written = zc07._write_report(requested, {"result": "new"})

    assert written != requested
    assert json.loads(written.read_text(encoding="utf-8")) == {"result": "new"}
    assert requested.read_text(encoding="utf-8") == "prior report\n"
