"""只根据本次实际执行的证据生成结论，不沿用修复会话的通过状态。"""
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


execution = read(BASE / 'execution.json')
assert len(execution) == 9 and all(row['status'] != 'running' for row in execution)
assert all(row['exit_code'] == 0 for row in execution if row['step'] != 'full_pytest')
new = read(BASE / 'new10/native_identity_comparison/totals.json')
old = read(BASE / 'old15/comparison_totals.json')
assert new['facts'] == {'bound_exact': 8893} and new['admitted_and_matched'] == 8893
assert old['facts'] == {'bound_exact': 11598} and old['admitted_and_matched'] == 11598
rules = read(BASE / 'rule_verification/new10.json')
assert rules['passed'] and rules['false_positive_cases_removed'] == 10 and rules['confirmed_candidates_retained'] == 14
regression = read(BASE / 'new10/native_identity_comparison/regression_diff.json')
assert not regression['regressions'] and not regression['previously_correct_source_cell_id_changes']
assert regression['original_source_grids_unchanged']
known19 = read(BASE / 'known_19_reverification.json')
assert known19['verified'] == 19
service = read(BASE / 'service/readback_verification.json')
assert service['status'] == 'passed' and service['stored_facts'] == 8893 and service['cleanup'] == 'complete'
api_business = read(BASE / 'service/api_rule_and_coverage_verification.json')
assert api_business['status'] == 'passed'
assert api_business['false_positive_cases_removed'] == 10 and api_business['correct_candidates_retained'] == 14
business = api_business['business_check_totals']
limits = read(BASE / 'unlabelled_reverification.json')
assert limits['status'] == 'passed'
receipts = read(BASE / 'receipt_totals.json')
protection = read(BASE / 'protection_after.json')
assert not any(protection[key] for key in ['source_test_fixture_drift', 'protected_evidence_drift', 'input_pdf_drift'])
cleanup_check = read(BASE / 'cleanup_independent_check.json')
assert cleanup_check['container_absent'] and cleanup_check['api_port_closed'] and cleanup_check['temporary_workspace_absent']
assert all(value == 'stopped' for value in cleanup_check['processes'].values())

full_text = (BASE / 'full_pytest.log').read_text()
summary_line = [line for line in full_text.splitlines() if re.search(r'\d+ failed, \d+ passed, \d+ skipped', line)][-1]
failed, passed, skipped = map(int, re.search(r'(\d+) failed, (\d+) passed, (\d+) skipped', summary_line).groups())
failed_tests = re.findall(r'^FAILED (\S+)', full_text, flags=re.M)
expected_failures = read(ROOT / 'outputs/native_fiscal_and_rule_repair_20261015_v25/final_summary.json')['tests']['known_failures']
new_failures = sorted(set(failed_tests) - set(expected_failures))
assert failed == len(failed_tests)
focus_text = (BASE / 'focused_pytest.log').read_text()
focused = re.search(r'(\d+) passed, (\d+) skipped', focus_text)
tests = dict(full_passed=passed, full_skipped=skipped, full_failed=failed, failed_tests=failed_tests,
             newly_failed_tests=new_failures, all_tests_green=failed == 0,
             focused_passed=int(focused[1]), focused_skipped=int(focused[2]))

audit = read(BASE / 'default_audit_before_tests.json')
actual_audit = Path(audit['path'])
audit_unchanged = (hashlib.sha256(actual_audit.read_bytes()).hexdigest() if actual_audit.exists() else None) == audit['sha256']
protection['default_audit_unchanged_since_early_test_snapshot'] = audit_unchanged
write(BASE / 'protection_after.json', protection)

rows = []
for phase in ['old15', 'new10']:
    for doc in read(BASE / phase / 'inventory.json'):
        folder = BASE / phase / 'documents' / doc['doc_id']
        comparison = read(BASE / phase / 'native_identity_comparison' / f"{doc['doc_id']}.json") if phase == 'new10' else read(folder / 'comparison.json')
        receipt = read(folder / 'receipt.json')
        row = dict(batch=phase, doc_id=doc['doc_id'], filename=doc['filename'], year=2024, pages=doc['pages'],
                   core_amounts=len(comparison), read_exact=sum(r['reading_status'] == 'read_exact' for r in comparison),
                   facts_exact=sum(r['fact_status'] == 'bound_exact' for r in comparison),
                   admitted=receipt['rule_admitted_count'], wider_amount_candidates=receipt['amount_cell_count'],
                   outside_core_pending=receipt['unresolved_money_cell_count'], verdict='核心表固定材料回归通过')
        assert row['core_amounts'] == row['read_exact'] == row['facts_exact'] == row['admitted']
        rows.append(row)
