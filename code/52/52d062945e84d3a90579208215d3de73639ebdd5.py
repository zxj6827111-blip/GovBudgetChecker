import sys,json,hashlib,shutil,subprocess,re,time,csv
from pathlib import Path
from collections import Counter
import fitz
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021'
SRC=Path('/Users/zhangxingjin/Desktop/决算测试上海市/2022')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2))
write(OUT/'baseline/code_fingerprints.json',{str(p.relative_to(ROOT)):sha(p) for d in ['api','src','ocr_service','scripts','app/app','app/lib'] for p in (ROOT/d).rglob('*') if p.is_file() and p.suffix in ['.py','.ts','.tsx','.cjs','.js','.css']})
for name,args in [('git_status',['git','status','--porcelain=v1','-uall']),('git_head',['git','rev-parse','HEAD']),('listeners',['lsof','-nP','-iTCP','-sTCP:LISTEN'])]:
 (OUT/'baseline'/f'{name}.txt').write_text(subprocess.run(args,cwd=ROOT,capture_output=True,text=True).stdout)
write(OUT/'baseline/config_file_hashes.json',{str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'.env',ROOT/'models/MANIFEST.sha256',ROOT/'ocr_service/requirements.lock.txt',ROOT/'config/app.yaml',ROOT/'rules/v3_3.yaml'] if p.exists()})
old=json.loads((ROOT/'outputs/pdf_trusted_ingest_20261020/baseline/source_manifest.json').read_text())
known={r['sha256']:r for r in old}
# Known PDF bytes in development fixtures and samples, beyond the delivered 103 corpus.
for d in ['samples','tests/fixtures']:
 for p in (ROOT/d).rglob('*.pdf'):known.setdefault(sha(p),{'file':str(p.relative_to(ROOT))})
rows=[]
for i,p in enumerate(sorted(SRC.glob('*.pdf')),1):
 digest=sha(p);dst=OUT/'sources'/p.name;dst.parent.mkdir(exist_ok=True);shutil.copy2(p,dst);assert sha(dst)==digest
 pages=[];titles=[]
 with fitz.open(dst) as pdf:
  for n,page in enumerate(pdf,1):
   text=page.get_text();words=page.get_text('words');chars=len(text.strip());bad=text.count('\ufffd')+text.count('\x00')
   imgs=page.get_image_info();area=page.rect.width*page.rect.height
   image_area=sum(max(0,min(x['bbox'][2],page.rect.x1)-max(x['bbox'][0],page.rect.x0))*max(0,min(x['bbox'][3],page.rect.y1)-max(x['bbox'][1],page.rect.y0)) for x in imgs)/area
   sizes=[span['size'] for b in page.get_text('dict')['blocks'] if b['type']==0 for ln in b['lines'] for span in ln['spans']]
   lines=[ln for b in page.get_text('dict')['blocks'] if b['type']==0 for ln in b['lines']]
   kind='real_scan_candidate' if chars<20 and image_area>.5 else 'image_and_text' if chars>=20 and image_area>.25 else 'native' if chars>=20 else 'sparse_or_blank'
   if bad/max(1,chars)>.01:kind='bad_text_layer_candidate'
   rec={'page':n,'width_pt':page.rect.width,'height_pt':page.rect.height,'rotation':page.rotation,'chars':chars,'words':len(words),'images':len(imgs),'image_area_ratio':round(image_area,4),'replacement_chars':bad,'classification':kind,'small_font_min_pt':min(sizes) if sizes else None,'dense_small_text_candidate':bool(sizes and min(sizes)<8),'nonhorizontal_text_candidate':any(abs(ln.get('dir',[1,0])[1])>.03 for ln in lines),'vector_drawing_count':len(page.get_drawings())}
   pages.append(rec)
   if n<=3:titles.append(text)
  first='\n'.join(titles);years=re.findall(r'(20\d{2})\s*年',first)
  row={'doc_id':f'SH2022-{i:02d}','file':p.name,'source_path':str(p),'frozen_path':str(dst),'sha256':digest,'bytes':p.stat().st_size,'pages':len(pdf),'year':int(Counter(years).most_common(1)[0][0]) if years else None,'year_basis':'first_three_pages_text','region':'上海市','organization_filename':p.stem,'document_type':'final' if '决算' in first else 'budget' if '预算' in first else 'unknown','duplicate_of_development':known.get(digest),'page_classes':dict(Counter(x['classification'] for x in pages)),'human_verified':False,'annotation_complete':False}
  write(OUT/'page_inventory'/f'{digest}.json',{'document':row,'pages':pages,'first_three_pages_text':first})
  rows.append(row)
 print(i,p.name,len(pages),row['page_classes'],flush=True)
 write(OUT/'source_manifest.json',rows)
write(OUT/'intake_summary.json',{'documents':len(rows),'pages':sum(x['pages'] for x in rows),'development_duplicates':sum(bool(x['duplicate_of_development']) for x in rows),'page_classes':dict(sum((Counter(x['page_classes']) for x in rows),Counter())),'official_source_urls':'not_supplied','annotation_complete':False})
with (OUT/'source_manifest.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
