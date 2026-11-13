from pathlib import Path
from decimal import Decimal
import json,hashlib,importlib.util,re
import pymupdf
R=Path(__file__).parent;B=R.parents[1];P=B/'outputs/unified_closeout_round6_20261104/B1_review_package';OLD=B/'outputs/unified_closeout_round4_20261104/B1_review_package'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
m=load('r5_verifier',R/'delivery_tools/decimal_checks_v2.py')
ann={};same={}
for p in (P/'annotations_revised').glob('*/annotation.json'):
 a=json.loads(p.read_text());ann[a['sample_id']]=a;same[a['sample_id']]=sha(p)==sha(OLD/'annotations_revised'/p.parent.name/'annotation.json')
spec=json.loads((P/'decimal_spec_v4.json').read_text());ident=[];quotes=[];pdfs=json.loads((B/'outputs/gold_b1_20261103/run_20261103T150928/b1_pdfs.json').read_text());docs={k:pymupdf.open(p) for k,p in pdfs.items()}
for row in spec['rows']:
 values=m._annotation_values(ann[row['sample_id']]).get(row['check_id'],[])
 for role,source in [('operand',s) for s in row.get('operands',[])]+[('reported',row['reported_source'])]:
  value=m._dec(row['reported'] if role=='reported' else source['value'])
  ok,unit,reason=m._match_source(source,value,values,source.get('unit',''),role=role,kind=row.get('kind','amount'))
  ident.append({'sample':row['sample_id'],'item':row['item'],'role':role,'source_ok':ok,'reason':reason})
  if source.get('source_kind')!='rule_constant':
   text=re.sub(r'\s+','',docs[row['sample_id']][source['page']-1].get_text());quotes.append({'sample':row['sample_id'],'page':source['page'],'quote':source['quote'],'found':re.sub(r'\s+','',source['quote']) in text})
leaf=json.loads((P/'t06_items.json').read_text());closure=json.loads((P/'closure_evidence.json').read_text());chain_checks=[];printed_checks=[]
for sid,data in leaf.items():
 page=data['printed']['page'];text=re.sub(r'\s+','',docs[sid][page-1].get_text()).replace(',','');nums={k:Decimal(data['printed'][k]) for k in ['人员经费合计','公用经费合计']}
 printed_checks.append({'sample':sid,'page':page,'values':{k:str(v) for k,v in nums.items()},'found':all(k+str(v) in text for k,v in nums.items()),'sha256':sha(Path(pdfs[sid]))})
 sums={k:sum((Decimal(i['value']) for i in data[k]['items']),Decimal(0)) for k in ['人员经费','公用经费']}
 for row in closure['relations']:
  if row['sample_id']!=sid or row['target']!='T06':continue
  if row['code']=='leaf_pub_vs_printed':left=sums['公用经费'];right=nums['公用经费合计'];n=len(data['公用经费']['items'])
  elif row['code']=='printed_sum_vs_table5':left=sum(nums.values(),Decimal(0));right=Decimal(row['total']);n=2
  elif row['code']=='leaf_total_vs_table5':left=sum(sums.values(),Decimal(0));right=Decimal(row['total']);n=sum(len(data[k]['items']) for k in sums)
  else:raise AssertionError(row)
  tol=(Decimal(n)+1)*Decimal('.005');within=abs(left-right)<=tol
  assert n==row['operand_count'] and Decimal(row['tolerance'])==tol and row['within_tolerance']==within
  if not within:assert row['verdict']=='PENDING_REVIEW'
  chain_checks.append({'sample':sid,'code':row['code'],'signed_difference':str(left-right),'n':n,'tolerance':str(tol),'verdict':row['verdict'],'ok':True})
result={'annotations_unchanged_from_round4':same,'spec_unchanged_from_round4':sha(P/'decimal_spec_v4.json')==sha(OLD/'decimal_spec_v4.json'),'identity_sources':len(ident),'identity_all_ok':all(x['source_ok'] for x in ident),'identity_checks':ident,'original_quotes':len(quotes),'all_quotes_found':all(x['found'] for x in quotes),'quotes':quotes,'printed_checks':printed_checks,'chain_checks':chain_checks}
(R/'b1_independent_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
core=[]
for p in (R/'delivery_tools').iterdir():
 q=B/'outputs/unified_closeout_round6_20261104/tools'/p.name
 if p.is_file() and q.is_file():core.append({'name':p.name,'matches_commit':sha(q)==sha(p)})
(R/'core_tool_binding.json').write_text(json.dumps(core,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ['identity_checks','quotes']},ensure_ascii=False,indent=2));print('core tool mismatches', [x for x in core if not x['matches_commit']])
