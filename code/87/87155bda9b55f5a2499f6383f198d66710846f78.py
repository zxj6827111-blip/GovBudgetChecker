"""Reviewer-owned local replay: same four original PDFs, same assembly for both code trees.

No database, AI, OCR, cache, upload writes, or server startup. Selected five rules only.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--code', required=True)
p.add_argument('--output', required=True)
p.add_argument('--only-job')
args = p.parse_args()
code = Path(args.code).resolve()
sys.path.insert(0, str(code))
os.environ.pop('GOVBUDGET_EMPTY_VALUE_CONVENTIONS', None)
import pdfplumber
from src.services.pdf_parser import PDFParser
from src.services.pdf_page_extract import extract_tables_from_page, extract_visible_text_from_page
from src.services.document_profile_resolver import resolve_document_profile
from src.services.check_subject import resolve_check_subject
from src.services.material_writing import qualified_identity_view
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document, run_rules_with_outcomes
from src.engine.rules_v33 import R33005_TableTotalConsistency, R33104_Expense_vs_Text, R33222_Narrative5_T5, R33240_Table2_IncomeAdvancedCheck, R33CrossSanGongEcon
from src.engine.narrative_zero_evidence import narrative_zero_sources
from src.engine.diagnostic_fields import _section_page

root = Path('/Volumes/workSSD/software development/GovBudgetChecker')
jobs = ['79efdd8f0d7b3a2938de65cfb5aad101','b589d6bd4436edaa140a224135bf10d0','02e4fb64ab6bbf325117d325a2dcbfa4','f1a4c441a3bd02ec4cce465b1739f046']
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
summary = []
for job in jobs:
    if args.only_job and job != args.only_job:
        continue
    started = time.monotonic()
    folder = root/'uploads'/job
    stored = json.loads((folder/'status.json').read_text())
    pdf_path = folder/stored['filename']
    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    with pdfplumber.open(pdf_path) as pdf:
        texts = [extract_visible_text_from_page(page) for page in pdf.pages]
        tables = [extract_tables_from_page(page) for page in pdf.pages]
        artifact = PDFParser(None).build_parse_artifact_from_handle(pdf, file_hash=sha)
    profile_text, spans = qualified_identity_view(texts, artifact)
    profile = resolve_document_profile(filename=pdf_path.name, page_texts=profile_text, explicit_report_kind=None)
    conflict_fields = {x.get('field') for x in profile.conflicts}
    org = stored['organization_name'] if job==jobs[0] else (None if 'organization_name' in conflict_fields else profile.organization_name.value)
    subject = resolve_check_subject(organization_id=None, organization_name=org,
        organization_level='unit' if job==jobs[0] else None,
        cover_name=profile.organization_name.value, cover_conflicted='organization_name' in conflict_fields,
        subject_level=profile.subject_level.value, cover_text=profile_text[0] if profile_text else None)
    context = build_fact_context_from_artifact(artifact, org_name=subject['name'],
        org_level='unit' if job==jobs[0] else None, fiscal_year=profile.year,
        report_kind=profile.kind, doc_type={'final':'dept_final','budget':'dept_budget'}.get(profile.kind),
        require_complete_identity=True)
    doc = build_document(str(pdf_path), texts, tables, pdf_path.stat().st_size)
    doc.fact_context=context
    doc.parse_artifact=artifact
    doc.document_profile=profile
    doc.writing_page_texts=profile_text
    doc.writing_source_spans=spans
    classes=[R33005_TableTotalConsistency,R33104_Expense_vs_Text,R33222_Narrative5_T5,R33240_Table2_IncomeAdvancedCheck,R33CrossSanGongEcon]
    cross_trace=[]
    def trace(frame,event,arg):
        if event=='return' and frame.f_code.co_name=='apply' and isinstance(frame.f_locals.get('self'),R33CrossSanGongEcon):
            cross_trace.append({k:frame.f_locals.get(k) for k in ['basic','three_public','blocked','blank_keys','zero_authorized','zero_basis_by_subject','unresolved']})
        return trace
    sys.settrace(trace)
    try:
        issues,outcomes=run_rules_with_outcomes(doc,use_ai_assist=False,report_kind='final',rules=classes)
    finally:
        sys.settrace(None)
    receipts=getattr(doc,'fact_receipts',[]) or []
    def as_dict(x):
        if isinstance(x,dict):return x
        if hasattr(x,'to_dict'):return x.to_dict()
        return vars(x)
    receipts=[as_dict(x) for x in receipts]
    result={'code':str(code),'job':job,'pdf':str(pdf_path),'sha256':sha,
        'identity':{'org':subject['name'],'year':profile.year,'kind':profile.kind,'facts':len(context.facts)},
        'rules':{},'receipts':receipts,'cross_trace':cross_trace,
        'issues':[as_dict(x) for x in issues],
        'fallback_project_page':_section_page(doc,'说明文本中未提取到项目支出'),
        'zero_sources':{i:narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator=i,stage='决算') for i in ['overseas','reception','vehicle_purchase']}}
    for o in outcomes:
        rs=[r for r in receipts if r.get('rule_id')==o.rule_id]
        result['rules'][o.rule_id]={'status':o.status,'detail':o.detail,
            'receipt_statuses':dict(Counter(r.get('status') for r in rs)),
            'receipt_count':len(rs),'diagnostics':o.diagnostics,
            'unresolved_reasons':o.unresolved_reasons,
            'issue_count':sum(getattr(i,'rule',None)==o.rule_id for i in issues)}
    (output/f'{job}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    compact={'job':job,'sha256':sha,'identity':result['identity'],
        'rules':{k:{f:v[f] for f in ['status','receipt_statuses','receipt_count','issue_count']} for k,v in result['rules'].items()},
        'fallback_project_page':result['fallback_project_page'],'elapsed':round(time.monotonic()-started,2)}
    summary.append(compact)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(compact,ensure_ascii=False),flush=True)
