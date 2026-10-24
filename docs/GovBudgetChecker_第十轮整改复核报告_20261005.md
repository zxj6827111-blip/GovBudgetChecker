# GovBudgetChecker 第十轮整改复核报告

复核日期：2026-10-05。对象：第九轮整改记录及当前本地工作区；依据：第八轮复核要求与用户提供的 `v3_3_portable` 规则。本报告审核系统实现与验收证据，不是对某一份政府公开材料作最终审校结论。

**结论：原比例反例已经修复，工程回归和两条非OCR服务链通过；仍发现两个可复现的比例绑定缺陷，整体业务验收继续不通过。** 新增OCR任务文档和冻结材料模板属于实施准备，不能代替功能交付或独立业务验收。

## 一、本轮验收结论

| 项目 | 独立复核结果 | 可据此确认的范围 |
|---|---|---|
| 原R8-01/02九个反例及阳性对照 | 全部符合整改预期 | 原局部分母与教育局主体反例可核销；不代表所有主体/年度表达都正确 |
| 新增比例绑定测试 | 50项通过，已进入全量回归 | 未发现删除原反例、放宽金额/比例容差；遗漏本报告R10-01/02 |
| 全量pytest | **2465通过、137跳过、0失败**，9条警告 | 工程回归；跳过项目不算通过 |
| tests/remediation | **247通过、2跳过、0失败** | PG测试URL项与cv2项跳过；另行完成真实PG服务实跑 |
| Ruff、环境对账、日志检查 | 均exit0 | 不替代业务准确率验证 |
| 画像传递 | 生产函数有限复放8/8符合预期；新增spawn测试通过 | legacy、隔离进程、EngineRunner中保留画像与本单位证据身份；非完整OCR/盲测证明 |
| 实际下载与历史版本 | 本轮5个版本视图的四格式均一致；历史版本字节稳定 | 5个视图来自同一合成预算材料，不是5份独立样本 |
| 新隔离服务实跑 | 两条API→独立Worker→PostgreSQL16.15链路通过 | 都是非OCR，终态均为review_required；预算输入为合成材料 |
| ZC07正式门禁 | **not_passed，exit2** | 独立冻结分母、可信OCR及完整四场景不足；业务指标不可计算 |

本轮未改业务源码、测试或用户整改材料。审查版本为分支 `codex/zcode-remediation`、HEAD `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`；验收的是未提交工作区，不能仅凭HEAD复现。累计54个已跟踪文件有改动，另有4个未跟踪脚本/测试纳入源码保护。开工差异指纹为 `329b1d80264316da1f8df185b636d188074a064d497690bf7b88f590b774588b`。相对上轮新增主业务改动是 `api/main.py`、`rules_v33.py`、`engine_rule_runner.py`，另有验收文档和50项测试。

## 二、已核销的整改

### R8-01：原显式局部分母误绑已修

完整分母经过归一化后精确匹配，不再因为“财政拨款收入合计”包含“收入合计”就借用本年收入总额。原收入局部分母、支出基本支出局部分母均转 `insufficient_data`，没有占比问题；支出的另一条有效关系继续 `pass`。

阳性和阴性对照保留：本年收入总额口径60%通过，59.89%失败；基本/项目支出60%/40%分别通过，55%/45%分别失败；有一条合法比例、另一条未披露时分别为 `pass` / `not_applicable`。没有用全部过滤或全部NA消除误报。

### R8-02：原教育局反例与画像传递已修，扩展边界尚未闭合

原“教育局财政拨款收入50万元，占50%”不再与本单位事实比较，回执为 `insufficient_data`，无比例问题。全称、可追溯简称、未知画像及旧上下文回退在有限检查中符合预期。

本单位99.89%与已识别的教育局50%同句时，仍检出本单位错误；对应finding、回执 `issue_refs`、操作数事实ID和比例引文保持本单位身份。API把已解析画像挂入Document，dual上下文携带同一序列化画像，EngineRunner恢复画像；真实spawn测试证明画像可跨进程传递。

