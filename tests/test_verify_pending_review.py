"""第一阶段验收器的正反例；测试输入不接触业务数据库或金标。"""

import argparse
import copy
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "pending_acceptance", os.environ.get("PENDING_REVIEW_HARNESS_PATH",
                                         Path(__file__).resolve().parents[1] / "scripts/verify_pending_review.py")
)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
CONTRACT = h.build_identity_contract(Path(__file__).resolve().parents[1])
LEGACY_CODES = ["stated_total_missing", "withheld_amount_unknown", "sum_scope_not_comparable",
                "comparison_identity_unresolved", "item_disclosure_missing", "item_amount_not_included",
                "item_sum_mismatch", "candidate_amount_conflict", "exception_amount_conflict",
                "exception_scope_ambiguous", "source_unit_normalized"]
TEST_LAYERS = {"操作数":["operand_missing","operand_ambiguous"], "规则":["rule_no_reason","parse_error"],
               "表":["table_mapping_incomplete","table_missing"], "口径":["scope_unconfirmed"],
               "语义模型":["ai_timeout","ai_not_requested"], "说明":["text_statement_absent","text_statement_unparsed"]}


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
    vocab = {"codes":sorted({c for codes in TEST_LAYERS.values() for c in codes} | set(LEGACY_CODES)),
             "by_layer":TEST_LAYERS, "legacy_existing":LEGACY_CODES}
    h.write_json(kit / "reason_codes_v1.json", vocab)
    h.write_json(kit / "rule_identity_dimensions.json", CONTRACT)
    h.write_json(kit / "diagnostic_policy.json", h.build_diagnostic_policy(vocab))
    entries = []
    for sample in samples:
        for receipt, decision in [
            ({"obligation_id":"OBL-TXT-T5","rule_id":"V33-222","sub_check_id":"total","status":"fail",
              "incomplete_reason":"合计操作数缺失或不唯一"},"diagnose"),
            ({"obligation_id":"OBL-TXT-T5","rule_id":"V33-224","sub_check_id":"derived-pass","status":"pass",
              "incomplete_reason":"由原文小计推导，与表内一致"},"explanation_only"),
        ]:
            entries.append({"case_id":h.receipt_scope_id(sample, receipt), "decision":decision})
    h.write_json(kit / "diagnostic_scope.json", {"version":1,"entries":entries})
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


@pytest.mark.parametrize("serializer", ["to_dict", "model_dump"])
def test_real_serialized_profile_reaches_identity_context(serializer):
    from src.schemas.document_profile import DocumentProfile
    profile = DocumentProfile()
    profile.report_kind.value = "final"
    profile.writing_scope = {"domain":"final"}
    payload = getattr(profile, serializer)()
    assert "kind" not in payload
    row = h.extract_record({"sample_id":"test","sha256":"a"*64}, {"document_profile":payload},
                           {"coverage_items":[]}, {"finding_keys":["id","created_at"]})
    assert row["identity_context"] == {"kind":profile.kind,"domain":"final"}
    assert "org_scope" not in h._required_dimensions(CONTRACT, "CMM-018", row["identity_context"])


@pytest.mark.parametrize("stage", ["DH-02", "DH-03b"])
def test_passed_derivation_is_not_forced_to_inherit_diagnostic_codes(evidence, stage):
    kit, run = evidence
    base, current = run("base"), run("current",stage)
    for root in (base,current):
        def add(row):
            row["receipts"].append({"obligation_id":"OBL-TXT-T5","rule_id":"V33-224",
                "sub_check_id":"derived-pass","status":"pass","reason_codes":[],
                "incomplete_reason":"由原文小计推导，与表内一致"})
        edit_rows(root,add)
    result = h.compare_runs(base,current,stage,kit)
    assert result["overall"] == "PASS",result
    if stage == "DH-03b":
        edit_rows(current, lambda row: row["receipts"][-1].update(incomplete_reason=""))
        assert verdict(h.compare_runs(base,current,stage,kit),"I3") == "FAIL"


def test_unreviewed_non_incomplete_receipt_never_silently_enters_or_leaves_scope(evidence):
    kit,run=evidence
    base,current=run("base"),run("current","DH-02")
    for root in (base,current):
        edit_rows(root,lambda row:row["receipts"].append({"obligation_id":"OBL-TXT-T5","rule_id":"V33-224",
             "sub_check_id":"unreviewed","status":"pass","reason_codes":[],"incomplete_reason":"尚未审定的说明"}))
    result=h.compare_runs(base,current,"DH-02",kit)
    assert verdict(result,"M12")=="PENDING"
    assert result["formal_acceptance_eligible"] is False


