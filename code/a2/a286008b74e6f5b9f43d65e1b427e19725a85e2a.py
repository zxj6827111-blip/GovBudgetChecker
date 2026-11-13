"""冻结裁定回归：误报必须消失、已确认原件候选必须保留；待复核不强作真值。"""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
FROZEN = ROOT / 'outputs/shanghai_2024_new9_acceptance_20261016'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(value):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))
    return re.sub(r'^(?:[一二三四五六七八九十百零]+|\d+)[、.．]', '', text)


def rule(issue):
    return issue.get('rule_id') or issue.get('rule')


def pages(issue):
    location = issue.get('location') or {}
    primary = location.get('page')
    if isinstance(primary, int):
        return {primary}
    return {p for p in location.get('pages', []) if isinstance(p, int)}


def same_issue(expected, actual):
    if expected['rule_id'] != rule(actual):
        return False
    left, right = compact(expected['message']), compact(actual.get('message'))
    if not (left == right or (min(len(left), len(right)) >= 80 and (left.startswith(right) or right.startswith(left)))):
        return False
    left_pages, right_pages = pages(expected), pages(actual)
    return not left_pages or not right_pages or bool(left_pages & right_pages)


def known_false_candidate(expected, actual):
    if expected['rule_id'] != rule(actual):
        return False
    message = compact(actual.get('message'))
    if expected['doc_id'] == 'SH2024N9_02' and expected['rule_id'] == 'V33-246':
        return True
    if expected['doc_id'] == 'SH2024N9_04' and expected['rule_id'] == 'V33-106':
        return True
    if expected['doc_id'] == 'SH2024N9_03' and expected['rule_id'] == 'V33-234':
        return ('未说明原因' in message and ('购房补贴' in message or ('29.98' in message and '18.69' in message)))
    return same_issue(expected, actual)


def review_layer(expected_rows, actual_by_doc, layer):
    records = []
    consumed = set()
    for index, expected in enumerate(expected_rows):
        doc_id = expected['doc_id']
        if doc_id not in actual_by_doc:
            records.append(dict(candidate_id=expected.get('candidate_id', f'{layer}:{index}'), doc_id=doc_id,
                                decision=expected['decision'], rule_id=expected['rule_id'], result='not_run', expected=expected))
            continue
        actual = actual_by_doc[doc_id]
        false = expected['decision'] == 'false_positive'
        matching = [i for i, issue in enumerate(actual) if (known_false_candidate(expected, issue) if false else same_issue(expected, issue))]
        if not false:
            matching = [i for i in matching if (doc_id, i) not in consumed]
        if matching and not false:
            consumed.add((doc_id, matching[0]))
        decision = expected['decision']
        if decision == 'false_positive':
            result = 'removed' if not matching else 'false_positive_retained'
        elif decision == 'confirmed_source_issue':
            result = 'retained' if matching else 'source_candidate_missing'
        else:
            result = 'manual_still_candidate' if matching else 'manual_no_candidate'
        records.append(dict(candidate_id=expected.get('candidate_id', f'{layer}:{index}'), doc_id=doc_id,
                            decision=decision, rule_id=expected['rule_id'], result=result,
                            expected=expected, matched_actual=[actual[i] for i in matching]))
    failures = [r for r in records if r['result'] in ('false_positive_retained', 'source_candidate_missing')]
    summary = dict(layer=layer, expected_candidates=len(expected_rows), executed_documents=sorted(actual_by_doc),
                   decisions=dict(Counter(r['decision'] for r in expected_rows)), results=dict(Counter(r['result'] for r in records)),
                   failures=failures, records=records)
    return summary


