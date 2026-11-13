"""Bounded review probes. No Docker, API, Worker, database or model runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent

from scripts import prepare_independent_acceptance as intake
from scripts import run_isolated_service_validation as service
from scripts.run_zc07_acceptance import (
    _frozen_scan_manifest,
    validate_dataset_manifest,
    validate_e2e_evidence,
)


def main():
    observed = {}
    for body_error in (False, True):
        events = []

        class Process:
            def poll(self):
                return None

            def terminate(self):
                events.append("process_terminate")

            def wait(self, timeout):
                events.append("process_wait")

        class Handle:
            def close(self):
                events.append("log_close")

        class Directory:
            def __enter__(self):
                events.append("temp_enter")
                return self

            def __exit__(self, *args):
                events.append("temp_remove")

        try:
            with Directory(), service.service_lifetime([Process()], [Handle()]):
                events.append("body")
                if body_error:
                    raise RuntimeError("probe_body_failure")
        except RuntimeError:
            events.append("body_error_propagated")
        assert events.index("process_wait") < events.index("log_close") < events.index("temp_remove")
        observed[f"lifetime_body_error_{body_error}"] = events

    # Both subprocess boundaries are replaced. Temporary generated credentials
    # never leave the temporary directory and are never printed or persisted.
    for cleanup_raises in (False, True):
        destination = OUT / ("cleanup_raises" if cleanup_raises else "cleanup_return_nonzero")
        cleanup = (
            OSError("probe cleanup command execution failure")
            if cleanup_raises
            else subprocess.CompletedProcess(["docker", "rm"], 1)
        )
        try:
            with (
                patch.object(sys, "argv", ["probe", "--output", str(destination)]),
                patch.object(service, "docker", side_effect=RuntimeError("probe startup failure")),
                patch.object(
                    service.subprocess,
                    "run",
                    side_effect=cleanup if cleanup_raises else None,
                    return_value=cleanup,
                ),
            ):
                exit_code = service.main()
            result = {"return_code": exit_code, "propagated_exception": None}
        except Exception as exc:
            result = {"return_code": None, "propagated_exception": f"{type(exc).__name__}: {exc}"}
        result["receipt_exists"] = (destination / "service_receipt.json").is_file()
        observed[f"main_cleanup_{cleanup_raises}"] = result

    # Transport contract only. This is a synthetic registry with no regions;
    # it is never put into corpus, a deployment registry, or business metrics.
    with tempfile.TemporaryDirectory(prefix="scan-contract-", dir=OUT) as temporary:
        root = Path(temporary)
        directory = root / "corpus" / "DOC-SYNTHETIC-CONTRACT"
        directory.mkdir(parents=True)
        path = directory / "attestations.json"
        path.write_text(json.dumps({
            "schema_version": "gbc.scan.attestations.v1",
            "frozen_at": "synthetic-contract-only",
            "reviewed_by": "simulated-review-contract",
            "generated_by": "independent_human_review",
            "regions": [],
        }))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        cases = {
            "complete": {"scan_attestations_file": path.name, "scan_attestations_sha256": digest},
            "missing_sha": {"scan_attestations_file": path.name},
            "wrong_sha": {"scan_attestations_file": path.name, "scan_attestations_sha256": "f" * 64},
            "absolute": {"scan_attestations_file": str(path), "scan_attestations_sha256": digest},
            "escape": {"scan_attestations_file": "../attestations.json", "scan_attestations_sha256": digest},
            "missing_file": {"scan_attestations_file": "absent.json", "scan_attestations_sha256": digest},
        }
        observed["scan_loader_contract"] = {
            name: _frozen_scan_manifest(golden, root, "DOC-SYNTHETIC-CONTRACT") is not None
            for name, golden in cases.items()
        }
        with patch.dict("os.environ", {
            "OCR_EVIDENCE_ATTESTATIONS_PATH": str(path),
            "OCR_EVIDENCE_ATTESTATIONS_SHA256": digest,
        }):
            observed["scan_loader_missing_fields_with_environment_pins"] = (
                _frozen_scan_manifest({}, root, "DOC-SYNTHETIC-CONTRACT") is not None
            )
    assert observed["scan_loader_contract"] == {
        "complete": True, "missing_sha": False, "wrong_sha": False,
        "absolute": False, "escape": False, "missing_file": False,
    }
    assert observed["scan_loader_missing_fields_with_environment_pins"] is False

    # The preparation tool sees only a synthetic temporary root and a byte
    # marker called .pdf. It never reads the actual corpus or a private blind set.
    with tempfile.TemporaryDirectory(prefix="intake-contract-", dir=OUT) as temporary:
        root = Path(temporary)
        known = root / "known_development"
        known.mkdir()
        (known / "marker.pdf").write_bytes(b"synthetic exclusion byte marker, not a document")
        (root / "code.py").write_text("# synthetic identity fixture\n")
        (root / "scripts").mkdir()
        (root / "scripts/run_zc07_acceptance.py").write_text("# synthetic evaluator identity\n")
        destination = root / "intake_package"

        def fake_git(args, **kwargs):
            return "synthetic-head" if "rev-parse" in args else b"code.py\0scripts/run_zc07_acceptance.py\0"

        with patch.object(intake, "ROOT", root), patch.object(intake.subprocess, "check_output", side_effect=fake_git):
            receipt = intake.prepare(destination, [known])
        dataset = json.loads((destination / "dataset.pending.json").read_text())
        slots = json.loads((destination / "slots.pending.json").read_text())["slots"]
        freeze = json.loads((destination / "freeze_record.pending.json").read_text())
        nonempty_labels = sum(
            bool(json.loads(path.read_text()).get("labels"))
            for path in destination.rglob("golden.pending.json")
        )
        try:
            intake.prepare(destination, [])
            refuses_existing = False
        except ValueError:
            refuses_existing = True
        observed["intake_preparation_contract"] = {
            "slot_count": len(slots),
            "documents": dataset["documents"],
            "slots_with_source_or_reviewer": sum(bool(slot["source_sha256"] or slot["reviewer"]) for slot in slots),
            "nonempty_golden_labels": nonempty_labels,
            "copied_pdfs": len(list(destination.rglob("*.pdf"))),
            "frozen_at": freeze["frozen_at"],
            "independent_reviewers": freeze["independent_reviewers"],
            "actual_frozen_documents": receipt["actual_frozen_documents"],
            "actual_human_defect_groups": receipt["actual_human_defect_groups"],
            "business_metrics": receipt["business_metrics"],
            "refuses_nonempty_output": refuses_existing,
        }

    pending = ROOT / "outputs/remediation_20261006_round13/independent_intake"
    dataset = validate_dataset_manifest(pending / "dataset.pending.json", ROOT)
    e2e = validate_e2e_evidence(pending / "e2e.pending.json", ROOT)
    observed["pending_gate"] = {
        "dataset_status": dataset["status"], "metrics_ready": dataset["metrics_ready"],
        "dataset_errors": dataset["errors"], "e2e_status": e2e["status"], "e2e_errors": e2e["errors"],
    }
    (OUT / "contract_probe_results.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(observed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
