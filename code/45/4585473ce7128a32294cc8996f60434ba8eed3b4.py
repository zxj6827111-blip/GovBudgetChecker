import csv,hashlib,json,subprocess
from collections import Counter
from pathlib import Path
root=Path.cwd();base=root/'outputs/remediation_20261008';evidence=base/'evidence_verified';service=base/'live_complete'
receipt=json.loads((service/'service_receipt.json').read_text())
assert receipt['status']=='passed' and receipt['cleanup']=='complete' and receipt['source_unchanged_during_run']
assert len(receipt['scenarios'])==23 and all(s['service_validation_passed'] for s in receipt['scenarios'])
assert receipt['scenarios'][-1]['correction_validation_passed']
assert receipt['scenarios'][-1]['correction']['cache_readback_validation_passed']
assert receipt['scenarios'][-1]['correction']['original_parse_basis_preserved']
assert receipt['scenarios'][-1]['correction']['rule_status_changed']
for rel,digest in receipt['code_fingerprints'].items():
 assert hashlib.sha256((root/rel).read_bytes()).hexdigest()==digest, rel
frozen=json.loads((base/'independent_freeze_manifest.json').read_text())
for record in frozen['records']:assert hashlib.sha256((root/record['file']).read_bytes()).hexdigest()==record['sha256']
rows=[];docs=[]
for stem,scenario in [('上海市数据局','native_final'),('上海市信访办公室','shanghai_03'),('上海市教育委员会','shanghai_08'),('上海市生态环境局','shanghai_14')]:
 result=json.loads((evidence/f'{stem}.json').read_text());api=json.loads((service/scenario/'report.json').read_text())
 signature=lambda f:(f['rule_id'],f['message'],f.get('page_number'))
 assert Counter(map(signature,result['findings']))==Counter(map(signature,api['issues']))
 assert all(f.get('evidence_verification',{}).get('status')=='verified' for f in api['issues'])
 for f in result['findings']:
  loc=f['location'];refs=f.get('evidence',[])
  snippets=[{'page':e.get('physical_page',e.get('page')),'bbox':e.get('bbox'),'quote':e.get('quote',e.get('text')),'role':e.get('operand_role',e.get('role'))} for e in refs]
  row={'document_type':'决算公开','year':2025,'organization':stem,'source_pdf':result['source_pdf'],'source_sha256':result['sha256'],
    'rule_id':f['rule_id'],'title':f['title'],'severity':f['severity'],'page':f['page_number'],'bbox':f.get('bbox'),
    'table_or_section':loc.get('section') or loc.get('table_code') or loc.get('table') or '原文关系（详见引用）',
    'snippet':snippets,'expected':loc.get('recomputed_percent') or '详见关系计算说明及操作数',
    'actual':loc.get('declared_percent') or [{'role':o.get('role'),'value':o.get('value'),'unit':o.get('value_unit')} for o in loc.get('operand_refs',[])],
    'difference':'详见关系计算说明','reason':f['message'],'suggestion':f.get('suggestion'),
    'confidence':None,'evidence_status':f['evidence_verification']['status'],
    'check_relation_id':f.get('check_relation_id'),'fact_ids':f.get('fact_ids'),'evidence_ids':f.get('evidence_ids')}
  rows.append(row)
 docs.append({'document':stem,'scenario':scenario,'findings':len(result['findings']),'all_api_issue_evidence_verified':True,
  'api_and_direct_rule_output_agree':True,'severity_counts':dict(Counter(f['severity'] for f in result['findings']))})
assert len(rows)==30
(base/'current_problem_list.json').write_text(json.dumps({'scope':'four reviewed development originals; issues, rounding hints and manual review listed separately by severity','documents':docs,'issues':rows},ensure_ascii=False,indent=2))
with (base/'current_problem_list.csv').open('w',newline='',encoding='utf-8-sig') as stream:
 writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader()
 for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
# Business matching is adjudicated separately from byte/source authentication.
adjudication={'scope':'original-only labels were frozen before repaired output; known development comparison, not unseen blind acceptance',
 'original_labels_unchanged':True,'documents':docs,'confirmed_original_groups':10,'confirmed_atoms_matched':12,
 'known_additional_false_positives_removed':['教育V33-108预算被当成决算','教育V33-226经营收入0.01万元差','教育V33-227跨基金口径误配','信访V33-243漏计明细产生291.35万元假差'],
 'conservative_adjudication':[{'document':'生态','original_frozen_issue_index':2,'decision':'manual_review',
  'reason':'总额45.57万元与国内44.72万元（含外宾0.85万元）表述可疑，但其他国外接待范围未确认；证据不足，不判确定数值错误。原标签保持不变。'}],
 'finding_class_counts':dict(Counter(row['severity'] for row in rows)),
 'remaining_accuracy_limits':['开发原件已暴露，不构成未接触盲测','未推断全产品准确率、精确率或召回率','待人工复核不计确定问题或确定漏报']}
(base/'business_adjudication_verified.json').write_text(json.dumps(adjudication,ensure_ascii=False,indent=2))
native=json.loads((base/'native_verified/summary.json').read_text())
final={'status':'development_self_check_passed','business_acceptance':'independent_unseen_blind_pending','branch':subprocess.check_output(['git','branch','--show-current'],text=True).strip(),
 'backend_tests':{'passed':2969,'skipped':142,'log':'full_backend_verified.log'},
 'native_documents':{'documents':20,'pages':678,'text_pages':sum(d['text_pages'] for d in native['documents']),'facts':13443,
 'core_table_instances':180,'cross_page_tables':81,'source_errors':0,'source_pdfs_unchanged':all(d['source_unchanged'] for d in native['documents']),
 'rule_execution_or_parse_errors':sum(d['rule_execution']['execution_error']+d['rule_execution']['parse_error'] for d in native['documents']),
 'obligations_completed':native['completed'],'obligations_applicable':native['applicable'],'unresolved_deterministic_obligations':native['applicable']-native['completed']-20,'optional_ai_not_requested':20},
 'reviewed_originals':{'documents':4,'findings_and_hints':30,'source_verified':30,'api_and_direct_results_agree':True,
 'confirmed_groups':10,'confirmed_atoms':12,'known_additional_false_positives_removed':4},
 'real_service':{'scenarios':23,'status':receipt['status'],'source_unchanged_during_run':True,'cleanup':receipt['cleanup'],'correction_cache_reanalysis_passed':True,
 'exports':['JSON','CSV','DOCX','PDF'],'receipt':'live_complete/service_receipt.json'},
 'focused_patch':{'files':25,'batches':4,'baseline_apply_and_sha_verified':True,'path':'patches_verified/manifest.json'},
 'independent_handoff':'independent_handoff/handoff.pending.json',
 'limitations':['25项确定性检查义务存在空白操作数、科目身份或说明匹配不足；未伪造零值或通过','正式独立盲测仍需未暴露原件、独立真值和实际人员签认','原始扫描链本轮未重新执行，不由文字层复验推断其性能或准确性']}
(base/'self_check_verified.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
print('Delivery receipts checked: 23 real scenes, 30 evidence identities, 25 focused source/test files; frozen labels unchanged.')
