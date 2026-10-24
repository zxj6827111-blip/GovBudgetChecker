"""Synthetic signed-record contracts; these tests never supply human truth."""

import itertools
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import fitz
import pytest

import scripts.prepare_independent_acceptance as handoff
from src.engine.fact_context import FactContext, FactOverlay, apply_fact_overlay
from src.schemas.parse_artifact import FiscalFact


@pytest.fixture
def pack(tmp_path):
    def ref(name, value):
        path = tmp_path / name
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            handoff.write(path, value)
        return {"path": name, "sha256": handoff.sha(path)}

    def pdf(name, page_count=1):
        path = tmp_path / name
        with fitz.open() as document:
            for number in range(page_count):
                document.new_page().insert_text((50, 50), f"SYNTHETIC CONTRACT {name} {number}")
            document.save(path)
        return {"path": name, "sha256": handoff.sha(path)}

    roles = {name: [f"fixture-{name}"] for name in ("developers", "custodians", "annotators", "independent_reviewers", "executors")}
    reviewer = roles["independent_reviewers"][0]
    custodian = roles["custodians"][0]
    documents = []
    for i, stratum in enumerate(itertools.product(("development", "blind"), ("budget", "final"), ("department", "unit"), ("native", "scanned"))):
        for j in range(4):
            doc_id = f"contract-{i}-{j}"
            documents.append(dict(zip(("split", "document_type", "organization_type", "source_type"), stratum, strict=True)) | {
                "doc_id": doc_id,
                "source_pdf": pdf(f"{doc_id}.pdf"),
                "official_original": True,
                "complete_original": True,
                "source_url": "https://example.invalid/synthetic-contract",
                "downloaded_at": "2026-10-01T01:00:00+08:00",
                "near_duplicate_group": doc_id,
            })
    intake_ref = ref("intake.json", {"documents": documents, "blind_defect_groups_attested": 100})
    source = {"src/contract.py": "c" * 64}
    identity = {
        "source_snapshot": ref("source_snapshot.json", source),
        "model_manifest": ref("model_manifest.txt", b"synthetic model\n"),
        "configuration": ref("configuration.json", {"contract": True}),
        "evaluator": ref("evaluator.py", b"# synthetic evaluator\n"),
        "dataset_manifest": ref("dataset.json", {"documents": documents}),
        "overlay_manifest": ref("overlay.json", {"overlays": []}),
    }
    freeze = {
        "status": "frozen_signed",
        "blind_truth_access_before_code_freeze": False,
        "truth_sealed_at": "2026-10-02T01:00:00+08:00",
        "code_frozen_at": "2026-10-03T01:00:00+08:00",
    }
    for name, field in (
        ("source_snapshot", "source_snapshot_sha256"),
        ("model_manifest", "model_manifest_sha256"),
        ("configuration", "configuration_fingerprint"),
        ("evaluator", "evaluator_sha256"),
        ("dataset_manifest", "dataset_manifest_sha256"),
        ("overlay_manifest", "overlay_manifest_sha256"),
    ):
        freeze[field] = identity[name]["sha256"]
    scan_pdf = pdf("scan_contract.pdf", 2)
    scan_result = {
        "job_id": "scan-contract",
        "checksum": scan_pdf["sha256"],
        "status": "review_required",
        "analysis_revision": "scan-r1",
        "result": {"meta": {"ocr": {"pages": [{"page": 1, "ok": True}, {"page": 2, "ok": True}], "failed_pages": []}}},
    }
    cache_pdf = pdf("cache_contract.pdf")
    cache = {"source_pdf": cache_pdf, "target_fact_uid": "real-amount-contract", "expected_corrected_raw_value": "756.62"}
    original_context = FactContext(
        facts=[FiscalFact(fact_uid="real-amount-contract", indicator="total_actual", raw_value="746.62", unit="万元", normalized_value=Decimal("746.62"), evidence_ids=["contract-e1"])],
        file_hash=cache_pdf["sha256"], parse_run_uid="contract-parse-run",
    )
    corrected_context = apply_fact_overlay(original_context, [FactOverlay(
        fact_uid="real-amount-contract", raw_value="756.62", corrected_by="fixture-actor", basis_token="cache-contract:r1",
    )])
    correction_record = {
        "record_id": "cor-fixture", "job_id": "cache-contract", "analysis_basis_token": "cache-contract:r1",
        "record_status": "append_only_overlay", "actor": "fixture-actor", "created_at": "2026-10-04T00:00:00+08:00",
        "changes": [{"target_type": "fact", "target_id": "real-amount-contract", "field": "raw_value", "value": "756.62", "evidence_ids": ["contract-e1"]}],
    }
    ledger_ref = ref("corrections.jsonl", (json.dumps(correction_record) + "\n").encode())
    for stage, revision in (("initial", "r1"), ("corrected", "r2")):
        result = {
            "job_id": "cache-contract",
            "checksum": cache_pdf["sha256"],
            "status": "review_required",
            "analysis_revision": revision,
            "result": {"meta": {"structured_ingest": {"status": "done", "parse_run_id": 77, "document_version_id": 51, "parse_cache_hit": stage == "corrected"}, "fact_context": (corrected_context if stage == "corrected" else original_context).summary()}},
        }
        cache[f"{stage}_result"] = ref(f"{stage}_result.json", result)
        db = {
            "schema_version": "gbc.current-ingest-query.v1",
            "job_id": "cache-contract",
            "analysis_revision": revision,
            "parse_run_id": 77,
            "document_version_id": 51,
            "source_sha256": cache_pdf["sha256"],
            "query_timestamp": "2026-10-04T01:00:00+08:00",
            "facts_count": 1,
            "evidence_count": 1,
            "corrected_fact_rows": [{"fact_uid": "real-amount-contract", "raw_value": "756.62", "evidence_ids": ["contract-e1"]}] if stage == "corrected" else [],
        }
        if stage == "corrected":
            raw_query = {
                "job": {"job_uuid": "cache-contract", "file_hash": cache_pdf["sha256"], "analysis_revision": 2},
                "result_revision": revision, "result_ingest_status": "done",
                "parse_run": {"id": 77, "document_version_id": 51, "status": "completed", "source_file_hash": cache_pdf["sha256"], "version_file_hash": cache_pdf["sha256"]},
                "run_facts_count": 1, "run_evidence_count": 1,
                "target_original_facts": [{"fact_uid": "real-amount-contract", "raw_value": "746.62", "evidence_ids": '["contract-e1"]'}],
                "target_original_evidence": [{"evidence_id": "contract-e1", "file_hash": cache_pdf["sha256"], "physical_page": 1, "quote": "746.62"}],
            }
            db.update(
                original_database_rows=ref("original_database_rows.json", raw_query),
                correction_ledger=ledger_ref,
                fact_context=ref("corrected_context.json", corrected_context.to_dict()),
            )
        cache[f"{stage}_database"] = ref(f"{stage}_database.json", db)
    cache["cache_readback"] = ref("cache_readback.json", {"cache_hit": True, "cell_provenance_decoded": True, "parse_run_id": 77})
    technical = {
        "schema_version": "gbc.independent-technical-preflight.v1",
        "service_receipt": ref("service_receipt.json", {"status": "passed", "cleanup": "complete", "source_unchanged_during_run": True, "code_fingerprints": source}),
        "original_scanned_budget": {
            "official_original": True,
            "synthetic": False,
            "derived": False,
            "source_pdf": scan_pdf,
            "page_count": 2,
            "processed_physical_pages": [1, 2],
            "worker_budget_seconds": 120,
            "elapsed_seconds": 80,
            "result": ref("scan_result.json", scan_result),
            "worker_trace": ref("worker.log", b"synthetic completed trace\n"),
        },
        "mixed_final_cache": cache,
    }
    doc_ids = sorted(row["doc_id"] for row in documents)
    review = {"status": "signed", "resolved": True, "reviewed_by": reviewer, "doc_ids": doc_ids}
    payload = {
        "schema_version": "gbc.independent-handoff.v1",
        "roles": roles,
        "role_signoff": ref("roles.json", {"status": "signed", "roles": roles}),
        "access_review": ref("access.json", {"status": "signed", "reviewed_by": reviewer, "blind_truth_access_before_code_freeze": False}),
        "development_exclusions": ref("exclusions.json", {"sha256_groups": {}}),
        "corpus_intake": intake_ref,
        "near_duplicate_review": ref("near.json", review),
        "disagreement_review": ref("disagreement.json", review),
        "freeze_record": ref("freeze.json", freeze),
        "freeze_signoff": ref("freeze_signoff.json", {"status": "signed", "custodian": custodian, "reviewed_by": reviewer}),
        "truth_seal": ref("sealed_truth.bin", b"MUST NOT BE PARSED AS JSON"),
        "frozen_identities": identity,
        "technical_preflight": ref("technical.json", technical),
    }
    blind_ids = sorted(row["doc_id"] for row in documents if row["split"] == "blind")
    runs = []
    for i in range(2):
        outputs = []
        for doc_id in blind_ids:
            output_ref = ref(f"run{i}_{doc_id}.bin", f"synthetic output {i} {doc_id}".encode())
            outputs.append({"doc_id": doc_id, "analysis_revision": f"run{i}-revision", "replay": output_ref, "exports": dict.fromkeys(("json", "csv", "docx", "pdf"), output_ref)})
        run = {key: freeze[key] for key in freeze if key.endswith("sha256") or key == "configuration_fingerprint"} | {
            "run_id": f"fixture-run-{i}",
            "frozen_at": f"2026-10-0{4+i}T01:00:00+08:00",
            "blind_doc_ids": blind_ids,
            "output_manifest": ref(f"outputs{i}.json", {"documents": outputs}),
        }
        runs.append(ref(f"run{i}.json", run))
    payload["output_freezes"] = runs
    payload["truth_release"] = ref("release.json", {"status": "signed", "custodian": custodian, "released_at": "2026-10-06T01:00:00+08:00"})
    path = tmp_path / "handoff.json"
    handoff.write(path, payload)
    return path, payload, ref


