from pathlib import Path
import json,hashlib,subprocess,shutil,collections,xml.etree.ElementTree as ET,datetime
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');out=w/'outputs/ir3_gap01_fix_20261106';base='9631ea55decc8e5b94531e483a5d907842be292a'
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(name):return json.loads((out/name).read_text())
def dump(name,v):(out/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
commands=read('check_commands.json');assert len(commands)==8 and all(x['exit_code']==0 and x['candidate_sha']==head for x in commands)
for item in commands:assert sha(out/item['log'])==item['log_sha256']
controls={}
for name in ['effect_boundary_controls','narrative_boundary_controls','extra_effect','extra_narrative','scope_v2_controls','neighbor_effect','neighbor_narrative']:
 rows=read('receipts/'+name+'.json');assert all(r['expectation_met'] for r in rows)
 controls[name]={'cases':len(rows),'expectations_met':len(rows),'receipt_sha256':sha(out/'receipts'/f'{name}.json')}
assert sum(r['cases'] for r in controls.values())==95
root=ET.parse(out/'full_suite.xml').getroot();skipped=[];passed=0
for t in root.iter('testcase'):
 assert t.find('failure') is None and t.find('error') is None
 if t.find('skipped') is not None:
  s=t.find('skipped');skipped.append({'test':t.attrib.get('classname','')+'::'+t.attrib['name'],'message':s.attrib.get('message',''),'detail':s.text})
 else:passed+=1
assert passed==5371 and len(skipped)==196,(passed,len(skipped))
assert '258 passed' in (out/'focused258.log').read_text() and '38 passed' in (out/'geometry38.log').read_text()
by_reason=collections.Counter(x['message'] for x in skipped)
dump('skipped196.json',{'candidate_sha':head,'count':len(skipped),'by_reason':dict(by_reason),'cases':skipped,'scope':'not executed; excluded from pass count and live/AI/DB acceptance'})
validation={'candidate_sha':head,'base_sha':base,'status':'SELF_TEST_PASS_AWAITING_INDEPENDENT_REVIEW','defects':1,'defect_id':'IR3-GAP-01','original_controls':66,'neighbor_controls':29,'control_receipts':controls,'same_original_inputs_replayed':64,'original_related_tests':258,'new_geometry_tests':38,'full_suite':{'passed':passed,'skipped':196,'skips':'skipped196.json','live_acceptance':False},'mypy_log':'mypy.log','ruff_log':'ruff.log','new_analysis_run':False,'analysis_source':'analysis_unchanged.json','current_gate':'gate_summary.json','all_legacy_approvals_retained_without_rebinding':True}
dump('validation.json',validation)
# 保存可复跑执行脚本；独立控制文件仍是原字节，预期从未改写。
for source,name in [('run_checks.py','run_checks.py'),('audit_final.py','replay_and_audit.py'),('package.py','package_delivery.py')]:
 shutil.copyfile(Path('/private/tmp/gbc-gap01-preflight')/source,out/'scripts'/name)
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
dump('supplementary_execution.json',{'completed_utc':now,'candidate_sha':head,'audit_command':['PYTHONDONTWRITEBYTECODE=1','/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python','/private/tmp/gbc-gap01-preflight/audit_final.py'],'audit_log':'audit_final.log','audit_script_sha256':sha(out/'scripts/replay_and_audit.py'),'package_script_sha256':sha(out/'scripts/package_delivery.py'),'earlier_audit_draft':'audit_draft_fingerprint_check','draft_reason':'fingerprint includes tests; final two-file delta matches committed change exactly'})
ledger={'candidate_sha':head,'base_sha':base,'previous_ledger':{'path':str(w/'outputs/ir3_residual_fix_20261106/ledger.json'),'sha256':sha(w/'outputs/ir3_residual_fix_20261106/ledger.json')},'changes':[{'id':'IR3-GAP-01','root_cause':'glyph spacing split complete field representations','status':'CANDIDATE_FIXED_SELF_TESTED_PENDING_INDEPENDENT_REVIEW','mismatches_in_review':{'false_pass':10,'false_fail':1},'count_as_defects':1}],'pending_registries':read('pending_registries.json'),'historical_gates':read('gate_summary.json')['gates'],'new_real_v2_approvals':0,'excluded':['B1','original7 disputed','all budget capabilities','OCR','AI','real template chain','real Word','production readiness'],'merged':False,'pushed':False,'daily_entry_started_or_switched':False}
dump('ledger.json',ledger)
reasons='\n'.join(f'- {n} 项：{reason}' for reason,n in by_reason.items())
(out/'复交结果.txt').write_text(f'''IR3-GAP-01 候选修复复交
基点：{base}
候选：{head}
结论：候选修复及自测完成，AWAITING_INDEPENDENT_REVIEW；专项保持未完成。

一个共同根因：不能把字形间距、PDF span 或 text line 分段当成原格边界。独立发现的 10 个错误 PASS 和 1 个错误 FAIL 作为同一个 IR3-GAP-01 处理。

改动仅为 scripts/effect_fact_sources.py 和新增 tests/test_effect_fact_source_geometry.py。
金额从原页同一物理行的全部字符识别完整表示，符号、括号、指数与数字之间允许保留空白和 PDF 分段。各完整表示必须唯一对应原页表头；同列多个金额、跨列表示、未解析字符或无对应表头时拒收。bbox 和引文必须包含完整语义字形。没有调大间距阈值，也没有用负面词表豁免反例。
单位按完整字段验证，只剔除独立绑定的表题与明确单位标签；未知数量级或其他未解释片段不能丢弃。百万元、千万元、括号金额和指数金额仍属不支持表示，完整或裁窄均拒收。合法完整负值、相邻独立金额列、标准正负号/千分位，以及元/万元/亿元换算控制保留。
复杂表头、不能唯一确认原格边界或单位上下文的布局保持拒收，本轮不宣称任意 PDF 版式均受支持。

固定候选验证：
- 按字节复制原脚本，66 项原控制与新增 24 效果 + 5 叙述控制共 95 项全部符合原预期。
- 直接读取独立保存的同一 PDF、前后工件和外部输入，进程内只替换 342 / 963 / 当前 fact_source 模块，64 个效果控制在当前模块全部符合预期。详见 same_input_module_differential.json；不是历史完整服务重跑，也不是批准。
- 原 258 项相关测试全部通过；新增 38 项几何边界测试通过，覆盖更大空隙、跨 PDF text line、同一 PDF line 的相邻列、完整负值及不明边界拒收。
- 全套 pytest：5371 passed，196 skipped。mypy 和 Ruff 通过；命令、日志哈希、依赖版本及精确 SHA 见 check_commands.json、source_and_test_identity.json。
跳过边界（不计为通过）：
{reasons}
逐项清单见 skipped196.json；没有因此签收数据库、真实 AI 或未执行能力。

分析链未改：叙述提取、api/src、运行配置、R-02 和 scope v2 实现/schema、原 258 项测试均保持原字节。采集指纹差异恰为离线门禁和新增测试两个文件。此次没有重采 27+4，未改其原 meta、fact_context 或分析版本；31 份完整上下文按原代际保留。analysis_unchanged.json 保存来源、哈希及原 native 配置；既有语料一致性不等于整体效果通过。
当前离线门禁对保存工件重新计算：原始基线→963 采集仍 FAIL 127/35；旧代际仅用原 R-05 SHA 证明仍 FAIL 119/35。342→963、R2→963 保存语料比较为 PASS 0/0，只表明该语料的回归比较结果。

原 9 组 147 对象、新 35 对象、31 条 UNAPPROVED 请求分别逐字保留。没有迁移旧授权、制造事实池、把 observed 改名为 approved 或签发真实 v2 批准。显式授权交叉配对合法正控通过，cross_old_key 负控拒收。IR3-F01、R-02、scope v2 未作无关改写。
独立输出 618 文件和旧交付 1745 文件哈希全部不变。核验辅助脚本的初次指纹断言漏计新增测试，失败草稿保留于 audit_draft_fingerprint_check；修正核验范围后，确认仅两个已提交文件变化。

没有合并或推送 main，没有启动 Web/DB 或启动/切换日常入口。B1、原 7 争议、全预算/OCR/AI、模板真实链路、真实 Word 和生产就绪继续未签收。下一步为独立复验本候选；代码合入与服务切换继续分别验收。
''')
(out/'独立复验入口.txt').write_text(f'''请先固定 worktree HEAD={head}，基点={base}。
读取 复交结果.txt、source_changes.patch、source_and_test_identity.json、validation.json。
核对 review_manifest.json 和 protected_input_integrity.json，原独立证据仅只读。
重点复验 same_input_module_differential.json 中的 space_extended_unit_1、space_negative_3、space_parentheses_3、space_exponent_3 与 spaced_signed_complete_positive；这些直接使用原独立 PDF/外部输入，不是执行者新造原件。
新输出 scripts 中三个独立脚本按原字节保留，查看 check_commands.json 可获原 66+29 的精确执行环境。再次运行时必须另建新输出并复制脚本；不要在已封印目录覆盖控制/回执。将 extra_controls 的 CAND 路径指向当前候选仅作路径适配，不改控制预期。
新增 tests/test_effect_fact_source_geometry.py 含 38 项同根因边界。全套日志与 full_suite.xml、skipped196.json 逐项对应，不把跳过项当通过。
分析未重跑、原代际未变，见 analysis_unchanged.json。historical gates 和 pending_registries 继续未完成；真实批准只能由独立复核者依据原件另发，观察或自测不能代替。
本轮仅复交候选，未授权合并、推送或切换日常入口。
''')
# 最后检查工作树与旧证据，再封印新交付全部文件（manifest 自身除外）。
assert not subprocess.check_output(['git','status','--porcelain'],cwd=w,text=True)
protected=read('protected_input_hashes.json')
for group in ['independent','prior_delivery']:
 directory=Path(protected[group+'_root']);actual={str(p.relative_to(directory)):sha(p) for p in directory.rglob('*') if p.is_file()}
 assert actual==protected[group]
files={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p.name!='review_manifest.json'}
dump('review_manifest.json',{'candidate_sha':head,'base_sha':base,'sealed_utc':now,'files':files,'status':'CANDIDATE_PENDING_INDEPENDENT_REVIEW'})
print(json.dumps({'sha':head,'full_pass':passed,'skip':len(skipped),'skip_reasons':dict(by_reason),'sealed_files':len(files),'tracked_status':'clean'},ensure_ascii=False))
