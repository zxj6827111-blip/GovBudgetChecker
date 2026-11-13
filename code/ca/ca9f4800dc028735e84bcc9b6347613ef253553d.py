from pathlib import Path
from decimal import Decimal as D
import json,hashlib,pdfplumber
BASE=Path('outputs/real_pdf_comparison_20261008')
FROZEN=BASE/'independent_numeric.json'; REPORT=BASE/'live_service/native_final/report.json'; OUT=BASE/'adjudication_numeric.json'
freeze_bytes=FROZEN.read_bytes(); freeze_hash=hashlib.sha256(freeze_bytes).hexdigest()
f=json.loads(freeze_bytes); raw=REPORT.read_bytes(); r=json.loads(raw)
items={x['id']:x for x in r['issues']}
ind={x['id']:x for x in f['checks']}
source=Path(f['summary']['source']); pdf=pdfplumber.open(source)

def match(page,pattern):
 vals=pdf.pages[page-1].search(pattern)
 return [{'page':page,'snippet':v['text'],'bbox':[v[k] for k in ['x0','top','x1','bottom']]} for v in vals]
def relation(check_id):
 a=ind[check_id]
 operands=a['rhs']; known=[x for x in operands if x['value'] is not None]
 total=sum((D(x['value']) for x in known),D('0'))
 left=D(a['lhs']['value'])
 return {'independent_check_id':check_id,'title':a['title'],'lhs':a['lhs'],'rhs':operands,'known_rhs_sum':str(total),'lhs_minus_known_rhs':str(left-total),'absolute_difference_wan':str(abs(left-total)),'actual_physical_pages':sorted(set(x['page'] for x in [a['lhs']]+operands if x.get('page') is not None)),'blank_operands':a['blank_operands'],'arithmetic_scope':'完整数字操作数' if not a['blank_operands'] else '仅已披露金额，空白保持null而未按0代入','status':'possible_display_rounding_within_0.5_wan_tolerance','financial_error':False}

adjudications=[]
rate=D('1.11')/(D('43.34')-D('1.11'))*100
adjudications.append({
 'system_id':'V33-245-4','rule_id':'V33-245','system_title':items['V33-245-4']['title'],'system_severity':'warn','classification':'false_positive_overstrong_semantic_judgment','numeric_claim_correct':True,'confirmed_financial_error':False,
 'reason':'原文说“基本持平”，有近似程度限定，而不是严格“持平/零变动”。2.63%的小幅增长与“基本持平”并不构成必然逻辑矛盾。系统直接宣称“两者不能同时成立”过强，违背优先降低误报原则。没有额外口径证据时至多作为表达精确性提示或待人工复核，不应列为已确认矛盾。',
 'calculation':{'current':'43.34','increase':'1.11','implied_prior':'42.23','growth_percent':str(rate),'printed_growth_percent':'2.63','note':'上年42.23由本年减增量反推，不是2024原件证据。算术增长率正确。'},
 'source_evidence':match(26,r'决算增加 1\.11 万元，增长 2\.63%')+match(27,r'公务用车购置及运行维护费支出基本持\n平。'),
 'location_adjudication':{'system_pages':[26],'actual_pages':[26,27],'classification':'cross_page_evidence_not_fully_located','reason':'增量与增长率印在26页，“基本持平”跨行印在27页。系统合成命中片段并把整段仅定位26页，未覆盖第二证据页。'},
 'recommendation':'撤销确定性的逻辑矛盾结论；如保留，降为manual_review/low表达提示，并按26、27页分别绑定原文。'
})
for sid,ids in [('V33-115-1',['N003']),('V33-120-2',['N062','N170','N171','N172']),('V33-120-3',['N060'])]:
 rels=[relation(z) for z in ids]
 adjudications.append({'system_id':sid,'rule_id':items[sid]['rule_id'],'system_title':items[sid]['title'],'system_severity':'info','classification':'arithmetically_correct_rounding_tip','numeric_claim_correct':True,'confirmed_financial_error':False,'confirmed_relationship_count':len(rels),'relations':rels,'reason':'原表复算与系统列示的总额、已填明细之和及0.01/0.02万元差额一致。这些差异在v3_3_portable的0.5万元容差内，称“可能为取整误差”正确；不能将提示计作真实材料错误。','location_adjudication':{'system_pages':[items[sid]['location']['page']],'actual_pages':sorted(set(p for a in rels for p in a['actual_physical_pages'])),'classification':'correct_start_page_but_continuation_needed' if sid=='V33-120-3' else 'correct_page' if sid=='V33-115-1' else 'partially_wrong_page','reason':'第7页数值全部可核对。' if sid=='V33-115-1' else '支出表的合计在10页、功能类包含11页续行。' if sid=='V33-120-3' else '第1个支出表项目支出关系在10—11页；其余3个一般公共预算表关系在14—15页（合计在15页）。系统把这4个关系全部标10页，后3个页码错误，也遗漏续页。'},'evidence_binding_adjudication':'系统片段是计算说明而非原文逐字引文，且bbox、fact_ids/evidence_ids与原件身份未完整绑定；数值真值与证据绑定质量应分别评价。','recommendation':'保留为info取整提示；逐关系附原始操作数、真实物理页、单元格bbox，不提示必须改数。'})

