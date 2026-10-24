# GovBudgetChecker 第三轮整改复核报告

复核日期：2026年10月4日。分支：`codex/zcode-remediation`。当前提交：`9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。对照提交：`edb4e6d7ecaef83252fb1844d0a6c45e4e6b3f07`及[上一轮复核报告](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_整改复核报告_20261003.md>)。

**结论：工程回归通过，多项核心反例已修，业务验收仍未通过；不能认定“剩余可代码化整改全部交付”。** 剩余不只有真实环境和独立盲测，还包括预算三公路径、风险事实回退、单位校正、人工决定版本绑定、证据认证、正式评测和默认部署中的可复现代码问题。

最重要的改善已经实证：真实合成中文PDF的100→90校正生效，相关差异消失；同原件重解析事实ID稳定；新旧报告revision正确写入；历史PDF与当前PDF使用不同产物，不再覆盖前一下载响应。上一轮这些具体失败结论已经失效，不应继续列为未修。

本轮只做核查，没有修改业务代码。开始时仅有未跟踪 `.mimosa/`，保留原状；本轮报告和探针单独保存，没有提交到Git。用户所述4个新工程提交已核实，另有1个收录上一报告的文档提交。日期使用客户端北京时间；合成运行内部revision中的UTC/主机时间仅作产物身份，不作为报告日期。

## 一 范围和证据

沿用[Zcode任务包](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_Zcode_分阶段执行任务包_V1.0.md>)和[AGENTS.md](</Volumes/workSSD/software development/GovBudgetChecker/AGENTS.md>)的首版范围：上海市及区级部门、单位，单份预算或决算PDF，原生/扫描/混合材料，本地CPU OCR，规则优先且默认无AI。规则契约以 `v3_3_portable` 为准；本报告审查软件，不裁定地区年度法规。

并行核对解析与事实、数字与口径、证据与交付；独立复跑全量工程检查、旧反例和新增边界。真实合成PDF路径包括原生解析、事实物化、生产规则、校正handler、实际主流程重算、不可变版本及下载；认证与存储边界作测试隔离，没有启动真实队列、连接业务数据库、调用云AI或真实OCR模型。

合成金额、页和bbox仅证明逻辑行为，不是政府材料真值或业务质量样本。没有使用保留盲测原件调规则。纯内存SQL投影能够验证序列化和分支，不能替代真实PostgreSQL的迁移、codec和事务往返。未核实用户正在运行的API/Worker加载版本，结论对应上述Git提交。

## 二 独立运行结果

| 检查 | 本轮实际结果 | 含义 |
|---|---|---|
| 后端全量 | **2358 passed、137 skipped，0 failed、0 xfailed**，退出码0 | 与用户报告一致，较上轮增加21个通过项；跳过项不能算已验 |
| 整改专项 | **141 passed、2 skipped**，退出码0 | 两项仍因测试数据库未配置、cv2缺失跳过；与全量重叠，不相加 |
| 前端单元及构建 | 均通过，退出码0 | 单元和构建通过，未代替浏览器E2E |
| Ruff、环境变量对账、日志安全 | 均通过 | 工程静态检查通过；不等于完整安全扫描 |
| ZC07预检 | **not_passed，退出码2** | 冻结数据、真实E2E缺失，正式业务指标不可计算 |
| 上轮23组新增规则反例 | **18组符合预期，5组仍失败** | 定向回归数，不能作为系统精确率或召回率 |
| 有效真实差异正控 | **5/5检出** | 修复没有简单通过“全部不报”绕过这些正控；不是全部规则无漏报证明 |
| 类型检查及浏览器E2E | 未完成 | mypy未安装，app依赖的Playwright Chromium不存在，本轮未运行浏览器E2E |

测试进程禁用dotenv加载，测试数据库、AI显式测试变量及上传目录作隔离。实际日志：[后端全量](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/backend_full.log>)、[专项及跳过原因](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/remediation_tests.log>)、[前端单元](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/frontend_unit.log>)、[前端构建](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/frontend_build.log>)、[ZC07预检](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/zc07_preflight.json>)。

用户报告的六组门禁翻转也已独立确认：错区域0.0；100条问题仅1条有核验记录时0.01；100个应检关系仅1回执时完成率0.01；结论冲突repeatable=false；导出比较不再抛AttributeError；匹配记录携带finding后，在构造的annotations接口上正确区域可算1.0，findings读取数为1。见[原反例重放](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/root_gate_original_replay.json>)。这些成功值不能外推为正式评测整体已正确。

## 三 剩余事项逐项复核

| 编号 | 已确认修复 | 本轮结论及剩余 |
|---|---|---|
| RC-01 事实消费 | V33-103/104及V33-214正常事实已真实消费；BUD-107角色、verified优先、风险阻断和纯事实路径改善 | **部分未通过**：V33-214风险事实仍回退旧表；CMM预算路径不读事实；登记仍5 complete/77 pending |
| RC-02 表身份 | 普通同年异单位、单位冲突不合并；真实续页原始来源格保存 | **部分未通过**：“续表”标记绕过主体/年度冲突；旧二维采购续页仍误拼；缓存物理片段未完整恢复 |
| RC-03 文字上下文 | 纯调整后预算不再作年初操作数 | **未关闭，用户已承认部分残留**：跨行历史、异主体未接线；新增整句禁词会误排明确年初值；叙述事实未物化 |
| RC-04 完成状态/回执 | 决算缺说明、T9各字段引用改善 | **部分未通过**：预算缺分项仍pass；决算缺表格值仍可不适用；问题与关系/事实/证据ID未贯通 |
| RC-05 条件性表 | 基金/国资去串扰、同句历史及零额反例修复 | **部分未通过**：历史年份只在上一行标题时，仍要求本年基金表 |
| RC-06 容差 | 决算CMM-001的0.5万元边界已修 | **部分未通过**：预算实际生产路径仍0.01万元 |
| RC-07 OCR | 通用合计无专项上下文被拒；低分事实不再作确定操作数 | **误用保护已修，自动审校待完成**：所有晋升事实固定low_confidence，没有认证为可信操作数的完整路径 |
| RC-08 缓存 | 命中完整恢复旧artifact，不再旧run配新事实；config/OCR指纹接入 | **部分未通过**：解析算法改变但指纹未变，仍可读旧混主体表；OCR缓存来源/定位标签改变 |
| RC-09 证据 | table_refs伪空白框被剥离，实际PDF不再误画；physical_page错引失败；两认证字段都缺时降级 | **部分未通过**：hash与quote尚未统一强制；只缺一个字段仍可verified；locator/guard错误hash反例仍成立 |
| RC-10 校正及复核 | 100→90重算真实生效，单位与原值组合的归一金额路径改善；部分不支持身份字段有未生效提示 | **部分未通过**：合法unit-only校正静默不生效；列映射等支持未完成；人工决定当前不投影、跨版本不绑定 |
| RC-11 AI接口 | critical封顶high，模型金额标记未验证，普通合并中无身份候选进入待确认 | **未关闭**：生产dict上下文的ID白名单失效，离线契约未接管；逐项异常/身份仍待完成 |
| RC-12 版本及PDF | 实际新旧revision写入、历史/当前PDF分路径、前响应不被改写 | **本轮这两项旧反例通过**；全格式人工决定一致性仍受RC-10阻断，不能等同完整交付已验 |
| RC-13 评测 | 六组旧门禁翻转、diagnostic不再永久阻断 | **部分未通过**：正式labels接口、区域等级、关系键/分母、真实性、全语义导出和盲测统计仍有错误 |
| RC-14 真实业务验收 | 没有新增正式业务通过证据 | **待环境和数据，用户已明确保留**；仍不具备计算业务指标的条件 |
| RC-15 默认部署 | README改为基础Compose启动、AI显式叠加 | **未通过**：实际基础Compose仅postgres，可选profile再加ocr，缺API/Worker/前端 |

这张表按具体条件判定，没有用局部测试成功替代整项验收，也不把上一轮已修反例继续算失败。

## 四 仍需代码整改的关键证据

### 1 预算三公及统一事实仍存在确定性错误

[CMM生产路由](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:488>)只有final进入事实实现，budget仍走旧实现。预算“合计30+出国0、其余分项缺失”仍pass；有真实合计差异时虽保留问题，却未同时聚合缺项为未完成。其[比较helper](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:265>)仍使用0.01，10.00与10.10/10.50万元均报不一致，与portable0.5不符。不能用决算路径修复声明预算也完成。

[V33-214](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:5274>)已经真实读事实，但ambiguous/low_confidence/missing事实配旧表100/100时，仍用旧表给completed、无问题。风险事实被拒后不能自动替换为确定旧值。

新增决算边界还表明：接待费表格为空但说明有10时，关系被记not_applicable并pass；“公务用车购置”对子项名称的子串匹配会与“购置及运行”混淆，完整合法输入反而未完成。另一个合法事实阶段契约探针表明年初预算事实被统一决算阶段过滤后回退旧值；当前物化器按文种统一阶段，故该探针证明接入规范年初预算事实时的契约风险，不能描述为已发生的真实材料漏报。

真实主流程问题项仍无 `fact_ids/check_relation_id/evidence_ids`，回执 `issue_refs=[]`。目录仍5条complete、77条pending；新增若干规则消费不能算全量迁移结束。

**再次验收：** 预算/决算各路径分别验证实际事实消费、0.5边界、缺字段、空格和字段身份；拒绝风险事实转未完成。按科目编码/层级/角色取值，区分预算、决算两列阶段；绑定叙述和表格两端事实以及问题ID。与真实错误正控一起跑，保留确定问题和未完成范围。

### 2 续表不能覆盖主体冲突 旧解析缓存须失效

普通甲局/乙局边界已修，但[标题冲突函数](</Volumes/workSSD/software development/GovBudgetChecker/src/services/pdf_parser.py:857>)在检查主体/年度前，对“续表”直接返回不冲突。合成“甲局2024收入表”接“乙局2024收入表（续表）”，两页归甲局；接“甲局2025收入表（续表）”，仍归2024。旧二维采购续页也仍可把独立标题表拼入。

本轮对两个提交的真实parser解析同一合成页：旧版本混主体1实例，新版本正确2实例，但[PARSER_FINGERPRINT](</Volumes/workSSD/software development/GovBudgetChecker/src/services/pdf_parser.py:92>)仍为相同的 `pdfplumber-cells-v3-zc02`。合法缓存命中旧artifact后恢复旧1实例，当前修复被旧缓存抵消。这是隔离缓存分支与两个版本解析器的执行证据，没有声称已连接真实库发生事故。

缓存存储的实际内存SQL往返还有来源契约问题：[loader](</Volumes/workSSD/software development/GovBudgetChecker/src/services/parse_artifact_store.py:414>)固定extraction_method为stored，[重新物化](</Volumes/workSSD/software development/GovBudgetChecker/src/services/fiscal_fact_materializer.py:554>)将OCR证据的 `ocr/region` 改成 `native_text/exact_cell`，同一evidence_id标签改变；事实仍low_confidence，不应写成已成为verified。原生来源格、页和bbox保持正确，但physical_fragments从2变0、原成员列表丢失。

**再次验收：** 硬身份冲突优先于续表标记；更新身份/解析/OCR策略指纹并验证旧缓存失效。缓存完整恢复物理片段、逻辑成员和真实提取来源，OCR不能借加载变成原生精确格。用真实隔离库补齐往返验证。

### 3 叙述阶段与历史适用性仍未绑定章节

用户已承认历史标题跨行、异主体和叙述事实未完成，本轮仍复现。新增[整句阶段禁词](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1285>)又会误排“调整后预算另行公开，年初收入预算328000……”中的明确年初金额，从应检差异退为未完成。条件性表在[同句历史过滤](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:2039>)后，上行标题为历史年度、下一行基金150仍触发本年缺基金表。

**再次验收：** 从章节继承主体、年度和阶段，按金额所在短句确定口径，不能用整行禁词删除当前正确操作数。文字金额物化为可定位事实。历史段换位置/换行不改变本年结果；只剩调整后数据时确实未完成；同句含阶段提示和明确年初值时仍检查。

### 4 OCR低分误用已阻断 可信扫描审校尚未交付

[晋升入口](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:327>)把所有表confidence固定0.75，物化后全部low_confidence、operand_count=0。低score0.1确定性误用已经消除，通用“名称/合计”无专项上下文也被拒，这是应保留的修复。

但这条路径没有逐格真实置信度及关键数值复验到可信操作数的能力；摘要 `usable_for_rules=true` 可与实际operand_count=0并存。支出通用三列的唯一表种身份也还未充分消歧。没有运行真实OCR模型，不能声称扫描件准确率或自动完成率达标。

**再次验收：** 区分“候选已物化”和“可确定计算”；完成模型/列身份/关键数字复验，对可信格产生操作数，不可信格待复核。真实扫描和混合材料由生产规则直接消费，不能以全部待复核替代自动审校能力。

### 5 证据认证仍缺同一份原件的完整身份

table_refs伪空白框现在降region/unverified，actual annotation targets为空，实际PDF无误画；physical_page错页引文现在failed。两者均已修。

但[locator的内容认证](</Volumes/workSSD/software development/GovBudgetChecker/src/utils/issue_bbox.py:140>)和guard对合法quote配wronghash仍verified；[读取端](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/corrections.py:332>)只在“quote和hash都缺失”时降级，缺quote但有hash、缺hash但有quote仍能verified。bbox-only的结构完整仍可能作为正式问题统计。某段文字恰好命中当前页不能认证缺失或错误的原件身份。

**再次验收：** 所有issue/evidence/table_refs/读取/导出统一认证hash、physical_page、来源cell/span、quote和区域。缺必要字段为未认证，不是verified；文档级结构问题和区域导航另设明确契约。重新验证只缺一个字段、错误hash、OCR来源及多证据，不只验证“两者同时缺”。

### 6 金额校正已修 单位校正和人工决定尚未闭环

100→90经过真实main已生效，新版本receipt pass、原收入问题消失，旧版本仍保留。报告revision及历史PDF独立产物也已通过。

新增合法unit-only请求则相反：接口queued，新版本产生，但 `applied=0、status=applied`，unit仍万元、归一值仍100，没有相应校正未生效原因。[覆盖层解析](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:732>)跳过不含数值的单位改动，unsupported收集也未将其标记为未应用。合法table_instance的column_mapping仍被跳过；year等部分身份字段已有明确blocking提示，应肯定这部分如实记录，但不等于支持这些必需校正。

人工决定也未完成：实际workflow.update_issue(confirmed)后，当次canonical/JSON仍 `human_decisions=[]`；下一次100→90重算移除问题后，新报告却带旧BUD-107-1的confirmed，仅issue_id/status，没有原revision、actor、fact/evidence绑定；CSV未反映该确认及复核说明。[工作流存储](</Volumes/workSSD/software development/GovBudgetChecker/src/services/issue_workflow_store.py:877>)和[主流程投影](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:2025>)仍未定义版本失效范围。

**再次验收：** 单位单独改动也重算，支持字段生效、不支持字段明确拒绝或记录未生效；不能applied=0且未说明。决定当次投影至所有交付，绑定原分析/问题/事实身份；新版本继承须验证关系未变，关系变化明确失效。验证单位、列映射、原值、过期提交和新旧导出。

### 7 AI白名单实际未读取生产dict上下文

本轮实际 `FactContext.to_dict()→JobContext` 的合法生产形态是dict。[AI转换器](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ai_findings.py:288>)却用对象属性读取facts，得到空白名单，unknown fact/evidence/relation全部保留且无过滤计数；对象形态控制夹具才正确剔除。

严重度critical→high、模型金额 `model_claimed_not_verified` 已生效。该候选通过正常merge/report后是**正式问题0、待确认候选1**，不能写成模型幻觉金额已直接形成正式判错。这一保护应保留。

另对单关系评价注入异常时，真实analyze_dual退出整体合并并切到legacy fallback：两条AI与两条规则全进canonical正式集合，candidate_ids/relations均空。该试验模拟异步服务与快照边界，是故障隔离反例，未调用模型，不能描述成默认关闭AI时已发生的线上事故。修复“不清空批次”还须保持原裁决状态，不能回收待确认候选成为正式项。

**再次验收：** 以实际schema序列化形态校验ID，空/缺上下文不能默认放行未知身份；实际生产入口使用候选契约。模型只提交语义或取数候选，系统执行金额/容差/公式。单项失败保留其他已完成结果和失败回执，默认关闭AI不影响规则任务。

### 8 评测仍不能证明规定的定位、覆盖和交付一致性

本轮[新增可重跑脚本](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/run_gate_probes.py>)和[实际输出](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/root_gate_probes.json>)证实：

| 残留 | 实际结果及原因 | 再次验收要求 |
|---|---|---|
| 正式golden接口 | 标准matched已携finding，但合法golden的labels+truth_region仍not_computable；[评测](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:745>)只读annotations | 全评测链使用统一正式golden契约，非仅人工构造annotations |
| 区域导航冒充精确 | 整页bbox、region、unverified仍算定位1.0；只对真值覆盖≥50%，不检查定位等级/候选区域大小 | region不得算精确；认证精确格/片段，限制候选范围，错页错框失败 |
| 关系键不一致 | 真实rule=BUD-107、sub=T1:income_budget:text_vs_table，用sub或rule:sub清单都完成率0.0 | 规范明确rule_id/sub_check_id对象或统一键，真实回执与冻结范围逐项对应 |
| 回执分母 | 原少回执例完成率已0.01，但覆盖仍1.0；无冻结范围时回退issued_receipts算1.0 | 回执覆盖与完成均以冻结适用全集作分母；缺范围不可计算，不能系统自报缩分母 |
| 真实性及门槛 | 不完整证据只贴verified标签仍1.0；真实性门槛仍0.98、回执仍0.95 | 核引用实体和结构，全部正式引用可验证、每应检关系有状态/原因，完成95%另计 |
| 导出语义 | 问题rule/severity/page相同但结论、人工决定不同仍1.0；不支持的JSON结构被当空一致 | 比完整统一报告及全部规定格式，拒绝缺字段/schema不匹配/缺产物 |
| 重复运行语义 | bbox、fact/evidence ID和完成状态改变，摘要不变仍repeatable=true | 同原件/配置/版本比规则结论、事实/证据及完成范围，不能只比摘要 |
| 统计集合 | [辅助指标](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:991>)仍用development+blind，而输出称acceptance_split=blind | 正式指标仅独立盲测，开发单列，避免开发结果稀释盲测失败 |

这些是子函数或子链反例，不表示整业务门禁当前能够伪造通过；当前整门禁诚实返回not_passed。诊断指标与正式门项的分离已修，不再永久阻断；不得据此降低必需业务门槛。

**再次验收：** 用合法、非空golden与生产回放贯通评测；验证正确输入通过、错页/整页导航/缺范围/少回执/缺认证/结论冲突/格式不匹配被拒，然后才对冻结真实盲测计算指标。

### 9 默认Compose没有完整服务，仍属代码交付缺项

[README](</Volumes/workSSD/software development/GovBudgetChecker/README.md:137>)现称基础Compose启动backend/worker/frontend/postgres和本地OCR链。但独立执行 `docker compose -f docker-compose.yml config --services`，正常只有postgres；启用ocr profile也只有ocr、postgres。使用虚构插值值作只读静态检查，没有启动服务，未暴露或使用业务凭据。见[部署探针](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/deployment_probe.json>)。

**再次验收：** 给基础配置实际增加或提供真实默认全链启动方案；API/Worker/前端/数据库/OCR默认无AI启动、就绪及上传实跑。修改README的标题和启动命令不能替代服务定义。

## 五 待真实环境验证

ZC07盘点仍为29份登记材料、1份golden、1份人工冻结真值、3个冻结缺陷组，原件hash核验通过0份；这不表示29份原件全部不存在。冻结开发32/盲测32、每集8层每层≥4份、盲测≥100真实缺陷组仍未提供。

测试数据库未配置，Docker CLI存在但daemon不可达，models/MANIFEST.sha256缺失，四条真实服务场景证据缺失；Linux容器内存超限受控终止尚无本轮实跑。用户报告Mimosa完整扫描未成功，本轮未执行该扫描，不作完整安全审计通过结论。

业务精确率、召回率、精确定位率、证据真实性及确定性完成率均**不可计算**。原生/扫描/混合的合成测试或137项skip不能替代真实链路。盲测保留材料不能拿来继续调规则，已用于修复的样本只能作开发回归。

## 六 下一轮交付顺序

当前不宜直接把剩余工作缩减为AI增益验收和独立业务验收。应先完成确定性代码残留，再接环境与真实数据。

1. **规则及校正**：修预算CMM事实消费/0.5/缺项，V33-214风险回退，unit-only与列映射，问题身份和人工决定版本绑定。保留已经通过的100→90及历史PDF测试。
2. **来源与证据**：硬身份优先续表；解析策略变更缓存失效；完整缓存恢复及OCR来源；统一hash/quote认证；实现可信OCR操作数路径。
3. **上下文和评测**：章节主体/年度/阶段事实，条件适用继承；正式labels、冻结关系键/分母、精确区域与全语义导出指标。
4. **默认部署和真实服务**：提供完整无AI服务定义，再在隔离PG/Docker/OCR执行上传、Worker、定位、复核、校正和导出，补Linux资源保护。
5. **AI与独立业务验收**：实际dict上下文白名单和生产候选契约完成后单独验AI；冻结盲测计算任务包规定的质量指标，不把诊断或合成数据算真实质量。

本轮[JSON问题清单](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/findings.json>)和[CSV问题清单](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/findings.csv>)用于交接，系统问题页码/bbox不适用，未编造政府材料定位。

## 七 复核附件

- [解析/身份/缓存审查](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/parse_audit.json>)：16组执行探针，含旧夹具失效说明、两个版本真实parser对比及缓存内存往返。
- [规则审查](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/rules_audit.json>)：23旧例重放、5条事实契约、21个必要范围契约、正控及精确源行。旧builder未传fact_receipts的失败未作为新版主链错误。
- [证据/校正/AI/导出审查](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/evidence_audit.json>)：真实合成PDF主流程、100→90与unit-only、文件工作流、实际dict AI上下文、标注及新旧下载。
- [旧门禁重放](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/root_gate_original_replay.json>)与[新门禁反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004/root_gate_probes.json>)：在项目根目录运行`.venv/bin/python outputs/reacceptance_20261004/run_gate_probes.py`可重现新增子函数问题。

附件目录受仓库忽略规则保护，交给Zcode或迁移机器时须与报告一起保留。未运行真实模型、数据库、容器或独立盲测的结论保持待验。
