import json,csv,hashlib,difflib,datetime,collections,platform,sys
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021'
def read(p):return json.loads((OUT/p).read_text())
def write(p,x):(OUT/p).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=read(Path('原件清单.json'));native=read(Path('runtime_native/service_receipt.json'));scan=read(Path('runtime_scan/service_receipt.json'));old=read(Path('runtime/service_receipt.json'));controls=read(Path('continued_controls/progress.json'));v2=read(Path('continued_controls_v2/receipt.json'));fields=read(Path('database_full_field_summary.json'));cells=read(Path('cell_comparison_summary.json'));tables=read(Path('table_reconstruction_audit.json'));ui=v2['steps']['all_material_pages'];recovery=v2['steps']['extended']['recovery'];visual=read(Path('model_visual_comparison.json'));integrity=read(Path('evidence_integrity.json'))
byhash={r['source_sha256']:r for r in native['scenarios']if r['scenario']!='native_correction'};bycomp={r['doc_id']:r for r in cells['documents']};byui={r['doc_id']:r for r in ui['records']};dbfields={r['case']:r for r in fields['documents']};outputs=[];raw_total=completed=applicable=0;statuses=collections.Counter();counts=collections.Counter();pending=0
for r in manifest:
 execution=byhash[r['sha256']];case=OUT/'runtime_native'/execution['scenario'];s=read(Path('runtime_native')/execution['scenario']/'immutable_snapshot.json') if (case/'immutable_snapshot.json').exists()else None
 meta=read(Path('runtime_native')/execution['scenario']/'status.json').get('result',{}).get('meta',{})if(case/'status.json').exists()else{}
 cov=meta.get('obligation_coverage',{});rec=meta.get('recognition_state',{});comp=bycomp[r['doc_id']];row={'doc_id':r['doc_id'],'file':r['file'],'sha256':r['sha256'],'pages':r['pages'],'year':r['year'],'region':r['region'],'organization_source':r['organization'],'document_type':'budget','source_type':'real_scan'if r['doc_id']=='SH2022-46'else'native','profile':'OCR_enabled_scan_observation'if r['doc_id']=='SH2022-46'else'native_layer_OCR_disabled','job_id':execution['job_id'],'terminal_status':execution.get('terminal_status','error'),'elapsed_seconds':execution['elapsed_seconds'],'raw_facts':len(s['raw_context']['facts'])if s else None,'effective_facts':len(s['effective_context']['facts'])if s else None,'revision_id':s['revision_id']if s else None,'snapshot_hash':s['snapshot_hash']if s else None,'storage_readback_passed':dbfields.get(execution['scenario'],{}).get('passed',False),'query_export_version_passed':execution.get('service_validation_passed',False),'four_formats':bool(execution.get('exports')),'browser_first_revision_passed':byui.get(r['doc_id'],{}).get('passed',False),'rule_checks_completed':cov.get('completed_total'),'rule_checks_applicable':cov.get('applicable_total'),'rule_checks_unresolved':cov.get('unresolved_total'),'pending_fact_qualifications':rec.get('pending_fact_qualifications'),'machine_candidate_amount_cells':comp.get('candidate_amounts'),'machine_comparison_counts':comp.get('counts'),'recognition_accuracy':None,'correct_auto_recovery_coverage':None,'human_verified':False,'annotation_complete':False,'scope_note':'存储与版本一致性不等于原件金额/身份完整正确'}
 if r['doc_id']=='SH2022-46':row.update(job_id=scan['scenarios'][0]['job_id'],native_profile_job_id=execution['job_id'],native_disabled_attempt_error=execution.get('error'),terminal_status='processing_at_observation_stop',elapsed_seconds=scan['scenarios'][0]['elapsed_seconds'],scan_job_id=scan['scenarios'][0]['job_id'],environment_observation_timeout_seconds=660,pending_pages=25,scope_note='扫描首轮未完成，无已发布事实快照；文字层关闭OCR尝试另存，不作为扫描验收通过')
 if s:
  raw_total+=len(s['raw_context']['facts']);completed+=cov.get('completed_total',0);applicable+=cov.get('applicable_total',0);statuses.update(f['recognition_status']for f in s['raw_context']['facts']);pending+=rec.get('pending_fact_qualifications',0)
 counts.update(comp.get('counts',{}));outputs.append(row)
