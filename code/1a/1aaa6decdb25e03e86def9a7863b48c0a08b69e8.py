"""本轮重新执行的验收收尾；不复用旧任务的通过状态。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
FROZEN = BASE.parent / 'shanghai_2024_v23_acceptance_20261015'


def read(name):
    return json.loads((BASE / name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    (BASE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    comparison = read('final_retest/comparison_totals.json')
    tests = read('fresh_test_summary.json')
    service = read('service_readback_verification.json')
    receipt = read('service_complete/service_receipt.json')
    preserved = read('preservation_verification.json')
    known = read('known_19_reverification.json')
    proofs = read('source_identity_proof_recheck.json')
    pending = read('pending_numeric_projections.json')
    before = read('workspace_code_before.json')
    current = {str(p.relative_to(ROOT)): sha(p) for folder in ['src', 'api', 'tests']
               for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
               and p.suffix in ['.py', '.json', '.pdf', '.txt']}
    inventory = json.loads((FROZEN / 'inventory.json').read_text())
    source_ok = all(sha(Path(doc['source_pdf'])) == doc['sha256']
                    and sha(Path(doc['frozen_pdf'])) == doc['sha256'] for doc in inventory)
    assert comparison['facts'] == {'bound_exact': 11598}
    assert comparison['admitted_and_matched'] == comparison['source_amounts'] == 11598
    assert service['status'] == receipt['status'] == 'passed'
    assert service['cleanup'] == receipt['cleanup'] == 'complete'
    assert before == current and source_ok
    assert tests['full']['match_prior_baseline']
    assert known['verified'] == 19 and proofs['verified'] == 79
    assert len(pending) == 13
    full_log = (BASE / 'full_pytest.log').read_text()
    assert '3486 passed' in full_log and '163 skipped' in full_log and '3 failed' in full_log
    critical = read('critical_source_readback.json')
    negative = read('negative_controls.json')
    result = dict(acceptance_date='2026-10-15', recorded_at=datetime.now(timezone.utc).isoformat(),
        verdict='15份固定材料核心表修复通过本轮重新验收；全套测试仍有3项既有失败',
        parser_fingerprint='pdfplumber-cells-v24-continuation-and-fiscal-coverage',
        fresh_execution=True, production_code_modified_by_this_task=False,
        scope=dict(pdfs=15, pages=520, core_table_instances=135, physical_regions=218,
                   amount_cells=11598, independent_unknown_material_blind_test=False),
        before=dict(missing_facts=120, wrong_column_facts=26, source_identity_pending=79,
                    rule_blocked_facts=251, confirmed_and_rule_admitted=11148),
        after=dict(literal_amount_matches=11598, missing_facts=0, wrong_column_facts=0,
                   source_identity_pending=0, rule_blocked_facts=0, confirmed_and_rule_admitted=11598),
        old_repair_and_provenance=dict(known_19_verified=known['verified'],
                   numeric_cells_preserved_in_old_14=known['previous_numeric_cells_preserved'],
                   facts_and_evidence_preserved_in_old_14=known['previous_facts_and_evidence_preserved'],
                   original_physical_cells_preserved=preserved['physical_cells_preserved'],
                   original_correct_facts_preserved=preserved['previously_correct_facts_and_source_ids_preserved'],
                   source_identity_proofs_verified=proofs['verified'],
                   critical_independent_source_readbacks=critical['verified'],
                   prior_three_unlabelled=read('prior_unlabelled_recheck.json')),
        tests=tests, negative_controls=negative, live_service=service,
        pending_outside_core=dict(count=13, enlarged_receipt_denominator=11611,
                   distribution={'SH2024_10':7, 'SH2024_12':3, 'SH2024_15':3},
                   classification='retained_unclassified_numeric_projections'),
        integrity=dict(source_pdfs_unchanged=source_ok, source_and_test_files_unchanged=before == current,
                   service_source_unchanged=receipt['source_unchanged_during_run'], isolated_resources_cleaned=True),
        limitations=dict(original_pdf_full_coverage_verified=False, human_independent_gold_standard=False,
                   real_nonzero_state_capital_balance_sample=False, existing_services_deployed=False,
                   browser_validation_performed=False))
    write('final_summary.json', result)
    report = f'''PDF续表列归属与财政事实覆盖：v24重新验收
日期：2026-10-15；页码均为PDF物理页，从1开始。

结论
本轮重新运行当前v24代码，原15份、520页固定材料的核心表修复通过。没有修改生产代码。
11,598格金额、科目、表种、列身份、物理来源与当前规则准入全部对照一致。
缺事实120→0；错列26→0；科目未裁定79→0；已有事实被门禁阻断251→0。
确认正确且准入11,148→11,598。统计只适用于这批已经参与发现和修复问题的材料，不能当成未知PDF准确率。

重点原页独立回读
人社局第18页合计/基本/项目：291,858.57 / 108,158.96 / 183,699.61。
经信委第20页合计/基本/项目：1,342,363.79 / 66,005.37 / 1,276,358.44。
民政局第24页年初/年末余额：1,782.05 / 1,782.15。
以上8格均再次直接用PyMuPDF从原件bbox回读，数字、列及决算阶段一致。

回退与来源保全
旧4份/19格重新核验通过；另对旧14份保全6,433个既有数值格、4,450个既有事实及证据。
本批原有28,458个物理格、原始source_grids及11,452个原先正确事实/来源身份保全。
79格科目裁定逐条回查原件哈希、原生字符索引、文字、bbox及首字归属，全部吻合；只做NFKC和空白归一，不拿被测facts补真值。此核验不是独立人工签署。
旧3格缺科目标签金额仍分别保留454.17、454.17、1,107.19，未生成猜填科目事实，回执继续source_label_missing。

测试
相关测试：122 passed、1 skipped。
完整测试：3486 passed、163 skipped、3 failed。3个失败ID与v24修复前后记录一致，无新增失败；不能称全套全绿。
{chr(10).join(tests['full']['failure_ids'])}
故意禁用续表对齐时2项固定原页测试失败；故意去掉余额别名时6项失败，均符合预期。生产源码未被禁用。

本轮真实服务验证
临时API、独立Worker、PostgreSQL完成{service['service_scenarios']}个场景。
数据库回读{service['unique_original_documents']}份不同原件及校正前后快照，核对{service['source_cell_observations_verified']}次来源金额观察，覆盖{service['unique_repaired_cells_verified']}个不同修复金额。
金额、科目、指标、预算/决算阶段、bbox、来源ID及存储证据一致。
较新的v23错误缓存被v24拒绝；v24校正后重分析缓存命中，原解析事实及历史版本保留。
服务验证允许合理的review_required状态；这不等于整份决算业务规则全部通过。
本次临时资源已清理，未部署或重启用户既有服务。

保留的边界
核心表分母11,598格已经通过；扩大回执分母11,611仍保留13个未分类数字投影，分别为教委7、民族宗教局3、财政局3，继续待复核，没有静默删除或冒充财政表事实。
本批没有真实非零国资表余额样本，仅有合成PDF边界控制；未验未知模板、扫描OCR及浏览器实机效果。
original_pdf_full_coverage_verified仍为false。
此前长任务中断的部分日志和数据库快照另存interrupted_*与service_interrupted/，核对随机临时数据库配置确认归属后完成清理。最终结论只使用本次完整重跑日志与service_complete/。

下一批建议样本
建议准备6—10份未参与过本轮发现/修复的完整原始PDF，可以在同一文件内覆盖多种条件：
1. 优先至少2份国有资本经营预算收入支出决算表，年初或年末结转结余真实非零；只有空表不能验证此边界。
2. 2—3份其他地区或明显不同版式的决算，覆盖无重复表头续页、编码列合并/拆分、开口尾行、合并单元格等。
3. 2—3份跨页长表、科目名称跨行、金额换行或表格线不完整的原生PDF。
如要验证OCR，请把扫描件单独分组，避免与原生PDF准确率混算。尽量附单位、年度和官网来源链接。
收到新材料后先冻结原件及独立原页真值，再运行当前代码；未识别和未裁定继续计入分母。

证据
final_summary.json：本轮结果、测试、服务和限制。
final_retest/：本轮重新解析与逐格对照、facts、receipt、准入结果。
critical_source_readback.json、source_identity_proof_recheck.json：8格关键原页回读及79格字符证明。
known_19_reverification.json、prior_unlabelled_recheck.json、preservation_verification.json：历史修复与保全。
targeted_tests.log、full_pytest.log、negative_*.log：本轮测试。
service_complete/、service_database/、service_readback_verification.json：本轮真实服务、数据库回读及清理。
pending_numeric_projections.json：13个范围外待复核投影。
'''
    (BASE / '复验报告.txt').write_text(report)
    manifest = {str(p.relative_to(BASE)): sha(p) for p in BASE.rglob('*')
                if p.is_file() and p.name != 'evidence_manifest.json' and '__pycache__' not in p.parts}
    write('evidence_manifest.json', manifest)
    print(json.dumps({key: result[key] for key in ['verdict', 'after', 'pending_outside_core', 'integrity']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
