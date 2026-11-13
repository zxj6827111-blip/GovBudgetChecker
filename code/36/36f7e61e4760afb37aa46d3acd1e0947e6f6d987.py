"""Run from the frozen candidate source root; no source or analysis-state writes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import Rule

class PerformanceRule(Rule):
    code = 'V33-250'
    severity = 'error'

base = '绩效自评的2025年度项目53个，'
counts = '绩效评级为“优”的项目50个，其余评级均为0个。'
cases = [
    ('complete_colon', base + '评级结果如下：' + counts, 1, 'fail', '='),
    ('complete_period', base + '评级结果如下。' + counts, 1, 'fail', '='),
    ('complete_semicolon', base + '评级结果如下；' + counts, 1, 'fail', '='),
    ('explicit_period', base + '全部项目评级结果如下。' + counts, 1, 'fail', '='),
    ('partial', base + '其中，绩效评级为“优”的项目50个。', 0, 'pass', '>='),
    ('materials', base + '其中，绩效评级为“优”的项目50个。相关评级材料如下：项目自评表、整改资料。', 0, 'pass', '>='),
]
results = []
for name, text, count, status, operator in cases:
    doc = SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + text],
                          report_kind='final', fiscal_year=2025, organization_name='甲局')
    try:
        issues = check_performance_counts(PerformanceRule(), doc)
    except RuleOutcomeSignal as exc:
        issues = exc.partial_issues
    receipts = getattr(doc, 'fact_receipts', [])
    expected_formula = 'self_evaluation_count ' + operator + ' sum(disclosed distinct rating counts)'
    ok = (len(issues) == count and len(receipts) == 1 and receipts[0]['status'] == status
          and receipts[0]['formula'] == expected_formula)
    results.append({'case': name, 'text': text, 'expected': {'count': count, 'status': status,
                    'formula': expected_formula}, 'actual_count': len(issues), 'receipts': receipts, 'ok': ok})
print(json.dumps(results, ensure_ascii=False, indent=2))
assert all(row['ok'] for row in results), '完整构成的同组数量不能因冒号改句号/分号变成部分列举；材料清单仍须排除'