def update_attachment(pack, field, transform):
    path, payload, ref = pack
    value = json.loads((path.parent / payload[field]["path"]).read_text())
    transform(value)
    payload[field] = ref(payload[field]["path"], value)
    handoff.write(path, payload)


def test_synthetic_contract_ready_never_claims_business_acceptance_or_reads_labels(pack):
    path, _, _ = pack
    for stage in ("intake", "execution"):
        result = handoff.validate_handoff(path, stage=stage)
        assert result["passed"], result["errors"]
        assert result["sealed_truth_read"] is False
        assert result["business_acceptance"] == "not_evaluated"


@pytest.mark.parametrize("case", ["ingest_error", "stale_revision", "stale_parse_run", "wrong_job", "old_fact_value", "missing_evidence", "cache_not_read", "cache_hit_only_declared"])
def test_cached_old_database_rows_cannot_mask_current_failure(pack, case):
    path, _, ref = pack

    def mutate(technical):
        cache = technical["mixed_final_cache"]
        field = "corrected_result" if case in {"ingest_error", "wrong_job", "cache_hit_only_declared"} else "cache_readback" if case == "cache_not_read" else "corrected_database"
        value = json.loads((path.parent / cache[field]["path"]).read_text())
        if case == "ingest_error":
            value["result"]["meta"]["structured_ingest"]["status"] = "error"
        elif case == "wrong_job":
            value["job_id"] = "different-job"
        elif case == "cache_hit_only_declared":
            value["result"]["meta"]["structured_ingest"]["parse_cache_hit"] = False
        elif case == "stale_revision":
            value["analysis_revision"] = "r1"
        elif case == "stale_parse_run":
            value["parse_run_id"] = 76
        elif case == "old_fact_value":
            value["corrected_fact_rows"][0]["raw_value"] = "746.62"
        elif case == "missing_evidence":
            value["corrected_fact_rows"][0]["evidence_ids"] = []
        else:
            value["cache_hit"] = False
        cache[field] = ref(cache[field]["path"], value)

    update_attachment(pack, "technical_preflight", mutate)
    assert handoff.validate_handoff(path)["passed"] is False


