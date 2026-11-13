from pathlib import Path
from decimal import Decimal as D, ROUND_HALF_UP
import json,re
base=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/jgw_original_review_20261108')
def dec(x):
 x=(x or '').replace(',','').strip()
 return D(x) if re.fullmatch(r'-?\d+(\.\d+)?',x) else D(0) if not x else None
def clean(x):return re.sub(r'\s+','',x or '')
checks=[]
for doc,tspec in [('部门',[("T2",range(8,12),[2,3]),("T3",range(12,16),[2,3,4]),("T5",range(18,20),[2,3,4])]),('单位',[("T2",range(9,11),[2,3]),("T5",[13],[2,3,4])])]:
 tables=json.loads((base/doc/'independent_tables.json').read_text())
 for tab,pages,cols in tspec:
  rows={};total=None
  for p in pages:
   for row in tables[str(p)][0]:
    c=clean(row[0]); vals=[dec(row[i]) for i in cols]
    if vals.count(None):continue
    if re.fullmatch(r'\d{3}|\d{5}|\d{7}',c): rows[c]=(p,clean(row[1]),vals)
    elif c=='合计':total=(p,'合计',vals)
  candidates=[('合计',total)] if total else []
  candidates+=list(rows.items())
  for code,parent in candidates:
   kids=[(k,v) for k,v in rows.items() if (len(k)==3 if code=='合计' else k.startswith(code) and len(k)==len(code)+2)]
   # 单位材料只列项级；合计直接对所有项求和。
   if code=='合计' and not kids:kids=list(rows.items())
   if not kids:continue
   for n,col in enumerate(cols):
    stated=parent[2][n];s=sum((v[2][n] for k,v in kids),D(0));diff=stated-s
    if diff:
     checks.append(dict(document=doc,table=tab,parent=code,name=parent[1],page=parent[0],column=col,stated=str(stated),sum=str(s),difference=str(diff),children=[{'code':k,'page':v[0],'value':str(v[2][n])} for k,v in kids],rounding_envelope=str(max(D('.01'),D(len(kids)+1)*D('.005'))),exceeds=max(D('.01'),D(len(kids)+1)*D('.005'))<abs(diff)))
 # 经济分类；两边分别读取原件科目和金额。
 eco={}
 for p in ([20] if doc=='部门' else [14,15]):
  for row in tables[str(p)][0]:
   for start in [0,3]:
    code=clean(row[start]);v=dec(row[start+2])
    if re.fullmatch(r'\d{3}|\d{5}',code) and v is not None:eco[code]=(p,clean(row[start+1]),v)
 for parent in ['301','302','303','310']:
  kids=[(k,v) for k,v in eco.items() if k.startswith(parent) and len(k)==5]
  s=sum((v[2] for k,v in kids),D(0));diff=eco[parent][2]-s
  checks.append(dict(document=doc,table='T6',parent=parent,page=eco[parent][0],stated=str(eco[parent][2]),sum=str(s),difference=str(diff),exceeds=abs(diff)>max(D('.01'),D(len(kids)+1)*D('.005'))))
 # 独立从说明原文提取项目数，不读取系统 facts。
 pages=range(25,31) if doc=='部门' else range(20,23)
 text='\n'.join((base/doc/f'p{p:03}.txt').read_text() for p in pages)
 text=text.split('六、一般公共预算财政拨款基本支出')[0]
 chunks=re.split(r'^\s*(\d{1,2})[、.]\s*(?=[\u4e00-\u9fff])',text,flags=re.M)
 items=[]
 for i in range(1,len(chunks)-1,2):
  n=int(chunks[i]);s=re.sub(r'\s+','',chunks[i+1])
  b=re.search(r'年初预算为([\d,]+(?:\.\d+)?)万元',s)
  a=re.search(r'支出决算(?:为)?([\d,]+(?:\.\d+)?)万元',s)
  if b and a:items.append(dict(item=n,budget=str(dec(b[1])),actual=str(dec(a[1]))))
 (base/doc/'narrative_items_independent.json').write_text(json.dumps(items,ensure_ascii=False,indent=2))
 print(doc,'说明项目',len(items),'预算合计',sum((D(x['budget']) for x in items),D(0)),'决算合计',sum((D(x['actual']) for x in items),D(0)))
(base/'independent_arithmetic.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print('表内不等项/经济分类复算',len(checks),'超容差', [c for c in checks if c['exceeds']])
for doc,cur,change in [('部门财政收支总计','363002.52','205258.75'),('部门机关运行经费','126.48','-1.12'),('单位总收支','24699.82','7849.31'),('单位机关运行经费','127.97','-1.12')]:
 c=D(cur);d=D(change);prev=c-d;p=abs(d/prev*100)
 print(doc,'上年推导',prev,'百分比',p.quantize(D('.01'),rounding=ROUND_HALF_UP))
print('部门一般公共支出占比',(D('130544.80')/D('361773.67')*100).quantize(D('.01'),rounding=ROUND_HALF_UP))
print('单位人员分项',D('1233.16')+D('70.98'),'人员表差',D('1335.03')-D('1233.16')-D('70.98'),'基本推导',D('1335.03')+D('126.48'))