manual_review_adjudication=[
 {'system_code':'ambiguous_table_schema','classification':'parser_quality_signal_from_misclassified_narrative_page','confirmed_document_issue':False,'reason':'report把FIN01低置信度对象定位21页。原PDF21页是第三部分决算情况说明，真实收入支出决算总表位于7页，数值可独立读取并复算。该提示反映系统错误识别/归类，不证明原件总表有歧义。','recommended_action':'检查解析对象边界，将说明页与真实表对象分开；本轮只读，不改系统。'},
 {'system_code':'rule_evidence_incomplete','classification':'valid_system_evidence_quality_warning_with_imprecise_message','confirmed_document_issue':False,'reason':'4条记录均已有page字段，并非全部缺页码；但bbox为null、事实/证据ID为空、合成摘要未逐页绑定原件，且V33-245和V33-120-2确有跨页或错误定位。系统报告证据不完整有根据，“缺少页码等”表述应更具体。','recommended_action':'补齐来源SHA256、物理页、原文/单元格引文、bbox和证据ID；区分数值正确与定位失败。'},
 {'system_id':'FIN_01_income_expenditure_total:low_confidence','system_table_code':'FIN_01_income_expenditure_total','system_page':21,'system_confidence':0.63,'classification':'false_table_classification_or_location','confirmed_document_issue':False,'correct_table_page':7,'source_evidence':match(21,'第三部分')+match(21,'一、收入支出决算总体情况说明')+match(7,'收入支出决算总表'),'independent_table_detection':{'physical_page_21_grid_tables':len(pdf.pages[20].find_tables()),'physical_page_7_grid_tables':len(pdf.pages[6].find_tables()),'note':'结合原页渲染验证：21页为连续说明文字，7页为收入/支出四列表格。不能仅以格线检测断言所有PDF的表类型；此文件有视觉证据。'},'reason':'21页有说明章节与收支数字，但不具有该总表表头、行列结构；将其作为FIN01表让用户复核“表头和关键金额列”是错误对象/位置。','recommended_action':'把21页对象归为narrative说明，真实FIN01关联7页；不把该解析噪声计为文档错误。'},
 {'system_code':'OBL-AI-SEMANTIC-REVIEW','classification':'accurate_unfinished_workflow_status','confirmed_document_issue':False,'reason':'report明确本次语义模型未执行。这是系统流程覆盖状态，不能推导材料存在语义问题；独立人工复核已另行进行。','recommended_action':'保留流程状态；不要将其计入材料问题数。'}
]
misses=[{'independent_rule_id':a['rule_id'],'independent_title':a['title'],'classification':'confirmed_numeric_error_not_reported','system_issues_match':False,'system_tips_match':False,'system_candidate_findings_match':False,'source_issue':a,'reason':'report的1条warn与3条info以及candidate_findings（空）中，没有报告车辆预算完成率95.26%与43.34/67.30=64.40%的错误。'} for a in f['issues']]
result={
 'summary':{'system_records_reviewed':4,'system_substantive_warnings':1,'system_rounding_tips':3,'system_manual_review_records_reviewed':4,'false_positive_warnings':1,'arithmetically_correct_rounding_tips':3,'rounding_relations_confirmed':6,'confirmed_independent_numeric_error_not_reported':1,'false_table_location_review_records':1,'conclusion':'系统3条取整提示的6个算式数值正确，均为容差内提示；V33-245判断过强；21页FIN01将说明页归成总表；漏报1项明确车辆预算完成率错误。'},
 'source':{'pdf':str(source),'pdf_sha256':f['summary']['source_sha256'],'system_report':str(REPORT.resolve()),'system_report_file_sha256':hashlib.sha256(raw).hexdigest(),'independent_snapshot':str(FROZEN.resolve()),'independent_snapshot_sha256':freeze_hash,'independent_snapshot_preserved':True,'comparison_phase':'读取系统输出在独立结论冻结之后；独立原件结果未修改。'},
 'system_record_adjudications':adjudications,'manual_review_adjudications':manual_review_adjudication,'missed_numeric_findings':misses,
 'scope_limits':['本文件仅比较指定4条系统记录和4条manual_review记录；不以系统规则执行pass作为真值。','取整提示的算术值正确不等于已确认所有空白单元格为0；独立操作数中null保持null。','不修改原PDF、系统输出、原冻结JSON或源码。','没有扩大到独立结构/披露分线结论，也未读取其他agent结论。']}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
assert hashlib.sha256(FROZEN.read_bytes()).hexdigest()==freeze_hash
print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
print('ADJUDICATION',OUT.resolve())
print('FROZEN_SHA256_UNCHANGED',freeze_hash)