这里可核销的是原输入和上述传递契约。下节两条新反例说明主体与年度绑定仍不能整体宣称完成。

## 三、仍需修复的问题

以下代码反例均为可复现的合成文本、合成表格与合成已核验事实，调用实际规则、画像、台账和关系回执。本轮未为这些文本制作可认证原始PDF；`page=1`只是测试页，`bbox=null`，证据保持 `unresolved/unverified`，不得作为真实材料精确定位成果。代码优先级P1/P2/P3与材料问题严重度是两套概念。

### R10-01 · P1：年度数字使未知主体检查失效

位置：[rules_v33.py:432](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:432>)；实体匹配范围见[第401行](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:401>)。

2025年“测试局”的当前事实为本年收入1000万元、财政拨款收入1000万元。在说明中加入：

> 第二实验中学2025年财政拨款收入50万元，占50%。

预期：该学校主体没有与本单位画像确认，不参加本单位算式；此声明转 `insufficient_data` 并保留学校与年度。实际：比例关系 `fail`，产生“表算100.00% ≠ 文本50%”；问题引文只剩“财政拨款收入50万元，占50%”，丢失学校和年度。改为“2025年第二实验中学……”同样复现；删去2025年则正常转未完成。

根因是“第二实验中学”未命中有限实体后缀，后备检查又用“存在任意数字”跳过未知限定词。年份不是主体已确认的依据，当前状态于是保持隐含本单位。

同一根因还有漏检后果：本单位99.89%的独立错误声明与上述学校50%同句，被当成相互冲突的本单位比例，整体转 `insufficient_data`，不再输出本单位比例错误。此处指规则层问题被抑制，不是伪称整份报告已经通过。

整改要求：分别解析年度、金额和主体，不用任意数字证明主体已解析；未知主体不得进入本单位已接受声明集合。证据片段必须保留决定主体/年度的原文。补充带/不带年度、年度前置/后置、已确认本单位真错误与他单位同句的回执及证据断言。

### R10-02 · P1：往年口径跨逗号数字分句丢失

位置：[rules_v33.py:425](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:425>)。主体在逗号段之间继承，年度却只从紧邻指标的最后一段读取。

仍使用2025年本单位收入/财政拨款各1000万元的事实。输入：

> 2024年本单位上级补助收入0万元，事业收入0万元，财政拨款收入800万元，占80%。

预期：保持2024年上下文；没有对应往年事实时，不用2025年事实判错。实际：比例关系 `fail`，输出“表算100.00% ≠ 文本80%”；引文保留了本单位，却丢失2024年。直接写“2024年本单位财政拨款收入800万元，占80%”能正确降级，中间加入数字分句后年度检查失效。

支出同类表达也复现：2024年人员经费、公用经费分句后，基本/项目支出各50%的声明分别被拿去和2025年60%/40%比较，两条关系均失败。改用2025年及本年正确比例的对照仍通过。

整改要求：主体、年度、阶段使用同一套继承与显式重置作用域；同时检查整条金额和比例关系，不能只给百分比再加一个末段年份判断。有相应往年事实时按同年同口径比较，否则保留原文并转未完成；证据要包含年度锚点。补收入、基本/项目支出以及显式切回本年的测试。

上述37条邻近检查中有5条契约偏差，归属于这两个根因。主审独立重放得到相同结果。金额0.50/0.51万元、比例0.10/0.11个百分点边界仍保持；这些计数不是业务误报率。

### R10-03 · P2：真值组配额与评测分组契约不一致

新[冻结材料要求第20行](</Volumes/workSSD/software development/GovBudgetChecker/docs/冻结盲测材料要求_20261005.md:20>)要求按每份文档原始 `truth_id` 去重，并建议加doc_id前缀；[配额实现](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:302>)也按原始ID计数。[评测分组](</Volumes/workSSD/software development/GovBudgetChecker/scripts/evaluate_golden_corpus.py:366>)却剥离所有尾部小写字母。

