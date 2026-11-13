"""汇总本轮实际原页、公开入口、数据库及最终API报告，不改生产代码。"""
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
import csv
import hashlib
import json
from pathlib import Path
import re

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def read(path):return json.loads(path.read_text())
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def compact(value):return re.sub(r'\s+','',str(value or ''))
def owner(doc):return re.split('2024',compact(doc['cover_text']))[0].lstrip('0123456789')
def signature(issue):return (issue.get('rule_id',issue.get('rule')),re.sub(r'^[一二三四五六七八九十百]+、','',issue['message']))


inventory=read(BASE/'inventory.json')
source_seal=read(BASE/'acceptance_source_seal.json')
seal_drift=[path for path,digest in source_seal['sha256'].items() if sha(BASE/path)!=digest]
assert not seal_drift,seal_drift
code=read(BASE/'code_before.json')
code_drift=[name for name,digest in code.items() if sha(ROOT/name)!=digest]
assert not code_drift,code_drift
assert all(row['matches'] for row in read(BASE/'v25_identity.json').values())
original_drift=[doc['source_pdf'] for doc in inventory if sha(Path(doc['source_pdf']))!=doc['sha256'] or sha(BASE/'documents'/doc['doc_id']/'source.pdf')!=doc['sha256']]
assert not original_drift
previous=read(ROOT/'outputs/v25_reacceptance_20261016/input_verification.json')
previous_drift=[row['path'] for row in previous if sha(Path(row['path']))!=row['sha256']]
assert not previous_drift
default_audit=read(ROOT/'outputs/v25_reacceptance_20261016/default_audit_before_tests.json')
audit_unchanged=sha(Path(default_audit['path']))==default_audit['sha256']
protection=dict(production_code_drift=code_drift,source_seal_drift=seal_drift,new_original_pdf_drift=original_drift,
                previous25_pdf_drift=previous_drift,default_audit_matches_prior_snapshot=audit_unchanged,
                dependencies_modified=False,production_code_modified=False,production_deployed=False,existing_services_restarted=False)
write(BASE/'protection_verification.json',protection)

identity_audits={row['truth_id']:row for row in read(BASE/'source_identity_audit.json')['records']}
all_rows=[]
per_doc=[]
unresolved=[]
fields=['amount_cell_count','identified_fiscal_amount_cell_count','unclassified_numeric_projection_count',
        'unresolved_money_cell_count','fact_missing_count','rule_admitted_count','rule_blocked_count']
receipt_totals=Counter()
for doc in inventory:
    rows=read(BASE/'public/native_identity_comparison'/f"{doc['doc_id']}.json")
    all_rows.extend(rows)
    receipt=read(BASE/'documents'/doc['doc_id']/'receipt.json')
    receipt_totals.update({field:receipt[field] for field in fields})
    for value in receipt['reading_quality']['unbound_money_cells']:
        unresolved.append(dict(doc_id=doc['doc_id'],filename=doc['filename'],**value))
    bad=[r for r in rows if r['fact_status']!='bound_exact']
    per_doc.append(dict(doc_id=doc['doc_id'],filename=doc['filename'],organization=owner(doc),year=2024,pages=doc['pages'],
        source_amounts=len(rows),read_exact=sum(r['reading_status']=='read_exact' for r in rows),
        value_and_identity_exact=sum(r['fact_status']=='bound_exact' for r in rows),identity_mismatches=len(bad),
        blocked=sum(bool(r['fact_admission_rejection']) for r in rows),
        correct_and_admitted=sum(r['fact_status']=='bound_exact' and not r['fact_admission_rejection'] for r in rows),
        previously_recorded_source=doc['overlap_with_recorded_development_sources'],
        broader_numeric_candidates=receipt['amount_cell_count'],unresolved_receipt_candidates=receipt['unresolved_money_cell_count'],
        core_amount_binding_verdict='not_passed' if bad else 'passed'))