def test_non_upstream_code_cannot_fake_organization_blocker(evidence):
    kit,run=evidence
    base,current=run("base"),run("current","DH-02")
    edit_rows(current,lambda row:update_diagnostics(row,lambda r:r.update(
        blocked_by=[{"dimension":"organization","ref":"fake","reason_code":"operand_missing"}])))
    assert verdict(h.compare_runs(base,current,"DH-02",kit),"T11")=="FAIL"


@pytest.mark.parametrize("code",["ai_timeout","ai_not_requested"])
def test_local_rule_cannot_replace_its_gap_with_ai_reason(evidence,code):
    kit,run=evidence
    base,current=run("base"),run("current","DH-02")
    edit_rows(current,lambda row:update_diagnostics(row,lambda r:r.update(
        reason_codes=[code],next_action="rerun_ai")))
    assert verdict(h.compare_runs(base,current,"DH-02",kit),"T16")=="FAIL"


def test_ai_off_mode_obligation_can_only_report_not_requested(evidence):
    kit,run=evidence
    base,current=run("base"),run("current","DH-02")
    for root in (base,current):
        def add(row, root=root):
            item={"obligation_id":"OBL-AI-SEMANTIC-REVIEW","status":"ai_not_run","detail":"未请求AI","reason_codes":[]}
            if root==current:
                item.update(reason_codes=["ai_not_requested"],blocked_by=[],root_cause_key="ai-mode",next_action="rerun_ai")
            row["obligations"][item["obligation_id"]]=item
            row["coverage_items"][item["obligation_id"]]=copy.deepcopy(item)
        edit_rows(root,add)
    assert h.compare_runs(base,current,"DH-02",kit)["overall"]=="PASS"
    def corrupt(row):
        for collection in (row["obligations"],row["coverage_items"]):
            collection["OBL-AI-SEMANTIC-REVIEW"]["reason_codes"]=["ai_timeout"]
    edit_rows(current,corrupt)
    assert verdict(h.compare_runs(base,current,"DH-02",kit),"T16")=="FAIL"


def test_table_mapping_failure_cannot_be_routed_to_material_author(evidence):
    kit,run=evidence
    base,current=run("base"),run("current","DH-02")
    edit_rows(current,lambda row:update_diagnostics(row,lambda r:r.update(
        reason_codes=["table_mapping_incomplete"],next_action="human_confirm",
        blocked_by=[{"dimension":"instance_uid","ref":"table-A","reason_code":"table_mapping_incomplete"}])))
    assert verdict(h.compare_runs(base,current,"DH-02",kit),"T16")=="FAIL"


def test_all_legacy_codes_have_a_frozen_sampling_layer(evidence):
    kit,_=evidence
    policy=h._diagnostic_policy(kit)
    assert all(policy["reasons"][code]["layer"]=="遗留原因" for code in LEGACY_CODES)


def test_sampling_is_order_independent_and_preserves_quotas(evidence):
    kit,_=evidence
    policy=h._diagnostic_policy(kit)
    groups=[{"root_id":f"root-{i:03}","layer":["操作数","说明","遗留原因"][i%3],
             "flags":["unowned_receipt"] if i==0 else ["scope_override"] if i==1 else ["legacy_reason"] if i%3==2 else []}
            for i in range(90)]
    first=h.select_audit_groups(groups,policy,"20261102",30)
    second=h.select_audit_groups(list(reversed(groups)),policy,20261102,30)
    assert first==second and len(first)==30
    for layer in {g["layer"] for g in groups}:
        assert sum(g["layer"]==layer for g in first)>=2
    for flag in policy["sampling"]["special_flags"]:
        assert any(flag in g["flags"] for g in first)
    assert first!=h.select_audit_groups(groups,policy,"other-seed",30)
    with pytest.raises(SystemExit):
        h.select_audit_groups(groups,policy,"20261102",1)


def test_multilayer_reason_has_one_deterministic_primary_layer(evidence):
    kit,_=evidence
    policy=h._diagnostic_policy(kit)
    first={"reason_codes":["operand_missing","text_statement_unparsed","stated_total_missing"],"blocked_by":[]}
    second={**first,"reason_codes":list(reversed(first["reason_codes"]))}
    assert h._primary_reason(first,policy,CONTRACT)==h._primary_reason(second,policy,CONTRACT)=="text_statement_unparsed"