直接调用正式函数：`DOC-001-income` 与 `DOC-001-expense` 是两个不同ID，配额口径计2组，指标口径都变成 `DOC-001-`，只计1组。原T4a/T4b多证据面兼容约定被扩展成任意英文结尾，容易把不同缺陷意外合组，改变召回分母和漏报统计。

整改要求：定义明确的canonical真值组身份，让材料配额和指标使用同一分组函数；优先将组身份与独立annotation_id分开，不靠任意英文尾缀猜测。保留旧多证据面数据的明确兼容办法，文档给出合法/非法命名例。独立缺陷和同缺陷多证据面都需契约测试。本轮只证明分组不一致，没有证明完整正式门禁已被绕过；不得根据当前输出事后合并盲测真值。

### R10-04 · P3：精确定位分母文档表述过宽

[ZC07数据格式第76行](</Volumes/workSSD/software development/GovBudgetChecker/docs/ZC07_真实验收数据格式.md:76>)写“每个真值定位框都进入分母”；[实际实现](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:883>)仅取 `defect` 标签。纯函数输入1个defect、1个acceptable、1个manual_review及各自框，实际 `truth_total=1`。

按现行验收口径应明确写“defect标签的真值定位区域”，解释多证据标注与问题组指标的区别；其他标签作为单独诊断，不混入正式错误定位分母。若业务决定改变分母，需先冻结新口径并同步实现、文档和测试。本轮没有为此改变门槛。

## 四、仍未核销的验收缺口

### R10-05 · 沿用R8-03，P2：可信OCR尚未交付

[OCR晋升实现](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:290>)仍固定0.75、low_confidence，不形成确定性勾稽操作数；相关源码与上轮相同。ZC07仍报告缺OCR模型清单。新增[8项OCR实施任务](</Volumes/workSSD/software development/GovBudgetChecker/docs/OCR_可信闭环实施任务_20261005.md>)明确区分操作数资格与原件证据认证，方向合理，但本轮交付的是任务定义。

后续必须实现真实逐格识别分数、数字/单位置信度、原格bbox、主体/年度/阶段验证、模型与原件指纹及可追溯晋升。用高质量扫描材料证明可形成可信操作数，用低质量/歧义材料证明继续待复核；不能提高固定分数冒充可信，也不能只提交模型目录核销代码闭环。无文本层扫描件的原件认证须有独立可审计依据，不把模型候选自动视作verified。

### R10-06 · 沿用R8-04，P1：独立冻结材料与正式四场景仍不齐备

本轮独立服务运行7.91秒，使用新隔离PostgreSQL16.15、localhost API、独立Worker及正式上传/排队/下载。内联回退与OCR关闭；两场景都有Worker认领、100%完成事件、当前job数据库结果、同版导出哈希及重复下载证据。

| 场景 | 输入 | 本轮观察 | 限制 |
|---|---|---|---|
| native_budget | 明确标识的合成原生预算 | 1条analysis_results；四格式一致、重复下载字节一致 | 合成材料；review_required；未测业务准确率 |
| native_final | 真实《中共上海市普陀区委社会工作部2024年度部门决算》 | 同上；原件SHA与下载、SQL及日志对齐 | 没有独立冻结人工真值；review_required |

真实决算原件SHA-256为 `44874c062a89c26ea6d94e4e6e7dccdaa136642c2c9bbb560ed21b33039b0597`。parse_runs/facts/evidence表计数是累积值，不是每个job独占事实数。两条链只证明本轮工程路径；API/Worker停止与隔离容器清理已独立核实。

第九轮声称的最终服务原件、任务、分析版本、5项代码指纹、日志与四格式文件哈希全部对齐。用当前门禁重新读取这些原样字节，与本轮新实跑、5个下载视图合计9套四格式，均computed且一致。这个1.0只属于这些导出一致性检查。

