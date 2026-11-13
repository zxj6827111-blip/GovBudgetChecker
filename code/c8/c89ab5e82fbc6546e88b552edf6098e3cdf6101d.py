import json,gzip,re,hashlib
from pathlib import Path
import pymupdf as fitz
from src.schemas.parse_artifact import ParseArtifact
from src.engine.fact_context import _fragment_for_instance,_column_mappings_for_instance
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs')
OUT=Path(__file__).parent
scope=json.loads((ROOT/'column_evidence_remediation_scope_20261105/范围与逐项证据.json').read_text())
manifest=json.loads((ROOT/'unified_closeout_round6_review_codex_20261104/diagnostic_manifest_10.json').read_text())['items']
norm=lambda s: re.sub(r'\s+','',str(s or ''))
rows=[]
for item in scope['historical_regressions_24']:
 order=item['order'];sid=f'SP-ORIG-{order:02}';oid=item['obligation_id']
 artifact=ParseArtifact.from_dict(json.loads((OUT/(f'{order:02}_artifact.json')).read_text()))
 source=next(x for x in manifest if x['order']==order)
 assert hashlib.sha256(Path(source['pdf_path']).read_bytes()).hexdigest()==source['sha256']
 raw=json.load(gzip.open(OUT/'baseline_main/status'/(sid+'.status.json.gz'),'rt'))
 obligation=next(x for x in raw['check_coverage']['instances'] if x['obligation_id']==oid)
 missing=obligation.get('missing_operands') or []
 unique={json.dumps({k:v for k,v in x.items() if k not in ['check_relation_id','rule_id','sub_check_id']},sort_keys=True):x for x in missing}
 proof=[]
 with fitz.open(source['pdf_path']) as pdf:
  for op in unique.values():
   if op.get('source')!='table':
    proof.append({'operand':op,'verdict':'TEXT_SOURCE_REQUIRES_SEMANTIC_REVIEW'});continue
   matches=[]
   for instance in artifact.logical_tables:
    if instance.table_code!=op.get('table_code'):continue
    table=_fragment_for_instance(artifact,instance)
    for mapping in _column_mappings_for_instance(instance,table):
     if mapping['canonical_measure']!=op.get('column'):continue
     header=mapping.get('source_col_name') or ''
     stage='年初预算' if '年初预算' in header or header.endswith('/预算数') else '决算'
     if op.get('stage') and op['stage']!=stage:continue
     col=mapping['source_col_idx'];labels=mapping.get('source_label_cols') or []
     key=str(op.get('row_key') or '')
     for ri,values in enumerate(table.rows):
      if ri<table.header_row_count:continue
      name=norm(''.join(str(values[c] or '') for c in labels if c<len(values)))
      variants=key.split('或')
      same=(('合计' in name or '总计' in name or not labels) if key=='total' else
            (bool(re.match(r'\d+',name)) and re.match(r'\d+',name).group()==key) if key.isdigit() else
            any(norm(v) and norm(v) in name for v in variants))
      if not same:continue
      cell=next((c for c in table.cells if c.row_idx==ri and c.col_idx==col),None)
      if cell is None or not cell.bbox:continue
      actual=pdf[cell.page_number-1].get_textbox(cell.bbox)
      rawcell=str(cell.raw_text or '')
      verdict=('SOURCE_BLANK' if not norm(actual) and not norm(rawcell) else
               'SOURCE_DASH_UNKNOWN' if norm(actual) in ['—','-','–'] else
               'SOURCE_VALUE_REQUIRES_BINDING_REVIEW')
      matches.append({'page':cell.page_number,'bbox':list(cell.bbox),'literal_row':name,'literal_column_header':header,
                      'source_cell_id':f'{cell.origin_fragment_uid or table.fragment_uid}:r{cell.origin_row_idx if cell.origin_row_idx is not None else cell.row_idx}c{cell.origin_col_idx if cell.origin_col_idx is not None else cell.col_idx}',
                      'native_pdf_box_text':actual,'parser_raw_text':rawcell,'verdict':verdict})
   proof.append({'operand':op,'matches':matches,'verdict':matches[0]['verdict'] if len(matches)==1 else 'POSITION_NOT_UNIQUE_OR_NOT_LOCATED'})
  if not unique and oid=='OBL-STRUCT-EMPTY-STATEMENT':
   for instance in artifact.logical_tables:
    if instance.table_code!='FIN_04_fiscal_grant_total':continue
    table=_fragment_for_instance(artifact,instance)
    fundcols={m['source_col_idx']:m for m in _column_mappings_for_instance(instance,table) if m['canonical_measure'] in ['government_fund_budget','state_capital_budget']}
    for cell in table.cells:
     if cell.row_idx<table.header_row_count or cell.col_idx not in fundcols or cell.is_header or not cell.bbox:continue
     if norm(''.join(str(x or '') for x in table.rows[cell.row_idx])).find('总计')<0:continue
     actual=pdf[cell.page_number-1].get_textbox(cell.bbox)
     proof.append({'fund_column':fundcols[cell.col_idx]['canonical_measure'],'page':cell.page_number,'bbox':list(cell.bbox),'native_pdf_box_text':actual,'verdict':'SOURCE_BLANK' if not norm(actual) else 'SOURCE_VALUE_REQUIRES_BINDING_REVIEW'})
 rows.append({'order':order,'sample_id':sid,'obligation_id':oid,'pdf_sha256':source['sha256'],
              'historical_source':item['source'],'baseline_analysis_revision':raw.get('analysis_revision'),
              'baseline_status':obligation['status'],'baseline_reason_codes':obligation['reason_codes'],
              'baseline_next_action':obligation['next_action'],'source_positions':proof})
(OUT/'historical24_source_positions.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
from collections import Counter
print('position verdicts',Counter(p['verdict']for r in rows for p in r['source_positions']))
for r in rows:print(r['order'],r['obligation_id'],dict(Counter(p['verdict']for p in r['source_positions'])))
