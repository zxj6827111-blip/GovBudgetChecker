import json,sys
sys.path.insert(0,sys.argv[1])
from dataclasses import asdict
from tests.test_material_writing_rules import archive_document
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology,CMM019_CoverElements,_archive_writing_contrast
from src.engine.rule_outcome import RuleOutcomeSignal
results=[]
for toc in ['2024年度单位决算情况说明 12','2024年度单位决算情况\n说明 12']:
 pages=['上海市测试中心2024年度部门决算',toc,
        '上海市测试中心2024年度部门决算表\n本表反映部门本年度收入。',
        '上海市测试中心2024年度部门决算情况说明\n本部门经费情况见相关说明。']
 doc=archive_document(pages)
 r={'pages':pages,'evidence':doc.document_profile.writing_scope['evidence'],'contrast':_archive_writing_contrast(doc),'issues':{}}
 for rule in [CMM018_ScopeTerminology(),CMM019_CoverElements()]:
  try:issues=rule.apply(doc)
  except RuleOutcomeSignal as e:issues=e.partial_issues
  r['issues'][rule.code]=[{'severity':i.severity,'message':i.message} for i in issues]
 results.append(r)
print(json.dumps(results,ensure_ascii=False,indent=2))
