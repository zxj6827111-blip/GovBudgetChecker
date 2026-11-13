import asyncio, hashlib, json, logging, sys, time, os
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import pdfplumber
from src.schemas.issues import JobContext, AnalysisConfig
from src.services.analyze_dual import DualModeAnalyzer
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.services.structure_view import build_structure_view
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations
from src.services.evidence_guard import apply_evidence_completeness
logging.disable(logging.CRITICAL)
OUT=Path('outputs/annotation_audit_20261010')
os.environ["UPLOAD_DIR"] = str((OUT/"snapshots").resolve())
async def main():
 summary=[]
 for d in sorted(Path('uploads').iterdir()):
  pdfs=list(d.glob('*.pdf')) if d.is_dir() else []
  if not pdfs: continue
  path=pdfs[0]; started=time.monotonic(); old=json.loads((d/'status.json').read_text());dest=OUT/d.name;dest.mkdir(exist_ok=True)
  try:
   sha=hashlib.sha256(path.read_bytes()).hexdigest()
   with pdfplumber.open(path) as pdf:
    texts=[p.extract_text() or '' for p in pdf.pages]
    artifact=PDFParser(None).build_parse_artifact_from_handle(pdf,file_hash=sha)
   profile=resolve_document_profile(filename=path.name,page_texts=texts)
   context=build_fact_context_from_artifact(artifact,org_name=profile.organization_name.value,fiscal_year=profile.year,report_kind=profile.kind,doc_type='dept_final')
   tables=[[] for _ in texts]
   for frag in artifact.physical_fragments:
    if frag.extraction_method!='page_text_fallback': tables[frag.page_number-1].append(frag.rows)
   jc=JobContext(job_id=d.name,pdf_path=str(path.resolve()),page_texts=texts,page_tables=tables,filesize=path.stat().st_size,parse_artifact=artifact.to_dict(),fact_context=context.to_dict(),meta={'report_kind':profile.kind,'document_profile':profile.model_dump(),'structure_view':build_structure_view(texts,artifact)})
   dual=await DualModeAnalyzer().analyze(jc,AnalysisConfig(ai_enabled=False,enable_ai_analysis=False,enable_ai_locator=False,rule_enabled=True))
   result=dual.model_dump(); binding=bind_rule_sources(result,page_texts=texts,source_file_hash=sha,artifact=artifact,fact_context=context,consumption=dual.meta.get('structured_consumption',[]))
   locations=verify_bound_rule_locations(result,source_pdf_path=str(path.resolve()))
   completeness=apply_evidence_completeness(result,document_page_count=len(texts),page_texts=texts,source_file_hash=sha,source_pdf_path=str(path.resolve()))
   fs=result['rule_findings'];oldfs=old.get('result',{}).get('rule_findings',[])
   record={'job_id':d.name,'name':path.stem,'sha256':sha,'pages':len(texts),'old_findings':len(oldfs),'new_findings':len(fs),'old_exact':sum(bool(f.get('bbox')) for f in oldfs),'new_exact':sum(bool(f.get('bbox')) for f in fs),'old_hierarchy':[f['message'] for f in oldfs if '层级校验失败' in f.get('message','')],'new_hierarchy':[f['message'] for f in fs if '层级校验失败' in f.get('message','')],'old_v221':[f['message'] for f in oldfs if f.get('rule_id')=='V33-221'],'new_v221':[f['message'] for f in fs if f.get('rule_id')=='V33-221'],'binding':binding,'completeness':{k:v for k,v in completeness.items() if not isinstance(v,list)},'unbound':[{'rule_id':f.get('rule_id'),'message':f.get('message'),'errors':f.get('source_binding_errors'),'level':f.get('location_level')} for f in fs if not f.get('bbox')],'seconds':round(time.monotonic()-started,1),'source_unchanged':sha==hashlib.sha256(path.read_bytes()).hexdigest()}
   for name,value in [('artifact.json',artifact.to_dict()),('fact_context.json',context.to_dict()),('report.json',result),('summary.json',record)]: (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str))
   summary.append(record)
   print(path.stem,'findings',len(oldfs),'->',len(fs),'exact',record['old_exact'],'->',record['new_exact'],'hierarchy',len(record['old_hierarchy']),'->',len(record['new_hierarchy']),'seconds',record['seconds'],flush=True)
  except Exception as exc:
   summary.append({'job_id':d.name,'name':path.stem,'error':repr(exc)}); print(path.stem,'ERROR',repr(exc),flush=True)
  (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
asyncio.run(main())
