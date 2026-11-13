"""Run from the fixed source root. A material heading must exclude operands, not only change a flag."""
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
complete = base + '评级结果如下。绩效评级为“优”的项目50个，其余评级均为0个。'
partial = base + '其中，绩效评级为“优”的项目50个。'
cases = [
    ('complete', complete, 1, 'fail', '='),
    ('complete_with_other_material_example', complete + '相关评级材料如下：其他材料中的示例，绩效评级为“良”的项目3个。', 1, 'fail', '='),
    ('partial', partial, 0, 'pass', '>='),
    ('partial_with_other_material_example', partial + '相关评级材料如下：其他材料中的示例，绩效评级为“良”的项目10个。', 0, 'pass', '>='),
]
results = []
for name, text, expected_count, status, operator in cases:
    doc = SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + text],
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
    results.append({'case': name, 'text': text, 'expected': {'issues': expected_count, 'status': status, 'formula': formula},
                    'actual_count': len(issues), 'signal': signal, 'receipts': receipts,
                    'locations': [i.location for i in issues], 'ok': ok})
print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
assert all(r['ok'] for r in results), '材料标题后的其他材料示例数量必须排除出本组操作数，不能凑平真矛盾或制造部分列举误报'
