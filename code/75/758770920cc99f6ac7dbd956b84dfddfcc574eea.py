import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
import pdfplumber
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from src.engine.fact_context import FactContext
from src.engine.pipeline import build_document,run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.schemas.parse_artifact import ParseArtifact
from src.services.document_profile_resolver import resolve_document_profile
from src.services.structure_view import build_structure_view
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.rule_source_binding import bind_rule_sources,verify_bound_rule_locations
from src.services.evidence_guard import apply_evidence_completeness
folder=ROOT/'outputs/remediation_20261008'; dest=folder/'evidence_verified';dest.mkdir(exist_ok=True)
summary=json.loads((folder/'native_verified/summary.json').read_text())
frozen=json.loads((folder/'independent_freeze_manifest.json').read_text())
records=[]
for label in frozen['records']:
 p=ROOT/label['file']; assert hashlib.sha256(p.read_bytes()).hexdigest()==label['sha256']
for index, audit in enumerate(summary['documents'],1):
 if audit['filename'] not in ['上海市信访办公室.pdf','上海市教育委员会.pdf','上海市生态环境局.pdf','上海市数据局.pdf']:continue
 path=Path(audit['source_pdf']); codefolder=folder/f'native_verified/document_{index:02}'
 artifact=ParseArtifact.from_dict(json.loads((codefolder/'artifact.json').read_text()))
 context=FactContext.from_dict(json.loads((codefolder/'fact_context.json').read_text()))
 before=hashlib.sha256(path.read_bytes()).hexdigest();assert before==audit['sha256']
 with pdfplumber.open(path) as pdf:texts=[p.extract_text() or '' for p in pdf.pages]
 tables=[[] for p in texts]
 for fragment in artifact.physical_fragments:
  if fragment.extraction_method!='page_text_fallback':tables[fragment.page_number-1].append(fragment.rows)
 doc=build_document(str(path),texts,tables,path.stat().st_size);doc.parse_artifact=artifact;doc.fact_context=context
 doc.document_profile=resolve_document_profile(filename=path.name,page_texts=texts);doc.structure_view=build_structure_view(texts,artifact)
 issues,outcomes=run_rules_with_outcomes(doc,report_kind='final');runner=EngineRuleRunner()
 findings=[runner._issue_to_finding(i,document=doc).model_dump(mode='json') for i in issues]
 for i,f in enumerate(findings,1):f['id']=f"{f['rule_id']}-{i}"
 result={'rule_findings':findings}
 b=bind_rule_sources(result,page_texts=texts,source_file_hash=before,artifact=artifact,fact_context=context,consumption=getattr(doc,'structured_consumption',[]))
 loc=verify_bound_rule_locations(result,source_pdf_path=str(path))
 gate=apply_evidence_completeness(result,document_page_count=len(texts),page_texts=texts,source_file_hash=before,source_pdf_path=str(path))
 record={'source_pdf':str(path),'sha256':before,'source_unchanged':hashlib.sha256(path.read_bytes()).hexdigest()==before,
   'pages':len(texts),'facts':len(context.facts),'binding':b,'location':loc,'evidence_completeness':gate,
   'rules':summarize_rule_outcomes(outcomes),'findings':findings,'receipts':getattr(doc,'fact_receipts',[])}
 (dest/f'{path.stem}.json').write_text(json.dumps(record,ensure_ascii=False,default=str,indent=2))
 print(path.name,len(findings),gate['complete'],[(f['rule_id'],f['severity'],f.get('source_binding_errors')) for f in findings if f.get('source_binding_status')=='unresolved'],flush=True)
 records.append({'file':path.name,'sha256':before,'issues':len(findings),'verified':gate['verified_count'],'failed':gate['failed_count'],'unverified':gate['unverified_count'],'business_errors':[{'rule':f['rule_id'],'severity':f['severity'],'message':f['message']} for f in findings if f['severity']!='info']})
(dest/'summary.json').write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'scope':'current native rule and source evidence development verification; no unseen blind acceptance','frozen_labels_unchanged':True,'documents':records},ensure_ascii=False,indent=2))
