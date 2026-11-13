import json,sys
sys.path.insert(0,sys.argv[1])
from tests.test_material_writing_rules import archive_document
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology,CMM019_CoverElements,_archive_writing_contrast
from src.engine.rule_outcome import RuleOutcomeSignal
rows=[]
for spacer in [' ', '  ', '\t']:
 pages=['上海市测试中心2024年度部门决算',
        '2024年度单位决算情况说明'+spacer+'12',
        '上海市测试中心2024年度部门决算表\n本表反映部门本年度收入。',
        '上海市测试中心2024年度部门决算情况说明\n本部门经费情况见相关说明。']
 doc=archive_document(pages)
 record={'pages':pages,'selected_level':'unit','page_number_separator':repr(spacer),'contrast':_archive_writing_contrast(doc),'issues':{}}
 for rule in [CMM018_ScopeTerminology(),CMM019_CoverElements()]:
  try:items=rule.apply(doc)
  except RuleOutcomeSignal as e:items=e.partial_issues
  record['issues'][rule.code]=[{'severity':x.severity,'message':x.message} for x in items]
 rows.append(record)
print(json.dumps(rows,ensure_ascii=False,indent=2))
