# GovBudgetChecker 第八轮整改复核报告

日期：2026-10-05。对象：第七轮整改后的当前工作区。基线：第六轮复核的 R6-01 至 R6-06；核对用户提供的第七轮整改记录，并独立运行当前源码、业务反例、实际导出与隔离服务链。

## 一、复核结论

**工程回归及本轮所测运行、导出链路通过；整体业务验收仍未通过。** 独立重跑为 **2415 passed / 137 skipped / 0 failed**，整改测试为 **197 passed / 2 skipped / 0 failed**。Ruff、环境配置对账、日志检查均通过。整改测试包含在全量测试中，不应相加。

四项原代码反例均已修复；实际主链五个版本视图的四格式原样下载均一致，历史 Word 字节稳定、PDF 数学减号修复成立。两条真实隔离 API→独立 Worker→PostgreSQL 链也通过独立复跑，任务均诚实返回 `review_required`。

不过，新比例解析仍在**显式分母限定词、指标前的主体限定词**两处误绑定，已经直接产生误报。本报告保留 **2 个 P1 代码问题、2 个未完成验收项**。因此不能把本轮结果理解为“只差提供模型和盲测路径，代码已无问题”。OCR 还需要实现可信晋升，独立业务验收还需要完整证据。

P1/P2 是工程处理顺序，不是被审校政府材料的严重度；复现中的金额、单位名称均为明确标识的合成输入，不是对真实单位作出的错误认定。

## 二、版本与独立验证范围

