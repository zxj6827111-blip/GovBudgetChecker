import json,sys
from dataclasses import asdict
sys.path.insert(0,sys.argv[1])
from src.services.material_writing import collect
from src.services.template_scope import comprehensive_writing_contrast
from tests.test_material_writing_rules import archive_document
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology,CMM019_CoverElements
from src.engine.rule_outcome import RuleOutcomeSignal
results=[]
for note in ['本表反映部门本年度收入情况。\n12','本表反映部门本年度收入情况\n12','本表反映部门本年度收入情况']:
 pages=['上海市测试中心2024年度部门决算','上海市测试中心2024年度部门决算表\n'+note,'上海市测试中心2024年度部门决算情况说明\n本部门经费情况见相关说明。']
 spans=[{'page':p,'start':0,'end':len(t),'source_method':'native_text'} for p,t in enumerate(pages,1)]
 evidence=collect(pages,source_spans=spans)
 c=comprehensive_writing_contrast(evidence=evidence,selected_scope='unit',page_texts=pages)
 doc=archive_document(pages)
 r={'note':note,'pages':pages,'collected':[asdict(e) for e in evidence],'contrast':c,'issues':{}}
 for rule in [CMM018_ScopeTerminology(),CMM019_CoverElements()]:
  try:items=rule.apply(doc)
  except RuleOutcomeSignal as e:items=e.partial_issues
  r['issues'][rule.code]=[{'severity':i.severity,'message':i.message} for i in items]
 results.append(r)
print(json.dumps(results,ensure_ascii=False,indent=2))
