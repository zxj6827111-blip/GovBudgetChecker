from pathlib import Path
import json,gzip,hashlib,subprocess,ast,collections
from scripts import verify_pending_review as gov
from scripts.effect_acceptance_v3 import reproducibility
OUT=Path(__file__).parent.resolve();REPO=Path.cwd();ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs')
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
status=lambda folder,sid:json.load(gzip.open(OUT/folder/'status'/(sid+'.status.json.gz'),'rt'))
scope=read(ROOT/'column_evidence_remediation_scope_20261105/范围与逐项证据.json')
head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
# 每条历史义务保留原记录，当前比较只在同一封面/目录身份的 base/candidate 之间进行。
positions=read(OUT/'historical24_source_positions.json');historical=[]
for original,proof in zip(scope['historical_regressions_24'],positions,strict=True):
 sid=proof['sample_id'];oid=proof['obligation_id'];b=status('cover_r2_base',sid);c=status('cover_r2_d1',sid)
 before=next(i for i in b['check_coverage']['instances']if i['obligation_id']==oid);after=next(i for i in c['check_coverage']['instances']if i['obligation_id']==oid)
 row={'order':original['order'],'document':original['document'],'sample_id':sid,'obligation_id':oid,
      'historical_source':original['source'],'pdf_sha256':proof['pdf_sha256'],
      'current_main_revision':b['analysis_revision'],'candidate_revision':c['analysis_revision'],
      'before':{k:before.get(k)for k in ['status','reason_codes','next_action','detail']},
      'after':{k:after.get(k)for k in ['status','reason_codes','next_action','detail']},
      'source_positions':proof['source_positions'],'source_classification':'ORIGINAL_BLANK_OR_MISSING_DISCLOSURE',
      'independent_acceptance':'NOT_SIGNED'}
 if original['order']==1:
  row['source_evidence']={'page':5,'image':str(OUT/'01_income_p5.png'),'literal_header':'财政拨款收入','missing_disclosure':'独立本年收入合计列','review':'full original page visually checked by Codex; no fiscal column substitution'}
 if after['status']=='completed':
  row.update(status='FIXED_VERIFIED',source_classification='CURRENT_FINAL_ZERO_DISCLOSURE_PREVIOUSLY_UNREAD',
             reason='同年度同主体正文明确决算零/无支出，原表格保持空白；预算缺失仍在其它义务中保留。',source_evidence={'page':18,'declaration':'2024年度无“三公”经费财政拨款支出','scope':'决算；不证明预算'})
 else:
  row.update(status='EXPECTED_UNRESOLVED',reason='有原件空白或披露缺项，未补零、借列或由勾稽反推。软件诊断已按真实来源分类；未完成检查仍保留。')
 if original['order']==7:row['identity_note']='旧归档在父级办公厅；本次 base/candidate 都按此前留存的封面单位裁决绑定，不能把输入身份修正记成解析成效。'
 historical.append(row)
(OUT/'historical24_dispositions.json').write_text(json.dumps(historical,ensure_ascii=False,indent=2))
# 原28条与当前同键逐项对应；N/A说明的来源证明保留，不自动签收范围。
lineage=read(OUT/'m12_lineage.json')
for row in lineage:
 s=status('final_d1',row['sample_id']);key=row['key'];r=next(r for r in s['review_report']['check_receipts']if list(gov._receipt_key(r))==key)
 row.update(candidate_status=r['status'],candidate_reason=r['incomplete_reason'],candidate_revision=s['analysis_revision'],review_status='CANDIDATE_DISPOSITION_COMPLETE_NOT_INDEPENDENTLY_APPROVED')
(OUT/'m12_lineage.json').write_text(json.dumps(lineage,ensure_ascii=False,indent=2))
# 新风险材料增加的3条范围项单独列出，不混入原28条。
report=read(OUT/'cover_r2_governance_self.json');extra=[]
for text in next(c['details']for c in report['checks']if c['id']=='M12'):
 sid,raw=text.split(' ',1);key=ast.literal_eval(raw[:raw.index('):')+1]);s=status('cover_r2_d1',sid);r=next(r for r in s['review_report']['check_receipts']if gov._receipt_key(r)==key)
 extra.append({'sample_id':sid,'key':list(key),'status':r['status'],'reason':r['incomplete_reason'],
   'proposed_disposition':'explanation_only','basis':('正式问题命中的计算说明，不是未完成诊断；不据此签发金额正确性。'if r['status']=='fail'else'条件性比例检查未出现对应占比声明；说明不代表其它义务完成。'),
   'case_id':gov.receipt_scope_id({'sha256':s['checksum']},r),'raw_status_sha256':sha(OUT/'cover_r2_d1/status'/(sid+'.status.json.gz')),'approval':'NOT_SIGNED'})
