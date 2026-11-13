from pathlib import Path
import csv,hashlib,json,subprocess,re
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
REPORT=ROOT/'docs/GovBudgetChecker_第十轮整改复核报告_20261005.md'
def dump(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
before=json.loads((OUT/'source_snapshot_before.json').read_text())
after={'head':git('rev-parse','HEAD').decode().strip(),'branch':git('branch','--show-current').decode().strip(),'diff_sha256':hashlib.sha256(git('diff','--binary','HEAD')).hexdigest(),'files':{p:sha(ROOT/p) for p in before['files']},'status':git('status','--short').decode()}
after['changed_protected_files']=[p for p,h in before['files'].items() if h!=after['files'][p]]
after['source_unchanged']=not after['changed_protected_files'] and after['diff_sha256']==before['diff_sha256'] and after['head']==before['head']
assert after['source_unchanged'],after['changed_protected_files'];dump(OUT/'source_snapshot_after.json',after)
audit=json.loads((OUT/'rules_audit.json').read_text()); replay=json.loads((OUT/'root_rules_replay.json').read_text())
original=[]
for row in replay['original_cases']:
 actual={r['sub_check_id']:r['status'] for r in row['final_check_receipts'] if 'share_of_' in r['sub_check_id']}
 expected=next(x['percentage_relation_status'] for x in audit['original_9_cases_summary'] if x['case_id']==row['case_id'])
 assert actual==expected,(row['case_id'],actual,expected)
 original.append({'case_id':row['case_id'],'expected':expected,'actual':actual,'matches':True})
bad=[x['case_id'] for x in replay['neighbors'] if x['expected']!=x['actual']];assert len(bad)==5
base={'document_type':'预决算审校系统实现/验收证据','year':None,'organization':None,'page':None,'bbox':None,'confidence':1.0,'status':'open','confidence_basis':'独立可复现或源码/门禁直接证据；不表示真实业务准确率'}
issues=[]
def add(ident,title,priority,severity,kind,rule,file,line,snippet,expected,actual,reason,suggestion,evidence,inherits=None):
 issues.append({**base,'issue_id':ident,'rule_id':rule,'title':title,'priority':priority,'severity':severity,'kind':kind,'source_file':str(ROOT/file),'source_line':line,'table_or_section':file,'snippet':snippet,'expected':expected,'actual':actual,'difference':'见expected与actual；不对不同主体或年度作有效数值差额','reason':reason,'suggestion':suggestion,'evidence_artifact':str(OUT/evidence),'inherits':inherits})
add('R10-01','年度数字使未知主体检查失效',1,'high','code','V33-103','src/engine/rules_v33.py',432,'第二实验中学2025年财政拨款收入50万元，占50%。','无法确认学校主体：insufficient_data；已确认本单位独立真错误仍fail','学校声明被接纳为本单位，fail并输出100%≠50%；同句本单位99.89%被冲突抑制','任意数字豁免未知限定词；学校主体与年度从错误证据中丢失','解析年份/金额后仍验证主体；未知主体不进入本单位接受集，保留原文和独立已确认声明','root_rules_replay.json')
add('R10-02','往年口径跨数字分句丢失',1,'high','code','V33-103/V33-104','src/engine/rules_v33.py',425,'2024年本单位上级补助收入0万元，事业收入0万元，财政拨款收入800万元，占80%。','保持2024年上下文；无同年事实时insufficient_data','用2025年事实输出100%≠80%；支出同类输入输出60%/40%≠50%/50%','年度只检查末段，不能与主体一同继承；引文丢失年度','主体/年度/阶段按共同作用域继承与重置，金额及比例均按同口径绑定，保留年度锚点','root_rules_replay.json')
add('R10-03','真值组配额与评测分组契约不一致',2,'medium','acceptance_contract','ZC07/truth_group','scripts/evaluate_golden_corpus.py',366,'DOC-001-income / DOC-001-expense','不同真实缺陷为两个组；同缺陷多证据面明确归一；配额与指标身份一致','配额按原ID为2组，指标剥尾部小写后同为DOC-001-，计1组','任意英文尾缀启发式造成身份碰撞，新材料说明未约束且与配额不同','定义canonical truth group，统一配额/指标函数，明确旧T4a/T4b兼容、独立annotation身份与命名示例','root_acceptance_contract_probe.json')
add('R10-04','精确定位分母文档表述过宽',3,'low','documentation','ZC07/exact_location','docs/ZC07_真实验收数据格式.md',76,'golden.labels的每个真值定位框都进入分母','明确现行仅defect标签定位区域进入正式分母','1 defect + 1 acceptable + 1 manual_review的3个框，truth_total=1','文档所有真值框表述与正式函数defect筛选不一致','按现行口径修正文档，其他标签单独诊断；如改变口径先冻结并同步实现/测试','root_acceptance_contract_probe.json')
add('R10-05','可信OCR尚未交付',2,'manual_review','acceptance_gap','OCR/trusted_operand','src/services/ocr_fact_promotion.py',290,'confidence=_PROMOTED_CONFIDENCE；固定0.75','可追溯的真实识别置信度、可信操作数资格、原件认证及正负扫描材料实跑','晋升事实仍low_confidence，可信确定性操作数未形成；模型清单缺失；本轮是8项任务文档','实现与真实模型/材料闭环仍未交付，工程通过不能核销OCR','完成真实逐格分数、数字/单位/主体/年度/阶段/bbox/指纹和认证，提交高质量与歧义扫描实跑','zc07_preflight.json','R8-03')
add('R10-06','独立冻结材料与正式四场景仍不齐备',1,'manual_review','acceptance_gap','ZC07/business_acceptance','docs/冻结盲测材料要求_20261005.md',20,'开发32份+盲测32份、8类分层、盲测100个真实缺陷组','独立冻结真值/区域/必查分母，完整4真实场景及同源同配置同版本重复运行通过','正式not_passed/exit2；空模板incomplete；仅2非OCR链通过且budget合成，两者review_required','缺少独立业务分母、可信OCR和正式完整场景，不可计算精确率/召回等业务指标','先对齐验收契约，再备齐独立冻结材料和4真实服务链，用正式门禁评价','zc07_preflight.json','R8-04')
summary={'review_round':10,'target_remediation_round':9,'scope':'独立代码与验收证据复核','document_type':'预决算审校系统实现','year':None,'organization':None,'overall_assessment':'原反例修复、工程回归与2非OCR服务链通过；2新代码缺陷、2契约/文档问题、2沿用验收缺口；整体业务验收不通过','business_acceptance':'not_passed','risk_flags':['主体/年度绑定边界','可信OCR未闭环','独立冻结业务验收缺失','真值分组契约不一致'],'findings_count':len(issues),'new_code_findings':2,'new_contract_or_doc_findings':2,'continuing_acceptance_gaps':2,'source_unchanged_during_review':after['source_unchanged'],'head':after['head'],'diff_sha256':after['diff_sha256']}
metrics={k:None for k in ['formal_issue_precision','real_issue_recall','major_issue_misses','exact_location_rate','evidence_authenticity_rate','out_of_bounds_count','required_check_completion_rate','receipt_coverage_rate','business_export_agreement_rate','business_repeatability_rate']}
dump(OUT/'findings.json',{'summary':summary,'issues':issues,'manual_review':[i['issue_id'] for i in issues if i['kind']=='acceptance_gap'],'business_metrics':metrics,'validation':{'pytest':{'passed':2465,'skipped':137,'failed':0},'remediation_pytest':{'passed':247,'skipped':2,'failed':0},'original_counterexamples':original,'neighbor_checks':{'count':37,'mismatches':5,'cases':bad,'distinct_causes':2},'fresh_real_non_ocr_service_chains':2,'current_synthetic_export_views':5,'gated_export_bundles':9,'formal_zc07_exit_code':2},'next_actions':['修主体/年度绑定并补回执/证据断言','统一真值组及精确定位分母契约','实现并实跑可信OCR','独立冻结32+32材料、100真实缺陷组及4真实场景业务验收']})
fields=['issue_id','rule_id','title','priority','severity','kind','status','inherits','source_file','source_line','document_type','year','organization','page','bbox','table_or_section','snippet','expected','actual','difference','reason','suggestion','confidence','evidence_artifact']
with (OUT/'findings.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(issues)
dump(OUT/'remediation_ledger.json',{'round':10,'original_items':[{'id':'R8-01','status':'closed_for_original_contract','evidence':'原局部分母反例及正确/错误阳性对照符合预期'},{'id':'R8-02','status':'partial_original_counterexamples_closed','new_linked_findings':['R10-01','R10-02'],'evidence':'原教育局及有限画像传递通过；未知主体含年度及往年分句继承仍失败'},{'id':'R8-03','status':'open_implementation_and_runtime','current_id':'R10-05','evidence':'8项任务文档不代替可信OCR实现/模型/实跑'},{'id':'R8-04','status':'open_independent_business_acceptance','current_id':'R10-06','evidence':'2非OCR链与空模板不代替独立冻结真值及4真实场景'}],'new_findings':[i['issue_id'] for i in issues[:4]],'overall_business_acceptance':'not_passed','business_metrics':metrics})
links=re.findall(r'\]\(<([^>]+)>\)',REPORT.read_text());missing=[]
for target in links:
 path=re.sub(r':\d+$','',target)
 if not Path(path).exists():missing.append(target)
assert not missing,missing
check={'report_exists':True,'report_absolute_links_checked':len(links),'missing_links':missing,'protected_source_files':len(after['files']),'source_unchanged':True,'original_9_contracts_match':True,'root_neighbor_mismatches_match_agent':set(bad)=={x['case_id'] for x in audit['adjacent_boundary_summary'] if not x['contract_matches']},'structured_issue_count':6,'business_metrics_all_null':all(v is None for v in metrics.values())};assert check['root_neighbor_mismatches_match_agent'];dump(OUT/'artifact_qa.json',check)
files=[p for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='artifact_manifest.json'];dump(OUT/'artifact_manifest.json',{'report':{'path':str(REPORT),'sha256':sha(REPORT)},'artifacts':[{'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size} for p in files],'source_diff_sha256':after['diff_sha256']})
print(json.dumps({'findings':6,'original_replays':len(original),'neighbor_mismatches':len(bad),'protected_source_unchanged':True,'report_links_checked':len(links),'artifact_count':len(files)},ensure_ascii=False))
