from pathlib import Path
import json,sys
R=Path('/Volumes/workSSD/software development/GovBudgetChecker');sys.path.insert(0,str(R));D=R/'outputs/workbuddy_independent_audit_20261020'
from src.engine.rule_outcome import RuleDeferred
from src.schemas.parse_artifact import ParseArtifact
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.rules_v33 import build_document,R33240_Table2_IncomeAdvancedCheck,R33222_Narrative5_T5,R33005_TableTotalConsistency
out=[]
for key in ['2023__上海化学工业区管理委员会','2023__上海市农场管理局','2023__上海市人民政府研究室','2023__上海市国防动员办公室']:
 art=ParseArtifact.from_dict(json.loads((D/(key+'__artifact.json')).read_text()));texts=[p['text']for p in json.loads((D/(key+'__native_text.json')).read_text())];tables=[[]for _ in texts]
 for t in art.table_fragments:tables[t.page_number-1].append(t.rows)
 doc=build_document(path=key+'.pdf',page_texts=texts,page_tables=tables,filesize=0);doc.parse_artifact=art;doc.fact_context=build_fact_context_from_artifact(art,org_name=key.split('__')[1],fiscal_year=2023,report_kind='final');doc.report_kind='final'
 for cls in [R33240_Table2_IncomeAdvancedCheck,R33222_Narrative5_T5,R33005_TableTotalConsistency]:
  rule=cls();doc.current_rule_code=rule.code
  try:
   issues=rule.apply(doc);rec={'file':key,'rule':rule.code,'status':'completed','issues':[i.message for i in issues]}
  except (RuleDeferred,Exception) as e:rec={'file':key,'rule':rule.code,'status':type(e).__name__,'detail':str(e)}
  out.append(rec);print(json.dumps(rec,ensure_ascii=False)[:500])
(D/'direct_rule_reproduction.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
