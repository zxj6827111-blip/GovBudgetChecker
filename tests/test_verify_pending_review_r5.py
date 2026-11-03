"""r5 追加合成测试；不运行 PDF、不接触金标、数据库、服务或正式抽样。"""
import argparse
import copy
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pending_r5", os.environ["PENDING_REVIEW_HARNESS_PATH"])
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
OLD = importlib.util.spec_from_file_location("frozen_r4_tests", ROOT / "tests/test_verify_pending_review.py")
legacy = importlib.util.module_from_spec(OLD)
OLD.loader.exec_module(legacy)
F = h.SAME_SOURCE_FIELD
K = h.SAME_SOURCE_KEY


@pytest.fixture
def evidence(tmp_path):
    kit, old_run = legacy.evidence.__wrapped__(tmp_path)
    for name in ("baseline_start_contract.json", "legacy_same_source_contract.json", "boundary_same_source_r5.json"):
        h.write_json(kit / name, h.read_json(h.DEFAULT_KIT / name))
    digest = h.seal_kit(kit)

    def run(name, stage="self"):
        dest = old_run(name, stage)
        meta = h.read_json(dest / "meta.json")
        meta.update(kit_digest=digest, evidence_kind="tool_selftest_only", collection_stage="tool-selftest")
        h.write_json(dest / "meta.json", meta)
        for p in (dest / "records").glob("*.json.gz"):
            row = h.read_json_gz(p)
            row["same_source_occurrences"] = []
            if stage in h.R5_STAGES:
                for collection in (row["obligations"], row["coverage_items"]):
                    for item in collection.values():
                        item[F] = []
                        item.setdefault("legacy_coverage_terms", [])
            h.write_json_gz(p, row)
        return dest
    return kit, run


def check(report, cid):
    return next(x for x in report["checks"] if x["id"] == cid)["verdict"]


@pytest.mark.parametrize("stage", ["self", "DH-02", "DH-03a", "DH-03b"])
def test_r5_valid_positive_full_synthetic(evidence, stage):
    kit, run = evidence
    result = h.compare_runs(run("base"), run("current", stage), stage, kit)
    assert result["overall"] == "PASS", result
    assert result["formal_acceptance_eligible"] is False
    assert result["tool_selftest_only"] is True


@pytest.mark.parametrize("oid,title,detail,expected", [
    ("OBL-STRUCT-EMPTY-STATEMENT", "", "财政拨款收入支出决算总表", [K]),
    ("OBL-STRUCT-EMPTY-STATEMENT", "财政拨款收入支出决算总表", "", []),
    ("OBL-STRUCT-EMPTY-STATEMENT", "", "财政拨款收支总表", []),
    ("OBL-STRUCT-EMPTY-STATEMENT", "", K, []),
    ("OBL-CROSS-FISCAL-TOTAL", "财政拨款收支总表", "", [K]),
    ("OBL-CROSS-FISCAL-TOTAL", "", "财政拨款收入支出决算总表", [K]),
    ("OBL-CROSS-FISCAL-TOTAL", "FIN_04", "财政拨款总表", []),
    ("OBL-OTHER", "财政拨款收支总表", "财政拨款收入支出决算总表", []),
])
def test_exact_old_algorithm_no_expansion(oid, title, detail, expected):
    assert h.old_same_source_keys(dict(obligation_id=oid, title=title, detail=detail)) == expected


@pytest.mark.parametrize("value", [None, "", K, {}, 1, True, [1], [None], [K, K], ["FIN_04"], [K, "extra"]])
def test_wrong_types_duplicate_unknown_extra_rejected(value):
    assert h.same_source_value_errors(value)


@pytest.mark.parametrize("value", [[], [K]])
def test_valid_key_lists_accepted(value):
    assert not h.same_source_value_errors(value)


def rows(detail="财政拨款收入支出决算总表"):
    oid = "OBL-STRUCT-EMPTY-STATEMENT"
    item = dict(obligation_id=oid, detail=detail, title="空表说明", status="insufficient_data")
    base = dict(obligations={oid: item}, coverage_items={oid: copy.deepcopy(item)}, receipts=[])
    cur = copy.deepcopy(base)
    for c in (cur["obligations"], cur["coverage_items"]):
        c[oid].update({F: [K] if "财政拨款收入支出决算总表" in detail else [], "legacy_coverage_terms": []})
    return base, cur, oid


