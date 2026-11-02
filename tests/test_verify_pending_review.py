"""第一阶段验收器的正反例；测试输入不接触业务数据库或金标。"""

import argparse
import copy
import importlib.util
import shutil
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "pending_acceptance", Path(__file__).resolve().parents[1] / "scripts/verify_pending_review.py"
)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
CONTRACT = h.build_identity_contract(Path(__file__).resolve().parents[1])


def operand():
    return {"source": "table", "role": "total", "table_code": "FIN_03_expenditure", "page": None,
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
    h.write_json(kit / "reason_codes_v1.json", {"codes": ["operand_missing", "operand_ambiguous", "rule_no_reason",
                 "table_mapping_incomplete", "scope_unconfirmed", "ai_timeout"]})
    h.write_json(kit / "rule_identity_dimensions.json", CONTRACT)
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
            "code": {"digest": code, "files": {CONTRACT["source_file"]: CONTRACT["source_sha256"]}},
            "code_after": code, "code_env_names": {}, "failed": [],
            "dotenv_isolation_ok": True, "code_root_writes": [],
            "entries": [{"sample_id": s["sample_id"], "ok": True, "dotenv_leaked": [],
                         "env_seen_matches_profile": True} for s in samples],
        })
        for s in samples:
            oid = "OBL-TXT-T5"
            item = {"obligation_id": oid, "status": "insufficient_data", "reason_codes": [],
                    "detail": "说明中的款“行政事业单位离退休”尚未定位金额"}
            row = {"sample_id": s["sample_id"], "sha256": s["sha256"],
                   "identity_context": {"kind": "final", "domain": "final"},
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


def update_diagnostics(row, fn):
    """测试侧独立构造回执投影，避免接口丢字段掩盖被测一致性问题。"""
    for receipt in row["receipts"]:
        fn(receipt)
        oid = receipt["obligation_id"]
        for collection in (row["obligations"], row["coverage_items"]):
            for field in ("reason_codes", "blocked_by", "root_cause_key", "next_action", "missing_operands", "candidates"):
                if field in receipt:
                    collection[oid][field] = copy.deepcopy(receipt[field])


def test_upstream_reason_cannot_have_empty_blockers(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    edit_rows(current, lambda row: update_diagnostics(row, lambda r: r.update(
        reason_codes=["table_mapping_incomplete"], blocked_by=[])))
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T11") == "FAIL"


def test_wrong_obligation_reason_and_action_cannot_replace_receipt(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    def bad(row):
        for collection in (row["obligations"], row["coverage_items"]):
            for item in collection.values():
                item.update(reason_codes=["ai_timeout"], next_action="rerun_ai", root_cause_key="ai")
    edit_rows(current, bad)
    report = h.compare_runs(base, current, "DH-02", kit)
    assert verdict(report, "T12") == "PASS"
    assert verdict(report, "T13") == "FAIL"


@pytest.mark.parametrize("field,value", [("root_cause_key", "not-a-child-root"), ("next_action", "rerun_ai")])
def test_obligation_root_and_action_must_come_from_same_receipt(evidence, field, value):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    def bad(row):
        for collection in (row["obligations"], row["coverage_items"]):
            next(iter(collection.values()))[field] = value
    edit_rows(current, bad)
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T13") == "FAIL"


@pytest.mark.parametrize("same_point,same_key,expected", [(True, True, "PASS"), (True, False, "FAIL"),
                                                         (False, True, "FAIL"), (False, False, "PASS")])
def test_root_keys_match_primary_blocker_points(evidence, same_point, same_key, expected):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    def add(row):
        oid = next(iter(row["obligations"]))
        for collection in (row["obligations"], row["coverage_items"]):
            collection["OBL-SECOND"] = copy.deepcopy(collection[oid])
            collection["OBL-SECOND"]["obligation_id"] = "OBL-SECOND"
        second = copy.deepcopy(row["receipts"][0])
        second.update(obligation_id="OBL-SECOND", sub_check_id="second")
        row["receipts"].append(second)
    for root in (base, current):
        edit_rows(root, add)
    def change(row):
        for index, receipt in enumerate(row["receipts"]):
            ref = "table-A" if same_point or index == 0 else "table-B"
            key = "root-A" if same_key or index == 0 else "root-B"
            receipt.update(reason_codes=["table_mapping_incomplete"], root_cause_key=key,
                           blocked_by=[{"dimension":"instance_uid","ref":ref,"reason_code":"table_mapping_incomplete"}])
            oid = receipt["obligation_id"]
            for collection in (row["obligations"], row["coverage_items"]):
                collection[oid].update({k:copy.deepcopy(receipt[k]) for k in ("reason_codes","root_cause_key","blocked_by")})
    edit_rows(current, change)
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T14") == expected


def text_operand():
    return {**operand(), "source":"text", "table_code":None, "page":20,
            "section":"三、支出决算情况说明", "column":None, "row_key":"基本支出"}


def test_text_operand_needs_section_instead_of_fabricated_table(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    edit_rows(current, lambda row: update_diagnostics(row, lambda r: r.update(missing_operands=[text_operand()])))
    report = h.compare_runs(base, current, "DH-02", kit)
    assert report["overall"] == "PASS", report


@pytest.mark.parametrize("valid", [True, False])
def test_text_candidates_use_page_spans_without_cell_ids(evidence, valid):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    candidates = [{"source":"text","section":"三、支出决算情况说明","page":20,
                   "span":[i,i+3],"value":"0.00"} for i in [10,30]]
    if not valid:
        candidates[0].pop("span")
    edit_rows(current, lambda row: update_diagnostics(row, lambda r: r.update(
        reason_codes=["operand_missing","operand_ambiguous"], missing_operands=[text_operand()], candidates=candidates)))
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T4") == ("PASS" if valid else "FAIL")


@pytest.mark.parametrize("kind,expected", [("unknown", "PASS"), ("final", "FAIL")])
def test_frozen_rule_dimensions_preserve_context_exceptions(evidence, kind, expected):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    def common(row):
        row["identity_context"] = {"kind":kind,"domain":"unknown"}
        row["receipts"][0]["rule_id"] = "CMM-018"
    for root in (base, current):
        edit_rows(root, common)
    edit_rows(current, lambda row: update_diagnostics(row, lambda r: r.update(
        reason_codes=["scope_unconfirmed"], blocked_by=[{"dimension":"org_scope","ref":"scope-A","reason_code":"scope_unconfirmed"}])))
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T11") == expected


def test_internal_rule_cannot_claim_dependency_on_org_scope(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    edit_rows(current, lambda row: update_diagnostics(row, lambda r: r.update(
        reason_codes=["scope_unconfirmed"], blocked_by=[{"dimension":"org_scope","ref":"scope-A","reason_code":"scope_unconfirmed"}])))
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T11") == "FAIL"


def test_changed_identity_declaration_invalidates_comparison(evidence):
    kit, run = evidence
    base, current = run("base"), run("current")
    edit_meta(current, lambda meta: meta["code"]["files"].update({CONTRACT["source_file"]:"changed"}))
    assert verdict(h.compare_runs(base, current, "self", kit), "M11") == "FAIL"


def test_missing_identity_contract_is_refused(evidence):
    kit, _ = evidence
    (kit / "rule_identity_dimensions.json").unlink()
    with pytest.raises(SystemExit):
        h._identity_contract(kit)
    with pytest.raises(SystemExit):
        h.seal_kit(kit)


def test_packaged_tool_finds_its_own_frozen_kit(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "rule_identity_dimensions.json").write_text("{}")
    (tmp_path / "manifest_RA.json").write_text("{}")
    source = tmp_path / "tools/verify_pending_review.py"
    shutil.copy2(Path(h.__file__), source)
    spec = importlib.util.spec_from_file_location("packaged_pending_acceptance", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_KIT == tmp_path


def test_partial_issue_aggregate_still_provides_diagnostic_lineage(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    for root in (base, current):
        edit_rows(root, lambda row: row["receipts"][0].update(status="fail"))
    report = h.compare_runs(base, current, "DH-02", kit)
    assert report["overall"] == "PASS", report


def test_unowned_native_receipt_is_kept_without_inventing_parent_link(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    for root in (base, current):
        def add(row):
            receipt = copy.deepcopy(row["receipts"][0])
            receipt.update(obligation_id=None, sub_check_id="unowned-relation")
            if "root_cause_key" in receipt:
                receipt["root_cause_key"] = "independent-root"
            row["receipts"].append(receipt)
        edit_rows(root, add)
    assert h.compare_runs(base, current, "DH-02", kit)["overall"] == "PASS"


@pytest.mark.parametrize("matching_action,expected", [(True,"PASS"), (False,"FAIL"), ("deeper","FAIL")])
def test_aggregate_can_select_one_root_without_losing_other_actions(evidence, matching_action, expected):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-02")
    for root in (base, current):
        def add(row):
            receipt = copy.deepcopy(row["receipts"][0])
            receipt["sub_check_id"] = "scope-check"
            row["receipts"].append(receipt)
        edit_rows(root, add)
    def change(row):
        child = row["receipts"][1]
        child.update(reason_codes=["scope_unconfirmed"], root_cause_key="scope-root", next_action="human_confirm",
                     blocked_by=[{"dimension":"fund_scope","ref":"fund-A","reason_code":"scope_unconfirmed"}])
        for collection in (row["obligations"],row["coverage_items"]):
            parent = next(iter(collection.values()))
            parent.update(reason_codes=["operand_missing","scope_unconfirmed"], root_cause_key="scope-root",
                          next_action="human_confirm" if matching_action else "tech_fix", blocked_by=copy.deepcopy(child["blocked_by"]))
            if matching_action == "deeper":
                parent.update(root_cause_key=row["receipts"][0]["root_cause_key"], next_action="tech_fix")
    edit_rows(current, change)
    assert verdict(h.compare_runs(base, current, "DH-02", kit), "T13") == expected
