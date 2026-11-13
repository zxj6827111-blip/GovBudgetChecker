"""新批原件审校与系统验收交付；不同分母、不同层次分别汇总。"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv
import hashlib
import json
from pathlib import Path
import re

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def read(name):return json.loads((BASE/name).read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(name,value):(BASE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    inventory=read('inventory.json');comparison=read('comparison_totals.json');perdoc=read('comparison_summary.json')
    business=read('confirmed_source_issues.json');engine=read('engine_candidate_review.json')
    database=read('service_readback_verification.json');service=read('service/service_receipt.json')
    native=read('native_amount_source_audit.json');seal=read('source_freeze.json');truth_seal=read('preparse_truth_seal.json')
    before=read('code_before.json')
    after={str(p.relative_to(ROOT)):sha(p) for folder in ['src','api'] for p in (ROOT/folder).rglob('*.py')}
    intact=dict(production_code_unchanged=before==after,
        source_pdfs_unchanged=all(sha(Path(d['source_pdf']))==sha(Path(d['frozen_pdf']))==d['sha256'] for d in inventory),
        frozen_source_oracles_unchanged=all(sha(BASE/'documents'/d['doc_id']/'source_oracle.json')==seal['oracles'][d['doc_id']] for d in inventory),
        preparse_truth_unchanged=all(sha(BASE/'documents'/d['doc_id']/'preparse_source_truth.json')==d['sha256'] for d in truth_seal['documents']))
    assert all(intact.values()),intact
    assert comparison['source_amounts']==native['summary']['checks']==8893
    assert database['source_comparison']==comparison['facts']
    assert service['status']=='passed' and service['cleanup']=='complete' and len(service['scenarios'])==11
    assert len(business)==11 and sum(i['decision']=='false_positive' for i in engine)==10
    readers=read('parser_results.json')
    actual_blocked=sum(r['facts']-r['admitted_facts'] for r in readers)
    gate_rows=[]
    for d in inventory:
        receipt=read(f"documents/{d['doc_id']}/receipt.json")
        gate_rows.append(dict(doc_id=d['doc_id'],filename=d['filename'],
            **{k:receipt.get(k) for k in ['amount_cell_count','fact_present_count','fact_bound_count','rule_admitted_count',
                'rule_blocked_count','pending_review_count','unclassified_numeric_projection_count']},
            unbound_money_cells=receipt['reading_quality'].get('unbound_money_cells',[])))
    write('receipt_diagnostics.json',gate_rows)

    bad_rows=[]
    for d in inventory:
        rows=read(f"documents/{d['doc_id']}/comparison.json")
        bad_rows.extend(r for r in rows if r['fact_status']!='bound_exact' or r['reading_status']!='read_exact')
    counts=Counter((r['doc_id'],r['fact_status']) for r in bad_rows)
    findings=[
      dict(rule_id='PDF-NATIVE-AMOUNT-CONTINUATION',severity='high',kind='parser_defect',organization='上海市水务局',pages=[18,19],
           title='金额尾位跨横线，4格金额被截断、7格未形成可用数值',affected_money_cells=11,
           numeric_conversion_missing=7,truncated_numeric_values=4,
           example=dict(expected='865,642.68',actual='865,642.6',physical_page=19),
           source_of_truth='native_amount_source_audit.json；32项来源覆盖层中的8项金额裁定；不按差额补数',
           runtime_guard='该一般公共预算表149个已有事实被读取门禁阻断，其中含4个截断金额。',
           suggestion='根据连续原字符、金额车道和标签归属恢复尾字并处理相邻行混入尾字；增加截断小数仍可转数字的反例。'),
      dict(rule_id='PDF-ECONOMIC-CODE-HEADER',severity='high',kind='parser_or_materialization_defect',organization='上海市水务局',pages=[20,21],
           title='65个经济分类事实丢失原页科目编码，2个经费合计未生成事实',missing_subject_codes=65,missing_total_facts=2,
           source_examples=['301 工资福利支出','302 商品和服务支出','人员经费合计37,896.38','公用经费合计11,056.12'],
           reason='原网格表头只得到“经济分类科目”，编码列未被正确继承为标签列；原页编码真实存在。',
           runtime_guard='65个无编码事实仍被通用准入接受，不能据此宣称按编码审校完整。',
           suggestion='恢复原生表头和编码列物理身份；保留来源列，确保跨列合计标签也能绑定。'),
      dict(rule_id='PDF-HEADER-TEXT-IN-SUBJECT',severity='medium',kind='parser_defect',organization='上海市体育局',pages=[7,9],
           title='6个科目行的39个金额事实名称混入表头“款”字',affected_facts=39,
           example=dict(expected='教育支出',actual=['教育支出 款','款\\n教育支出']),
           reason='原件表头残留文字的行归属错误，金额及编码本身一致。',
           suggestion='按原生对象及表头角色清理串入数据行的表头文字，不能按部门或科目字典特判。'),
      dict(rule_id='RULE-FALSE-POSITIVE-NEW-BATCH',severity='high',kind='rule_defect',title='3类业务规则误报，共10条候选',
           cases=[dict(organization='上海市体育局',count=7,rule='V33-240',reason='收入横向求和遗漏上级补助收入。'),
                  dict(organization='上海市体育局',count=1,rule='V33-235',reason='把年初预算0误作本年决算的条目开头金额。'),
                  dict(organization='上海市水务局、上海市绿化和市容管理局',count=2,rule='CMM-007',reason='将出国费本期金额与车辆费增额、增长率混用。')],
           evidence='engine_candidate_review.json；source_arithmetic_checks.json；原页29、36、37等图像',
           suggestion='按完整列清单、预算/决算阶段及明确费用项目绑定操作数；复用真实新样本建立反例，不靠金额容差压制。')
    ]
    write('system_findings.json',findings)
    doc_business=Counter(r['doc_id'] for r in business)
    result=dict(acceptance_date='2026-10-15',recorded_at=datetime.now(timezone.utc).isoformat(),
        verdict='新增10份原件未通过完整读取/事实验收；同时确认11个原件问题及10条规则误报',
        parser_fingerprint='pdfplumber-cells-v24-continuation-and-fiscal-coverage',scope=dict(pdfs=10,pages=357,core_table_kinds=90,physical_core_regions=150,amount_cells=8893,
            selected_from='/Users/zhangxingjin/Desktop/决算测试上海市/2024',excluded='旧15份及2025目录',prior_recorded_source_hash_overlap=0),
        parsing=dict(physical_numeric_exact=8882,physical_numeric_truncated=4,physical_numeric_unavailable=7,
            facts_missing=9,subject_identity_mismatches=104,sports_names_polluted=39,water_subject_codes_missing=65,
            source_identity_and_value_exact=8776,confirmed_rule_admitted=8631,actual_existing_facts_rule_blocked=actual_blocked,
            complete_documents=8,incomplete_documents=['SH2024NEW_02','SH2024NEW_06'],
            note='分层数字有重叠，不能相加；149个门禁阻断含145个金额/身份对照正确事实和4个截断金额事实。'),
        source_native_audit=dict(checks=8893,source_grid_truncations_found=8,source_only_overlay_amounts=8,source_only_overlay_identities=24,
            initial_source_grid_truth_and_initial_comparison_preserved=True,
            lesson='两个取表引擎可能共同截断尾字；本轮逐格回到连续原字符核验，没有根据勾稽补金额。'),
        source_document_issues=dict(count=11,severity=dict(Counter(r['severity'] for r in business)),documents=6),
        rule_review=dict(engine_issues=sum(len(json.loads(p.read_text()).get('issues',[])) for p in (BASE/'service').glob('*/report.json') if p.parent.name!='native_budget'),
            noninfo_candidates=len(engine),false_positives=10,remaining_candidates_merged_into_source_issues=11),
        independent_arithmetic=read('source_arithmetic_summary.json'),
        service=dict(status=service['status'],cleanup=service['cleanup'],scenarios=len(service['scenarios']),new_original_documents=10,
            stored_facts=database['stored_facts'],public_database_parity=database['status'],
            recognition_not_passed_despite_service_storage_success=True),
        integrity=intact,production_code_modified=False,dependencies_added=False,ocr_used=False,existing_services_restarted=False,
        limitations=dict(human_independent_gold_standard=False,universal_accuracy_guarantee=False,original_pdf_full_coverage_verified=False,
            real_nonzero_state_capital_balance_sample=False,source_urls_or_official_publication_dates_not_provided=True,
            performance_appendices_not_fully_cell_audited=True,new_full_pytest_run=False))
    assert actual_blocked==149
    write('final_summary.json',result)
    lines=['报告 | 页数 | 核心表读取/事实结果 | 原件确认问题数']
    byid={r['doc_id']:r for r in perdoc}
    for d in inventory:
        p=byid[d['doc_id']]
        status='通过本轮逐格对照'
        if d['doc_id']=='SH2024NEW_02':status='39格科目名称污染'
        if d['doc_id']=='SH2024NEW_06':status='4截断、7数值化失败、9缺事实、65缺编码'
        lines.append(f"{d['filename'].removesuffix('.pdf')} | {d['pages']} | {status} | {doc_business[d['doc_id']]}")
    report='''上海市2024年度新增10份决算：详细检查与v24新样本验收
日期：2026-10-15。所有页码均为PDF物理页，从1开始。

一、审校结论
本次检查原2024目录从15份增至25份后新增的10份，共357页，封面均为2024年度部门决算。2025目录20份旧文件未纳入本轮。
当前被测指纹pdfplumber-cells-v24-continuation-and-fiscal-coverage。10份均与已记录开发原件无同SHA256；这不等于独立人工签署的盲测或近似重复审查。
九类核心财政表90张、物理区域150处均出现并被系统识别；未发现整张核心表缺失。但完整读取/财政事实验收未通过：体育局和水务局有确定系统缺陷，其余8份在本轮核心表分母内通过逐格对照。
同时，用户提供的PDF原件中确认11个内容/结构问题（8 high、1 medium、2 low）。没有官网原始下载链接，不推断官网文件是否与本地PDF相同。

二、验收方法和误差控制
先保存新批原件、SHA256、当前源码指纹、独立PyMuPDF文字/词坐标/网格和8,893个原页金额候选，再运行被测公开解析入口。
随后对全部8,893个金额逐格核查连续原字符，发现独立网格工具本身也截断了水务局8格金额尾字。完整尾字在原件原生字符中真实存在；通过字符索引和bbox核实后写入单独来源裁定层，没有按合计差额补数，没有改被测源码或覆盖初版真值。
例如水务局第19页合计，独立网格和系统都曾得到865,642.6，但连续原字符为865,642.68。不能因为两个引擎碰巧一致就称准确。
另16格开放顶行、8格科目跨线截字属于独立网格工具的定位/截字限制，均用原件词坐标或原生字符独立裁定，未把系统facts复制成真值。
没有通过放宽金额容差处理读取错误。金额读取要求字面准确；业务勾稽按v3_3_portable默认0.5万元、百分比0.1个百分点判别，并保留容差内的小差异。

三、逐份结果
'''+ '\n'.join(lines)+'''

四、确定的系统问题
1. 水务局第18—19页：7格没有形成可用数值，4格金额尾位被截断。原格文字并非全部消失，但不能作为准确金额使用。
   截断例：121,914.65→121,914.6；374,454.99→374,454.9；190,553.95→190,553.9；865,642.68→865,642.6。
   相邻行还混入上一行尾字，导致本来完整的12,338.72、37,330.24、47,846.16等未被数值化。
   该一般公共预算表149个已有事实已被读取门禁阻断，不能仅为提高完成率取消门禁。
2. 水务局第20—21页：65个经济分类事实的subject_code变成null，原页301、302、30101等编码明确存在。人员经费37,896.38、公用经费11,056.12两格金额保存了，但未生成事实。
   原网格表头只提取到“经济分类科目”，编码列未正确绑定；65个无编码事实仍被通用准入接受，存在按编码审校不完整风险。
3. 体育局第7、9页：教育支出、职业教育、中等职业教育6个科目行，共39个金额事实的subject_name混入“款”字。金额和编码一致，名称不准确。
4. 规则误报：24条非info候选中10条经原页复核应剔除，不能让用户修改本来正确的原文。
   体育局7条V33-240遗漏“上级补助收入”。例如科学技术支出5,328.84=4,898.83+200.00+225.64+4.37，本来成立；系统漏加200.00后误报差200.00。
   体育局1条V33-235把年初预算0当作本年决算的条目开头金额；原文只说年初预算0、决算3,631.14，不能据此判前后决算冲突。
   水务局、绿化局各1条CMM-007把出国费金额与车辆费增额/增长率拼在一起。正确车辆复算分别为250.60÷(250.60-58.59)-1=30.51%，326.94÷(326.94-50.02)-1=18.06%，均与原文一致。

五、金额与事实分层数字
核心表分母8,893格：物理数值字面正确8,882格，截断4格，未形成可用数值7格。
9格无财政事实（含前述7格和2个经费合计）；104格科目字段异常（体育名称39、水务编码65）；另4个已有事实金额截断。
金额、科目与列身份对照正确8,776格，其中145格仍被来源表读取门禁阻断；确认对照正确且准入8,631格。
实际已有事实门禁阻断149个=145个来源对照正确事实+4个截断金额事实。
上述层次数字有重叠，不得相加成“总问题数”。原页唯一金额格中确定缺失/截断/科目异常共117格，另145格受同表门禁间接影响。

六、原件确认问题清单
'''
    for i in business:
        report+=f"\n{i['issue_id']} [{i['severity']}] {i['organization']} 第{i['page']}页：{i['title']}\n原文/实际：{i['actual']}\n期望/复核：{i['expected']}\n差异：{i['difference']}\n建议：{i['suggestion']}\n"
    report+='''
七、待人工复核和边界
规划资源局收入表有原页可见的层级缺项与5组重复行；类级可见汇总差99,819.81只能作为结构完整性线索，不能直接认定总收入本身错误，更不能按差额猜填缺失科目。
水务局原件有金额尾字挤出格线、表头裁切等排版风险，建议从原始表格重导出，同时修复系统原生字符/列身份读取能力。
独立原页勾稽共4,030项：3,762项相等、176项在默认容差内、84项比较项不完整、3项直接差异、5项层级不完整。后8项关联规划资源局，按根因归并在问题清单。84项未以空白补零，不计作已确认错误或全部通过。
绩效文字在材料中可检查，但外部绩效附件是否齐全及其逐格准确率没有完成独立验收；发布日和官网来源链接未提供。
本批没有真实非零国资表金额；国资委作为发布单位不等于这份报告一定有国资经营预算收支，其本报告国资表仍无可验非零余额。
original_pdf_full_coverage_verified保持false；本轮不是独立人工完整金标准，也不保证任意未来PDF。

八、真实服务与完整性
临时API、独立Worker和Postgres共11个场景（1个合成预算控制+10份不同新增原件）完成，导出与存储技术检查通过。
从真实数据库回读8,884个事实，逐条与公开入口金额、科目、指标、阶段、单位、年份及证据对照，一致；数据库也复现4截断、9缺事实、104科目字段异常，没有把函数层发现当成持久化已修好。
服务技术通过不等于材料审校通过；新增原件任务均保留review_required状态。临时资源已清理。
本轮没有改生产代码、重启既有服务、覆盖旧任务、安装依赖或使用OCR；也未再次运行完整pytest套件。本轮依靠新样本原页对照、独立计算、真实服务和数据库复核，不复用旧15份的通过结论。
新原件、运行前独立网格候选、初版比较、原生字符裁定及最终比较均分别保留；生产代码和原件哈希无漂移。

九、下一步
这10份已足够稳定复现新问题，暂不需要继续扩大同类样本数量。
优先修复水务局金额跨行尾字与经济分类编码/合计绑定、体育局表头字串入科目，再修复遗漏上级补助、预算决算阶段混用、不同费用项目混用的3类误报。
将本批失败原件和裁定加入固定回归，保留既有19格及旧15份回归。不能因为修复新样本而删除旧边界或放松门禁。
原件问题应根据底稿修改PDF源表/说明；系统问题应修改读取和规则。两类问题不能混为一谈。
后续如补样，最有价值的是未参与开发、且国资表年初/年末余额真实非零的完整决算原件及官网链接。2025目录可另开一批按年度单独验收。

十、证据索引
final_summary.json：本轮最终分层结果及边界。
详细检查报告.txt、原件问题清单.csv、confirmed_source_issues.json：原件11个确认问题及原始字符定位。
system_findings.json、issues.json、issues.csv：系统根因和逐格失败/阻断项。
engine_candidate_review.json：24条非info候选逐条裁定，含10条误报。
source_arithmetic_checks.json、source_duplicate_rows.json：独立勾稽和原件重复行。
native_amount_source_audit.json、source_truth_overlay.json、preparse_truth_seal.json：全部金额原生字符复核及独立网格纠偏；原初真值未覆盖。
documents/*/source.pdf、source_oracle.json、preparse_source_truth.json：冻结原件和运行前原页证据。
documents/*/artifact.json、facts.json、comparison.json、receipt.json：当前v24解析、事实、逐格对照和回执。
service/、service_database/、service_readback_verification.json：本轮真实服务、数据库及持久化复核。
images/：相关完整原页渲染；页码均为物理页。
'''
    (BASE/'详细检查报告.txt').write_text(report)
    manifest={str(p.relative_to(BASE)):sha(p) for p in BASE.rglob('*') if p.is_file() and p.name!='evidence_manifest.json' and '__pycache__' not in p.parts}
    write('evidence_manifest.json',manifest)
    print(json.dumps({k:v for k,v in result.items() if k in ['verdict','scope','parsing','source_document_issues','rule_review','service','integrity']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
