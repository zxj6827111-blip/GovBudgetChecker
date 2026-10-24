"""R16 release-gate counterexamples; fixtures remain synthetic engineering data."""

import copy
import json
import os

import pytest

from scripts import prepare_independent_acceptance as handoff
from scripts import run_zc07_acceptance as zc07
from tests.remediation.test_round15_independent_handoff import pack as handoff_pack


@pytest.fixture
def pack(tmp_path):
    return handoff_pack.__wrapped__(tmp_path)


@pytest.mark.parametrize("denominator", [[], [""], ["structure"], [{}], [{"rule_id": "BUD-001"}], [{"rule_id": 123, "sub_check_id": 456}], ["BUD-001::"], ["BUD-001::structure", "BUD-001::structure"]])
def test_each_document_requires_a_valid_frozen_denominator(denominator):
    rows = [
        {"doc_id": "complete", "golden": {"applicable_sub_checks": ["BUD-001::structure"]},
         "run": {"meta": {"check_receipts": [{"rule_id": "BUD-001", "sub_check_id": "structure", "status": "pass"}]}}},
        {"doc_id": "missing", "golden": {"applicable_sub_checks": denominator}, "run": {"meta": {}}},
    ]
    result = zc07._evaluate_receipts(rows)
    assert result["status"] == "not_computable"
    assert any("missing" in error for error in result["denominator_errors"])


def test_complete_frozen_relation_ids_keep_internal_colons_and_terminal_results():
    rows = [
        {"doc_id": "budget", "golden": {"applicable_sub_checks": ["BUD-001::structure:table"]},
         "run": {"meta": {"check_receipts": [{"rule_id": "BUD-001", "sub_check_id": "structure:table", "status": "pass"}]}}},
        {"doc_id": "final", "golden": {"applicable_sub_checks": [{"rule_id": "V33-001", "sub_check_id": "total:actual"}]},
         "run": {"meta": {"check_receipts": [{"rule_id": "V33-001", "sub_check_id": "total:actual", "status": "fail"}]}}},
    ]
    result = zc07._evaluate_receipts(rows)
    assert result["status"] == "computed"
    assert result["expected_total"] == 2
    assert result["completion_rate"] == result["receipt_coverage_rate"] == 1.0


def repeat_fixture(tmp_path):
    first = {
        "run_id": "actual-run-first", "executed_at": "2026-10-06T01:00:00+08:00",
        "repeatability_basis": {"source_sha256": "a" * 64, "configuration_fingerprint": "config", "version_fingerprint": "version"},
        "legacy": {"analysis_conclusion": "incomplete", "findings": [], "meta": {"check_receipts": []}},
    }
    repeat = copy.deepcopy(first)
    repeat.update(run_id="actual-run-repeat", executed_at="2026-10-06T02:00:00+08:00")
    handoff.write(tmp_path / "first.json", first)
    handoff.write(tmp_path / "repeat.json", repeat)
    manifest = {"documents": [{"doc_id": "D1", "golden_sha256": "a" * 64, "replay_path": "first.json", "repeat_replay_path": "repeat.json"}]}
    return manifest, first, repeat


@pytest.mark.parametrize("case", ["same_path", "symlink", "hardlink", "same_run", "missing_run", "same_timestamp", "missing_timezone"])
def test_one_execution_cannot_be_claimed_as_two_independent_runs(tmp_path, case):
    manifest, first, repeat = repeat_fixture(tmp_path)
    if case == "same_path":
        manifest["documents"][0]["repeat_replay_path"] = "first.json"
    elif case in {"symlink", "hardlink"}:
        (tmp_path / "repeat.json").unlink()
        if case == "symlink":
            (tmp_path / "repeat.json").symlink_to(tmp_path / "first.json")
        else:
            os.link(tmp_path / "first.json", tmp_path / "repeat.json")
    else:
        if case == "same_run":
            repeat["run_id"] = first["run_id"]
        elif case == "missing_run":
            repeat.pop("run_id")
        elif case == "same_timestamp":
            repeat["executed_at"] = first["executed_at"]
        else:
            repeat["executed_at"] = "2026-10-06T02:00:00"
        handoff.write(tmp_path / "repeat.json", repeat)
    result = zc07._evaluate_repeatability(manifest, tmp_path, "legacy")
    assert result["status"] == "not_computable"


def test_independent_runs_with_equal_business_content_are_comparable(tmp_path):
    manifest, _, _ = repeat_fixture(tmp_path)
    result = zc07._evaluate_repeatability(manifest, tmp_path, "legacy")
    assert result["status"] == "computed"
    assert result["repeatable"] is True


