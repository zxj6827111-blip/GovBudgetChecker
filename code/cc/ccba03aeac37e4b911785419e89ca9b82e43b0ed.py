"""汇总本轮核验边界；不把回归通过率当成 PDF 准确率。"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASE = OUT.parent
log = (OUT/'target-full-verified.log').read_text()
match = re.search(r'(\d+) passed, (\d+) skipped', log)
assert match and not re.search(r'\d+ failed', log), 'Full regression not green'
protected = json.loads((BASE/'protected_hashes.json').read_text())
protected_checks = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest for name,digest in protected.items()}
snapshots = json.loads((OUT/'live-snapshots-before-service-update.json').read_text())
snapshot_checks = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest for name,digest in snapshots.items()}
assert all(protected_checks.values()) and all(snapshot_checks.values())
before = json.loads((OUT/'replay-before.json').read_text())
after = json.loads((OUT/'replay-after.json').read_text())
actual_unchanged = before['actual'] == after['actual']
assert actual_unchanged and all(row['source_unchanged'] and row['matches_stored_file_hash'] for row in after['actual'].values())
receipt = {
    'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    'branch':subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip(),
    'changes':[
        'CMM-006 无法证明同一主体、年度、基期、资金范围或唯一操作数时，保留 insufficient_data',
        '涉及结转结余等其他资金来源时，不直接以收支反向认定矛盾',
        'V33-236 同页换行和空格归一后检查并映射原文位置；疑似跨页断裂保留待补核',
        '部分规则已产生的问题与尚未完成的状态同时保留，门禁继续阻塞',
    ],
    'validation':{
        'new_boundary_tests_passed':18,
        'regression_passed':int(match.group(1)), 'regression_skipped':int(match.group(2)), 'regression_failed':0,
        'new_tests_old_version':{'failed':16,'passed':1,'note':'新增集成门禁用例之前的17项反例'},
        'original_actual_two_rule_results_unchanged':actual_unchanged,
        'original_actual_pdf_hashes_unchanged':True,
        'protected_files_unchanged':all(protected_checks.values()),'protected_files_count':len(protected_checks),
        'live_status_and_facts_unchanged':all(snapshot_checks.values()),
    },
    'actual_side_effect':{
        'frozen_budget_context_blockers_added':{'chengguan':['CMM-006'],'jianguanwei':['CMM-006']},
        'reason':'总额、分项与同比未唯一绑定，不为保留旧补核数量强制通过；其他历史待办集合仍精确断言',
        'test_name_instability':'固定2024随机尾缀在旧测试上复现422，调整测试名称后通过；业务名称校验未改',
    },
    'service':json.loads((OUT/'processes.json').read_text()),
    'health':json.loads((OUT/'health.json').read_text()),
    'limitations':[
        '18个新边界用例及全量回归不是误报率或漏报率的统计证明；尚未进行独立标注语料盲测',
        '实际材料只读文字层复放本轮两个规则，没有重跑整个PDF解析、表格检查或AI',
        '信访办现有结果的AI语义检查仍有2个窗口JSON解析失败，不能宣称全量语义检查完成',
        '保留待补核可以减少不当确定判定，但不能覆盖未知的PDF提取、身份绑定和规则语义缺口',
    ],
    'evidence':{'before':'replay-before.json','after':'replay-after.json','full':'target-full-verified.log',
                'initial_failures':['target-full-initial.log','target-full-final.log'],
                'budget_contexts':'budget-frozen-contexts.json','original_execution':'petition-execution-comparison.json'},
}
(OUT/'validation-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps({'head':receipt['head'],'validation':receipt['validation'],'service':receipt['service']},ensure_ascii=False,indent=2))
