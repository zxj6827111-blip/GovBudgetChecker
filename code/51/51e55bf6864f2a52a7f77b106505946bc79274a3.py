"""只读复放两个受影响规则；不修改任务、不调用 AI、不代替原文准确率验收。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument('--repo', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
sys.path.insert(0, args.repo)

import fitz
from src.engine.common_rules import CMM006_IncomeExpenseTrendConsistency
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import R33236_DocumentScopeTerminology, build_document

root = Path('/Volumes/workSSD/software development/GovBudgetChecker')
out = root / 'outputs/pdf_review_batch2_20261027'

def replay(texts, scope=None, year=2024, owner='上海市甲局', rule=None):
    material = build_document('2024年度决算.pdf', texts, [[] for _ in texts], 0)
    material.fact_context = SimpleNamespace(org_scope=scope, fiscal_year=year, org_name=owner,
        facts=[SimpleNamespace(extra_dims={'org_scope_pending':scope is None})])
    issues, outcomes = run_rules_with_outcomes(material, report_kind='final', rules=[rule])
    return {'status':outcomes[0].status, 'findings':len(issues),
            'detail':outcomes[0].detail, 'messages':[issue.message for issue in issues]}

cases = {
 'different_owners': replay(['上海市甲局2024年收入预算100万元，比2023年预算增加10万元。上海市乙局2024年支出预算90万元，比2023年预算减少10万元。'], rule=CMM006_IncomeExpenseTrendConsistency()),
 'different_years': replay(['上海市甲局2024年收入预算100万元，比2023年预算增加10万元。上海市甲局2023年支出预算90万元，比2022年预算减少10万元。'], rule=CMM006_IncomeExpenseTrendConsistency()),
 'wrapped_unknown': replay(['部门决算\n安排如下。'], rule=R33236_DocumentScopeTerminology()),
 'wrapped_known_unit': replay(['部门决算\n安排如下。'], scope='unit', rule=R33236_DocumentScopeTerminology()),
 'cross_page': replay(['本年部门决算\n1','安排如下。\n2'], scope='unit', rule=R33236_DocumentScopeTerminology()),
 'same_context_real_errors': replay(['2024年财政拨款收入预算100万元，比2023年预算增加10万元。2024年财政拨款支出预算100万元，比2023年预算减少10万元。财政拨款收入支出减少的主要原因是项目调整。'], rule=CMM006_IncomeExpenseTrendConsistency()),
}
actual = {}
for job in (out/'live-after').iterdir():
    status = json.loads((job/'status.json').read_text())
    context = json.loads((job/'fact_context.json').read_text())
    path = root/'uploads'/status['saved_path']
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with fitz.open(path) as pdf:
        texts = [page.get_text() for page in pdf]
    actual[job.name] = {'filename':path.name, 'source_sha256':before,
        'matches_stored_file_hash':before == context['file_hash'], 'physical_pages':len(texts),
        'checks':{rule.code: replay(texts, scope=context['org_scope'], year=context['fiscal_year'],
            owner=context['org_name'], rule=rule) for rule in [CMM006_IncomeExpenseTrendConsistency(), R33236_DocumentScopeTerminology()]},
        'source_unchanged':before == hashlib.sha256(path.read_bytes()).hexdigest()}
result={'repository':args.repo, 'cases':cases, 'actual':actual,
        'limits':['只读原件文字层并复放两个规则；未重新解析表格、未重跑 AI、未改写在线任务',
                  '原件规则结果不变不等于零误报漏报；未做整套语料盲测']}
Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2))
