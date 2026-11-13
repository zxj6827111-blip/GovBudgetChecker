"""Aggregate frozen original reviews and the fixed-version live intake receipts."""
from pathlib import Path
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import csv
import hashlib
import json

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
load=lambda name: json.loads((OUT/name).read_text(encoding='utf-8'))
sha=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest=load('sample_manifest.json')
num=load('numeric_truth.json'); nc=load('numeric_comparison.json')
disc=load('disclosure_truth.json'); dc=load('disclosure_comparison.json')
struct=load('structure_truth.json'); sc=load('structure_comparison.json')
root=load('root_numeric_truth.json'); receipt=load('live/service_receipt.json')
coverage=load('structure_coverage_followup.json')
records=[]
for idx,(truth,comparison) in enumerate(zip(num['issues'],nc['relation_comparison'],strict=True),1):
 r=deepcopy(truth);r['id']=comparison['truth_id'];r['classification']=comparison['status']
 r['truth_source']='numeric_truth.json';r['system_matches']=comparison['matched_records'];records.append(r)
for idx,truth in enumerate(root['issues'],1):
 r=deepcopy(truth);r['id']=f'ROOT-{idx:02}';r['classification']='FN' if idx==4 else 'TP';r['truth_source']='root_numeric_truth.json';records.append(r)
for key in ['D-TRUTH-004','D-TRUTH-005','D-TRUTH-006','D-TRUTH-010','D-TRUTH-011']:
 truth=next(r for r in disc['issues'] if r['id']==key)
 comparison=next(r for r in dc['truth_comparison'] if r['truth_id']==key)
 r=deepcopy(truth);r['classification']='FN' if comparison['classification']=='missed_in_native_diagnostic' else 'TP';r['truth_source']='disclosure_truth.json';r['system_matches']=comparison['native_matches'];records.append(r)
for idx,truth in enumerate(struct['issues'],1):
 r=deepcopy(truth);r['id']=f'STRUCT-{idx:02}';r['classification']='FN';r['truth_source']='structure_truth.json';records.append(r)
for r in records:
 for key in ['rule_id','title','severity','document_type','year','organization','page','bbox','table_or_section','snippet','expected','actual','difference','reason','suggestion','confidence']:
  r.setdefault(key,None)
 if r['bbox'] is None:
  ev=r.get('evidence',[])
  if isinstance(ev,list):
   hit=next((e for e in ev if e.get('page')==r['page'] and e.get('bbox')),None)
   if hit:r['bbox']=hit['bbox']
 r['bbox_coordinate_system']='PDF points; top-left origin; physical page starts at 1'
 if r['confidence'] is None:r['confidence']=0.95
 if r['document_type'] is None:r['document_type']='决算公开'
 if r['year'] is None:r['year']=2025
 if r['table_or_section'] is None:r['table_or_section']=r.get('section') or '未识别到，见原文证据'
 if r['classification']=='FN':r['system_actual']='本轮原生诊断及实际API报告均未输出对应问题'
 # Cross-lane descriptions are preserved; parent judgments do not rewrite frozen truth.
false_positives=deepcopy(nc['false_positives'])
for idx,r in enumerate(false_positives,1):
 r['id']=f'FP-{idx:02}';r['classification']='FP';r['rule_id']=r['rule'];r['page']=r['reported_page']
 r['system_severity']=r['severity'];r['severity']='high'
 r['document_type']='决算公开';r['year']=2025;r['table_or_section']='财政拨款收入支出决算总表' if idx==1 else ('一般公共预算财政拨款支出决算表' if idx==2 else '三公经费支出决算情况说明')
 r['actual']=r['reported_message'];r['bbox']=None
 evidence=r.get('source_evidence',[])
 r['snippet']='；'.join(f"第{e['page']}页原文：{e.get('needle') or e.get('snippet') or '见原件坐标'}" for e in evidence)
 r['difference']='不适用，属于系统误报；正确原件关系成立'
 hit=next((e for e in evidence if e.get('page')==r['page'] and e.get('bbox')),None)
 if hit:r['bbox']=hit['bbox']
 r['suggestion']='修正完整跨页表与分项绑定后重审；当前该告警应撤回'
