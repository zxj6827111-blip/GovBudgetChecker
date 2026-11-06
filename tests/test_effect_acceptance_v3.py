"""单一故障正负控：独立包与候选分离，不从被检查回执生成事实。"""
import copy
import gzip
import json

import fitz
import pytest

from scripts import effect_acceptance_v3 as h
from scripts.acceptance_artifacts import AdapterError, formal_layers, provenance


def write(root, record, status, code="code"):
    (root / "records").mkdir(parents=True, exist_ok=True)
    (root / "status").mkdir(exist_ok=True)
    for folder, name, obj in (("records", "S.json.gz", record), ("status", "S.status.json.gz", status)):
        with gzip.open(root / folder / name, "wt", encoding="utf-8") as f:
            json.dump(obj, f)
    meta = {"samples": [{"sample_id": "S", "sha256": record["sha256"]}],
            "code": {"digest": code}, "code_after": code, "profile": {"OCR": False}}
    (root / "meta.json").write_text(json.dumps(meta))
    return meta


@pytest.fixture
def case(tmp_path):
    kit = tmp_path / "kit"
    (kit / "pdfs/S").mkdir(parents=True)
    pdf = kit / "pdfs/S/source.pdf"
    with fitz.open() as d:
        p = d.new_page()
        p.insert_text((50, 60), "10.00 = 6.00 + 4.00")
        d.save(pdf)
    sha = h.sha(pdf)
    issue = {"id": "R-1", "rule_id": "R", "severity": "high", "title": "error", "message": "m",
             "page_number": 1, "check_relation_id": "R:sum", "evidence": [{"page": 1, "quote": "10.00"}],
             "location": {"page": 1}, "bbox": None}
    receipt = {"obligation_id": "O", "rule_id": "R", "sub_check_id": "sum", "status": "insufficient_data",
               "reason_codes": ["operand_missing"], "next_action": "tech_fix", "operand_fact_ids": [],
               "formula": "total = sum(parts) (万元)"}
    status = {"checksum": sha, "analysis_revision": "rev-a", "fiscal_year": 2025,
              "organization_name": "unit", "report_kind": "final", "result": {"meta": {}},
              "review_report": {"issues": [issue], "candidate_findings": [], "tips": [], "manual_review": [],
                                "check_receipts": [receipt]}}
    record = {"sample_id": "S", "sha256": sha, "receipts": [receipt], "run": {},
              "findings": [], "finding_bodies": {}, "obligations": {}}
    base, cand = tmp_path / "base", tmp_path / "cand"
    write(base, record, status)
    newr, news = copy.deepcopy(record), copy.deepcopy(status)
    newr["receipts"][0].update(status="pass", operand_fact_ids=["total", "part1", "part2"],
                                 reason_codes=[], next_action=None)
    news["analysis_revision"] = "rev-b"
    news["review_report"]["check_receipts"] = copy.deepcopy(newr["receipts"])
    meta = write(cand, newr, news, "new-code")
    binding = provenance(news, meta, "S", sha)
    evidence = {"schema": "independent-effect-evidence-v1", "samples": [{"binding": binding, "facts": [
        {"fact_id": fid, "value_wan": value, "quote": value, "page": 1, "file_hash": sha,
         "unit": "万元", "year": 2025, "organization": "unit", "table_code": "FIN_03_expenditure",
         "indicator": fid, "row_key": "total", "fund_scope": "all_funds", "stage": "决算",
         "relation_keys": [["O", "R", "sum"]]}
        for fid, value in (("total", "10.00"), ("part1", "6.00"), ("part2", "4.00"))]}], "decisions": []}
    evidence["samples"][0]["relation_contracts"] = [{"key": ["O", "R", "sum"],
        "receipt_digest": h.digest(newr["receipts"][0]), "operand_identities": {
            f["fact_id"]: {k: f[k] for k in ("table_code", "indicator", "row_key", "fund_scope", "stage")}
            for f in evidence["samples"][0]["facts"]}}]
    return base, cand, kit, newr, news, evidence


def check(case):
    b, c, kit, r, s, ev = case
    write(c, r, s, "new-code")
    return h.compare_collections(b, c, kit, ev)


def test_independent_legal_recovery_passes(case):
    assert check(case)["overall"] == "PASS"


@pytest.mark.parametrize("fault", ["unknown_fact", "missing_snapshot", "wrong_revision", "wrong_sample", "wrong_sha",
                                  "wrong_code", "wrong_relation", "wrong_year", "wrong_unit", "false_equation",
                                  "forged_quote", "wrong_page", "wrong_bbox", "non_finite",
                                  "wrong_fund", "wrong_stage", "wrong_org"])
