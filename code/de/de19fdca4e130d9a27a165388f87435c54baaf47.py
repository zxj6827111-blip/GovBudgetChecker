"""Assemble the comparison from retained service responses and source audits."""
from pathlib import Path
from decimal import Decimal
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
SOURCE = Path('/Users/zhangxingjin/Desktop/决算测试上海市/上海市数据局.pdf')

def read(name):
    return json.loads((BASE / name).read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

service = read('live_service/service_receipt.json')
scenario = next(s for s in service['scenarios'] if s['scenario'] == 'native_final')
report = read('live_service/native_final/report.json')
numeric = read('independent_numeric.json')
adjudication = read('adjudication_numeric.json')
locations = {i['page']: i for i in read('independent_issue_locations.json')}
assert service['status'] == 'passed' and scenario['service_validation_passed']
assert sha(SOURCE) == scenario['source_sha256'] == numeric['summary']['source_sha256']
assert len(report['issues']) == 4
assert len(report['review_report']['issues']) == 1 and len(report['review_report']['tips']) == 3
assert numeric['summary']['confirmed_numeric_issues'] == 1
assert all(sha(ROOT / file) == fingerprint for file, fingerprint in service['code_fingerprints'].items())

issue = dict(numeric['issues'][0])
issue['comparison_status'] = 'false_negative'
issue['system_match'] = None
disclosure = {
    'rule_id': 'D-005', 'title': '一般公共预算总体差异原因表述不足', 'severity': 'medium',
    'document_type': '决算公开', 'year': 2025, 'organization': '上海市数据局',
    'page': 22, 'bbox': locations[22]['bbox'],
    'table_or_section': '五、一般公共预算财政拨款支出决算情况说明（三）',
    'snippet': locations[22]['phrase'],
    'expected': '对总体预算与决算差额给出业务或执行原因',
    'actual': '以“决算数与预算数基本持平”作为原因，仅描述差异程度',
    'difference': '预算234430.42万元减决算233564.77万元为865.65万元',
    'reason': '总体原因语句是结果描述；后续分项虽有部分原因，仍宜归纳总体差额来源。属于披露质量问题，不认定其“基本持平”描述本身构成数字矛盾。',
    'suggestion': '结合人员招聘、项目结转、执行调整等实际情况说明总体差额，不能照抄建议为既成事实。',
    'confidence': 0.9, 'comparison_status': 'disclosure_quality_not_reported', 'system_match': None,
}
numbering = {
    'rule_id': 'FORMAT-SEQ', 'title': '功能分类支出说明序号跳号', 'severity': 'low',
    'document_type': '决算公开', 'year': 2025, 'organization': '上海市数据局',
    'page': 25, 'bbox': locations[25]['bbox'],
    'table_or_section': '五、一般公共预算财政拨款支出决算情况说明（三）',
    'snippet': '第24—25页第12项后接第14项，随后15、16，未见第13号',
    'expected': '已有15项说明采用连续序号1至15', 'actual': '1至12、14至16',
    'difference': '跳过一个序号',
    'reason': '科目说明仍可与表中15个项级科目对应，不能因跳号报缺失科目或金额漏披露。',
    'suggestion': '核实后将原14、15、16调整为13、14、15。',
    'confidence': 0.98, 'comparison_status': 'format_not_reported', 'system_match': None,
}
comparison = {
    'summary': {
        'date': '2026-10-08', 'document_type': '决算公开', 'year': 2025, 'organization': '上海市数据局',
        'source': str(SOURCE), 'source_sha256': sha(SOURCE), 'physical_pages': 31,
        'published_date': None, 'source_url': None,
        'overall_assessment': '当前原生PDF导入与跨页重建在本样本通过；规则准确性对照未通过，存在明确漏报、过强语义告警及错误结构提示。',
        'risk_flags': ['空白金额语义受限', '原始同比基数未提供', '部分系统证据跨页或绑定不完整'],
        'system_records': 4, 'system_warning_records': 1, 'system_rounding_tips': 3,
        'confirmed_numeric_errors_found_independently': 1, 'confirmed_numeric_errors_detected_by_system': 0,
        'confirmed_numeric_false_negatives': 1, 'disclosure_quality_unreported': 1, 'format_unreported': 1,
        'overstrong_semantic_alerts': 1, 'incorrect_structural_review_targets': 1,
        'accuracy_rate': None, 'accuracy_rate_note': '单份材料且样本来自开发材料集，不估计总体准确率、误报率或召回率。',
    },
    'method': {
        'service': '当前代码在隔离 API、独立 Worker、PostgreSQL 中实际上传原件并执行本地规则，未启用AI；未改变用户日常运行环境。',
        'independent_review': numeric['independence'],
        'original_pdf_unchanged': True, 'application_code_unchanged_during_check': True,
        'page_policy': '均为从封面起计的物理PDF页码；未发现印刷页码，不推造目录页码。',
        'tolerance': {'amount_wan': '0.5', 'percentage_points': '0.1'},
    },
    'service_evidence': {
        'job_id': report['job_id'], 'terminal_status': scenario['terminal_status'],
        'analysis_elapsed_seconds': scenario['pipeline_budget']['elapsed_seconds'],
        'scenario_elapsed_seconds': scenario['elapsed_seconds'],
        'source_facts': scenario['database']['run_facts_count'],
        'artifact_readback': scenario['database']['artifact_readback'],
        'export_agreement': scenario['export_agreement'],
        'repeated_downloads_identical': scenario['repeated_downloads_identical'],
        'cleanup': service['cleanup'],
    },
    'independent_numeric_summary': numeric['summary'],
    'issues': [issue, disclosure, numbering],
    'system_record_adjudication': [
        {'system_id': 'V33-245-4', 'result': 'overstrong_semantic_alert',
         'reason': '增长2.63%与“基本持平”可分别表达精确数值和定性程度；不能将“基本持平”机械等同于严格零变动。',
         'page_binding_problem': '增加金额与比例在26页，基本持平在27页，系统只给26页。'},
        {'system_id': 'V33-115-1', 'result': 'valid_rounding_tip', 'numeric_difference': '0.01万元', 'pages': [7]},
        {'system_id': 'V33-120-2', 'result': 'valid_rounding_tip_with_incorrect_page_binding',
         'numeric_difference': '4个关系各差0.01万元', 'correct_pages': [10, 11, 14, 15],
         'page_binding_problem': '3个一般公共预算表关系在14—15页，不能统一归到10页。'},
        {'system_id': 'V33-120-3', 'result': 'valid_rounding_tip', 'numeric_difference': '0.02万元', 'pages': [10, 11]},
    ],
    'system_structural_review_adjudication': {
        'table_code': 'FIN_01_income_expenditure_total', 'system_page': 21, 'actual_table_page': 7,
        'result': 'incorrect_structural_review_target',
        'reason': '21页为第三部分决算说明，无收支总表；真正总表7页、原生线框识别置信度0.99。二次识别将UNKNOWN_P21_T0按标题词重新判为FIN01，置信度0.63。',
        'diagnostic': 'secondary_recognition_diagnostic.json',
    },
    'system_gaps': [
        {'id': 'SYS-RATE-TABLE-BINDING', 'priority': 'high',
         'finding': '表中预算及决算事实正确存在，但完成率规则只寻找正文分母；无法绑定时直接返回空，检查记录仍pass。',
         'diagnostic': 'completion_rule_diagnostic.json',
         'suggestion': '按同主体、同年度、同分项和预算阶段绑定表格分母；有完成率声明却未执行比较时记录未完成。'},
        {'id': 'SYS-QUALIFIED-FLAT', 'priority': 'medium',
         'finding': '方向词处理未区分严格持平和基本持平。',
         'suggestion': '保留定性修饰语，证据不足时待复核；不要设定未经用户规则支持的统一百分比阈值。'},
        {'id': 'SYS-NARRATIVE-SECOND-RECOGNITION', 'priority': 'medium',
         'finding': '正文伪表被二次识别成核心表，生成错误低置信度提示。',
         'suggestion': '二次识别应尊重正文身份和实际表格证据；同表代码不可让弱候选覆盖原生确证表。'},
        {'id': 'SYS-ISSUE-EVIDENCE', 'priority': 'medium',
         'finding': '4条输出均缺完整来源绑定；跨表、跨页计算摘要被当作单页原文片段，部分页码错误。',
         'suggestion': '分别绑定每个操作数及各页原始片段与坐标；计算摘要保留在公式或说明字段。'},
    ],
    'manual_review': numeric['manual_review'] + [
        {'title': '泛化原因说明的充分程度', 'severity': 'manual_review', 'pages': [23, 25, 27],
         'reason': '“根据实际情况，经费支出减少”“预算按需实施支出”等缺少业务细节，建议补充；不把所有泛化表述一律计为确定错误。'},
    ],
    'text_style_suggestions': [
        {'page': 27, 'snippet': '国内公务接待支出1.90万',
         'suggestion': '将“万”写完整为“万元”。',
         'adjudication': '当前段落及对应表均已明确万元，未确认数量级或金额口径错误；列写法建议，不计主要漏报。'},
    ],
    'next_actions': [
        '修复表格预算与文字完成率的绑定及跳过比较仍pass问题，用本样本保留修复前后对照。',
        '修复定性持平告警、正文二次识别及跨页证据绑定，再用另外真实材料扩展对照。',
        '原文将车辆完成率复核为64.40%，完善总体差异原因，修正连续编号；原件目前未修改。',
    ],
    'artifacts': {},
}
for name in ['independent_numeric.json', 'adjudication_numeric.json', 'independent_root.json',
             'independent_arithmetic.json', 'completion_rule_diagnostic.json',
             'secondary_recognition_diagnostic.json', 'live_service/native_final/report.json',
             'live_service/service_receipt.json']:
    comparison['artifacts'][name] = sha(BASE / name)
for name in ['independent_structure.json', 'independent_narrative.json']:
    if (BASE / name).exists():
        comparison['artifacts'][name] = sha(BASE / name)
        comparison.setdefault('independent_additional_reviews', {})[name] = read(name)
(BASE / 'comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2))
print(json.dumps(comparison['summary'], ensure_ascii=False, indent=2))