def load_api_reports(mapping_path=None, api_root=None):
    inventory = read(FROZEN / 'inventory.json')
    by_hash = {doc['sha256']: doc['doc_id'] for doc in inventory}
    mappings = {}
    if mapping_path:
        mappings = {key: Path(value) for key, value in read(mapping_path).items()}
    elif api_root:
        for path in api_root.rglob('report.json'):
            source = path.parent / 'source.pdf'
            if not source.is_file():
                continue
            doc_id = by_hash.get(sha(source))
            if not doc_id:
                continue
            assert doc_id not in mappings, f'重复报告需用--api-map明确指定最终报告：{doc_id}'
            mappings[doc_id] = path
    result = {}
    proof = []
    for doc_id, path in mappings.items():
        assert doc_id in {d['doc_id'] for d in inventory}
        report = read(path)
        review = report.get('review_report') or report
        issues = review.get('issues', [])
        assert isinstance(issues, list)
        result[doc_id] = [issue for issue in issues if str(issue.get('severity')).lower() != 'info']
        source = path.parent / 'source.pdf'
        if source.is_file():
            assert by_hash.get(sha(source)) == doc_id, (doc_id, path)
        proof.append(dict(doc_id=doc_id, report=str(path), sha256=sha(path), source_pdf_hash_verified=source.is_file()))
    return result, proof


def main(phase, output, mapping_path=None, api_root=None):
    expected_engine = read(FROZEN / 'engine_candidate_review.json')
    expected_api = read(FROZEN / 'api_candidate_and_coverage_verification.json')['issue_review']
    assert len(expected_engine) == 23 and len(expected_api) == 16
    actual_engine = {}
    for doc in read(phase / 'inventory.json'):
        path = phase / 'documents' / doc['doc_id'] / 'engine_issues.json'
        if path.is_file():
            actual_engine[doc['doc_id']] = [issue for issue in read(path) if str(issue.get('severity')).lower() != 'info']
    engine = review_layer(expected_engine, actual_engine, 'public_rule_layer')
    api_actual, api_proof = load_api_reports(mapping_path, api_root)
    api = review_layer(expected_api, api_actual, 'final_api') if mapping_path or api_root else None
    issues = read(FROZEN / 'confirmed_source_issues.json')
    assert len(issues) == 15
    # 原件清单不等同系统必须产生15个候选；只要求冻结的14条有效候选不丢，并保留全部15项来源真值。
    source_path = FROZEN / 'confirmed_source_issues.json'
    source_digest = sha(source_path)
    protection = read(BASE / 'immutable_before.json')
    expected_digest = protection[str(source_path.relative_to(ROOT))]
    assert source_digest == expected_digest, '原件15项真值被修改'
    source_records = []
    by_doc = {doc['doc_id']: doc for doc in read(FROZEN / 'inventory.json')}
    for issue in issues:
        doc = by_doc[issue['doc_id']]
        source = Path(doc['source_pdf'])
        assert sha(source) == doc['sha256']
        evidence = issue.get('evidence') or []
        assert all(item.get('sha256') == doc['sha256'] for item in evidence)
        source_records.append(dict(issue_id=issue['issue_id'], doc_id=issue['doc_id'], page=issue['page'], title=issue['title'],
                                   source_pdf_hash_unchanged=True, original_issue_truth_retained=True, source_evidence_records=len(evidence)))
    failures = len(engine['failures']) + (len(api['failures']) if api else 0)
    incomplete = engine['results'].get('not_run', 0) + (api['results'].get('not_run', 0) if api else 0)
    report = dict(status='failed' if failures else ('partial' if incomplete else 'passed'),
                  tested_outputs_used_as_source_truth=False, current_phase=str(phase), engine=engine, final_api=api,
                  api_report_proof=api_proof, source_issues=dict(count=15, truth_sha256=source_digest,
                  unchanged_from_before=True, all_original_pdfs_unchanged=True, records=source_records),
                  manual_review_policy='Six rule-layer and one final API boundary cases remain undecided source review; presence or absence alone is not a pass/fail verdict.',
                  limitation='Only originally confirmed 14 system candidates are detection regressions. All 15 independent source issues are preserved; this does not claim all 15 must produce automated issues.')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(status=report['status'], engine=engine['results'], final_api=api['results'] if api else None, source_issues=15), ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase')
    parser.add_argument('--api-root')
    parser.add_argument('--api-map')
    parser.add_argument('--output', default='rule_candidate_verification.json')
    args = parser.parse_args()
    phase = Path(args.phase)
    if not phase.is_absolute():
        phase = BASE / phase
    output = Path(args.output)
    if not output.is_absolute():
        output = BASE / output
    assert BASE in output.resolve().parents
    report = main(phase, output, Path(args.api_map) if args.api_map else None, Path(args.api_root) if args.api_root else None)
    raise SystemExit(1 if report['status'] == 'failed' else 0)
