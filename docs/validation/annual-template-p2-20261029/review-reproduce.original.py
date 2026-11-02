"""只读复验：保留原 R1-R4 脚本，补充有效表格标题的折行反例。"""
from pathlib import Path
import hashlib
import json
import os
import sys

IMPLEMENTATION = Path('/Users/zhangxingjin/.codex/worktrees/annual-template-management/GovBudgetChecker')
ORIGINAL = Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/annual_template_independent_review_20261028/reproduce.py')
OUT = Path(__file__).resolve().parent
os.chdir(IMPLEMENTATION)
sys.path.insert(0, str(IMPLEMENTATION))

# 旧脚本末尾断言四个缺陷应存在。保留其原字节，只读取已算出的结果。
original_bytes = ORIGINAL.read_bytes()
assert hashlib.sha256(original_bytes).hexdigest() == '1d3aa544ee576fdb2c9be34f8c037aec3c35909c342687128bcdcd3ea333fd7e'
namespace = {'__name__': 'original_audit_replay'}
try:
    exec(compile(original_bytes, str(ORIGINAL), 'exec'), namespace)
except AssertionError:
    assert 'results' in namespace and len(namespace['results']) == 4
original_results = namespace['results']
assert all(item['reproduced'] is False for item in original_results.values())
(OUT / 'four-p1-replay.json').write_text(json.dumps({
    'original_script_sha256': hashlib.sha256(original_bytes).hexdigest(),
    'original_script_unchanged': True,
    'all_four_old_defects_no_longer_reproduce': True,
    'cases': original_results,
}, ensure_ascii=False, indent=2) + '\n')

from tests.test_annual_template_p1_regressions import table_document
from src.services.template_anchor_checks import additional_anchor_issues, _matching_table_instances
from src.engine.checklist_text_rules import CMM008_TemplateResidue

expected = '上海市普陀区某某事业发展中心2024年度一般公共预算财政拨款基本支出决算表'
rows = []
for label, title in [
    ('three_lines', '上海市普陀区某某事业发展中心\n2024年度一般公共预算\n财政拨款基本支出决算表'),
    ('four_lines', '上海市普陀区某某事业发展中心\n2024年度一般公共预算\n财政拨款基本支出\n决算表'),
]:
    doc = table_document()
    doc.page_texts[1] = title
    snapshot = doc.document_profile.writing_scope['snapshot']
    snapshot['target_year'] = 2024
    snapshot['content']['config']['anchors'][0]['text'] = expected
    doc.parse_artifact.table_fragments[0].title = expected
    doc.parse_artifact.logical_tables[0].title = expected
    instances = _matching_table_instances(doc, expected, [{'page': 2}])
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, 'normative_table')
    rows.append({
        'case': label, 'title': title, 'expected_title': expected,
        'text_equal_after_removing_linebreaks': ''.join(title.split()) == expected,
        'qualified_matching_instances': instances,
        'issues': [item.message for item in issues], 'pending': pending,
    })
assert all(row['text_equal_after_removing_linebreaks'] for row in rows)
assert all(len(row['qualified_matching_instances']) == 1 for row in rows)
assert rows[0]['issues'] == rows[0]['pending'] == []
assert len(rows[1]['issues']) == 1 and rows[1]['pending'] == []
(OUT / 'p2-four-line-title.json').write_text(json.dumps({
    'reproduced': True, 'priority': 'P2',
    'file': str(IMPLEMENTATION / 'src/services/template_anchor_checks.py'),
    'line': 25,
    'reason': '标题最多拼接三行，四行完整标题和对应有效表实例仍被确定性报缺失。',
    'cases': rows,
}, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'four_p1_replay': 'pass', 'new_p2_reproduced': True}, ensure_ascii=False))
