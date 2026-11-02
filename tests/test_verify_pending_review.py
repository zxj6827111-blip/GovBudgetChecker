"""第一阶段验收器的正反例；测试输入不接触业务数据库或金标。"""

import argparse
import copy
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "pending_acceptance", Path(__file__).resolve().parents[1] / "scripts/verify_pending_review.py"
)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def operand():
    return {"role": "total", "table_code": "FIN_03_expenditure", "page": None,
            "row_key": "合计", "column": "决算数", "fund_scope": "general",
            "stage": "final", "year": 2025}


def diagnose(row, stage):
    for collection in (row["obligations"], row["coverage_items"]):
        for item in collection.values():
            item.update(reason_codes=["operand_missing"], blocked_by=[], next_action="tech_fix",
                        root_cause_key="FIN03:total", missing_operands=[operand()])
            if stage in {"DH-03a", "DH-03b"}:
                item["legacy_coverage_terms"] = h._old_coverage_terms(item["detail"])
            if stage == "DH-03b":
                item["detail"] = "支出表合计的决算数尚未定位；由系统修复取数。"
    for receipt in row["receipts"]:
        receipt.update(reason_codes=["operand_missing"], blocked_by=[], next_action="tech_fix",
                       root_cause_key="FIN03:total", missing_operands=[operand()])
        if stage == "DH-03b":
            receipt["incomplete_reason"] = "支出表合计的决算数尚未定位。"


