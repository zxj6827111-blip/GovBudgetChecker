import json,re
from pathlib import Path
import pymupdf as fitz
from PIL import Image,ImageDraw
root=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
rows=json.loads((root/'source_manifest.json').read_text());records=[];meta=[]
for r in rows:
 with fitz.open(r['frozen_path']) as doc:
  cover=doc[0].get_text();plain=re.sub(r'\s+','',cover)
  meta.append({'doc_id':r['doc_id'],'source_sha256':r['sha256'],'year':2022,'organization':r['organization_filename'],'document_type':'budget' if '预算' in plain else 'unknown','basis':'cover_text','cover_text':cover,'human_verified':False})
  if int(r['doc_id'][-2:]) not in {2,15,20,33,46,1,9}:continue
  pages=[]
  for i,p in enumerate(doc):
   t=re.sub(r'\s+','',p.get_text())
   if i>2 and any(s in t for s in ['财务收支预算总表','收入预算总表','支出预算总表','基本支出部门预算经济分类','经费和机关运行经费预算表','一般公共预算支出功能分类']):pages.append(i)
  if r['doc_id'].endswith('46'):pages=[0,6,7,8,9,10,11,12,13,14,15]
  pages=list(dict.fromkeys(pages))[:12];folder=root/'budget_table_renders'/r['doc_id'];folder.mkdir(parents=True,exist_ok=True);thumbs=[]
  for i in pages:
   pix=doc[i].get_pixmap(matrix=fitz.Matrix(1.8,1.8).prerotate(90 if r['doc_id'].endswith('46') else 0))
   path=folder/f'page-{i+1}.png';pix.save(path)
   records.append({'doc_id':r['doc_id'],'file':r['file'],'sha256':r['sha256'],'page':i+1,'path':str(path),'render_rotation':90 if r['doc_id'].endswith('46') else 0})
   im=Image.open(path).convert('RGB');im.thumbnail((560,400));thumb=Image.new('RGB',(580,430),'white');thumb.paste(im,((580-im.width)//2,10));ImageDraw.Draw(thumb).text((10,413),f"{r['doc_id']} page {i+1}",fill='black');thumbs.append(thumb)
  if thumbs:
   sheet=Image.new('RGB',(580*min(2,len(thumbs)),430*((len(thumbs)+1)//2)),(220,220,220))
   for j,im in enumerate(thumbs):sheet.paste(im,((j%2)*580,(j//2)*430))
   sheet.save(folder/'contact.png')
# Visual cover inspection of the real scan supplies auxiliary identity; never human truth.
meta[45].update(document_type='budget',year=2022,basis='model_visual_cover_auxiliary',visual_evidence=str(root/'budget_table_renders/SH2022-46/page-1.png'))
(root/'verified_metadata_auxiliary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
(root/'budget_table_renders/index.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));print('rendered',len(records))