def test_audit_sample_manifest_repeats_byte_for_byte_and_is_not_a_verdict(evidence,tmp_path):
    kit,run=evidence
    base,current=run("base"),run("current","DH-02")
    def sample(out):
        return h.audit_sample_main(argparse.Namespace(kit=str(kit),baseline=str(base),current=str(current),
            stage="DH-02",allow_partial=False,seed=None,size=None,selftest_sampling=True,out=str(out)))
    out1,out2=tmp_path/"sample1",tmp_path/"sample2"
    assert sample(out1)==sample(out2)==0
    assert (out1/"selection.json").read_bytes()==(out2/"selection.json").read_bytes()
    assert h.read_json(out1/"selection.json")["purpose"]=="manual_audit_selection_not_verdict"
    assert h.sha256_file(out1/"selection.json") in (out1/"selection.sha256").read_text()
    with pytest.raises(SystemExit):
        sample(out1)
    edit_rows(current,lambda row:row["run"].update(status="error"))
    with pytest.raises(SystemExit):
        sample(tmp_path/"bad-sample")


def test_snapshot_refuses_packaged_default_and_empty_git_source(tmp_path,monkeypatch):
    monkeypatch.setattr(h,"REPO_ROOT",tmp_path)
    monkeypatch.setattr(h,"DEFAULT_KIT",tmp_path)
    with pytest.raises(SystemExit):
        h.snapshot_main(argparse.Namespace(source=None,out=str(tmp_path/"wrong")))
    source=tmp_path/"repo"
    source.mkdir()
    subprocess.run(["git","init","-q",str(source)],check=True)
    with pytest.raises(SystemExit):
        h.snapshot_main(argparse.Namespace(source=str(source),out=str(tmp_path/"empty")))
    assert not (tmp_path/"empty").exists()
    (source/"src").mkdir()
    (source/"src/sample.py").write_text("VALUE=1\n")
    assert h.snapshot_main(argparse.Namespace(source=str(source),out=str(tmp_path/"valid")))==0
    assert h.read_json(tmp_path/"valid/SNAPSHOT.json")["file_count"]==1


def test_code_difference_paths_are_preserved_in_comparison(evidence):
    kit,run=evidence
    base,current=run("base"),run("current")
    edit_meta(current,lambda meta:(meta["code"].update(digest="changed"),meta.update(code_after="changed"),
                                  meta["code"]["files"].update({"src/example.py":"changed"})))
    report=h.compare_runs(base,current,"self",kit)
    assert report["code_files_differing"]==["src/example.py"]
    assert next(c for c in report["checks"] if c["id"]=="M5")["details"]==["src/example.py"]


@pytest.mark.parametrize('action',['tech_fix','system_recheck'])
def test_unparsed_text_keeps_specific_reason_and_allows_DH06_routing(evidence,action):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    edit_rows(current,lambda row:update_diagnostics(row,lambda r:r.update(
        reason_codes=['operand_missing','text_statement_unparsed'],next_action=action)))
    report=h.compare_runs(base,current,'DH-02',kit)
    assert report['overall']=='PASS',report


def test_unconfirmed_single_action_restrictions_are_only_recommendations():
    codes=['row_code_missing','row_level_unresolved','column_identity_unconfirmed','column_width_drift',
           'cell_unbound','parse_error','execution_error','rule_no_reason','table_missing',
           'text_statement_absent','name_mismatch_confirmed','code_name_conflict']
    policy=h.build_diagnostic_policy({'codes':codes,'by_layer':{'规则':codes},'legacy_existing':[]})
    for code in codes:
        rule=policy['reasons'][code]
        assert set(rule['actions'])=={'tech_fix','system_recheck','human_confirm'}
        assert rule['constraint_status']=='provisional_nonexclusive'
        assert rule['basis']['source'] and rule['basis']['rationale']
        assert rule['recommended_actions']


def audit_args(kit,base,current,out,**kwargs):
    return argparse.Namespace(kit=str(kit),baseline=str(base),current=str(current),stage='DH-02',
        allow_partial=False,seed=None,size=None,selftest_sampling=False,registration=None,seed_receipt=None,
        out=str(out),**kwargs)


def registered_seed(kit,base,current,tmp_path):
    reg,seed=tmp_path/'registration',tmp_path/'reviewer-seed'
    assert h.audit_register_main(audit_args(kit,base,current,reg))==0
    assert h.audit_seed_main(argparse.Namespace(kit=str(kit),registration=str(reg),reviewer='验收测试员',out=str(seed)))==0
    return reg,seed


def test_fixed_seed_is_explicitly_selftest_only_even_with_full_source(evidence,tmp_path):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    args=audit_args(kit,base,current,tmp_path/'rejected')
    with pytest.raises(SystemExit):h.audit_sample_main(args)
    args.selftest_sampling=True
    args.out=str(tmp_path/'test-only')
    assert h.audit_sample_main(args)==0
    manifest=h.read_json(tmp_path/'test-only/selection.json')
    assert manifest['formal_source'] is True
    assert manifest['formal_sampling_eligible'] is False
    assert manifest['seed_provenance']['mode']=='selftest_only_not_formal'


