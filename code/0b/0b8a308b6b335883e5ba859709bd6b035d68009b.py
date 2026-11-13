import sys,json,copy,hashlib
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
import pymupdf as fitz
import pdfplumber
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1
out=Path('/private/tmp/gbc-r6-results-20261111');pdf=out/'year_amount_source.pdf'
source=[['本年收入合计','3000','本年支出合计','2800'],['使用非财政拨款结余','0','结余分配','0'],['年初结转和结余','2025','年末结转和结余','2225'],['总计','5025','总计','5025']]
doc=fitz.open();pg=doc.new_page();pg.insert_font(fontname='CJK',fontfile='/System/Library/Fonts/STHeiti Light.ttc');pg.insert_text((65,70),'收入支出决算总表（金额角色控制）',fontname='CJK',fontsize=12);pg.insert_text((390,95),'金额单位：万元',fontname='CJK',fontsize=10)
xs=[60,210,290,450,535];ys=[110,145,180,215,250,285]
for x in xs:pg.draw_line((x,ys[0]),(x,ys[-1]))
for y in ys:pg.draw_line((xs[0],y),(xs[-1],y))
for ri,row in enumerate([['收入项目','金额','支出项目','金额']]+source):
 for ci,v in enumerate(row):pg.insert_text((xs[ci]+7,ys[ri]+23),v,fontname='CJK',fontsize=10)
doc.subset_fonts();doc.save(pdf,garbage=4,deflate=True);doc.close();digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
a=PDFParser(None).build_parse_artifact(str(pdf));assert len(a.logical_tables)==1
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=a):normal=d1.reconcile_auto(str(pdf))
b=copy.deepcopy(a);positions=set();changes=[]
for f in list(b.table_fragments)+list(b.physical_fragments):
 for ri,row in enumerate(f.rows):
  if len(row)>=4 and row[0]=='年初结转和结余' and row[2]=='年末结转和结余':
   before=row[1];row[1]='2026';positions.add((f.fragment_uid,ri,1));changes.append({'uid':f.fragment_uid,'row':ri,'col':1,'before':before,'after':'2026'})
   for c in f.cells:
    if c.row_idx==ri and c.col_idx==1:c.raw_text='2026';c.numeric_value=Decimal('2026')
assert len(positions)==1
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=b):bad=d1.reconcile_auto(str(pdf))
with pdfplumber.open(pdf) as h:text=h.pages[0].extract_text()
assert hashlib.sha256(pdf.read_bytes()).hexdigest()==digest
res={'source_pdf':str(pdf),'source_sha256':digest,'expected_rows':source,'expected_monetary_cells':8,'source_text':text,'changes':changes,'method':'Real current PDFParser artifact; only left amount cell 2025 -> 2026 changed in physical/effective views, original PDF unchanged.','expected':'normal PASS, corrupt GAP','normal':normal,'corrupt':bad}
(out/'year_amount_source_control.json').write_text(json.dumps(res,ensure_ascii=False,indent=2));(out/'year_amount_original_artifact.json').write_text(json.dumps(a.to_dict(),ensure_ascii=False,indent=2,default=str));(out/'year_amount_corrupt_artifact.json').write_text(json.dumps(b.to_dict(),ensure_ascii=False,indent=2,default=str))
print('normal',normal['pass'],normal['gap'],'corrupt',bad['pass'],bad['gap']);print('corrupt table',bad['results'][0])
