from pathlib import Path
import json,hashlib,subprocess,shutil,collections,xml.etree.ElementTree as ET,datetime
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');o=w/'outputs/ir3_gap02_fix_20261106';base='d71c9ad7eb671ff956b2fcf21d814a5e9c0cc640'
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(n):return json.loads((o/n).read_text())
def dump(n,v):(o/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
cmds=read('check_commands.json');assert len(cmds)==12 and all(c['exit_code']==0 and c['candidate_sha']==head for c in cmds)
assert all(sha(o/c['log'])==c['log_sha256'] for c in cmds)
controls={}
for name in ['effect_boundary_controls','narrative_boundary_controls','extra_effect','extra_narrative','scope_v2_controls','neighbor_effect','neighbor_narrative','masking_boundary','legal_row_label']:
 rows=read('receipts/'+name+'.json');assert all(r['expectation_met'] for r in rows),name
 controls[name]={'cases':len(rows),'expectations_met':len(rows),'sha256':sha(o/'receipts'/f'{name}.json')}
assert sum(r['cases'] for r in controls.values())==102
pair=read('exact_same_pdf_masking_pair_replay.json');assert pair['outcomes']['candidate_module']=={'normal_All':'FAIL','expanded_All_minus':'FAIL'}
assert pair['outcomes']['d71_module']=={'normal_All':'FAIL','expanded_All_minus':'PASS'}
for name,count in [('same_original_inputs64.json',64),('same_original_masking_inputs.json',8)]:
 rows=read(name)['cases'];assert len(rows)==count and all(r['expectation_met'] for r in rows)
 for r in rows:
  source,copy=Path(r['source']),Path(r['copy'])
  assert all(sha(source/p)==v==sha(copy/p) for p,v in r['file_hashes'].items())
root=ET.parse(o/'full_suite.xml').getroot();passed=0;skips=[]
for t in root.iter('testcase'):
 assert t.find('failure') is None and t.find('error') is None
 s=t.find('skipped')
 if s is None:passed+=1
 else:skips.append({'test':t.attrib.get('classname','')+'::'+t.attrib['name'],'message':s.attrib.get('message',''),'detail':s.text})
assert passed==5392 and len(skips)==196,(passed,len(skips))
for log,count in [('focused258.log',258),('geometry38.log',38),('row_boundary21.log',21)]:assert f'{count} passed' in (o/log).read_text()
reasons=dict(collections.Counter(r['message'] for r in skips))
dump('skipped196.json',{'candidate_sha':head,'count':len(skips),'by_reason':reasons,'cases':skips,'scope':'not executed; no database/real-original/platform capability acceptance inferred'})
# 核对本轮起始快照与更早封印记录，不能只证明本轮内部前后一致。
protected=read('protected_input_hashes.json');previous=json.loads((w/'outputs/ir3_gap01_fix_20261106/protected_input_hashes.json').read_text())
assert protected['independent618']['files']==previous['independent']
assert protected['prior1745']['files']==previous['prior_delivery']
current_root=Path(protected['current_independent']['root'])
assert protected['gap01_delivery']['files']==json.loads((current_root/'delivery_input_snapshot.json').read_text())
for name,record in protected.items():
 actual={str(p.relative_to(Path(record['root']))):sha(p) for p in Path(record['root']).rglob('*') if p.is_file()}
 assert actual==record['files'],name
seal=json.loads((current_root/'independent_manifest.json').read_text());assert all(sha(current_root/r['path'])==r['sha256'] for r in seal['files'])
for line in (current_root/'SHA256SUMS').read_text().splitlines():
 value,path=line.split('  ',1);assert sha(current_root/path)==value
validation={'candidate_sha':head,'base_sha':base,'status':'AWAITING_INDEPENDENT_REVIEW','defect_id':'IR3-GAP-02','defect_count':1,'controls':controls,'original_controls':95,'new_controls':7,'exact_same_pdf_pair':'all unchanged kit/before/after bytes verified; d71 FAIL/PASS, candidate FAIL/FAIL','same_sealed64':'64/64 expected','same_sealed_new7_plus_pair':'8/8 expected','related_tests':{'original':258,'geometry':38,'new_row_boundary':21},'full_suite':{'passed':passed,'skipped':196,'details':'skipped196.json','not_a_live_acceptance':True},'mypy':'PASS; 582 source files','ruff':'PASS','analysis_changed':False,'new_capture':False,'real_approvals_added':0,'old_evidence_baseline_and_current_hashes_match':True,'audit_status':'PASS'}
dump('validation.json',validation)
priorledger=w/'outputs/ir3_gap01_fix_20261106/ledger.json'
dump('ledger.json',{'candidate_sha':head,'base_sha':base,'previous_ledger':{'path':str(priorledger),'sha256':sha(priorledger)},'changes':[{'id':'IR3-GAP-02','priority':'P1','count_as_defects':1,'status':'CANDIDATE_FIXED_SELF_TEST_PASS_PENDING_INDEPENDENT_REVIEW'}],'historical_gates':read('gate_summary.json')['gates'],'pending_registries':read('pending_registries.json'),'new_real_approvals':0,'excluded':['B1','original7 disputes','full budget acceptance','OCR','AI','real template chain','real Word','production readiness'],'merge':False,'push':False,'start_or_switch_services':False})
for name in ['run_checks.py','audit.py','package.py']:shutil.copyfile(Path('/private/tmp/gbc-gap02-20261106')/name,o/'scripts'/('package_delivery.py' if name=='package.py' else name))
dump('audit_execution.json',{'candidate_sha':head,'command':['PYTHONDONTWRITEBYTECODE=1','/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python','/private/tmp/gbc-gap02-20261106/audit.py'],'script_sha256':sha(o/'scripts/audit.py'),'log':'audit.log','log_sha256':sha(o/'audit.log'),'exit_code':0,'fixture_preflight_failure':'preflight_tests.log: new test chose the row occurrence of Total: as the Total header; corrected the new fixture to select actual y<80 heading; initial test/script retained under preflight; original independent expected values unchanged'})
(o/'封存输入说明.txt').write_text('sealed_inputs64 为原独立 64 组输入逐字复制，sealed_masking_inputs 为本轮独立 7 控制和精确同 PDF 对照逐字复制。所有 PDF、前后工件、外部事实及旧观察回执均与原封印文件一致。目录内 observed/independent_observed 等文件仍是原独立历史观察，不能当作新候选结果或批准；当前结果分别见 same_original_inputs64.json、same_original_masking_inputs.json 及 exact_same_pdf_masking_pair_replay.json。\n新输出其余 control_cases/masking_cases 是按未修改的独立原脚本重放产生，另与原封存输入直接重放分开。\n')
reason_text='\n'.join(f'- {count} 项：{reason}' for reason,count in reasons.items())
(o/'复交结果.txt').write_text(f'''IR3-GAP-02 最小候选修复复交
基点：{base}
候选：{head}
状态：AWAITING_INDEPENDENT_REVIEW，整体专项保持未完成。

修复范围：仅 scripts/effect_fact_sources.py 与新增 tests/test_effect_row_label_boundary.py。IR3-GAP-02 记为一个 P1；精确同 PDF 对照不另计缺陷。
行标删除延后到原页列头验证之后。任何相关金额列都受保护，当前声明的金额列不会被排除为身份列。行标必须位于金额列左侧，并在删除前核对未遮蔽原行的完整金额表示；会改变金额表示的片段拒收。行标自带横线只在有完整、独立、含身份文字、位于金额列之外的原页文字组件依据时允许剔除；边界不能确认时拒收。没有禁用含负号的行标、没有特定引文黑名单，也没有恢复字距切段。

完整复现证据：
1. exact_same_pdf_masking_pair_replay.json：同一原独立 PDF，SHA {pair['pdf_sha256']}；kit、before、after 全部逐字一致。仅原独立外部 row 引文/bbox 及相应 source_cell_digest 有变化。
2. 原 d71 模块：正常 All 行标 FAIL；扩框 All - PASS。当前模块：两者均 FAIL。旧 963 模块：两者均 FAIL。这是同一当前验收器内只切换 fact_source 的模块差分，不是历史完整服务重跑。
3. same_original_inputs64.json：逐字复制封存 64 组 PDF/前后工件/外部输入后直接重放，64/64 符合原预期。旧 GAP-01 的故障与合法正控继续保留。
4. same_original_masking_inputs.json：本轮原 7 控制及精确对照共 8 组输入直接重放，8/8 符合预期，普通正值、完整负值、合法 All - 行标仍 PASS。封存复制中的观察回执属于历史观察，当前结果以这两个 differential JSON 的 candidate_module 为准，不作批准。

固定候选检查：
- 原 66+29 控制按原脚本/原预期在新输出重放，95/95 符合预期；本轮 7 控制 7/7，精确同 PDF 对照符合预期。脚本均按原字节复制。
- 原 258+38 项相关测试通过，新增 21 项结构边界测试通过。新增测试覆盖侵入其他金额列、列头左侧前缀、同行切掉前缀、行标自身合法横线/编码/括号/标点、完整负值、同行金额与身份列表头。
- 全套 pytest：5392 passed / 196 skipped。Mypy、Ruff 通过。精确命令、SHA、日志哈希、软件版本见 check_commands.json、source_and_test_identity.json。
- 通过/失败边界：最终所要求检查均按预期通过；负控的预期 FAIL 仍为拒收。初次新增测试夹具把行标 Total: 的 Total 子串选作列头导致一次失败，修正该新夹具的实际表头定位后通过；初次日志和夹具保留于 preflight，不曾修改独立原预期。
196 跳过项不计为通过，原因逐项见 skipped196.json：
{reason_text}

来源及代际：分析链、叙述、R-02、scope v2、原 258+38 测试和运行配置文件保持原字节。本轮未启动分析采集，继续保存 9631 的 final_candidate 27 份与 repeat4_final 4 份完整上下文和 native 配置；不将它们改称 d71 或新候选采集。
当前门禁重放原工件，整体仍 FAIL 127/35；原 R-05 SHA 证明只用于原绑定代际，仍 FAIL 119/35。342/R2→963 保存语料比较 PASS 0/0 不能表示整体效果通过。
原 147 对象、新 35 对象及 31 UNAPPROVED 请求分别逐字保留，没有互抵、授权迁移、制造事实池、观察字段改名或真实 v2 批准。

旧 618、1745 文件与原记录逐字匹配；上一交付与本轮独立封印也完整复核，见 protected_input_integrity.json 和 protected_input_hashes.json。新输出独立生成 manifest；独立原脚本、PDF、工件、回执、封印及金标未覆盖。
没有合并或推送 main，没有启动或切换 Web/DB 或日常入口。B1、原 7 争议、全预算/OCR/AI、真实模板、真实 Word 与生产就绪继续未签收。独立通过也不自动授权代码合入或服务切换。
''')
(o/'独立复验入口.txt').write_text(f'''固定候选 {head}，基点 {base}。
先核 source_changes.patch、source_and_test_identity.json、review_manifest.json 与 protected_input_integrity.json。
核心原件复现：exact_same_pdf_masking_pair_replay.json；对应 sealed_masking_inputs/cropped_negative_exact_row 与 exact_same_pdf_row_mask，PDF/kit/before/after 完全一致。候选的两个结果均应 FAIL；d71 原模块为 FAIL/PASS。
直接复验 same_original_inputs64.json 所列封存原件/外部输入，不能仅以新生成相似 PDF 代替；另核 same_original_masking_inputs.json 的 7 项及精确对照。全部源/复制文件 SHA 随结果保存。
按 check_commands.json 在另一个新输出重放原控制脚本，预期不可改；不要在本封印目录原地覆盖回执。原 masking/exact 脚本沿用 d71_module 名称表示当前 import 的模块，实际代码 SHA 以命令记录为准，三模块精确身份以本轮 differential JSON 为准。
原 258+38 与新增 21 项测试、全套 5392/196 跳过明细可核；跳过项不计为业务签收。
analysis_unchanged.json：原采集身份是 9631，不是本候选；不得迁移旧授权。pending_registries.json 的 147/35/31 均仍 UNAPPROVED。
专项状态为 AWAITING_INDEPENDENT_REVIEW。本候选独立通过也不自动授权合并、推送或服务启动/切换。
''')
assert not subprocess.check_output(['git','status','--porcelain'],cwd=w,text=True)
files={str(p.relative_to(o)):sha(p) for p in o.rglob('*') if p.is_file() and p.name!='review_manifest.json'}
dump('review_manifest.json',{'candidate_sha':head,'base_sha':base,'status':'AWAITING_INDEPENDENT_REVIEW','sealed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':files})
print(json.dumps({'sha':head,'full_pass':passed,'skip':len(skips),'skip_reasons':reasons,'manifest_files':len(files),'status':'AWAITING_INDEPENDENT_REVIEW'},ensure_ascii=False))
