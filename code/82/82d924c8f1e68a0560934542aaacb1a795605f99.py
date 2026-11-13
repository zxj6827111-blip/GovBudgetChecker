import pymupdf,json,re
from pathlib import Path
base=Path('/Users/zhangxingjin/Desktop/决算测试上海市')
out=Path('outputs/real_intake_review_20261008/numeric_source')
num_re=re.compile(r'^-?[\d,]+(?:\.\d+)?$')
code_re=re.compile(r'^\d{3}(?:\d{2}){0,2}$')
keys=['收入支出决算总表','收入决算表','支出决算表','财政拨款收入支出决算总表','一般公共预算财政拨款支出决算表','一般公共预算财政拨款基本支出决算表','财政拨款“三公”经费支出决算表','政府性基金预算财政拨款收入支出决算表','国有资本经营预算财政拨款收入支出决算表']
all_result={}
for name in ['上海市交通委员会','上海市文化和旅游局','上海市民政局','上海市财政局']:
 d=pymupdf.open(base/(name+'.pdf'));current='';groups={}
 for i,p in enumerate(d):
  txt=p.get_text().replace(' ','').replace('\n','')
  if '第三部分' in txt and i>10:break
  if i<7:continue
  found=[k for k in keys if k in txt]
  if found:current=max(found,key=len)
  if current not in [keys[1],keys[2],keys[4],keys[5],keys[7],keys[8]]:continue
  for tab in p.find_tables().tables:
   for rn,row in enumerate(tab.extract()):
    def take(vals):
     if len(vals)<3 or not code_re.fullmatch(str(vals[0])):return
     nums=[]
     for v in vals[2:]:
      s=(v or '').replace(' ','').replace('\n','')
      if num_re.fullmatch(s):nums.append(float(s.replace(',','')))
      elif not s:nums.append(0.0)
      else:return
     cells=tab.rows[rn].cells
     groups.setdefault(current,[]).append({'page':i+1,'code':vals[0],'name':vals[1],'values':nums,'bbox':[round(v,2) for v in cells[0]] if cells[0] else None})
    if current==keys[5] and len(row)==6:take(row[:3]);take(row[3:])
    else:take(row)
 diffs=[]
 for k,rows in groups.items():
  for r in rows:
   vals=r['values']
   if k in [keys[1],keys[2],keys[4]] and len(vals)>1:
    dif=vals[0]-sum(vals[1:])
    if abs(dif)>.5:diffs.append({'kind':'row_sum','table':k,**r,'difference':dif})
   if len(r['code']) in [3,5]:
    children=[c for c in rows if c['code'].startswith(r['code']) and len(c['code'])==len(r['code'])+2]
    if children:
     for col,v in enumerate(vals):
      if not all(len(c['values'])==len(vals) for c in children):continue
      total=sum(c['values'][col] for c in children);dif=v-total
      if abs(dif)>.5:diffs.append({'kind':'hierarchy_sum','table':k,**r,'col':col,'children':children,'expected':total,'difference':dif})
 all_result[name]={'tables':groups,'differences':diffs}
 print(name,[(x['kind'],x['table'],x['page'],x['code'],round(x['difference'],2)) for x in diffs])
(out/'independent_tables.json').write_text(json.dumps(all_result,ensure_ascii=False,indent=2))
