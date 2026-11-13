"""同一可视表格，仅改变 PDF 原生文字分段；不能由分段授权吞掉金额负号。"""
from pathlib import Path
import copy,hashlib,json,subprocess,sys,types
import pymupdf as fitz
OUT=Path(__file__).resolve().parents[1];C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
p=OUT/'scripts/independent_boundary_controls.py';ns={'__file__':str(p),'__name__':'fixture_only'}
exec(compile(p.read_text().split('\nresults=[test(')[0],str(p),'exec'),ns)
h=ns['h'];Fixture=ns['Fixture']
from scripts import effect_fact_sources as current
old_source=subprocess.check_output(['git','show','d71c9ad7:scripts/effect_fact_sources.py'],cwd=C);previous=types.ModuleType('scripts.effect_fact_sources');exec(compile(old_source,'d71c9ad7','exec'),previous.__dict__)
root=OUT/'native_line_cases';root.mkdir(exist_ok=True);(OUT/'renders').mkdir(exist_ok=True)
results=[]
cases=[('joined_line_cropped','joined','mask','FAIL'),('split_line_cropped','split','mask','FAIL'),('joined_line_true_row_cropped','joined','true_row','FAIL'),('complete_negative_positive','joined','complete','PASS'),('legal_row_hyphen_positive','legal','true_row','PASS')]
for name,emission,claim,want in cases:
 folder=root/name;assert not folder.exists();f=Fixture(folder)
 with fitz.open() as d:
  page=d.new_page();page.insert_text((45,35),'Fiscal table');page.insert_text((125,35),'wan')
  # 金额列左边界 130：负号 x=141 位于 Total 原格内。列头文字从150起不是列边界。
  for x in [35,130,230,330,430,520]:page.draw_line((x,50),(x,112),color=(0,0,0),width=.5)
  for y in [50,76,112]:page.draw_line((35,y),(520,y),color=(0,0,0),width=.5)
  for x,label in zip([150,250,350,450],['Total','Basic','Project','Reserve'],strict=True):page.insert_text((x,65),label)
  if emission=='joined':page.insert_text((45,100),'All'+' '*13+'-',fontname='cour',fontsize=10)
  elif emission=='split':
   page.insert_text((45,100),'All',fontname='cour',fontsize=10);page.insert_text((141,100),'-',fontname='cour',fontsize=10)
  else:page.insert_text((45,100),'All -',fontname='cour',fontsize=10)
  for i,(x,value) in enumerate(zip([155,250,350,450],['10.00','6.00','4.00','6.00'],strict=True)):
   if claim=='complete' and i in [1,2]:page.insert_text((x-14,100),'-',fontname='cour',fontsize=10)
   page.insert_text((x,100),value,fontname='cour',fontsize=10)
  data=d.tobytes()
 f.pdf.write_bytes(data);f.sha=h.sha(f.pdf)
 for obj in [f.br,f.cr]:obj['sha256']=f.sha
 for obj in [f.bs,f.cs]:obj['checksum']=f.sha
 f.save();f.before=h.load_collection(f.base)['I']['source_binding'];f.after=h.load_collection(f.cand)['I']['source_binding'];f.ev['samples'][0]['binding']=f.after
 with fitz.open(f.pdf) as d:
  page=d[0]
  def anchor(quote,x=None):
   boxes=page.search_for(quote)
   if x is not None:boxes=[b for b in boxes if abs(b.x0-x)<.1]
   assert len(boxes)==1,(name,quote,x,boxes)
   return {'page':1,'quote':quote,'bbox':list(boxes[0])}
  row=anchor('All -' if emission=='legal' else 'All')
  if claim=='mask':row={'page':1,'quote':'All -','bbox':list(page.search_for('All')[0]|page.search_for('-')[0])}
  for i,(fact,x,value,label) in enumerate(zip(f.ev['samples'][0]['facts'],[155,250,350],['10.00','6.00','4.00'],['Total','Basic','Project'],strict=True)):
   cell=anchor(value,x);raw=value
   if claim=='complete':
    sign=next(b for b in page.search_for('-') if abs(b.x0-(x-14))<.1)
    cell={'page':1,'quote':'- '+value,'bbox':list(sign|fitz.Rect(cell['bbox']))};raw='-'+value
   fact.update(file_hash=f.sha,quote=cell['quote'],value_wan=raw)
   fact['source_cell'].update(cell=cell,row=copy.deepcopy(row),column=anchor(label),table={'page':1,'quote':'Fiscal table','bbox':[35,15,520,125]},unit_source=anchor('wan'),raw_value=raw,raw_unit='wan')
  source_checks={fact['fact_id']:{k:h.source_errors(v,d,f.sha) for k,v in fact['source_cell'].items() if isinstance(v,dict)} for fact in f.ev['samples'][0]['facts']}
  native_lines=[{'text':''.join(s['text'] for s in line['spans']),'bbox':list(line['bbox'])} for b in page.get_text('dict')['blocks'] for line in b.get('lines',[])]
  pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False);pixel_hash=hashlib.sha256(pix.samples).hexdigest()
  page.get_pixmap(matrix=fitz.Matrix(2,2),clip=fitz.Rect(25,15,530,120),alpha=False).save(OUT/f'renders/{name}.png')
 f.seal_contract();f.save();evp=folder/'external-evidence.json';evp.write_text(json.dumps(f.ev,ensure_ascii=False,indent=2));ev=h.load_evidence(evp,h.sha(evp));reports={}
 try:
  for version,module in [('d71_module',previous),('9e66_module',current)]:
   sys.modules['scripts.effect_fact_sources']=module;reports[version]=h.compare_collections(f.base,f.cand,f.kit,ev)
 finally:sys.modules['scripts.effect_fact_sources']=current
 result={'case':name,'expected':want,'actual':reports['9e66_module']['overall'],'expectation_met':reports['9e66_module']['overall']==want,'versions':reports,'source_checks':source_checks,'pdf_sha256':f.sha,'external_input_sha256':h.sha(evp),'full_page_pixel_sha256':pixel_hash,'native_lines':native_lines,'row_anchor':row}
 (folder/'independent_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));results.append(result)
# 复制第一反例的原件和前后工件，只改变外部行标及对应契约摘要。
import shutil
original=root/'joined_line_cropped';target=root/'same_pdf_true_row_control';target.mkdir()
for part in ['before','after','kit']:shutil.copytree(original/part,target/part)
ev=h.read(original/'external-evidence.json')
with fitz.open(target/'kit/pdfs/I/original.pdf') as d:row={'page':1,'quote':'All','bbox':list(d[0].search_for('All')[0])}
for fact in ev['samples'][0]['facts']:
 fact['source_cell']['row']=row;ev['samples'][0]['relation_contracts'][0]['operand_identities'][fact['fact_id']]['source_cell_digest']=h.digest(fact['source_cell'])
p=target/'external-evidence.json';p.write_text(json.dumps(ev,ensure_ascii=False,indent=2));r=h.compare_collections(target/'before',target/'after',target/'kit',h.load_evidence(p,h.sha(p)))
pair={'original_case':str(original),'control_case':str(target),'same_pdf_and_before_after_bytes':all(h.sha(p)==h.sha(original/p.relative_to(target)) for part in ['before','after','kit'] for p in (target/part).rglob('*') if p.is_file()),'changed_external_fields':['source_cell.row','operand_identities.*.source_cell_digest'],'mask_actual':results[0]['actual'],'true_row_actual':r['overall'],'true_row_expected':'FAIL','true_row_report':r,'joined_and_split_page_pixels_identical':results[0]['full_page_pixel_sha256']==results[1]['full_page_pixel_sha256']}
(OUT/'native_line_exact_pair.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2)+'\n');(OUT/'receipts/native_line_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'controls':[{k:r[k] for k in ['case','expected','actual','expectation_met']} for r in results],'pair':pair},ensure_ascii=False,indent=2))
