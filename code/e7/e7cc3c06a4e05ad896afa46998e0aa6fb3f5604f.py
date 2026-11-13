from pathlib import Path
from decimal import Decimal
import csv,json,re,pdfplumber,hashlib
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/comprehensive_review_closure_20261109'
PDF=ROOT/'uploads/23734f26696d2271a1e7246d80329a51/2025年石泉路街道办事处部门决算公开.pdf'
fields=['total_actual','basic_actual','project_actual','to_superior_actual','operational_actual','subsidiary_actual'];rows=[]
with pdfplumber.open(PDF) as pdf:
 assert '万元' in pdf.pages[11].extract_text()
 for number in range(12,17):
  page=pdf.pages[number-1]
  for table in page.find_tables():
   extracted=table.extract()
   for rn,row in enumerate(extracted):
    code=re.sub(r'\s+','',str(row[0] or ''))
    if code!='合计' and not re.fullmatch(r'\d{3}(?:\d{2}){0,2}',code):continue
    assert len(row)==8,(number,row)
    values={};blank=[]
    for col,field in enumerate(fields,2):
     raw=row[col];box=table.rows[rn].cells[col];assert box is not None,(number,code,field)
     if raw=='':
      assert not any(str(c.get('text') or '').strip() and box[0]<(c['x0']+c['x1'])/2<box[2] and box[1]<(c['top']+c['bottom'])/2<box[3] for c in page.chars)
      assert not any(min(i['x1'],box[2])>max(i['x0'],box[0]) and min(i['bottom'],box[3])>max(i['top'],box[1]) for i in page.images)
      values[field]=Decimal(0);blank.append(field)
     else:
      assert raw is not None and re.fullmatch(r'[\d,]+(?:\.\d+)?',raw),raw
      values[field]=Decimal(raw.replace(',',''))
    rows.append(dict(page=number,code='total' if code=='合计' else code,name=re.sub(r'\s+','',str(row[1] or '合计')),values=values,blank=blank))
assert len({r['code'] for r in rows})==len(rows)
lookup={r['code']:r for r in rows};classes=[r for r in rows if len(r['code'])==3];checks=[]
for field in fields:
 expected=sum((r['values'][field] for r in classes),Decimal(0));actual=lookup['total']['values'][field]
 checks.append(dict(kind='column',field=field,actual=actual,computed=expected,difference=actual-expected))
for row in rows:
 expected=sum((row['values'][f] for f in fields[1:]),Decimal(0));checks.append(dict(kind='row',code=row['code'],page=row['page'],actual=row['values']['total_actual'],computed=expected,difference=row['values']['total_actual']-expected))
 for field in fields:
  children=[r for r in rows if len(r['code'])==len(row['code'])+2 and r['code'].startswith(row['code'])]
  if children:
   expected=sum((r['values'][field] for r in children),Decimal(0));checks.append(dict(kind='hierarchy',code=row['code'],page=row['page'],field=field,actual=row['values'][field],computed=expected,difference=row['values'][field]-expected))
leaf=[row for row in rows if row['code']!='total' and not any(x['code']!=row['code'] and x['code'].startswith(row['code']) for x in rows)]
result=dict(source_sha256=hashlib.sha256(PDF.read_bytes()).hexdigest(),rows=rows,class_count=len(classes),row_count=len(rows),checks=checks,leaf_total_actual=sum((x['values']['total_actual'] for x in leaf),Decimal(0)))
(OUT/'independent_original_reconciliation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
with (OUT/'原件三列逐行复算.csv').open('w',encoding='utf-8-sig',newline='') as f:
 writer=csv.writer(f);writer.writerow(['物理页','科目编码','科目名称','本年支出合计','基本支出','项目支出','其他三项合计','行差额','原件空白字段'])
 for row in rows:
  v=row['values'];writer.writerow([row['page'],row['code'],row['name'],v['total_actual'],v['basic_actual'],v['project_actual'],sum((v[x] for x in fields[3:]),Decimal(0)),v['total_actual']-sum((v[x] for x in fields[1:]),Decimal(0)),';'.join(row['blank'])])
print('rows',len(rows),'classes',len(classes),'leaf_total',result['leaf_total_actual'])
for c in checks:
 if c['kind']=='column' or abs(c['difference'])>Decimal('.05'):print(c)
