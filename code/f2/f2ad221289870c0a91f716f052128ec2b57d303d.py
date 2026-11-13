"""只汇总已完成的最终证据，任何硬门未满足则拒绝生成交接结论。"""
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def read(path):
    return json.loads(path.read_text())


def write(name, data):
    (BASE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    new = read(BASE / 'final_verified_new10/native_identity_comparison/totals.json')
    diff = read(BASE / 'final_verified_new10/native_identity_comparison/regression_diff.json')
    old = read(BASE / 'final_verified_old15/comparison_totals.json')
    service = read(BASE / 'service_final_verified/readback_verification.json')
    rules = read(BASE / 'rule_verification/final_verified_new10.json')
    known = read(BASE / 'known19_final_verified/known_19_reverification.json')
    assert new['facts'] == {'bound_exact': 8893} and new['admitted_and_matched'] == 8893
    assert not diff['regressions'] and diff['original_source_grids_unchanged']
    assert not diff['previously_correct_source_cell_id_changes']
    assert old['facts'] == {'bound_exact': 11598} and old['admitted_and_matched'] == 11598
    assert not new['production_code_changed_during_parse'] and not old['production_code_changed']
    assert service['status'] == 'passed' and service['stored_facts'] == 8893
    assert rules['passed'] and known['verified'] == 19
    test_log = (BASE / 'full_pytest_verified.log').read_text()
    assert '3 failed, 3542 passed, 163 skipped' in test_log
    previous26 = (BASE / 'previous_26_tests.log').read_text()
    assert '26 passed' in previous26
    scope_spec = importlib.util.spec_from_file_location('scope', BASE / 'verify_scope_protection.py')
    scope_module = importlib.util.module_from_spec(scope_spec)
    scope_spec.loader.exec_module(scope_module)
    allowed = ['src/services/' + name + '.py' for name in (
        'fiscal_column_identity', 'native_dimension_recovery', 'native_structure_recovery',
        'pdf_parser', 'table_import_receipt', 'table_reading_assessment', 'fiscal_fact_materializer')]
    allowed += ['src/engine/common_rules.py', 'src/engine/rules_v33.py', 'tests/remediation/test_zc01.py']
    scope = scope_module.verify(allowed)
    assert all(scope[k] for k in ('pass_frozen', 'pass_originals', 'pass_unrelated', 'default_audit_log_bytes_unchanged'))
    receipts, retained = {}, {}
    for phase in ('final_verified_new10', 'final_verified_old15'):
        counts, pending = Counter(), []
        for path in (BASE / phase / 'documents').glob('*/receipt.json'):
            receipt = read(path)
            for key in ('amount_cell_count', 'identified_fiscal_amount_cell_count', 'unclassified_numeric_projection_count',
                        'unresolved_money_cell_count', 'unmapped_amount_cell_count', 'fact_missing_count',
                        'fact_conflict_count', 'rule_blocked_count', 'rule_admitted_count'):
                counts[key] += receipt[key]
            for table in receipt['tables']:
                pending.extend(dict(doc_id=path.parent.name, table_code=table['table_code'], **cell)
                               for cell in table['unresolved_money_cells'])
        receipts[phase] = dict(counts)
        retained[phase] = pending
        write(phase + '_retained_unresolved_amounts.json', pending)
    assert receipts['final_verified_new10']['unresolved_money_cell_count'] == 9
    assert receipts['final_verified_old15']['unresolved_money_cell_count'] == 13
    lint = read(BASE / 'ruff_summary.json')
    assert not lint['new_diagnostics']
    failures = [
        'tests/test_budget_intake_20260929.py::test_finals_unresolved_total_le_11',
        'tests/test_engine_repair_20260927.py::test_unresolved_total_le_15',
        'tests/test_review_known_regressions.py::test_t7_thousands_amount_and_yoy_change_are_not_false_mismatches[True]',
    ]
    summary = dict(
        recorded_at=datetime.now(timezone.utc).isoformat(),
        verdict='v25修复完成：固定材料核心来源回归及隔离服务入库通过；保留范围外待复核与既有测试失败',
        parser_fingerprint='pdfplumber-cells-v25-native-tail-and-dimension-identity',
        scope=dict(new_pdfs=10, new_pages=357, new_core_amounts=8893, old_pdfs=15, old_pages=520,
                   old_core_amounts=11598, combined_core_amounts=20491, fixed_material_regression=True,
                   unknown_material_blind_acceptance=False, human_independent_full_truth=False),
        before=dict(source_amounts=8893, literal_exact=8882, truncated_numeric=4, numeric_missing=7,
                    facts_present=8884, facts_missing=9, fact_identity_mismatches=104, facts_value_and_identity_exact=8776,
                    existing_facts_blocked=149, blocked_but_source_correct=145, confirmed_admitted=8631),
        after=dict(source_amounts=8893, literal_exact=8893, truncated_numeric=0, numeric_missing=0,
                   facts_present=8893, facts_missing=0, fact_identity_mismatches=0, facts_value_and_identity_exact=8893,
                   existing_facts_blocked=0, confirmed_admitted=8893, core_pending_review=0),
        repairs=dict(direct_unique_amount_cells=117, cross_grid_amounts=11, economic_subject_codes=65,
                     economic_footer_totals=2, header_polluted_names=39, source_correct_admissions_restored=145,
                     total_improved_cells=262, false_positives_removed=10, correct_candidates_preserved=14,
                     frozen_source_issues_preserved=11, supplementary_source_issues=1,
                     stable_fragment_identity=True, json_proof_roundtrip=True),
        root_causes=[
            '网格按横线分格，原生金额尾字连续但跨线；有效一位小数前缀也可能截断。按原字符首字、金额车道、标签基线及尾字对齐恢复。',
            '经济分类科目缩写未纳入受约束的编码列身份，导致65格缺码及2个跨列合计未物化。',
            '分类标记原生字流起于表头/合并合计格，延伸进入科目格；按连续字流来源角色排除并保存证明。',
            '横向收入漏多行上级补助收入；年初部门预算未触发预算阶段守卫；软换行费用名称退化造成跨项取数。',
            '空白来源列被规则当零；现在逐项说明未完成，明确0仍可计算，完整行真差错不丢弃。',
            '跨格恢复不应改变同一物理网格片段身份；尾字归属证明必须使用稳定JSON列表，避免真实数据库完整快照回读失败。',
        ],
        regression=dict(new_fixed_tests=56, previous_fixed_tests=26, previous_19_cells=19,
                        old_original_pdfs=known['documents'], old_numeric_cells_preserved=6433, old_facts_and_evidence_preserved=4450,
                        new_previously_correct_literal=8882, new_previously_correct_facts=8776, new_previously_correct_admitted=8631,
                        regressions=0, previous_source_cell_id_changes=0, old15=old),
        tests=dict(full_passed=3542, full_skipped=163, full_failed=3, known_failures=failures,
                   focused_fixed_regressions_passed=107, ruff=lint, all_tests_green=False),
        service=service,
        receipts=receipts,
        limitations=[
            '材料已用于发现问题，只能称固定材料回归；不宣称通用100%或独立人工全页签收。',
            '新10份仍保留9个核心范围外数字候选（8个文字投影、1个未分类网格数字）；旧15份保留13个文字数字投影。均留在更宽回执分母中。',
            '此前3格原件缺科目标签的冻结证据保留，不猜填科目。',
            '金额尾数字符顺序中断、不能安全对齐、合并拆行与跨格尾数组合，继续拒绝确定性计算并说明原因。',
            '国资空白来源列按未完成处理；不按差额补零。原件问题未改，未部署或重启现有服务。',
            '旧bbox验收器对相邻同额跨线bbox产生3项歧义；最终以冻结原页字符身份独立对照消解，保留原报告。',
        ],
        protection=dict(frozen_files_checked=2287, original_pdfs_checked=25, preexisting_workspace_files_checked=3473,
                        unrelated_modified=0, default_audit_unchanged=True, dependencies_unchanged=True,
                        existing_services_untouched=True, nas_accessed=False),
        final_evidence=dict(new='final_verified_new10/native_identity_comparison', old='final_verified_old15',
                            known19='known19_final_verified/known_19_reverification.json',
                            rules='rule_verification/final_verified_new10.json',
                            service='service_final_verified/readback_verification.json', tests='full_pytest_verified.log'),
        intermediate_evidence_policy='所有中间失败与中断回执保留。service_verified暴露来源ID与JSON问题，service_storage_verified仍暴露JSON往返；以service_final_verified为最终结果。',
    )
    write('final_summary.json', summary)
    # 只包含本轮相对开工快照的增量，不能用git diff混入用户已有工作。
    patch, changes = [], []
    before = read(BASE / 'workspace_before.json')
    for name in allowed:
        current, previous = ROOT / name, BASE / 'code_before' / name
        changes.append(dict(path=name, before=before[name], after=sha(current)))
        patch.extend(difflib.unified_diff(previous.read_text().splitlines(True), current.read_text().splitlines(True), fromfile='a/' + name, tofile='b/' + name))
    additions = [*ROOT.glob('tests/remediation/*v25.py'), *ROOT.glob('tests/fixtures/native_tail_v25/*'), *ROOT.glob('tests/fixtures/native_identity_v25/*')]
    for path in additions:
        name = str(path.relative_to(ROOT))
        changes.append(dict(path=name, new_file=True, sha256=sha(path)))
        if path.suffix != '.pdf':
            patch.extend(difflib.unified_diff([], path.read_text().splitlines(True), fromfile='/dev/null', tofile='b/' + name))
    (BASE / 'incremental_changes.patch').write_text(''.join(patch))
    write('changed_files.json', changes)
    handoff = f'''GovBudgetChecker v25修复核验与验收交接
记录时间：{summary['recorded_at']}。页码均为PDF物理页，从1开始。
最终代码指纹：{summary['parser_fingerprint']}
工作目录：{ROOT}
新证据目录：{BASE}

一、结果及根因
新增10份357页8893格，读取8882→8893；截断4→0、无可用数值7→0；缺事实9→0、科目身份异常104→0；金额/身份均正确8776→8893；正确准入8631→8893。
117个直接缺陷：水务跨横线尾字11、经济分类缺码65、经费合计2、体育科目串表头39。另145个原本正确的受阻金额恢复准入，共262格改善。
规则10误报移除，14正确候选保留；冻结原件11问题不改，水务p18农林水支出与p32农林支出漏水字另附原字符/图像证明。
跨线恢复依靠原字符顺序及列/标签坐标，不依金额相等或合计差额。保留原网格、原source_cell_id、行列页码及字符证明；表头排除也保留被排除字流证明。
隔离服务暴露了两项需补齐的合同：物理网格身份须稳定，嵌套证明经过JSON必须原样往返。均已修复，不放松完整回读检查。

二、最终证据（仅以这些为本轮结论）
final_verified_new10/native_identity_comparison：8893格按冻结原页字符index独立匹配，逐字符坐标回读，原8882/8776/8631正确项零回退，原source_grids及正确source_cell_id未变化。
final_verified_old15：15份520页11598格金额、科目、列、事实及准入全部一致。旧79项身份裁定沿用原页来源证据，不读取系统事实充当真值。
known19_final_verified：14份旧原件19格通过，原6433数值格、4450事实及证据保持；此前3格缺科目标签冻结记录保留。
rule_verification/final_verified_new10.json：逐项10误报/14正确候选，源文本直接复算7行六来源收入和2个车辆同比，4项注入真错正例仍会报错。国资空白列保持insufficient_data。
service_final_verified/readback_verification.json：隔离API/Worker/Postgres12场景（10份原件、合成预算控制、水务更正重分析），DB回读8893事实及证据与公开入口一致；冻结原字符再次逐格对照。10份真实v24旧缓存后写入仍不被v25误用；重分析复用v25缓存，JSON/CSV/DOCX/PDF历史下载字节不变，原始事实/证据保留；临时环境清理完成。

三、测试及保护
新增56项固定回归（公开PDF/事实入口、真实裁页来源证明、JSON往返及拒绝反例）。原26项通过；focused107项通过。
完整测试3542 passed、163 skipped、3既有失败。失败仍为：
{chr(10).join(failures)}
相关Ruff前后均9项既有诊断，无新增。没有修改依赖。
2287冻结文件、25份本批/旧批原PDF哈希不变；核对3473已有工作区文件，仅10个授权既有文件变更，未覆盖无关改动。默认审计日志字节不变。未接触NAS、其他项目、用户现有服务；未部署。

四、保留的限制
两批已用于发现问题，结果是固定材料回归，不是未知材料盲测；不宣称所有PDF通用100%或独立人工全页签收。
更宽回执中，新批9个、旧批13个核心范围外数字候选继续保留在分母，不为零缺口而静默过滤；详见final_verified_*_retained_unresolved_amounts.json。
所有original_pdf_full_coverage_verified保持false。国资来源空白不补零。字符顺序中断/无法对齐及拆行与跨格尾数组合显式未完成，不做确定性计算。
legacy bbox比较器的3项同额相邻行歧义已按原生字符身份独立消解；旧报告保留，不能把验收器歧义当系统错误。

五、下一会话
先读final_summary.json、本交接、changed_files.json。incremental_changes.patch只反映本轮相对开工快照，不混入用户大量既有变更；PDF fixture按manifest的hash保留。
不要覆盖冻结outputs/shanghai_2024_new10_acceptance_20261015、native_fiscal_coverage_repair_20261015_v24及native_structure_repair_20261015。
重跑公开解析：.venv/bin/python {BASE}/run_batch.py <新phase> --batch new10
独立对照：.venv/bin/python {BASE}/compare_native_identity.py <phase> --before baseline --verify-pdf-characters
真实服务：.venv/bin/python {BASE}/run_live_service.py <新service目录名>（Docker隔离环境权限）
DB对照：.venv/bin/python {BASE}/verify_service_readback.py <service目录名> <公开phase>
完整pytest必须将AUDIT_LOG_PATH指向本轮新目录，防止默认审计日志被测试追加。
中间失败和主动中断证据全部保留，不要将其中的失败场景当作最终服务结论，也不要删除失败基线。
'''
    (BASE / '修复核验与验收交接.txt').write_text(handoff)
    print(json.dumps(dict(verdict=summary['verdict'], combined_core_amounts=20491, full_tests=summary['tests'], service_status=service['status']), ensure_ascii=False))


if __name__ == '__main__':
    main()
