import json, collections, re, sys
C=collections.Counter
def norm(m): return re.sub(r'“[^”]*”','“X”',re.sub(r'\d+','N',m))
report={}
for y in (2022,2023,2024,2025):
    b=json.load(open(f'r2-base-{y}.json')); h=json.load(open(f'r2-cur-{y}.json'))
    finals=[k for k,v in h.items() if 'CMM019' in v]
    yr={'final_docs':len(finals),'non_final':len(h)-len(finals),'writing_status':dict(C(h[k].get('writing') for k in finals))}
    for r in ('CMM018','CMM019','CMM027','CMM008','CMM013','CMM021','CMM023'):
        sb=C(b[k].get(r,{}).get('s','absent') for k in finals); sh=C(h[k].get(r,{}).get('s','absent') for k in finals)
        added=C(); removed=C(); docs_changed=0; ex_add=[]; ex_rm=[]
        for k in finals:
            bi=C(b[k].get(r,{}).get('i',[])); hi=C(h[k].get(r,{}).get('i',[]))
            a=hi-bi; d=bi-hi
            if a or d: docs_changed+=1
            for m in a.elements(): added[norm(m)]+=1; ex_add.append((k,m))
            for m in d.elements(): removed[norm(m)]+=1; ex_rm.append((k,m))
        yr[r]={'status_base':dict(sb),'status_current':dict(sh),'docs_with_issue_change':docs_changed,
               'added':dict(added.most_common()),'removed':dict(removed.most_common()),
               'added_examples':ex_add[:6],'removed_examples':ex_rm[:6]}
        if r=='CMM019': yr[r]['deferred_reasons']=dict(C(h[k][r].get('d') for k in finals if h[k][r]['s']=='deferred').most_common())
        if r=='CMM018': yr[r]['deferred_reasons']=dict(C(h[k][r].get('d') for k in finals if h[k][r]['s']=='deferred').most_common())
    report[y]=yr
json.dump(report,open(sys.argv[1],'w'),ensure_ascii=False,indent=1)
for y,yr in report.items():
    print('==',y,'finals',yr['final_docs'],'writing',yr['writing_status'])
    for r in ('CMM018','CMM019','CMM027','CMM008','CMM013','CMM021','CMM023'):
        x=yr[r]; print(' ',r,'base',x['status_base'],'cur',x['status_current'],'changed docs',x['docs_with_issue_change'],'+',sum(x['added'].values()),'-',sum(x['removed'].values()))
