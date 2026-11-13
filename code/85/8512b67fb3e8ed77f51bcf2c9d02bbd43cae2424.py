"""独立检查新行标/表题消除是否吞掉原格语义；原件先落盘再建立外部输入。"""
from pathlib import Path
import copy,hashlib,json,sys,subprocess,types
import pymupdf as fitz
OUT=Path(__file__).resolve().parents[1];C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
p=OUT/'scripts/independent_boundary_controls.py';ns={'__file__':str(p),'__name__':'fixture_only'}
exec(compile(p.read_text().split('\nresults=[test(')[0],str(p),'exec'),ns)
h=ns['h'];Fixture=ns['Fixture']
from scripts import effect_fact_sources as current
source=subprocess.check_output(['git','show','9631ea55:scripts/effect_fact_sources.py'],cwd=C)
previous=types.ModuleType('scripts.effect_fact_sources');exec(compile(source,'9631ea55','exec'),previous.__dict__)
root=OUT/'masking_cases';root.mkdir(exist_ok=True)
results=[]
for name,kind,want in [('plain_positive','plain','PASS'),('cropped_negative_exact_row','narrow','FAIL'),('cropped_negative_row_swallows_sign','rowmask','FAIL'),('complete_negative_positive','negative','PASS'),('extended_unit_exact_title','unit_narrow','FAIL'),('extended_unit_title_swallows_prefix','titlemask','FAIL')]:
 folder=root/name;assert not folder.exists();f=Fixture(folder)
 # 新造原件不覆盖任何历史材料。行标 All 与列头 Total 都先于外部事实确定。
 with fitz.open() as d:
  page=d.new_page();page.insert_text((45,35),'Fiscal table')
  page.insert_text((125,35),'100 wan' if kind.startswith('unit') or kind=='titlemask' else 'wan')
  for x,label in zip([150,250,350,450],['Total','Basic','Project','Reserve'],strict=True):page.insert_text((x,65),label)
  page.insert_text((45,100),'All')
  for i,(x,value) in enumerate(zip([150,250,350,450],['10.00','6.00','4.00','6.00'],strict=True)):
   signed=(kind in ['narrow','rowmask'] and i==0) or (kind=='negative' and i<3)
   if signed:page.insert_text((x,100),'-');page.insert_text((x+10,100),value)
   else:page.insert_text((x,100),value)
  data=d.tobytes()
 f.pdf.write_bytes(data);f.sha=h.sha(f.pdf)
 for obj in [f.br,f.cr]:obj['sha256']=f.sha
 for obj in [f.bs,f.cs]:obj['checksum']=f.sha
 f.save();f.before=h.load_collection(f.base)['I']['source_binding'];f.after=h.load_collection(f.cand)['I']['source_binding'];f.ev['samples'][0]['binding']=f.after
 with fitz.open(f.pdf) as d:
  page=d[0]
  def anchor(q,near=None):
   boxes=page.search_for(q)
   if near is not None:boxes=[b for b in boxes if abs(b.x0-near)<1]
   assert len(boxes)==1,(name,q,near,boxes)
   return {'page':1,'quote':q,'bbox':list(boxes[0])}
  row=anchor('All')
  if kind=='rowmask':
   row={'page':1,'quote':'All -','bbox':list(fitz.Rect(row['bbox']) | page.search_for('-')[0])}
  table={'page':1,'quote':'Fiscal table 100' if kind=='titlemask' else 'Fiscal table','bbox':[35,15,520,125]}
  for i,(fact,x,value,label) in enumerate(zip(f.ev['samples'][0]['facts'],[150,250,350],['10.00','6.00','4.00'],['Total','Basic','Project'],strict=True)):
   negative=kind=='negative';offset=10 if negative or (kind in ['narrow','rowmask'] and i==0) else 0
   cell=anchor(value,x+offset);raw=value
   if negative:
    sign=next(b for b in page.search_for('-') if abs(b.x0-x)<1)
    cell={'page':1,'quote':'- '+value,'bbox':list(fitz.Rect(cell['bbox'])|sign)};raw='-'+value
   fact.update(file_hash=f.sha,quote=cell['quote'],value_wan=raw)
   fact['source_cell'].update(cell=cell,row=copy.deepcopy(row),column=anchor(label),table=copy.deepcopy(table),unit_source=anchor('wan'),raw_value=raw,raw_unit='wan')
  page_text=page.get_text();source_checks={}
  for fact in f.ev['samples'][0]['facts']:
   source_checks[fact['fact_id']]={k:h.source_errors(v,d,f.sha) for k,v in fact['source_cell'].items() if isinstance(v,dict)}
 f.seal_contract();f.save();evp=folder/'external-evidence.json';evp.write_text(json.dumps(f.ev,ensure_ascii=False,indent=2));ev=h.load_evidence(evp,h.sha(evp));reports={}
 try:
  for version,module in [('963_module',previous),('d71_module',current)]:
   sys.modules['scripts.effect_fact_sources']=module
   reports[version]=h.compare_collections(f.base,f.cand,f.kit,ev)
 finally:sys.modules['scripts.effect_fact_sources']=current
 row={'case':name,'expected':want,'actual':reports['d71_module']['overall'],'expectation_met':reports['d71_module']['overall']==want,'versions':reports,'source_checks':source_checks,'original_pdf_sha256':f.sha,'external_input_sha256':h.sha(evp),'page_text':page_text}
 (folder/'independent_observed_report.json').write_text(json.dumps(row,ensure_ascii=False,indent=2));results.append(row)
(OUT/'receipts/masking_boundary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps([{k:r[k] for k in ['case','expected','actual','expectation_met']} | {'versions':{k:v['overall'] for k,v in r['versions'].items()}} for r in results],ensure_ascii=False,indent=2))
