import json,re,hashlib,unicodedata,collections
from decimal import Decimal
from pathlib import Path
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
def normalize(v):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(v or '')))
comparisons=[]
for label,truth_path,result_dir in [('regression',ROOT/'outputs/putuo_random_check_20261014/frozen_key_field_truth.json',BASE/'after'),('holdout',BASE/'holdout_corrected_truth.json',BASE/'holdout_after')]:
 truth=json.loads(truth_path.read_text())['fields'];results=[];cache={}
 for field in truth:
  sid=field['sample_id']
  if sid not in cache:
   cache[sid]=(json.loads((result_dir/sid/'fact_context.json').read_text()),json.loads((result_dir/sid/'artifact.json').read_text()))
  context,artifact=cache[sid]
  candidates=[]
  for fact in (context or {}).get('facts',[]):
   if fact['table_code']!=field['table_code'] or fact['indicator']!=field['indicator'] or fact['subject_code']!='total':continue
   if (field['phase']=='actual') != (fact['budget_stage']=='决算'):continue
   if field['table_code']=='FIN_06_basic_expenditure' and normalize(fact['subject_name'])!=normalize(field['evidence_label']):continue
   if field['table_code']=='FIN_07_three_public':
    refs=fact.get('extra_dims',{}).get('evidence_refs',[])
    if not any(re.search(r'c'+str(field['source_col_idx'])+r'$',str(r.get('source_cell_id',''))) and r.get('physical_page')==field['page'] for r in refs):continue
   candidates.append(fact)
  record={**field,'observed':[{'value':f['normalized_value'],'raw':f['raw_value'],'fact_uid':f['fact_uid']} for f in candidates]}
  expected=field['value_wan']
  if expected is not None:
   record['status']='match' if len(candidates)==1 and Decimal(candidates[0]['normalized_value'])==Decimal(expected) else 'mismatch'
  else:
   blank=[]
   for table in artifact['table_fragments']:
    if table['table_code']!=field['table_code'] or table['extraction_method'] not in ['lines','lines_strict']:continue
    rows=[]
    if field['table_code']=='FIN_07_three_public':rows=list(range(table['header_row_count'],len(table['rows'])))
    else:rows=[c['row_idx'] for c in table['cells'] if normalize(c['raw_text'])==normalize(field['evidence_label'])]
    blank.extend(c for c in table['cells'] if c['row_idx'] in rows and c['col_idx']==field['source_col_idx'] and c['page_number']==field['page'])
   record['status']='blank_preserved' if not candidates and len(blank)==1 and not blank[0]['raw_text'].strip() and blank[0]['numeric_value'] is None and blank[0]['native_blank_verified'] else 'blank_mismatch'
   record['source_cells']=[{'raw':c['raw_text'],'native_blank_verified':c['native_blank_verified']} for c in blank]
  results.append(record)
 summary=dict(collections.Counter(r['status'] for r in results));bad=[r for r in results if 'mismatch' in r['status']]
 value={'summary':summary,'mismatches':bad,'fields':results,'truth_sha256':hashlib.sha256(truth_path.read_bytes()).hexdigest()}
 (BASE/(label+'_key_field_comparison.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))
 print(label,summary,'errors',len(bad))
 comparisons.append(value)
assert not any(x['mismatches'] for x in comparisons)
