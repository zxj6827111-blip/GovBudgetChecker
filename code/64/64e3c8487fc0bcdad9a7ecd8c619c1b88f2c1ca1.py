"""原始PDF网格对照：运行前不导入产品解析器，金额列与合并标签分别处理。"""
import json, re, hashlib, shutil, subprocess
from pathlib import Path
from datetime import datetime, timezone
import pdfplumber
BASE=Path(__file__).resolve().parent
manifest=json.loads((BASE/'sample_manifest.json').read_text())
refs=[];fields=[];notes=[]
NUM=re.compile(r'[\d,]+(?:\.\d+)?')
def compact(x):return re.sub(r'\s+','',str(x or ''))
def record(item,code,row,col,page,indicator,label,phase='actual'):
 value=str(row[col] or '').strip()
 if value and not NUM.fullmatch(value) and value not in {'-','—','–','－'}:raise ValueError((item['sample_id'],label,value))
 fields.append({'sample_id':item['sample_id'],'table_code':code,'indicator':indicator,'subject_code':'total','phase':phase,'value_wan':value.replace(',','') if NUM.fullmatch(value) else None,'source_marker':'dash' if value in {'-','—','–','－'} else ('blank' if not value else 'number'),'page':page,'evidence_label':label,'source_col_idx':col,'raw_original_row':row})
for item in manifest['selected']:
 assert hashlib.sha256(Path(item['source_pdf']).read_bytes()).hexdigest()==item['sha256']
 with pdfplumber.open(item['source_pdf']) as pdf:
  entry={**item,'pages':len(pdf.pages),'text_pages':0,'cover':'','tables':[]}
  for number,page in enumerate(pdf.pages,1):
   text=page.extract_text() or '';entry['text_pages']+=bool(text.strip())
   if number==1:entry['cover']=text
   literal=compact(text);lines=[compact(l) for l in text.splitlines()];code=None
   if '支出决算表' in lines:code='FIN_03_expenditure'
   elif '一般公共预算财政拨款基本支出决算表' in lines or '人员经费合计' in literal:code='FIN_06_basic_expenditure'
   elif any(re.fullmatch(r'(?:一般公共预算)?财政拨款[“”"]*三公[“”"]*经费支出决算表',l) for l in lines):code='FIN_07_three_public'
   elif any(l in ['政府性基金预算财政拨款收入支出决算表','国有资本经营预算财政拨款收入支出决算表'] for l in lines):code='FIN_08_gov_fund' if '政府性基金' in literal else 'FIN_09_state_capital'
   if number==1 or code:
    target=BASE/'originals'/item['sample_id']/f'page_{number:02}.png';target.parent.mkdir(parents=True,exist_ok=True)
    poppler=shutil.which('pdftoppm')
    if poppler:subprocess.run([poppler,'-r','90','-f',str(number),'-l',str(number),'-singlefile','-png',item['source_pdf'],str(target.with_suffix(''))],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:page.to_image(resolution=90).save(str(target))
   if not code:continue
   tables=page.extract_tables();entry['tables'].append({'page':number,'code':code,'raw_text':text,'raw_grids':tables})
   for rows in tables:
    if code=='FIN_03_expenditure':
     mapping={}
     for indicator,label in [('total_actual','本年支出合计'),('basic_actual','基本支出'),('project_actual','项目支出')]:
      columns={col for row in rows[:5] for col,v in enumerate(row) if compact(v)==label}
      if len(columns)==1:mapping[indicator]=next(iter(columns))
     if len(mapping)!=3:notes.append({'sample_id':item['sample_id'],'page':number,'reason':'原始网格未唯一给出支出表三列标题'});continue
     for row in rows:
      if any(compact(v)=='合计' for col,v in enumerate(row) if col<min(mapping.values())):
       for indicator,col in mapping.items():record(item,code,row,col,number,indicator,'合计')
    elif code=='FIN_06_basic_expenditure':
     for row in rows:
      for col,v in enumerate(row):
       label=compact(v)
       if label not in ('人员经费合计','公用经费合计'):continue
       next_label=next((j for j in range(col+1,len(row)) if '经费合计' in compact(row[j])),len(row))
       money_col=next_label-1
       if money_col<=col:raise ValueError((item['sample_id'],'footer group ambiguous'))
       record(item,code,row,money_col,number,'total_actual',label)
    elif code=='FIN_07_three_public':
     for row in rows[::-1]:
      if len(row)==12 and all(not str(v or '').strip() or NUM.fullmatch(str(v or '').strip()) or str(v or '').strip() in {'-','—','–','－'} for v in row):
       for col,v in enumerate(row):
        indicator=('total_actual','overseas','vehicle_purchase_operation','vehicle_purchase','vehicle_operation','reception')[col//2]
        record(item,code,row,col,number,indicator,indicator,'actual' if col%2 else 'budget_unqualified')
       break
  refs.append(entry)
 print(item['sample_id'],entry['pages'],sum(f['sample_id']==item['sample_id'] for f in fields),'fields',flush=True)
(BASE/'original_reference.json').write_text(json.dumps(refs,ensure_ascii=False,indent=2))
(BASE/'reference_notes.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2))
p=BASE/'frozen_truth.json';p.write_text(json.dumps({'scope':'Original raw pdfplumber grids and page renders recorded before before/after product-engine result inspection; extraction library shared, not independent human-signed blind accuracy benchmark','frozen_at':datetime.now(timezone.utc).isoformat(),'fields':fields},ensure_ascii=False,indent=2));(BASE/'truth_sha256.txt').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
