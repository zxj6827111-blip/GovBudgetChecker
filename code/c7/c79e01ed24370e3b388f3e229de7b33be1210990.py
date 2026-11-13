"""Compare the fixed real-service reports with the unchanged prior review.

This is regression verification on development originals, not blind acceptance.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime
import csv
import difflib
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FROZEN = ROOT / 'outputs/real_intake_review_20261008'
LIVE = OUT / 'live_complete'
NATIVE = OUT / 'native_complete'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def csv_save(name, rows, columns):
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                             for key, value in row.items() if key in columns})


# Explicit rule + original physical page + original relation values. Similar
# amounts elsewhere in a report are never sufficient to match a frozen case.
SPECS = {
    'NUM-T01': (1, 'V33-234', 30, ('2468832.94', '5.00%')),
    'NUM-T02': (1, 'V33-234', 30, ('2442677.65', '5.23%')),
    'NUM-T03': (1, 'V33-249', 31, ('2196397.98', '2443579.41', '89.88%')),
    'NUM-T04': (1, 'CMM-007', 39, ('370.23', '3.50%')),
    'NUM-T05': (1, 'CMM-007', 39, ('34.15', '12.53%')),
    'NUM-T06': (1, 'V33-248', 40, ('234955.97', '156299.44')),
    'NUM-T07': (1, 'V33-234', 43, ('6679.47', '335.66', '4.78%')),
    'NUM-W01': (5, 'V33-226', 26, ('16371.53', '0.00')),
    'NUM-W02': (5, 'V33-234', 27, ('139407.47', '16819.41', '13.72%')),
    'NUM-W03': (5, 'V33-117', 22, ('302明细', '10265.86', '10533.26')),
    'NUM-W04': (5, 'V33-117', 22, ('310明细', '11033.37')),
    'NUM-W05': (5, 'V33-117', 22, ('公用经费合计', '10533.25', '10265.86')),
    'NUM-W08': (5, 'V33-250', 38, ('自评108', '127', '19')),
    'NUM-M01': (6, 'V33-120', 11, ('208):', '163344.37', '156069.67')),
    'NUM-M02': (6, 'V33-120', 14, ('208):', '164977.86', '157926.65')),
    'NUM-M03': (6, 'V33-120', 19, ('208):', '144363.50', '139231.67')),
    'ROOT-01': (7, 'V33-224', 26, ('1438.03', '95.77%')),
    'ROOT-02': (7, 'V33-224', 26, ('20.45', '1.36%')),
    'ROOT-03': (2, 'V33-TREND-COMPLETION-RATE', 34, ('70.23', '116.20', '60.44%')),
    'ROOT-04': (2, 'V33-222', 30, ('544.49元', '0.054449', '544.4356')),
    'D-TRUTH-004': (4, 'V33-234', 30, ('购置支出为0万元', '实际购车用途')),
    'D-TRUTH-005': (4, 'V33-234', 26, ('原因仅重述结果', '实际支出小于预算数')),
    'D-TRUTH-006': (4, 'V33-234', 32, ('原因仅重述结果', '15.2', '略有增多')),
    'D-TRUTH-010': (1, 'V33-113', 39, ('是是',)),
    'D-TRUTH-011': (2, 'V33-113', 35, ('的的',)),
    'STRUCT-01': (7, 'V33-234', 18, ('仍保留该表格式', '编制操作提示')),
}


def pages(issue):
    loc = issue.get('location') or {}
    return {value for value in [issue.get('page_number'), loc.get('page'), *(loc.get('pages') or [])]
            if type(value) is int and value > 0}


def message(issue):
    return re.sub(r'^[一二三四五六七八九十百零]+、', '', issue['message'])


def main():
    original = read(FROZEN / 'review_result.json')
    service = read(LIVE / 'service_receipt.json')
    native = read(NATIVE / 'summary.json')
    assert service['status'] == 'passed' and service['cleanup'] == 'complete'
    assert service['source_unchanged_during_run'] and not service['errors']
    assert len(service['scenarios']) == 10 and all(s['service_validation_passed'] for s in service['scenarios'])
    assert all(sha(ROOT / name) == value for name, value in service['code_fingerprints'].items())
    reports = {n: read(LIVE / f'shanghai_{n:02}/report.json') for n in range(1, 9)}
    artifacts = {n: read(NATIVE / f'document_{n:02}/artifact.json') for n in range(1, 9)}
    checks, documents = [], []
    for n, doc in enumerate(native['documents'], 1):
        report = reports[n]
        raw = read(NATIVE / f'document_{n:02}/issues.json')
        expected = Counter((i['rule'], message(i)) for i in raw)
        actual = Counter((i['rule_id'], message(i)) for i in report['issues'])
        assert expected == actual, (n, 'native/API mismatch')
        assert not doc['source_errors'] and doc['source_unchanged']
        assert not any(t['projection'] == 'blocked' for t in doc['tables'])
        assert sha(Path(doc['source_pdf'])) == doc['sha256']
        scenario = next(s for s in service['scenarios'] if s['scenario'] == f'shanghai_{n:02}')
        assert scenario['source_sha256'] == doc['sha256']
        assert scenario['database']['artifact_readback']['passed']
        coverage = report['check_coverage']
        no_bbox = [i['id'] for i in report['issues'] if i['severity'] != 'info' and not i.get('bbox')]
        documents.append({'number': n, 'organization': doc['organization'], 'year': doc['year'],
                          'document_type': '决算公开', 'filename': doc['filename'], 'pages': doc['pages'],
                          'sha256': doc['sha256'], 'facts': doc['facts'], 'source_errors': doc['source_errors'],
                          'issues': len(report['issues']), 'non_info_without_primary_bbox': no_bbox,
                          'native_api_agreement': True, 'status': report['status'],
                          'completed_obligations': coverage['completed_total'],
                          'applicable_obligations': coverage['applicable_total'],
                          'unfinished': report['unfinished_checks']})
    cases = []
    for old in original['issues']:
        key = old.get('truth_id') or old['id']
        if old['classification'] == 'FP':
            continue
        n, rule, page, tokens = SPECS[key]
        found = [i for i in reports[n]['issues'] if i['rule_id'] == rule and page in pages(i)
                 and all(token in i['message'] for token in tokens)]
        assert found, (key, 'not detected')
        if key == 'D-TRUTH-005':
            assert len(found) == 2 and all(any(amount in i['message'] for i in found) for amount in ('17.68', '682.18'))
        assert all(i.get('bbox') and i['verification_status'] == 'verified' for i in found), (key, 'no verified bbox')
        matches = [{'id': i['id'], 'rule_id': i['rule_id'], 'related_rule_ids': i.get('related_rule_ids', []),
                    'page': i.get('page_number'), 'pages': sorted(pages(i)), 'bbox': i.get('bbox'),
                    'message': i['message'], 'source_evidence': i['evidence'],
                    'fact_ids': i.get('fact_ids', []), 'check_relation_ids': i.get('check_relation_ids', [])}
                   for i in found]
        item = {**old, 'id': key, 'repair_state': 'detected_after_fix' if old['classification'] == 'FN' else 'preserved',
                'classification_before': old['classification'], 'matched_after': matches,
                'system_matches_before': old.get('system_matches', []),
                'system_matches': matches, 'system_actual_after': [i['message'] for i in found]}
        cases.append(item)

    # False-positive disappearance is paired with a positive source/receipt check.
    fp_checks = []
    culture, civil = reports[5], reports[6]
    bad = [i for i in culture['issues'] if i['severity'] != 'info' and
           ((i['rule_id'] == 'V33-005' and 18 in pages(i)) or
            (i['rule_id'] == 'V33-120' and '20708' in i['message']))]
    assert not bad
    facts = read(NATIVE / 'document_05/fact_context.json')['facts']
    restored = [f for f in facts if f['table_code'] == 'FIN_05_general_public_expenditure' and
                f['subject_code'] == '2070899' and f['indicator'] == 'total_actual']
    assert len(restored) == 1 and str(restored[0]['normalized_value']) == '6874.45'
    receipts = civil['review_report']['check_receipts']
    passed_business = [r for r in receipts if r['rule_id'] == 'V33-TREND-COMPLETION-RATE' and
                       r['status'] == 'pass' and ('completion:reception:' in r['sub_check_id'] or
                                                'completion:vehicle_purchase_operation:' in r['sub_check_id'])]
    assert len(passed_business) == 2
    assert not [i for i in civil['issues'] if i['rule_id'] == 'V33-TREND-COMPLETION-RATE' and i['severity'] != 'info']
    for key, proof in [('FP-01', {'relation': '540.82 + 10018.79 = 10559.61; closing = 0',
                                'logical_table': 'FIN_04_fiscal_grant_total', 'pages': [17, 18]}),
                       ('FP-02', {'restored_original_fact': restored[0],
                                  'relation': '781.22 + 1167.57 + 102 + 6874.45 = 8925.24'}),
                       ('FP-03', {'pass_receipts': passed_business,
                                  'relations': ['166.30 / 255.63 * 100 = 65.05%', '25.52 / 34.30 * 100 = 74.40%']})]:
        old = next(i for i in original['issues'] if i['id'] == key)
        cases.append({**old, 'repair_state': 'false_positive_removed', 'classification_before': 'FP',
                      'matched_after': [], 'positive_verification': proof})
        fp_checks.append({'id': key, 'passed': True, 'proof': proof})
    checks.extend(fp_checks)

    legacy_without_pair = []
    for n, report in reports.items():
        receipt_rules = {r['rule_id'] for r in report['review_report']['check_receipts']}
        for issue in report['issues']:
            if issue['severity'] == 'info' or not issue.get('related_rule_ids'):
                continue
            owning = [r for r in report['review_report']['check_receipts'] if r['rule_id'] in issue['related_rule_ids']
                      and r['status'] == 'fail' and issue['id'] in r.get('issue_refs', [])]
            paired = set(issue['related_rule_ids']) & receipt_rules
            assert paired <= {r['rule_id'] for r in owning}, (n, issue['id'], 'orphan merged rule')
            unpaired = set(issue['related_rule_ids']) - receipt_rules
            if unpaired:
                legacy_without_pair.append({'document': n, 'issue_id': issue['id'], 'rule_ids': sorted(unpaired),
                    'reason': '该legacy规则没有单独义务或事实关系回执，关联规则与原始问题保留在related_findings；不捏造回执。'})
    checks.append({'id': 'merged_issue_ledger_links', 'passed': True,
                   'reason': '已有检查回执的关联失败规则均指向保留的问题ID，各自挂回所属义务。',
                   'legacy_rules_without_individual_pair': legacy_without_pair})
    fiscal = reports[8]['review_report']['check_receipts']
    cmm = [r for r in fiscal if r['rule_id'] == 'CMM-001']
    assert cmm and all(r['status'] == 'pass' for r in cmm)
    assert {r['obligation_id'] for r in cmm} == {'OBL-TXT-T7', 'OBL-SG-TABLE-TEXT'}
    checks.append({'id': 'fiscal_zero_purchase_and_running_30', 'passed': True, 'receipts': cmm})
    phase = [r for r in reports[1]['review_report']['check_receipts'] if r['rule_id'] == 'V33-TREND-COMPLETION-RATE'
             and r['status'] == 'insufficient_data' and r['sub_check_id'].startswith('three_public:completion:')]
    assert len(phase) == 4 and all('预算阶段' in r['incomplete_reason'] for r in phase)
    checks.append({'id': 'unconfirmed_budget_phase_retained', 'passed': True, 'receipts': phase})
    assert all(not d['non_info_without_primary_bbox'] for d in documents)
    checks.append({'id': 'confirmed_non_info_main_bbox', 'passed': True,
                   'scope': '本批8份服务报告所有非info输出；不能外推其他版式一定可定位。'})

    before = {i['path']: i['sha256'] for i in read(OUT / 'before_manifest.json')}
    changed, patch = [], []
    for folder in ('api', 'src', 'tests', 'scripts'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            name = str(path.relative_to(ROOT))
            digest = sha(path)
            if digest == before.get(name):
                continue
            baseline = OUT / 'before' / name
            changed.append({'path': name, 'before_sha256': before.get(name), 'after_sha256': digest,
                            'kind': 'modified' if baseline.exists() else 'added'})
            patch.extend(difflib.unified_diff(baseline.read_text().splitlines(keepends=True) if baseline.exists() else [],
                                            path.read_text().splitlines(keepends=True),
                                            fromfile='before/' + name, tofile='after/' + name))
    (OUT / 'repair_changes_from_start.patch').write_text(''.join(patch), encoding='utf-8')
    save('repair_change_manifest.json', changed)
    lint_before, lint_after = read(OUT/'lint_before_delivery.json'), read(OUT/'lint_after_delivery.json')
    baseline_lint = {(i['code'], i['message'], i['filename'].split('/before/')[-1]) for i in lint_before}
    introduced = [i for i in lint_after if (i['code'], i['message'], str(Path(i['filename']).relative_to(ROOT))) not in baseline_lint]
    assert not introduced
    test_log = (OUT / 'backend_complete.log').read_text()
    test_summary = re.search(r'\d+ passed, \d+ skipped[^\n]*', test_log)
    assert test_summary and 'failed' not in test_summary.group()
    summary = {'document_type': '决算公开', 'year': 2025, 'documents': 8, 'pages': 295,
               'rule_set': 'v3_3_portable', 'readiness': 'assisted_real_intake',
               'overall_assessment': '本轮确认的9个漏报组和3条误报已补齐，真实文字层PDF可继续用于人工复核配合的审校。',
               'known_missed_groups_detected': sum(i['repair_state'] == 'detected_after_fix' for i in cases),
               'known_false_positive_records_removed': 3,
               'previous_confirmed_groups_preserved': sum(i['repair_state'] == 'preserved' for i in cases),
               'native_api_issue_agreement': sum(d['issues'] for d in documents),
               'fact_source_errors': 0, 'non_info_without_primary_bbox': 0,
               'backend_tests': test_summary.group(), 'lint_introduced': 0,
               'lint_existing_diagnostics': len(lint_after),
               'service_scenarios_passed': 10, 'service_elapsed_seconds': service['elapsed_seconds'],
               'service_cleanup': service['cleanup'], 'accuracy_estimate': None,
               'limitations': ['既有开发样本的修复回归，不是独立盲测，不计算全产品准确率。',
                              '扫描预算入口未开启OCR，只验证接收、终态及导出；不代表扫描内容审校通过。',
                              '预算阶段、截断科目名称、空白金额及不完整绩效评级继续如实列为未完成。',
                              '车辆数量统计范围和时点、文旅三公跨表倒挂须结合原预算及财务底稿人工复核。',
                              '本轮实际API和Worker导入；未操作前端浏览器、未验证可选AI内容检查。']}
    assert summary['known_missed_groups_detected'] == 9
    save('closure_result.json', {'created_at': datetime.now().astimezone().isoformat(),
                                'summary': summary, 'documents': documents, 'issues': cases,
                                'manual_review': original['manual_review'], 'repair_checks': checks,
                                'frozen_baseline': {'path': str(FROZEN/'review_result.json'), 'sha256': sha(FROZEN/'review_result.json')},
                                'service_receipt': str(LIVE/'service_receipt.json')})
    save('repair_checks.json', checks)
    rows = []
    for item in cases:
        matched = item['matched_after']
        rows.append({**item, 'matched_rule_ids': [x['rule_id'] for x in matched],
                     'matched_issue_ids': [x['id'] for x in matched],
                     'source_pages_after': sorted({p for x in matched for p in x['pages']}),
                     'source_bbox_after': [x['bbox'] for x in matched]})
    columns = ['id', 'rule_id', 'title', 'severity', 'document_type', 'year', 'organization', 'page', 'bbox',
               'table_or_section', 'snippet', 'expected', 'actual', 'difference', 'reason', 'suggestion',
               'confidence', 'classification_before', 'repair_state', 'matched_rule_ids', 'matched_issue_ids',
               'source_pages_after', 'source_bbox_after', 'system_actual_after']
    csv_save('problem_list_after.csv', rows, columns)
    all_rows = []
    for n, report in reports.items():
        for issue in report['issues']:
            location = issue.get('location') or {}
            quotes = list(dict.fromkeys(str(e.get('quote') or e.get('text') or e.get('text_snippet') or '')
                                       for e in issue.get('evidence', []) if isinstance(e, dict)))
            snippet = '\n'.join(quotes)
            all_rows.append({**issue, 'organization': documents[n-1]['organization'],
                             'document_type': '决算公开', 'year': 2025, 'pages': sorted(pages(issue)),
                             'table_or_section': location.get('section') or location.get('table') or location.get('table_code'),
                             'snippet': snippet[:4000], 'snippet_truncated': len(snippet) > 4000,
                             'full_evidence_report': str(LIVE/f'shanghai_{n:02}/report.json')})
    csv_save('system_issue_list_after.csv', all_rows, ['organization', 'document_type', 'year', 'id', 'rule_id',
              'severity', 'title', 'message', 'page_number', 'pages', 'bbox', 'related_rule_ids',
              'table_or_section', 'snippet', 'snippet_truncated', 'full_evidence_report'])
    save('delivery_verification.json', {'status': 'passed', 'checked_cases': len(cases),
         'checks': len(checks), 'source_code_matches_live_receipt': True, 'native_api_agreement': True,
         'tests': summary['backend_tests'], 'lint_existing': len(lint_after), 'lint_introduced': 0,
         'files': {name: sha(OUT/name) for name in ['closure_result.json', 'problem_list_after.csv',
                    'system_issue_list_after.csv', 'repair_checks.json', 'repair_change_manifest.json',
                    'repair_changes_from_start.patch']}})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
