from pathlib import Path
import json,re,hashlib
from decimal import Decimal
import fitz
R=Path(__file__).parent;B=R.parents[1];P=B/'outputs/unified_closeout_round4_20261104/B1_review_package'
pdfs=json.loads((B/'outputs/gold_b1_20261103/run_20261103T150928/b1_pdfs.json').read_text())
leafs=json.loads((P/'t06_items.json').read_text());out={'leafs':{},'pdfs':{}}
for sid,d in leafs.items():
 doc=fitz.open(pdfs[sid]);out['pdfs'][sid]={'path':pdfs[sid],'sha256':hashlib.sha256(Path(pdfs[sid]).read_bytes()).hexdigest()}
 groups={}
 for col in ['人员经费','公用经费']:
  items=d[col]['items']; checks=[]
  for it in items:
   text=re.sub(r'\s+','',doc[it['page']-1].get_text()).replace(',','')
   needle=it['code']+re.sub(r'\s+','',it['name'])+it['value']
   checks.append(dict(it,found=needle in text))
  groups[col]={'count':len(items),'unique_codes':len({i['code'] for i in items}),'sum':str(sum((Decimal(i['value']) for i in items),Decimal(0))),'all_found':all(c['found'] for c in checks),'items':checks}
 out['leafs'][sid]=groups
spec=json.loads((P/'decimal_spec_v4.json').read_text());checks=[]
for row in spec['rows']:
 for src in row.get('operands',[])+[row.get('reported_source')]:
  if not src or src.get('source_kind')=='rule_constant':continue
  sid=row['sample_id'];text=re.sub(r'\s+','',fitz.open(pdfs[sid])[src['page']-1].get_text());q=re.sub(r'\s+','',src['quote'])
  checks.append({'sample':sid,'item':row['item'],'page':src['page'],'quote':src['quote'],'found':q in text})
out['decimal_quotes']={'total':len(checks),'all_found':all(c['found'] for c in checks),'items':checks}
sid='B1-2025-D01';doc=fitz.open(pdfs[sid]);pg=doc[18];folder=R/'pdf_original';folder.mkdir(exist_ok=True)
pg.get_pixmap(matrix=fitz.Matrix(1.4,1.4)).save(folder/(sid+'_p19.png'))
(folder/(sid+'_p19.txt')).write_text(pg.get_text())
out['t06_reported_vs_leaf']={'sample':sid,'page':19,'printed_personnel':'89230.64','printed_public':'17395.21','leaf_personnel':out['leafs'][sid]['人员经费']['sum'],'leaf_public':out['leafs'][sid]['公用经费']['sum'],'printed_sum':'106625.85','leaf_sum':'106625.89','table5_basic_total':'106625.87','public_leaf_minus_printed':'0.04','closure_problem':'leaf sum 17395.25 labelled printed 公用经费合计 instead of printed 17395.21; difference direction reversed'}
(R/'b1_source_audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({'leafs':{sid:{k:{a:v[a] for a in ['count','unique_codes','sum','all_found']} for k,v in groups.items()} for sid,groups in out['leafs'].items()},'decimal_quotes':{k:out['decimal_quotes'][k] for k in ['total','all_found']},'t06':out['t06_reported_vs_leaf']},ensure_ascii=False,indent=2))
