"""单一故障正负控：独立包与候选分离，不从被检查回执生成事实。"""
import copy
import gzip
import json

import fitz
import pytest

from scripts import effect_acceptance_v3 as h
from scripts.acceptance_artifacts import AdapterError, formal_layers, provenance, adapt_record


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
        p.insert_text((50, 40), "Table; wan")
        for x, label, value in [(100, 'Total', '10.00'), (200, 'Basic', '6.00'), (300, 'Project', '4.00')]:
            p.insert_text((x, 65), label)
            p.insert_text((x, 95), value)
        p.insert_text((50, 95), 'All')
        d.save(pdf)
    sha = h.sha(pdf)
    issue = {"id": "R-1", "rule_id": "R", "severity": "high", "title": "error", "message": "m",
             "page_number": 1, "check_relation_id": "R:sum", "evidence": [{"page": 1, "quote": "10.00"}],
             "location": {"page": 1}, "bbox": None}
    receipt = {"obligation_id": "O", "rule_id": "R", "sub_check_id": "sum", "status": "insufficient_data",
               "reason_codes": ["operand_missing"], "next_action": "tech_fix", "operand_fact_ids": [],
               "formula": "total = sum(parts) (万元)"}
    status = {"checksum": sha, "analysis_revision": "rev-a", "fiscal_year": 2025,
              "organization_name": "unit", "report_kind": "final", "result": {"meta": {
                  "rule_policy_version": "v3_3_portable", "rule_registry_version": "fixture-registry-v1"}},
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
    evidence = {"schema": "independent-effect-evidence-v2", "samples": [{"binding": binding, "facts": [
        {"fact_id": fid, "value_wan": value, "quote": value, "page": 1, "file_hash": sha,
         "unit": "万元", "year": 2025, "organization": "unit", "table_code": "FIN_03_expenditure",
         "indicator": fid, "row_key": "total", "fund_scope": "all_funds", "stage": "决算",
         "relation_keys": [["O", "R", "sum"]]}
        for fid, value in (("total", "10.00"), ("part1", "6.00"), ("part2", "4.00"))]}], "decisions": []}
    with fitz.open(pdf) as d:
        def anchor(quote):
            return {'page': 1, 'quote': quote, 'bbox': list(d[0].search_for(quote)[0])}
        for fact, label in zip(evidence['samples'][0]['facts'], ['Total', 'Basic', 'Project'], strict=True):
            fact['source_cell'] = {'schema': 'independent-fiscal-cell-v2',
                'raw_value': fact['value_wan'], 'raw_unit': 'wan',
                'cell': anchor(fact['quote']), 'row': anchor('All'),
                'column': anchor(label), 'table': {'page': 1, 'quote': 'Table', 'bbox': [40, 20, 400, 120]},
                'unit_source': anchor('wan')}
    evidence["samples"][0]["relation_contracts"] = [{"key": ["O", "R", "sum"],
        "receipt_digest": h.digest(newr["receipts"][0]), "operand_identities": {
            f["fact_id"]: {**{k: f[k] for k in ("table_code", "indicator", "row_key", "fund_scope", "stage")}, "source_cell_digest": h.digest(f["source_cell"])}
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
    s = copy.deepcopy(s)
    s['analysis_revision'] = 'rev-independent-repeat'
    write(other, r, s, "new-code")
    assert h.reproducibility(c, other)["verdict"] == "PASS"
    s["review_report"]["tips"].append({"title": "changed"})
    write(other, r, s, "new-code")
    assert h.reproducibility(c, other)["verdict"] == "FAIL"


def test_reusing_same_artifact_is_not_independent_repeat(case, tmp_path):
    _, c, _, r, s, _ = case
    assert h.reproducibility(c, c)['verdict'] == 'FAIL'
    copied = tmp_path / 'copied'
    write(copied, r, s, 'new-code')
    assert h.reproducibility(c, copied)['verdict'] == 'FAIL'


def test_adapter_keeps_revision_binding_outside_problem_content(case):
    _, c, _, r, s, _ = case
    meta = h.read(c / "meta.json")
    first = adapt_record(r, s, meta)
    second_status = copy.deepcopy(s)
    second_status["analysis_revision"] = "rev-next"
    for issue in second_status["review_report"]["issues"]:
        issue.update(analysis_revision="rev-next", created_at=999)
    second = adapt_record(r, second_status, meta)
    assert first["findings"] == second["findings"]
    assert first["source_binding"] != second["source_binding"]


@pytest.mark.parametrize('approved', [False, True])
def test_missing_relation_requires_precise_replacement_evidence(case, approved):
    b, c, _, r, s, ev = case
    before = h.load_collection(b)['S']
    old = before['receipts'][0]
    r['receipts'][0]['sub_check_id'] = 'sum-located'
    s['review_report']['check_receipts'] = copy.deepcopy(r['receipts'])
    s['review_report']['issues'][0]['check_relation_id'] = 'R:sum-located'
    bundle = ev['samples'][0]
    for fact in bundle['facts']: fact['relation_keys'] = [['O', 'R', 'sum-located']]
    bundle['relation_contracts'][0].update(key=['O', 'R', 'sum-located'], receipt_digest=h.digest(r['receipts'][0]))
    if approved:
        ev['decisions'].append({'action': 'replace_relation', 'before': before['source_binding'],
            'after': bundle['binding'], 'key': ['O', 'R', 'sum'], 'replacement_keys': [['O', 'R', 'sum-located']],
            'old_digest': h.digest(old), 'new_digest': h.digest(r['receipts']),
            'reason': 'source now located', 'sources': [{'page': 1, 'quote': '10.00'}]})
    if approved:
        old_issue = before['finding_layers']['formal'][0]
        new_issue = s['review_report']['issues'][0]
        ev['decisions'].append({'action': 'issue_changed', 'before': before['source_binding'],
            'after': bundle['binding'], 'key': list(h.formal_identity(old_issue)),
            'old_digest': h.digest(old_issue), 'new_digest': h.digest(new_issue),
            'reason': 'independent relation binding reviewed', 'sources': [{'page': 1, 'quote': '10.00'}]})
    assert check(case)['overall'] == ('PASS' if approved else 'FAIL')


@pytest.mark.parametrize('fault', ['missing_status', 'unknown_status', 'duplicate_receipt', 'duplicate_issue_id',
                                  'missing_rule_version', 'missing_issue_content'])
def test_invalid_source_shape_never_means_no_problem(case, fault):
    _, _, _, r, s, _ = case
    if fault == 'missing_status':
        del r['receipts'][0]['status']
        s['review_report']['check_receipts'] = copy.deepcopy(r['receipts'])
    elif fault == 'unknown_status':
        r['receipts'][0]['status'] = 'anything'
        s['review_report']['check_receipts'] = copy.deepcopy(r['receipts'])
    elif fault == 'duplicate_receipt':
        r['receipts'].append(copy.deepcopy(r['receipts'][0]))
        s['review_report']['check_receipts'] = copy.deepcopy(r['receipts'])
    elif fault == 'duplicate_issue_id':
        s['review_report']['issues'].append(copy.deepcopy(s['review_report']['issues'][0]))
    elif fault == 'missing_rule_version':
        del s['result']['meta']['rule_registry_version']
    else:
        s['review_report']['issues'][0].pop('message')
        s['review_report']['issues'][0].pop('title')
    assert check(case)['overall'] == 'FAIL'


def test_typed_reference_annotation_does_not_change_original_evidence(case):
    case[4]['review_report']['issues'][0]['location']['reference_kind'] = 'check'
    assert check(case)['overall'] == 'PASS'


def test_forged_location_is_rejected_even_when_original_evidence_is_retained(case):
    case[4]['review_report']['issues'][0]['location']['page'] = 999
    assert check(case)['overall'] == 'FAIL'


def test_completed_receipt_diagnostic_cleanup_is_not_counted_as_recovery(case):
    b, _, _, r, s, ev = case
    base_record, base_status = copy.deepcopy(r), copy.deepcopy(s)
    base_record['receipts'][0].update(reason_codes=['operand_missing'], next_action='system_recheck')
    base_status['review_report']['check_receipts'] = copy.deepcopy(base_record['receipts'])
    write(b, base_record, base_status, 'code')
    ev['samples'] = []  # 没有声称新恢复，无需借独立事实快照证明诊断字段清理。
    result = check(case)
    assert result['overall'] == 'PASS'
    assert all(row['change_kind'] == 'diagnostic_only' for row in result['ledger'])


def test_na_basis_remains_part_of_evaluation():
    old = {'status': 'not_applicable', 'incomplete_reason': '原文无支出'}
    new = {**old, 'incomplete_reason': '任意新说明'}
    assert h.receipt_evaluation(old) != h.receipt_evaluation(new)


def issue_change_decision(case):
    b, c, _, _, status, evidence = case
    old = h.load_collection(b)['S']['finding_layers']['formal'][0]
    new = status['review_report']['issues'][0]
    decision = {'action': 'issue_changed', 'before': h.load_collection(b)['S']['source_binding'],
                'after': h.load_collection(c)['S']['source_binding'], 'key': list(h.formal_identity(old)),
                'old_digest': h.digest(old), 'new_digest': h.digest(new),
                'reason': 'exact object independently reviewed', 'sources': [{'page': 1, 'quote': '10.00'}]}
    evidence['decisions'].append(decision)
    return decision


@pytest.mark.parametrize('field', ['check_relation_id', 'group_members', 'message', 'title'])
def test_formal_semantic_change_requires_full_object_authorization(case, field):
    issue = case[4]['review_report']['issues'][0]
    if field == 'check_relation_id':
        for root in case[:2]:
            record = h.read(root / 'records/S.json.gz')
            status = h.read(root / 'status/S.status.json.gz')
            record['receipts'].append({**record['receipts'][0], 'sub_check_id': 'other'})
            status['review_report']['check_receipts'] = copy.deepcopy(record['receipts'])
            write(root, record, status, 'code' if root == case[0] else 'new-code')
        # New relation exists both sides: this control cannot be rejected just for a dangling ref.
        case[3]['receipts'].append({**case[3]['receipts'][0], 'sub_check_id': 'other'})
        case[4]['review_report']['check_receipts'] = copy.deepcopy(case[3]['receipts'])
        case[-1]['samples'][0]['relation_contracts'].append({
            **case[-1]['samples'][0]['relation_contracts'][0], 'key': ['O', 'R', 'other'],
            'receipt_digest': h.digest(case[3]['receipts'][-1])})
        for f in case[-1]['samples'][0]['facts']: f['relation_keys'].append(['O', 'R', 'other'])
        issue[field] = 'R:other'
    elif field == 'group_members':
        issue['location']['relation_ids'] = ['R:other']
    else:
        issue[field] = 'changed content'
    assert check(case)['overall'] == 'FAIL'
    issue_change_decision(case)
    assert check(case)['overall'] == 'PASS'


@pytest.mark.parametrize('fault', ['quote_other_column', 'same_value_wrong_cell', 'wrong_raw_value', 'wrong_unit', 'missing_binding'])
def test_independent_raw_cell_binding_rejects_displaced_facts(case, fault):
    f = case[-1]['samples'][0]['facts'][1]
    if fault == 'quote_other_column': f['quote'] = '4.00'
    elif fault == 'same_value_wrong_cell': f['source_cell']['column'] = copy.deepcopy(case[-1]['samples'][0]['facts'][2]['source_cell']['column'])
    elif fault == 'wrong_raw_value': f['source_cell']['raw_value'] = '4.00'
    elif fault == 'wrong_unit': f['source_cell']['raw_unit'] = '元'
    else: del f['source_cell']
    assert check(case)['overall'] == 'FAIL'


@pytest.mark.parametrize('fault', [None, 'wrong_sample', 'wrong_revision', 'wrong_object', 'wrong_layer', 'wrong_digest', 'forged_source', 'v1'])
def test_nonformal_change_decisions_are_object_and_layer_bound(case, fault):
    b, c, _, _, s, ev = case
    new = {'id': 'M1', 'title': 'current observation'}
    s['review_report']['manual_review'] = [new]
    decision = {'action': 'layer_object_changed', 'before': h.load_collection(b)['S']['source_binding'],
                'after': h.load_collection(c)['S']['source_binding'], 'key': ['manual_review', 'id', 'M1'],
                'old_digest': h.digest(None), 'new_digest': h.digest(new),
                'reason': 'independent exact observation review', 'sources': [{'page': 1, 'quote': '10.00'}]}
    if fault == 'wrong_sample': decision['after'] = {**decision['after'], 'sample_id': 'other'}
    elif fault == 'wrong_revision': decision['after'] = {**decision['after'], 'analysis_revision': 'old'}
    elif fault == 'wrong_object': decision['key'][-1] = 'other'
    elif fault == 'wrong_layer': decision['key'][0] = 'tips'
    elif fault == 'wrong_digest': decision['new_digest'] = 'wrong'
    elif fault == 'forged_source': decision['sources'][0]['quote'] = 'not on source'
    elif fault == 'v1': ev['schema'] = 'independent-effect-evidence-v1'
    assert check(case)['overall'] == 'PENDING'
    ev['decisions'].append(decision)
    assert check(case)['overall'] == ('PASS' if fault is None else 'PENDING')


def test_fact_correct_unit_conversion_from_original_yuan(case):
    _, _, kit, _, status, ev = case
    pdf = kit/'pdfs/S/source.pdf'
    with fitz.open() as d:
        p = d.new_page()
        p.insert_text((50, 40), 'Table; yuan')
        p.insert_text((50, 95), 'All')
        for x, label, val in [(100, 'Total', '100000'), (200, 'Basic', '60000'), (300, 'Project', '40000')]:
            p.insert_text((x, 65), label)
            p.insert_text((x, 95), val)
        d.save(pdf)
    sha = h.sha(pdf)
    for root in case[:2]:
        record, raw = h.read(root/'records/S.json.gz'), h.read(root/'status/S.status.json.gz')
        record['sha256'] = sha
        raw['checksum'] = sha
        raw['review_report']['issues'][0]['evidence'] = [{'page': 1, 'quote': '100000'}]
        write(root, record, raw, 'code' if root == case[0] else 'new-code')
    case[3]['sha256'] = sha
    status['checksum'] = sha
    status['review_report']['issues'][0]['evidence'] = [{'page': 1, 'quote': '100000'}]
    ev['samples'][0]['binding']['pdf_sha256'] = sha
    with fitz.open(pdf) as d:
        def anchor(q): return {'page': 1, 'quote': q, 'bbox': list(d[0].search_for(q)[0])}
        for f, label, raw in zip(ev['samples'][0]['facts'], ['Total','Basic','Project'], ['100000','60000','40000'], strict=True):
            f.update(quote=raw, file_hash=sha)
            f['source_cell'].update(raw_value=raw, raw_unit='yuan', cell=anchor(raw), column=anchor(label), unit_source=anchor('yuan'))
            ev['samples'][0]['relation_contracts'][0]['operand_identities'][f['fact_id']]['source_cell_digest'] = h.digest(f['source_cell'])
    assert check(case)['overall'] == 'PASS'


def test_exact_same_number_at_other_column_is_not_same_cell(case):
    _, _, kit, _, status, ev = case
    pdf = kit/'pdfs/S/source.pdf'
    with fitz.open(pdf) as d:
        d[0].insert_text((400, 95), '6.00')
        d.saveIncr()
    sha = h.sha(pdf)
    for root in case[:2]:
        record, raw = h.read(root/'records/S.json.gz'), h.read(root/'status/S.status.json.gz')
        record['sha256'] = sha
        raw['checksum'] = sha
        write(root, record, raw, 'code' if root == case[0] else 'new-code')
    case[3]['sha256'], status['checksum'] = sha, sha
    ev['samples'][0]['binding']['pdf_sha256'] = sha
    for f in ev['samples'][0]['facts']: f['file_hash'] = sha
    # Same text, value and page, but the top-level location points to a repeated number.
    with fitz.open(pdf) as d:
        ev['samples'][0]['facts'][1]['bbox'] = list(d[0].search_for('6.00')[-1])
    assert check(case)['overall'] == 'FAIL'


@pytest.mark.parametrize('layer', ['manual_review','tips','candidate_findings'])
def test_nonformal_display_id_and_order_changes_are_volatile(case, layer):
    b, _, _, _, s, _ = case
    r, raw = h.read(b/'records/S.json.gz'), h.read(b/'status/S.status.json.gz')
    raw['review_report'][layer] = [{'id': 'old', 'title': 'same observation'}, {'id': 'old2', 'title': 'other observation'}]
    write(b, r, raw, 'code')
    s['review_report'][layer] = [{'id': 'new2', 'title': 'other observation'}, {'id': 'new', 'title': 'same observation'}]
    assert check(case)['overall'] == 'PASS'


def test_added_evidence_on_formal_issue_needs_explicit_object_decision(case):
    case[4]['review_report']['issues'][0]['evidence'].append({'page': 1, 'quote': '4.00'})
    assert check(case)['overall'] == 'FAIL'


@pytest.mark.parametrize('field', ['sample_id','analysis_revision','pdf_sha256'])
def test_formal_content_change_cannot_borrow_new_authorization(case, field):
    case[4]['review_report']['issues'][0]['message'] = 'updated'
    decision = issue_change_decision(case)
    decision["after"] = {**decision["after"], field: "wrong"}
    assert check(case)["overall"] == "FAIL"


def replace_independent_grid(case, printed, raw, unit="wan", claimed_unit=None):
    """原件数值/单位是输入真值，不从被检查的操作数构造。"""
    b, c, kit, record, status, ev = case
    pdf = kit / "pdfs/S/source.pdf"
    with fitz.open() as doc:
        p = doc.new_page()
        p.insert_text((45, 35), "Table")
        p.insert_text((125, 35), unit, fontname="china-s" if unit in ["元", "万元"] else "helv")
        p.insert_text((45, 95), "All")
        for x, label, value in zip(
            [150, 250, 350], ["Total", "Basic", "Project"], printed, strict=True
        ):
            p.insert_text((x, 65), label)
            p.insert_text((x, 95), value)
        doc.save(pdf)
    checksum = h.sha(pdf)
    for root in [b, c]:
        r, s = h.read(root / "records/S.json.gz"), h.read(root / "status/S.status.json.gz")
        r["sha256"] = checksum
        s["checksum"] = checksum
        s["review_report"]["issues"][0]["evidence"] = [{"page": 1, "quote": printed[0]}]
        write(root, r, s, "code" if root == b else "new-code")
    record["sha256"] = checksum
    status["checksum"] = checksum
    status["review_report"]["issues"][0]["evidence"] = [{"page": 1, "quote": printed[0]}]
    bundle = ev["samples"][0]
    bundle["binding"]["pdf_sha256"] = checksum
    with fitz.open(pdf) as doc:

        def anchor(q):
            return {"page": 1, "quote": q, "bbox": list(doc[0].search_for(q)[0])}

        for fact, value, literal, label in zip(
            bundle["facts"], raw, printed, ["Total", "Basic", "Project"], strict=True
        ):
            fact.update(value_wan=value, quote=literal, file_hash=checksum)
            fact["source_cell"].update(
                raw_value=value,
                raw_unit=claimed_unit or unit,
                cell=anchor(literal),
                row=anchor("All"),
                column=anchor(label),
                table={"page": 1, "quote": "Table", "bbox": [35, 15, 450, 120]},
                unit_source=anchor(claimed_unit or unit),
            )
            bundle["relation_contracts"][0]["operand_identities"][fact["fact_id"]][
                "source_cell_digest"
            ] = h.digest(fact["source_cell"])


@pytest.mark.parametrize(
    "printed,raw,expected",
    [
        (["-10.00", "-6.00", "-4.00"], ["-10", "-6", "-4"], "PASS"),
        (["10.00", "-6.00", "4.00"], ["10", "6", "4"], "FAIL"),
        (["10.00", "+6.00", "4.00"], ["10", "6", "4"], "PASS"),
    ],
)
def test_original_amount_sign_is_preserved(case, printed, raw, expected):
    replace_independent_grid(case, printed, raw)
    assert check(case)["overall"] == expected


def test_unit_cannot_be_suffix_of_wan_on_original_page(case):
    replace_independent_grid(case, ["100000", "60000", "40000"], ["10", "6", "4"], "万元", "元")
    # 单独构造正确原始数值+错误换算；不得只靠不同raw_value拒收。
    for f, value in zip(case[-1]["samples"][0]["facts"], ["100000", "60000", "40000"], strict=True):
        f["source_cell"]["raw_value"] = value
        case[-1]["samples"][0]["relation_contracts"][0]["operand_identities"][f["fact_id"]][
            "source_cell_digest"
        ] = h.digest(f["source_cell"])
    assert check(case)["overall"] == "FAIL"


def test_repeated_operand_must_not_replace_required_fact(case):
    replace_independent_grid(case, ["12.00", "6.00", "4.00"], ["12", "6", "4"])
    case[3]["receipts"][0].update(
        operand_fact_ids=["total", "part1", "part1"], required_fact_ids=["total", "part1", "part2"]
    )
    case[4]["review_report"]["check_receipts"] = copy.deepcopy(case[3]["receipts"])
    case[-1]["samples"][0]["relation_contracts"][0]["receipt_digest"] = h.digest(
        case[3]["receipts"][0]
    )
    assert check(case)["overall"] == "FAIL"


def test_independently_declared_role_multiplicity_can_pass(case):
    replace_independent_grid(case, ["12.00", "6.00", "4.00"], ["12", "6", "4"])
    r = case[3]["receipts"][0]
    r.update(
        operand_fact_ids=["total", "part1", "part1"], required_fact_ids=["total", "part1", "part1"]
    )
    case[4]["review_report"]["check_receipts"] = copy.deepcopy(case[3]["receipts"])
    contract = case[-1]["samples"][0]["relation_contracts"][0]
    contract["receipt_digest"] = h.digest(r)
    del contract["operand_identities"]["part2"]
    contract["operand_bindings"] = [
        {"role": "total", "fact_id": "total"},
        {"role": "part", "fact_id": "part1"},
        {"role": "part", "fact_id": "part1"},
    ]
    assert check(case)["overall"] == "PASS"


def test_display_ids_can_swap_between_different_formal_objects(case):
    b, _, _, _, status, _ = case
    rec, raw = h.read(b / "records/S.json.gz"), h.read(b / "status/S.status.json.gz")
    second = {
        **copy.deepcopy(raw["review_report"]["issues"][0]),
        "id": "R-2",
        "title": "another",
        "message": "another",
    }
    raw["review_report"]["issues"].append(second)
    write(b, rec, raw, "code")
    status["review_report"]["issues"] = [
        {**copy.deepcopy(second), "id": "R-1"},
        {**copy.deepcopy(status["review_report"]["issues"][0]), "id": "R-2"},
    ]
    assert check(case)["overall"] == "PASS"


def test_body_and_evidence_share_the_same_public_authorization_key(case):
    b, c, _, _, status, ev = case
    old = h.load_collection(b)["S"]["finding_layers"]["formal"][0]
    new = status["review_report"]["issues"][0]
    new["message"] = "reviewed body"
    new["evidence"].append({"page": 1, "quote": "Project"})
    for action in ["issue_changed", "evidence_replaced"]:
        ev["decisions"].append(
            {
                "action": action,
                "before": h.load_collection(b)["S"]["source_binding"],
                "after": h.load_collection(c)["S"]["source_binding"],
                "key": list(h.formal_identity(old)),
                "old_digest": h.digest(old),
                "new_digest": h.digest(new),
                "reason": "independent exact change",
                "sources": [{"page": 1, "quote": "10.00"}],
            }
        )
    assert check(case)["overall"] == "PASS"


def test_changed_objects_can_swap_display_ids_with_precise_authorizations(case):
    b,c,_,_,status,ev=case
    record,raw=h.read(b/'records/S.json.gz'),h.read(b/'status/S.status.json.gz')
    olda=copy.deepcopy(raw['review_report']['issues'][0])
    oldb={**copy.deepcopy(olda),'id':'R-2','title':'second','message':'second'}
    raw['review_report']['issues']=[olda,oldb]
    write(b,record,raw,'code')
    newa={**copy.deepcopy(olda),'id':'R-2','message':'updated first'}
    newb={**copy.deepcopy(oldb),'id':'R-1','message':'updated second'}
    status['review_report']['issues']=[newb,newa]
    for old,new in [(olda,newa),(oldb,newb)]:
        ev['decisions'].append({'action':'issue_changed','before':h.load_collection(b)['S']['source_binding'],'after':h.load_collection(c)['S']['source_binding'],
            'key':list(h.formal_identity(old)),'old_digest':h.digest(old),'new_digest':h.digest(new),'reason':'exact independent pair','sources':[{'page':1,'quote':'10.00'}]})
    assert check(case)['overall']=='PASS'
