from pathlib import Path
import json,sys,shutil,subprocess,types
import pymupdf as fitz
OUT=Path(__file__).resolve().parents[1];C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');sys.path.insert(0,str(C))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
source=subprocess.check_output(['git','show','9631ea55:scripts/effect_fact_sources.py'],cwd=C);previous=types.ModuleType('scripts.effect_fact_sources');exec(compile(source,'9631ea55','exec'),previous.__dict__)
original=OUT/'masking_cases/cropped_negative_exact_row';target=OUT/'masking_cases/exact_same_pdf_row_mask'
assert not target.exists();target.mkdir()
for name in ['before','after','kit']:shutil.copytree(original/name,target/name)
ev=h.read(original/'external-evidence.json');pdf=target/'kit/pdfs/I/original.pdf'
with fitz.open(pdf) as d:
 p=d[0];left=p.search_for('All')[0];sign=p.search_for('-')[0];broad={'page':1,'quote':'All -','bbox':list(left|sign)}
 for fact in ev['samples'][0]['facts']:fact['source_cell']['row']=broad
 for fact in ev['samples'][0]['facts']:ev['samples'][0]['relation_contracts'][0]['operand_identities'][fact['fact_id']]['source_cell_digest']=h.digest(fact['source_cell'])
 geometry={'minus':list(sign),'real_row_label':list(left),'claimed_row':broad,'total_column_header':list(p.search_for('Total')[0]),'cropped_cell':ev['samples'][0]['facts'][0]['source_cell']['cell'],'complete_row_text':p.get_textbox(fitz.Rect(35,85,520,110)),'source_checks':{k:h.source_errors(v,d,h.sha(pdf)) for k,v in ev['samples'][0]['facts'][0]['source_cell'].items() if isinstance(v,dict)}}
 (OUT/'renders').mkdir(exist_ok=True)
 p.draw_rect(fitz.Rect(broad['bbox']),color=(1,0,0),width=.8)
 p.draw_rect(fitz.Rect(ev['samples'][0]['facts'][0]['source_cell']['cell']['bbox']),color=(0,0,.9),width=.8)
 p.get_pixmap(matrix=fitz.Matrix(2,2),clip=fitz.Rect(25,15,530,120),alpha=False).save(OUT/'renders/row_mask_counterexample.png')
p=target/'external-evidence.json';p.write_text(json.dumps(ev,ensure_ascii=False,indent=2));evidence=h.load_evidence(p,h.sha(p));reports={}
try:
 for label,module in [('963_module',previous),('d71_module',current)]:
  sys.modules['scripts.effect_fact_sources']=module;reports[label]=h.compare_collections(target/'before',target/'after',target/'kit',evidence)
finally:sys.modules['scripts.effect_fact_sources']=current
result={'case':'exact_same_pdf_row_mask','expected':'FAIL','actual':reports['d71_module']['overall'],'expectation_met':reports['d71_module']['overall']=='FAIL','baseline_negative_control':str(original),'changed_inputs':['source_cell.row.quote','source_cell.row.bbox','relation_contracts.operand_identities.*.source_cell_digest'],'all_pdf_and_before_after_bytes_equal':all(h.sha(p)==h.sha(original/p.relative_to(target)) for n in ['before','after','kit'] for p in (target/n).rglob('*') if p.is_file()),'pdf_sha256':h.sha(pdf),'external_input_sha256':h.sha(p),'geometry':geometry,'versions':reports}
(OUT/'exact_same_pdf_masking_pair.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['expected','actual','all_pdf_and_before_after_bytes_equal','geometry']},ensure_ascii=False))
