"""Independent PyMuPDF table-grid observations; machine comparison is not human truth."""
import json,re,time,sys
from pathlib import Path
from decimal import Decimal
from collections import defaultdict,Counter
import pymupdf as fitz
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
def norm(s):return re.sub(r'\s+','',str(s or '')).replace('，',',').replace('−','-')
def amount(s):
 s=norm(s)
 if re.fullmatch(r'[-+]?\d[\d,]*(?:\.\d+)?',s):
  try:return Decimal(s.replace(',',''))
  except:return None
 return None
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
rows=json.loads((ROOT/'source_manifest.json').read_text())
for r in rows:
 out=ROOT/'original_occurrences'/r['doc_id']
 if (out/'observations.json').exists():continue
 start=time.monotonic();cells=[];table_records=[];errors=[]
 with fitz.open(r['frozen_path']) as doc:
  previous=False
  for i,page in enumerate(doc):
   text=norm(page.get_text())
   title=any(s in text for s in ['财务收支预算总表','收入预算总表','支出预算总表','财政拨款收支预算总表','支出功能分类预算表','基本支出部门预算经济分类','经费和机关运行经费预算表'])
   continuation=previous and i>2 and ('功能分类科目编码' in text or '部门预算经济分类科目编码' in text)
   if i<3 or not(title or continuation):previous=False;continue
   previous=True
   try:
    tables=page.find_tables(strategy='lines').tables
    for ti,table in enumerate(tables):
     data=table.extract();trs=list(table.rows);ncols=table.col_count
     # Source-only monetary columns: exclude function-code columns before the name column.
     header='|'.join(norm(c) for row in data[:4] for c in row)
     codecols=3 if ('功能分类科目编码' in header or '部门预算经济分类科目编码' in header) else 0
     monetary=set()
     for ci in range(ncols):
      vals=[norm(row[ci] if ci<len(row) else '') for row in data]
      if ci>=codecols and any(re.search(r'\d,\d{3}|\d\.\d{2}',v) for v in vals):monetary.add(ci)
      if ci>=codecols+1 and any(any(k in v for k in ['预算数','合计','基本支出','项目支出','财政拨款收入','事业收入','人员经费','公用经费','因公出国','接待费','购置费','运行费']) and len(v)<25 for v in vals[:4]):monetary.add(ci)
     observed=0
     for ri,row in enumerate(data):
      for ci in sorted(monetary):
       if ci>=len(row) or ci>=len(trs[ri].cells):continue
       box=trs[ri].cells[ci];v=row[ci];raw=norm(v)
       if box is None or v is None:continue
       value=amount(raw)
       kind='amount' if value is not None else 'blank' if raw=='' else 'dash' if raw in ['-','--','—','–','/'] else None
       if kind is None:continue
       if ri<2 and kind!='amount':continue
       unit='万元' if '单位:万元' in text or '单位：万元' in text else '元' if '单位:元' in text or '单位：元' in text else 'unknown'
       cells.append({'occurrence_id':f"{r['doc_id']}:p{i+1}:t{ti}:r{ri}:c{ci}",'physical_page':i+1,'bbox':list(box),'row_idx':ri,'col_idx':ci,'table_index':ti,'raw_value':str(v),'normalized_lexeme':raw,'literal_kind':kind,'source_unit':unit,'source_row':row,'annotation_method':'independent_pymupdf_grid_machine_observation','human_verified':False})
       observed+=1
     table_records.append({'physical_page':i+1,'table_index':ti,'bbox':list(table.bbox),'rows':len(data),'columns':ncols,'candidate_monetary_columns':sorted(monetary),'observed_cells':observed,'raw_rows':data})
   except Exception as exc:errors.append({'page':i+1,'error':str(exc)})
 write(out/'observations.json',{'source_sha256':r['sha256'],'doc_id':r['doc_id'],'file':r['file'],'annotation_complete':False,'human_verified':False,'independent_truth':False,'source_method':'PyMuPDF find_tables lines; independent of production parser, not visual truth','cells':cells,'tables':table_records,'errors':errors,'elapsed_seconds':time.monotonic()-start})
 print(r['doc_id'],r['file'],len(cells),len(table_records),flush=True)
