"""Export actual development observations and empty human decisions; never mint gold truth."""
from pathlib import Path
from collections import Counter
import csv,json
OUT=Path(__file__).resolve().parent
SERVICE=OUT/'final_footer_services'
DEST=OUT/'shanghai/final_footer'
DEST.mkdir(parents=True,exist_ok=True)
read=lambda p:json.loads(p.read_text())
observations=read(OUT/'shanghai/metadata_observation.json')
folders=sorted(SERVICE.glob('shanghai_*'))
assert len(folders)==20 and all((p/'report.json').exists() for p in folders)
reviews=[];obligations=[];tips=[]
for original,folder in zip(observations,folders):
    report=read(folder/'report.json')['review_report']
    status=read(folder/'status.json')
    base={'filename':original['filename'],'source_sha256':original['sha256'],
          'source_pdf':str((folder/'source.pdf').resolve()),'job_id':status['job_id'],
          'analysis_revision':status['analysis_revision'],
          'organization':report['metadata']['organization'],'year':report['metadata']['year']}
    for record_class in ['issues','tips']:
        for item in report[record_class]:
            loc=item.get('location') or {}
            entry={**base,'record_class':record_class,'issue_id':item['id'],'rule_id':item['rule_id'],
                   'system_severity':item['severity'],'title':item['title'],
                   'primary_page':item.get('page_number') or loc.get('page'),
                   'source_evidence':item.get('evidence',[]),'evidence_status':item.get('evidence_status'),
                   'suggestion':item.get('suggestion'),'current_fact_ids':item.get('fact_ids',[]),
                   'human_decision':None,'reviewer_id':None,'reviewed_at':None,
                   'human_original_reference':None,'human_reason':None,
                   'scope':'known development materials; not blind truth; undecided observations only'}
            reviews.append(entry)
            if record_class=='tips':tips.append(entry)
    for item in report['obligation_coverage']['instances']:
        if item['status']=='not_applicable':continue
        obligations.append({**base,'obligation_id':item['obligation_id'],'title':item['title'],
             'group':item['group_title'],'system_status':item['status'],'reason':item.get('reason'),
             'detail':item.get('detail'),'checkers':item.get('checkers',[]),
             'input_gaps':item.get('input_gaps',[]),'human_applicable':None,
             'human_complete':None,'human_missing_defect':None,'reviewer_id':None,
             'reviewed_at':None,'human_original_reference':None})
assert len(obligations)==860
for name,data in [('manual_review.pending',reviews),('obligations_review.pending',obligations),('information_tips',tips)]:
    (DEST/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    if data:
        with (DEST/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader()
            for row in data:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
gaps=Counter((item['obligation_id'],item['title'],item['system_status']) for item in obligations if item['system_status']!='completed')
(DEST/'unresolved_obligation_summary.json').write_text(json.dumps([{'obligation_id':k[0],'title':k[1],'status':k[2],'documents':n} for k,n in gaps.most_common()],ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'actual_observations':len(reviews),'information_tips':len(tips),'applicable_review_rows':len(obligations),'actual_human_decisions':0,'business_metrics':None},ensure_ascii=False))