@pytest.fixture
def evidence(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    samples = []
    for name in ("RA", "RB"):
        sid = name + "-001"
        pdf = kit / (sid + ".pdf")
        pdf.write_bytes(("%PDF-test-" + sid).encode())
        sample = {"sample_id": sid, "sha256": h.sha256_file(pdf), "pdf": pdf.name}
        samples.append(sample)
        h.write_json(kit / f"manifest_{name}.json", {"count": 1, "samples": [sample]})
    profile = {"OCR_ENABLED": "false", "PYTHON_DOTENV_DISABLED": "1"}
    h.write_json(kit / "runtime_profile.json", {"env": profile})
    h.write_json(kit / "gold_exclusion.json", {"sha256": []})
    h.write_json(kit / "reason_codes_v1.json", {"codes": ["operand_missing", "operand_ambiguous", "rule_no_reason"]})
    digest = h.seal_kit(kit)

    def run(name, stage="self"):
        dest = tmp_path / name
        dest.mkdir()
        code = "original" if stage == "self" else "remediated"
        h.write_json(dest / "meta.json", {
            "schema_version": h.META_SCHEMA_VERSION, "kit_version": h.KIT_VERSION,
            "kit_digest": digest, "samples": [{k: s[k] for k in ("sample_id", "sha256")} for s in samples],
            "sample_scope": "full", "profile": profile, "profile_overrides": {},
            "runtime": {"python_version": "test", "packages_sha256": "test", "package_count": 1},
            "code": {"digest": code}, "code_after": code, "code_env_names": {}, "failed": [],
            "dotenv_isolation_ok": True, "code_root_writes": [],
            "entries": [{"sample_id": s["sample_id"], "ok": True, "dotenv_leaked": [],
                         "env_seen_matches_profile": True} for s in samples],
        })
        for s in samples:
            oid = "OBL-TXT-T5"
            item = {"obligation_id": oid, "status": "insufficient_data", "reason_codes": [],
                    "detail": "说明中的款“行政事业单位离退休”尚未定位金额"}
            row = {"sample_id": s["sample_id"], "sha256": s["sha256"],
                   "run": {"status": "review_required", "error": None, "analysis_conclusion": "incomplete"},
                   "obligation_summary": {"catalog_version": h.CATALOG_V12},
                   "obligations": {oid: item}, "coverage_items": {oid: copy.deepcopy(item)},
                   "receipts": [{"rule_id": "V33-222", "obligation_id": oid, "sub_check_id": "total",
                                 "status": "insufficient_data", "reason_codes": [],
                                 "incomplete_reason": "合计操作数缺失或不唯一"}],
                   "findings": [], "finding_bodies": {}, "reading_items": [], "ledger_sources_equal": True,
                   "todo": {"coverage_available": True, "blocking_ids": [oid], "covered_by_finding": {oid: False}}}
            if stage != "self":
                diagnose(row, stage)
            h.write_json_gz(dest / "records" / (s["sample_id"] + ".json.gz"), row)
        return dest

    return kit, run


def edit_meta(root, fn):
    path = root / "meta.json"
    data = h.read_json(path)
    fn(data)
    h.write_json(path, data)


def edit_rows(root, fn):
    for path in (root / "records").glob("*.json.gz"):
        row = h.read_json_gz(path)
        fn(row)
        h.write_json_gz(path, row)


def verdict(report, check):
    return next(c["verdict"] for c in report["checks"] if c["id"] == check)


@pytest.mark.parametrize("stage", ["self", "DH-02", "DH-03a", "DH-03b"])
def test_valid_full_outputs_pass(evidence, stage):
    kit, run = evidence
    result = h.compare_runs(run("base"), run("current", stage), stage, kit)
    assert result["overall"] == "PASS", result
    assert result["formal_acceptance_eligible"] is True


def test_empty_samples_are_refused(evidence):
    kit, _ = evidence
    with pytest.raises(SystemExit):
        h.load_samples(kit, [])


@pytest.mark.parametrize("kind", ["empty", "partial"])
def test_missing_sample_scope_cannot_pass_formal_acceptance(evidence, kind):
    kit, run = evidence
    base, current = run("base"), run("current")
    for root in (base, current):
        def trim(meta):
            meta["samples"] = meta["samples"][:0 if kind == "empty" else 1]
            meta["entries"] = meta["entries"][:len(meta["samples"])]
            meta["sample_scope"] = "partial"
        edit_meta(root, trim)
        for path in (root / "records").glob("*.json.gz"):
            if kind == "empty" or path.name.startswith("RB"):
                path.unlink()
    assert verdict(h.compare_runs(base, current, "self", kit), "M2") == "FAIL"
    partial = h.compare_runs(base, current, "self", kit, allow_partial=True)
    assert partial["overall"] == ("FAIL" if kind == "empty" else "PARTIAL_PASS")
    assert partial["formal_acceptance_eligible"] is False


@pytest.mark.parametrize("field,value,check", [
    ("kit_digest", "different-kit", "M1"),
    ("profile", {"OCR_ENABLED": "true"}, "M3"),
    ("profile_overrides", {"OCR_ENABLED": "false"}, "M3"),
])
def test_same_wrong_conditions_on_both_sides_are_rejected(evidence, field, value, check):
    kit, run = evidence
    base, current = run("base"), run("current")
    for root in (base, current):
        edit_meta(root, lambda meta: meta.update({field: value}))
    assert verdict(h.compare_runs(base, current, "self", kit), check) == "FAIL"


@pytest.mark.parametrize("field,value", [("sample_id", "OTHER"), ("sha256", "0" * 64)])
def test_record_identity_is_checked(evidence, field, value):
    kit, run = evidence
    base, current = run("base"), run("current")
    edit_rows(current, lambda row: row.update({field: value}))
    assert verdict(h.compare_runs(base, current, "self", kit), "M2") == "FAIL"


def test_identical_failed_records_do_not_form_a_valid_baseline(evidence):
    kit, run = evidence
    base, current = run("base"), run("current")
    for root in (base, current):
        edit_rows(root, lambda row: row["run"].update(status="failed", error="failure"))
    assert verdict(h.compare_runs(base, current, "self", kit), "M6") == "FAIL"


@pytest.mark.parametrize("status", ["failed", "error", "queued", "done"])
def test_worker_rejects_failed_or_incomplete_pipeline_output(tmp_path, monkeypatch, status):
    job = tmp_path / "job"
    job.mkdir()
    h.write_json(job / "status.json", {"status": status})
    task, result = tmp_path / "task.json", tmp_path / "result.json"
    h.write_json(task, {"kind": "pipeline", "job_dir": str(job)})
    monkeypatch.setattr(h, "_worker_prepare", lambda _: None)
    monkeypatch.setattr(h, "adapter_run_pipeline", lambda _: None)
    monkeypatch.setattr(h, "adapter_todo", lambda *_: pytest.fail("无效结果不应进入待办采集"))
    assert h.worker_main(argparse.Namespace(code_root=str(tmp_path), task=str(task), result=str(result))) == 1
    assert h.read_json(result)["ok"] is False


@pytest.mark.parametrize("field", ["reason_codes", "blocked_by", "missing_operands", "next_action", "root_cause_key"])
def test_each_diagnostic_field_must_reach_coverage(evidence, field):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    edit_rows(current, lambda row: next(iter(row["coverage_items"].values())).pop(field))
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T12") == "FAIL"


def test_extra_coverage_item_is_rejected(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    edit_rows(current, lambda row: row["coverage_items"].update({"EXTRA": {"obligation_id": "EXTRA"}}))
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "I6") == "FAIL"


@pytest.mark.parametrize("field,value,check", [
    ("missing_operands", 123, "T3"), ("missing_operands", [{}], "T3"),
    ("candidates", "不是列表", "T4"), ("candidates", [{}], "T4"),
    ("blocked_by", [{}], "T11"), ("root_cause_key", 123, "T11"),
])
def test_invalid_diagnostic_shapes_are_rejected(evidence, field, value, check):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    def bad(row):
        for receipt in row["receipts"]:
            receipt[field] = value
            if field == "candidates":
                receipt["reason_codes"].append("operand_ambiguous")
    edit_rows(current, bad)
    assert verdict(h.compare_runs(base, current, "DH-02", kit), check) == "FAIL"


def test_explicit_unknown_operand_positions_are_allowed(evidence):
    kit, run = evidence
    result = h.compare_runs(run("base"), run("current", "DH-02"), "DH-02", kit)
    assert result["overall"] == "PASS"


def test_dh03b_cannot_delete_all_reason_text(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-03b")
    def remove(row):
        for collection in (row["obligations"], row["coverage_items"]):
            for item in collection.values():
                item.pop("detail")
        for receipt in row["receipts"]:
            receipt.pop("incomplete_reason")
    edit_rows(current, remove)
    assert verdict(h.compare_runs(base, current, "DH-03b", kit), "T10") == "FAIL"


def test_selftest_selects_a_sample_with_real_incomplete_receipts(evidence):
    _, run = evidence
    root = run("base")
    path = root / "records/RA-001.json.gz"
    row = h.read_json_gz(path)
    row["receipts"][0]["status"] = "pass"
    h.write_json_gz(path, row)
    assert h._selftest_probe(root) == "RB-001"


def test_selftest_refuses_missing_negative_test_prerequisites(evidence):
    _, run = evidence
    root = run("base")
    edit_rows(root, lambda row: [r.update(status="pass") for r in row["receipts"]])
    with pytest.raises(SystemExit):
        h._selftest_probe(root)