write(Path('逐份执行结果.json'),outputs)
with (OUT/'逐份执行结果.csv').open('w',encoding='utf-8-sig',newline='')as f:
 keys=sorted({k for r in outputs for k in r});w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(outputs)
# Flatten every occurrence; repeated amounts remain separate physical rows.
with (OUT/'逐格机器辅助核对.csv').open('w',encoding='utf-8-sig',newline='')as f:
 keys=['doc_id','occurrence_id','source_sha256','physical_page','bbox','row_idx','col_idx','literal_kind','raw_value','source_unit','comparison','mismatch_fields','observed_fact_uids','facts','human_verified','annotation_complete'];w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
 for p in sorted((OUT/'cell_comparisons').glob('*/comparison.json')):
  for c in json.loads(p.read_text())['cells']:w.writerow({k:({'doc_id':p.parent.name,'human_verified':False,'annotation_complete':False}|c).get(k)for k in keys})
with (OUT/'表格重建与事实覆盖.csv').open('w',encoding='utf-8-sig',newline='')as f:
 w=csv.DictWriter(f,fieldnames=list(tables['tables'][0]));w.writeheader();w.writerows(tables['tables'])
summary={'schema_version':'gbc.new-material-acceptance.v1','recorded_at':datetime.datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(),'verdict':'not_accepted','materials':48,'pages':2199,'document_kinds':{'budget':48,'final':0},'development_hash_duplicates':0,'native_documents':47,'real_scan_documents':1,'native_profile_chain_completed':47,'native_profile_chain_attempted':48,'scan_ocr_full_documents_completed':0,'scan_pending_pages_at_stop':25,'unique_original_raw_facts_read_back_all_fields':raw_total,'raw_fact_statuses':dict(statuses),'raw_effective_database_query_export_bindings_passed':47,'four_format_new_material_export_sets':47,'new_material_exports':188,'functional_page_suite_with_isolated_rate_10000':{'passed':ui['passed'],'documents':47},'default_rate_page_suite':{'passed':False,'documents':47,'passed_documents':6,'proxy_503':40,'auth_401':1},'first_execution_frozen_files':708,'integrity_passed':integrity['passed'],'first_execution_rule_checks':{'published_native_documents_only':47,'completed':completed,'applicable':applicable,'completion_rate':completed/applicable,'scanned_document_checks':'not_processed_no_invented_denominator'},'machine_candidate_comparison_counts':dict(counts),'human_truth_cells':0,'recognition_accuracy':None,'correct_auto_recovery_coverage':None,'formal_quota_met':False,'formal_gate_accepted':False,'independent_truth_confirmed':False,'model_visual_auxiliary_counts':visual['counts'],'model_visual_human_verified':False,'numeric_correction_control_passed':native['scenarios'][-1]['correction_validation_passed'],'mapping_latest_job_control_passed':controls['steps']['mapping_latest_job']['passed'],'historical_controls_passed':controls['steps']['history']['passed'],'backup_recovery_passed':v2['steps']['extended']['passed'],'restarted_current_historical_exports_byte_identical':recovery['restarted_verified_exports'],'production_logic_changed':False,'business_write_commands_issued':False,'report_paths':['中文验收报告.txt','逐份执行结果.csv','defects.csv','逐格机器辅助核对.csv'],'profile_elapsed_seconds':{'initial_OCR_native_observation':old['elapsed_seconds'],'native_layer_batch_and_first_controls':native['elapsed_seconds'],'real_scan_observation':scan['elapsed_seconds']},'specific_final_rules_not_covered':['V33-005','V33-222','V33-240']}
write(Path('summary.json'),summary)
# Record harness adaptation separately from protected production code.
for original,local in [('scripts/run_isolated_service_validation.py','run_native_batch.py'),('scripts/validate_extended_acceptance.py','extended_all_pages.py')]:
 delta=''.join(difflib.unified_diff((ROOT/original).read_text().splitlines(True),(OUT/'harness'/local).read_text().splitlines(True),fromfile=original,tofile='harness/'+local));(OUT/'harness'/(local+'.diff')).write_text(delta)