(OUT/'additional_scope3_candidate.json').write_text(json.dumps(extra,ensure_ascii=False,indent=2))
# 当前工具重新检查真实双跑；版本与原始采集元数据均保留。
repeats={}
for a,b in [('final_d1','final_d2'),('cover_r2_d1','cover_r2_d2')]:
 r=reproducibility(OUT/a,OUT/b);repeats[a]=r;(OUT/(a+'_reproducibility_final.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2))
identities={}
for name in ['scripts/acceptance_artifacts.py','scripts/adapt_acceptance_collection.py','scripts/verify_pending_review.py','scripts/effect_acceptance_v3.py']:
 identities[name]=sha(REPO/name)
identity={'source_commit':head,'policy':'real-painpoint-effect-v3','adapter':'review-report-adapter-v3','governance':'unified-governance-v1-candidate','files':identities,'contracts':{str(p.relative_to(REPO)):sha(p)for p in (REPO/'contracts/pending_review_r4').glob('*.json')},'status':'CANDIDATE_NOT_APPROVED'}
(OUT/'tool_identity.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2))
ledger=read(OUT/'ledger.json');ledger.update(candidate_code_commit=head,overall='CANDIDATE_REQUIRES_INDEPENDENT_REVIEW',daily_service='UNCHANGED_MAIN_47581360',tool_identity=str(OUT/'tool_identity.json'),reproducibility=repeats)
ledger['work_packages']={
 'SP-00':{'implementation':'SOURCE_AUDIT_AND_ADAPTER_IMPLEMENTED','independent_acceptance':'NOT_SIGNED','evidence':['docs/unified_remediation_20261106/source_audit.json','adapted160_final/adapter_audit.json']},
 'SP-01':{'implementation':'SOURCE_DRIVEN_DIAGNOSTICS_AND_FINAL_ZERO_READING_FIXED','material_unknowns':'PRESERVED','independent_acceptance':'NOT_SIGNED','evidence':['historical24_dispositions.json','monetary_fact_preservation.json']},
 'SP-02':{'implementation':'INDEPENDENT_SNAPSHOT_REQUIRED_NO_RECEIPT_SELF_PROOF','development_controls':'PASS','independent_acceptance':'NOT_SIGNED'},
 'SP-03':{'implementation':'SAMPLE_REVISION_OBJECT_BOUND_AUTHORIZATION','development_controls':'PASS','independent_acceptance':'NOT_SIGNED'},
 'SP-04':{'implementation':'EVIDENCE_LOSS_AND_FORGED_LOCATION_REJECTED','development_controls':'PASS','independent_acceptance':'NOT_SIGNED'},
 'SP-05':{'implementation':'14_OF_14_REFERENCE_CLOSURES_VERIFIED','issues_deleted':0,'independent_acceptance':'NOT_SIGNED','evidence':['reference14_current.json']},
 'SP-06':{'implementation':'4_OF_4_AGGREGATE_STATUS_FIXES_VERIFIED','underlying_unresolved_checks':'PRESERVED','independent_acceptance':'NOT_SIGNED','evidence':['t13_current.json']},
 'SP-07':{'implementation':'28_CANDIDATE_DISPOSITIONS_COMPLETE_PLUS_3_NEW_SCOPE_ITEMS','independent_acceptance':'PENDING_SCOPE_SIGNOFF','evidence':['m12_lineage.json','additional_scope3_candidate.json','cover_r2_governance_self.json']},
 'SP-08':{'implementation':'24_CANDIDATE_DISPOSITIONS_COMPLETE','FIXED_VERIFIED':sum(x['status']=='FIXED_VERIFIED'for x in historical),'EXPECTED_UNRESOLVED':sum(x['status']=='EXPECTED_UNRESOLVED'for x in historical),'independent_acceptance':'NOT_SIGNED','evidence':['historical24_dispositions.json']}}
ledger['validation']={'full_python':{'passed':5221,'skipped':196,'code_commit':'4829d2b','scope':'完整回归；之后的验收器修订单独跑当前正负控'},'current_tools':{'passed':129,'log':'final-tool-tests.log'},'ruff':'PASS','mypy':'579 source files PASS','real_api_flow':'flow/evidence/user_flow/verification.json','real_browser_flow':'flow/evidence/user_flow/browser_verification.json','scope_gate':'PENDING: 3 additional scope items','effect_gate':'NOT_PASSED: independent evidence/authorization missing; see effect_final_sealed/effect_report.json'}
ledger['separate_gates']={'independent_review':'NOT_COMPLETED','main_merge':'NOT_PERFORMED','runtime_switch':'NOT_PERFORMED'}
ledger['capability_tracks']={'B1':'NOT_ACCEPTED','original_seven_disputes':'NOT_COMPLETED','budget':'NOT_ACCEPTED','OCR':'NOT_ACCEPTED','AI':'NOT_ACCEPTED'}
ledger['collection_note']='27风险样本双跑保留原历史输入身份；封面绑定的补充材料另做6样本当前main/candidate/重复采集。集合交叉，不相加为材料数或缺陷数；工具-only再适配不改旧工件/meta。'
(OUT/'ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
print('historical24',collections.Counter(x['status']for x in historical),'repeats', {k:v['verdict']for k,v in repeats.items()})