manual=deepcopy(nc['pending_scope'])+deepcopy(disc['manual_review'])
for r in manual:r['classification']='manual_review';r['severity']='manual_review'
source_checks=[];parity=[];all_live=[]
for idx,sample in enumerate(sorted(manifest['samples'],key=lambda r:r['name']),1):
 doc=OUT/'native'/f'document_{idx:02}'
 audit=json.loads((doc/'audit.json').read_text())
 native=json.loads((doc/'issues.json').read_text())
 live=json.loads((OUT/'live'/f'shanghai_{idx:02}'/'report.json').read_text())
 assert audit['filename']==sample['name']+'.pdf'
 equal={(i['rule'],i['message']) for i in native}=={(i['rule'],i['message']) for i in live['issues']}
 parity.append({'organization':sample['name'],'native_count':len(native),'live_count':len(live['issues']),'rule_and_message_identical':equal,'live_status':live['status'],'native_elapsed_seconds':audit['elapsed_seconds'],'live_elapsed_seconds':next(s['elapsed_seconds'] for s in receipt['scenarios'] if s['scenario']==f'shanghai_{idx:02}')})
 source_checks.append({'organization':sample['name'],'sha256':sample['sha256'],'unchanged':sha(Path(sample['path']))==sample['sha256'],'facts':audit['facts'],'fact_source_errors':len(audit['source_errors']),'tables':sum(t['instances'] for t in audit['tables']),'pages':audit['pages'],'completed_obligations':live['check_coverage']['completed_total'],'applicable_obligations':live['check_coverage']['applicable_total']})
 all_live.extend((sample['name'],i) for i in live['issues'])
missing_pages=[{'organization':org,'rule_id':i['rule'],'message':i['message']} for org,i in all_live if not i.get('location',{}).get('page') and not i.get('location',{}).get('pages')]
missing_bboxes=[{'organization':org,'rule_id':i['rule'],'message':i['message']} for org,i in all_live if i['severity']!='info' and not i.get('location',{}).get('bbox')]
fn=[r for r in records if r['classification']=='FN']
assert len(fn)==9 and len(false_positives)==3
assert all(p['rule_and_message_identical'] for p in parity)
assert all(p['unchanged'] and p['fact_source_errors']==0 for p in source_checks)
issues=records+false_positives
features=[
 {'priority':'P1','title':'全部表内规则使用完整逻辑表及互斥分项','evidence':['FP-01','FP-02'],'acceptance':'文旅p17–18基金540.82+10018.79=10559.61不报错；p19–20的2070899纳入8925.24层级合计；汇总行不再次参与分项和。'},
 {'priority':'P1','title':'跨页分词后分别绑定三公业务与百分比','evidence':['FP-03'],'acceptance':'民政p31–32车辆65.05%和接待74.40%各自绑定正确分子分母，无误报告。'},
 {'priority':'P1','title':'功能分类每项说明金额与单位均参与表文比对','evidence':['ROOT-04'],'acceptance':'发改p30的544.49元按元换算，与p19的544.49万元比对产生R001高严重度问题，保留两端原文和坐标。'},
 {'priority':'P1','title':'基金总收入和总支出说明纳入表文校验','evidence':['NUM-T06'],'acceptance':'交通p40基金收入78656.53与p28总收入234955.97比较并报错；同时区分本年收入、收入总计、年初结转及基金项目支出。'},
 {'priority':'P1','title':'全口径支出占比和机关运行同比完整复算','evidence':['NUM-T03','NUM-T07'],'acceptance':'交通一般公共预算占本年支出90.34%应89.88%；机关运行下降5.03%应4.78%，两项均有真实操作数证据。'},
 {'priority':'P1','title':'绩效数量与评级整改等分项勾稽','evidence':['NUM-W08'],'acceptance':'文旅同段自评108与评级105+21+0+1=127形成确定问题；不同绩效范围不得相加。'},
 {'priority':'P1','title':'决算规则未完成关系完整映射到义务台账','evidence':['COVERAGE-ORPHAN-CMM001-FINAL'],'acceptance':'CMM-001两条不足回执进入决算表文/三公义务及未完成清单，不能仅因其他规则pass显示该关系已完成。'},
 {'priority':'P2','title':'已披露字段识别与预算阶段确认','evidence':['财政p28 CMM-001','交通三公预算阶段'],'acceptance':'财政购置0万元、运行30万元可识别；泛预算数不在缺明确证据时直接认定年初预算。'},
 {'priority':'P2','title':'模板语义矛盾和同义重述原因','evidence':['D-TRUTH-004','D-TRUTH-005','D-TRUTH-006','STRUCT-01'],'acceptance':'识别零购车却写购买一辆、仅重述支出小于预算/同比略增及模板操作句；短但有效业务原因不得误报。'},
 {'priority':'P2','title':'问题去重及完整原件定位','evidence':['交通CMM-001错误p1','商务V33-202/V33-242无页','C线重复6条'],'acceptance':'同一关系合并跨规则证据；交通预算说明定位p39而非封面p1；商务跨表/结转问题给真实原页、每个操作数坐标，无法定位如实null。'}]