def test_registration_then_random_receipt_is_bound_and_reproducible(evidence,tmp_path):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    reg,seed=registered_seed(kit,base,current,tmp_path)
    receipt=h.read_json(seed/'seed.json')
    assert len(receipt['seed'])==64
    assert receipt['registration_sha256']==h.sha256_file(reg/'registration.json')
    assert 'seed' not in h.read_json(reg/'registration.json')
    for name in ('one','two'):
        args=audit_args(kit,base,current,tmp_path/name)
        args.registration=str(reg)
        args.seed_receipt=str(seed)
        assert h.audit_sample_main(args)==0
    assert (tmp_path/'one/selection.json').read_bytes()==(tmp_path/'two/selection.json').read_bytes()
    manifest=h.read_json(tmp_path/'one/selection.json')
    assert manifest['formal_sampling_eligible'] is True
    assert manifest['seed_provenance']['registration_sha256']==h.sha256_file(reg/'registration.json')
    assert manifest['seed_provenance']['seed_receipt_sha256']==h.sha256_file(seed/'seed.json')


def test_changed_root_key_after_registration_cannot_grind_sampling(evidence,tmp_path):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    reg,seed=registered_seed(kit,base,current,tmp_path)
    edit_rows(current,lambda row:update_diagnostics(row,lambda r:r.update(root_cause_key='renamed-to-evade-sampling')))
    assert h.compare_runs(base,current,'DH-02',kit)['overall']=='PASS'
    args=audit_args(kit,base,current,tmp_path/'must-not-exist')
    args.registration=str(reg)
    args.seed_receipt=str(seed)
    with pytest.raises(SystemExit):h.audit_sample_main(args)
    assert not (tmp_path/'must-not-exist').exists()


@pytest.mark.parametrize('field',['seed','size','selftest_sampling'])
def test_formal_receipt_cannot_be_mixed_with_seed_or_size_overrides(evidence,tmp_path,field):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    reg,seed=registered_seed(kit,base,current,tmp_path)
    args=audit_args(kit,base,current,tmp_path/'bad')
    args.registration=str(reg)
    args.seed_receipt=str(seed)
    setattr(args,field,True if field=='selftest_sampling' else 1 if field=='size' else '20261102')
    with pytest.raises(SystemExit):h.audit_sample_main(args)


def test_new_submission_cannot_reuse_old_seed_receipt(evidence,tmp_path):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    reg,seed=registered_seed(kit,base,current,tmp_path)
    edit_rows(current,lambda row:update_diagnostics(row,lambda r:r.update(root_cause_key='new-submission')))
    reg2=tmp_path/'registration-new'
    assert h.audit_register_main(audit_args(kit,base,current,reg2))==0
    args=audit_args(kit,base,current,tmp_path/'bad')
    args.registration=str(reg2)
    args.seed_receipt=str(seed)
    with pytest.raises(SystemExit):h.audit_sample_main(args)


def test_seed_issue_uses_randomness_and_records_custody_limit(evidence,tmp_path):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    reg,seed=registered_seed(kit,base,current,tmp_path)
    other=tmp_path/'another-reviewer-seed'
    assert h.audit_seed_main(argparse.Namespace(kit=str(kit),registration=str(reg),reviewer='验收测试员',out=str(other)))==0
    a,b=h.read_json(seed/'seed.json'),h.read_json(other/'seed.json')
    assert a['seed']!=b['seed']
    assert a['identity_assurance']=='reviewer_custody_required_not_authenticated_by_cli'
    with pytest.raises(SystemExit):
        h.audit_seed_main(argparse.Namespace(kit=str(kit),registration=str(reg),reviewer='',out=str(tmp_path/'no-reviewer')))


def test_tampered_seed_receipt_hash_is_rejected(evidence,tmp_path):
    kit,run=evidence
    base,current=run('base'),run('current','DH-02')
    reg,seed=registered_seed(kit,base,current,tmp_path)
    data=h.read_json(seed/'seed.json')
    data['seed']='a'*64
    h.write_json(seed/'seed.json',data)
    args=audit_args(kit,base,current,tmp_path/'bad')
    args.registration=str(reg)
    args.seed_receipt=str(seed)
    with pytest.raises(SystemExit):h.audit_sample_main(args)


def test_parse_artifact_identity_changes_are_not_whitelisted_as_volatile(evidence):
    kit,run=evidence
    base,current=run('base'),run('current')
    for path in (base,current):
        edit_rows(path,lambda row:row['receipts'][0].update(cell_observations=[{'parse_run_uid':'artifact-original'}]))
    edit_rows(current,lambda row:row['receipts'][0]['cell_observations'][0].update(parse_run_uid='artifact-changed'))
    assert verdict(h.compare_runs(base,current,'self',kit),'I3')=='FAIL'
