import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
sys.path.insert(0, str(ROOT))
from src.services.template_fixed_sentence_checks import trial_fixed_sentence_findings

with zipfile.ZipFile(ROOT / 'outputs/tpl_trial_20261031_r2/trial_export_2025_final.zip') as bundle:
    config = json.loads(bundle.read('catalog.json'))['packages'][0]['content']['config']
entries = {entry['id']: entry for entry in config['trial_fixed_sentences']}

cases = [
    ('unknown_control', 'F2', '本节内容待核实。', '未检查'),
    ('both_current_zero_control', 'F2', '国有资本经营预算财政拨款收入0.00万元，支出0.00万元。', '命中'),
    ('both_current_positive_control', 'F2', '国有资本经营预算财政拨款收入10.00万元，支出10.00万元。', '不适用'),
    ('incomplete_zero_false_positive', 'F2', '国有资本经营预算财政拨款收入0.00万元，支出情况待核实。', '未检查'),
    ('budget_vs_actual_false_negative', 'F1', '“三公”经费财政拨款支出年初预算为10.00万元，支出决算为0.00万元。', '命中'),
    ('prior_year_vs_actual_false_negative', 'F2', '2025年度国有资本经营预算财政拨款收入0.00万元、支出0.00万元；2024年度收入10.00万元、支出10.00万元。', '命中'),
]
results = []
for name, entry_id, paragraph, expected in cases:
    entry = entries[entry_id]
    pages = ['2025年度决算', '第三部分 决算情况说明\n' + entry['section_anchor'] + '\n' + paragraph]
    resolution = {'status': 'confirmed', 'value': 'unit', 'snapshot': {
        'available': True, 'target_year': 2025,
        'content': {'config': {'trial_fixed_sentences': [entry]}}}}
    profile = SimpleNamespace(writing_scope=resolution,
                              organization_name=SimpleNamespace(value='上海市测试局', source='cover_label'))
    doc = SimpleNamespace(document_profile=profile, page_texts=pages)
    actual = trial_fixed_sentence_findings(doc)[0]
    result = {'case': name, 'entry_id': entry_id, 'paragraph': paragraph,
              'expected': expected, 'actual': actual['verdict'],
              'condition_state': actual['condition_state'],
              'candidate_type': actual.get('candidate_type'),
              'condition_basis': actual['condition_basis']}
    results.append(result)
    print(json.dumps(result, ensure_ascii=False))

Path('/private/tmp/gbc_trial_r2_review_20261031.json').write_text(
    json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