with (BASE / '逐份复验结果.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

source_issues = read(ROOT / 'outputs/shanghai_2024_new10_acceptance_20261015/confirmed_source_issues.json')
supplement = read(BASE / 'rule_verification/supplementary_source_issue.json')
write(BASE / '原件问题索引.json', dict(frozen_issues=source_issues, supplementary_issue=supplement,
    separation='这是原件问题，不是本次解析回归失败；未更改原PDF。'))

summary = dict(recorded_at=datetime.now(timezone.utc).isoformat(),
    verdict='v25在当前固定25份材料的核心表读取回归通过；保留原件问题、范围外候选及既有测试失败',
    parser_fingerprint='pdfplumber-cells-v25-native-tail-and-dimension-identity',
    scope=dict(pdfs=25, pages=877, core_tables=225, physical_core_regions=368, core_amounts=20491,
               broader_numeric_candidates=20513, outside_core_pending=22, independent_unknown_material_blind_test=False),
    new10=new, old15=old, known19=known19,
    improvements=dict(before_literal_exact=8882, after_literal_exact=8893, truncated_numeric=0, unusable_numeric=0,
                      missing_core_facts=0, subject_identity_errors=0, blocked_core_facts=0,
                      before_correct_and_admitted=8631, after_correct_and_admitted=8893,
                      direct_defect_cells=117, improved_cells=262, original_grid_and_correct_source_id_preserved=True),
    rules=dict(false_positives_removed=10, correct_candidates_retained=14, positive_controls=4,
               frozen_source_issues=11, supplementary_source_issues=1,
               new_unadjudicated_candidates=rules['new_unadjudicated_noninfo_candidates']),
    service=service, api_business_coverage={key: value for key, value in api_business.items() if key not in ['documents', 'candidate_checks']},
    tests=tests, receipt_totals=receipts, unlabelled_legacy=limits,
    protection=protection,
    limitations=['两批材料已经用于修复，只能判定固定样本回归通过，不能推导未知PDF通用100%准确。',
                 '核心范围之外22个数字候选继续计入20513个宽口径候选分母，不等同于22个已确认漏读金额。',
                 '原件本身11项问题及1项补充名称漏字仍在；这不是所有报告内容无误。',
                 '实际API仍为review_required，业务取数不足和未运行语义模型的检查未完成，不能称全自动审校通过。',
                 '此前3格原件缺科目标签金额继续保留、不猜科目；国资来源空白不填零。',
                 '没有修改生产代码、原PDF或既有服务；未部署。'],
    next_samples=dict(recommended=bool(not new_failures), count='8–12份此前未用于修复的完整原始PDF',
        priority=['2–3份国有资本经营预算表实际非零，尽量同时有期初/收入/支出/期末',
                  '其他省市、不同制表软件或明显不同版式，部门与所属单位均覆盖',
                  '原生表格中带跨页续表、缺线尾行、两行金额或复杂合并表头',
                  '扫描件如需验收，单列为OCR样本组，另计分母'],
        source_metadata='尽量附官方下载链接、年度与单位；先冻结原件及独立真值，再运行系统。'))
write(BASE / 'final_summary.json', summary)

text = [
    'v25新会话复验报告（2026-10-16）',
    '结论：当前25份2024年度部门决算的核心表读取回归通过。尚不能称所有报告内容无误，或任意PDF识别准确率100%。',
    '',
    '一、本轮实际重新执行',
    '25份原PDF哈希与冻结基线一致；本次开始时22个v25变更文件/fixture哈希与修复交接一致。',
    '重新调用当前公开解析/事实/规则入口，而非读取此前通过结果。25份共877页、225张核心逻辑表、368处核心物理区域。',
    '新10份：8893/8893格的金额逐字、科目、指标、列和准入全部匹配。按冻结原页字符index选择来源，不按同额金额或跨行bbox猜身份；8893格原字符文字及坐标再次核对。',
    '旧15份：11598/11598格匹配并准入。两批合计20491个核心金额格通过。旧79项来源身份裁定继续使用冻结来源证据。',
    '旧4份/19格修复仍通过；14份旧原件6433个数值格、4450个事实及来源证据保持。',
    '',
    '二、v25修复效果（新10份）',
    '金额正确：8882→8893；截断4→0；无可用数值7→0；缺财政事实9→0；科目身份错误104→0。',
    '正确且准入：8631→8893。117格直接缺陷修复，另145格解除连带阻断，共262格改善；原先正确项、原始网格及正确source_cell_id未回退。',
    '10条已裁定误报全部移除，14条正确候选全部保留。4项主动注入真错的正例仍能报错。',
    '国资委空白收入来源列保持insufficient_data，不将空白补零，也不按差额填值。',
    '旧bbox比较器仍留下3项同额相邻行歧义；本轮保留其记录，并用独立原字符index核对全部8893格消解。',
    '',
    '三、真实服务与测试',
    f"实际隔离API/Worker/Postgres共{service['scenarios']}场景、10份原件；DB回读{service['stored_facts']}条核心事实及来源证据一致。",
    '10份真实v24旧缓存不会被v25误用；更正重分析使用v25缓存，原事实与历史JSON/CSV/DOCX/PDF下载字节保留；隔离资源已清理。',
    '真实API中的10条误报也全部消除，14条正确候选仍在。10份真实材料终态仍均为review_required，不应将技术场景通过解释为自动审校通过。',
    f"10份实际API按当前检查目录共有{business['applicable_total']}项适用检查，完成{business['completed_total']}项，未完成{business['unresolved_total']}项，其中阻断项{business['blocking_total']}项。未完成原因：{json.dumps(api_business['unfinished_by_reason'], ensure_ascii=False)}。未完成项继续计入分母。",
    f"定向测试：{tests['focused_passed']} passed、{tests['focused_skipped']} skipped。",
    f"完整测试：{passed} passed、{skipped} skipped、{failed} failed。全套不是全绿。新增失败：{len(new_failures)}。",
    '失败项：', *failed_tests,
    '',
    '四、保留的问题与范围限制',
    '原件已有11项问题仍保留；另确认水务局第32页防汛条目“农林支出”漏“水”字，原第18页对应名称为“农林水支出”。',
    '例如体育局采购合计与分项相差5151.25万元、规划局人员经费与301+303相差111.07万元、外事与规划的“元/万元”问题，不应当因读取修复而消失。',
    '新批9个、旧批13个核心范围外数字候选继续保留；宽口径20513候选中，20491格核心已核对，22个候选未作为核心财政事实裁定。不能将此表述为全PDF零未知。',
    '其中生态环境局第8页候选“3”经完整原页渲染确认为单位名单序号；原回执仍保留候选及分母。其余范围外候选保留上下文待复核。',
    '此前妇联单位/部门各454.17、小铃铛幼儿园1107.19三个缺标签金额已再次解析核对：金额保留，缺科目仍不编造。',
    '补充三格验收脚本初次按相交bbox取字时带入妇联下一行433.74，产生验收器失败；原始失败日志保留。已按来源格内唯一原词中心选取重新通过，未按期望金额搜索，未改生产代码。详见legacy_unlabelled_bbox_adjudication.json。',
    '所有原件全文独立覆盖签收标记仍为false；这些材料已参加修复，属于固定回归，不是盲测。',
    '本轮没有改生产代码、原件或既有服务，没有部署。',
    '',
    '五、下一批样本',
    '建议准备8–12份未参加过修复的完整原始PDF，用于真正的独立验收。现有样本足以证明本轮已知缺陷修复，不必继续堆同版式。',
    '优先2–3份国有资本经营预算实际非零材料，尽量含期初/收入/支出/期末；再覆盖其他省市、不同版式、不同制表软件及复杂续表/缺线尾行。',
    '已直接核对25处原页国有资本经营预算表，当前样本没有非零金额。现有材料也足以继续排查业务未完成项；新样本主要用于检验泛化能力。',
    '扫描件如需测试，请单列OCR组；原生表格与OCR不混算准确率。尽量附官方下载链接、单位与年度。',
    '收到新材料后，先保存原件、冻结独立原页真值与未裁定分母，再运行系统；新缺陷先留失败证据再修代码。',
    '',
    '证据：final_summary.json、逐份复验结果.csv、new10/native_identity_comparison、old15/comparison_totals.json、rule_verification/new10.json、service/readback_verification.json、full_pytest.log、retained_unresolved_candidates.json、unlabelled_reverification.json。',
]
(BASE / '复验报告.txt').write_text('\n'.join(text) + '\n')
manifest = {str(path.relative_to(BASE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in BASE.rglob('*') if path.is_file() and not path.is_symlink()
            and path.name not in ['evidence_manifest.json'] and '__pycache__' not in str(path)}
write(BASE / 'evidence_manifest.json', dict(files=len(manifest), sha256=manifest))
print(json.dumps(dict(verdict=summary['verdict'], tests=tests, source_issue_count=12, core_amounts=20491), ensure_ascii=False))
