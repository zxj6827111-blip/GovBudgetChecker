"""汇总稳定产物；开发探针、早期失败记录留存但不充当最终结果。"""
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


def main():
    source = read(BASE / 'verified_new9/native_identity_comparison/totals.json')
    old15 = read(BASE / 'verified_old15/comparison_totals.json')
    new10 = read(BASE / 'verified_new10/native_identity_comparison/totals.json')
    diff = read(BASE / 'verified_new9/native_identity_comparison/regression_diff.json')
    new10diff = read(BASE / 'verified_new10/native_identity_comparison/regression_diff.json')
    old15diff = read(BASE / 'verified_old15/previous_v25_regression.json')
    old19 = read(BASE / 'known19_verified/known_19_reverification.json')
    service = read(BASE / 'service_verified/readback_verification.json')
    service_receipt = read(BASE / 'service_verified/service_complete/service_receipt.json')
    api_coverage = read(BASE / 'service_verified/api_business_coverage_verified.json')
    api_receipt = read(BASE / 'service_verified/api_coverage_receipt_verified.json')
    rules = read(BASE / 'rule_candidate_api_verified.json')
    protection = read(BASE / 'protection_final.json')
    ruff = read(BASE / 'ruff_summary_verified.json')
    receipts = read(BASE / 'coverage_and_remaining_review.json')
    previous = read(ROOT / 'outputs/native_fiscal_and_rule_repair_20261015_v25/final_summary.json')
    full_log = (BASE / 'full_pytest_final.log').read_text()
    match = re.search(r'(\d+) failed, (\d+) passed, (\d+) skipped', full_log)
    assert match and tuple(map(int, match.groups())) == (3, 3594, 163)
    failures = re.findall(r'^FAILED (\S+)', full_log, re.M)
    assert failures == previous['tests']['known_failures']
    assert '78 passed' in (BASE / 'focused_verified.log').read_text()
    assert '131 passed' in (BASE / 'previous_repair_focused_verified.log').read_text()
    assert source['facts'] == {'bound_exact': 4235} and source['admitted_and_matched'] == 4235
    assert old15['admitted_and_matched'] == 11598 and new10['admitted_and_matched'] == 8893
    assert source['source_owner_mismatches'] == source['source_profile_owner_mismatches'] == 0
    assert source['recognized_core_regions'] == 110
    assert not diff['regressions'] and not new10diff['regressions'] and not old15diff['changes']
    assert diff['original_source_grids_unchanged'] and new10diff['original_source_grids_unchanged'] and old15diff['source_grids_unchanged']
    assert old19['verified'] == 19 and old19['previous_facts_and_evidence_preserved'] == 4450
    assert service['status'] == service_receipt['status'] == protection['status'] == rules['status'] == api_receipt['status'] == 'passed'
    assert service['cleanup'] == 'complete' and service['stored_facts'] == 4235
    assert ruff['before_diagnostics'] == ruff['after_diagnostics'] == 9 and not ruff['new_diagnostics']

    # 最终三批解析、服务与交付时的代码须相同；不借用中途成功结果。
    live = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for area in ('src', 'api') for p in (ROOT / area).rglob('*.py')}
    for phase in ('verified_new9', 'verified_new10', 'verified_old15'):
        assert read(BASE / phase / 'code_before.json') == read(BASE / phase / 'code_after_parse.json') == live
    for name, sha in service_receipt['code_fingerprints'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha, name
    write(BASE / 'code_final.json', live)

    before = dict(amount_literals_exact=4235, facts_present=4235, identity_exact=4204,
                  identity_mismatches=31, mismatched_but_admitted=15, low_confidence_blocked=16,
                  correct_and_admitted=4204, all_admitted=4219, fact_owner_mismatches=409,
                  profile_owner_unrecognized_documents=2, recognized_core_regions=109,
                  raw_rule_false_positives=3, final_api_false_positives=1)
    after = dict(amount_literals_exact=4235, facts_present=4235, identity_exact=4235,
                 identity_mismatches=0, mismatched_but_admitted=0, low_confidence_blocked=0,
                 correct_and_admitted=4235, all_admitted=4235, fact_owner_mismatches=0,
                 profile_owner_unrecognized_documents=0, recognized_core_regions=110,
                 raw_rule_false_positives=0, final_api_false_positives=0)
    final = dict(
        acceptance_date='2026-10-17', version='v26',
        verdict='本批已确认系统缺陷已修复，固定材料来源回归与隔离服务验证通过；非未知材料盲测、非全页人工验收、未部署',
        parser_fingerprint='pdfplumber-cells-v26-source-row-and-total-identity',
        profile_resolver_version='document-profile-v5-native-cover-room-owner',
        scope=dict(new9_pdfs=9, new9_pages=238, source_core_types=80, source_core_regions=110,
                   new9_core_amounts=4235, fixed_regression_pdfs=34, fixed_regression_pages=1115,
                   fixed_regression_core_amounts=24726, independent_unseen_acceptance=False,
                   human_independent_full_truth=False),
        before=before, after=after,
        repairs=dict(spaced_total_identity_cells=16, cross_row_subject_cells=14,
                     previous_blank_row_tail_identity_cells=1, source_cover_owner_facts=409,
                     blank_basic_expenditure_continuation_regions=1,
                     blank_amount_cells_kept_blank=4, invented_zero_facts=0,
                     physical_source_ids_changed_for_target31=0),
        root_causes=[
            '合 计空格未归一成独立总计标记，16格失去total身份并被低置信度门禁阻断',
            '已证明列范围一致的无表头续页未传入原表标签列身份，跨线科目14格被截断或污染',
            '空金额前行未参与标签来源归属，尾字款污染下一总计；须同时保护后行完整独立单行/多行文字',
            '封面机构后缀未覆盖室，公共事实owner缺失而DB文件名回退产生409条差异',
            '全空金额经济分类尾页被has_amount过滤，31099编码没有获得有证据的列身份排除',
            '叙述科目跨主章节取数/原因、整体金额锚点吸收功能分项、国内接待次未计作批次，分别造成误报',
        ],
        source_comparison=source,
        prior_regressions=dict(old15_amounts=11598, new10_amounts=8893,
                               old15_facts_and_evidence_unchanged=11598,
                               new10_changed_cells=0, previously_correct_new9_source_ids_changed=0,
                               source_grids_unchanged=True, known19=old19,
                               previous_78_continuation_and_68_balance_repairs_preserved=True,
                               prior_79_identity_adjudications_preserved=True,
                               prior_three_unlabelled_source_cells_still_not_guessed=True),
        tests=dict(new_fixed_regressions_passed=52, original_fixed26_passed=26,
                   focused_passed=78, prior_repair_focused_passed=131,
                   full_passed=3594, full_skipped=163, full_failed=3,
                   failures=failures, new_failures=0, all_tests_green=False, ruff=ruff),
        rules=dict(public=rules['engine']['results'], final_api=rules['final_api']['results'],
                   confirmed_source_issue_truth_records=15, source_truth_unchanged=True,
                   public_manual_candidates_retained=6, final_api_manual_candidates_retained=1),
        service=dict(status='passed', scenarios=11, original_documents=9,
                     stored_facts=4235, field_and_evidence_parity_errors=0,
                     old_v25_cache_versions_rejected=9, history_identical=service['history_identical'],
                     corrected_overlay_preserved_original_parse=True,
                     cleanup='complete', postgres_removed=service_receipt['postgres_removed'],
                     processes_stopped=service_receipt['processes_stopped'],
                     temporary_workspace_removed=service_receipt['temporary_workspace_removed'],
                     api_receipt_parity=api_receipt['status'], business_coverage=api_coverage,
                     existing_user_services_restarted=False, production_deployed=False),
        receipts={p: receipts[p]['totals'] for p in ('baseline', 'verified_new9', 'verified_new10', 'verified_old15')},
        limitations=dict(
            enlarged_numeric_candidate_denominator=24751, retained_unclassified_numeric_projections=25,
            retained_projection_distribution=dict(new9=3, new10=9, old15=13),
            projections_are_not_proven_missing_fiscal_amounts=True,
            original_pdf_full_coverage_verified=False, retained_in_denominator=True,
            real_nonzero_state_capital_balance_sample_not_present=True,
            no_new_model_ocr_threshold_dependency_or_ui_redesign=True,
            live_browser_validation_in_this_round=False,
            existing_historical_jobs_not_reanalyzed=True,
            ai_not_run_original_documents=9,
            existing_test_failures=3, existing_lint_diagnostics=9),
        protection=dict(original_pdfs=34, unchanged_original_pdfs=34,
                        frozen_files=5661, unchanged_frozen_files=5661,
                        existing_workspace_files=3485, authorized_existing_file_changes=7,
                        unrelated_workspace_changes=0, expected_new_test_and_fixture_files=23,
                        default_audit_bytes=180396, default_audit_bytes_unchanged=True),
        comparison_scope_correction=dict(early_region_count=80, source_rechecked_baseline=109,
                                         final_region_count=110,
                                         cause='早期比较器仅统计物理标题，漏计逻辑续区；不是解析器缺30区',
                                         baseline_record_overwritten=False,
                                         proof='baseline_region_role_recheck.json'),
        final_evidence=dict(
            source='verified_new9/native_identity_comparison/totals.json',
            repaired_cells='target_31_identity_repairs.json',
            independent_review='independent_evidence_final_review.json',
            remaining_review='coverage_and_remaining_review.json',
            old15='verified_old15/comparison_totals.json',
            old10='verified_new10/native_identity_comparison/totals.json',
            old19='known19_verified/known_19_reverification.json',
            tests='full_pytest_final.log', focused='focused_verified.log',
            old_focused='previous_repair_focused_verified.log', lint='ruff_summary_verified.json',
            service='service_verified/readback_verification.json',
            service_api='rule_candidate_api_verified.json',
            api_receipts='service_verified/api_coverage_receipt_verified.json',
            business_coverage='service_verified/api_business_coverage_verified.json',
            protection='protection_final.json', incremental_patch='scope_incremental_existing.patch'),
        intermediate_evidence_policy='baseline、final_*、service_final及早期失败日志全部留存。仅verified_*、service_verified和本汇总引用证据作为最终结果。',
    )
    write(BASE / 'final_summary.json', final)
    changed = [dict(path=r['path'], before=r['before'], after=r['after'])
               for r in protection['existing_workspace']['changed']]
    added = [dict(path=r['path'], sha256=hashlib.sha256((ROOT / r['path']).read_bytes()).hexdigest())
             for r in protection['new_workspace_files']]
    write(BASE / 'changed_files.json', dict(basis='workspace_before.json；增量补丁基于code_before，不包含此前脏改', changed=changed, added=added))

    text = '''GovBudgetChecker v26 修复核验与验收交接
交付日期：2026-10-17。页码均为原PDF物理页，从1开始。
项目：/Volumes/workSSD/software development/GovBudgetChecker
证据目录：outputs/native_identity_and_profile_repair_20261016_v26/

结论
针对上海2024新增9份的已确认系统问题完成最小修复。金额读取本来已正确；本次补齐31格科目/总计身份、409条事实单位、1个空金额核心续区，并移除3项原始规则误报（其中1项到达最终API）。
原9份238页4235金额格现为：4235字面一致、4235身份和列一致、4235事实有证据、4235正确准入。不是未知材料盲测，也不是全PDF全页人工签字验收。未部署、未重启现有服务、未重分析用户历史任务。

一、修改前复现与根因
修改前代码、工作区哈希、默认审计字节、5661冻结文件哈希均先保存。baseline/保存公开PDF、财政事实、回执、规则复现；31语义差异、409事实owner差异、2份画像缺失可重查，金额4235格从未变。
16格“合 计”：只归一完整独立标记为total；不改原始格字面。
14格应急管理跨线科目：原生字符流恢复须使用经证明相同物理金额列范围的相邻续页表头。非相邻、单位冲突、不安全边界继续拒绝。
参事室第12页1格：前行“国有资本经营预算财政拨款”末字跨横线；原流证明款归前行，总计归本行。空金额前行可拥有尾字，但不产生0事实。同时保护完整独立单行总计、偏移基线总计、多行科目，避免把正确后行吸入前行。
2份封面“室”后缀遗漏：从源封面恢复单位，保留多主体/括号校验和封面优先级。金融材料误导文件名仍不能覆盖实际发布单位。
国防第11页：与第10页六列经济分类表按连续页面、金额边界、原生编码证明及闭合标签对齐。31099为编码；四个金额格保持明确空白，不造零值事实。
规则：V33-234按主章节边界和真实页尾续读原因，禁止房补项借后续三公金额/原因；V33-106须有同一总体主体直接披露的金额，不能把672.47分项当1622.36总体；V33-246识别国内公务接待18次、114人次，排除任意业务次数/外宾混用。
生产改动6个模块，另更新指纹固定测试；没有新增依赖或模型，没有降低OCR/置信度门禁，没有界面重设计。
解析指纹：pdfplumber-cells-v26-source-row-and-total-identity
画像版本：document-profile-v5-native-cover-room-owner

二、修复前后（同一来源分母4235）
金额逐字一致：4235 -> 4235；有事实：4235 -> 4235。
身份和列一致：4204 -> 4235；身份错误：31 -> 0。
正确且可准入：4204 -> 4235；错误但准入：15 -> 0。
低置信度阻断：16 -> 0；先证明身份后恢复准入，没有关闭门禁。
事实owner与封面/公共入口差异：409 -> 0；画像单位未识别：2 -> 0。
核心物理区域：109/110 -> 110/110；金额没有因补空区增加。
原始规则3项误报 -> 0；最终API1项误报 -> 0。
31目标的金额字面和source_cell_id全保持，原先正确4204格的source_cell_id全保持；所有原始source_grids全保持。
详细逐格：target_31_identity_repairs.json、verified_new9/native_identity_comparison/。
说明：早期baseline的附加比较器曾仅算有物理标题区域而得到80；按同一来源区域和逻辑续区独立重核为109，见baseline_region_role_recheck.json。旧记录保留，不能把80当解析器缺30区。

三、旧材料与来源回归
34份1115页，共24726核心金额：旧15份11598 + 新10份8893 + 本批4235，金额、科目、列、事实与准入全部来源对照一致。
旧15份11598条事实字段和证据与v25严格一致，原网格不变；新10份8893格0变化、0回退，原网格不变。此前78格续表、68格余额、79格独立裁定继续保留。
此前19格在原14份中再次通过；6433原有物理金额格、4450条原有事实与证据严格保留；3格原件缺科目标签的证据未变，不猜填。
来源真值来自冻结原生字符、坐标、原始网格及已有独立裁定，不把被测事实抄成expected，也不按相等金额反找来源。

四、测试和隔离服务
新增52项固定回归全部通过，覆盖31目标、空区不造零、独立多行保护、来源证据、误导文件名及规则反例；另原26项固定回归全部通过，合计78通过。其他此前修复相关131项通过。
最终完整测试：3594 passed，163 skipped，3 failed，见full_pytest_final.log。
3项均与v25已有失败名单相同：
  tests/test_budget_intake_20260929.py::test_finals_unresolved_total_le_11
  tests/test_engine_repair_20260927.py::test_unresolved_total_le_15
  tests/test_review_known_regressions.py::test_t7_thousands_amount_and_yoy_change_are_not_false_mismatches[True]
不得说全套全绿。早期新增测试曾误用不存在的ParsedCell.source_cell_id，已改用物理fragment/row/col派生ID；失败日志保留，最终完整重跑仅上述3项既有失败。
Ruff同配置前后均9项既有诊断（2 B023、7 B905），0新增；新增测试无lint诊断。不是lint零错误。
隔离API/Worker/Postgres共11场景：9原件、1合成预算、1真实原件修订重分析。全部服务校验通过，数据库回读4235事实、来源证据、原网格、单位和画像均与公开入口一致；独立来源再核4235/4235，110/110区域。
9份逐一注入比当前版本更新创建的真实v25旧缓存，v26查询均拒绝误用；旧版仍可按旧指纹读取。重分析保留原解析依据，原JSON/CSV/DOCX/PDF历史字节全部一致。
最终9份API回执与公共回执计数一致，original_pdf_full_coverage_verified全部false；本批3个未分类投影仍在API回执中。
服务完成不等于业务审校全通过：387项适用义务，327完成，60未完成，其中51 insufficient_data、9 ai_not_run；7份review_required、2份done。对比原验收299完成、88未完成、79阻断；现51阻断。
最终API14条原件问题有效候选保留，1条原误报消失，1条边界候选继续待复核；原始规则6条待复核仍保留。15项独立原件问题真值未修改。
临时Postgres、API、Worker和工作目录全部清理；未触及NAS或其他项目。

五、仍有限制和待复核
更宽回执分母仍为24751数值候选，非仅成功映射列：24726核心事实 + 25个未分类数字投影（旧15份13、新10份9、本批3）。25个候选有页/格/原因保留，不能直接称25格真实财政金额漏失；也不能静默排除以获得零缺口。
本批4238宽分母 = 4235核心 + 3投影，尚不能声称整份PDF全覆盖；原页全覆盖标志继续false。
金融材料出国费占比57.56%与63.24/110.06的约57.46%存在0.10043百分点边界差异，继续待复核，不调整容差以消除它。
没有获得真实非零国有资本余额新样本；既有零值/空白/破折号控制保持，不能据此声称已对所有资本余额实样本验证。
本轮未做真实浏览器验收，按既有API/页面合同保留覆盖字段，无界面改动。没有对现有用户服务或历史任务执行升级重分析；实际应用需在后续获授权的运行环境按v26重新分析，保留历史版本。

六、保护与交付入口
34/34原PDF哈希未变，5661/5661冻结文件未变。3485既有文件中仅允许的7个变化，0无关改动，23个新增测试/fixture均在精确清单；默认audit保持180396字节完全一致。另14原件在19格重核中逐份校验SHA。
最终汇总：final_summary.json
本轮变更：changed_files.json；scope_incremental_existing.patch以code_before为基准，仅包含本轮7旧文件差异，不混入用户此前脏改。
最终来源：verified_new9/、verified_new10/、verified_old15/。
最终服务：service_verified/service_complete/service_receipt.json、service_verified/readback_verification.json、service_verified/api_business_coverage_verified.json。
最终候选：rule_candidate_api_verified.json；api_report_map_verified_proof.json保留来源/job/报告哈希关系。
保护：protection_final.json；独立总审：independent_evidence_final_review.json。
开发探针final_*、service_final、早期失败日志都留存；不可代替上面最终稳定产物。

七、下一会话使用
先读本交接、final_summary.json和independent_evidence_final_review.json；核对当前代码与code_final.json，再决定是否需重跑。不要沿用v25指纹，不覆写冻结验收或基线，不把本批称为未知材料盲测。
公开入口复跑（用新PHASE名，不能覆盖既有目录）：
  .venv/bin/python outputs/native_identity_and_profile_repair_20261016_v26/run_batch.py <新PHASE>
  .venv/bin/python outputs/native_identity_and_profile_repair_20261016_v26/compare_source_v26.py <新PHASE> --verify-pdf-characters --before baseline
仅隔离服务复验可运行run_live_service.py <新RUN>，再verify_service_readback.py <新RUN> <新PHASE>；会创建和清理专属临时Postgres/API/Worker，需要相应本机Docker权限，不能操作现有服务。
完整测试必须给AUDIT_LOG_PATH指定本次新输出路径，避免写入默认历史审计文件。
'''
    (BASE / '修复核验与验收交接.txt').write_text(text)
    print(json.dumps(dict(status='passed', summary=str(BASE / 'final_summary.json'), handoff=str(BASE / '修复核验与验收交接.txt')), ensure_ascii=False))


if __name__ == '__main__':
    main()
