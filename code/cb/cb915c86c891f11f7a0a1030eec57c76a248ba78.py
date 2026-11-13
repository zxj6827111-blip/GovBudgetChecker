"""Independent explicit carrier comparison; preserves missing/null and issue content.

Excludes only creation/update clocks and analysis_revision from issue projections.
Compares evidence, pages, amounts, IDs, severity and text; no semantic auto-waiver.
"""
import gzip
import json
from collections import Counter
from pathlib import Path

OUT=Path(__file__).resolve().parent
VOLATILE={'created_at','updated_at','analysis_revision'}
def read(p):return json.loads(gzip.decompress(p.read_bytes()))
def norm(x):
 if isinstance(x,dict):return {k:norm(v) for k,v in x.items() if k not in VOLATILE}
 if isinstance(x,list):return [norm(v) for v in x]
 return x
def canonical(x):return json.dumps(norm(x),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def carrier(s,path):
 v=s
 for k in path:
  if not isinstance(v,dict) or k not in v:return {'state':'missing','items':[]}
  v=v[k]
 if v is None:return {'state':'null','items':[]}
 if isinstance(v,list):return {'state':'list','items':v}
 if isinstance(v,dict):
  items=[]
  for k,l in v.items():
   if k!='all' and isinstance(l,list):items+=l
  return {'state':'dict','items':items,'buckets':{k:len(l) if isinstance(l,list) else 'invalid' for k,l in v.items()}}
 return {'state':'invalid','items':[]}
def diff(a,b):
 ca=Counter(map(canonical,a));cb=Counter(map(canonical,b))
 return {'before_count':len(a),'after_count':len(b),'removed':[json.loads(v) for v in (ca-cb).elements()],'added':[json.loads(v) for v in (cb-ca).elements()]}
def compare(batch):
 rows=[]
 for f in sorted((OUT/(batch+'_main')/'status').glob('*.gz')):
  sid=f.name.split('.status')[0];a=read(f);b=read(OUT/(batch+'_final')/'status'/f.name)
  row={'sample_id':sid,'checksum_equal':a['checksum']==b['checksum'],'sha256':a['checksum'],'carrier_comparisons':{}}
  for path in [('result','rule_findings'),('result','issues'),('review_report','issues')]:
   av=carrier(a,path);bv=carrier(b,path)
   row['carrier_comparisons']['.'.join(path)]={'before_state':av['state'],'after_state':bv['state'],**diff(av['items'],bv['items'])}
  am=a['result']['meta'];bm=b['result']['meta']
  def facts(m):return {str((x.get('rule_id'),x.get('sub_check_id'))):x.get('status') for x in m.get('fact_receipts',[]) if isinstance(x,dict)}
  def checks(m):return {str(x.get('rule_id')):x.get('status') for x in m.get('check_receipts',[]) if isinstance(x,dict)}
  for kind,fn in [('fact_status',facts),('rule_status',checks)]:
   ac=fn(am);bc=fn(bm);row[kind+'_differences']=[{'key':k,'before':ac.get(k),'after':bc.get(k)} for k in sorted(ac.keys()|bc.keys()) if ac.get(k)!=bc.get(k)]
  rows.append(row)
 return rows
results={b:compare(b) for b in ['gold','indep'] if (OUT/(b+'_main')/'meta.json').exists() and (OUT/(b+'_final')/'meta.json').exists()}
(OUT/'fresh_main_final_comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
for batch,rows in results.items():
 changed=[r for r in rows if any(c['added'] or c['removed'] for c in r['carrier_comparisons'].values())]
 print(batch,'samples',len(rows),'changed_findings',len(changed),'changed_rules',sum(bool(r['rule_status_differences']) for r in rows),'changed_relations',sum(bool(r['fact_status_differences']) for r in rows))
 for r in changed:print(r['sample_id'],{k:{'removed':len(c['removed']),'added':len(c['added'])} for k,c in r['carrier_comparisons'].items()},'rule_delta',r['rule_status_differences'])