当前预检登记29份材料、1份人工复核冻结真值、3个缺陷组、原始PDF认证0；没有正式开发/独立盲测清单和完整四场景证据。新模板可解析，正式validators均返回 `incomplete`，与第九轮自行记录一致，没有把空模板标成验收通过。

继续交付开发32份、独立盲测32份，每套预算/决算×部门/单位×原生/扫描8类各至少4份，盲测至少100个人审真实缺陷组；提供原件哈希、区域真值、独立适用检查分母和人工复核/近重复审查记录。完成原生真实预算、原生决算已知问题、扫描预算、混合决算校正四个真实服务场景。已用于定位错误或调参的材料不能充当新的独立盲测。

沿用门槛：正式问题precision≥98%、真实问题recall≥95%、重大问题无漏报、精确定位≥98%、正式证据真实性100%、越界定位0、确定性适用检查完成≥95%、状态/原因覆盖100%、四格式与同源同配置同版本重复运行100%一致。业务指标仍不可计算，结构化输出为null。

人工位置覆盖仍明确未应用，当前回执诚实失败并提示复核。若试用要求人工改框生效，需要另行交付版本化位置/证据重绑定及四格式、历史版本同步；不把这个既知能力限制冒称已实现。

## 五、下一轮执行及验收条件

1. 修R10-01/02：把比例的主体、年度、阶段、分母和证据范围作为完整声明绑定。固化本报告5个失败用例及当前通过的阳性、阴性、unknown、旧年度和本年重置对照；本单位真实错误不得被他单位声明抑制。
2. 修R10-03/04：先统一真值组和精确定位分母契约，再依文档准备材料；100组配额与召回指标应使用一致身份。不得借命名规则调整现有盲测标签来迎合结果。
3. 执行可信OCR任务并提交实跑证据：高质量材料可形成可信操作数；低质量与歧义材料保持未完成；候选识别与原件认证分别证明。
4. 用独立冻结材料和完整四场景做正式业务验收，保留原样下载、版本/配置指纹、Worker与PG回执和重复运行。继续回归已核销项，未完成项不得填成pass或0样本100%。

当前可继续在人工复核下测试原生文本材料；**不满足全面正式使用的业务验收条件**，尤其不能承诺任意扫描预算/决算都能自动准确判断并精确定位。后续是否可正式使用，以独立业务门禁结果决定。

## 六、证据及边界

- [结构化问题JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/findings.json>)、[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/findings.csv>)、[原项核销台账](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/remediation_ledger.json>)：2个代码缺陷、2个验收契约/文档问题、2个沿用验收缺口。
- [规则完整证据](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/rules_audit.json>)、[主审独立复放](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/root_rules_replay.json>)及同名Python脚本；[画像传递审计](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/context_audit.json>)。
- [验收材料审计](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/acceptance_audit.json>)、[主审契约反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/root_acceptance_contract_probe.json>)及同名脚本。
- [新服务回执](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/live_services/service_receipt.json>)、[服务证据交叉核对](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/live_service_evidence_integrity.json>)、[原样四格式门禁](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/production_export_gate.json>)、[历史稳定性与篡改补验](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/export_supplement.json>)、[下载/校正过程](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/export_chain.json>)。
- [全量日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/full_pytest.log>)、[整改日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/remediation_pytest.log>)、[正式预检](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round10/zc07_preflight.json>)、首尾源码快照、working_tree.patch和artifact_manifest.json。

画像复放包含真实规则函数/子进程，但使用合成已核验事实；部分EngineRunner检查使用空白PDF及预注入文本/表/事实，并仅缩选规则集。5个下载视图使用测试身份/存储边界替身；两条隔离服务另行证明真实HTTP、Worker、PG主链，不能把两种证据混称为完整业务盲测。PDF附录已渲染检查；Poppler缺原始CID字体映射时用MuPDF复核。本轮未专门重做数学符号专项输出。前端本轮未改，未重复运行浏览器验收；也未重新评价真实云端AI增益或宣称完整安全扫描通过。
