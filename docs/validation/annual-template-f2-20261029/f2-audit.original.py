"""P2 独立复验：冻结原审核脚本，验证修复状态及编号/引用组合边界。"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
IMPLEMENTATION = Path('/Users/zhangxingjin/.codex/worktrees/annual-template-management/GovBudgetChecker')
OUT = Path(__file__).resolve().parent
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda name, value: (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
os.chdir(IMPLEMENTATION)
sys.path.insert(0, str(IMPLEMENTATION))

# 原脚本根据 __file__ 选择输出位置；使用新证据目录中的原样副本，保护旧证据。
source_path = ROOT / 'outputs/annual_template_final_acceptance_20261028/reproduce_final.py'
source = source_path.read_bytes()
assert sha(source) == '100a3b6931e81954ee11c98186c9aeff3f6a1a2c97e5b71d33f9fe08d30d4f74'
copy_path = OUT / 'previous-review.original.py'
copy_path.write_bytes(source)
namespace = {'__name__': 'independent_p2_replay', '__file__': str(copy_path)}
old_bug_assertion_failed = False
try:
    exec(compile(source, str(copy_path), 'exec'), namespace)
except AssertionError:
    old_bug_assertion_failed = True
assert old_bug_assertion_failed
assert all(not item['reproduced'] for item in namespace['original_results'].values())
rows = namespace['rows']
assert len(rows) == 2
assert all(row['text_equal_after_removing_linebreaks'] for row in rows)
assert all(len(row['qualified_matching_instances']) == 1 for row in rows)
assert all(row['issues'] == row['pending'] == [] for row in rows)
write('original-p2-replay.json', {
    'original_script_sha256': sha(source), 'original_script_unchanged': True,
    'old_bug_presence_assertion_failed_as_expected': old_bug_assertion_failed,
    'original_four_p1_still_fixed': True, 'original_four_line_p2_fixed': True, 'cases': rows,
})

from src.services import template_anchor_checks as current
from src.engine.checklist_text_rules import CMM008_TemplateResidue
from tests.test_material_writing_rules import document
from tests.test_template_anchor_checks import fixture_resolution

# 对照旧源文件时只在当前进程执行，不 checkout，也不更改任何 worktree 文件。
old_source = subprocess.check_output(['git', 'show', 'bc8da8d7:src/services/template_anchor_checks.py'], text=True)
old = {}
exec(compile(old_source, 'baseline_bc8da8d7_template_anchor_checks.py', 'exec'), old)
expected = '新增政策执行情况说明'
cases = []
inputs = [
    ('normal_numbered_four_lines', '一、\n新增\n政策执行\n情况\n说明'),
    ('quoted_without_number', '引用：\n新增\n政策执行\n情况\n说明'),
    ('quoted_numbered_four_lines', '引用：\n一、\n新增\n政策执行\n情况\n说明'),
    ('negative_numbered_four_lines', '本报告尚未提供\n一、\n新增\n政策执行\n情况\n说明'),
    ('example_numbered_four_lines', '例如：\n一、\n新增\n政策执行\n情况\n说明'),
    ('quoted_numbered_three_lines', '引用：\n一、\n新增政策\n执行情况\n说明'),
]
for label, text in inputs:
    anchor = {'kind': 'normative_section', 'scope': 'all', 'text': expected, 'basis': 'isolated independent audit fixture'}
    doc = document(['2025年度决算', text], fixture_resolution([anchor]))
    row = {'case': label, 'text': text, 'expected_title': expected}
    for version, ns in [('baseline', old), ('current', current.__dict__)]:
        issues, pending = ns['additional_anchor_issues'](CMM008_TemplateResidue(), doc, 'normative_section')
        row[version] = {'headings': ns['_headings'](doc, expected), 'issues': [i.message for i in issues], 'pending': pending}
    cases.append(row)
by_name = {row['case']: row for row in cases}
assert not by_name['normal_numbered_four_lines']['current']['issues']
assert by_name['quoted_without_number']['current']['headings'] == []
for label in ['quoted_numbered_four_lines', 'negative_numbered_four_lines', 'example_numbered_four_lines']:
    assert by_name[label]['baseline']['headings'] == []
    assert by_name[label]['baseline']['issues']
    assert by_name[label]['current']['headings']
    assert by_name[label]['current']['issues'] == by_name[label]['current']['pending'] == []
assert by_name['quoted_numbered_three_lines']['baseline']['headings']
write('numbered-context-bypass.json', {
    'reproduced': True, 'priority': 'P2',
    'file': str(IMPLEMENTATION / 'src/services/template_anchor_checks.py'), 'lines': [21, 22],
    'baseline_source_sha256': sha(old_source.encode()),
    'classification': '旧有编号遮挡语境盲区，本次取消行数限制后扩展到四行及更多行；不是原四行误报未修复。',
    'expected': '引用、否定或举例中列出的标题不能完成正式章节要求。',
    'actual': '独占行编号挡住紧邻语境检查，下一行作为新的候选重入并通过。',
    'cases': cases,
})
print(json.dumps({'original_p2_fixed': True, 'four_p1_still_fixed': True, 'numbered_context_bypass_reproduced': True}, ensure_ascii=False))
