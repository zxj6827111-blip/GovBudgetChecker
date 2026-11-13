from pathlib import Path
import sys,json,hashlib,time,re,logging
from collections import Counter,defaultdict
from decimal import Decimal
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
sys.path.insert(0,str(ROOT))
import pymupdf
from src.services.pdf_parser import PDFParser
from src.services.table_reading_assessment import assess_table_reading,number
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.fiscal_table_rules import NINE_TABLE_RULES
logging.disable(logging.WARNING)
OUT=ROOT/'outputs/workbuddy_independent_audit_20261020'
SOURCE=Path('/Users/zhangxingjin/Desktop/决算测试上海市')
SELECT={('2023','上海化学工业区管理委员会.pdf'),('2023','上海市交通委员会.pdf'),('2023','上海市国防动员办公室.pdf'),('2023','上海市农场管理局.pdf'),('2023','上海市人民政府研究室.pdf'),('2025','上海市商务委员会.pdf'),('2024','上海市人民政府研究室.pdf'),('2024','上海市国防动员办公室.pdf'),('2024','上海市人民政府办公厅.pdf')}
files=sorted(SOURCE.rglob('*.pdf'),key=lambda p:((p.parent.name,p.name) not in SELECT,str(p)))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest={'source_root':str(SOURCE),'created_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'files':[{'path':str(p),'year':p.parent.name,'file':p.name,'sha256':sha(p),'bytes':p.stat().st_size} for p in files], 'code':{str(p):sha(ROOT/p) for p in map(Path,['src/services/pdf_parser.py','src/services/fiscal_fact_materializer.py','src/services/table_reading_assessment.py','src/services/fiscal_table_rules.py','src/engine/fact_context.py','src/engine/rules_v33.py','src/engine/check_obligations.py'])}}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
rows=[]
for i,p in enumerate(files,1):
 key=p.parent.name+'__'+p.stem
 target=OUT/(key+'.json')
 if target.exists():
  rows.append(json.loads(target.read_text()));continue
 start=time.monotonic()
 try:
  art=PDFParser(None).build_parse_artifact(str(p));reading=assess_table_reading(art)
  ctx=build_fact_context_from_artifact(art,org_name=p.stem,fiscal_year=int(p.parent.name),report_kind='final')
  facts=ctx.facts if ctx else []
  doc=pymupdf.open(p)
  native={'pages':len(doc),'no_text_pages':[],'image_only_pages':[],'decimal_occurrences_in_known_table_regions':0,'spatial_value_matches':0,'unmatched':[]}
  page_cells=defaultdict(list);page_tables=defaultdict(list)
  for t in art.table_fragments:
   for c in t.cells:
    if c.bbox and number(c.raw_text) is not None:page_cells[c.page_number].append((t,c))
   if t.table_code in NINE_TABLE_RULES:
    for page in {c.page_number for c in t.cells}:page_tables[page].append(t)
  for no,page in enumerate(doc,1):
   words=page.get_text('words')
   if not page.get_text().strip():
    native['no_text_pages'].append(no)
    if page.get_images():native['image_only_pages'].append(no)
   for w in words:
    s=w[4].replace('，',',');v=number(s)
    if '.' not in s or v is None:continue
    x,y=(w[0]+w[2])/2,(w[1]+w[3])/2
    tables=[t for t in page_tables[no] if t.bbox and t.bbox[0]-1<=x<=t.bbox[2]+1 and t.bbox[1]-1<=y<=t.bbox[3]+1]
    if not tables:continue
    native['decimal_occurrences_in_known_table_regions']+=1
    hit=[c for t,c in page_cells[no] if number(c.raw_text)==v and c.bbox[0]-1<=x<=c.bbox[2]+1 and c.bbox[1]-1<=y<=c.bbox[3]+1]
    if hit:native['spatial_value_matches']+=1
    else:native['unmatched'].append({'page':no,'raw_text':s,'bbox':list(w[:4]),'table_codes':sorted({t.table_code for t in tables})})
  r={'year':p.parent.name,'file':p.name,'sha256':art.source_file_hash,'parser_fingerprint':art.parser_fingerprint,'parse_run_uid':art.parse_run_uid,'pages':art.page_count,'table_codes':sorted({t.table_code for t in art.table_fragments if t.table_code in NINE_TABLE_RULES}),'logical_tables':[{'code':t.table_code,'uid':t.instance_uid,'fragments':len(t.fragment_uids)} for t in art.logical_tables if t.table_code in NINE_TABLE_RULES],'gaps':reading['gaps'],'reading_status':reading['status'],'original_pdf_full_coverage_verified':reading['original_pdf_full_coverage_verified'],'probe_amount_tokens':sum(len(a['amounts']) for a in art.reading_assessment['pages']),'facts_count':len(facts),'fact_statuses':dict(Counter(f.recognition_status for f in facts)),'native_independent_probe':native,'seconds':round(time.monotonic()-start,3)}
  if (p.parent.name,p.name) in SELECT:
   (OUT/(key+'__artifact.json')).write_text(json.dumps(art.to_dict(),ensure_ascii=False))
   (OUT/(key+'__facts.json')).write_text(json.dumps([f.to_dict() for f in facts],ensure_ascii=False))
   (OUT/(key+'__native_text.json')).write_text(json.dumps([{'page':j+1,'text':q.get_text(),'words':[list(w) for w in q.get_text('words')]} for j,q in enumerate(doc)],ensure_ascii=False))
  doc.close()
 except Exception as e:r={'year':p.parent.name,'file':p.name,'error':repr(e),'seconds':round(time.monotonic()-start,3)}
 target.write_text(json.dumps(r,ensure_ascii=False,indent=2));rows.append(r)
 (OUT/'current_parse_summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 print(json.dumps({'i':i,'total':len(files),'year':r['year'],'file':r['file'],'gaps':len(r.get('gaps',[])),'spatial_unmatched':len(r.get('native_independent_probe',{}).get('unmatched',[])),'seconds':r['seconds'],'error':r.get('error')},ensure_ascii=False),flush=True)
print('COMPLETE',len(rows),flush=True)
