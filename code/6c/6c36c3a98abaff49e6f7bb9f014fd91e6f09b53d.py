import sys,json,gzip
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent;CODE=ROOT/'artifacts/comprehensive-review-closure-20261109';sys.path.insert(0,str(CODE))
from src.services.pdf_parser import PDFParser
from src.schemas.parse_artifact import ParseArtifact
from src.services.input_processing_identity import bind_artifact_document_identity
from src.engine.fact_context import build_fact_context_from_artifact,_fragment_for_instance,_column_mappings_for_instance
from src.engine.native_empty_operands import blank_table_terms,blank_term
manifest=json.loads((OUT/'baseline_fresh_source_manifest.json').read_text())
for prefix in sys.argv[1:]:
 j=next(k for k in manifest if k.startswith(prefix));s=json.loads((OUT/'baseline_fresh'/j/'uploads'/j/'status.json').read_text());cache=OUT/(j+'_artifact.json.gz')
 if cache.exists():a=ParseArtifact.from_dict(json.loads(gzip.decompress(cache.read_bytes())))
 else:
  a=PDFParser(None).build_parse_artifact(manifest[j]['pdf']);cache.write_bytes(gzip.compress(json.dumps(a.to_dict(),ensure_ascii=False).encode()))
 profile=s['document_profile'];meta=s['result']['meta'];owner=(profile.get('organization_name') or {}).get('value');year=meta['report_year']
 bind_artifact_document_identity(a,organization_id=None,organization_name=owner,fiscal_year=year,report_kind='final',doc_type='dept_final',check_subject_name=owner)
 ctx=build_fact_context_from_artifact(a,org_name=owner,fiscal_year=year,report_kind='final',doc_type='dept_final');doc=SimpleNamespace(parse_artifact=a,fact_context=ctx,page_texts=[])
 print('JOB',j,manifest[j]['filename'],owner,year,flush=True)
 for inst in a.logical_tables:
  if inst.table_code!='FIN_06_basic_expenditure':continue
  t=_fragment_for_instance(a,inst);m=_column_mappings_for_instance(inst,t);print('TABLE',inst.to_dict(),'rows',len(t.rows),'header',t.header_row_count,'unit',t.unit_hint,'mappings',m,flush=True)
  for n,row in enumerate(t.rows):
   if n<4 or n>len(t.rows)-7:print(n,row,flush=True)
  ts=blank_table_terms(doc,inst.table_code);print('blankterms',len(ts),'facts',len([f for f in ctx.facts if f.table_code==inst.table_code]),flush=True)
  if not ts:
   last={}
   def trace(frame,event,arg):
    if frame.f_code.co_name=='blank_term':
     if event=='line':last['line']=frame.f_lineno
     elif event=='return':print('blank_term return',last.get('line'),arg is not None,flush=True)
    return trace
   sys.settrace(trace);blank_term(doc,inst.table_code,'total_actual',code='301');sys.settrace(None)

 from src.engine.native_table_arithmetic import table_facts,_economic_table_view
 fs,error=table_facts(doc,'FIN_06_basic_expenditure');print('viewerror',error if error else _economic_table_view(doc,fs)[1],flush=True)
