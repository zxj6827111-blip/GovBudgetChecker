# GovBudgetChecker 第六轮整改复核报告

复核日期：2026-10-05。对象：用户完成第五轮修改后的当前工作区。依据：第五轮复核的 R5-01 至 R5-10，以及 AGENTS.md 的 `v3_3_portable` 误报控制与证据原则。

## 一、验收结论

**工程回归通过，业务验收仍未通过。** 当前源码直接运行得到 **2387 passed / 137 skipped / 0 failed**；整改测试 **169 passed / 2 skipped / 0 failed**；Ruff、前端单测与构建均通过。整改测试包含在全量测试内，两组数字不能相加。

上轮 8 个代码问题中，**7 个原反例已验证修复**。实际导出的一致性契约仍未通过；另外发现占比借用相邻指标、部分不适用关系错误覆盖配对回执、本单位显式简称被排除三个边界。本报告保留 **4 个代码整改项、2 个未完成验收项**。P1/P2 表示工程处理顺序，不是政府材料问题的业务严重度。

这轮已明显改善“检出了但回执说通过”“拒绝的 AI 候选却参加 agreement”“定位未应用却说 applied”等可信度问题。当前适合继续在人工复核下试用和整改；仍不能承诺任意预算/决算、特别是扫描件，都能自动准确检查并精确标记全部问题。

## 二、版本与验证边界

