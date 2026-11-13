import json,collections
from pathlib import Path
import pymupdf as fitz
root=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
allrows=[]
for r in json.loads((root/'source_manifest.json').read_text()):
 pages=[]
 with fitz.open(r['frozen_path'])as pdf:
  for n,p in enumerate(pdf,1):
   traces=p.get_texttrace();seen=set();duplicates=hidden=offpage=chars=0
   for span in traces:
    for c in span['chars']:
     chars+=1;key=(c[0],tuple(round(v,2)for v in c[3]))
     duplicates+=int(key in seen);seen.add(key)
    hidden+=len(span['chars']) if span.get('opacity')==0 else 0
    box=fitz.Rect(span['bbox']);offpage+=int(not p.rect.intersects(box))
   pages.append({'physical_page':n,'trace_char_count':chars,'duplicate_position_character_candidates':duplicates,'zero_opacity_character_candidates':hidden,'text_spans_outside_page_candidates':offpage,'visibility_status':'machine_trace_only_requires_render_review_if_flagged'})
 out=root/'text_layer_supplement'/f"{r['doc_id']}.json";out.parent.mkdir(exist_ok=True);out.write_text(json.dumps({'source_sha256':r['sha256'],'pages':pages},ensure_ascii=False,indent=2));allrows.extend({'doc_id':r['doc_id'],**p}for p in pages)
(root/'text_layer_supplement_summary.json').write_text(json.dumps({'pages_checked':len(allrows),'flagged_pages':[p for p in allrows if any(p[k]for k in ['duplicate_position_character_candidates','zero_opacity_character_candidates','text_spans_outside_page_candidates'])],'bad_text_layer_confirmed':False,'note':'Trace anomalies are triage candidates; no OCR accuracy or visual-completeness conclusion'},ensure_ascii=False,indent=2));print('checked',len(allrows))
