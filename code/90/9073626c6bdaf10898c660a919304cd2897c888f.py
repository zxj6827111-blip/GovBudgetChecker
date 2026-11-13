"""汇总可重跑的技术验收记录，区分物理读取、事实绑定和规则准入。"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def main():
    inventory = json.loads((BASE/'inventory.json').read_text())
    totals = json.loads((BASE/'comparison_totals.json').read_text())
    docs = json.loads((BASE/'comparison_summary.json').read_text())
    issues = json.loads((BASE/'issues.json').read_text())
    seal = json.loads((BASE/'source_freeze.json').read_text())
    code_before = json.loads((BASE/'code_before.json').read_text())
    code_after = {str(p.relative_to(ROOT)):sha(p) for name in ('src','api') for p in (ROOT/name).rglob('*.py')}
    integrity = dict(source_files_unchanged=all(sha(Path(d['source_pdf']))==d['sha256'] for d in inventory),
                     frozen_originals_match=all(sha(Path(d['frozen_pdf']))==d['sha256'] for d in inventory),
                     frozen_oracles_unchanged=all(sha(BASE/'documents'/d['doc_id']/'source_oracle.json')==seal['oracles'][d['doc_id']] for d in inventory),
                     production_code_unchanged=code_before==code_after)
    assert all(integrity.values()),integrity
    assert totals['source_amounts']==11598
    assert sum(totals['facts'].values())==totals['source_amounts']
    assert totals['facts']==dict(bound_exact=11373,missing=120,semantic_mismatch=26,manual_review=79)
    assert totals['reading']==dict(read_exact=11598)
    assert json.loads((BASE/'known_19_reverification.json').read_text())['verified']==19
    assert '26 passed' in (BASE/'v23_fixed_regression.log').read_text()
    original_status={d['doc_id']:d for d in inventory}
    findings=[]
    for doc_id,page in [('SH2024_01',18),('SH2024_14',20)]:
        affected=[r for r in issues if r['doc_id']==doc_id and r['page']==page and r['fact_status'] in ('missing','semantic_mismatch')]
        wrong=[r for r in affected if r['fact_status']=='semantic_mismatch']
        missing=[r for r in affected if r['fact_status']=='missing']
        total=next(r for r in wrong if r['expected']['subject_code']=='total')
        doc=original_status[doc_id]
        findings.append(dict(rule_id='PDF-CONTINUATION-COLUMNS',severity='high',issue_kind='parser_acceptance_defect',
             document_type='决算公开',year=2024,organization=doc['filename'].removesuffix('.pdf'),
             physical_page=page,table_or_section='一般公共预算财政拨款支出决算表（续表）',
             title='前页7列、续页5列未对齐，项目支出被绑定为合计',
             affected_cells=len(affected),missing_facts=len(missing),wrong_column_facts=len(wrong),
             evidence_truth_ids=[r['truth_id'] for r in affected],
             expected='合计、基本支出、项目支出三列分别保留，续页科目与原页对应',
             actual=dict(project_total_on_source=total['raw_text'],materialized_indicator=total['actual_facts'][0]['indicator']),
             bbox=total['bbox'],reason='跨页表列数变化；逻辑表沿用前页列位置，续页前两个金额列进入标签区域。',
             guard='整张表已有事实标为 low_confidence / cross_page_width_drift，未作为确定性规则操作数',
             suggestion='先对齐物理列位置及来源身份，再生成事实；保留当前拒绝不安全事实的门禁。',
             source_pdf=doc['source_pdf'],frozen_pdf=doc['frozen_pdf'],source_image=str(BASE/'images'/f'{doc_id}_p{page:02}.png')))
    fund_missing=[r for r in issues if r['table_code']=='FIN_08_gov_fund' and r['fact_status']=='missing']
    fund_docs=Counter(r['doc_id'] for r in fund_missing)
    findings.append(dict(rule_id='PDF-FUND-BALANCE-FACTS',severity='high',issue_kind='fact_coverage_limit',
        document_type='决算公开',year=2024,title='政府性基金表年初/年末结转结余未进入财政事实',
        affected_cells=len(fund_missing),nonzero_cells=sum(float(r['raw_text'].replace(',',''))!=0 for r in fund_missing),
        documents=[dict(doc_id=k,filename=original_status[k]['filename'],count=v,
                        physical_pages=sorted({r['page'] for r in fund_missing if r['doc_id']==k})) for k,v in fund_docs.items()],
        evidence_truth_ids=[r['truth_id'] for r in fund_missing],
        expected='原页余额金额完整物化为财政事实，且保留金额原文、科目、列、页码和bbox',
        actual='物理单元格金额均存在；基金表映射仅覆盖本年收入及支出三列，余额两列未生成事实',
        reason='FIN_08 的字段别名未覆盖年初/年末余额；回执仅枚举已映射金额列，未对未映射余额列告警。',
        suggestion='补充余额列身份与事实物化，并让覆盖检查从原页所有金额列起算。'))
    findings.append(dict(rule_id='PDF-READING-RECEIPT-COVERAGE',severity='medium',issue_kind='diagnostic_coverage_limit',
        title='读取缺口和未绑定回执为0，不能证明全表事实完整',
        documents=15,observed_reading_gaps=0,observed_unbound_money_cells=0,
        independent_missing_facts=120,independent_wrong_column_facts=26,
        reason='原页数字已落入物理单元格即不会报读取缺口；未映射金额列未进入绑定回执的分母。',
        suggestion='分别输出原页金额覆盖、列身份、事实覆盖和规则准入回执，缺失及待复核均纳入分母。'))
    write(BASE/'acceptance_findings.json',findings)
    final=dict(acceptance_date='2026-10-15',recorded_at=datetime.now(timezone.utc).isoformat(),
        input_directory='/Users/zhangxingjin/Desktop/决算测试上海市/2024',
        parser_fingerprint='pdfplumber-cells-v23-native-structure-recovery',
        verdict='v23固定修复有效；新批材料金额物理读取一致，但完整财政事实验收未通过',
        coverage_scope='15份2024年度决算的九类核心财政表；不含正文/绩效附表的逐格准确率验收',
        pdfs=15,pages=520,actual_core_table_kinds=135,core_physical_regions=218,
        amount_cells=11598,physical_amount_literal_matches=11598,
        fact_present=11478,fact_missing=120,fact_wrong_column=26,fact_identity_manual_review=79,
        identity_and_column_matched=11373,matched_but_rule_blocked=225,
        rule_admission_blocked_total=251,matched_and_rule_admitted=11148,
        conservative_complete_rate=11148/11598,
        known_19_currently_verified=19,fixed_regression_tests_passed=26,
        frozen_v22_parser_same_physical_amount_matches=11598,
        new_batch_does_not_demonstrate_incremental_v23_amount_recovery=True,
        toc_page_text_fallback_candidates=15,toc_candidates_counted_as_real_tables=False,
        source_grid_adjudication_notes='79项原页科目跨线/编码挤行，独立网格工具无法可靠裁定；不判系统错误，不计成功，继续留分母。',
        prior_development_sample_hash_overlap=0,human_independent_full_truth=False,
        full_suite_rerun=False,existing_services_restarted=False,current_existing_service_version_verified=False,
        live_api_worker_database_validation_in_this_round=False,ocr_used=False,dependencies_added=False,
        integrity=integrity)
    write(BASE/'final_summary.json',final)
    table=['单位 | 核心表金额格 | 字面读取一致 | 财政事实缺失 | 确定错列 | 科目待复核 | 已生成但门禁阻断']
    for d in docs:
        table.append(f"{d['filename'].removesuffix('.pdf')} | {d['source_amounts']} | {d['reading'].get('read_exact',0)} | {d['facts'].get('missing',0)} | {d['facts'].get('semantic_mismatch',0)} | {d['facts'].get('manual_review',0)} | {d['blocked_by_parser_gate']}")
    report='''上海市2024年度决算 PDF 表格识别技术验收
验收日期：2026-10-15。以下页码均为 PDF 物理页，从1开始。

一、验收结论
当前代码确为 v23，交接记录列出的改动文件哈希全部吻合。原有4份/19格修复重新运行公开解析与事实物化入口，19格金额、科目、列及原页证据均通过；固定回归26项通过。
新批15份、520页的金额物理读取结果良好，但“表格读取到完整、可靠财政事实”的验收未通过，不能宣称准确率问题已经全部解决。

二、范围、方法与分母
只读验收本地文件夹全部15份PDF；原件及代码先冻结哈希，原页文本/词坐标与独立PyMuPDF网格结果先保存，再运行被测pdfplumber公开解析入口和财政事实入口。
15份均有原生文本层；封面均标注2024年度决算。9类核心表共135份逻辑业务表，跨页物理区域218处。所有218处均能映射到解析结果。另有15个目录页page_text_fallback候选，未计为真实业务表。
金额分母为独立原页核心表中11,598格明确小数金额，包含0.00、余额列、缺失项和待复核项。数字比较未使用金额容差；忽略排版空白后包括千分位与小数位在内的字面文本全部一致。
统计不涵盖正文数字、绩效附件、组织名单等其他23处网格的逐格准确率；无扫描件、未知模板或未知未来材料的准确率保证。
本次是不同提取引擎的来源对照及重点原页视觉复核，不是独立人工完整金标准或第三方盲测。与v23交接列出的旧开发原件哈希无重合；不能据此证明与历史全部开发活动均无接触。

三、分层结果
物理单元格金额读取一致：11,598 / 11,598（本批上述范围内100%）。没有发现漏金额或错数字；这不代表科目/列/事实都正确。
有同原页证据对应的财政事实：11,478 / 11,598（98.97%）。其中26格确定错列，79格科目身份尚待裁定。
金额、表种、科目及列身份对照一致：11,373 / 11,598（98.06%）。
其中225格虽对照一致，仍因整表跨页列宽漂移被阻止用于规则。加上26格错列，共251个已有事实被门禁阻断。
确认对照一致且当前可进入规则：11,148 / 11,598（96.12%，保守可用率；未裁定项仍在分母）。此数不是普适PDF识别率或业务规则准确率。
事实缺失120格 + 确定错列26格 + 待复核79格 + 对照正确但阻断225格 + 对照正确且准入11,148格 = 11,598格。

四、确定问题
1. 续表列数变化，金额读到了，但财政列身份错误。
   人社局物理第18页：8行，16格未生成事实、8格项目支出错记为合计。合计行原页依次为291,858.57 / 108,158.96 / 183,699.61，当前total_actual却为183,699.61。
   经信委物理第20页：18行，36格未生成事实、18格项目支出错记为合计。合计行原页依次为1,342,363.79 / 66,005.37 / 1,276,358.44，当前total_actual却为1,276,358.44。
   两张表前页7列、续页5列，逻辑表沿用前页列位置。科目名称中还出现前两个金额，证明列对齐失效。整表119及132个现有事实已被low_confidence / cross_page_width_drift门禁挡住，因此未把这26个错列事实当确定性规则操作数；但相关审校能力也未恢复。
2. 政府性基金表余额未进入财政事实。
   住建委第25页8格、商务委第21页8格、教委第26页20格、民政局第24页16格、科委第21页8格、经信委第24页8格，共68格。均保存在物理单元格，但未物化为余额事实。
   其中16格非零余额全部出现在民政局。合计行年初1,782.05、年末1,782.15，原页清晰、物理金额保存，facts中无对应余额。
   其余52格虽为0.00，仍是原件明确披露的金额，不能从分母删除。
3. 读取与绑定回执存在覆盖边界。
   15份的reading_gaps和unresolved_money_cell_count均为0，但原页对照仍发现上述120格缺事实及26格错列。读取检查主要判断原页数字是否已落入格；绑定回执只遍历已映射金额列。余额列没映射时不进入该回执分母。
   这不表示回执曾承诺原页全覆盖：original_pdf_full_coverage_verified本来就是false。应避免把零缺口直接当作全表事实完整。

五、待复核及验收器自身限制
79格：发改委（部门）75格、司法局2格、教委2格。原件科目/编码跨横线，独立PyMuPDF网格有截断或编码尾字挤入下一行的问题。金额和列均一致，但独立科目真值尚未逐字裁定。这79格不判作系统确定错误，也不计入成功。
民政局第17页一处原生词把相邻同额143,481.99合并；独立网格及原页渲染确认两格确实分别存在，均被当前系统正确读取。原始自动比较和后续裁定分别保留。
初版比较器还把双边总表的余额项目名称误当成列指标，产生54项验收器误报；已按收入/支出物理列合同排除，并保存initial_*记录，未为此修改被测系统。

六、v23是否生效与验证边界
19格历史修复现查通过，26项固定测试通过，证明修复当前仍生效。
冻结v22解析器（下游保持当前版本）对这15份的11,598格物理金额同样全部读取一致。因此本批没有提供v23新增金额恢复的增益证据，也不能把本批金额全读到归因于v23；本批暴露的是仍未解决的跨页列对齐和余额事实覆盖问题。
未重跑完整测试套件，本轮不能声称全套测试全绿。没有安装依赖、使用OCR、修改解析器、重启部署用户服务、覆盖历史任务。本轮没有再跑API/Worker/Postgres隔离栈，未核实现有运行中服务是否已加载v23；这里验证的是新进程加载当前本地代码的公开解析和事实入口。
15份源PDF、冻结副本、独立原页证据、src/api代码哈希全程无漂移。旧4份/19格回归和旧3格原件缺标签证据全部保留。

七、逐份结果
'''+ '\n'.join(table) + '''

八、下一步
先修复两张续表的物理列对齐，再补政府性基金及同结构国资表的余额字段和全金额列覆盖回执；保留拒绝不安全事实的门禁，不能只取消low_confidence。
对上述78格续表问题、68格基金余额建立固定回归，重跑本批全量对照及原有19格；79格科目真值独立裁定后再更新完整可用率。代码修改应作为下一轮工作，当前先保留本次失败验收基线。

九、证据索引
final_summary.json：最终分层数字、验证边界和完整性检查。
inventory.json、source_freeze.json、code_before.json、v23_identity.json：输入/代码冻结及版本核对。
documents/*/source.pdf、source_oracle.json：冻结原件和先于被测运行采集的原页证据。
documents/*/source_amount_truth.json、comparison.json：每格金额、页码、bbox、原格行列、预期科目/列及实际事实。
documents/*/artifact.json、facts.json、receipt.json、fact_admission.json：真实公开入口输出及准入结果。
acceptance_findings.json：按根因归并的确定问题；issues.json、issues.csv：全部待处理单元格及门禁项。
images/、adjudications.json、initial_*：原页渲染、验收器差异裁定及初版记录。
known_19_reverification.json、v23_fixed_regression.log、v22_parser_comparison.json：历史修复现查、26项回归及v22解析器对照。
重跑顺序：冻结源证据（已有冻结不可覆盖）→run_parser.py→compare_source.py→compare_v22.py→finalize_report.py。
'''
    (BASE/'验收报告.txt').write_text(report)
    manifest={str(p.relative_to(BASE)):sha(p) for p in BASE.rglob('*') if p.is_file() and p.name!='evidence_manifest.json' and '__pycache__' not in p.parts}
    write(BASE/'evidence_manifest.json',manifest)
    print(json.dumps(final,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
