"""Read-only checks of final evidence; writes only this round's audit outputs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess

from scripts.run_zc07_acceptance import _evaluate_export_agreement

ROOT = Path.cwd()
OUT = ROOT / "outputs/remediation_20261005_round9"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


rows = json.loads((OUT / "root_business_neighbors_stdout.log").read_text())
save("root_business_neighbors.json", rows)
summary = []
for row in rows:
    receipts = row["payload"].get("fact_receipts", [])
    summary.append({
        "case_id": row["case_id"], "synthetic": True,
        "claim": row.get("claim"),
        "ratio_receipts": [r for r in receipts if "share_of" in r["sub_check_id"]],
        "ratio_issues": [i for i in row["payload"]["issues"]["all"] if "占比" in i["message"]],
    })
save("business_neighbors_summary.json", summary)

folder = OUT / "live_services_final"
receipt = json.loads((folder / "service_receipt.json").read_text())
checks = {
    "receipt_status": receipt["status"],
    "current_code_matches": {p: sha(ROOT / p) == h for p, h in receipt["code_fingerprints"].items()},
    "log_sha_matches": {p: sha(folder / p) == h for p, h in receipt["artifact_sha256"].items()},
    "scenarios": [],
}
for item in receipt["scenarios"]:
    scenario = folder / item["scenario"]
    status = json.loads((scenario / "status.json").read_text())
    report = json.loads((scenario / "report.json").read_text())
    job = item["database"]["job"]
    gate = _evaluate_export_agreement({"documents": [{
        "doc_id": item["job_id"], "split": "blind",
        "export_paths": [str(scenario / p) for p in item["exports"]],
    }]}, ROOT)
    check = {
        "scenario": item["scenario"], "synthetic": item["synthetic"],
        "job_id_consistent": status["job_id"] == report["job_id"] == job["job_uuid"] == item["job_id"],
        "terminal_database_matches": status["status"] == job["status"] == item["terminal_status"],
        "source_database_matches": sha(scenario / "source.pdf") == job["file_hash"] == item["source_sha256"],
        "revision_status_report_matches": report["analysis_revision"] == status["analysis_revision"] == item["analysis_revision"] == report["review_report"]["analysis_revision"],
        "download_sha_matches": {p: sha(scenario / p) == h for p, h in item["exports"].items()},
        "analysis_results_for_this_job": item["database"]["analysis_results"],
        "worker_completion_in_saved_log": item["job_id"] in (folder / "worker.log").read_text() and bool(item["worker_completion_events"]),
        "export_gate_recomputed": gate,
        "repeat_bytes_reported": item["repeated_downloads_identical"],
        "business_accuracy": None,
    }
    checks["scenarios"].append(check)
running = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
checks["cleanup_verified_container_absent"] = receipt["postgres"]["isolation"] not in running
checks["cleanup_verified_processes_exited"] = {}
for key in ("api_pid", "worker_pid"):
    try:
        os.kill(receipt["processes"][key], 0)
        exited = False
    except ProcessLookupError:
        exited = True
    checks["cleanup_verified_processes_exited"][key] = exited
assert receipt["status"] == "passed"
assert all(checks["current_code_matches"].values()) and all(checks["log_sha_matches"].values())
assert checks["cleanup_verified_container_absent"] and all(checks["cleanup_verified_processes_exited"].values())
for check in checks["scenarios"]:
    assert all(check[k] for k in ("job_id_consistent", "terminal_database_matches", "source_database_matches", "revision_status_report_matches", "worker_completion_in_saved_log"))
    assert all(check["download_sha_matches"].values()) and check["analysis_results_for_this_job"] == 1
    assert check["export_gate_recomputed"]["status"] == "computed" and check["export_gate_recomputed"]["export_agreement_rate"] == 1
save("live_service_evidence_integrity.json", checks)

templates = {p.name: json.loads(p.read_text()) for p in (ROOT / "docs/acceptance_templates").glob("*.json")}
rejection = json.loads((OUT / "template_rejection.json").read_text())
assert rejection["exit_code"] == 2 and rejection["business_acceptance"] == "not_passed"
assert not rejection["dataset"]["metrics_ready"]
save("template_checks.json", {
    "valid_json_files": sorted(templates),
    "empty_dataset_documents": templates["dataset.template.json"]["documents"] == [],
    "frozen_samples_created": 0,
    "gate_exit_code": rejection["exit_code"],
    "business_acceptance": rejection["business_acceptance"],
    "dataset_status": rejection["dataset"]["status"],
    "e2e_status": rejection["e2e"]["status"],
})
print("delivery integrity checks passed; no frozen business accuracy claimed")
