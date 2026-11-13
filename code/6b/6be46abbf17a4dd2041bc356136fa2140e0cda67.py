"""只封印本轮脱敏复核回执；不收集运行配置、凭据、原件或数据库。"""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import re
import subprocess

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_r2_20261106')
CAND = Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
BASE = Path('/Volumes/workSSD/software development/GovBudgetChecker')
EV = CAND / 'outputs/unified_review_remediation_20261106'
PRIOR = BASE / 'outputs/unified_independent_review_20261106'
HEAD = '69884e9c1d7fee91e530b952b7e5b1935802e987'
MAIN = '47581360966191d180880f7fb07dab48974948bb'

def read(name):
    return json.loads((ROOT / name).read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(name, obj):
    (ROOT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

def git(*args):
    return subprocess.check_output(['git', '-C', str(CAND), *args], text=True).strip()

identity = read('receipts/source_and_collection_identity.json')
effect = read('receipts/effect_boundary_controls.json')
narrative = read('receipts/narrative_boundary_controls.json')
scope = read('receipts/scope_semantic_rebase_control.json')
live = read('live_receipts_2/independent_live_report.json')
browser = read('live_receipts_2/independent_browser_report.json')
cleanup = read('live_receipts_2/independent_cleanup.json')
r05 = read('receipts/r05_eight_independent_verifications.json')

gate_names = [
    ('old27', 'old27_independent_replay.json'),
    ('old_cover', 'old_cover_independent_replay.json'),
    ('current_unadjudicated', 'current_unadjudicated_independent_replay.json'),
    ('current_with_only_r05', 'current_with_only_r05_evidence.json'),
]
gates = []
for label, name in gate_names:
    data = read('receipts/' + name)
    groups = sorted(set((s.split(':', 1)[0], s.split(':', 1)[1].strip().split(' ', 1)[0])
                        for s in data['pending']))
    gates.append({'case': label, 'overall': data['overall'], 'samples': data['samples'],
                  'refusals': len(data['problems']), 'pending_objects': len(data['pending']),
                  'sample_layer_groups': len(groups),
                  'pending_groups': [{'sample_id': s, 'layer': l} for s, l in groups],
                  'receipt': 'receipts/' + name})
write('receipts/gate_summary.json', {'policy': 'real-painpoint-effect-v4', 'gates': gates,
      'refusal_counts_are_not_software_defect_counts': True,
      'scope31_self_pass_is_independent_business_acceptance': False,
      'original_nine_semantic_groups_independently_closed': False})

findings = [
    {'id': 'F01', 'priority': 'P1', 'remediation': 'R-01',
     'title': '逗号/冒号前限定被删除，部分非零矛盾未阻止叙述零',
     'source': 'src/engine/narrative_zero_evidence.py', 'lines': [19, 61, 97],
     'controls': ['negation_before_colon', 'conditional_before_comma', 'negated_before_comma',
                  'positive_operation_conflict', 'positive_dual_total_conflict'],
     'observed': '五个独立负控均错误返回决算零出处；预算未纳入本项授权。'},
    {'id': 'F02', 'priority': 'P1', 'remediation': 'R-02',
     'title': 'reference_kind变化可缩减有效引用集合而不要求授权',
     'source': 'scripts/effect_acceptance_v3.py', 'lines': [74, 355],
     'controls': ['reference_group_kind_changed_without_authorization'],
     'observed': 'source_diagnostic_group改为check后有效引用由两条缩为一条，未提供决定仍PASS。'},
    {'id': 'F03', 'priority': 'P1', 'remediation': 'R-03',
     'title': '原格负号丢失后错误正金额仍能通过独立复算',
     'source': 'scripts/effect_fact_sources.py', 'lines': [48, 52],
     'controls': ['true_quote_wrong_signed_value', 'negative_amounts_legal_positive'],
     'observed': '原格-6.00被宣称为+6.00仍PASS；合法全负数加总反而FAIL。至少须拒绝不支持的带符号值。'},
    {'id': 'F04', 'priority': 'P1', 'remediation': 'R-03',
     'title': '从万元截取元作为单位可使事实缩小一万倍仍PASS',
     'source': 'scripts/effect_fact_sources.py', 'lines': [44, 53],
     'controls': ['unit_subtoken_yuan_from_original_wan'],
     'observed': '原件完整单位万元，单位定位只选尾字元，原格100000/60000/40000被按元换算为10/6/4万元。'},
    {'id': 'F05', 'priority': 'P1', 'remediation': 'R-03',
     'title': '重复操作数可替代必需分项而复算通过',
     'source': 'scripts/effect_acceptance_v3.py', 'lines': [218, 251],
     'controls': ['duplicate_operand_omits_required_project'],
     'observed': '原件及独立事实为总12/基本6/项目4，operand改为总/基本/基本，required仍含项目，得到12=6+6并PASS。'},
    {'id': 'F06', 'priority': 'P2', 'remediation': 'R-04',
     'title': '旧范围批准被重新绑定到已变化的适用性理由',
     'source': 'scripts/verify_pending_review.py', 'lines': [1117, 1141],
     'controls': ['changed_applicability_reason'],
     'observed': '同材料/关系的未披露变成已披露但提取失败，工具把旧explanation_only写到新case_id并消除未知。'},
    {'id': 'F07', 'priority': 'P2', 'remediation': 'R-02',
     'title': '展示ID交换和正文加证据的精确合法授权被误拒',
     'source': 'scripts/effect_acceptance_v3.py', 'lines': [327, 349, 364],
     'controls': ['display_id_permutation_legal', 'body_and_evidence_exact_authorizations_legal'],
     'observed': '展示ID优先配对早于全局同内容抵消；两类决定使用formal_identity与same-object内部token两个不同键。'},
]

summary = {
    'schema': 'independent-remediation-review-r2-v1',
    'created_at_utc': datetime.now(timezone.utc).isoformat(),
    'candidate_commit': HEAD, 'candidate_branch': 'codex/unified-remediation-20261106',
    'main_baseline': MAIN, 'overall': 'REQUEST_CHANGES', 'completion': '未完成',
    'candidate_code_modified_by_reviewer': False, 'merged': False, 'pushed': False,
    'daily_entry_switched': False, 'executor_self_pass_used_as_acceptance': False,
    'remediations': {
        'R-01': {'status': 'PARTIAL_NOT_CLOSED', 'findings': ['F01']},
        'R-02': {'status': 'PARTIAL_NOT_CLOSED', 'findings': ['F02', 'F07']},
        'R-03': {'status': 'PARTIAL_NOT_CLOSED', 'findings': ['F03', 'F04', 'F05']},
        'R-04': {'status': 'PARTIAL_NOT_CLOSED', 'findings': ['F06'],
                 'bounded_v2_layer_consumer_controls': 'PASS', 'semantic_groups_closed': False},
        'R-05': {'status': 'CONFIRMED_BOUNDED_8_RELATIONS', 'verified_relations': len(r05),
                 'samples': ['RB-2023-U04', 'RB-2025-U02'],
                 'budget_zeros_authorized': False, 'persisted_zero_facts_authorized': False,
                 'evidence_file': 'r05_independent_evidence_v2.json',
                 'evidence_sha256': sha(ROOT / 'r05_independent_evidence_v2.json'),
                 'old_v1_seal_modified': False, 'authorization_transfer_to_future_generation': False},
    },
    'findings': findings,
    'independent_validation': {
        'replayed_executor_suite': {'passed': 194, 'warnings': 5, 'seconds': 3.71,
                                   'log': 'receipts/replayed194.log'},
        'new_effect_controls': {'total': len(effect), 'expected_results': sum(x['expectation_met'] for x in effect),
                                'unexpected': [x['case'] for x in effect if not x['expectation_met']]},
        'new_narrative_controls': {'total': len(narrative), 'expected_results': sum(x['expectation_met'] for x in narrative),
                                   'unexpected': [x['case'] for x in narrative if not x['expectation_met']]},
        'new_scope_controls': {'total': len(scope), 'expected_results': sum(x['actual_unknown'] == x['expected_unknown'] for x in scope)},
        'controls_are_not_independent_defect_counts': True,
        'full_executor_5212_and_50_suite_independently_rerun': False,
    },
    'collection_evidence': {
        'source_changes_patch_matches_git_diff': identity['source_changes_patch_matches_git_diff'],
        'changed_source_files': len(identity['changed_paths']),
        'final_collection_samples': 27, 'recorded_failures': 0,
        'four_same_version_risk_repeats': 'INDEPENDENT_COMPARATOR_PASS',
        'full27_twice_rerun_claimed': False,
        'retained_full_fact_context_count_in_offline27': 0,
        'saved_real_upload_amount_projection_counts': [144, 192, 182],
        'saved_real_upload_amount_identity_value_projection': 'IDENTICAL',
        'all27_full_facts_independently_accepted': False,
        'archival_subject_correction_counted_as_parser_recovery': False,
    },
    'effect_gates': gates,
    'independent_live_replay': {
        'overall': live['overall'], 'profile': live['profile'],
        'fresh_isolated_state': True, 'executor_state_reused': False,
        'uploads': len(live['uploads']), 'http_receipts': live['independent_live_http_count'],
        'actual_409': 2, 'exports': len(live['exports']),
        'sql_readback': 'PASS', 'browser': browser['overall'],
        'first_attempt': 'Observer polling exhausted unchanged default rate limiter; original failure retained',
        'second_attempt_polling_seconds': 2, 'default_rate_limit_lowered': False,
        'cleanup': cleanup,
        'frontend': 'copied unchanged local compiled build; no frontend source diff in R-01 to R-05',
        'daily_entry_or_production_readiness_accepted': False,
    },
    'classifications': {
        '已证实修复': ['R-05限定八条N/A原件依据', '普通关系/成员/正文变更的精确v2授权正负控',
                     '普通行列错位和同页重复数负控', '元到万元合法换算',
                     'candidate/tips/manual_review三层精确授权及错层/材料/代际拒收',
                     '限定native配置下独立上传/复核/代际隔离/四格式内容导出/浏览器链路'],
        '合理未知': ['两份原件预算空格仍未知', '原24历史中的22条按既有独立结论保留未知，本轮不重签新代际'],
        '技术未解': [f['id'] for f in findings],
        '缺独立证据': ['原9组完整语义逐对象裁决', '当前35对象/3材料层组的新代际裁决',
                       '27离线材料完整fact_context未保留', '当前其他恢复和替换的独立原件/事实授权'],
        '未验证能力': ['B1', '原7争议', '全预算', 'OCR', 'AI', '模板门禁的真实链路', '真实Word排版',
                       '完整PDF导出版式', '其他地区规则', 'main合入', '唯一日常入口切换'],
    },
    'scope31_self_check': 'PASS_SCOPE_AND_INTERNAL_CONSISTENCY_ONLY_WITH_F06_CAVEAT',
    'preserved': ['r4保护', '旧v1证据', '旧包', '旧meta', '金标', '原独立签收边界'],
    'next_action': '只修复残余R-01至R-04及对应精确契约；保存新代际并逐对象独立裁决。不得为候选调绿。',
}
write('independent_review.json', summary)

report = '''GovBudgetChecker R-01～R-05 第二轮独立复核结果

结论：REQUEST_CHANGES，保持未完成。
候选69884e9c1d7fee91e530b952b7e5b1935802e987；main仍为47581360966191d180880f7fb07dab48974948bb。
R-01～R-04均部分有效但未关闭；R-05仅两份原件对应八条关系的恢复得到独立确认。本结论不授权合并、推送或切换服务。

一、来源、版本和复核边界
先核读本轮整改说明、ledger、source_identity、validation和source_changes.patch，再检查真实源码和新旧工件。e92ccd6至69884e9的9个改动文件与交付哈希全部相符，patch与git实际差异一致；本段没有api/app/config/rules/旧合同改动。最终采集、4风险材料复采及source-view复采均绑定当前相同代码/配置/规则/工具身份。旧独立索引38个文件及执行者交付manifest所列文件保持原哈希。
不改旧包、旧meta、旧v1封印或金标；没有整体引入未批准r5。归档主体正确化属于来源修正，不计为解析恢复。未扩大到其他地区规则，驻广州/西宁仍按上海机构核读。

二、仍阻塞签收的源码问题
F01 / P1 / R-01：narrative_zero_evidence.py:61贪婪移除逗号/冒号之前的前缀，使“以下表述不成立：…无支出”“若使用旧模板填报，…无支出”“不能作此认定，…无支出”错误产生零出处。第19行漏掉常见“公务用车运行维护支出5万元”；第97～98行的全部金额反证又未纳入双阶段总额句式，使“年初预算5万元，支出决算4万元”与总述无支出并存时仍给分项零。独立9个叙述用例中5个负控错误返回零出处。需保留语气支配范围并统一规范金额断言，再做同身份冲突裁决；继续保持预算未知。

F02 / P1 / R-02：effect_acceptance_v3.py:74～79、355～357忽略reference_kind。一条含两个成员的source_diagnostic_group只改为check，成员元数据、正文、关系回执均保留，实际解释却从两条引用变成check_relation_id的一条；无逐对象授权仍PASS。必须比较有效引用集合及解释类型，不能将影响引用解释的类型视为展示字段。

F03 / P1 / R-03：effect_fact_sources.py:48提取无符号数字。独立原格为-6.00，真引文/bbox仍指该格，但raw_value和value_wan填成+6，错误的10=6+4仍PASS。合法-10=-6+-4反而FAIL。如负值不在支持范围，应明确拒绝，不能脱落负号后放行正值。
F04 / P1 / R-03：同文件:44～53只比选定单位引文与raw_unit，未核完整单位词。原件“万元”仅框尾字“元”，100000/60000/40000便被缩为10/6/4万元并PASS，差一万倍。合法元到万元的独立正控已经PASS，修复须同时保留合法换算。
F05 / P1 / R-03：effect_acceptance_v3.py:218～253逐项读取operand_fact_ids，但不核独立契约/required_fact_ids的完整性和重数。原件与独立事实总12、基本6、项目4，operand换为[总,基本,基本]，required仍为[总,基本,项目]，算成12=6+6并PASS。该负控只同步回执摘要以隔离数值机制，未改变独立事实、维度或必需操作数，不能靠摘要一致代替操作数完整性。

F06 / P2 / R-04：verify_pending_review.py:1117～1143按材料SHA+关系匹配旧范围裁决后，以当前status/reason重新生成case_id并批准。真实SP-ORIG-01占比理由从“未披露”改成“已披露但提取失败”，原case_id4816278b…变成e5a73b39…，旧explanation_only仍被套用且未知消失。旧范围补充契约并未为每项保存可校验的批准状态/理由指纹；这一缺口不能转成新批准。需另留新版本，绑定批准case_id/语义指纹/来源；有变化的对象保持待核。

F07 / P2 / R-02：同工具:327～334在全局同内容抵消前优先按易变展示ID配对，两个未变问题交换ID并重排也会FAIL。正文与证据同时合法改变时，issue_changed按formal_identity(old)取授权，而evidence_replaced按内部same-object token取授权（349/364行），提供完整前后摘要、来源和两类精确决定仍误拒。应统一公开的逐对象身份，先抵消未变对象，再配对变化，不能削弱语义授权。

三、已证实有效的限定修复
独立重放指定194项：194 passed，5条依赖弃用告警，3.71秒。另造27个effect用例：20个结果符合预期，7个异常；9个叙述用例：4个符合，5个异常；2个范围用例：1个符合，1个异常。用例数不是缺陷数，执行者全量5212+50等套件不列为本轮独立重放。
普通同规则改挂、成员变化、正文变化在缺授权时拒收，精确授权正控通过；真引文错列、同页重复数错bbox、具有一致摘要的错列头负控被拒；真实元/万元正控通过。三层candidate/tips/manual_review的v2精确授权正控通过，错层/材料/代际/出处及旧v1冒用保持PENDING。上述改善不抵消F01～F07。

R-05：独立提取并目视核读RB-2023-U04（上海驻广州2023）、RB-2025-U02（上海驻西宁2025）两份原件物理页14与21。页14为12列“三公”表，购置决算格第7列空白，预算第6列亦空白；页21明确“公务用车购置支出为0万元”。8条V33-224/CMM-001 × OBL-TXT-T7/OBL-SG-TABLE-TEXT关系具有同主体年度、空格几何与叙述依据，not_applicable合理。没有印刷零金额事实，没有补预算零、借列或以结果反推原件。
另封新代际independent-effect-evidence-v2，只含这8条精确关系契约/适用性证明；facts为空，因为依据是独立空格观察及明确叙述。不是从被检查回执造事实池，也不是规则通行证。SHA256=e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092。旧v1封印未改，不能借给其他代际或关系。

四、采集、金额和效果门禁
最终27份记录全部成功，源码无漂移、源码根无写入；4份风险材料的真实同版本复跑比较PASS。未独立重采全部27两遍，也没有设固定份数门槛。
三份已保存真实上传fact_context的完整金额身份/值投影逐条（保留重复数）比较一致：SP-ORIG-07为144条、RB-2023-U04为192条、RB-2025-U02为182条。投影字段含主体/范围/年度/表/指标/资金/期间/阶段/科目/行序/raw与normalized金额/单位/识别状态，不等于所有出处字段均不变。27离线采集没有保留完整fact_context，因此不能宣称27全部事实已独立复核。
旧27用新工具和旧v1包重放：FAIL，29拒收，233待核对象/16材料层组。29含12同材料代际快照缺失、11新增完整语义授权缺失和6关系替换依据缺失，不是29个软件缺陷。
旧cover：PENDING，0拒收，54对象/5组，不能写成原9组全部关闭。
当前旧final_d1对新final_collection且无新证据：FAIL，127拒收，35对象/3组；只有本轮8条R-05新精确证明时仍FAIL，119拒收，35对象/3组（111快照缺失、4关系契约缺失、4替换依据缺失）。没有填充其他批准来调绿。
scope31_governance自身对照PASS仅支持范围和内部一致性，且受F06影响；原28与additional3的已封范围不等于新代际业务语义批准。原9组缺独立语义证据与当前35对象均仍需逐对象核原件/事实/变化并封印，不能互相抵账。

五、真实流程独立复演
除静态核对执行者42条HTTP、两次真实409、SQL和16份四格式文件外，本轮另从69884e9归档建全新隔离PostgreSQL/API/worker和浏览器会话，不复用执行者state/账号/数据库。范围是上海native PDF和本地规则，明确关闭AI/OCR/template gates；不能外推到那些轨道或日常配置。
独立流程PASS：3份新上传、60条脱敏HTTP回执、未完成阻止结案409和旧分析依据写入409均为真实回应、SQL回读、重分析前后及历史/新会话16份JSON/CSV/PDF/DOCX内容与备注代际隔离、新浏览器显示主体和原页15金额0.0012定位、未完成按钮禁用。没有页面错误或500。前端为本地现有未改编译产物的隔离副本，本轮无前端源码差异；不据此签入口切换。
首轮过密0.5秒轮询触及原默认限流429，失败回执保留；第二轮新实例改观察轮询为2秒后通过，未降低服务限流。所有本轮自建API/worker/数据库/前端已停止，日常服务未触碰。导出验证覆盖内容和代际隔离，真实Word排版与完整PDF导出版式没有签收。

六、五类结论与下一步
已证实修复：上述有效的精确授权/原格定位/合法换算路径、R-05限定8条N/A，以及明确native配置下独立真实流程链。
合理未知：两份原件预算空格继续未知；原24历史中的22条按既有独立材料结论保留未知，本轮没有将其改为完成或重签新代际。旧2条限定恢复不扩权；14引用/4聚合等旧独立结论边界不扩到未审新对象。
技术未解：F01～F07，分别对应R-01～R-04残余错误放行和兼容性误拒。
缺独立证据：原9组和当前新代际对象的完整语义/金额/出处/授权链、其他恢复/替换依据、27离线材料未留存的完整fact_context。不能把来源修正、纯诊断清理或自检PASS计为业务恢复。
未验证能力：B1、原7争议、全预算/OCR/AI、模板门禁的真实链路、真实Word排版、完整PDF版式、其他地区规则、main合入及唯一日常入口切换。
下一步只处理残余R-01～R-04，保留本轮限定R-05证明。新代码/新采集/新适配另留身份、包和meta，不能宣称旧采集属于新封印。关键项任一未关闭继续未完成，不补造授权或材料。可交执行者的具体要求见ZCode修复与复交提示词.txt；脱敏回执索引见review_index.json。
原件、截图、运行state、数据库及连接配置仅保留本地，不进入复核索引或对外发送。本轮没有推送、合并、切换服务或修改候选源码。
'''
(ROOT / '复核结果.txt').write_text(report)

prompt = '''请只修复 GovBudgetChecker 本轮独立复核的残余 R-01～R-04，并准备新候选复交。执行者不得自签独立验收。

基线：69884e9c1d7fee91e530b952b7e5b1935802e987，工作区 /Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106；main 47581360966191d180880f7fb07dab48974948bb。
先读 /Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_r2_20261106/复核结果.txt、independent_review.json、receipts/effect_boundary_controls.json、receipts/narrative_boundary_controls.json、receipts/scope_semantic_rebase_control.json。
独立复现脚本在同根 scripts/independent_boundary_controls.py 和 scripts/scope_semantic_rebase_control.py；合成输入在 control_cases。将旧复核根及其脚本视为只读，复制到新输出后运行，不覆盖旧回执。不得只修改预期或删除失败用例。正控必须保留独立原件/事实依据。

1. R-01 / F01：保留逗号、冒号前对断言的否定/条件支配，不可贪婪删除后再判assertive。三个不成立/若使用旧模板/不能认定负控均不得给零；完整支持规范指标的运行维护支出和双阶段总额非零反证。统一同主体/年度/资金/阶段断言后裁决冲突。购置费/购置支出、物理分页、同身份明确决算零正控仍应通过。预算不补零、不借决算，不把不确定变成完成。避免继续靠追加孤立否定词修补。

2. R-02 / F02、F07：有效引用组解释类型与解析出的成员集合都属于语义。source_diagnostic_group改check导致两成员缩一条、同规则改挂、成员增删/替换、正文变化均要求精确前后完整对象决定，错材料/代际/摘要/来源拒收。不要整规则或整层许可。先抵消完整未变对象，再处理展示ID/次序变化，不因交换展示ID误判。正文及证据同时改变时，issue_changed与evidence_replaced使用同一个有文档定义的逐对象身份；不能要求外部猜内部same-object token。证据全清空/伪造定位仍拒收。保留已有合法关系/成员/正文单独及同时变更正控。

3. R-03 / F03～F05：从原金额格解析数值必须保留符号；如不支持某种负数表示明确拒绝，不可吞掉负号后给正值PASS。单位需核原件完整单位词边界，“万元”框尾字“元”不得作为原单位；真实元到万元换算保留PASS。操作数契约应校验角色、顺序/对应关系、必需集合及合法重数，不能以同一个基本支出替代项目，不能仅更新回执摘要就绕过独立算式。总12/基本6/项目4的[总,基本,基本]负控必须拒收；10=6+4及独立负值支持正控按明确范围处理。事实池继续来自独立原件，不从被测回执生成，不补齐假事实。

4. R-04 / F06：当前v2三层精确授权的正负控已独立确认，保留。修复scope旧批准重绑定：同材料/关系的理由由“未披露”变成“已披露但提取失败”必须留待核。旧scope v1缺少某项可校验语义锚点时不能推导新批准；新增契约或包须另留版本及来源，绑定批准case_id、status/reason或适用性指纹、原件身份，任何语义变化需要独立重审。不得改旧v1封印/r4/旧meta，不把新校验工具伪装为旧采集来源。scope31自检仅内部一致性，不等于原9组或新35对象语义签收。

5. R-05仅保留已独立确认的上海驻广州2023/驻西宁2025共8条N/A。页14购置决算格空白、页21明确购置支出零；预算继续未知。独立新证据包 r05_independent_evidence_v2.json SHA256 e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092，仅绑定原final_collection代际和8条关系；任何新分析代际需要重新绑定和独立核对，不借旧授权。不得把空白变为持久零金额事实。

复交要求：
- 保留当前194项与新边界用例，给出执行命令/原日志和源码patch，不沿用SELF_PASS作签收。测试数与拒收数都不是业务恢复数。
- 新采集只在隔离输出；保存新代际code/profile/rules/kit/analysis身份及完整fact_context。旧27没有保留完整fact_context，不得补写旧meta后声称旧事实已全部复核。风险材料按实际变化选择同版本复跑，不用固定份数门槛。
- 原9组及当前待核对象建立逐对象登记，分别列：可由原件证明、合理未知、技术未解、缺独立证据、未验证。独立批准由复核者完成，执行者不得造批准包或从候选回执构造真值。旧cover仍5组/54对象，旧新工具29拒收是政策要求等混合结果，不能计29缺陷或强制全部调绿。
- 既有本轮真实流程已用新隔离实例独立复演：3上传、60HTTP、2真实409、SQL、16四格式内容/代际隔离、浏览器均PASS，仅native且AI/OCR/template关闭。若新修改影响这些路径再做对应复演，记录完整实际配置；不外推生产就绪。初轮默认限流429保留，不通过降低限流掩盖观察问题。所有自建服务完毕停止。
- 原件、state、数据库及连接配置、凭据仅本地；对外与索引仅脱敏回执。旧证据、旧金标、原7争议/B1不改。仅上海材料及上海模板，不新增其他地区规则。全预算/OCR/AI/真实Word排版继续单列未验收。
- 不合并、不推送main、不切换唯一日常入口。完成整改仍仅SELF_PASS并交独立复核，关键项未关闭保持未完成。独立通过后main合入与日常入口切换仍需各自后续验收。
'''
(ROOT / 'ZCode修复与复交提示词.txt').write_text(prompt)

assert git('rev-parse', 'HEAD') == HEAD
assert git('rev-parse', 'main') == MAIN
assert not git('status', '--porcelain')
preserved = []
for label, manifest in [('prior_review', PRIOR / 'review_index.json'), ('executor_delivery', EV / 'review_manifest.json')]:
    rows = json.loads(manifest.read_text())['files']
    mismatches = [entry['path'] for entry in rows if sha(Path(entry['path'])) != entry['sha256']]
    assert not mismatches, (label, mismatches)
    preserved.append({'manifest': label, 'files': len(rows), 'mismatches': mismatches, 'manifest_sha256': sha(manifest)})
assert sha(ROOT / 'r05_independent_evidence_v2.json') == 'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092'
assert live['overall'] == browser['overall'] == 'PASS'
assert all(cleanup[k] for k in ('owned_api_worker_stopped', 'owned_database_stopped', 'owned_frontend_stopped'))
write('receipts/final_integrity.json', {
    'candidate_head': HEAD, 'candidate_clean': True, 'main': MAIN, 'preserved_manifests': preserved,
    'r05_evidence_sha256': sha(ROOT / 'r05_independent_evidence_v2.json'),
    'own_cleanup': cleanup, 'process_exit_check': read('live_receipts_2/process_exit_check.json'),
    'deliverables': [{'name': name, 'sha256': sha(ROOT / name)} for name in
                     ['复核结果.txt', 'independent_review.json', 'ZCode修复与复交提示词.txt']],
    'index_raw_runtime_originals_or_credentials': False,
})

# 明确白名单，不递归发现文件，避免把私有运行state或原件带入索引。
receipt_names = [
    'independent_review.json',
    'receipts/source_and_collection_identity.json', 'receipts/repeat4_independent.json',
    'receipts/gate_summary.json', 'receipts/old27_independent_replay.json',
    'receipts/old_cover_independent_replay.json', 'receipts/current_unadjudicated_independent_replay.json',
    'receipts/current_with_only_r05_evidence.json', 'receipts/scope31_governance_independent_replay.json',
    'receipts/monetary_projection3_independent.json', 'receipts/effect_boundary_controls.json',
    'receipts/narrative_boundary_controls.json', 'receipts/scope_semantic_rebase_control.json',
    'receipts/r05_eight_independent_verifications.json',
    'receipts/executor_flow_artifacts_independent_audit.json',
    'live_receipts/independent_live_report.json', 'live_receipts/independent_http_receipts.json',
    'live_receipts_2/independent_live_report.json', 'live_receipts_2/independent_http_receipts.json',
    'live_receipts_2/independent_sql_readback.json', 'live_receipts_2/independent_browser_report.json',
    'live_receipts_2/independent_cleanup.json', 'live_receipts_2/process_exit_check.json',
    'receipts/final_integrity.json',
]
entries = []
for name in receipt_names:
    path = ROOT / name
    value = path.read_text()
    json.loads(value)
    # 检测连接URI及带值凭据；这里允许描述性词语和布尔元信息。
    assert not re.search(r'(?:postgres(?:ql)?|redis|mysql)://|Bearer\s+[A-Za-z0-9._-]{12,}|"(?:password|secret|token|api_key|database_url)"\s*:\s*"[^"\s]+"', value, re.I), name
    entries.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path), 'kind': 'sanitized_review_receipt'})
write('review_index.json', {
    'schema': 'independent-review-receipt-index-r2-v1', 'candidate_commit': HEAD,
    'overall': 'REQUEST_CHANGES', 'completion': '未完成',
    'contains': 'Only explicitly whitelisted sanitized review receipts; source originals remain local',
    'excluded': ['runtime_private', 'runtime_private_2', 'credentials and connection configuration',
                 'database state/dumps', 'raw uploaded PDFs and original page images', 'screenshots',
                 'raw export files', 'raw runtime logs', 'control-case original fixtures', 'scripts'],
    'files': entries,
})
(ROOT / 'review_index.sha256').write_text(sha(ROOT / 'review_index.json') + '  review_index.json\n')
print(json.dumps({'overall': summary['overall'], 'indexed_sanitized_receipts': len(entries),
                  'gates': [{k: g[k] for k in ['case', 'overall', 'refusals', 'pending_objects', 'sample_layer_groups']} for g in gates],
                  'source_and_seals_preserved': True, 'index_sha256': sha(ROOT / 'review_index.json')}, ensure_ascii=False))
