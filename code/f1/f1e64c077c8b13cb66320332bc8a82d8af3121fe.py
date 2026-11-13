import json,re
from pathlib import Path
import pymupdf as fitz
from PIL import Image,ImageDraw
root=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
rows=json.loads((root/'source_manifest.json').read_text());selection={2,15,20,33,46,1,9}
out=root/'original_renders';out.mkdir(exist_ok=True)
records=[]
for r in rows:
 if int(r['doc_id'][-2:]) not in selection:continue
 doc=fitz.open(r['frozen_path']);pages=[]
 for i,p in enumerate(doc):
  t=re.sub(r'\s+','',p.get_text())
  if i and any(s in t for s in ['收入支出决算总表','收入决算表','支出决算表','基本支出决算表','经费支出决算表']) and len(t)<4000:pages.append(i)
 if r['doc_id'].endswith('46'):pages=[0,4,5,6,7,8,9,10,11,12]
 pages=list(dict.fromkeys(pages))[:12]
 folder=out/r['doc_id'];folder.mkdir(exist_ok=True)
 for i in pages:
  pix=doc[i].get_pixmap(dpi=130);p=folder/f'page-{i+1}.png';pix.save(p)
  records.append({'doc_id':r['doc_id'],'file':r['file'],'sha256':r['sha256'],'page':i+1,'path':str(p),'width_pt':doc[i].rect.width,'height_pt':doc[i].rect.height,'dpi':130})
 thumbs=[]
 for i in pages:
  im=Image.open(folder/f'page-{i+1}.png').convert('RGB');im.thumbnail((440,620));thumb=Image.new('RGB',(460,650),'white');thumb.paste(im,((460-im.width)//2,20));ImageDraw.Draw(thumb).text((10,630),f"{r['doc_id']} page {i+1}",fill='black');thumbs.append(thumb)
 if thumbs:
  sheet=Image.new('RGB',(460*min(3,len(thumbs)),650*((len(thumbs)+2)//3)),(220,220,220))
  for j,im in enumerate(thumbs):sheet.paste(im,((j%3)*460,(j//3)*650))
  sheet.save(out/f"{r['doc_id']}-contact.png")
(root/'original_renders/index.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
print('rendered',len(records))
