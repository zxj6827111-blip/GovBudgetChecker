"""核对真实API中的已裁定规则候选，同时保留业务未完成分母。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
inventory = json.loads((BASE / 'new10/inventory.json').read_text())
by_hash = {doc['sha256']: doc for doc in inventory}
reports = {}
for path in (BASE / 'service/service_complete').glob('*/report.json'):
    source = path.parent / 'source.pdf'
    if not source.exists():
        continue
    doc = by_hash.get(hashlib.sha256(source.read_bytes()).hexdigest())
    if doc:
        reports[doc['doc_id']] = json.loads(path.read_text())
assert len(reports) == 10


def signature(issue):
    return (issue.get('rule', issue.get('rule_id')), re.sub(r'^[一二三四五六七八九十百]+、', '', issue['message']))


checks = []
for candidate in json.loads((ROOT / 'outputs/shanghai_2024_new10_acceptance_20261015/engine_candidate_review.json').read_text()):
    count = sum(signature(candidate) == signature(issue) for issue in reports[candidate['doc_id']]['issues'])
    expected = 0 if candidate['decision'] == 'false_positive' else 1
    checks.append(dict(doc_id=candidate['doc_id'], decision=candidate['decision'],
                       engine_issue_id=candidate['engine_issue_id'], api_matches=count, expected_matches=expected,
                       passed=count == expected))
assert all(check['passed'] for check in checks)
totals = Counter()
by_reason = Counter()
per_doc = []
for doc_id, report in sorted(reports.items()):
    coverage = report['check_coverage']
    for field in ['applicable_total', 'completed_total', 'unresolved_total', 'blocking_total']:
        totals[field] += coverage[field]
    by_reason.update(coverage['by_reason'])
    per_doc.append(dict(doc_id=doc_id, filename=next(doc['filename'] for doc in inventory if doc['doc_id'] == doc_id),
                        api_status=report['status'], candidate_issues=len(report['issues']),
                        check_coverage={field: coverage[field] for field in ['applicable_total', 'completed_total', 'unresolved_total', 'blocking_total', 'by_reason']},
                        unfinished_checks=report['unfinished_checks']))
result = dict(status='passed', checked_api_documents=10,
              false_positive_cases_removed=sum(check['decision'] == 'false_positive' for check in checks),
              correct_candidates_retained=sum(check['decision'] == 'confirmed_source_issue' for check in checks),
              candidate_checks=checks, api_terminal_statuses=dict(Counter(r['api_status'] for r in per_doc)),
              business_check_totals=dict(totals), unfinished_by_reason=dict(by_reason),
              interpretation='服务技术链路与核心读取通过，不等同于全部业务自动审校完成；未完成项继续保留在适用检查分母。',
              documents=per_doc)
(BASE / 'service/api_rule_and_coverage_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps({key: value for key, value in result.items() if key not in ['candidate_checks', 'documents']}, ensure_ascii=False))