@pytest.mark.parametrize("case", ["timeout", "client_wait_only", "truncated", "skip_ocr", "synthetic", "wrong_source"])
def test_complete_original_scan_is_a_required_prerequisite(pack, case):
    path, _, ref = pack

    def mutate(technical):
        scan = technical["original_scanned_budget"]
        if case == "client_wait_only":
            scan["elapsed_seconds"] = scan["worker_budget_seconds"] + 1
        elif case == "truncated":
            scan["page_count"] = 1
            scan["processed_physical_pages"] = [1]
        elif case == "synthetic":
            scan["synthetic"] = True
        else:
            value = json.loads((path.parent / scan["result"]["path"]).read_text())
            if case == "timeout":
                value["status"] = "error"
                value["error"] = "pipeline_timeout_600s"
            elif case == "wrong_source":
                value["checksum"] = "f" * 64
            else:
                value["result"]["meta"]["ocr"]["pages"] = [{"page": 1, "ok": True}]
            scan["result"] = ref(scan["result"]["path"], value)

    update_attachment(pack, "technical_preflight", mutate)
    assert handoff.validate_handoff(path)["passed"] is False


def test_known_development_pdf_and_cross_split_near_duplicates_are_rejected(pack):
    path, _, _ = pack
    update_attachment(pack, "corpus_intake", lambda intake: intake["documents"][-1].update(near_duplicate_group=intake["documents"][0]["near_duplicate_group"]))
    assert any("crosses" in error for error in handoff.validate_handoff(path)["errors"])
    intake = json.loads((path.parent / "intake.json").read_text())
    digest = intake["documents"][-1]["source_pdf"]["sha256"]
    update_attachment(pack, "development_exclusions", lambda exclusions: exclusions["sha256_groups"].update({digest: ["already_seen.pdf"]}))
    assert any("known development" in error for error in handoff.validate_handoff(path)["errors"])


