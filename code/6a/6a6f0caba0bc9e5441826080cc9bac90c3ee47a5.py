"""汇总最终冻结验证；失败的中间尝试保留，根目录摘要只指向最终证据。"""
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def read(path):return json.loads((BASE/path).read_text())
def write(path,value):(BASE/path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline=read('baseline/comparison_totals.json');final=read('final_complete/comparison_totals.json')
    assert baseline['facts']==dict(bound_exact=11373,missing=120,semantic_mismatch=26,manual_review=79)
    assert final['facts']==dict(bound_exact=11598) and final['reading']==dict(read_exact=11598)
    assert not final['production_code_changed']
    service=read('service_readback_verification.json');assert service['status']=='passed'
    known=read('known_19_reverification.json');assert known['verified']==19
    identities=read('identity_adjudication/summary.json');assert identities['status']=={'source_confirmed':79}
    projection=read('retained_unclassified_numeric_projections.json');assert len(projection)==13
    logfile=(BASE/'final_full_suite.log').read_text()
    match=re.search(r'(\d+) failed, (\d+) passed, (\d+) skipped',logfile);assert match
    failures=[line.split(' - ')[0].removeprefix('FAILED ') for line in logfile.splitlines() if line.startswith('FAILED ')]
    historical=json.loads((ROOT/'outputs/native_structure_repair_20261015/final_summary.json').read_text())['full_suite']['failures']
    assert set(failures)==set(historical),failures
    targeted=(BASE/'final_targeted_suite.log').read_text();assert '129 passed' in targeted
    assert '26 passed' in (BASE/'v23_fixed_regression.log').read_text()
    intended=[];patch=[]
    for p in sorted((BASE/'code_before').rglob('*.py')):
        relative=p.relative_to(BASE/'code_before');current=ROOT/relative
        if current.read_bytes()!=p.read_bytes():
            intended.append(str(relative))
            patch.extend(difflib.unified_diff(p.read_text().splitlines(True),current.read_text().splitlines(True),fromfile='a/'+str(relative),tofile='b/'+str(relative)))
    added=['tests/remediation/test_native_fiscal_coverage.py',*[str(p.relative_to(ROOT)) for p in (ROOT/'tests/fixtures/native_fiscal_coverage').iterdir() if p.is_file()]]
    newtest=ROOT/added[0]
    patch.extend(difflib.unified_diff([],newtest.read_text().splitlines(True),fromfile='/dev/null',tofile='b/'+added[0]))
    (BASE/'task_changes.patch').write_text(''.join(patch))
    for name in intended+added:
        p=ROOT/name;target=BASE/'final_code'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
    before=read('workspace_before.json')
    changed=[name for name,h in before.items() if not (ROOT/name).is_file() or sha(ROOT/name)!=h]
    unrelated=sorted(set(changed)-set(intended));assert not unrelated,unrelated
    assert all(sha(Path(name) if Path(name).is_absolute() else ROOT/name)==h for name,h in read('immutable_before.json').items())
    assert all(sha(ROOT/name)==h for name,h in read('final_code_fingerprints.json').items())
    for document in json.loads((ROOT/'outputs/native_structure_repair_20261015/original_verification.json').read_text())['results']:
        assert sha(Path(document['source_pdf']))==document['sha256']
    # 对照本轮改动前的同文件 Ruff，不把既有告警说成新增，也不声称原文件全绿。
    checked=intended+[added[0]]
    result=subprocess.run([str(ROOT/'.venv/bin/python'),'-m','ruff','check',*checked,'--output-format','json'],cwd=ROOT,capture_output=True,text=True)
    current_lint=json.loads(result.stdout);write('final_ruff.json',current_lint)
    result=subprocess.run([str(ROOT/'.venv/bin/python'),'-m','ruff','check',*[str(BASE/'code_before'/f) for f in intended],'--output-format','json'],cwd=ROOT,capture_output=True,text=True)
    previous_lint=json.loads(result.stdout);write('baseline_ruff.json',previous_lint)
    def diag(x):return Path(x['filename']).name,x['code'],x['message']
    assert not Counter(map(diag,current_lint))-Counter(map(diag,previous_lint))
    lint=dict(new_diagnostics=0,existing_diagnostics=len(current_lint),details=[dict(file=k[0],code=k[1],count=v) for k,v in Counter(map(diag,current_lint)).items()])
    write('ruff_baseline_comparison.json',lint)
    receipts=read('receipt_comparison.json')['final_complete']['totals']
    assert receipts['identified_fiscal_amount_cell_count']==11598 and receipts['pending_review_count']==13
    totals={}
    for doc,code in [('SH2024_01','FIN_05_general_public_expenditure'),('SH2024_14','FIN_05_general_public_expenditure'),('SH2024_11','FIN_08_gov_fund')]:
        facts=read(f'final_complete/documents/{doc}/facts.json')
        totals[doc]={f['indicator']:f['raw_value'] for f in facts if f['subject_code']=='total' and f['table_code']==code}
    integrity=dict(original_15_pdfs_unchanged=True,prior_original_pdfs_unchanged=True,
                   frozen_evidence_files_unchanged=len(read('immutable_before.json')),
                   original_source_grids_unchanged=True,unrelated_existing_files_unchanged=True,
                   final_production_code_drift=False,test_audit_side_effect_restored=read('test_side_effect_cleanup.json')['original_bytes_restored'])
    write('integrity.json',integrity)
    summary=dict(acceptance_date='2026-10-15',recorded_at=datetime.now(timezone.utc).isoformat(),
      parser_fingerprint='pdfplumber-cells-v24-continuation-and-fiscal-coverage',
      verdict='已修复续表列归属及财政事实覆盖；15份固定材料核心表回归通过，保留范围外未分类数字及旧缺标签项目',
      scope='15份已用于发现问题的固定材料，520页，九类核心表135张/物理区域218处；不是未知材料盲测',
      before=dict(amount_cells=11598,physical_literal_matches=11598,facts_present=11478,facts_missing=120,wrong_column_facts=26,source_identity_unadjudicated=79,rule_blocked=251,confirmed_rule_admitted=11148),
      after=dict(amount_cells=11598,physical_literal_matches=11598,facts_present=11598,facts_missing=0,wrong_column_facts=0,source_identity_unadjudicated=0,rule_blocked=0,confirmed_rule_admitted=11598),
      repairs=dict(continuation_cells=78,continuation_missing_facts_restored=52,continuation_wrong_columns_corrected=26,
                   fund_balance_facts_restored=68,fund_nonzero_balance_facts=16,
                   source_identity_cells_adjudicated=79,stage_parity='dept_final/unit_final/district_final归一为final，公开与数据库入口保持决算阶段一致'),
      source_totals=totals,receipt=receipts,
      preservation=read('preservation_verification.json'),known_19=known,
      prior_three_unlabelled=read('known_three_unlabelled_reverification.json'),
      tests=dict(new_fixed_tests=29,source_fixture_amounts=146,old_fixed_tests=26,related_tests_passed=129,
                 full_suite=dict(failed=int(match[1]),passed=int(match[2]),skipped=int(match[3]),failures=failures,failures_match_historical_baseline=True),
                 negative_controls=read('negative_controls.json'),lint=lint),
      live_service=service,
      limitations=dict(retained_unclassified_numeric_projections=13,projection_distribution={'SH2024_10':7,'SH2024_12':3,'SH2024_15':3},
            enlarged_receipt_denominator=11611,enlarged_receipt_facts_present=11598,
            original_pdf_full_coverage_verified=False,human_independent_full_truth=False,independent_unseen_acceptance=False,
            source_words_grid_disagreement_adjudicated_in_prior_baseline=1,
            state_capital_real_balance_amount_cells_in_this_batch=0,state_capital_zero_nonzero_blank_dash_controls=True,
            current_existing_service_version_verified=False,browser_live_validation_in_this_round=False),
      operations=dict(dependencies_added=False,ocr_used=False,existing_services_restarted=False,deployed=False,nas_or_other_projects_touched=False,isolated_resources_cleaned=True),
      integrity=integrity,modified_existing_files=intended,added_files=added,
      evidence=dict(final_comparison='final_complete/comparison_totals.json',per_cell='final_complete/documents/*/comparison.json',
             public_artifact_facts_receipts='final_complete/documents/*/{artifact,facts,receipt,fact_admission}.json',
             source_adjudications='identity_adjudication/source_identity_adjudications.json',service='service_complete/service_receipt.json',
             database='service_database/*/*/{artifact,facts,evidence,cache_runs}.json',
             final_full_suite='final_full_suite.log',handoff='修复核验与验收交接.txt'))
    write('final_summary.json',summary)
    lines=['PDF续表列归属与财政事实覆盖修复交接（v24，2026-10-15）','',
      '最终状态：本地代码已修复并验证；未部署，未重启用户服务，未覆盖历史任务。',
      '最终证据以本文件、final_summary.json、final_complete/、service_complete/及final_full_suite.log为准。',
      'before/、baseline/保留修复前复现；round1/和其他final_*/service_*/中间尝试均为历史开发证据，不能混用版本结论。','',
      '根因和修复',
      '1. 原续表对齐已有严格物理列边界检查，但末行“注：”整表合并格被当成异常科目，提前退出；随后启发式合并造成7列/5列漂移。现只接受唯一、横跨整表的原生注释格，并逐个核验金额矩形及字符均在继承的金额列内。缺字符证明、单格边界偏移、注释不横跨全表的情况继续拒绝确定性计算。',
      '2. FIN_08/FIN_09缺少年初、年末结转结余别名。现通过现有列映射和事实物化链补齐，不根据差额补数；零、空白、破折号、无此项、未识别原文保持区别。双边总表仍使用收入/支出列指标，不按余额项目名称改列指标。',
      '3. 回执曾只遍历成功映射列。现从所有保留数值格起算，分开记录列身份、事实存在/绑定、冲突、规则准入和待复核；编码、序号、重复文字投影、真实填充区的排除逐格保留来源判据。列漂移时不能沿逻辑标签位置过滤整数金额。',
      '4. 真实服务回读另发现dept_final等材料类型未归一，导致数据库事实决算阶段为null。已复用既有文档类型归一，统一公开/存储入口。',
      '5. 指纹为pdfplumber-cells-v24-continuation-and-fiscal-coverage。原source_grids完全保留；调列保留原页、原行列、source_cell_id、bbox和字符证明。','',
      '固定材料前后数字',
      '15份、520页、九类核心表11,598格：金额字面一致11,598→11,598；缺事实120→0；错列26→0；未裁定科目79→0；原有规则门禁阻断251→0；经来源对照且准入11,148→11,598。',
      '78格续表包括52格事实补齐及26格错列纠正；68格基金余额包括16格非零民政余额。',
      '人社续表合计/基本/项目：291,858.57 / 108,158.96 / 183,699.61；经信委：1,342,363.79 / 66,005.37 / 1,276,358.44；民政年初/年末余额：1,782.05 / 1,782.15。',
      '79格裁定仅用原件原生字符对象顺序、坐标和原页渲染，脚本不读取被测facts；原source_oracle和初版比较均未覆盖。不是独立人工签署。',
      '本批已用于发现及修复问题，只能称固定材料回归，不能称未知材料盲测或任意PDF100%准确。','',
      '验证',
      f'新增固定回归29项；相关回归129项通过；旧26项通过；旧4份/19格再次核验通过。禁用续表对齐时2项原页回归按预期失败，去掉余额别名时6项按预期失败；源码未被禁用。',
      '全部15份原先28,458个物理格原文/位置及原始source_grids保全；11,452个原先正确财政事实的金额、科目、列与来源ID保全。',
      '另对旧14份保全6,433个v23冻结数值格、4,450个既有事实及证据；旧3格原件缺科目标签仍无猜填事实。',
      f'完整测试：{match[2]} passed、{match[3]} skipped、{match[1]} failed。失败与旧v23记录一致：',
      *['- '+f for f in failures],
      'Ruff无新增诊断；pdf_parser.py原有6个B905、test_native_pdf_core.py原有3个B905仍保留，不能称全套lint全绿。',
      '真实隔离API/独立Worker/Postgres完成5场景；数据库逐格回读3份原件及校正前后5个快照，共5,024次来源金额观察，覆盖102个本轮修复金额。来源金额、科目、指标、预算/决算阶段、页码、bbox、source_cell_id和存储证据一致。',
      '注入与本轮修复前一致的v23产物及事实，令其成为更新的缓存行；v24查询仍拒绝该旧指纹并命中自身运行。校正后重分析命中v24缓存，原始解析事实/证据及历史导出版本保留。临时容器、进程、目录均已清理。',
      '初次隔离取证脚本调用参数错误的失败尝试、以及阶段字段发现过程均保留；最终以service_complete/和service_readback_verification.json为准。','',
      '未解决和边界',
      '核心表分母11,598格本轮无未裁定项；扩大回执仍保留13个未分类正文文字策略数字投影，分母11,611，已有财政事实11,598，待复核13，不通过静默过滤变成0。分布为教委7、民族宗教局3、财政局3，见retained_unclassified_numeric_projections.json；它们不是本轮已确认的核心表系统缺陷，也未擅自变成财政表事实。',
      '旧3格原件缺科目标签仍保留在各自材料分母；没有猜填。',
      '本批国资表没有可作余额真值的原页金额；同结构国资年初/年末列已通过合成PDF的非零、零、空白、破折号和无此项控制，尚无真实非零国资余额样本验收。',
      'original_pdf_full_coverage_verified始终false；回执和规则通过不等于独立原页全覆盖。原民政同额词合并的1项独立工具差异保留既有裁定，不算系统缺陷。',
      '未重新做浏览器实机测试；接口和现有页面仍消费review_reasons.message，未重设计UI。',
      '早期完整测试向默认审计日志追加87条测试事件；已验证为三次相同测试事件序列，归档后恢复任务开始时原始字节。最终完整测试改用独立AUDIT_LOG_PATH。','',
      '完整性和下一会话',
      '660个冻结证据/源PDF文件哈希无漂移；无关已有文件字节未覆盖；生产源码在最终回归及服务验证期间无漂移。未新增依赖、使用OCR、访问NAS或其他项目。',
      '先读本文件和final_summary.json。复跑使用run_regression.py新的final_*目录、compare_source_v24.py及已有来源裁定；不要覆盖baseline或冻结oracle。',
      '若继续验证泛化能力，应使用未参与修复的新材料，先冻结独立原页真值，再运行公开入口；未识别、待复核继续计入分母。',
      '现有服务与历史任务尚未应用此代码；如需使用须另行授权部署/重分析，不把本地通过当成已上线。',
      'task_changes.patch和final_code/提供本轮增量及最终副本；源码和固定测试位于原工作区，其他未提交修改保留。']
    (BASE/'修复核验与验收交接.txt').write_text('\n'.join(lines)+'\n')
    subprocess.run(['git','status','--short'],cwd=ROOT,stdout=(BASE/'git_status_after.txt').open('w'),check=True)
    write('artifact_manifest.json',{str(p.relative_to(BASE)):sha(p) for p in BASE.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in str(p) and p.name!='artifact_manifest.json'})
    print(json.dumps({k:summary[k] for k in ['parser_fingerprint','before','after','integrity']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