write(Path('harness_manifest.json'),{p.name:sha(p)for p in (OUT/'harness').glob('*')if p.is_file()})
text=f'''GovBudgetChecker 上海2022年新材料验收报告
记录时间：{summary['recorded_at']}（America/Los_Angeles）
结论：本轮不予通过正式验收。

一、结论与范围
已实际测试48份、2199页。原件内容为2022年上海预算公开材料，不能按“决算测试”目录名当成决算。
47份有文字层，1份25页真实扫描件。与交付的103份开发材料及 samples/tests/fixtures PDF按SHA256对比，未发现字节重复；近重复模板、官方来源URL和独立人员签署未核实。
已完成整批文字层补充链路：47份获得不可变快照并完成上传、解析、规则、入库、查询和四种导出。扫描件的真实OCR观察未完成，不能称整批端到端恢复通过。
原件的财政总表金额事实、三公字段、科目名称和主体存在覆盖缺陷。所有47份已发布结果的状态为review_required，服务验证通过只表示链路/存储一致，不能解释成业务审校通过。
识别正确率和正确自动恢复覆盖率均为null。没有人工逐格真值，不能宣布99.9%或任何整批准确率。
本报告不沿用此前3760项测试或103份开发回归的通过结论。

二、冻结与运行环境
Git HEAD：{(OUT/'baseline/git_head.txt').read_text().strip()}；工作区已有大量未提交修改，记录见baseline/git_status.txt。
冻结并复核569个源码文件，变动0；首次测试708个文件指纹无漂移；48份桌面原件与冻结副本SHA256全部相同。证据：evidence_integrity.json。
新建隔离PostgreSQL16、API、Worker；验收库实际有37项迁移，包含0032—0037。业务库只读核查仍为0031；未向业务库执行写入、未切换业务历史、未重启业务容器。
OCR使用独立端口19021、本地只读模型及当前挂载代码。APP trusted-input-ocr-4.3，协议gbc.ocr.cells.v3，模型指纹9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea，处理指纹757f06928196e5e81459f0ab7f614a470305e5527658c11d79c70dfbf6465f76。
CPU线程1，容器内存限额5GiB，未发生OOM或重启。配置与实际尺寸见baseline/ocr_runtime_fingerprints.json、ocr_observation_summary.json和原始检查点。
实际页面定位150DPI、识别300DPI；图块上限512×256，实际检测张量有不同补白到32倍数的尺寸，均保留原始尺寸/变换/PNG/响应；局部诊断512×256且input_resize_applied=false。450/600DPI补读本轮未触发，不能声称已验收。
没有修改生产解析或规则代码；仅在outputs内编写/修正验收脚本，差异及指纹见harness/和harness_manifest.json。

三、执行顺序、失败和重试
先对交通委开启真实OCR。首轮约602.49秒仍停在封面，计划280块，停止观察时已完成73块；保留207个未处理图块及自动续跑记录，未把该任务记作完成。
随后在独立配置OCR_ENABLED=false下，先验证交通、商务、农场、研究室、化工区、水务6份的文字层链路，再跑完整48份。该配置是文字层补充验证，不能替代开启OCR后的整批验收。
扫描件单独经真实API/Worker/OCR测试，660秒等待预算后仍processing，脚本观测耗时665.71秒。封面计划574块，最终保留58块原始响应，按几何计算516块尚未处理；后续24页未完成调度。脚本结束观察不是生产服务永久失败或自然硬超时的结论。
全批额外重复上传交通原件用于隔离数值校正控制，不计作第49份独立材料。较早交通任务的列映射重分析被“older job cannot overwrite a newer document fact projection”拒绝；首次失败保留在runtime_native，属于验收脚本顺序冲突及正确的旧任务保护。改在最新任务测试，列映射验证通过。
默认100请求/60秒的页面批测触发/api/auth/me 429，版本代理呈现503。首次47项仅6项通过，40项503、1项401，完整结果未删除。失败发生在版本查询、导航之前时，截图可能是上一个页面，不用它冒充失败目标的页面证据。
仅将第二轮隔离API的GOVBUDGET_RATE_LIMIT改为10000后完成47项页面功能验证，未改鉴权、解析、规则或置信度。该通过结论不核销默认配置的限流/错误映射问题，也不是生产容量结论。
各轮实际耗时：{json.dumps(summary['profile_elapsed_seconds'],ensure_ascii=False)}秒；这些轮次有重叠，不能相加当作墙钟总耗时。上传/导出429及退避见run_native.log、api.log；Worker错误和各轮清理状态见对应service_receipt.json。

四、已验证通过的内容
1. 47份新材料共{raw_total}条原始事实，逐笔比较数据库中的24类字段（金额、单位、完整身份、资格、置信度、证据、行序等），差异0。原始/有效快照、兼容投影、真实鉴权HTTP分页查询均与声明版本一致。
2. 每份JSON/CSV/Word/PDF完整导出集共188份，规范化内容及版本校验通过，重复下载逐字节一致。规则事实消费UID/版本、报告metadata、快照hash一致性47份通过；这不是规则业务结论正确率。
3. 提高隔离HTTP额度后的47份实际任务页面，均显示目标首次revision，raw/effective代理绑定同一snapshot，前端JSON导出与首次结果逐字节相同，无浏览器运行时错误。截图和每份记录：continued_controls_v2/extended/all_material_pages/。
4. 真实扫描第7页仅做一个人工指定90度方向的辅助裁片。模型实际收到512×256，识别到两次111,615,328，保存完整原始响应和可逆坐标。该裁片不代表生产整页自动方向识别、格身份或扫描准确率通过。
5. 实际断点续跑保留原有73块全部响应，增加到74块，仍pending；没有把剩余206块记作完成。证据：real_ocr_probes/probe_receipt.json。
6. 在隔离控制任务中显式改一个金额，重分析产生新revision，原始解析事实与证据保留；旧版四种导出仍逐字节相同。最新任务列映射保留raw interpretation，原始/有效视图经数据库和HTTP验证。
7. 历史候选不改当前结果、未验收候选拒绝发布、后段失败整批回滚、发布及文件镜像、旧行ID/内容精确恢复、旧协议无指针回滚均通过。控制凭据明确ISOLATED_CONTROL_TEST_ONLY，不是人工材料验收签署。
8. 实际备份恢复后全库所有表、665个上传文件、审计日志及应用状态一致；重启恢复API后当前/历史共8份导出逐字节相同。证据：continued_controls_v2/extended/extended_receipt.json。

五、发现缺陷（优先修复，不在本轮改生产逻辑）
P1：财政拨款收支预算总表被识别，但无金额事实。47份产物中有46个FIN_04已识别实例全部事实数0、列映射为空；具体原图核实交通物理17页、农场物理10页，缺失包含重复金额和0.00。
P1：三公表公务用车小计/购置/运行和机关运行字段没有对应事实。交通物理27页852.69、150.00、702.69、6149.06及农场物理15页4个0.00均有原图/位置证据。此处单位按原表万元，不与其他元口径混比。
P1：商务委物理12页可见“商贸事务”“行政运行”等名称，事实subject_name却为20113、2011301等编码。资格low_confidence阻止准入是正确保护，但可读名称尚未恢复。
P1：虹桥材料的目录/第3页机构名可读，raw_context.org_name和事实org_name仍为null、资格ambiguous，完整主体身份未完成。
P2：交通物理33页立项依据引用2019政策文号，被BUD-003报为2022报告年份不一致，是上下文误报。
上述文字层缺陷的范围为OCR关闭补充配置。开启OCR并最终完成后是否能补回，本轮尚未验证，不能把补充结果冒充开启OCR的终态。
另有扫描运行未完成、默认请求配额下代理错误映射两项观察。完整9条记录、文件hash、物理格bbox（能定位者）、原图、系统结果、影响和复现方式见defects.json/defects.csv；未知bbox及页码明确保留null。

六、逐格核对及指标纪律
独立于生产解析器，用PyMuPDF从原件网格提取26799个候选格：15542个金额、11257个空白。按物理页/表/行/列/位置逐次保留，不按金额集合去重。
原件候选15542个金额中，14040个位置找到事实，机器字面比较未发现金额差异；其中381个位置另有身份疑点（243名称、138主体）。1502个位置没有对应事实，仍保留在候选分母；这些数量只描述该网格程序覆盖的核心表范围，不是整份原件真值。
11257个候选空白位置没有金额事实。它不等于已逐格光学认证所有空白，也不替代V33-240专项。
模型看原图的125条辅助记录，100条字面匹配候选、8条未恢复、17条扫描观察bbox未知；仅限所列位置，未外推整份准确率。全部human_verified=false、annotation_complete=false，未把生产输出复制为真值。
规则检查完成率：已发布的47份文字层材料{completed}/{applicable}={completed/applicable:.2%}。扫描材料尚未进入规则完成阶段，不能伪造其适用检查分母。规则完成率不是识别正确率。
人工真值分母=0；识别正确率=null；正确自动恢复覆盖率=null；存储一致性仅对上述已产生的{raw_total}条事实验证。缺失事实不因没有入库而被核销。

七、待人工核对、环境边界和未覆盖
2199页均做文字层、字体/方向/图片及字符轨迹清点；198页有机器轨迹风险候选（48页重复位置字符、150页页外文本，可能重叠），不能直接认定为198页坏文字层。未逐页光学签署，见text_layer_supplement/。
真实扫描整页/整份、完全漏区恢复、邻格绑定、原生/OCR冲突裁定、自动90度方向、高分辨率补读均未完成验收。没有用人工生成扫描件替代真实扫描。
本批是预算-only。V33-005/222/240在当前注册表中只属于final，实际预算路由未执行这3项。明确为尚未覆盖，不能用旧决算回归或强制改文种宣布通过。
独立网格候选中未找到负数或破折号；不据此断言原件其他范围没有，也不声称覆盖负数/破折号专项。真实混合页、明确坏文字层、无框线等分层配额未核销。
正式方案要求至少30份（预算/决算各15），文字型至少12、扫描/困难可读至少18，至少3地区、3年度，并有独立人工全格真值和零错误准入。当前48份均上海2022预算，仅1份真实扫描、未完成人工标注，配额和正确率门槛均未通过。
已实际运行正式工具，使用明确draft/空cells、annotation_complete=false的待标注模板作输入完整性检查，返回accepted=false、exit2；空模板的“无已列错误”是空集结果，不能解释为零错误准入证明。证据：formal_gate_result.json、formal_gate_execution.json。

八、交付与后续优先级
原件清单：原件清单.csv/json（source_manifest.json保留最初启发式识别，3份曾因正文含“决算”而误分类，最终以封面/目录/表题复核纠正；扫描年度取可见封面，虹桥主体取第3页，非人工签署）。
逐份执行：逐份执行结果.csv/json；逐物理格机器辅助核对：逐格机器辅助核对.csv、original_occurrences/、cell_comparisons/；表级覆盖：表格重建与事实覆盖.csv。
缺陷：defects.csv/json与defect_evidence/；快照、DB原始查询结果和188份导出：runtime_native/各材料目录；全部断点和原始OCR响应：ocr_enabled_first_observation/、runtime_scan/runtime_uploads/、real_ocr_probes/。
隔离数据库备份保留在各轮acceptance_database.dump和恢复归档内；所有本轮新API/Worker/PostgreSQL/OCR/前端进程已清理，业务库未由本任务写入。
下一步先修预算列映射、三公字段、科目/主体完整身份，再验证真实扫描整页及资源调度；随后处理默认限流错误映射和年份误报。不能通过补零、配平、提升置信度或减少分母制造通过。
若后续修改识别/规则逻辑，本批失败材料作为开发回归证据，不再冒充新独立盲测；另留未接触的跨年度/地区预算与决算、真实扫描/混合/困难原件，补足人工逐格真值后再核验正式门槛。
'''
(OUT/'中文验收报告.txt').write_text(text)
print('report_written','facts',raw_total,'checks',completed,applicable,'defects',len(read(Path('defects.json'))['defects']))