- 分支：`codex/zcode-remediation`；HEAD：`9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。
- **改动仍未提交**：相对 HEAD，54 个已跟踪文件约 +4838/-585。与第五轮相比，原有 13 个修改文件再次变化，另有 3 个文件进入修改清单，38 个原修改文件不变。
- 工作区 diff SHA256：`94bfab0161f67b4dc42179c76ac327524b197a676e785f8114d6922925117c35`。同一 HEAD 不代表两轮工作区内容相同。
- 并行审计解析、规则、证据/校正/AI，主审独立复验门禁及原样下载字节。运行当前源码，没有临时修业务代码以使测试通过。
- 缓存/存储探针使用内存 SQL 协议替身；AI 使用合成模型响应并调用实际构建、契约和合并接口；校正/下载使用隔离任务目录及实际处理函数。它们不能替代真实 HTTP 上传、Worker、PostgreSQL、OCR 模型联调。
- 测试进程禁用 dotenv、真实数据库与云端 AI。未读取 `.env`、`.mimosa/` 或冻结盲测原件；未安装模型、未启动部署、未重置或提交用户改动。
- 前后源码指纹检查结果见证据目录；本次写入仅为复核报告及隔离审计产物。

## 三、第五轮问题逐项核销

“原反例修复”只覆盖所测契约；同模块的新边界另列，不能据此宣称整个模块已通过独立业务验收。

| 上轮编号 | 本轮结果 | 验证依据及边界 |
|---|---|---|
| R5-01 占比漏报、失败回执仍 pass | 原反例已修；相邻边界未关闭 | “占/占比99.89%”均检出并关联比例及汇总 fail；100%/99.90%保持通过。相邻指标误绑见 R6-01；部分不适用聚合见 R6-03 |
| R5-02 V33 作者缺身份 | 原反例已修 | V33-103/104/214 的 relation/fact/evidence ID 经 legacy、engine、ReviewReport 六条路径保持，失败回执引用对应问题。合成证据 ID 仅验证传输，不认证原始 PDF |
| R5-03 三公混合取数伪称事实完成 | 原反例已修 | 四个必需角色及来源明确；旧表与事实混合时标记 legacy/pending；旧表合计30→31仍正确改变勾稽结果 |
| R5-04 新语义复用旧 v4 缓存 | 原反例已修至存储协议层 | 指纹升级 v5；安装旧 v4 run1，第一次重建 v5 run2，第二次复用 run2，物理格/事实/证据保真。未连接真实 PG |
| R5-05 他单位教育局简称串值 | 原反例已修；简称边界未关闭 | 他单位教育局/财政局被排除，本单位相应合法短名保留，90对100真实差异仍检出；明示建管委简称见 R6-04 |
| R5-06 拒绝 AI 候选参加 agreement | 原反例已修至生产接口层 | 混合非法 ID、非法值、空证据、错配事实证据持续隔离；合法候选仍 agreement，强规则保留。未调用云模型 |
| R5-07 实际四格式与门禁不兼容 | 部分修复，仍开放 | PDF 的标记顺序、人工决定、记录类型和跨页页眉旧问题已修；Word/CSV 仍不一致，见 R6-02 |
| R5-08 人工定位未应用却报 applied | 旧状态错误已修 | 实际主链改为 failed/applied=0/blocking=true，并明确 correction_overlay_incomplete；自动应用人工定位仍不支持 |
| R5-09 可信 OCR 闭环 | 未完成 | 安全降级有效，但可信自动操作数未证明，见 R6-05 |
| R5-10 冻结盲测与真实服务验收 | 未完成 | ZC07 仍 not_passed，见 R6-06 |

旧已关项回归保持：年初/调整预算与跨年隔离、条件表控制、独立采购表不误拼、合法续页物理格保存、部分列映射保留其他事实、校正依赖映射、历史决定隔离、单关系 AI 故障隔离及门禁反例。

## 四、剩余代码整改项

### R6-01 · P1：占比提取仍借用同句后一个指标，造成误报

**合法输入（合成审计样本）：** 表中本年收入合计1000、财政拨款收入1000、事业收入0万元；说明为“本年收入合计1000.00万元，其中财政拨款收入1000.00万元，事业收入0.00万元，占0.00%。”末尾0%描述事业收入。

**实际结果：** V33-103 把0%绑定财政拨款，产生“财政拨款收入占比：表算100.00% ≠ 文本0.00%”，规则及对应比例/配对回执均 fail。真实 DocumentProfile、标准九列收入表和实际 V33-103/V33-226 路径同样复现。把财政句与事业句用分号隔开，或显式写财政占100%，均不误报。

**原因与位置：** [提取器](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:330>)按句号、分号等分句，但从指标末尾一直搜索到句末，没有在下一个指标处终止；[收入占比调用](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:2513>)因此拿到事业收入比例。函数注释声称不借相邻指标，实际边界尚未成立。

**整改与验收：** 比例声明应绑定最近的适用指标、分子/分母和原文范围，遇到另一指标或主体时停止跨越；不能简单把所有逗号都切断，合法“财政拨款收入1000万元，占100%”仍须识别。保留两种99.89%错误阳性、100%/99.90%阴性，并新增“财政无比例、事业占0%”“财政占100%、事业占0%”“多个指标各自错误”的用例。无法确定归属时转待人工复核。

证据：`rules_audit.json → neighbor_indicator_ratio_probe`、`actual_obligation_ledger_ratio_boundary`；精简结果 `business_probe_summary.json → neighbor_ratios`。

### R6-02 · P1：真实 Word/CSV 与四格式门禁契约仍不一致

**复现范围：** 实际主链生成、校正及人工决定后，从下载处理函数保存五组原样 JSON/CSV/DOCX/PDF：校正前、100→90后、后续新决定前后两份历史报告、110的新报告。原样字节再次通过当前 `_evaluate_export_agreement`。

**结果：** 01/03/04/05 为 `not_computable`，Word 的 BUD-107-1 问题行不符合 canonical 契约；02 为 `computed`、该合成样本一致率0.0。该0.0仅描述下载反例，不是系统真实业务导出一致率。

两个具体原因已经分离：

1. Word [问题块](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1034>)没有逐问题的人工处理 status/note；[独立决定表](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1087>)完整保存了它们。当前[门禁](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:1410>)却要求字段在对应 ISSUE_ID 段内。**这是关联契约不兼容，不能称为 Word 丢失了人工决定。**
2. `review_reason` 的 canonical JSON 没有 title，签名按空值读取；[CSV 生成器](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1959>)用 code 回退显示标题。[JSON 签名](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:1466>)与[CSV 签名](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:1550>)因这一默认值不同而不一致。逐组件诊断显示问题、决定、回执及结论相同，待复核原因原文也相同。

**整改与验收：** 四格式共享规范化的 manual title；如需显示回退值，应明确定义 display_title。Word 可把决定写入对应问题块，或让门禁通过 ISSUE_ID↔DECISION_ID 严格关联核对。不能降为全局关键词存在性，也不能忽略应校验的人工决定。上述五组真实下载均须通过，再以删除决定、互换问题内容、更改原因或版本作反例。

**已修部分：** 五版 PDF 的 ISSUE_ID 位于标题/严重度前，逐问题 status/note、待复核类型已打印，跨页重复页眉已处理；当前读取器下旧 PDF 缺项均消失。两份历史下载的六个文件 SHA 全部相同，confirmed 与新版本 no_issue 仍隔离，不重新开启历史覆盖问题。

证据：`production_export_gate.json`、`export_signature_diagnosis.json`、`evidence_audit.json → actual_four_format_semantic_diagnosis`；原始产物见 `export_probe/`。

### R6-03 · P2：部分子关系不适用，被聚合成整个配对不适用

**合法输入：** 财政收入金额一致但未声明自己的比例；用分号另写“事业收入0万元，占0%”。两条金额关系 pass，财政比例关系 not_applicable，规则 V33-103 pass。

**实际结果：** `OBL-TXT-T2:V33-103` 配对回执为 not_applicable。原因是[状态优先级](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/check_obligations.py:72>)把 not_applicable 排在 pass 上方，[聚合](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/check_obligations.py:1430>)直接取最高优先级。

**影响范围：** 配对回执不能准确表达“适用金额检查已完成、比例无须检查”。本轮使用真实 profile、两条实际规则及 `build_obligation_ledger`，**整个 OBL-TXT-T2 台账仍为 completed**；未证明真实业务完成率分母因此被剔除或虚增，不作该推断。

**整改与验收：** 聚合先隔离不适用子关系；全部不适用才整体 not_applicable。存在适用关系且均通过则 pass；适用 fail/未完成仍如实传播，并保留各子关系与原因。新增全NA、pass+NA、fail+NA、insufficient_data+NA及混合多规则实际台账用例。

证据：`rules_audit.json → actual_obligation_ledger_ratio_boundary` 的 `semicolon_other_indicator_share`；精简结果保留实际台账与 profile。

### R6-04 · P2：明示本单位简称仍被当成他单位，减少自动检查覆盖

**合成输入：** 发布单位为“上海市普陀区建设和管理委员会”；正文明确“上海市普陀区建设和管理委员会（以下简称‘建管委’）”，随后写“建管委2026年收入预算100万元，支出预算100万元”，与T1表100/100一致。

**实际结果：** 主单位识别正确，但 excluded 包含建管委，T1收入/支出对应文本被排除，回执转 insufficient_data。[主体排除](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1396>)只比较名称字符串包含关系，未消费明示别名。

**影响与边界：** 这是自动化覆盖缺口；系统诚实转人工复核，没有在此例伪称通过或报告金额错误。本例仅提供T1，其他T4/T9缺失另外影响整个BUD-107；不能把整个规则未完成都归因于简称。

**整改与验收：** 从主单位元数据及“以下简称/简称”声明构建有来源的主体别名，绑定声明范围和年度；拒绝他单位/下属单位别名冒充主单位。合法建管委100/100应完成T1检查；90/100的真实错误仍须检出；他单位教育局/财政局仍排除。

证据：`parse_audit.json → reproductions.r5_05_other_vs_self_legal_alias_and_true_difference.observed` 的 `explicit_self_initialism`。

## 五、未完成验收及能力边界

### R6-05 · P2：可信 OCR 自动晋升仍未交付闭环

沿用 R5-09。[OCR 候选物化](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:290>)仍固定0.75、low_confidence、规则操作数0；歧义/身份不明时安全降级有效。本轮源码未改变，未找到真实模型文件清单，未实际运行可信 OCR 模型。

需要用模型/文件指纹、原格 bbox、数字与单位置信度、表格主体/年度/阶段验证，证明高质量扫描件可进入可追溯操作数、低质量扫描件仍待复核。不能通过调高固定分数获得“可信”。证据：`parse_audit.json → reproductions.ocr_promotion_score_semantic_ambiguity`。

### R6-06 · P1：冻结盲测与真实 API/Worker/PG/OCR 验收仍缺证据

沿用 R5-10。ZC07 exit2 / `business_acceptance=not_passed`：登记29份，冻结人工真值1份，缺陷组3组，原始PDF哈希认证0；无冻结开发/盲测清单、无真实四条服务链证据。Docker CLI可用但daemon不可达，`models/MANIFEST.sha256`缺失。隔离测试未配置PG URL，只说明本轮未实跑PG，不推断用户生产数据库状态。

继续按既定任务包交付：开发32份 + 独立盲测32份，每套8类分层各至少4份，盲测至少100个人审缺陷组；在真实隔离部署执行原生预算、原生决算已知问题、扫描预算、混合决算校正四条 API→Worker→PG→OCR→下载链，并记录运行资源及版本。旧开发样本不能充当新盲测。

门槛沿用：precision≥98%、recall≥95%、重大问题无漏报、精确定位≥98%、正式证据真实性100%、越界定位0、确定性适用检查完成≥95%、全部应检查状态/原因覆盖100%、四格式与重复运行100%一致。**真实业务指标尚未计算，结构化输出为 null。** 合成门禁正例1.0、上述单下载反例0.0及pytest通过数均不能作为业务指标。

**人工定位能力边界：** R5-08的状态错误已修；请求bbox `[205,155,315,195]` 后实际事实仍是旧 `[200,195,320,235]`，但明确返回未应用并要求复核。当前不能宣称人工定位修改已作用于新版证据或标注PDF。若这一能力属于正式试用范围，还需实现版本化位置覆盖、原PDF引文再认证及四格式同步，旧报告位置保持不变。

## 六、工程验证与测试可信度

| 检查 | 本轮结果 | 限制 |
|---|---|---|
| 全量 pytest | 2387 passed / 137 skipped，exit0 | 9条警告；跳过不是通过 |
| tests/remediation | 169 passed / 2 skipped，exit0 | 真实PG、cv2项目跳过；6条警告 |
| Ruff | exit0 | 不替代业务正确性 |
| 前端单测 / build | 均exit0 | 未实跑浏览器端到端 |
| 环境配置一致性 / 日志检查 | 均exit0 | 没有新安全扫描结论 |
| ZC07 preflight | exit2，not_passed | 数据与真实服务证据不足 |

与第五轮相比，全量新增16项通过、整改新增14项通过，跳过数不变。相关新增断言增强了占比失败引用、V33身份、实际三公混合来源和AI隔离，未发现靠调宽金额/比例阈值或删除应检错误使其通过。金额政策与数学模块相对第五轮未改变。

现有测试仍缺三个相邻边界：同句多个指标的比例归属、pass+NA真实配对聚合、明示本单位首字简称。新实际下载测试使用手工status样本，没有human_decisions/review_reasons，因此不能覆盖本轮真实主链导出失败；这属于场景覆盖不足，并非断言被弱化。缓存v5指纹断言本身不足，本轮补做旧v4安装→新版重建→二次复用的协议闭环。

规则目录仍82条，5条声明事实迁移complete、77条pending，实际访问FactContext的也是5条。这不代表77条都无法执行，但不能声称全部规则已实现完整可追溯事实计算。

门禁原反例回归保持：错区域、错哈希、无认证证据不能算可信；缺冻结分母、冲突回执、缺真值区域不能计算；结论/定位/容差/引文变化不能判重复一致；互换Word/PDF问题内容仍被拒绝。上述均为独立合成/协议检查。

PDF视觉检查：合成原件/导出经Poppler渲染并查看；实际中文报告经MuPDF补充渲染查看，标记与人工状态清晰。当前Poppler缺Adobe-GB1映射，实际中文页渲染受限，原件也受相同限制；不能把该环境问题判为产品丢文字，也不能宣称跨阅读器兼容性已通过。

## 七、下一轮具体交付顺序

1. **先处理两个P1代码项：R6-01、R6-02。** 修比例归属；让真实主链五组下载通过统一契约，保留逐问题ID校验及篡改反例。只修手工测试正例不足以核销。
2. **处理两个P2代码项：R6-03、R6-04。** 正确聚合适用关系；消费有证据的本单位别名。每项保留合法阴性、真实错误阳性及待复核边界，不能以全部转NA或过滤问题替代实现。
3. **补OCR与业务验收：R6-05、R6-06。** 真实模型可信晋升与风险隔离、冻结盲测、四条真实服务链完成后，才计算准确率/召回率/定位等指标并作正式可用判断。

若暂不提供人工位置覆盖，界面与报告应持续明确“未应用、需复核”；如要开放该功能，须补位置证据重新绑定的验收。每次交付需附旧反例翻转、相邻合法/错误用例、版本指纹、命令退出码、原样产物。

## 八、证据交付

证据目录：[本轮复核产物](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6>)。

- [结构化问题JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/findings.json>)、[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/findings.csv>)：6项；[上轮核销台账](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/remediation_ledger.json>)：10项。
- [业务反例精简结果](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/business_probe_summary.json>)、[规则完整复现](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/rules_audit.json>)、[解析完整复现](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/parse_audit.json>)、[证据/校正/AI完整复现](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/evidence_audit.json>)。
- [实际导出门禁](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/production_export_gate.json>)、[原函数签名诊断](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/export_signature_diagnosis.json>)、[原样下载](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/export_probe>)、[门禁反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/root_gate_probes.json>)。
- [全量测试日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/full_pytest.log>)、[整改测试日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/remediation_pytest.log>)、各 `*_run.json` 命令回执、[ZC07结果](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261005_round6/zc07_preflight.json>)。
- `source_snapshot_before.json`、`source_snapshot_after.json`、`round5_comparison.json`、`working_tree.patch`、两份相对第五轮补丁及 `artifact_manifest.json`：版本及证据追溯记录。
