"""只读按哈希去重本地PDF，记录质量线索；不运行历史分析、不生成验收真值。"""
from pathlib import Path
import hashlib,json,time
from collections import Counter
import fitz
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');out=ROOT/'artifacts/pdf-input-v2-20261018-orientation'
paths=(out/'local-pdf-all-paths.txt').read_text().splitlines();unique={};errors=[]
for name in paths:
 p=ROOT/name
 try:
  data=p.read_bytes();sha=hashlib.sha256(data).hexdigest()
  unique.setdefault(sha,{'sha256':sha,'paths':[],'bytes':len(data)})['paths'].append(name)
 except Exception as exc:errors.append({'path':name,'error':type(exc).__name__})
records=[];start=time.monotonic()
for sha,record in unique.items():
 p=ROOT/record['paths'][0]
 try:
  with fitz.open(p) as pdf:
   record['page_count']=len(pdf);pages=[]
   for i,page in enumerate(pdf):
    text=page.get_text();images=page.get_images(full=True)
    fonts=[span['size'] for block in page.get_text('dict')['blocks'] if 'lines' in block for line in block['lines'] for span in line['spans'] if len(span.get('text','').strip())>=3]
    pages.append({'page':i+1,'text_chars':len(text.strip()),'images':len(images),'rotation':page.rotation,
                  'font_min':min(fonts) if fonts else None,'font_median':sorted(fonts)[len(fonts)//2] if fonts else None,
                  'replacement_chars':text.count('\ufffd'),'cid_markers':text.count('(cid:')})
   record['pure_image_pages']=[r['page'] for r in pages if not r['text_chars'] and r['images']]
   record['mixed_candidate_pages']=[r['page'] for r in pages if r['text_chars']>30 and r['images']]
   record['decode_anomaly_candidate_pages']=[r['page'] for r in pages if r['replacement_chars'] or r['cid_markers']]
   record['dense_candidate_pages']=[r['page'] for r in pages if r['text_chars']>=1000 and r['font_median'] and r['font_median']<=8]
   record['pages']=pages;record['acceptance_status']='unreviewed_quality_clues_only'
 except Exception as exc:record['error']=type(exc).__name__
 records.append(record)
 (out/'local-original-quality-progress.json').write_text(json.dumps({'processed':len(records),'total_unique':len(unique),'seconds':time.monotonic()-start,'records':records,'errors':errors},ensure_ascii=False,indent=2))
summary={'scope':'readonly_local_quality_inventory_no_historical_reanalysis_no_acceptance','files':len(paths),'unique_pdf_hashes':len(unique),
         'scan_candidates':[{'sha256':r['sha256'],'path':r['paths'][0],'pages':r.get('page_count'),'pure_image_pages':r.get('pure_image_pages')} for r in records if len(r.get('pure_image_pages',[]))>=3],
         'decode_anomaly_candidates':[{'sha256':r['sha256'],'path':r['paths'][0],'pages':r['decode_anomaly_candidate_pages']} for r in records if r.get('decode_anomaly_candidate_pages')],
         'dense_candidates':[{'sha256':r['sha256'],'path':r['paths'][0],'pages':r['dense_candidate_pages']} for r in records if r.get('dense_candidate_pages')],
         'seconds':time.monotonic()-start,'records':records,'errors':errors}
(out/'local-original-quality-inventory.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k not in ('records','errors','dense_candidates')},ensure_ascii=False),flush=True)
