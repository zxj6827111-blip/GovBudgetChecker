import os
import sys,json,copy,importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from scripts import d1_table_reconciliation as current
import pymupdf as fitz
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')));out.mkdir(parents=True,exist_ok=True);p=out/'integer_table_embedded_original.pdf'
pdf=fitz.open();page=pdf.new_page();page.insert_font(fontname='CJK',fontfile='/System/Library/Fonts/STHeiti Light.ttc');page.insert_text((72,72),'支出决算表',fontname='CJK');page.insert_text((260,95),'单位：万元',fontname='CJK')
for x,y,txt,font in [(72,125,'项目','CJK'),(220,125,'决算数','CJK'),(72,160,'合计','CJK'),(220,160,'100','helv')]:page.insert_text((x,y),txt,fontname=font)
for x in [65,200,320]:page.draw_line((x,105),(x,175))
for y in [105,140,175]:page.draw_line((65,y),(320,y))
pdf.subset_fonts();pdf.save(p,garbage=4,deflate=True);pdf.close()
oldpath=Path(str(Path(__file__).resolve().parents[1]/'baseline_70cd8d37/scripts/d1_table_reconciliation.py'))
spec=importlib.util.spec_from_file_location('old_d1_control',oldpath);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
res={'source_pdf':str(p),'source_value':'100','injected_value':'200','expected':'normal PASS; corrupted GAP (integer remains an amount even if its format needs correction)'}
for name,value in [('normal','100'),('corrupted','200')]:
 frag=SimpleNamespace(fragment_uid='p1',page_number=1,rows=[['合计',value]],source_fragment_uids=[])
 inst=SimpleNamespace(table_code='FIN_03_expenditure',title='支出决算表',page_start=1,page_end=1,fragment_uids=['p1'])
 artifact=SimpleNamespace(table_fragments=[frag],physical_fragments=[frag],logical_tables=[inst])
 for edition,mod in [('before',old),('after',current)]:
  with patch.object(mod.PDFParser,'build_parse_artifact',return_value=artifact):res[edition+'_'+name]=mod.reconcile_auto(str(p))
import pdfplumber
with pdfplumber.open(p) as d:res['original_text']=d.pages[0].extract_text()
res['new_original_inventory']=current._parse_original_page(res['original_text'],1)
(out/'integer_auto_control.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str))
for n,r in res.items():
 if isinstance(r,dict) and 'pass' in r:print(n,r['pass'],r['gap'])
print('source_text',repr(res['original_text']),'new_inventory',res['new_original_inventory'])