- 分支 `codex/zcode-remediation`，HEAD `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。
- 仍未提交：相对 HEAD，54 个已跟踪文件 +5074/-626；另有3个新增未跟踪源码/测试文件。与第六轮相比，6个既有源码文件改变：比例规则、义务聚合、预算主体、ReviewReport、报告导出、ZC07验证器。
- 当前 tracked diff SHA256：`184db21780180aaecd212880c05c9e1f8981ad5ad758562d708758b3aec83f25`。新增脚本与两份测试另有逐文件指纹，不能只靠此 diff 标识全部实现。
- 第七轮保存的947个文件指纹与当前文件一致；最终服务回执的源码、原件、下载、日志指纹亦核对一致。早期失败或中间服务结果没有被当成最终成功证据。
- 规则/简称/协议检查使用合成合法输入、当前生产函数及有限存储替身；导出使用实际主链、校正、人工决定和下载函数。没有使用源码补丁使审计通过。
- 真实服务复跑使用 localhost HTTP、独立 Worker、临时 PostgreSQL16、随机隔离凭据及临时上传目录，禁用内联回退、云AI和OCR；退出后进程及本轮数据库容器已核验清理。
- 本次业务源码和测试保持只读，仅写复核报告及隔离审计产物；没有重置、提交用户改动，没有使用生产 `.env`、生产数据库或上传目录。

## 三、原问题及额外修复核销

| 第六轮项目 | 本轮独立结果 | 核销边界 |
|---|---|---|
| R6-01 同句相邻指标占比误绑 | 原反例已修复；模块仍有边界 | 事业收入0占0%不再借给财政；100%/99.90%阴性与99.89%错误阳性保留。新问题见 R8-01/02 |
| R6-02 Word/CSV与门禁不一致 | 原契约反例已修复 | 五个实际版本视图均computed且一致；JSON/CSV组件无差异；人决逐ID篡改仍被拒绝 |
| R6-03 部分NA覆盖配对回执 | 原反例及状态契约已修复 | 全NA才整体NA；pass/fail/不足证据与NA混合如实聚合；真实多规则台账相符 |
| R6-04 明示建管委简称被排除 | 原反例已修复 | 100/100的T1检查pass，90/100仍fail；13组主体、年度、阶段、声明重新定义边界符合预期 |
| R6-05 可信OCR自动操作数 | 未完成 | 固定0.75、low_confidence、自动操作数0；安全降级保持 |
| R6-06 冻结盲测及完整四场景 | 部分推进，仍未完成 | 独立复跑两条非OCR服务链；缺冻结分母、可信OCR及其余完整场景 |

额外修复成立：

- **历史 Word 稳定性：** 两份历史视图的六个文件SHA保持一致。另将含10个问题、1个人工决定的真实保存内容输入当前生成器，模拟相隔一天，字节相同，ZIP条目时间固定。不是仅用空报告测试稳定性。
- **PDF 数学减号：** 真实决算服务下载的38页PDF，第26页CMM-007建议完整保留“本期−基期”。MuPDF与Poppler均查看了该相关正文页，公式可读；渲染版本进入派生缓存键。没有改写源PDF。
- **一致计数：** 删除决定标记、更改原因/版本/问题内容均为not_computable，consistent_documents为0；不能拿不可计算结果里的计数冒充通过。
- **逐记录约束：** 仅改DECISION_ID块中的复核人，并在文档其他位置保留旧复核人字符串，门禁仍拒绝对应决定行；证明该边界没有退化为全局关键词存在性。

上述五组是同一合成材料的版本视图，不是五份独立盲测材料；导出样例的一致率不能代表系统真实业务一致率。Word本轮核验了字段和字节，未重新渲染全部Word页面。PDF相关页通过不等于所有阅读器、所有页面均已认证兼容。

有限旧项回归保持：v4→v5重建后二次复用、续页物理格保真、独立采购表不误拼、条件表/年度/预算阶段隔离、历史决定隔离、AI拒绝候选隔离。人工定位仍明确“未应用、需复核”，没有新增自动位置覆盖能力。

## 四、新发现的代码问题

### R8-01 · P1：显式局部分母被子串匹配成总收入/总支出

**合成复现：** 收入合计1000、财政拨款收入600万元，说明“财政拨款收入600万元，占财政拨款收入合计100%”。这里明确使用财政拨款收入合计作为分母，不是本年收入合计。相应支出例为支出合计100、基本支出60万元，说明“基本支出60万元，占基本支出合计100%”。

**实际结果：** 解析均为ok/value100；V33-103、V33-104分别报“表算60% ≠ 文本100%”，比例及汇总回执fail。实际标准表、FactContext、配套规则与DocumentProfile/ledger都参与重放；不是只调用辅助函数观察。

**原因：** [分母判断](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:385>)使用 `any(item in denominator ...)`；白名单“收入合计”匹配进“财政拨款收入合计”，“支出合计”匹配进“基本支出合计”，丢掉了决定口径的限定词。[收入消费](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:2573>)和[支出消费](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:2775>)于是用全体总额重算。

**期望：** 对与默认分母不同的显式口径，先列入口径差异或待人工复核，不能确认其全体占比算错。若业务支持该分母，还需取得对应同口径事实再计算。本例是否符合披露规范需另行判断，本发现只证明当前数值比较用了错误分母。

**整改及验收：** 解析完整分母并规范化指标，最长完整语义匹配保留限定词；仅明确等价的本年总收入/总支出可用于相应默认关系。局部合计、多个或不明确分母转insufficient_data，留下原文及原因。上述两例不得产生60%≠100%的确定性算错；本年总收入60%应pass、59.89%仍fail；基本/项目60%/40%仍pass，55%/45%仍分别fail。0.1个百分点容差保持。

证据：`rules_audit.json → adjacent_boundaries`、独立重放 `root_business_neighbors.json → qualified_fiscal_denominator / expense_qualified_basic_denominator`。

### R8-02 · P1：他单位位于指标之前时，比例仍被绑定到当前单位事实

**合成复现：** 当前文档明确为测试单位决算，表中本年收入合计和财政拨款收入均1000万元；说明“本年收入合计1000万元，财政拨款收入1000万元，教育局财政拨款收入50万元，占50%”。教育局比例不是当前单位财政拨款比例。

**实际结果：** 最近指标选中教育局句中的“财政拨款收入”，claim为ok50；V33-103报当前单位“表算100% ≠ 文本50%”，引用当前单位的事实。提取quote从指标开始，连“教育局”也未保留。若改写为“教育局50万元，占50%”，现有守卫反而能正确转ambiguous/insufficient_data。

**原因：** [主体切换判断](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:373>)仅检查指标末尾到“占”之间的bridge；“教育局”在指标之前，根本未进入检查范围。只绑定最近指标，不等于已经绑定正确主体。

**整改及验收：** 比例声明先绑定指标前的主体限定词，与当前DocumentProfile及有来源的合法别名比较；跨主体不参与当前单位算式。无法确认主体则未完成并保留原文。两个教育局表达均不得产生当前单位比例错误；当前单位显式100%仍pass、真实99.89%仍fail；不能通过排除所有带单位名的句子消除误报。

证据：`rules_audit.json → adjacent_boundaries`、独立重放 `root_business_neighbors.json → foreign_subject_with_repeated_indicator / foreign_subject_without_repeated_indicator_control`。

这两项重放中的ledger为completed，表示执行产生了结果，**不是结果正确或材料无问题**。没有据此计算、推断或改写业务完成率与准确率。

## 五、服务与业务验收缺口

### R8-03 · P2：可信OCR闭环仍需实现和实跑

沿用R6-05。[OCR晋升](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:290>)本轮未修改，候选仍固定0.75、low_confidence、操作数0；歧义输入安全降级有效。ZC07仍未找到 `models/MANIFEST.sha256`。

模型路径只是输入条件。还需接入真实逐格识别分数、数字/单位置信度、原格bbox、主体/年度/阶段验证与模型/原件指纹；分别证明高质量扫描件可形成可追溯操作数、低质量与歧义扫描件继续待复核。不能提高固定分数冒充可信，也不能只补模型清单就核销实现。

### R8-04 · P1：冻结盲测与完整四场景验收仍未齐备

本轮独立复跑 `scripts/run_isolated_service_validation.py`，exit0，7.43秒；真实PostgreSQL16.15、localhost API、独立Worker、正式登录/上传/排队/下载均参与，内联回退关闭。

| 场景 | 输入 | 独立观察 | 业务边界 |
|---|---|---|---|
| native_budget | 明确标识的合成原生预算 | Worker完成事件、当前job SQL结果、原件/下载哈希对齐；四格式computed且一致；重复下载字节相同 | review_required；未评价准确率 |
| native_final | 本地真实《中共上海市普陀区委社会工作部2024年度部门决算》 | 同上；原件SHA256 `44874c062a89c26ea6d94e4e6e7dccdaa136642c2c9bbb560ed21b33039b0597` | review_required；未冻结人工真值 |

每个job均有1条analysis_results；parse_runs/facts/evidence有SQL入库回执。表计数为累积值，不当成每job独占事实数。日志中对应job的100%完成事件与保存回执匹配；API/Worker进程退出、本轮PG容器删除亦已核实。第七轮声称的两条最终服务证据及本轮新下载字节都通过当前门禁。

**两条链均关闭OCR，预算输入还是合成样本，不能核销原要求的四个真实场景。** 当前ZC07 exit2/not_passed：登记29份、冻结人工真值1份、缺陷组3组、原始PDF认证0；无独立冻结清单，未向正式门禁提供完整四场景证据。Docker现已可用，不能继续沿用上轮“daemon不可达”的旧环境判断。隔离pytest未配置测试PG URL，不影响本轮另外完成的真实PG服务复跑。

继续交付开发32份与独立盲测32份，每套8类各至少4份、盲测至少100个人审缺陷组，以及原始PDF哈希、区域真值、适用检查分母；完成原生真实预算、原生决算已知问题、扫描预算、混合决算校正四条服务链。旧开发材料不能充当新盲测。

沿用门槛：precision≥98%、recall≥95%、重大问题无漏报、精确定位≥98%、正式证据真实性100%、越界定位0、确定性适用检查完成≥95%、状态/原因覆盖100%、四格式和重复运行100%一致。**独立业务指标尚未计算，结构化输出保持null**；本轮样例1.0及pytest数量不能填入业务指标。

## 六、测试可信度与下一步交付

| 检查 | 本轮独立结果 | 说明 |
|---|---|---|
| 全量pytest | 2415通过、137跳过、0失败 | 9条警告；跳过不是通过 |
| tests/remediation | 197通过、2跳过、0失败 | PG测试URL及cv2项跳过；6条警告 |
| Ruff / 环境对账 / 日志检查 | 均exit0 | 不替代业务正确性 |
| 实际五个版本四格式 | 均computed且一致 | 同一合成材料，存储替身；不是独立盲测 |
| 两条真实隔离非OCR服务链 | passed，均review_required | 有真实HTTP、Worker、PG证据，边界如上 |
| ZC07正式验收 | not_passed，exit2 | 分母、模型及完整四场景不足 |

新增28项测试进入当前全量回归。18项比例/聚合测试、7个简称参数用例与3项导出测试没有发现调宽阈值或删除原反例。新增测试确实覆盖实际规则回执、台账和下载；仍遗漏“局部分母包含白名单子串”和“他主体在最近指标之前”两个边界。金额政策/数学模块未改变。本轮未重跑前端构建或浏览器端到端；前端没有本轮变化。

下一轮先修 **R8-01/02**，固化同口径/同主体阳性、阴性与未完成三类回执，不要用全部过滤或全部NA代替绑定。随后完成 **R8-03** 的可信OCR实现，再以 **R8-04** 的独立真值和完整链路作业务验收。导出与历史稳定性已核销的原反例只需保持回归，不应重复当作未修。

人工位置覆盖仍未实现，当前明确未应用；若正式试用要求修改位置生效，还需版本化位置/证据重新绑定、原PDF引文认证、四格式同步及旧版本隔离。

## 七、证据交付

- [结构化问题JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/findings.json>)、[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/findings.csv>)：2个代码问题、2个验收缺口；[原问题核销台账](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/remediation_ledger.json>)。
- [主审独立业务反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/root_business_neighbors.json>)及同名Python脚本；[规则完整审计](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/rules_audit.json>)、[简称/解析审计](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/parse_audit.json>)、[导出/校正/AI审计](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/evidence_audit.json>)。
- [原样下载门禁](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/production_export_gate.json>)、[导出稳定性/篡改补验](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/export_supplement.json>)、[实际版本下载](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/export_probe>)、[合成门禁反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/root_gate_probes.json>)。
- [新实跑服务回执](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/live_services/service_receipt.json>)、[服务证据交叉验证](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/live_service_evidence_integrity.json>)、[第七轮最终服务证据校验](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/prior_service_evidence_integrity.json>)、[PDF相关页检查记录](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/live_pdf_math_qa.json>)。
- [全量日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/full_pytest.log>)、[整改日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/remediation_pytest.log>)、各命令回执和[正式门禁](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round8/zc07_preflight.json>)。
- `source_snapshot_before/after.json`、`round6_comparison.json`、`working_tree.patch`及`artifact_manifest.json`：版本、首尾源码保护及产物指纹。
