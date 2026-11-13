import hashlib,json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
import pymupdf as fitz
import pdfplumber
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1
OUT=Path('/private/tmp/gbc-r7-results-20261111')
results=[]
for name,left in [('mixed',['100','200','300']),('decimal',['100.00','200.00','300.00'])]:
 pdf=OUT/('mixed_amount_source_'+name+'.pdf')
 rows=[['本年收入合计',left[0],'本年支出合计','100.50'],['年初结转和结余',left[1],'年末结转和结余','199.50'],['总计',left[2],'总计','300.00']]
 doc=fitz.open();page=doc.new_page();page.insert_font(fontname='CJK',fontfile='/System/Library/Fonts/STHeiti Light.ttc')
 page.insert_text((65,70),'收入支出决算总表',fontname='CJK',fontsize=12);page.insert_text((390,95),'金额单位：万元',fontname='CJK',fontsize=10)
 xs=[60,210,290,450,535];ys=[110,145,180,215,250]
 for x in xs:page.draw_line((x,ys[0]),(x,ys[-1]))
 for y in ys:page.draw_line((xs[0],y),(xs[-1],y))
 for ri,row in enumerate([['收入项目','金额','支出项目','金额']]+rows):
  for ci,v in enumerate(row):page.insert_text((xs[ci]+7,ys[ri]+23),v,fontname='CJK',fontsize=10)
 doc.subset_fonts();doc.save(pdf,garbage=4,deflate=True);doc.close();digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
 artifact=PDFParser(None).build_parse_artifact(str(pdf))
 with patch.object(d1.PDFParser,'build_parse_artifact',return_value=artifact):report=d1.reconcile_auto(str(pdf))
 assert len(artifact.logical_tables)==1
 with pdfplumber.open(pdf) as h:text=h.pages[0].extract_text()
 view,errors=d1._logical_view_with_provenance(d1.doc_for_artifact(artifact,str(pdf),[text]),artifact.logical_tables)
 roles,codes,year=d1._learn_column_roles(view)
 result={'case':name,'source':str(pdf),'source_sha256':digest,'expected_rows':rows,'expected_monetary_cells':6,'expected':{'instances':1,'pass':1,'gap':0},'source_text':text,'roles':roles,'codes':sorted(codes),'year_flag':year,'actual_merged_rows':view,'original_inventory':d1._parse_original_page(text,1,codes,year),'actual':report,'source_unchanged':hashlib.sha256(pdf.read_bytes()).hexdigest()==digest}
 (OUT/('mixed_amount_'+name+'_artifact.json')).write_text(json.dumps(artifact.to_dict(),ensure_ascii=False,indent=2,default=str));results.append(result)
 print(name,roles,{k:report[k] for k in ['instances','pass','gap']},report['results'][0],flush=True)
(OUT/'mixed_amount_source_control.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
