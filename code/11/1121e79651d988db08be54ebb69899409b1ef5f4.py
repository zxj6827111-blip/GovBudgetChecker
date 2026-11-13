"""An archive predicate must not borrow a following rating predicate to become a material title."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import Rule
from src.engine.rules_v33 import R33250_PerformanceCounts
from src.engine.pipeline import run_rules_with_outcomes

class PerformanceRule(Rule):
    code = 'V33-250'
    severity = 'error'

prefix = '绩效自评的2025年度项目53个，'
counts = '绩效评级为“优”的项目50个，其余评级均为0个。'
cases = [
    ('base', prefix + '评级结果如下。' + counts, 1, 'fail', '='),
    ('archive_period', prefix + '相关材料已归档。评级结果如下。' + counts, 1, 'fail', '='),
    ('archive_comma', prefix + '相关材料已归档，评级结果如下。' + counts, 1, 'fail', '='),
    ('archive_parentheses', prefix + '（相关材料已归档）评级结果如下。' + counts, 1, 'fail', '='),
    ('archive_semicolon', prefix + '相关材料已归档；评级结果如下。' + counts, 1, 'fail', '='),
    ('partial', prefix + '其中，绩效评级为“优”的项目50个。', 0, 'pass', '>='),
]
results = []
for name, body, expected_count, status, operator in cases:
    doc = SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + body],
                          report_kind='final', fiscal_year=2025, organization_name='甲局')
    try:
        issues = check_performance_counts(PerformanceRule(), doc)
        signal = None
    except RuleOutcomeSignal as exc:
        issues = list(exc.partial_issues)
        signal = str(exc)
    receipts = getattr(doc, 'fact_receipts', [])
    formula = 'self_evaluation_count ' + operator + ' sum(disclosed distinct rating counts)'
    ok = (signal is None and len(issues) == expected_count and len(receipts) == 1
          and receipts[0]['status'] == status and receipts[0]['formula'] == formula)
    runner_doc = SimpleNamespace(path='甲局2025年度部门决算.pdf', page_tables=[[]],
                                page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + body],
                                report_kind='final', fiscal_year=2025, organization_name='甲局')
    runner_issues, runner_outcomes = run_rules_with_outcomes(
        runner_doc, report_kind='final', rules=[R33250_PerformanceCounts()])
    runner_values = [value.to_dict() for value in runner_outcomes]
    ok = (ok and len(runner_issues) == expected_count and len(runner_values) == 1
          and runner_values[0]['status'] == status)
    results.append({'case': name, 'text': body, 'expected': {'count': expected_count, 'status': status, 'formula': formula},
                    'actual_count': len(issues), 'receipts': receipts, 'signal': signal,
                    'actual_rule_runner': {'issues': len(runner_issues), 'outcomes': runner_values,
                                           'fact_receipts': getattr(runner_doc, 'fact_receipts', [])}, 'ok': ok})
print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
assert all(x['ok'] for x in results), '归档句不是材料清单标题，不能截掉后续同组完整评级数量并产生零问题零回执'
