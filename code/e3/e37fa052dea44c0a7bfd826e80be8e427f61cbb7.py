"""冻结本轮结构恢复修复证据，保留 v22 和初次回归记录。"""
from pathlib import Path
import difflib,hashlib,json,re,shutil
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
 corpus=json.loads((BASE/"frozen_corpus/summary.json").read_text())
 original=json.loads((BASE/"original_verification.json").read_text())
 service=json.loads((BASE/"service/service_receipt.json").read_text())
 db=json.loads((BASE/"service_readback_verification.json").read_text())
 assert corpus["pdfs"]==377 and corpus["pages"]==9700 and corpus["reading_gaps"]==0
 assert corpus["unbound_money_cells"]==3 and corpus["reading_blocked_facts"]==0
 assert corpus["source_unchanged"] and not corpus["source_code_drift"]
 assert original["code_unchanged"] and original["source_unchanged"] and original["repaired_money_cells"]==19
 assert original["documents"]==14 and original["previous_numeric_cells_preserved"]==6294
 assert service["status"]=="passed" and service["cleanup"]=="complete" and service["source_unchanged_during_run"]
 assert len(service["scenarios"])==6 and db["status"]=="passed" and db["unique_repaired_money_cells"]==19
 log=(BASE/"final_full_pytest.log").read_text();counts=re.search(r"(\d+) failed, (\d+) passed, (\d+) skipped",log)
 assert counts and tuple(map(int,counts.groups()))==(3,3473,147)
 failures=re.findall(r"^FAILED ([^ \n]+)",log,re.M)
 assert set(failures)=={
  "tests/test_budget_intake_20260929.py::test_finals_unresolved_total_le_11",
  "tests/test_engine_repair_20260927.py::test_unresolved_total_le_15",
  "tests/test_review_known_regressions.py::test_t7_thousands_amount_and_yoy_change_are_not_false_mismatches[True]"}
 assert "4 failed" in (BASE/"controlled_no_recovery_final.log").read_text()
 files=["src/services/pdf_parser.py","src/services/native_structure_recovery.py",
        "src/services/table_reading_assessment.py","src/services/fiscal_fact_materializer.py",
        "tests/remediation/test_zc01.py","tests/remediation/test_native_structure_recovery.py",
        "tests/remediation/test_native_structure_pdf_pipeline.py"]
 files += [str(p.relative_to(ROOT)) for p in (ROOT/"tests/fixtures/native_structure").iterdir() if p.is_file()]
 fingerprints={name:sha(ROOT/name) for name in files}
 for name in files:
  target=BASE/"final_code"/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
 patch=[]
 for name in files:
  old=BASE/"before"/name
  previous=old.read_text().splitlines(True) if old.exists() else []
  patch.extend(difflib.unified_diff(previous,(ROOT/name).read_text().splitlines(True),fromfile="before/"+name if old.exists() else "/dev/null",tofile="after/"+name))
 (BASE/"task_changes.patch").write_text("".join(patch))
 summary=dict(parser_fingerprint="pdfplumber-cells-v23-native-structure-recovery",known_reading_gaps_closed=19,
              fixed_originals=4,new_regression_tests=26,pipeline_negative_control_failures_expected=4,
              original_comparison_documents=14,previous_numeric_cells_preserved=6294,
              corpus=corpus,live_service_scenarios=6,live_database_repaired_cells_verified=19,
              cache_and_original_history_verified=True,full_suite=dict(passed=3473,skipped=147,failed=3,failures_preexisting=True,failures=failures),
              typecheck=dict(status="not_run",reason="mypy is not installed in the available environment"),
              ruff_new_diagnostics=0,dependencies_added=False,deployed_to_existing_services=False,
              existing_jobs_bulk_reanalyzed=False,independent_unseen_acceptance=False,
              universal_pdf_recognition_guarantee=False,changed_files=fingerprints)
 (BASE/"final_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 unbound=[r for r in json.loads((BASE/"frozen_corpus/results.json").read_text()) if r["unbound_money_cells"]]
 (BASE/"source_label_missing.json").write_text(json.dumps(unbound,ensure_ascii=False,indent=2))
 report="""PDF 原生表格结构修复与新会话验收交接（v23，2026-10-15）

本文件承接 outputs/reading_recovery_20261015/验收交接.txt。旧文件是 v22 时点的历史记录，不应再把其中“19 处未修复”当作当前结果。

结果
此前 4 份原件中的 19 处缺口已经实际恢复为财政事实，金额、科目、列与原页逐格对上；这次修复了读取能力本身。
1. 就业促进中心：第 8 页末行四个 0.00、第 11 页 31022 无形资产购置 0.00，共 5 格。
2. 眼病牙病防治所：第 12 页合计行 650.58、374.00、276.58，共 3 格。
3. 精神卫生中心：第 5 页 13,774.36、18,331.49、21,362.29，共 3 格。
4. 机关事务管理局：第 9 页 2080501/2080502/2080505/2080506 对应 38.24/85.93/213.13/113.51，在合计、基本支出两列共 8 格。

如何修复
- 由原生字符索引、实际字符坐标与已有列范围恢复，不按部门名特判、不按科目字典猜金额、不根据合计差额补数。
- 缺线格只接收同一标签行、同一金额列中的唯一原生数字；开口尾行必须有明确科目，且原文在相邻区域没有未纳入的续行。
- 金额续行必须满足两位小数、连续原字符顺序、同格右对齐和科目单行居中等条件；两笔完整金额、横向分开的整数都不能拼成一笔。
- 合并明细按原科目编码基线及名称中心位置拆行；缺数字、科目错位或原字符对不上就不强行分配。
- 原始网格保留在 source_grids，规范后的金额/行列另存；字符范围与原字符 bbox 同时进入物理表、财政事实和证据。新尾行没有原网格，grid_raw_text 保持 null，不会沿用上一行的文字。
- 解析指纹为 pdfplumber-cells-v23-native-structure-recovery，重分析不会命中旧 v22 解析缓存。

验证
- 新增 26 项固定回归通过：22 项真实原件字符/网格快照与反例，4 项实时生成 PDF 经公开解析入口验证。
- 临时禁用恢复入口，4 项 PDF 全流程测试全部按预期失败；证明测试能发现入口失效。当前源代码未禁用该入口。
- 现有 CI 的 python -m pytest 会包含这些测试，不依赖本机原件目录；尚未提交或运行远端 CI。
- 对 4 份问题原件和此前抽查的 10 份原件，共 14 份做修复前后比较：6294 个原先已解析的数值单元格（包括编码）全部保留，19 个修复金额及归属与独立 PyMuPDF 原页读取一致，源码及原文件无漂移。
- 实际隔离 API/Worker/Postgres 运行 6 个场景；4 份原件的读取缺口门禁均已解除，全部 19 个修复金额均从真实数据库回读并与原页对照，物理页、金额文字、bbox、原格身份和字符证明一致。
- 同原件校正后重分析，确认解析缓存回读、原始事实与历史版本保留；不是只测 Python 函数。
- 冻结同版源码重跑已有普陀 377 份、9700 页：原生读取缺口 19 → 0，受此缺口阻塞的事实 287 → 0；原文件未修改，代码无漂移。
- 最终完整测试 3473 passed、147 skipped、3 failed。3 个失败均为本轮之前已复现的旧问题，详情在 final_full_pytest.log；不能称全套测试全绿。
- 新/改动逻辑和新测试 Ruff 检查通过；pdf_parser.py 原有的 6 项 B905 无新增。当前环境未安装 mypy，类型检查未执行，没有为此改依赖。

仍须区分的情况
- 剩余 3 个金额格原件没有标注科目：妇联单位/部门两份第 15 页 454.17，小铃铛幼儿园第 13 页 1,107.19。已再次渲染确认，source_images/unlabelled_*.png 保留原页证据；金额已保存，不猜填科目。
- 这 377 份是已有开发材料的回归，不是未知材料盲测；未为全量原件另外制作独立逐格真值。0 个已检测缺口不能推导未来任意 PDF 100% 准确。
- 本轮没有新增工具、安装模型或降低 OCR 阈值。19 个缺口通过原生读取修复，不依赖 OCR。
- 实际服务验证在可清理的隔离栈完成，资源已清理。没有重启/部署用户既有服务，也没有批量覆盖历史审校任务。旧任务须由加载 v23 代码的服务重新分析后才应用修复。

新会话验收
先读取本文件与 final_summary.json，再使用用户新提供、未参与修复的材料冻结独立原页真值。先验表是否读全、金额是否逐字准确、科目/列是否对应，再看业务规则；未识别、未裁定必须计入分母。新问题先保存原件和验收结果，再改代码。
本轮 4 份/19 格固定回归及 3 个原件缺标签金额须保留，不因新批材料而删除。

证据索引
final_summary.json：最终状态和改动文件 SHA256。
original_verification.json、originals/*/comparison.json：独立原页金额核对及旧数值保全。
service/service_receipt.json、service_readback_verification.json：真实服务、数据库与缓存结果。
service_database/*/*/artifact.json、facts.json、evidence.json：实际数据库回读快照。
frozen_corpus/summary.json、results.json、code_fingerprints.json：377 份回归和源码冻结。
final_full_pytest.log、controlled_no_recovery_final.log：最终回归与禁用入口的负向控制。
source_label_missing.json、source_images/：原件缺科目格与修复金额图像证据。
task_changes.patch、final_code/：本轮差异和最终文件副本，便于复核；原工作区的其他未提交修改均保留。
round1/、round2/：开发中的中间验证记录。随后完善尾行溯源和金额续行的科目绑定约束，再完整重跑；最终以根目录这轮证据为准。
"""
 (BASE/"修复核验与验收交接.txt").write_text(report)
 manifest={str(p.relative_to(BASE)):sha(p) for p in BASE.rglob("*") if p.is_file() and p.name!="artifact_manifest.json" and p.suffix!=".pyc"}
 (BASE/"artifact_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 print(json.dumps(dict(status="closed_known_19_gaps",changed_files=len(files),artifacts=len(manifest),handoff=str(BASE/"修复核验与验收交接.txt")),ensure_ascii=False))


if __name__=="__main__":main()