bad=[r for r in all_rows if r['fact_status']!='bound_exact']
assert len(all_rows)==4235 and len(bad)==31
assert all(r['reading_status']=='read_exact' for r in all_rows)
system_rows=[]
for row in bad:
    expected=row['expected'];actual=row['actual_fact']
    group='SPACED_TOTAL' if expected.get('subject_code')=='total' and actual['subject_code'] is None else (
        'CROSS_ROW_SUBJECT_NAME' if row['doc_id']=='SH2024N9_06' else 'PREVIOUS_ROW_LABEL_RESIDUE')
    system_rows.append(dict(group=group,**row,source_identity_proof=identity_audits[row['truth_id']]))
counts=Counter(row['group'] for row in system_rows)
assert counts=={'SPACED_TOTAL':16,'CROSS_ROW_SUBJECT_NAME':14,'PREVIOUS_ROW_LABEL_RESIDUE':1}
write(BASE/'system_cell_failures.json',system_rows)
write(BASE/'retained_unresolved_candidates.json',unresolved)
write(BASE/'receipt_totals.json',dict(receipt_totals))
with (BASE/'逐份验收结果.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(per_doc[0]));writer.writeheader();writer.writerows(per_doc)
with (BASE/'系统识别问题31格.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    columns=['truth_id','group','doc_id','page','raw_text','expected_subject_code','expected_subject_name',
             'actual_subject_code','actual_subject_name','indicator','admission_rejection','source_cell_id','source_bbox']
    writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
    for row in system_rows:
        writer.writerow(dict(truth_id=row['truth_id'],group=row['group'],doc_id=row['doc_id'],page=row['page'],raw_text=row['raw_text'],
            expected_subject_code=row['expected']['subject_code'],expected_subject_name=row['expected']['subject_name'],
            actual_subject_code=row['actual_fact']['subject_code'],actual_subject_name=row['actual_fact']['subject_name'],
            indicator=row['expected']['indicator'],admission_rejection=row['fact_admission_rejection'],source_cell_id=row['fact_source_cell_id'],
            source_bbox=json.dumps(row['independent_source']['bbox'])))

by_hash={doc['sha256']:doc for doc in inventory}
api_reports={}
contexts={}
for path in (BASE/'service').glob('*/report.json'):
    digest=sha(path.parent/'source.pdf')
    if digest in by_hash:
        doc_id=by_hash[digest]['doc_id'];api_reports[doc_id]=read(path);contexts[doc_id]=read(path.parent/'fact_context.json')
assert len(api_reports)==9
profile_records=[]
for doc in inventory:
    doc_id=doc['doc_id'];folder=BASE/'documents'/doc_id
    public=read(folder/'facts.json');stored=read(BASE/'service_database'/doc_id/'facts.json')
    current=contexts[doc_id]
    public_by_uid={f['fact_uid']:f for f in public}
    runtime_differences=[]
    for fact in current['facts']:
        previous=public_by_uid[fact['fact_uid']]
        for field in ['raw_value','subject_code','subject_name','indicator','budget_stage','table_code','org_name']:
            if fact[field]!=previous[field]:runtime_differences.append(dict(fact_uid=fact['fact_uid'],field=field,public=previous[field],runtime=fact[field]))
    profile_records.append(dict(doc_id=doc_id,source_cover_owner=owner(doc),source_cover_text=doc['cover_text'],
        api_profile_owner=api_reports[doc_id]['document_profile']['organization'],profile_status=api_reports[doc_id]['profile_status'],
        api_runtime_context_owner=current.get('org_name'),runtime_fact_owners=sorted({str(f['org_name']) for f in current['facts']}),
        public_fact_owners=sorted({str(f['org_name']) for f in public}),database_fact_owners=sorted({str(f['org_name']) for f in stored}),
        public_runtime_field_differences=runtime_differences,facts=len(public)))
assert not any(row['public_runtime_field_differences'] for row in profile_records)
missing_owners=[row for row in profile_records if row['api_profile_owner'] is None]
assert len(missing_owners)==2 and sum(row['facts'] for row in missing_owners)==409
write(BASE/'profile_identity_verification.json',dict(status='inconsistent',affected_documents=2,affected_facts=409,
    source_owner_missing=False,public_matches_actual_runtime=True,database_owner_values_match_source_cover=True,
    root_cause_evidence='document_profile_resolver.py封面机构后缀列表不含通用室；structured_ingest_runner._normalize_org_name在元数据空时按文件名回退，实际API画像/规则事实为空而DB已有名称。',records=profile_records))

reviews=read(BASE/'engine_candidate_review.json')
decisions={signature(row):row['decision'] for row in reviews}
api_issues=[]
coverage=Counter();coverage_reasons=Counter();statuses=Counter()
for doc_id,report in api_reports.items():
    statuses[report['status']]+=1
    values=report['check_coverage']
    coverage.update({field:values[field] for field in ['applicable_total','completed_total','unresolved_total','blocking_total']})
    coverage_reasons.update(values['by_reason'])
    for issue in report['issues']:
        if issue['severity']=='info':continue
        decision=decisions.get(signature(issue),'unreviewed')
        api_issues.append(dict(doc_id=doc_id,decision=decision,rule_id=issue['rule_id'],message=issue['message'],location=issue.get('location')))
api_decisions=Counter(row['decision'] for row in api_issues)
assert api_decisions=={'confirmed_source_issue':14,'false_positive':1,'manual_review':1},api_decisions
write(BASE/'api_candidate_and_coverage_verification.json',dict(issues=len(api_issues),candidate_decisions=dict(api_decisions),
    public_rule_layer_decisions=dict(Counter(row['decision'] for row in reviews)),terminal_statuses=dict(statuses),
    business_checks=dict(coverage),unfinished_by_reason=dict(coverage_reasons),issue_review=api_issues,
    note='done是流程完成，不等于没有错误；虹桥报告done仍列出两个真实原件问题。国防空表和接待次数候选在最终证据门禁中未展示。'))

math=[]
for name,numerator,denominator,expected in [
    ('参事室一般公同比','73.25','1641.81','4.46'),('参事室车辆完成率','3.68','7.00','52.57'),
    ('参事室车辆降幅','2.34','6.02','38.87'),('参事室机关运行同比','54.96','200.31','27.44'),
    ('应急车辆完成率','103.17','128.90','80.04'),('虹桥一般公完成率','7066.32','8418.98','83.93'),
    ('金融办出国占比边界','63.24','110.06','57.46')]:
    exact=Decimal(numerator)/Decimal(denominator)*100
    rounded=exact.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    assert str(rounded)==expected
    math.append(dict(check=name,numerator=numerator,denominator=denominator,exact_percent=str(exact),display_percent=str(rounded)))
write(BASE/'independent_narrative_math.json',dict(reads_tested_facts=False,checks=math))
source_arithmetic=read(BASE/'source_arithmetic_summary.json')
business=read(BASE/'business_review_summary.json')
service=read(BASE/'service_readback_verification.json')
assert service['source_comparison']['facts']=={'bound_exact':4204,'semantic_mismatch':31}
assert service['field_difference_types']=={'org_name':409}
cleanup=read(BASE/'cleanup_independent_check.json')
assert cleanup['container_absent'] and cleanup['api_port_closed'] and cleanup['temporary_workspace_absent']

continuation=[]
for doc in inventory:
    for region in read(BASE/'documents'/doc['doc_id']/'table_coverage.json'):
        if not region['recognized']:continuation.append(dict(doc_id=doc['doc_id'],**region))
assert len(continuation)==1
write(BASE/'unclassified_core_continuation.json',dict(regions=continuation,
    source_page_has_amount=False,source_page_has_code_31099=True,
    decision='国防第11页基本支出表两行尾部仍被读为UNKNOWN，31099编码进入未映射数字候选；不能将其解释为漏读31099万元。'))
capital=[row for doc in inventory for row in read(BASE/'documents'/doc['doc_id']/'preparse_adjudicated_truth.json')
         if row['table_code']=='FIN_09_state_capital' and Decimal(row['raw_text'].replace(',',''))!=0]
summary=dict(recorded_at=datetime.now(timezone.utc).isoformat(),verdict='新9份验收未通过：金额逐字通过，科目/合计身份、画像与存储单位仍有缺口',
    parser_fingerprint='pdfplumber-cells-v25-native-tail-and-dimension-identity',
    scope=dict(new_files=9,pages=238,source_core_types=80,source_physical_regions=110,recognized_core_regions=109,
               source_amounts=4235,integer_amounts=9,previously_recorded_sources=1,not_recorded_same_hash_sources=8,
               all_unknown_material_blind_test=False,independent_human_full_truth=False,original2024_pdf_count=34,old25_unchanged=True,excluded2025_pdfs=20),
    reading=dict(exact=4235,missing=0,wrong_literal=0,facts_present=4235),
    identities=dict(exact=4204,mismatched=31,groups=dict(counts),correct_and_admitted=4204,all_admitted=4219,
                    mismatched_but_admitted=15,blocked_low_confidence=16,failed_binding_documents=4,
                    core_binding_only_passed_documents=5),
    metadata=dict(profile_owner_unrecognized_documents=2,runtime_database_org_different_facts=409,
                  overlap_with_amount_cell_defects_not_added_to_cell_denominator=True),
    receipt_totals=dict(receipt_totals),public_rule_candidates=dict(Counter(row['decision'] for row in reviews)),
    final_api_candidates=dict(api_decisions),api_business_checks=dict(coverage),api_unfinished_by_reason=dict(coverage_reasons),
    api_terminal_statuses=dict(statuses),original_issues=business,source_arithmetic=source_arithmetic,
    service=service,protection=protection,per_document=per_doc,
    testing=dict(fresh_public_pdf_and_fact_entry=True,fresh_native_source_character_checks=4235,
                 fresh_service_scenarios=10,fresh_original_service_pdfs=9,full_pytest_rerun=False,
                 reason='本轮只读新样本验收、未修改生产代码；不沿用上轮pytest数字称本轮已重跑。'),
    source_truth_process=dict(before_tested_parse=True,source_only_money_and_identity_adjudication=True,
        initial_grid_only_evidence_preserved=True,integer_columns_and_compact_income_header_verified=True,
        multiple_tables_one_page_refined_by_native_title_coordinates=True),
    limitations=['只有核心原页金额及已保存候选的来源核对，不宣称独立人工逐页完整真值或所有业务规则100%覆盖。',
                 '1份虹桥报告与既有开发记录同哈希；其余8份未发现已记录同哈希，不排除未记录或近似材料。',
                 '宽口径4239数字候选保留，其中20项未绑定/不准入；16项是已识别核心合计，另4项为范围外候选，不能把4项全称漏读金额。',
                 '国防原件为8种核心表、部分表格本身空白；不把空白当零或臆测保密/未披露的具体原因。',
                 '单位口径差异已在API画像/规则快照与数据库实际回读中复现，不是只归咎验收脚本；初步假设及失败回执均保留。',
                 '未修改原PDF、生产代码或既有服务；临时栈已清理。'],
    next_action='现有9份已足够修复；先处理31格身份、2份单位口径和最终API误报，再复验旧25份/旧19格/缺标签3格。暂不需更多同版式样本。',
    future_sample_gap=dict(nonzero_state_capital_amounts=len(capital),suggestion='修复后再补真实非零国有资本经营预算和不同地区版式；扫描件另组验收。'))
write(BASE/'final_summary.json',summary)

lines=[
    '上海2024新增9份材料详细验收（v25，2026-10-16）',
    '结论：本批尚未通过。金额读取全部正确，但31格科目/合计身份仍有问题，另有单位画像与入库口径差异、最终API误报。',
    '',
    '一、输入与独立来源',
    '2024目录34份，旧25份哈希未变，本轮检查新增9份238页。2025目录20份没有纳入本轮。全部封面为2024年度决算，文件名不能代替原件单位名称。',
    '其中金融监督管理局文件的封面单位为中共上海市委金融委员会办公室；“海市人民政府合作交流办公室.pdf”封面单位含完整“上海市”。',
    '虹桥管委会1份已在开发证据中出现，单独标记回归；8份未见已记录同哈希，不能把全部9份宣称严格未知材料盲测。',
    '在调用被测解析器前封存原PDF、PyMuPDF原页/网格/词、4,235格真值，再逐格核对原生字符序号与bbox；金额及科目来源裁定不读取系统facts。',
    '验收口径覆盖9个整数金额；国防收入表只明示财政拨款收入，按原表头检查，不套7列标准收入。虹桥同页两张空表按标题坐标分别归属。初始网格草稿与来源裁定均保留。',
    '',
    '二、金额与身份结果',
    '来源4,235格：逐字金额4,235正确、0缺失、0错字；4,235条财政事实均已形成。金额/科目/列及准入同时正确4,204格。',
    '31格身份问题按原字符和物理格定位确认；16格被low_confidence挡住，15格错误身份仍被准入。这不是31个金额读错。',
    '研究室6格：第6页收入合计3格、第8页支出合计3格；“合 计”没有归一为合计身份，subject_code为空。',
    '应急管理局21格：第8页收入合计7格同上；第9页22499与2249999两行共14格科目名缺尾或混入前行字符。',
    '合作交流办公室3格：第13页一般公共预算合计三列，“合 计”身份未归一。',
    '参事室1格：第12页财政拨款总表收入总计1715.06，科目被前行残余“款”污染成“款\n总计”。',
    '另外，国防第11页基本支出两行尾部被识别为UNKNOWN，原编码31099进入未映射数字候选；原页该区域金额为空，不是漏了31099万元。核心物理区域匹配109/110。',
    f"宽口径回执保留{receipt_totals['amount_cell_count']}个数字候选、{receipt_totals['unresolved_money_cell_count']}个未完成项：16个上述合计身份+4个范围外候选。原回执没有为清零而删分母。",
    '',
    '三、逐份核心金额绑定（不等同于全文业务审校通过）',
]
for row in per_doc:
    lines.append(f"{row['organization']}：{row['pages']}页，金额{row['read_exact']}/{row['source_amounts']}正确，身份正确{row['value_and_identity_exact']}，身份异常{row['identity_mismatches']}，受阻{row['blocked']}；{row['core_amount_binding_verdict']}。")
lines.extend([
    '',
    '四、真实服务和单位口径',
    '9份原件+1合成预算控制，共10个隔离API/Worker/Postgres场景完成。实际DB回读4235事实及完整字符/格身份/金额/页码/bbox证据，再对照冻结原页，复现相同31格。',
    '公共金额/科目/证据和存储往返一致，但不能称所有字段全一致：参事室206条、研究室203条，共409条org_name字段在规则事实为null、DB为原件单位名称。API最终画像单位也为null。',
    '已确认封面单位明示，名称以“室”结尾；画像封面后缀列表未覆盖通用“室”，存储structured_ingest_runner._normalize_org_name在元数据空时按文件名回退。该409是元数据字段差异，与31金额格有重叠，不相加。修复应以原封面身份贯通各层，不能用文件名猜值掩盖画像缺口。',
    '初步曾怀疑旧验收脚本封面取字方式不同；完整原页文本重核仍为空，假设被否定。强制assert回读一致的两次失败、原始脚本和修正说明全部保留；没有把公开facts改成DB值来掩盖差异。',
    f"最终API状态：{dict(statuses)}。done是处理流程完成，虹桥done仍带2项真实原件问题，不能解释为审核无误。",
    f"实际业务检查：适用{coverage['applicable_total']}，完成{coverage['completed_total']}，未完成{coverage['unresolved_total']}，其中阻断{coverage['blocking_total']}。原因{dict(coverage_reasons)}；全部保留分母。",
    '临时Docker、API/Worker进程、端口及目录已独立检查清理；没有重启、部署用户既有服务。',
    '',
    '五、规则误报、原件问题和待复核',
    '规则层23条非info候选逐项裁定：14条对应原件问题、3条误报、6条待复核。最终API仅展示其中16条：14条原件问题、1条明确误报、1条容差边界待复核。',
    '最终仍在的误报：研究室第20页三公预算29.98、决算18.69被串到前段购房补贴，第21页明示团组减少原因仍被报缺原因。',
    '另外两条仅规则层误报已被API最终门禁过滤：参事室第24页已有“18次、114人次”却报缺批次人数；国防将社会保障分项672.47当成总额而报与1622.36不符。不能夸大成最终API三条误报。',
    '金融办出国占比57.56%与63.24/110.06复算57.46%相差显示值0.10个百分点，精确差约0.10043，位于默认容差/舍入边界；保留人工复核，不直接计作确定差错。',
    '原件独立确认15项问题（6 high、3 medium、6 low），含单位漏字、完成率/同比不符、表文差异、科目名称和重复文字。详细页码、原字符bbox、片段、实际/期望及建议见原件问题清单.csv和confirmed_source_issues.json。',
    '例如：参事室年初预算48元/决算43.35万元；车辆完成率96.58%应按表复算52.57%；应急车辆完成率93.12%按表应80.04%；金融办事业运行原文2213.08与财政表2207.61相差5.47万元。原件问题不能当解析器缺陷处理。',
    '国防空表和国资表未见说明的适用范围仍待核实，不能把空白补零或默认无支出。参事室全年车辆2辆与年末资产0辆另列口径/时点复核，不直接判矛盾。',
    '原页表内/表间独立勾稽2035项：1748精确、65在0.5万元容差内、222取数不足，未发现超金额容差的确定表内差额；这些数字不代表所有业务规则均覆盖。',
    '',
    '六、当前结论与下一步',
    'v25解决了上一批已知金额问题，但新结构仍暴露合计归一化、相邻行科目文本归属、空白续页角色和单位画像/存储一致性缺口。本批不能验收为全部正常。',
    '这9份已足够继续修复，暂不需要更多同版式报告。优先先保存本轮失败真值和31格来源证明，再改通用逻辑；不得按部门名/金额差额/科目字典特判。',
    '修复后重跑本批4235格、最终API误报与409单位差异，并保留旧25份/20491格、v23旧19格和3格原件缺标签记录。',
    '本轮未改生产源码、未改依赖、未安装模型、未降低OCR门槛；原件全部保留。本轮未重新运行完整pytest，不把上轮3542/163/3当成本轮重跑成绩。',
    '未来泛化样本仍优先真实非零国有资本经营预算、其他地区不同版式；本批该项非零来源金额仍为0。扫描件单列OCR组。',
    '',
    '证据索引',
    'final_summary.json；逐份验收结果.csv；系统识别问题31格.csv；system_cell_failures.json；profile_identity_verification.json。',
    'acceptance_source_seal.json、source_freeze.json、native_amount_source_audit.json、source_identity_audit.json、source_truth_overlay.json：先冻结原页及字符真值。',
    'public/native_identity_comparison、database_public/native_identity_comparison：公开入口与真实DB分别对原字符身份比较。',
    'service_readback_verification.json、service_database/、service/service_receipt.json、cleanup_independent_check.json：实际服务、DB回读、差异及清理。',
    'engine_candidate_review.json、api_candidate_and_coverage_verification.json、原件问题清单.csv、confirmed_source_issues.json、images/：规则、原件及完整页证据。',
    '初始网格/标签草稿、两次metadata assert失败与无效取字假设日志全部保留，最终以本报告和根目录final_summary.json为准。',
])
(BASE/'详细验收报告.txt').write_text('\n'.join(lines)+'\n')
manifest={str(path.relative_to(BASE)):sha(path) for path in BASE.rglob('*') if path.is_file() and not path.is_symlink()
          and path.name!='evidence_manifest.json' and '__pycache__' not in str(path)}
write(BASE/'evidence_manifest.json',dict(files=len(manifest),sha256=manifest))
print(json.dumps(dict(verdict=summary['verdict'],identities=summary['identities'],api_decisions=dict(api_decisions),business_checks=dict(coverage)),ensure_ascii=False))
