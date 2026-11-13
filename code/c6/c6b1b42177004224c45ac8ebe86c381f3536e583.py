"""Check M-03's documented wrong-type handling through the full component boundary."""
import copy
import json
from pathlib import Path
import remaining_boundary_probes as controls

e=controls.e
out=Path(__file__).resolve().parent
rows=[]
cases=[
 ('result.issues_scalar',('result','issues'),'bad-carrier'),
 ('result.rule_findings_number',('result','rule_findings'),42),
 ('review_report.issues_scalar',('review_report','issues'),'bad-carrier'),
 ('severity_bucket_wrong_type',('result','issues'),{'error':'lost-error-bucket','warn':[],'info':[],'all':[]}),
 ('non_object_finding_item',('result','issues'),['bad-finding-item']),
]
for name,path,value in cases:
 b=controls.payload();b['result']['issues']=[]
 a=copy.deepcopy(b);a[path[0]][path[1]]=value
 r=e.compare_payloads(b,a,controls.contract())
 row={'case':name,'expected':'pending/reject; no zero-regression claim for malformed present formal carrier',
      'input_after_value':value,'actual':r}
 rows.append(row)
 print(name,json.dumps({'verdict':r['verdict'],'zero_regression_claim':r.get('zero_regression_claim'),
       'damaged':r.get('damaged_carriers'),'after_readability':r.get('carrier_readability',{}).get('after')},ensure_ascii=False))
(out/'schema_type_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
