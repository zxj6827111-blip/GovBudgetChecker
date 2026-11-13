import json,re,sys
from pathlib import Path
import pdfplumber
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from src.engine.pipeline import build_document,run_rules_with_outcomes
from src.engine.rule_outcome import summarize_rule_outcomes
from src.engine.check_obligations import build_obligation_ledger
from src.engine.fact_context import FactContext
from src.schemas.parse_artifact import ParseArtifact
from src.services.document_profile_resolver import resolve_document_profile
from src.services.structure_view import build_structure_view
from src.engine.native_empty_operands import blank_term
from src.engine.native_no_activity import empty_three_public_evidence
BASE=Path(__file__).resolve().parent
manifest={x['sample_id']:x for x in json.loads((BASE/'sample_manifest.json').read_text())['selected']}
results=[]
for sid,owner in [('fresh_02','上海市普陀区城市运行管理中心'),('fresh_07','上海市普陀区文化馆'),('fresh_13','上海市普陀区石泉路街道综合行政执法队'),('fresh_14','上海市普陀区人民政府长寿路街道办事处（本级）'),('fresh_19','上海市普陀区总工会')]:
 item=manifest[sid];folder=BASE/'after'/sid
 with pdfplumber.open(item['source_pdf']) as pdf:texts=[page.extract_text() or '' for page in pdf.pages]
 assert owner in re.sub(r'\s+','',texts[0])
 artifact=ParseArtifact.from_dict(json.loads((folder/'artifact.json').read_text()));context=FactContext.from_dict(json.loads((folder/'fact_context.json').read_text()));before_money=[(f.fact_uid,f.raw_value,str(f.normalized_value)) for f in context.facts]
 profile=resolve_document_profile(filename=item['filename'],page_texts=texts)
 current=json.loads((folder/'coverage.json').read_text())
 # 只在当前进程副本中注入原封面可逐字验证的主体。数字/原格/磁盘结果不变。
 context.org_name=owner
 for fact in context.facts:fact.org_name=owner
 profile.organization_name.value=owner
 doc=build_document(item['source_pdf'],texts,[[] for _ in texts],item['bytes']);doc.parse_artifact=artifact;doc.fact_context=context;doc.document_profile=profile;doc.structure_view=build_structure_view(texts,artifact)
 issues,outcomes=run_rules_with_outcomes(doc,report_kind=profile.kind)
 coverage=build_obligation_ledger(profile,report_kind=profile.kind,rule_execution_summary=summarize_rule_outcomes(outcomes),ai_execution={'requested':False,'state':'not_requested'})
 after_money=[(f.fact_uid,f.raw_value,str(f.normalized_value)) for f in context.facts]
 results.append({'sample_id':sid,'source_cover':texts[0],'original_owner':None,'probe_owner':owner,'actual_blockers':current['blocking_total'],'in_memory_owner_probe_blockers':coverage['blocking_total'],'resolved_ids':sorted(set(current['blocking_obligation_ids'])-set(coverage['blocking_obligation_ids'])),'money_values_unchanged':before_money==after_money,'scope':'diagnostic only, production code and saved result unchanged'})
 print(sid,current['blocking_total'],coverage['blocking_total'],flush=True)
sid='fresh_12';item=manifest[sid];folder=BASE/'after'/sid
with pdfplumber.open(item['source_pdf']) as pdf:texts=[p.extract_text() or '' for p in pdf.pages]
artifact=ParseArtifact.from_dict(json.loads((folder/'artifact.json').read_text()));context=FactContext.from_dict(json.loads((folder/'fact_context.json').read_text()));doc=build_document(item['source_pdf'],texts,[[] for _ in texts],item['bytes']);doc.parse_artifact=artifact;doc.fact_context=context
terms=[blank_term(doc,'FIN_07_three_public',ind,code='total',stage=stage) for stage in ['年初预算','决算'] for ind in ['total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception']]
results.append({'sample_id':sid,'cause':'explicit empty statement variant not recognized','physical_page':14,'source_note':texts[13],'all_twelve_blank_operands_certified':all(t is not None for t in terms),'current_empty_disclosure_recognized':empty_three_public_evidence(doc) is not None,'difference':'原文没有“三公”经费支出，当前正则额外要求财政拨款字样；没有修改原文或数字'})
(BASE/'root_cause_probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