summary={'document_type':'决算公开','year':2025,'organizations':[s['name'] for s in manifest['samples']],'publication_date':None,'source_urls':None,'documents':8,'pages':295,'overall_assessment':'可用于有人工复核的真实文字层PDF辅助审校；尚不符合无人复核的最终审校要求。','readiness':'assisted_trial_only','confirmed_missed_groups':len(fn),'confirmed_false_positive_records':len(false_positives),'numeric_frozen_relations':16,'numeric_detected_relations':12,'numeric_missed_relations':4,'accuracy_estimate':None,'risk_flags':['既有开发样本复测，非独立盲测','数字与语义检查覆盖不完整','跨页误报','部分证据定位缺失','义务台账漏记确定性未完成关系','预算阶段及车辆统计范围需人工确认']}
result={'summary':summary,'issues':issues,'manual_review':manual,'next_actions':features,'service_validation':{'receipt':'live/service_receipt.json','status':receipt['status'],'main_original_scenarios':8,'total_service_scenarios':10,'extra_scenarios':'同一监狱原件复跑1次；另1份既有扫描预算仅用于入口对照，OCR关闭，无内容准确性验收','elapsed_seconds_including_startup_and_rate_limit_waits':receipt['elapsed_seconds'],'export_formats':['json','csv','docx','pdf'],'parity':parity,'original_checks':source_checks,'live_issue_total':len(all_live),'severity_counts':dict(Counter(i['severity'] for _,i in all_live)),'location_missing_pages':missing_pages,'non_info_location_missing_bbox':missing_bboxes,'cleanup':receipt['cleanup'],'service_sources_unchanged':receipt['source_unchanged_during_run']},'coverage_followup':coverage['finding'],'limitations':['不是独立人员签认真值，也不是未暴露新样本的盲测。','本轮复核先冻结原件判断；误报反查另外标记后验来源，未回写冻结真值。','限定范围发现9个漏报组和3条误报，不构成全材料精确率/召回率。','本轮实际走API与独立Worker，未操作前端浏览器上传。','未开启AI；上海8份均有文字层，未检验扫描OCR准确率。','数值关系不等于缺陷组：文旅同一经济表3个勾稽关系及民政多列关系具有关联。','不同统计范围的车辆数差异只待复核，不计确定漏报；上年度及调整预算未提供。','规则义务完成数不能解释为准确率，且已发现CMM-001决算映射遗漏。','初读财政p28的图像缺字猜测已撤回：原件无图无绘图，系统已取全文，实际为下游字段匹配缺口。']}
(OUT/'review_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
columns=['id','classification','rule_id','title','severity','document_type','year','organization','page','bbox','table_or_section','snippet','expected','actual','difference','reason','suggestion','confidence']
with (OUT/'problem_list.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
 for r in issues:
  row={k:r.get(k) for k in columns}
  for k,v in row.items():
   if isinstance(v,(dict,list)):row[k]=json.dumps(v,ensure_ascii=False)
   if v is None:row[k]='未识别到/当前无法定位'
  writer.writerow(row)

link=lambda label,path:f'[{label}](<{ROOT/path}>)'
rows='\n'.join(f"| {s['organization'].replace('上海市','')} | {s['pages']} | 9 | {s['completed_obligations']}/{s['applicable_obligations']} | 待人工复核 |" for s in source_checks)
fnrows='\n'.join(f"| {r['id']} | {r['organization'].replace('上海市','')} | {r['page']} | {r['title']} | {r['expected']} | {r['severity']} |" for r in fn)
fprows='\n'.join(f"| {r['id']} | {r['organization'].replace('上海市','')} | {r['page']} | {r['title']} | {r['expected']} |" for r in false_positives)
feature_rows='\n'.join(f"| {f['priority']} | {f['title']} | {f['acceptance']} |" for f in features)
report=f'''# 真实PDF导入复测与功能缺口报告

日期：2026-10-08。分支：codex/zcode-remediation。规则：AGENTS.md 内置 v3_3_portable。

## 一 审校结论

**当前系统可以用于人工复核配合的真实文字层 PDF 审校试用，尚不符合无人复核的最终审校要求。** 上传、解析、独立 Worker、数据库和导出链路正常；扩大原文复核后，仍找到至少 **9 个漏报组、3 条确定误报**，另有已披露说明未匹配、未完成检查漏记及证据定位缺口。本轮固定业务代码评估，未实施修复，因此这些缺口仍存在。

8份均为2025年度上海市部门决算，共295页。部门、年度及九张真实核心表全部识别正确。所有页均有文字层，不等于每个字段都已正确匹配。发布日期、来源链接未提供。表名、金额、片段与页码均以原件为准，页码为从封面起计的物理 PDF 页码。

### 实际导入结果

| 材料 | 页数 | 核心表实例 | 系统义务完成数 | 实际服务结论 |
| --- | ---: | ---: | ---: | --- |
{rows}

8份原件均经过实际API上传、独立Worker分析、PostgreSQL入库、JSON/CSV/Word/PDF导出与重复下载。直接规则诊断与实际API报告共87条输出的规则编号及问题文字逐条相同。原件SHA未变化、金额事实来源校验0项错误、服务代码指纹未变化。全批10个服务场景通过、耗时131.61秒（含启动和限流等待），临时服务及数据库清理完整。其中另两个场景是同一监狱原件重复导入和一份既有扫描预算入口对照，不计入8份业务复核，扫描对照未开OCR，不代表内容审校通过。

**服务回执中的 passed 表示运行和导出一致，8份业务任务均为 review_required；既不表示无材料问题，也不表示检查全面。** 义务完成数是系统记账，本轮发现其漏映射问题，不能解释为准确率。

### 原件与系统对照范围

按A文档结构、B核心表、C数字、D口径说明、E证据交付五线检查。原件判断先冻结，再比对系统；针对系统输出回查得到的误报另外记录，没有回写成预先盲审结果。C线限定4份原件的16个确认数字关系，12个关系命中、4个遗漏；对应18条系统输出有6条重复。文旅经济表3个关系同属一张坏表，民政多列关系也有关联，不能按告警条数增加缺陷数。

这8份均来自此前20份开发材料，属于扩大深度的开发复测，不是独立盲测，不计算全产品精确率或召回率。本轮未操作前端浏览器上传，也未验证扫描OCR或可选AI检查。

## 二 问题清单

### 已确认漏报

以下9组在本轮原生诊断及实际API报告均未检出。两项商务差异原因按同类说明合并为1组；它们各自的原文金额、bbox保存在结构化清单。

| 编号 | 材料 | 物理页 | 原件问题 | 期望关系或修改依据 | 级别 |
| --- | --- | ---: | --- | --- | --- |
{fnrows}

交通机关运行同比按同段6679.47万元、减少335.66万元反推上年7015.13万元，335.66/7015.13×100=4.7848%，两位小数为4.78%。该推导值不冒充已读取的2024年原文件。商务零购置用途矛盾需要补充付款与车辆购置口径，当前确认的是文字自洽问题，不直接改写财务金额。

### 已确认误报

| 编号 | 材料 | 物理页 | 系统误报原因 | 原件正确关系 |
| --- | --- | ---: | --- | --- |
{fprows}

文旅V33-005仍对财政拨款总表无表头续页执行通用分项求和；V33-120没有纳入下页首行明细。民政完成率在“公务接/待费”的跨页断词处失去分项身份，实际API虽将其降为manual_review，错误车辆告警仍保留。证据门禁的降级不能代替纠正算式绑定。

### 已正确检出的真实问题

交通p30两项总体同比、p39出国及接待同比，文旅p22经济分类汇总不平、p26将其他收入写成经营收入、p26–27一般公共预算同比，民政p11/p14/p19社会保障类合计与已列款级分项不平，监狱p26车辆和接待占比，以及发改p34车辆完成率，均与原件对应。民政三表差额分别7274.70、7051.21、5131.83万元，已经检查续页，没有据此臆定缺少某个科目或哪笔应改。

### 检查覆盖与交付缺口

财政局p28原件和系统artifact均含购置支出0万元、运行维护支出30万元，CMM-001却记“说明未披露”。同时，该规则只映射预算专用义务，决算台账没有接收其2条insufficient_data关系，仍显示42/43、自动完成率100%、未完成清单仅AI未运行。只读复现中，将CMM-001在内存改成pass后整份台账相同。质量门仍保留rules_insufficient_data并返回review_required/incomplete_scope，不能称整个任务假绿。

交通CMM-001将p39三公预算说明定位到封面p1，且与V33-108重复。商务3条跨表/结转告警没有页码；全8份有4条位置缺页码（含民政1条取整提示）、11条非info位置缺主bbox；不等同于每条都完全没有其他证据。真实问题仍需要原单元格、原文片段和每端物理页，不能用算式解释代替原文。

## 三 待人工复核

- 交通三公表仅标“预算数”，说明标“年初预算”。现系统默认将泛预算数认作年初预算，形成5条相关告警。缺少原年初/调整预算时不能把这些全部判为确定错误或确定误报，应先核对预算阶段。
- 文旅经济基本支出中的两项三公金额高于三公全口径金额，倒挂数字存在，但同一经济表本身已多处不平，需财务底稿确认口径和正确金额，不计入本轮确认误报/命中统计。
- 6例公务用车与国有资产车辆数量差异仅列待复核。统计范围、时点及所属单位未完全明确，不直接认定大小关系违反。
- 财政p28曾被fitz仅抽出63字；pdfplumber和系统artifact实际提取652字，且原件无图片无绘图。已撤回“混合图像页/OCR漏字”猜测，实际系统缺口是下游字段归一化与匹配。
- 核心表存在、数值来源正确、义务完成率高均不能证明未披露项目、语义关系及全部数字比较已经覆盖。当前未提供2024年度原件、原始/调整预算和合同/车辆台账。

## 四 下一步建议

先处理P1项目并用相同原件复验，再扩大不同部门、年度和版式的试用。对已确认材料问题同步修改表与说明；对未知口径补依据后校正重审。确认错误、取整提示、待复核和未完成关系应分别展示。

| 优先级 | 缺失或不完整的功能 | 用真实原件验收的要求 |
| --- | --- | --- |
{feature_rows}

在这些缺口修复前，可以导入真实材料做辅助审校，但业务人员须复核告警、未完成清单和本轮指出的漏检区域。正式独立验收应另选未参与开发的新原件，由独立人员封存判断，不能把这20份材料改名作为盲测。

### 可复查附件

- {link('结构化总结果与原件问题清单','outputs/real_intake_review_20261008/review_result.json')}
- {link('问题清单CSV','outputs/real_intake_review_20261008/problem_list.csv')}
- {link('真实服务导入和导出回执','outputs/real_intake_review_20261008/live/service_receipt.json')}
- {link('数字原件冻结判断','outputs/real_intake_review_20261008/numeric_truth.json')}，{link('数字误报漏报对照','outputs/real_intake_review_20261008/numeric_comparison.json')}
- {link('口径说明冻结判断','outputs/real_intake_review_20261008/disclosure_truth.json')}，{link('口径说明对照','outputs/real_intake_review_20261008/disclosure_comparison.json')}
- {link('结构冻结判断','outputs/real_intake_review_20261008/structure_truth.json')}，{link('决算义务映射缺口复现','outputs/real_intake_review_20261008/structure_coverage_followup.json')}

复跑当前固定版本的原生检查：

```bash
.venv/bin/python scripts/verify_native_pdf_core.py --input outputs/real_intake_review_20261008/selected_originals --output outputs/real_intake_repeat_new
```

测试脚本、原件指纹及导出结果均保留；既有未提交业务工作不被覆盖。
'''
report_path=ROOT/'docs/真实PDF导入复测与功能缺口报告_20261008.md'
report_path.write_text(report,encoding='utf-8')
(OUT/'delivery_receipt.json').write_text(json.dumps({'report':str(report_path),'report_sha256':sha(report_path),'review_result_sha256':sha(OUT/'review_result.json'),'csv_sha256':sha(OUT/'problem_list.csv'),'source_truth_sha256':{name:sha(OUT/name) for name in ['numeric_truth.json','disclosure_truth.json','structure_truth.json','root_numeric_truth.json']},'numeric_truth_matches_comparison':sha(OUT/'numeric_truth.json')==nc['truth_sha256'],'disclosure_truth_matches_comparison':sha(OUT/'disclosure_truth.json')==dc['truth_sha256'],'structure_truth_matches_comparison':sha(OUT/'structure_truth.json')==sc['truth_sha256'],'source_checks':source_checks,'business_result_scope':'development_review_no_business_code_fix_or_formal_accuracy_claim'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'missed_groups':len(fn),'false_positives':len(false_positives),'live_issue_total':len(all_live),'pages':sum(r['pages'] for r in source_checks),'facts':sum(r['facts'] for r in source_checks),'report':str(report_path)},ensure_ascii=False))
