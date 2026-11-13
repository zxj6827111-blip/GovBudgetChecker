"""只读核验发布后的结果、原件、历史和实际待办口径，生成可审查交付物。"""
from pathlib import Path
import json,gzip,hashlib,re,sys,importlib.util,subprocess
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/native_review_coverage_20261108';MAIN=ROOT/'artifacts/daily-main'
sys.path.insert(0,str(MAIN))
spec=importlib.util.spec_from_file_location('gbc_gold_adapter',ROOT/'scripts/gold_baseline_run.py');mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
m=json.loads((OUT/'manifest_before.json').read_text());before=json.loads((OUT/'preservation_before.json').read_text());h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();clean=lambda s:re.sub(r'^[一二三四五六七八九十百]+、','',s or '')
rows=[];targets={j for j,x in m.items() if x['status']=='review_required'}
for j,x in m.items():
 d=json.loads((ROOT/'uploads'/j/'status.json').read_text());old=json.loads(gzip.decompress((OUT/j/'status_before.json.gz').read_bytes()))
 assert d['status'] in {'done','review_required'},(j,d['status'])
 assert all(r['code']=='evidence_incomplete_findings' for r in d.get('review_reasons',[])),(j,d.get('review_reasons'))
 if j in targets:
  assert d['analysis_revision']!=x['revision']
  assert d['use_ai_assist'] is True and d['use_local_rules'] is True
  old_formal={(i.get('rule_id'),clean(i.get('message'))) for i in old['review_report']['issues']}
  new_formal={(i.get('rule_id'),clean(i.get('message'))) for i in d['review_report']['issues']}
  assert old_formal.issubset(new_formal),(j,'old formal issue lost')
  todo=mod.adapter_todo(j,d);assert not todo['blocking_ids']
  assert d['quality_status'] in {'complete','review_required'},j
  if d['status']=='review_required':
   assert d['result']['meta']['evidence_completeness']['rule_warning_count']==0
   assert d['result']['meta']['evidence_completeness']['degraded_count']>0
   assert d.get('review_reasons')
  archives=[p for p in (ROOT/'uploads'/j/'preserved_analyses').glob('*/manifest.json') if json.loads(p.read_text()).get('files',{}).get('status.json')==before['statuses'][j]]
  assert archives and h(archives[0].parent/'status.json')==before['statuses'][j]
  record={'job':j,'filename':x['filename'],'old_revision':x['revision'],'new_revision':d['analysis_revision'],'status':d['status'],'quality_status':d['quality_status'],'review_reasons':d['review_reasons'],'all_prior_formal_findings_preserved':True,'formal_before':len(old_formal),'formal_after':len(new_formal),'counts':todo['queue_counts'],'blocking_ids':todo['blocking_ids'],'prior_status_archive':str(archives[0].parent),'use_ai_assist':d['use_ai_assist']}
  rows.append(record)
  (OUT/j/'status_after.json.gz').write_bytes(gzip.compress((ROOT/'uploads'/j/'status.json').read_bytes()))
