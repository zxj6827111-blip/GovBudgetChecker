import json,hashlib,sys
from pathlib import Path
from types import SimpleNamespace
import fitz
from src.services.pdf_parser import PDFParser
from src.engine.fact_context import build_fact_context_from_artifact,_column_mappings_for_instance,_fragment_for_instance
from src.engine.rules_v33 import build_document,R33241_Table3_ExpenseAdvancedCheck
from src.engine.rule_outcome import RuleDeferred
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=Path(__file__).parent
manifest=json.loads((root/'outputs/unified_closeout_round6_review_codex_20261104/diagnostic_manifest_10.json').read_text())
for item in manifest['items']:
 if item['order'] not in [1,4,5,6,7,8,9]:continue
 p=Path(item['pdf_path']);sid=str(item['order']).zfill(2)
 print('start',sid,p.name,flush=True)
 assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
 artifact=PDFParser(None).build_parse_artifact(str(p))
 ctx=build_fact_context_from_artifact(artifact,org_name=p.stem.split('202')[0],fiscal_year=item['year'],report_kind='final',doc_type=item['historical_doc_type'])
 (out/(sid+'_artifact.json')).write_text(json.dumps(artifact.to_dict(),ensure_ascii=False))
 (out/(sid+'_facts.json')).write_text(json.dumps(ctx.to_dict(),ensure_ascii=False))
 rows=[]
 for instance in artifact.logical_tables:
  if instance.table_code not in ['FIN_03_expenditure','FIN_02_income','FIN_07_three_public']:continue
  table=_fragment_for_instance(artifact,instance)
  rows.append({'table':instance.table_code,'page':table.page_number,'headers':table.rows[:table.header_row_count],'rows':table.rows[table.header_row_count:table.header_row_count+5],'mappings':_column_mappings_for_instance(instance,table)})
 (out/(sid+'_tables.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 with fitz.open(p) as pdf:
  texts=[page.get_text() for page in pdf]
  doc=build_document(str(p),texts,[[] for _ in texts],p.stat().st_size);doc.parse_artifact=artifact;doc.fact_context=ctx
  if item['order'] in [4,6,7,8]:
   try:r=R33241_Table3_ExpenseAdvancedCheck().apply(doc);status='finished'
   except RuleDeferred as ex:r=ex.partial_issues;status=str(ex)
   (out/(sid+'_rule.json')).write_text(json.dumps({'status':status,'receipts':getattr(doc,'fact_receipts',[]),'issues':[{'message':i.message,'check_relation_id':i.check_relation_id} for i in r]},ensure_ascii=False,indent=2))
  for t in rows:
   if t['table']=='FIN_03_expenditure':pdf[t['page']-1].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(out/(sid+'_expenditure.png'))
 print('done',sid,len(ctx.facts),flush=True)
