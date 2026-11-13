import json,sys,hashlib,re
from pathlib import Path
from datetime import datetime, timezone
import pdfplumber
BASE=Path(__file__).resolve().parent
manifest=json.loads((BASE/'holdout_manifest.json').read_text())
refs=[];fields=[]
for item in manifest['selected']:
 with pdfplumber.open(item['source_pdf']) as pdf:
  entry={**item,'pages':len(pdf.pages),'text_pages':0,'cover':'','tables':[]}
  for number,page in enumerate(pdf.pages,1):
   text=page.extract_text() or '';entry['text_pages']+=bool(text.strip())
   if number==1:entry['cover']=text
   compact=re.sub(r'\s+','',text);code=None
   lines=[re.sub(r'\s+','',l) for l in text.splitlines()]
   if '支出决算表' in lines:code='FIN_03_expenditure'
   elif any(l=='一般公共预算财政拨款基本支出决算表' for l in lines) or '人员经费合计' in compact:code='FIN_06_basic_expenditure'
   elif any(re.fullmatch(r'(?:一般公共预算)?财政拨款[“”"\s]*三公[“”"\s]*经费支出决算表',l) for l in lines):code='FIN_07_three_public'
   if number==1 or code:
    target=BASE/'holdout_originals'/item['sample_id']/f'page_{number:02}.png';target.parent.mkdir(parents=True,exist_ok=True)
    page.to_image(resolution=90).save(str(target))
   if not code:continue
   tables=page.extract_tables()
   entry['tables'].append({'page':number,'code':code,'raw_text':text,'raw_grids':tables})
   for rows in tables:
    if code=='FIN_03_expenditure':
     for row in rows:
      if row and re.sub(r'\s+','',str(row[0] or ''))=='合计':
       values=[(col,str(v or '').strip()) for col,v in enumerate(row) if re.fullmatch(r'[\d,]+(?:\.\d+)?',str(v or '').strip())]
       if len(values)>=3:
        for indicator,(col,value) in zip(('total_actual','basic_actual','project_actual'),values[:3]):
         fields.append({'sample_id':item['sample_id'],'table_code':code,'indicator':indicator,'subject_code':'total','phase':'actual','value_wan':value.replace(',',''),'page':number,'evidence_label':'合计','source_col_idx':col})
    elif code=='FIN_06_basic_expenditure':
     for row in rows:
      for col,v in enumerate(row):
       label=re.sub(r'\s+','',str(v or ''))
       if label in ('人员经费合计','公用经费合计') and col+1<len(row):
        value=str(row[col+1] or '').strip()
        if value and not re.fullmatch(r'[\d,]+(?:\.\d+)?',value):raise ValueError((item['sample_id'],value))
        fields.append({'sample_id':item['sample_id'],'table_code':code,'indicator':'total_actual','subject_code':'total','phase':'actual','value_wan':value.replace(',','') or None,'page':number,'evidence_label':label,'source_col_idx':col+1})
    else:
     for row in rows[::-1]:
      if len(row)==12 and all(not str(v or '').strip() or re.fullmatch(r'[\d,]+(?:\.\d+)?',str(v or '').strip()) for v in row):
       for col,v in enumerate(row):
        indicator=('total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception')[col//2]
        fields.append({'sample_id':item['sample_id'],'table_code':code,'indicator':indicator,'subject_code':'total','phase':'actual' if col%2 else 'budget_unqualified','value_wan':str(v or '').strip().replace(',','') or None,'page':number,'evidence_label':indicator,'source_col_idx':col})
       break
  refs.append(entry)
 print(item['sample_id'],entry['pages'],{t['code'] for t in entry['tables']},sum(f['sample_id']==item['sample_id'] for f in fields),flush=True)
(BASE/'holdout_source_reference.json').write_text(json.dumps(refs,ensure_ascii=False,indent=2))
p=BASE/'holdout_frozen_truth.json'
p.write_text(json.dumps({'scope':'Independent raw pdfplumber grids, original page renders; frozen before production engine holdout outputs; not human-signed accuracy benchmark','frozen_at':datetime.now(timezone.utc).isoformat(),'fields':fields},ensure_ascii=False,indent=2))
(BASE/'holdout_truth_sha256.txt').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
