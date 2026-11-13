import json,re,csv
from pathlib import Path
from decimal import Decimal
from collections import defaultdict,Counter
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def norm(s):return re.sub(r'\s+','',str(s or '')).replace('，',',').replace('−','-')
def num(s):
 try:return Decimal(norm(s).replace(',',''))
 except:return None
def anchors(facts):
 out=defaultdict(list)
 for f in facts:
  seen=set()
  for ref in f.get('extra_dims',{}).get('evidence_refs',[]):
   key=(ref.get('physical_page'),tuple(ref.get('bbox') or []))
   if key in seen or len(key[1])!=4:continue
   seen.add(key);out[key[0]].append((f,ref))
 return out
def at(c,index):
 b=c['bbox'];x=(b[0]+b[2])/2;y=(b[1]+b[3])/2;found={}
 for f,ref in index.get(c['physical_page'],[]):
  q=ref['bbox'];ix=max(0,min(q[2],b[2])-max(q[0],b[0]));iy=max(0,min(q[3],b[3])-max(q[1],b[1]));area=max(1e-9,(b[2]-b[0])*(b[3]-b[1]))
  # Match physical position before inspecting the value. Never match equal-amount sets.
  if q[0]-.5<=x<=q[2]+.5 and q[1]-.5<=y<=q[3]+.5 and ix*iy/area>=.6:found[f['fact_uid']]=(f,ref)
 return list(found.values())
scenes={}
for p in (ROOT/'runtime_native').glob('*/immutable_snapshot.json'):
 if p.parent.name=='native_correction':continue
 s=json.loads(p.read_text());scenes[s['source_file_hash']]=(p.parent,s)
summary=[]
for manifest in json.loads((ROOT/'source_manifest.json').read_text()):
 obs=json.loads((ROOT/'original_occurrences'/manifest['doc_id']/'observations.json').read_text());matched=scenes.get(manifest['sha256'])
 if not matched:
  missing={'doc_id':manifest['doc_id'],'file':manifest['file'],'status':'no_immutable_snapshot','candidate_cells':len(obs['cells']),'source_sha256':manifest['sha256'],'recognition_accuracy':None,'correct_auto_recovery_coverage':None};summary.append(missing);write(ROOT/'cell_comparisons'/manifest['doc_id']/'comparison.json',{'summary':missing,'human_verified':False,'annotation_complete':False,'cells':obs['cells'],'comparison':'snapshot_unavailable_pending_or_execution_failed','scan_readable_cells_excluded_from_denominator':False,'full_original_cell_denominator':'not_established_for_scan'});continue
 path,snapshot=matched;index=anchors(snapshot['raw_context']['facts']);checks=[]
 for c in obs['cells']:
  found=at(c,index);rec={**c,'source_sha256':manifest['sha256'],'snapshot_hash':snapshot['snapshot_hash'],'observed_fact_uids':[f['fact_uid'] for f,_ in found],'facts':[{'fact_uid':f['fact_uid'],'raw_value':f['raw_value'],'normalized_value':f['normalized_value'],'unit':f.get('unit'),'subject_code':f.get('subject_code'),'subject_name':f.get('subject_name'),'table_code':f.get('table_code'),'indicator':f.get('indicator'),'source_header':f.get('extra_dims',{}).get('source_header'),'budget_stage':f.get('budget_stage'),'recognition_status':f.get('recognition_status')} for f,_ in found]}
  if c['literal_kind']!='amount':rec['comparison']='blank_amount_candidate' if found else 'blank_preserved_no_amount_at_anchor'
  elif not found:rec['comparison']='no_fact_at_original_candidate'
  elif len(found)>1:rec['comparison']='ambiguous_fact_anchor'
  else:
   f,_=found[0];fails=[]
   if norm(f['raw_value'])!=c['normalized_lexeme']:fails.append('literal')
   if c['source_unit']!='unknown' and f.get('unit')!=c['source_unit']:fails.append('unit')
   if f.get('fiscal_year')!=2022:fails.append('year')
   if f.get('org_name')!=manifest['organization_filename']:fails.append('organization_requires_alias_review')
   if f.get('budget_stage') not in ['年初预算','全年预算','调整后预算']:fails.append('budget_stage')
   row=norm('|'.join(str(x or '') for x in c['source_row']))
   name=norm(f.get('subject_name'))
   if name and name not in row and name not in ['合计','收入总计','支出总计']:fails.append('row_name_requires_visual_review')
   if num(f['raw_value'])!=num(c['normalized_lexeme']):fails.append('numeric_value')
   rec['comparison']='candidate_mismatch' if fails else 'literal_unit_basic_identity_candidate_match'
   rec['mismatch_fields']=fails;rec['automatically_admitted']=f.get('recognition_status')in ['unverified','verified']
  checks.append(rec)
 counts=dict(Counter(c['comparison']for c in checks));row={'doc_id':manifest['doc_id'],'file':manifest['file'],'status':'machine_compared_no_human_truth','candidate_cells':len(checks),'candidate_amounts':sum(c['literal_kind']=='amount'for c in checks),'zero_candidates':sum(c['literal_kind']=='amount'and num(c['normalized_lexeme'])==0 for c in checks),'counts':counts,'snapshot_hash':snapshot['snapshot_hash'],'recognition_accuracy':None,'correct_auto_recovery_coverage':None}
 summary.append(row);write(ROOT/'cell_comparisons'/manifest['doc_id']/'comparison.json',{'summary':row,'annotation_complete':False,'human_verified':False,'cells':checks,'algorithm':'source-only PyMuPDF physical grid; position first; literal/unit/basic identity after match; no full semantic or visual truth'})
write(ROOT/'cell_comparison_summary.json',{'scope':'machine_auxiliary_not_independent_accuracy','human_verified':False,'annotation_complete':False,'documents':summary})
# Compare limited model visual observations; mark unknown locations separately.
visual=json.loads((ROOT/'model_visual_auxiliary_cells.json').read_text())['cells'];vv=[]
for c in visual:
 scene=scenes.get(c['source_sha256']);rec=dict(c)
 if not c.get('bbox'):rec['comparison']='bbox_unknown_cannot_bind'
 elif scene is None:rec['comparison']='snapshot_unavailable'
 else:
  found=at(c,anchors(scene[1]['raw_context']['facts']));rec['observed_facts']=[f for f,_ in found]
  rec['comparison']='visual_literal_matches_candidate' if len(found)==1 and norm(found[0][0]['raw_value'])==norm(c['visual_literal']) else 'missing_or_conflicting_candidate_requires_review'
 vv.append(rec)
write(ROOT/'model_visual_comparison.json',{'human_verified':False,'annotation_complete':False,'counts':dict(Counter(x['comparison']for x in vv)),'cells':vv})
print('compared',len(scenes),'documents',dict(sum((Counter(r.get('counts',{})) for r in summary),Counter())))
