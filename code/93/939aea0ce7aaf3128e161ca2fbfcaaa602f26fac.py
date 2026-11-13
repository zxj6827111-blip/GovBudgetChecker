"""从PDF重新提取的表格按原列头与科目定位缺数主张；不把空白推成0。"""
from pathlib import Path
import collections,json,re
ROOT=Path(__file__).resolve().parents[1];R3=ROOT.parents[1]/'outputs/unified_review_remediation_r3_20261106'
tables=json.loads((ROOT/'independent_pdf_tables.json').read_text())
def norm(s):return re.sub(r'\s+','',str(s or ''))
income={'total_actual':'本年收入合计','fiscal_allocation':'财政拨款收入','superior_subsidy_income':'上级补助收入','business_income':'事业收入','operational_income':'经营收入','subsidiary_income':'附属单位上缴收入','other_income':'其他收入'}
expense={'total_actual':'本年支出合计','basic_actual':'基本支出','project_actual':'项目支出','to_superior_actual':'上缴上级支出','operational_actual':'经营支出','subsidiary_actual':'对附属单位补助支出'}
sg={'total_actual':0,'overseas':2,'vehicle_purchase_operation':4,'vehicle_purchase':6,'vehicle_operation':8,'reception':10}
def walk(v):
 if isinstance(v,dict):
  yield v
  for x in v.values():yield from walk(x)
 elif isinstance(v,list):
  for x in v:yield from walk(x)
def locate(sid,m):
 table=m.get('table_code');indicator=m.get('column');rowkey=m.get('row_key');stage=m.get('stage');page=m.get('page')
 selected=[p for p in tables[sid] if p['page']==page]
 if len(selected)!=1:return {'status':'UNRESOLVED_PAGE_OR_TEXT_CLAIM'}
 observed=[]
 for t in selected[0]['tables']:
  rows=t['rows']
  if table=='FIN_07_three_public' and indicator in sg:
   # 标准12列三公表的四层表头，直接使用最后一条数据行；不拿回执值作真值。
   if len(rows[-1])==12 and any('决算数' in norm(c) for row in rows[:-1] for c in row):
    col=sg[indicator]+(stage=='决算');observed.append({'page':page,'raw_text':rows[-1][col],'column':col,'header':{'indicator':indicator,'stage':stage},'bbox_table':t['bbox']})
  elif table in ['FIN_02_income','FIN_03_expenditure']:
   mapping=income if table=='FIN_02_income' else expense;label=mapping.get(indicator)
   if not label:continue
   matching=[(ri,ci) for ri,row in enumerate(rows[:8]) for ci,v in enumerate(row) if norm(v)==label]
   for ri,ci in matching:
    candidates=[]
    for row in rows[ri+1:]:
     before=[norm(c) for c in row[:ci] if norm(c)]
     if ('合计' in before if rowkey=='total' else str(rowkey) in before):
      candidates.append(row)
    if len(candidates)==1:observed.append({'page':page,'raw_text':candidates[0][ci],'column':ci,'header':label,'row_key':rowkey,'bbox_table':t['bbox']})
 if len(observed)==1:
  v=observed[0]['raw_text'];return {'status':'ORIGINAL_CELL_BLANK' if not norm(v) or norm(v) in ['-','—'] else 'ORIGINAL_CELL_HAS_TEXT','observation':observed[0]}
 return {'status':'UNRESOLVED_GRID_ALIGNMENT','candidates':observed}
for fn in ['original9_objects.json','r2_current35_objects.json']:
 rows=[]
 for i,o in enumerate(json.loads((R3/fn).read_text())['objects']):
  sid=o.get('sample_id',o.get('origin_group'));claims={}
  for v in walk([o.get('old'),o.get('new')]):
   if v.get('source')=='table' and v.get('column') and v.get('row_key'):
    k=tuple(v.get(x) for x in ['table_code','page','row_key','column','stage']);claims[k]=v
  checks=[{'claim':v,'independent_pdf_check':locate(sid,v)} for v in claims.values()]
  x=o.get('new') or o.get('old') or {}
  rows.append({'index':i,'sample_id':sid,'title':x.get('title'),'direction':'add' if o.get('old') is None else 'remove' if o.get('new') is None else 'change','table_claims':checks,'summary':dict(collections.Counter(c['independent_pdf_check']['status'] for c in checks)),'original_semantic_approval':'NOT_CREATED','limit':'仅核表格缺数观察；文字、聚合数量、身份、全部关系前后变化仍需逐对象语义裁决。'})
 (ROOT/fn.replace('.json','_missing_claim_review.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 print(fn,dict(collections.Counter(c['independent_pdf_check']['status'] for r in rows for c in r['table_claims'])))
