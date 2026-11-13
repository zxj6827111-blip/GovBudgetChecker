import csv
import hashlib
import json
from pathlib import Path

import fitz
import pdfplumber
from PIL import Image, ImageDraw

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'outputs/fp_batch1_20261103/reviewer_codex_01a19f67'
KIT=ROOT/'outputs/pending_review_20261101/acceptance_kit/v1-regression-r4'
DOCS=Path('/Volumes/workSSD/software development/gbc-worktrees/fp-batch1-20261103/docs/fp_batch1_20261103')
samples=[]
for name in ['nongold_sample20.csv','nongold_sample_rare_classes.csv']:
    samples.extend(csv.DictReader((DOCS/name).open(encoding='utf-8-sig')))
manifest={x['sample_id']:x for name in ['manifest_RA.json','manifest_RB.json']
          for x in json.loads((KIT/name).read_text())['samples']}
pages=OUT/'sample_pages'
pages.mkdir(exist_ok=False)
records=[]
rendered={}
for index,row in enumerate(samples,1):
    sid=row['sample_id'];m=manifest[sid];pdf=KIT/m['pdf']
    assert hashlib.sha256(pdf.read_bytes()).hexdigest()==m['sha256']
    entry={'index':index,**row,'pdf':str(pdf),'sha256_verified':True,'page_texts':{}}
    with fitz.open(pdf) as doc,pdfplumber.open(pdf) as text_doc:
        for page in map(int,row['pages'].split()):
            key=(sid,page)
            if key not in rendered:
                png=pages/f'{sid}_p{page:03}.png'
                doc[page-1].get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(png)
                rendered[key]=png
            text=text_doc.pages[page-1].extract_text(layout=False) or ''
            entry['page_texts'][str(page)]=text
            (pages/f'{sid}_p{page:03}.txt').write_text(text)
    records.append(entry)
(OUT/'sample_original_pages.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
images=list(rendered.items())
for offset in range(0,len(images),6):
    sheet=Image.new('RGB',(1800,1770),'#d7d7d7')
    draw=ImageDraw.Draw(sheet)
    for j,((sid,page),png) in enumerate(images[offset:offset+6]):
        im=Image.open(png).convert('RGB');im.thumbnail((590,845))
        x=(j%3)*600+(600-im.width)//2;y=(j//3)*885+30
        sheet.paste(im,(x,y));draw.text(((j%3)*600+5,(j//3)*885+5),f'{sid} physical p{page}',fill='black')
    sheet.save(pages/f'contact_{offset//6+1:02}.png')
print(json.dumps({'sample_rows':len(records),'unique_pages':len(images),'contact_sheets':(len(images)+5)//6},ensure_ascii=False))
