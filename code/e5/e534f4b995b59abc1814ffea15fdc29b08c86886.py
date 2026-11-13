import json,sys,zipfile
from pathlib import Path
from types import SimpleNamespace
repo=Path('/Volumes/workSSD/software development/GovBudgetChecker')
sys.path.insert(0,str(repo))
from src.services.template_fixed_sentence_checks import trial_fixed_sentence_findings
with zipfile.ZipFile(repo/'outputs/tpl_trial_20261031/trial_export_2025_final.zip') as z:
    catalog=json.loads(z.read('catalog.json'))
entry=next(e for e in catalog['packages'][0]['content']['config']['trial_fixed_sentences'] if e['id']=='F2')
resolution={'status':'confirmed','value':'unit','source':'archive','snapshot':{'available':True,'content':{'config':{'trial_fixed_sentences':[entry]}}}}
heading='九、国有资本经营预算财政拨款收入支出决算情况说明'
cases=[
 ('condition_unknown',['2025年度决算','第三部分 决算情况说明\n'+heading+'\n本节内容待核实。']),
 ('explicit_zero_amounts',['2025年度决算','第三部分 决算情况说明\n'+heading+'\n国有资本经营预算财政拨款收入0.00万元，支出0.00万元。']),
 ('positive_amounts',['2025年度决算','第三部分 决算情况说明\n'+heading+'\n国有资本经营预算财政拨款收入10.00万元，支出10.00万元。']),
 ('other_section_quotation',['2025年度决算','第三部分 决算情况说明\n'+heading+'\n本节内容待核实。','第四部分 名词解释\n引用往年说明示例：2025年度无国有资本经营预算财政拨款收入和支出。'])
]
rows=[]
for name,pages in cases:
    doc=SimpleNamespace(document_profile=SimpleNamespace(writing_scope=resolution),page_texts=pages)
    result=trial_fixed_sentence_findings(doc)[0]
    rows.append({'case':name,'pages':pages,'result':result})
print(json.dumps(rows,ensure_ascii=False,indent=2))
