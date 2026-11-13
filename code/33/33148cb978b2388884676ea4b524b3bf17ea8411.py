"""Read-only development corpus audit; never generate human truth or P/R."""
import argparse,csv,hashlib,json,re,unicodedata
from decimal import Decimal
from pathlib import Path
import fitz
import pdfplumber

BASE=Path(__file__).resolve().parent
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
def norm(s):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(s or ''))).replace('（','(').replace('）',')')
parser=argparse.ArgumentParser();parser.add_argument('services',type=Path);parser.add_argument('--audit-output',type=Path);args=parser.parse_args()
AUDIT=args.audit_output or BASE/'shanghai';AUDIT.mkdir(parents=True,exist_ok=True)
observations=json.loads((BASE/'shanghai/metadata_observation.json').read_text())
folders=sorted(args.services.glob('shanghai_*'))
rows=[];findings=[];errors=[];manual_evidence_risks=[];reading_order_fallbacks=[]
for index,folder in enumerate(folders):
 if not (folder/'report.json').exists():continue
 original=observations[index];source=folder/'source.pdf'
 status=json.loads((folder/'status.json').read_text());report=json.loads((folder/'report.json').read_text())
 context=json.loads((folder/'fact_context.json').read_text());rr=report['review_report'];meta=status.get('meta') or {}
 with fitz.open(source) as pdf:pages=[p.get_text() for p in pdf]
 context_by_id={f['fact_uid']:f for f in context['facts']}
 identity_errors=[]
 if sha(source)!=original['sha256']:identity_errors.append('source_sha_mismatch')
 org=(status.get('document_profile',{}).get('organization_name') or {}).get('value')
 if not org or norm(org) not in norm(original['cover_original_text']):identity_errors.append('organization_not_bound_to_literal_cover')
 if status.get('report_year')!=2025 or status.get('report_kind')!='final':identity_errors.append('report_identity')
 for fact in context['facts']:
  refs=fact.get('extra_dims',{}).get('evidence_refs',[])
  for ref in refs:
   page=ref.get('physical_page')
   if ref.get('file_hash')!=original['sha256'] or not isinstance(page,int) or not 1<=page<=len(pages):
    identity_errors.append('fact_evidence_original_page:'+fact['fact_uid'])
  if fact.get('recognition_status') not in ['rejected','low_confidence']:
   if not refs:identity_errors.append('usable_operand_without_original_reference:'+fact['fact_uid'])
   try:assert Decimal(str(fact['normalized_value'])).is_finite()
   except Exception:identity_errors.append('nonfinite_operand:'+fact['fact_uid'])
 for entry in meta.get('structured_consumption',[]):
  if entry.get('fact_version')!=context['version_token']:identity_errors.append('projection_version')
  if any(uid not in context_by_id for uid in entry['fact_uids']):identity_errors.append('projection_fact_identity')
 issues=rr.get('issues',[])
 for issue in issues+rr.get('tips',[]):
  matches=[]
  for evidence in issue.get('evidence',[]):
   page=evidence.get('physical_page') or evidence.get('page');quote=evidence.get('quote') or evidence.get('text') or evidence.get('text_snippet')
   exact=isinstance(page,int) and 1<=page<=len(pages) and bool(norm(quote)) and norm(quote) in norm(pages[page-1])
   if not exact and isinstance(page,int) and 1<=page<=len(pages) and norm(quote):
    with pdfplumber.open(source) as native_pdf:
     primary_text=native_pdf.pages[page-1].extract_text() or ''
    exact=norm(quote) in norm(primary_text)
    if exact:reading_order_fallbacks.append({'scene':folder.name,'issue_id':issue.get('id'),'physical_page':page,'reason':'Native pdfplumber and PyMuPDF disagree on text reading order; quote verified exactly against the same original physical page using the primary extractor.'})
   matches.append(exact)
   if issue.get('evidence_status') in ['verified','complete'] and evidence.get('file_hash')!=original['sha256']:identity_errors.append('verified_quote_wrong_file_hash:'+str(issue.get('id')))
   if issue.get('evidence_status') in ['verified','complete'] and not exact:identity_errors.append('verified_quote_not_on_actual_source_page:'+str(issue.get('id')))
  findings.append({'filename':original['filename'],'record_class':('issue' if issue in issues else 'tip'),'rule_id':issue.get('rule_id'),'severity':issue.get('severity'),
    'title':issue.get('title'),'page':(issue.get('location') or {}).get('page'),
    'evidence_status':issue.get('evidence_status'),'literal_whole_quote_on_source_page':bool(matches) and all(matches),
    'note':'whole quote check is conservative; generated explanations are not source quotes; not human adjudication'})
  # Explicitly incomplete candidates are risks, never verified numeric findings.
  # Fail if a high-severity unverified candidate escapes the production gate.
  if issue.get('severity') in ['error','critical','high'] and issue.get('evidence_status') not in ['verified','complete']:
   gate=meta.get('quality_gate') or {}
   guarded=(status.get('status')=='review_required'
     and status.get('analysis_conclusion')=='incomplete'
     and issue.get('evidence_status')=='incomplete_rule_warning'
     and any(reason.get('code')=='rule_evidence_incomplete' for reason in gate.get('review_reasons',[])))
   risk={'scene':folder.name,'issue_id':issue.get('id'),'rule_id':issue.get('rule_id'),
     'severity':issue.get('severity'),'evidence_status':issue.get('evidence_status'),
     'explicit_incomplete_gate':guarded,'business_verified':False}
   manual_evidence_risks.append(risk)
   if not guarded:identity_errors.append('unverified_severity_without_incomplete_gate:'+str(issue.get('id')))
 rows.append({'filename':original['filename'],'scene':folder.name,'sha256':original['sha256'],'pages':len(pages),
  'organization':(status.get('document_profile',{}).get('organization_name') or {}).get('value'),
  'year':status.get('report_year'),'kind':status.get('report_kind'),
  'status':status['status'],'structured_ingest':status['structured_ingest']['status'],
  'facts':len(context['facts']),'operand_count':meta.get('fact_context',{}).get('operand_count'),
  'issue_count':len(issues),'tip_count':len(rr.get('tips',[])),'manual_review_count':len(rr.get('manual_review',[])),
  'applicable':status['check_coverage']['applicable_total'],'completed':status['check_coverage']['completed_total'],
  'unresolved':status['check_coverage']['unresolved_total'],
  'current_fact_consumers':meta.get('fact_projection_consumers',[]),'integrity_errors':sorted(set(identity_errors)),
  'business_accepted':False})
 errors.extend({'scene':folder.name,'error':e} for e in sorted(set(identity_errors)))
complete=len(rows)==20
result={'scope':'real Shanghai development corpus technical/integrity audit, no independent human gold truth',
 'expected_documents':20,'completed_documents':len(rows),'total_pages':sum(r['pages'] for r in rows),
 'complete':complete,'technical_integrity_passed':complete and not errors,'errors':errors,
 'business_metrics':None,'business_acceptance':'not_evaluated',
 'manual_evidence_risks':manual_evidence_risks,'reading_order_fallbacks':reading_order_fallbacks,'documents':rows}
(AUDIT/'final_development_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
(AUDIT/'final_issues_observation.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2)+'\n')
for name,data in [('final_development_audit.csv',rows),('final_issues_observation.csv',findings)]:
 if data:
  with (AUDIT/name).open('w',encoding='utf-8-sig',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
print(json.dumps({k:v for k,v in result.items() if k!='documents'},ensure_ascii=False))
