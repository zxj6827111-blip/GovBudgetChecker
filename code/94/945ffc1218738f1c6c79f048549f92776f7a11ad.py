"""只读源码的 R16 工程反例，所有合成人员/材料均非独立真值。"""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts import prepare_independent_acceptance as handoff
from scripts import run_zc07_acceptance as zc07
from tests.remediation.test_round15_independent_handoff import pack as fixture_pack

OUT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


protected = [
    "scripts/prepare_independent_acceptance.py",
    "scripts/run_zc07_acceptance.py",
    "scripts/evaluate_golden_corpus.py",
    "scripts/run_isolated_service_validation.py",
    "src/services/structured_ingest_runner.py",
    "src/services/correction_overlay_store.py",
    "docs/独立盲测交接操作_20261006.md",
    "docs/acceptance_templates/current_ingest_query.template.json",
    "docs/GovBudgetChecker_系统整改PLAN_V2.0_20260930.md",
    "docs/GovBudgetChecker 完整整改计划.md",
]
before = {name: digest(ROOT / name) for name in protected}

# A: 当前版本校正投影只需自报值，故意提供空台账及旧版本 FactContext。
synthetic = OUT / "synthetic_handoff_contract"
synthetic.mkdir(exist_ok=True)
path, payload, ref = fixture_pack.__wrapped__(synthetic)
baseline = handoff.validate_handoff(path)
assert baseline["passed"]
technical = json.loads((synthetic / payload["technical_preflight"]["path"]).read_text())
cache = technical["mixed_final_cache"]
database = json.loads((synthetic / cache["corrected_database"]["path"]).read_text())
database["original_database_rows"] = ref("original_rows.json", {
    "parse_run_id": 77,
    "facts": [{"fact_uid": "real-amount-contract", "raw_value": "746.62", "evidence_ids": ["contract-e1"]}],
})
database["correction_ledger"] = ref("empty_corrections.jsonl", b"")
database["fact_context"] = ref("stale_fact_context.json", {
    "analysis_revision": "r1",
    "facts": [{"fact_uid": "real-amount-contract", "raw_value": "746.62", "evidence_ids": ["contract-e1"]}],
})
cache["corrected_database"] = ref("corrected_database.json", database)
payload["technical_preflight"] = ref("technical.json", technical)
handoff.write(path, payload)
projection = handoff.validate_handoff(path)
assert projection["passed"], projection["errors"]

# B: 一个政府预算文档冻结分母为空，其未执行状态在整体分母中消失。
rows = [
    {"doc_id": "D1", "golden": {"applicable_sub_checks": ["BUD-001:structure"]},
     "run": {"meta": {"check_receipts": [{"rule_id": "BUD-001", "sub_check_id": "structure", "status": "pass"}]}}},
    {"doc_id": "D2", "golden": {"applicable_sub_checks": []}, "run": {"meta": {"check_receipts": []}}},
]
denominator = zc07._evaluate_receipts(rows)
assert denominator["status"] == "computed"
assert denominator["completion_rate"] == denominator["receipt_coverage_rate"] == 1.0
write(OUT / "denominator_input.json", rows)

# C: 一份回放同时作首轮和重跑，机器门禁仍给出独立重复一致。
replay = {
    "doc_id": "REPEAT-CONTRACT", "sha256": "a" * 64,
    "repeatability_basis": {"source_sha256": "a" * 64,
                            "configuration_fingerprint": "config-contract",
                            "version_fingerprint": "version-contract"},
    "legacy": {"findings": [], "analysis_conclusion": "incomplete", "meta": {"check_receipts": []}},
}
write(OUT / "single_replay.json", replay)
manifest = {"documents": [{"doc_id": "REPEAT-CONTRACT", "golden_sha256": "a" * 64,
                           "replay_path": "single_replay.json", "repeat_replay_path": "single_replay.json"}]}
repeat = zc07._evaluate_repeatability(manifest, OUT, "legacy")
assert repeat["status"] == "computed" and repeat["repeatable"] is True
write(OUT / "repeatability_input.json", manifest)

# D: 采用生产 meta.fact_context 摘要格式，事实上下文身份变化仍被忽略。
second = copy.deepcopy(replay)
replay["legacy"]["meta"]["fact_context"] = {"context_version": "fact-context-v1", "fact_count": 62, "operand_count": 62,
                                           "version_token": "context-first", "parse_run_uid": "parse-first", "file_hash": "a" * 64, "overlay_count": 0}
second["legacy"]["meta"]["fact_context"] = {"context_version": "fact-context-v1", "fact_count": 62, "operand_count": 62,
                                           "version_token": "context-changed", "parse_run_uid": "parse-changed", "file_hash": "a" * 64, "overlay_count": 1}
write(OUT / "first_fact_replay.json", replay)
write(OUT / "second_fact_replay.json", second)
fact_manifest = {"documents": [{"doc_id": "REPEAT-CONTRACT", "golden_sha256": "a" * 64,
                                "replay_path": "first_fact_replay.json", "repeat_replay_path": "second_fact_replay.json"}]}
fact_repeat = zc07._evaluate_repeatability(fact_manifest, OUT, "legacy")
assert fact_repeat["status"] == "computed" and fact_repeat["repeatable"] is True
write(OUT / "fact_repeatability_input.json", fact_manifest)

real_runs = []
for name in ("services_run1", "services_run2"):
    directory = ROOT / "outputs/remediation_20261007_round15" / name
    receipt_path = directory / "service_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    scenario = next(row for row in receipt["scenarios"] if row["scenario"] == "original_mixed_final")
    correction = scenario["correction"]
    result_path = directory / "original_mixed_final/corrected/status.json"
    result = json.loads(result_path.read_text())
    ingest = result["result"]["meta"]["structured_ingest"]
    real_runs.append({
        "source_receipt_path": str(receipt_path.relative_to(ROOT)),
        "source_receipt_sha256": digest(receipt_path),
        "source_status_path": str(result_path.relative_to(ROOT)),
        "source_status_sha256": digest(result_path),
        "service_status": receipt["status"],
        "correction_passed": scenario["correction_validation_passed"],
        "parse_cache_hit": ingest.get("parse_cache_hit"),
        "parse_cache": ingest.get("parse_cache"),
        "original_amount": correction["original_raw_value"],
        "corrected_amount": correction["corrected_raw_value"],
        "current_fact_context_amount": correction["persisted_raw_value"],
        "original_database_rows": correction["database_after"]["target_original_facts"],
        "database_numeric_revision": correction["database_after"]["job"]["analysis_revision"],
        "database_result_revision_token": correction["database_after"]["result_revision"],
        "status_revision_token": result["analysis_revision"],
    })

write(OUT / "real_service_contract.json", real_runs)
after = {name: digest(ROOT / name) for name in protected}
write(OUT / "source_integrity.json", {"before": before, "after": after, "unchanged": before == after})
write(OUT / "gate_probe_results.json", {
    "scope": "工程反例，非独立人审真值或业务验收",
    "projection_with_empty_ledger_and_stale_fact_context": projection,
    "partial_empty_denominator": denominator,
    "same_file_used_for_both_runs": repeat,
    "production_fact_context_identity_change_ignored_by_repeatability_signature": fact_repeat,
    "source_unchanged": before == after,
})
print(json.dumps({"probes_reproduced": 4, "source_unchanged": before == after,
                  "real_parse_cache_field_correct": all(row["parse_cache_hit"] is True for row in real_runs)}, ensure_ascii=False))