def test_self_review_and_pre_freeze_label_access_are_rejected(pack):
    path, payload, ref = pack
    payload["roles"]["independent_reviewers"] = payload["roles"]["annotators"]
    payload["role_signoff"] = ref("roles.json", {"status": "signed", "roles": payload["roles"]})
    handoff.write(path, payload)
    assert any("own truth" in error for error in handoff.validate_handoff(path)["errors"])
    update_attachment(pack, "access_review", lambda access: access.update(blind_truth_access_before_code_freeze=True))
    assert any("pre-freeze" in error for error in handoff.validate_handoff(path)["errors"])


def test_truth_cannot_be_released_before_second_output_freeze(pack):
    path, _, _ = pack
    update_attachment(pack, "truth_release", lambda release: release.update(released_at="2026-10-04T12:00:00+08:00"))
    result = handoff.validate_handoff(path, stage="execution")
    assert not result["passed"]
    assert any("both frozen outputs" in error for error in result["errors"])


def test_frozen_output_attachments_cannot_be_changed(pack):
    path, _, _ = pack
    output = next(path.parent.glob("run0_contract*.bin"))
    output.write_text("modified after freeze")
    result = handoff.validate_handoff(path, stage="execution")
    assert not result["passed"]
    assert any("SHA-256 mismatch" in error for error in result["errors"])


def test_malformed_inputs_fail_closed_without_crashing(pack):
    path, _, _ = pack
    update_attachment(pack, "technical_preflight", lambda technical: technical.update(original_scanned_budget={"result": []}))
    assert not handoff.validate_handoff(path)["passed"]
    path.write_text("[]")
    assert not handoff.validate_handoff(path)["passed"]


def test_preparation_keeps_default_exclusions_and_pending_human_fields(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for name in ("samples", "corpus", "outputs", "external"):
        (root / name).mkdir(parents=True)
        (root / name / "source.PDF").write_bytes(name.encode())
    monkeypatch.setattr(handoff, "ROOT", root)
    monkeypatch.setattr(handoff.subprocess, "check_output", lambda command, **kwargs: "synthetic-head\n" if kwargs.get("text") else b"")
    (root / "scripts").mkdir()
    (root / "scripts" / "run_zc07_acceptance.py").write_text("# synthetic evaluator")
    destination = tmp_path / "prepared"
    receipt = handoff.prepare(destination, [root / "external"])
    exclusion = json.loads((destination / "development_exclusions.json").read_text())
    assert len(exclusion["sha256_groups"]) == 4
    assert receipt["actual_frozen_documents"] == receipt["actual_human_defect_groups"] == 0
    assert receipt["business_metrics"] is None
    assert len(json.loads((destination / "slots.pending.json").read_text())["slots"]) == 64
    assert not handoff.validate_handoff(destination / "handoff.pending.json")["passed"]
    with pytest.raises(ValueError, match="empty"):
        handoff.prepare(destination, [])


def test_pending_cli_uses_nonzero_exit_and_preserves_prior_receipt(tmp_path):
    path = tmp_path / "handoff.json"
    handoff.write(path, {})
    report = tmp_path / "gate.json"
    command = [sys.executable, str(Path(handoff.__file__)), "--check-handoff", str(path), "--check-report", str(report)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 2
    assert json.loads(report.read_text())["business_acceptance"] == "not_evaluated"
    before = report.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode != 0
    assert report.read_bytes() == before