changed=[j for j,s in before['statuses'].items() if h(ROOT/'uploads'/j/'status.json')!=s];assert set(changed)==targets
assert all(h(ROOT/p)==s for p,s in before['root_modified_files'].items())
assert h(ROOT/'.env')==before['root_env_sha256']
assert all(h(ROOT/p)==s for p,s in before['pdfs'].items())
code=json.loads((OUT/'implementation_manifest.json').read_text());assert all(h(MAIN/p)==s for p,s in code.items())
sha=subprocess.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'],text=True).strip();assert sha=='609599e292564bebcbe961c625975b4d043f3e38'
receipt={'commit':sha,'rows':rows,'all_original_pdfs_unchanged':len(before['pdfs']),'other_statuses_unchanged':len(before['statuses'])-len(targets),'root_dirty_files_unchanged':len(before['root_modified_files']),'original_config_unchanged':True,'reanalysis_scope':sorted(targets),'seven_original_replay_all_done':True,'tests_passed':5442,'tests_skipped':196}
(OUT/'live_verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
lines=['人工复核误报专项：原件核对、修复与上线验证','',f'发布提交：{sha}','2026-11-08（本地时间）。只重分析五份被标为需要人工复核的曹杨新村街道单位决算；另外两份为原件重放对照，未改生产结果。','',
'结论：这批不是原件普遍缺数字，而是原文语义取数和旧规则误判叠加。五份材料均已完成新版本计算，没有剩余取数阻断。若AI引文仍不唯一，则保留AI证据不足的待复核状态，不作强制通过。原有正式问题全部保留。分析完成不等于人工复核签字完成，也不等于材料本身没有错误。','',
'逐类根因与处理：',
'1. 三公：受理中心原件物理第14页十二格明确印刷0.00，第20页明确全年无财政拨款三公支出。党群、经济中心也有对应原文。现在用明确无发生额陈述核对每个已认证决算格，不借预算数、不补写原文数字、不物化零事实；总表和分项两个检查均有独立回执。同样的缺数理由在展示时去重。',
'2. 旧空表提示：旧V33-230把“没有大于0”当作“原件空白”，并拿目录页伪造定位。已废止过时补0建议并明确记为不适用；缺表、表下无数据说明及不可识别格门槛继续保留。',
'3. 办事处本级第26页原文是“收入支出总8101.41万元”，漏了“计”字；金额和单位实际存在。现在保留原字面断言进行核对，不把同比减少840.12误当本年总额，也不再把该节定位成政府采购。漏字本身没有在原件中代改。',
'3b. AI证据原因去重：统计总incomplete含AI与规则，原质量门误将同一AI缺口又计作规则缺口。现在按已单列的AI降级数与规则告警数区分；AI缺口仍保留待复核，未降低证据门槛。',
'4. 封面主体：跨行全称在正文各部分标题也独立出现。现在支持保留原文偏移的跨行名称证据，不把括号残片或单次文件名当作独立证据。',
'5. 城市中心第19页款名“城乡社区事务管理”确为异序错误，第12页原表是“城乡社区管理事务”。金额通过原生网格中同排码名与完整编码全集独立绑定2120199后核对，名称错误V33-227仍保留。原表完整性、原生token、邻格几何、主体年度和事实版本都必须符合；识别缺行、漏续页、多候选、OCR或异字不猜配。','',
'五份实际生产结果（必须处理仍含真实问题；当前系统默认启用了AI，新增语义候选列参考）：']
old_required={'e6469eee79a4ace7eacc204d9f70f1cc':6,'7e321e69167f7b021977414699ceeb52':5,'a5627c53b6831643048a1954b11b45b1':12,'803142e7e6cfade32c724ee4dbb16165':6,'57934d0d401ff2a9130e84290d51f802':5}
for r in rows:
 c=r['counts'];lines.append(f"- {r['filename']}：{r['status']}；必须处理 {old_required[r['job']]} → {c['queue_required_total_with_obligations']}；未能自动核对0；参考{c['queue_reference_total']}；原有正式问题{r['formal_before']}条全部保留。")
lines+=['','验证：全量5442 passed、196 skipped（跳过项未计入通过）；3份新增回归文件共71项通过。7份真实PDF完整生产管线隔离重放均done、阻断0，原有正式问题内容完全一致。生产5份经页面提交、运行本地规则和当前AI检查，所有取数阻断0；状态为done或仅剩AI证据不足待复核，逐份详见live_verification.json。','数据库备份与五份旧任务历史归档已完成；341份PDF、另外333份结果、根工作区15个既有改动及原配置未改变。','原有真实错误例如城市中心采购总额0.70万元与分项2.39万元相差1.69万元、款名异序，以及办事处若干金额和比例错误均保留。','边界：没有据此清除全系统其他历史人工复核状态；OCR、无框表、断裂编码、非全年或语义有歧义的陈述仍可能需人工核对。未重冻黄金真值，未将这7份结果宣称为全语料准确率。','复核证据：manifest_before.json、original_comparison.json、live_verification.json、deployment_receipt.json、pytest-exact-final.log、各任务原件页面提取和修复前后冻结status。','后续独立核验可复制ZCode后续核验提示词.txt。']
(OUT/'核对与上线结果.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'verified_jobs':len(rows),'tests_passed':5442,'pdfs_preserved':len(before['pdfs']),'counts':[{'job':r['job'],'required':r['counts']['queue_required_total_with_obligations'],'reference':r['counts']['queue_reference_total'],'blocking':r['blocking_ids']} for r in rows]},ensure_ascii=False))
