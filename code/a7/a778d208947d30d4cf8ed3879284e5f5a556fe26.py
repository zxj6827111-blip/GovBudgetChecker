"""Copy actual exported candidates; no human adjudication or invented values."""
from pathlib import Path
import csv, json
OUT=Path(__file__).resolve().parent
SERVICES=OUT/'final_bound_services'
AUDIT=OUT/'shanghai/final_bound'
observations=json.loads((OUT/'shanghai/metadata_observation.json').read_text())
items=[]
for original,folder in zip(observations, sorted(SERVICES.glob('shanghai_*'))):
    status=json.loads((folder/'status.json').read_text())
    report=json.loads((folder/'report.json').read_text())['review_report']
    for issue in report.get('issues',[]):
        loc=issue.get('location') or {}
        evidence=issue.get('evidence') or []
        pages=sorted({e.get('physical_page') or e.get('page') for e in evidence if isinstance(e.get('physical_page') or e.get('page'),int)})
        snippet='\n'.join(e.get('quote') or e.get('text') or e.get('text_snippet') or '' for e in evidence)
        item={'filename':original['filename'],'source_sha256':original['sha256'],
              'rule_id':issue.get('rule_id'),'title':issue.get('title'),'severity':issue.get('severity'),
              'document_type':'决算公开','year':status.get('report_year'),
              'organization':(status.get('document_profile',{}).get('organization_name') or {}).get('value'),
              'page':loc.get('page') or (pages[0] if len(pages)==1 else None),'physical_pages':pages,
              'bbox':loc.get('bbox'),'table_or_section':loc.get('table') or issue.get('section_id'),
              'snippet':snippet or None,'expected':issue.get('expected'),'actual':issue.get('actual'),
              'difference':loc.get('diff'),'reason':issue.get('description') or issue.get('title'),
              'suggestion':issue.get('suggestion'),'confidence':issue.get('confidence'),
              'evidence_status':issue.get('evidence_status'),'verification_status':issue.get('verification_status'),
              'fact_ids':issue.get('fact_ids',[]),'evidence':evidence,
              'business_verified':False,'human_decision':None,
              'note':'Actual system candidate; source verification does not adjudicate business correctness. Unknown fields stay null.'}
        items.append(item)
AUDIT.mkdir(parents=True,exist_ok=True)
(AUDIT/'real_candidate_issues.json').write_text(json.dumps(items,ensure_ascii=False,indent=2)+'\n')
if items:
    with (AUDIT/'real_candidate_issues.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(items[0]));writer.writeheader()
        for item in items:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in item.items()})
old=json.loads((OUT/'shanghai/final_development_audit.json').read_text())
new=json.loads((AUDIT/'final_development_audit.json').read_text())
by_old={d['sha256']:d for d in old['documents']}
delta=[]
for row in new['documents']:
    baseline=by_old[row['sha256']]
    delta.append({'filename':row['filename'],'sha256':row['sha256'],
                  'v8_completed':baseline['completed'],'v10_completed':row['completed'],
                  'applicable':row['applicable'],'v8_candidates':baseline['issue_count'],
                  'v10_candidates':row['issue_count'],'v8_organization':baseline['organization'],
                  'v10_organization':row['organization'],
                  'business_metrics':None,'scope':'same original files; source-integrity and coverage comparison, not human precision/recall'})
(AUDIT/'same_source_v8_v10_delta.json').write_text(json.dumps(delta,ensure_ascii=False,indent=2)+'\n')
with (AUDIT/'same_source_v8_v10_delta.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(delta[0]));w.writeheader();w.writerows(delta)
print(json.dumps({'actual_candidates':len(items),'completed':sum(r['completed'] for r in new['documents']),
                  'applicable':sum(r['applicable'] for r in new['documents'])},ensure_ascii=False))
