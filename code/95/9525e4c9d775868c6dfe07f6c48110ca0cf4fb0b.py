"""同一真实原件全部已保存响应的最新重建；不增加推理、不改变断点轮次。"""
from pathlib import Path
import json,sys,hashlib,time,collections,csv
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'validation-runtime-v28'))
from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.lossless_json import pack_jsonb
OUT=BASE/'complete-income-replay-v28';OUT.mkdir(exist_ok=False)
source=BASE/'income-boundaries-v13/artifact.json'
started=time.monotonic()
def hash_json(data):
 h=hashlib.sha256()
 for piece in json.JSONEncoder(ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(data):h.update(piece.encode())
 return h.hexdigest()
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
original_hash=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()
with source.open() as f:payload=json.load(f)
print('loaded original carrier',source.stat().st_size,flush=True)
# 仅清除可再生逻辑副本，原字符、物理格、原始响应及坐标变换全部保留。
payload['table_fragments']=[t for t in payload['table_fragments'] if not t['fragment_uid'].startswith('image-logical-')]
payload['logical_tables']=[t for t in payload['logical_tables'] if not t['instance_uid'].startswith('image-logical-')]
for key in ('logical_cell_candidates','resolved_logical_cells','reconstruction_diagnostics'):payload[key]=[]
payload['reconstructed_tables']={}
a=ParseArtifact.from_dict(payload);del payload
physical=hash_json([g.to_dict() for g in a.source_grids])
print('raw sources prepared',len(a.source_grids),len(a.coordinate_transforms),flush=True)
context=FiscalInputBuilder().facts(a)
assert hash_json([g.to_dict() for g in a.source_grids])==physical
write('fact_context.lossless.json',pack_jsonb(context.to_dict()))
write('logical-diagnostics.json',a.reconstruction_diagnostics)
with (OUT/'facts.csv').open('w') as stream:
 fields=['fact_uid','page','row','column','table_code','instance_uid','subject_code','subject_name','indicator','raw_value','normalized_value','unit','org_name','report_year','amount_year','period','status','local_qualification','table_extent']
 writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
 for fact in context.facts:
  ref=fact.extra_dims.get('evidence_refs',[{}])[0]
  writer.writerow({'fact_uid':fact.fact_uid,'page':ref.get('physical_page'),'row':fact.row_order,'column':ref.get('source_cell_id'),'table_code':fact.table_code,'instance_uid':fact.instance_uid,'subject_code':fact.subject_code,'subject_name':fact.subject_name,'indicator':fact.indicator,'raw_value':fact.raw_value,'normalized_value':fact.normalized_value,'unit':fact.unit,'org_name':fact.org_name,'report_year':fact.extra_dims.get('report_year'),'amount_year':fact.extra_dims.get('amount_year'),'period':fact.period,'status':fact.recognition_status,'local_qualification':fact.extra_dims.get('ocr_provenance',{}).get('qualification',{}).get('status'),'table_extent':fact.extra_dims.get('table_extent_status')})
receipt={'source_artifact_sha256':original_hash,'source_file_hash':a.source_file_hash,'runtime':'validation-runtime-v28','new_inference':False,'counters_reset':False,'source_grids_sha256':physical,'physical_grids_unchanged':True,'facts':len(context.facts),'operands':len(context.definite_facts()),'locally_qualified':sum(f.extra_dims.get('ocr_provenance',{}).get('qualification',{}).get('status')=='qualified' for f in context.facts),'facts_by_page':dict(collections.Counter(f.extra_dims['evidence_refs'][0]['physical_page'] for f in context.facts)),'statuses':dict(collections.Counter(f.recognition_status for f in context.facts)),'instances':[{'code':i.table_code,'start':i.page_start,'end':i.page_end,'fragments':len(i.fragment_uids)} for i in a.logical_tables],'elapsed_seconds':time.monotonic()-started,'independent_acceptance':False}
write('receipt.json',receipt)
print(json.dumps(receipt,ensure_ascii=False),flush=True)
assert hashlib.file_digest(source.open('rb'),'sha256').hexdigest()==original_hash