@pytest.mark.parametrize("stage", ["DH-03a", "DH-03b"])
def test_real_positive_nonempty_key_uses_base_independent_of_new_detail(stage):
    base, cur, oid = rows()
    for coll in (cur["obligations"], cur["coverage_items"]):
        coll[oid]["detail"] = "新文案。规则说明：不再包含任何旧表名。"
    assert h.r5_row_checks(base, cur, stage) == ([], [], [])


def test_new_detail_cannot_create_frozen_evidence():
    base, cur, oid = rows("原文仅有FIN_04简称")
    for coll in (cur["obligations"], cur["coverage_items"]):
        coll[oid]["detail"] = "规则说明：财政拨款收入支出决算总表"
        coll[oid][F] = [K]
    assert h.r5_row_checks(base, cur, "DH-03b")[0]


@pytest.mark.parametrize("where", ["obligations", "coverage_items"])
@pytest.mark.parametrize("action", ["missing", "null", "changed"])
def test_loss_null_or_change_in_transport_rejected(where, action):
    base, cur, oid = rows()
    if action == "missing": cur[where][oid].pop(F)
    else: cur[where][oid][F] = None if action == "null" else []
    k, t, _ = h.r5_row_checks(base, cur, "DH-03a")
    assert k or t


@pytest.mark.parametrize("stage", ["self", "DH-02"])
@pytest.mark.parametrize("value", [None, [], [K]])
def test_early_field_presence_even_null_rejected(stage, value):
    base, cur, oid = rows()
    for collection in (cur["obligations"], cur["coverage_items"]): collection[oid][F] = value
    assert h.r5_row_checks(base, cur, stage)[2]


@pytest.mark.parametrize("path,record_type,legal", [
    (["status", "check_coverage", "instances", 0, F], None, True),
    (["status", "result", "meta", "obligation_coverage", "instances", 0, F], None, True),
    (["status", "review_report", "obligation_coverage", "instances", 0, F], None, True),
    (["status", "review_report", "manual_review", 0, F], "unfinished_check", True),
    (["todo", "coverage_items", 0, F], None, True),
    (["status", "review_report", "manual_review", 0, F], "structured_review", False),
    (["status", "result", "meta", "check_receipts", 0, F], None, False),
    (["status", "result", "rule_findings", 0, F], None, False),
    (["status", "facts", 0, F], None, False),
    (["status", "unregistered_export", 0, F], "unfinished_check", False),
])
def test_exact_legal_raw_positions(path, record_type, legal):
    assert h.legal_same_source_path(dict(path=path, record_type=record_type)) is legal


def test_illegal_raw_field_cannot_be_hidden_by_extraction():
    sample = dict(sample_id="SYNTHETIC", sha256="0"*64)
    status = {"result": {"rule_findings": [{"id": "f", F: [K]}]}, "check_coverage": {"instances": []}}
    row = h.extract_record(sample, status, {}, {"finding_keys": ["id", "created_at"]})
    assert row["same_source_occurrences"][0]["path"] == ["status", "result", "rule_findings", 0, F]
    base, cur, _ = rows()
    cur["same_source_occurrences"] = row["same_source_occurrences"]
    assert h.r5_row_checks(base, cur, "DH-03a")[2]


def test_unknown_field_still_I10_REVIEW(evidence):
    kit, run = evidence
    base, current = run("base"), run("current", "DH-03a")
    legacy.edit_rows(current, lambda row: next(iter(row["obligations"].values())).update(extra_undocumented=1))
    report = h.compare_runs(base, current, "DH-03a", kit)
    assert check(report, "I10") == "REVIEW"
    assert report["overall"] == "REVIEW"


def test_parse_uid_still_I3_FAIL(evidence):
    kit, run = evidence
    base, current = run("base"), run("current")
    for root, uid in ((base, "old"), (current, "new")):
        legacy.edit_rows(root, lambda row: row["receipts"][0].update(parse_run_uid=uid))
    report = h.compare_runs(base, current, "self", kit)
    assert check(report, "I3") == "FAIL"


