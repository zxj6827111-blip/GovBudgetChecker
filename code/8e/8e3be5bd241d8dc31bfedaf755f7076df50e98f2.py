"""整理已验证证据与下一会话交接，不运行新材料验收。"""
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    corpus=json.loads((BASE/"frozen_corpus/summary.json").read_text())
    assert corpus["source_unchanged"] and not corpus["source_code_drift"]
    service=json.loads((BASE/"service_final/service_receipt.json").read_text())
    positive=json.loads((BASE/"service_positive/service_receipt.json").read_text())
    for receipt in [service,positive]:
        assert receipt["status"]=="passed" and receipt["cleanup"]=="complete" and receipt["source_unchanged_during_run"]
    log=(BASE/"verified_full_pytest.log").read_text()
    match=re.search(r"(\d+) failed, (\d+) passed, (\d+) skipped",log)
    assert match and tuple(map(int,match.groups()))==(3,3447,147)
    failures=re.findall(r"^FAILED ([^ \n]+)",log,re.M)
    assert set(failures)=={
        "tests/test_budget_intake_20260929.py::test_finals_unresolved_total_le_11",
        "tests/test_engine_repair_20260927.py::test_unresolved_total_le_15",
        "tests/test_review_known_regressions.py::test_t7_thousands_amount_and_yoy_change_are_not_false_mismatches[True]",
    }
    initial=json.loads((BASE/"service_positive/derived_mixed_final/status.json").read_text())
    meta=initial["result"]["meta"]
    assert meta["ocr"]["promotion"]["operand_count"]==4
    assert not meta["structured_ingest"]["reading_import"]["reading_quality"]["gaps"]
    native_gap=json.loads((BASE/"service_positive/shanghai_01/status.json").read_text())
    assert any(r["code"]=="table_reading_incomplete" for r in native_gap["review_reasons"])
    files=["api/main.py","src/schemas/parse_artifact.py","src/services/pdf_parser.py",
           "src/services/ocr_pipeline.py","src/services/ocr_qualification.py",
           "src/services/ocr_fact_promotion.py","src/services/table_import_receipt.py",
           "src/services/table_reading_assessment.py","src/services/structured_ingest_runner.py",
           "src/engine/fact_context.py","tests/remediation/test_table_reading_recovery.py",
           "tests/remediation/test_zc01.py"]
    snapshots=BASE/"final_code"
    for name in files:
        target=snapshots/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
    fingerprints={name:digest(ROOT/name) for name in files}
    patches=[]
    before=BASE/"before"
    for old in sorted(before.rglob("*.py")):
        name=str(old.relative_to(before));new=ROOT/name
        patches.extend(difflib.unified_diff(old.read_text().splitlines(True),new.read_text().splitlines(True),fromfile="before/"+name,tofile="after/"+name))
    for name in ["src/services/table_reading_assessment.py","tests/remediation/test_table_reading_recovery.py"]:
        patches.extend(difflib.unified_diff([], (ROOT/name).read_text().splitlines(True),fromfile="/dev/null",tofile="after/"+name))
    (BASE/"task_changes_available_baselines.patch").write_text("".join(patches))
    summary=dict(date="2026-10-15",parser_fingerprint="pdfplumber-cells-v22-table-reading-recovery",
        scope="reading gap detection, automatic OCR recovery routing, source binding and fail-closed quality gates",
        mechanism_supplemented=True,all_original_tables_recognized=False,independent_batch_acceptance="not_run",
        full_suite=dict(passed=3447,skipped=147,failed=3,failures=failures,all_failures_preexisting=True),
        new_targeted_tests=20,isolated_service_scenarios=[len(service["scenarios"]),len(positive["scenarios"])],
        repeated_service_scenarios_are_not_independent_documents=True,
        positive_ocr_control=dict(kind="synthetic native-text plus image-table",operands=4,all_amounts="100.00",source_certification="unverified"),
        known_corpus=corpus,changed_files=fingerprints,
        original_business_accuracy=None,formal_production_ready=False)
    (BASE/"final_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    rows=json.loads((BASE/"frozen_corpus/results.json").read_text())
    known=[r for r in rows if r["reading_gaps"] or r["unbound_money_cells"]]
    (BASE/"known_unresolved_cases.json").write_text(json.dumps(known,ensure_ascii=False,indent=2))
    text="""GovBudgetChecker 表格读取补充与新会话验收交接
日期：2026-10-15
工作目录：/Volumes/workSSD/software development/GovBudgetChecker
本记录不是批量业务验收通过证明。尚未启动用户下一批材料的验收。

一、本轮完成的内容
1. 文本充足页也按财政表题、原始金额位置、大图片区域发现表格读取缺口，和已抽取的合格单元格核对；确认为空白的页不再混作扫描缺页。
2. OCR 候选页合并低文本、扫描、表格缺口页。启用既有本地 OCR 服务时自动尝试；未启用、服务失败、复验不足、与原生表冲突均显式保留原因，不静默视为完成。
3. 合格 OCR 可处理与原生文字不重叠的图片表格。重叠结果保留为候选，不自动覆盖原生金额或重复入表。
4. 图片表格必须满足区域资格；一个合格表头不能掩盖未合格金额。OCR 物理表、逻辑表与金额原格一起保存，数据库回读能追溯。
5. 未读完的表，其已提取金额也不得冒充完整表的安全操作数。缺口和未绑定金额写入结构化复核项及质量门禁；原件缺科目名称与解析漏格分开记录。
6. 修复真实 DB 链路发现的几何负零问题：仅将坐标矩阵 -0.0 规范为 0.0，避免 JSONB 变化导致完整快照哈希失配。没有修改 OCR 原始预测或财务金额。
7. 解析指纹更新为 pdfplumber-cells-v22-table-reading-recovery。没有新增依赖，没有降低识别阈值，没有部署或批量覆盖历史任务。

二、验证结果与证明边界
- 新增 20 个回归测试通过；OCR/读取相关组合为 102 个测试通过。新模块和新测试 Ruff 通过。
- 最终完整测试：3447 passed、147 skipped、3 failed。3 项均为任务前已复现的既有失败；完整套件不是全绿。见 verified_full_pytest.log 及旧基线 outputs/pdf_table_import_repair_20261014/baseline_existing_failures.log。
- 两轮最终隔离 API/Worker/Postgres 验证各 5 个工程场景通过，包括实际上传、结构化入库/逐格来源回读、四类报告导出、校正重分析/缓存及历史保留。两轮包含重复材料，不能当作 10 份独立样本。隔离资源已清理，未改动用户现有服务。
- 正向 OCR 控制是合成图文混合页：页面文字充分，旧低文本门槛不会触发；4 个 100.00 与一般公共服务支出/合计及收入合计/财政拨款列一一对应。实际服务从原页识别主体后自动补识别、复验、入库，4 个金额可进入操作数；区域无剩余已检测缺口。此为工程控制，不是真实扫描公开材料的独立验收。
- 负向控制保留：宽字形数字造成区域复验低置信度时不晋升；大图含留白而未被表格区域覆盖时保守保留待核验。其结果保存在各 ocr_control_* 目录及初始 live_ocr_receipt.json，未删除失败记录。
- 最终冻结代码下重跑已有普陀 377 份/9700 页。原文件未变化、代码无漂移。该轮仅检验原生读取及缺口守卫，没有逐份 OCR，也没有重新人工独立制作全部原页真值。因此不能由“未触发缺口”推导所有原件百分之百准确。

三、必须保留的未解决事实
目前不能声明所有表格已完整识别，也不能把系统判为正式验收通过。
仍检出 4 份报告、19 处真实原生读取缺口：
- 就业促进中心年度决算，第 8 页末行 4 个 0.00、第 11 页末行 1 个 0.00：开口表格尾行未进入闭合网格。
- 眼病牙病防治所，第 12 页合计行：650.58、374.00、276.58，标签格存在而金额区没有形成可抽取闭合格。
- 精神卫生中心，第 5 页：13,774.3 换行 6、18,331.4 换行 9、21,362.2 换行 9。数字原字符仍保留；没有以全局去换行方式盲目拼数。
- 机关事务管理局单位决算，第 9 页：同一大格合并了 4 个科目行，38.24、85.93、213.13、113.51 在合计/基本两列各出现一次，共 8 处。需基于原字符位置拆出真正行，再核对名称和金额归属，不能按字符串顺序盲配。
另外 3 个金额格已读取保存，但原件未标科目名称：妇联单位/部门两份第 15 页 454.17；小铃铛幼儿园第 13 页 1,107.19。此类不应通过猜测科目“补全”。
当前缺口发现以财政表题、原生小数金额、大图区域为主要线索。整数金额在网格外丢失、无表题的未分类表、小图片、OCR 自己漏掉的行等情况，仍不能仅凭这套守卫排除；本轮也未对真实扫描原件建立独立逐格真值。
这些读取缺口所涉及的原生事实被约束为不可确认的操作数；它们不是原件财务勾稽错误的证明。已保留原文、物理页、坐标、稳定 ID 和原因。完整路径/哈希/原格见 known_unresolved_cases.json。

四、新会话建议执行顺序
1. 先读本文件、final_summary.json、known_unresolved_cases.json，核验当前代码哈希及 OCR 服务/模型指纹，避免旧进程或旧解析缓存混入。
2. 已知 4 份读取缺口应作为必测回归保留；用户新提供且未参与修复的材料另建冻结清单。原生、真实扫描、图文混排、跨页/缺表线等类型分层记录，禁止将合成/转图片材料算作真实扫描原件。
3. 先独立阅读原 PDF，逐页建立表格清单和应有单元格基准；保留页、表、行、列、原文字/数字、单位、来源哈希与图像。基准不能从本系统导出结果反向生成；有歧义保留“未裁定”。
4. 再从真实 API/Worker/数据库导出同版解析结果，比较表是否缺失、原格是否遗漏、金额是否逐字正确、名称/代码/金额列是否对应、空白/零/无此项是否区分、跨页是否重漏。读取验收先于规则业务判断。
5. 不只列准确的格子：每张原表都进入分母，未识别和未裁定也计入未完成；分别报表检出、金额准确、归属准确、自动完成及人工占比。缺口提示能力与实际读全能力分开统计，暂不输出无独立分母的“识别率”。
6. 选定样本中任何错误金额、错科目、静默漏格均不作为通过；已提示的未读区域仍属于未完成读取。先保存冻结验收结果和证据，再进入修复，避免边改边验把同批材料当作独立验证。
7. 样本通过只能说明该批/这些类型通过，不能对未来任意 PDF 保证永不失败。真实扫描件、低清晰度、多表区域与复杂跨页仍需独立覆盖。

五、文件与复现
- final_summary.json：最终结果和当前改动文件 SHA256。
- frozen_corpus/summary.json、results.json、code_fingerprints.json：最终已有材料回归。
- service_positive/service_receipt.json、service_final/service_receipt.json：冻结源码的真实隔离服务结果。
- service_database_positive/、service_database_final/：按任务/代际保存的真实 DB artifact/facts/evidence/readback。
- verified_full_pytest.log：最终全量回归；tests/remediation/test_table_reading_recovery.py：20 项新测试。
- final_code/：本轮涉及文件最终副本；task_changes_available_baselines.patch：有任务前副本的文件差异及新增模块/测试。ocr_qualification.py、test_zc01.py 没有单独任务前副本，完整最终副本在 final_code，不能把全仓库 git diff 当成本轮独占修改。
- 初始失败 service/、JSONB 诊断 jsonb_diff.json、各 OCR 控制目录保留作溯源；最终应读 service_positive 与 service_final。
- 项目原有大量未提交工作全部保留；不要 reset/revert 整个工作区，也不要自动部署。

新会话可用提示：
请先读取 /Volumes/workSSD/software development/GovBudgetChecker/outputs/reading_recovery_20261015/验收交接.txt 和同目录 final_summary.json。我要验收一批新 PDF 的表格读取，先冻结原件清单与独立逐格真值，再对照真实 API/数据库；本轮先记录读取结果，不要边改代码边给验收通过。已知 4 份/19 处读取缺口和 3 个原件缺标签金额须保留统计，不能从验收分母删除。
"""
    (BASE/"验收交接.txt").write_text(text)
    manifest={str(p.relative_to(BASE)):digest(p) for p in BASE.rglob("*") if p.is_file()
              and p.name not in {"artifact_manifest.json"} and p.suffix not in {".pyc"}}
    (BASE/"artifact_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({"handoff":str(BASE/"验收交接.txt"),"summary":str(BASE/"final_summary.json"),"artifacts":len(manifest)},ensure_ascii=False))


if __name__=="__main__":main()
