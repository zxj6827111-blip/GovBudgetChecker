import copy,hashlib,json,sys
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal
sys.path.insert(0,str(Path.cwd()))
import pymupdf as fitz
import pdfplumber
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1
OUT=Path('/private/tmp/gbc-r8-results-20261111')
data=[['一般公共服务支出','100','20.50','79.50'],['教育支出','200','80.50','119.50'],['合计','300','101.00','199.00']]
results=[]
for name,header in [('business',['项目','本年支出合计','基本支出','项目支出']),('explicit_amount',['项目','金额','金额','金额'])]:
 pdf=OUT/('expenditure_header_'+name+'.pdf')
 doc=fitz.open();p=doc.new_page();p.insert_font(fontname='CJK',fontfile='/System/Library/Fonts/STHeiti Light.ttc')
 p.insert_text((65,70),'支出决算表',fontname='CJK',fontsize=12);p.insert_text((390,95),'金额单位：万元',fontname='CJK',fontsize=10)
 xs=[50,220,330,440,550];ys=[110,145,180,215,250]
 for x in xs:p.draw_line((x,ys[0]),(x,ys[-1]))
 for y in ys:p.draw_line((xs[0],y),(xs[-1],y))
 for ri,row in enumerate([header]+data):
  for ci,value in enumerate(row):p.insert_text((xs[ci]+6,ys[ri]+23),value,fontname='CJK',fontsize=10)
 doc.subset_fonts();doc.save(pdf,garbage=4,deflate=True);doc.close();before=hashlib.sha256(pdf.read_bytes()).hexdigest()
 artifact=PDFParser(None).build_parse_artifact(str(pdf))
 with pdfplumber.open(pdf) as h:text=h.pages[0].extract_text()
 assert len(artifact.logical_tables)==1
 view,errors=d1._logical_view_with_provenance(d1.doc_for_artifact(artifact,str(pdf),[text]),artifact.logical_tables)
 expected=[header]+data
 assert [x['cells'] for x in view]==expected,(name,view)
 roles,codes=d1._learn_column_roles(view)
 report=d1.reconcile_auto(str(pdf))
 assert hashlib.sha256(pdf.read_bytes()).hexdigest()==before
 row={'case':name,'source':str(pdf),'source_sha256':before,'expected_rows':expected,'expected_monetary_cells':9,'artifact_rows_equal_expected':True,'source_text':text,'header_roles':d1._header_column_roles(view),'learned_roles':roles,'known_codes':sorted(codes),'actual':report,'source_unchanged':True,'original_inventory':d1._parse_original_page(text,1,codes)}
 results.append(row);(OUT/('expenditure_header_'+name+'_artifact.json')).write_text(json.dumps(artifact.to_dict(),ensure_ascii=False,indent=2,default=str))
 print(name,roles,{k:report[k] for k in ['instances','pass','gap']},report['results'][0],flush=True)
(OUT/'expenditure_header_source_control.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