@pytest.mark.parametrize("change", ["value_token", "overlay_count", "recognition", "applied_count", "random_parse_uid"])
def test_repeatability_compares_stable_fact_and_overlay_semantics(tmp_path, change):
    manifest, first, repeat = repeat_fixture(tmp_path)
    summary = {"context_version": "fact-context-v1", "file_hash": "a" * 64, "version_token": "stable-fact-values", "fact_count": 2,
               "recognition_statuses": {"unverified": 2}, "operand_count": 2, "non_operand_count": 0, "overlay_count": 0, "parse_run_uid": "runtime-first"}
    first["legacy"]["meta"]["fact_context"] = summary
    repeat["legacy"]["meta"]["fact_context"] = copy.deepcopy(summary)
    if change == "value_token":
        repeat["legacy"]["meta"]["fact_context"]["version_token"] = "different-fact-values"
    elif change == "overlay_count":
        repeat["legacy"]["meta"]["fact_context"]["overlay_count"] = 1
    elif change == "recognition":
        repeat["legacy"]["meta"]["fact_context"]["recognition_statuses"] = {"verified": 2}
    elif change == "applied_count":
        first["legacy"]["meta"]["fact_overlay_applied"] = 0
        repeat["legacy"]["meta"]["fact_overlay_applied"] = 1
    else:
        repeat["legacy"]["meta"]["fact_context"]["parse_run_uid"] = "runtime-second"
    handoff.write(tmp_path / "first.json", first)
    handoff.write(tmp_path / "repeat.json", repeat)
    result = zc07._evaluate_repeatability(manifest, tmp_path, "legacy")
    assert result["status"] == "computed"
    assert result["repeatable"] is (change == "random_parse_uid")


def mutate_projection(pack, field, transform):
    path, payload, ref = pack
    technical = json.loads((path.parent / payload["technical_preflight"]["path"]).read_text())
    cache = technical["mixed_final_cache"]
    db = json.loads((path.parent / cache["corrected_database"]["path"]).read_text())
    if field == "query":
        transform(db)
    else:
        attachment = db[field]
        data = (path.parent / attachment["path"]).read_bytes() if field == "correction_ledger" else json.loads((path.parent / attachment["path"]).read_text())
        updated = transform(data)
        db[field] = ref(attachment["path"], data if updated is None else updated)
    cache["corrected_database"] = ref(cache["corrected_database"]["path"], db)
    payload["technical_preflight"] = ref(payload["technical_preflight"]["path"], technical)
    handoff.write(path, payload)


@pytest.mark.parametrize("field", ["original_database_rows", "correction_ledger", "fact_context"])
def test_hand_filled_projection_without_its_source_artifacts_is_rejected(pack, field):
    mutate_projection(pack, "query", lambda db: db.pop(field))
    result = handoff.validate_handoff(pack[0])
    assert not result["passed"]
    assert result["sealed_truth_read"] is False


@pytest.mark.parametrize("case", ["empty_ledger", "wrong_basis", "wrong_job", "wrong_value", "wrong_evidence", "stale_context", "context_changed_without_token", "old_database_revision", "wrong_database_source", "overwritten_original", "projection_evidence"])
def test_projection_cannot_hide_stale_or_missing_persisted_correction(pack, case):
    if case == "empty_ledger":
        mutate_projection(pack, "correction_ledger", lambda _: b"")
    elif case in {"wrong_basis", "wrong_job", "wrong_value", "wrong_evidence"}:
        def mutate_ledger(data):
            record = json.loads(data)
            if case == "wrong_basis":
                record["analysis_basis_token"] = "cache-contract:unrelated-revision"
            elif case == "wrong_job":
                record["job_id"] = "another-job"
            elif case == "wrong_value":
                record["changes"][0]["value"] = "746.62"
            else:
                record["changes"][0]["evidence_ids"] = ["borrowed-evidence"]
            return (json.dumps(record) + "\n").encode()
        mutate_projection(pack, "correction_ledger", mutate_ledger)
    elif case in {"stale_context", "context_changed_without_token"}:
        def mutate_context(context):
            if case == "stale_context":
                context["version_token"] = "stale-version"
            else:
                context["facts"][0]["raw_value"] = "999.00"
                context["facts"][0]["normalized_value"] = "999.00"
        mutate_projection(pack, "fact_context", mutate_context)
    elif case == "projection_evidence":
        mutate_projection(pack, "query", lambda db: db["corrected_fact_rows"][0].update(evidence_ids=["borrowed-evidence"]))
    else:
        def mutate_db(raw):
            if case == "old_database_revision":
                raw["result_revision"] = "r1"
            elif case == "wrong_database_source":
                raw["parse_run"]["source_file_hash"] = "b" * 64
            else:
                raw["target_original_facts"][0]["raw_value"] = "756.62"
        mutate_projection(pack, "original_database_rows", mutate_db)
    assert not handoff.validate_handoff(pack[0])["passed"]


def test_projection_preserves_original_database_and_binds_current_context(pack):
    path, payload, _ = pack
    result = handoff.validate_handoff(path)
    assert result["passed"], result["errors"]
    technical = json.loads((path.parent / payload["technical_preflight"]["path"]).read_text())
    db_ref = technical["mixed_final_cache"]["corrected_database"]
    db = json.loads((path.parent / db_ref["path"]).read_text())
    raw = json.loads((path.parent / db["original_database_rows"]["path"]).read_text())
    assert raw["target_original_facts"][0]["raw_value"] == "746.62"
    assert db["corrected_fact_rows"][0]["raw_value"] == "756.62"
    assert raw["job"]["analysis_revision"] == 2
    assert db["analysis_revision"] == raw["result_revision"] == "r2"
