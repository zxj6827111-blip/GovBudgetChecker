import os
import sys,json,asyncio
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
from src.schemas.issues import JobContext,AnalysisConfig
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.pdf_page_extract import extract_visible_text_from_page,extract_tables_from_page
from src.schemas.parse_artifact import ParseArtifact
from src.engine.fact_context import FactContext
from src.services.rule_source_binding import bind_rule_sources,verify_bound_rule_locations
from src.services.evidence_guard import apply_evidence_completeness
import pdfplumber
out=Path(os.environ.get('CODEX_REVIEW_OUT', str(Path(__file__).resolve().parents[1] / 'replay')))
out.mkdir(parents=True,exist_ok=True)
async def main():
 reports=[]
 for pref in ['6000a11c','0b507c','0a8a27']:
  prior=json.loads((out/f'{pref}_new_result.json').read_text());pdf=Path(prior['source']);artifact=json.loads((out/f'{pref}_artifact.json').read_text())
  with pdfplumber.open(pdf) as d:
   texts=[extract_visible_text_from_page(pg) for pg in d.pages];tables=[extract_tables_from_page(pg) for pg in d.pages]
  ctx=JobContext(job_id='independent-'+pref,pdf_path=str(pdf),page_texts=texts,page_tables=tables,fact_context=prior['facts'],parse_artifact=artifact,meta={'report_kind':'final','fiscal_year':2025,'org_name':prior['profile']['organization_name']['value'],'document_profile':prior['profile'],'use_ai_assist':False,'writing_page_texts':texts})
  runner=EngineRuleRunner();items=await runner.run_rules(ctx,[],AnalysisConfig(dual_mode=False,ai_enabled=False,rule_enabled=True,enable_ai_analysis=False,enable_ai_locator=False))
  stats=runner.get_stats();result={'rule_findings':[i.model_dump() for i in items]}
  bind_rule_sources(result,page_texts=texts,source_file_hash=prior['source_hash'],artifact=ParseArtifact.from_dict(artifact),fact_context=FactContext.from_dict(prior['facts']),consumption=stats.get('structured_consumption',[]))
  verify_bound_rule_locations(result,source_pdf_path=str(pdf));apply_evidence_completeness(result,document_page_count=len(texts),page_texts=texts,source_file_hash=prior['source_hash'],source_pdf_path=str(pdf))
  report={'case':pref,'summary':runner.get_rule_execution_summary(),'evidence_counts':dict(Counter((i.get('evidence_verification') or {}).get('status') for i in result['rule_findings'])),'result':result,'stats':stats}
  reports.append(report);print(pref,report['evidence_counts'], 'V33-222',report['summary']['rule_statuses'].get('V33-222'),flush=True)
 (out/'dual_path_check.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2,default=str))
asyncio.run(main())