def test_independent_fact_faults_rejected_alone(case, fault):
    _, _, _, r, s, ev = case
    bundle = ev["samples"][0]
    fact = bundle["facts"][0]
    if fault == "unknown_fact":
        r["receipts"][0]["operand_fact_ids"][0] = "nonexistent"
        s["review_report"]["check_receipts"] = copy.deepcopy(r["receipts"])
        # 仅验证事实存在性，不借“回执摘要变化”这个别的故障拒收。
        bundle["relation_contracts"][0]["receipt_digest"] = h.digest(r["receipts"][0])
    elif fault == "missing_snapshot":
        ev["samples"] = []
    elif fault in {"wrong_revision", "wrong_sample", "wrong_sha", "wrong_code"}:
        field = {"wrong_revision": "analysis_revision", "wrong_sample": "sample_id", "wrong_sha": "pdf_sha256",
                 "wrong_code": "code_digest"}[fault]
        bundle["binding"][field] = "wrong"
    elif fault == "wrong_relation": fact["relation_keys"] = [["O", "R", "other"]]
    elif fault == "wrong_year": fact["year"] = 2024
    elif fault == "wrong_unit": fact["unit"] = "元"
    elif fault == "false_equation": fact["value_wan"] = "11"
    elif fault == "forged_quote": fact["quote"] = "forged"
    elif fault == "wrong_page": fact["page"] = 999
    elif fault == "wrong_bbox": fact["bbox"] = [400, 400, 450, 450]
    elif fault == "non_finite": fact["value_wan"] = "NaN"
    elif fault == "wrong_fund": fact["fund_scope"] = "non_fiscal"
    elif fault == "wrong_stage": fact["stage"] = "年初预算"
    elif fault == "wrong_org": fact["organization"] = "other-unit"
    result = check(case)
    assert result["overall"] == "FAIL", fault
    if fault == "unknown_fact":
        assert any("操作数不在独立事实快照" in e for e in result["problems"])


def removal_authorization(case):
    b, c, _, _, s, ev = case
    before, after = h.load_collection(b)["S"], h.load_collection(c)["S"]
    issue = s["review_report"]["issues"][0]
    decision = {"action": "remove_issue", "before": before["source_binding"], "after": after["source_binding"],
                "key": list(h.formal_identity(issue)), "old_digest": h.digest(issue), "new_digest": h.digest(None),
                "reason": "independent original recheck", "sources": [{"page": 1, "quote": "10.00"}]}
    ev["decisions"].append(decision)
    s["review_report"]["issues"] = []
    return decision


def test_precise_removal_authorization_passes(case):
    removal_authorization(case)
    assert check(case)["overall"] == "PASS"


@pytest.mark.parametrize("fault", ["no_authorization", "other_sample", "other_revision", "other_relation"])
def test_removal_does_not_borrow_authorization(case, fault):
    decision = removal_authorization(case)
    if fault == "no_authorization": case[-1]["decisions"] = []
    elif fault == "other_relation": decision["key"][2] = "different-title"
    else: decision["after"]["sample_id" if fault == "other_sample" else "analysis_revision"] = "wrong"
    assert check(case)["overall"] == "FAIL"


def test_existing_issue_evidence_loss_rejected(case):
    issue = case[4]["review_report"]["issues"][0]
    issue.update(evidence=[], location=None, bbox=None)
    assert check(case)["overall"] == "FAIL"


def test_display_id_change_and_originally_absent_bbox_are_legal(case):
    case[4]["review_report"]["issues"][0]["id"] = "R-99"
    assert check(case)["overall"] == "PASS"


def test_adapter_checks_raw_record_independently(case):
    case[3]["receipts"][0]["operand_fact_ids"] = ["fake"]
    assert "原始工件不一致" in check(case)["problems"][0]


@pytest.mark.parametrize("layer", ["issues", "candidate_findings", "tips", "manual_review"])
def test_missing_layer_is_not_zero(case, layer):
    del case[4]["review_report"][layer]
    with pytest.raises(AdapterError): formal_layers(case[4])


def test_same_version_repeat_compares_all_layers_and_code(case, tmp_path):
    _, c, _, r, s, _ = case
    other = tmp_path / "repeat"
    write(other, r, s, "new-code")
    assert h.reproducibility(c, other)["verdict"] == "PASS"
    s["review_report"]["tips"].append({"title": "changed"})
    write(other, r, s, "new-code")
    assert h.reproducibility(c, other)["verdict"] == "FAIL"