def formal_origin():
    contract = h.read_json(h.DEFAULT_KIT / "baseline_start_contract.json")
    meta = dict(evidence_kind="phase1_collection", baseline_start_commit=h.APPROVED_BASELINE_START,
                collection_stage="baseline", sample_scope="full", jobs=1, profile_overrides={},
                runtime=copy.deepcopy(contract["runtime"]), code=copy.deepcopy(contract["approved_code"]))
    return meta, contract


def test_approved_origin_positive():
    meta, contract = formal_origin()
    assert h.r5_origin_errors(meta, contract, baseline=True) == []


@pytest.mark.parametrize("field,value", [("baseline_start_commit", "c6cd033d"), ("collection_stage", "DH-03b"),
    ("jobs", 2), ("sample_scope", "partial"), ("profile_overrides", {"OCR_ENABLED": "false"}),
    ("runtime", {}), ("code", {"digest": "final-dh", "files": {}})])
def test_wrong_start_or_final_DH_code_cannot_form_baseline(field, value):
    meta, contract = formal_origin()
    meta[field] = value
    assert h.r5_origin_errors(meta, contract, baseline=True)


def test_added_DH_field_code_detected_even_if_digest_forged():
    meta, contract = formal_origin()
    meta["code"]["files"]["src/schemas/check_receipt.py"] = "changed-DH02"
    assert h.r5_origin_errors(meta, contract, baseline=True)


def test_new_stage_missing_one_of_two_lists_rejected():
    base, cur, oid = rows()
    cur["obligations"][oid].pop("legacy_coverage_terms")
    assert h.r5_row_checks(base, cur, "DH-03a")[0]


def test_r4_seal_is_refused_without_mutation(tmp_path):
    h.write_json(tmp_path / "revision.json", {"revision": "r4"})
    before = (tmp_path / "revision.json").read_bytes()
    with pytest.raises(SystemExit): h.seal_kit(tmp_path)
    assert (tmp_path / "revision.json").read_bytes() == before
    assert not (tmp_path / "SHA256SUMS").exists()


def test_manifest_rebuild_is_refused():
    with pytest.raises(SystemExit): h.manifest_main(argparse.Namespace())


def test_contract_absence_refused_even_for_existing_seal(tmp_path):
    (tmp_path / "SHA256SUMS").write_text("")
    with pytest.raises(SystemExit): h.verify_kit(tmp_path)


@pytest.mark.parametrize("stage", ["DH-03a", "DH-03b"])
def test_sealed_comparator_nonempty_key_valid_and_forged_negative(evidence, stage):
    kit, run = evidence
    base, current = run("base"), run("current", stage)
    target = "OBL-STRUCT-EMPTY-STATEMENT"
    def rename(row, new):
        old = next(iter(row["obligations"]))
        for coll in (row["obligations"], row["coverage_items"]):
            item = coll.pop(old)
            item.update(obligation_id=target, detail="财政拨款收入支出决算总表")
            if new: item.update({F: [K], "legacy_coverage_terms": []})
            coll[target] = item
        for receipt in row["receipts"]: receipt["obligation_id"] = target
        row["todo"]["blocking_ids"] = [target]
        row["todo"]["covered_by_finding"] = {target: False}
    legacy.edit_rows(base, lambda row: rename(row, False))
    legacy.edit_rows(current, lambda row: rename(row, True))
    result = h.compare_runs(base, current, stage, kit)
    assert result["overall"] == "PASS", result
    legacy.edit_rows(current, lambda row: row["obligations"][target].update({F: []}))
    assert check(h.compare_runs(base, current, stage, kit), "R5K") == "FAIL"


def test_formal_raw_transport_summary_cannot_be_omitted():
    base, cur, _ = rows()
    assert h.r5_row_checks(base, cur, "DH-03a", require_raw=True)[1]


def test_formal_raw_transport_positive():
    base, cur, oid = rows()
    cur["same_source_occurrences"] = [dict(path=[*prefix, 0, F], obligation_id=oid, value=[K])
        for prefix in [["status", "check_coverage", "instances"],
                       ["status", "result", "meta", "obligation_coverage", "instances"],
                       ["todo", "coverage_items"]]]
    assert h.r5_row_checks(base, cur, "DH-03a", require_raw=True) == ([], [], [])


def test_unregistered_root_position_rejected_without_crash():
    assert not h.legal_same_source_path({"path": [F]})
