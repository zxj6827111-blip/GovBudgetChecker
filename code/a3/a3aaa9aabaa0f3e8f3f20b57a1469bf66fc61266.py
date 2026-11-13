import fitz,json,hashlib
from pathlib import Path
p=Path('outputs/remediation_20261006_round11/development_materials')
with fitz.open() as d:
 page=d.new_page(width=900,height=600)
 page.insert_text((30,40),'2025',fontname='helv',fontsize=30)
 page.insert_text((30,119),"决算单位：测试局",fontname="china-s",fontsize=26)
 page.insert_text((110,40),'年测试局部门决算',fontname='china-s',fontsize=30)
 for xy,text,size in [((30,85),'收入决算表 单位：万元',27),((30,425),'二、收入决算情况说明',26),((30,475),'本年收入合计',25),((30,525),'财政拨款收入',25)]:
  page.insert_text(xy,text,fontname='china-s',fontsize=size)
 page.insert_text((190,475),'1000.00',fontname='helv',fontsize=25)
 page.insert_text((300,475),'万元。',fontname='china-s',fontsize=25)
 page.insert_text((190,525),'1000.00',fontname='helv',fontsize=25)
 page.insert_text((300,525),'万元，占',fontname='china-s',fontsize=25)
 page.insert_text((420,525),'100%',fontname='helv',fontsize=25)
 xs=[30,185,385,625,870]; ys=[130,240,350]
 for x in xs: page.draw_line((x,130),(x,350),width=2)
 for y in ys: page.draw_line((30,y),(870,y),width=2)
 for row,values in enumerate([['科目编码','科目名称','本年收入合计','财政拨款收入'],['','合计','1000.00','1000.00']]):
  for col,value in enumerate(values):
   page.insert_text((xs[col]+9,ys[row]+65),value,fontname='helv' if row and col>=2 else 'china-s',fontsize=24 if row==0 else 32)
 path=p/'synthetic_ocr_control.pdf'; d.save(path)
 (p/'synthetic_ocr_control.png').write_bytes(page.get_pixmap(dpi=300).tobytes('png'))
(p/'synthetic_ocr_control.source.json').write_text(json.dumps({'synthetic':True,'scope':'real_model_engineering_positive_control','formal_sample_eligible':False,'pdf_sha256':hashlib.sha256(path.read_bytes()).hexdigest()},indent=2))
