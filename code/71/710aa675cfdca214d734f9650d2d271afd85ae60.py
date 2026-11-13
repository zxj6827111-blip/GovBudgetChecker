import os
import sys,json,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from scripts import d1_table_reconciliation as d
import pymupdf as fitz
import pdfplumber
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')));out.mkdir(parents=True,exist_ok=True)
p=out/'no_code_multilane_source.pdf';doc=fitz.open();pg=doc.new_page();pg.insert_font(fontname='CJK',fontfile='/System/Library/Fonts/STHeiti Light.ttc')
pg.insert_text((70,70),'收入支出决算总表（控制原件）',fontname='CJK',fontsize=12);pg.insert_text((385,95),'金额单位：万元',fontname='CJK',fontsize=10)
xs=[65,190,280,420,510];ys=[110,145,180,215,250]
for x in xs:pg.draw_line((x,ys[0]),(x,ys[-1]))
for y in ys:pg.draw_line((xs[0],y),(xs[-1],y))
source=[['本年收入A','100','本年支出A','100'],['本年收入B','200','本年支出B','200'],['收入总计','300','支出总计','300']]
for ri,row in enumerate([['收入项目','金额','支出项目','金额']]+source):
 for ci,value in enumerate(row):pg.insert_text((xs[ci]+8,ys[ri]+23),value,fontname='CJK',fontsize=10)
doc.subset_fonts();doc.save(p,garbage=4,deflate=True);doc.close()
res={'source_pdf':str(p),'expected_source_rows':source,'expected_monetary_cells':6,'expected_codes':[],'basis':'No code column; source income and expenditure both 100+200=300. Only effective-view expenditure A/B values are swapped in the negative control.'}
for name,rows in [('normal',source),('swapped_expenditure',copy.deepcopy(source))]:
 if name=='swapped_expenditure':rows[0][3],rows[1][3]=rows[1][3],rows[0][3]
 f=SimpleNamespace(fragment_uid='p1',page_number=1,rows=rows,source_fragment_uids=[])
 i=SimpleNamespace(table_code='FIN_01_income_expenditure_total',title='收入支出决算总表',page_start=1,page_end=1,fragment_uids=['p1'])
 a=SimpleNamespace(table_fragments=[f],physical_fragments=[f],logical_tables=[i])
 with patch.object(d.PDFParser,'build_parse_artifact',return_value=a):result=d.reconcile_auto(str(p))
 known=d._codes_from_rows([{'page':1,'cells':r} for r in rows])
 with pdfplumber.open(p) as h:text=h.pages[0].extract_text()
 res[name]={'effective_rows':rows,'learned_codes':sorted(known),'original_inventory':d._parse_original_page(text,1,known),'result':result}
 print(name,result['pass'],result['gap'],'learned',sorted(known))
res['original_text']=text
(out/'d1_source_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
