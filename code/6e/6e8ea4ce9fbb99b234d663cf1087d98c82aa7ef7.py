"""逐份核对新旧报告差异，并从PDF独立网格验证新增层级提示的原行。"""
import collections, json, re, unicodedata
from pathlib import Path
import pdfplumber

OUT=Path(__file__).resolve().parent
read=lambda name:json.loads((OUT/name).read_text())
assert '\nCOMPLETE 345 ' in (OUT/'release_final12.log').read_text()
entries=read('release_final12_summary.json');assert len(entries)==345
sources=read('release_final12_source_manifest.json')
families={'V33-005','V33-120','V33-240','V33-241'}
normalize=lambda value:re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or '')))
def message(item):return re.sub(r'^[一二三四五六七八九十百零〇\d]+[、.．]','',str(item.get('message') or '')).strip()
def signature(items, *, others=False):
 return collections.Counter((i.get('rule_id') or i.get('rule'),message(i),i.get('severity')) for i in items
                            if not others or (i.get('rule_id') or i.get('rule')) not in families)
def native_codes(rows):
 result=[]
 for row in rows:
  labels=[];name=None
  for cell in row:
   text=normalize(cell)
   if re.search(r'[\u4e00-\u9fff]',text):name=text;break
   labels.append(text)
  code=''.join(labels)
  if re.fullmatch(r'\d{3}(?:\d{2}){0,2}',code) and name:result.append((code,name))
 return result
pdfs={};table_cache={};proofs=[];changes=[]
def tables(source_hash,page):
 key=(source_hash,page)
 if key not in table_cache:
  table_cache[key]=[(t.bbox,native_codes(t.extract())) for t in pdfs[source_hash].pages[page-1].find_tables()]
 return table_cache[key]
def contains(box,point):return box[0]-.5<=point[0]<=box[2]+.5 and box[1]-.5<=point[1]<=box[3]+.5
try:
 for entry in entries:
  job=entry['job'];old=read(f'release_final9/{job}/uploads/{job}/status.json')['review_report']
  new=read(f'release_final12/{job}/uploads/{job}/status.json')['review_report']
  for bucket in ('issues','tips'):assert signature(old[bucket],others=True)==signature(new[bucket],others=True),(job,bucket,'unrelated_checker_changed')
  changed=any(signature(old[b])!=signature(new[b]) for b in ('issues','tips'))
  if not changed:continue
  roots=[item for item in new['issues'] if item.get('location',{}).get('check_type')=='orphan_classification']
  assert roots,(job,'report changed without retained source hierarchy problem')
  context=read(f'release_final12/{job}/uploads/{job}/fact_context.json');facts=context['facts']
  source=sources[job];sha=source['sha256']
  if sha not in pdfs:pdfs[sha]=pdfplumber.open(source['pdf'])
  root_proofs=[]
  for root in roots:
   assert root.get('evidence_status')=='complete' and root.get('evidence_verification',{}).get('source_identity_complete'),(job,root['id'],'unverified_new_problem')
   for member in root['location'].get('related_findings') or [{'location':root['location']}]:
    location=member['location'];code_set=set(location['orphan_codes']);table=location['table_code']
    all_facts=[f for f in facts if f.get('table_code')==table]
    selected_tables={}
    for fact in all_facts:
     for ref in fact.get('extra_dims',{}).get('evidence_refs',[]):
      page,box=ref.get('physical_page'),ref.get('bbox')
      if not page or not box:continue
      point=((box[0]+box[2])/2,(box[1]+box[3])/2)
      for source_box,rows in tables(sha,page):
       if contains(source_box,point):selected_tables[(page,tuple(source_box))]=rows
    printed_codes={code for rows in selected_tables.values() for code,name in rows}
    assert printed_codes,(job,table,'no_independent_original_grid')
    for code in sorted(code_set):
     matches=[(page,box,name) for (page,box),rows in selected_tables.items() for current,name in rows if current==code]
     assert matches,(job,table,code,'code_missing_from_original_grid')
     # 仅验证同表已印高层与孤立明细混层，不查权威科目目录，不推测正确编码。
     depth=min(len(current) for current in printed_codes)
     assert len(code)>depth and code[:depth] not in printed_codes,(job,table,code,'not_an_orphan_at_printed_root_level')
     proof=dict(job=job,source_sha256=sha,table=table,code=code,original_rows=[dict(page=page,table_bbox=box,name=name) for page,box,name in matches],
                printed_root_depth=depth,printed_ancestor_absent=True,reported_problem=root['id'])
     root_proofs.append(proof);proofs.append(proof)
  changes.append(dict(job=job,filename=entry['filename'],formal_before=len(old['issues']),formal_after=len(new['issues']),
                      tips_before=len(old['tips']),tips_after=len(new['tips']),source_proofs=root_proofs))
finally:
 for pdf in pdfs.values():pdf.close()
result=dict(verified=True,materials=len(entries),changed_tasks=len(changes),unique_changed_pdf_contents=len({p['source_sha256'] for p in proofs}),
            allowed_checker_families=sorted(families),unrelated_checkers_unchanged=True,all_added_hierarchy_rows_independently_verified=True,
            source_proofs=proofs,changes=changes)
(OUT/'final12_orphan_impact_verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in {'source_proofs','changes'}},ensure_ascii=False))
