# GovBudgetChecker 第五轮整改复核报告

复核日期：2026-10-04。对象：用户完成第四轮修改后的当前工作区。业务代码保持只读。

## 一、验收结论

**工程回归通过，业务验收仍未通过。** 上轮后端无法导入的问题已经解决；本轮原始工作区直接运行全量测试得到 **2371 passed / 137 skipped / 0 failed**，整改测试得到 **155 passed / 2 skipped / 0 failed**，Ruff、前端单测和构建均通过。本轮没有使用删除重复参数等诊断补丁。

同时，独立反例仍证明存在占比漏报、检查结论与回执冲突、事实身份链缺失、外单位简称串值、旧缓存覆盖新解析、AI 拒绝项进入 agreement、人工定位覆盖未生效等代码缺口。实际下载的四格式报告也不能通过当前一致性门禁。尚不能据此承诺“输入任意预算/决算报告即可准确找出并精确标记所有问题”。

本报告列出 **8 个代码整改项、2 个未完成验收项**，编号为 R5-01 至 R5-10。优先级 P1/P2 表示工程修复顺序，不是被审校政府材料的业务严重度。本次没有新增 P0 导入阻断。

## 二、版本、范围与验证边界

- 分支：`codex/zcode-remediation`；HEAD：`9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。
- 本轮仍是未提交改动：51 个已跟踪文件，相对 HEAD 约 +3960/-553。上轮为 26 个文件。同一 HEAD 不能代表两轮工作区内容相同。
- 工作区 diff SHA256：`d1c7348cdb1dbd125dcd6354bcce336f5dd2d7eb099fc4996b4417b7b8d6d257`。逐文件指纹及前后比较保存在本轮证据目录。
- 默认业务规则依据仓库 AGENTS.md 的 `v3_3_portable`，重点验证误报控制、同主体/年度/预算阶段取数、0.5 万元金额容差、0.1 个百分点容差、证据诚实性与版本隔离。
- 按既有分工并行复核解析、规则、证据/校正/AI；再独立复验门禁和实际下载结果。
- 直接运行当前源码。涉及数据库的缓存/落库探针采用内存 SQL 协议替身；AI 采用合成响应；API 校正与下载采用临时任务目录及实际处理函数。它们不等同于真实 HTTP 上传、Worker、PostgreSQL、OCR 模型完整联调。
- 测试禁用 dotenv 加载、生产数据库和真实 AI；未读取 `.env`、`.mimosa/` 或冻结盲测原件。未改业务代码、未重置用户改动、未提交 Git。

## 三、已验证修复与仍未关闭的旧项

下表的“修复”仅指所列反例与对应契约，不表示整个 RC 模块已通过真实业务验收。

| 第四轮发现 | 本轮状态 | 当前证据 |
|---|---|---|
| R4-01/02：重复关键字参数及 Optional 导入 | 已修复 | 原始模块编译、API 导入、全量 pytest、Ruff 直接通过 |
| R4-03：风险合计被有效明细替代 | 已修复 | 合计 ambiguous + 明细 verified 时返回 blocked；合法总计仍选 100 |
| R4-04/18：年初/调整预算阶段隔离及决算年初预算 | 已修复 | 调整后预算不作年初操作数；合法年初预算仍被使用 |
| R4-05：legacy 身份转发与 V33 作者身份 | 部分修复 | 转发已修复；V33-103/104/214 作者未填 ID，见 R5-02 |
| R4-06：纯旧表取数伪称事实完成 | 部分修复 | 无事实时改标 legacy/pending；混合取数仍错误声明，见 R5-03 |
| R4-07/19：公车内部回执、V214 未完成原因 | 已修复 | 内部勾稽有回执，阻断分支明确暴露原因 |
| R4-08：部分列映射删除其他事实 | 已修复 | 合法四事实输入仍保留四事实，不再只保留被改两列 |
| R4-09：后续决定覆盖历史决定 | 已修复 | 实际主链重算与下载：旧版 confirmed、新版 no_issue 分离 |
| R4-10：异主体叙述金额 | 部分修复 | 乙局/乙单位/带地域长名已排除；教育局简称仍误取，见 R5-05 |
| R4-11：独立采购表误拼 | 已修复 | 11 行保持 11 行；已知三页表恢复 38/38/44 行 |
| R4-12：合并续页丢物理格与表头 | 已修复到存储协议层 | 合法重复表头续页的原 grid/cells/evidence 往返一致；未实跑 PG |
| R4-13：自动可信 OCR | 未关闭 | 安全降级正确，自动可信操作数仍未证明，见 R5-09 |
| R4-14/15：门禁分母、冲突回执、真值区域、重复性遗漏 | 已修复所列反例 | 缺分母/冲突/缺真值区域返回 not_computable；引文/容差变化不能判一致 |
| R4-16：Word/PDF 互换问题内容仍通过 | 原反例已修复，模块未关闭 | 消息互换被拒绝；实际导出正例却不能通过，见 R5-07 |
| R4-17：冻结数据及真实服务验收 | 未关闭 | ZC07 仍 not_passed，见 R5-10 |
| R4-20：校正依赖只识别 operand_fact_uids | 已修复 | 当前真实校正返回 affected_checks=mapped |
| R4-21：AI 离线契约未接线、单关系失败影响整批 | 部分修复 | 生产构建入口已调用契约；单关系故障隔离有效；拒绝项持续隔离仍缺失，见 R5-06 |

## 四、剩余代码整改项

### R5-01 · P1：占比常用表述漏报；已报错时最终回执仍全部 pass

**复现输入：** 收入合计与财政拨款收入均为 1000 万元，应占 100%。对应说明金额一致，分别写“占99.89%”和“占比99.89%”。差额 0.11 个百分点，超过既定 0.1 容差；同组保留 100% 和 99.90% 的合法阴性。

**实际结果：** “占99.89%”无 finding，V33-103 直接 pass；“占比99.89%”产生占比错误、规则 fail，但最终义务回执及两条金额回执全部 pass，均无问题引用。金额检查完成被错误当作整条收入说明检查完成。问题文案还残留“容忍±1pct”，与实际 0.1 容差不一致。

**位置：** [百分比提取](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:1887>)、[占比检查](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:2455>)、[回执聚合](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/check_obligations.py:1430>)。

**整改及验收：** 绑定分子/分母/占比叙述事实；支持“占、占比、比重”等合法表达并限定同句指标；增加独立比例回执和稳定关系 ID。聚合必须覆盖规则全部适用关系，不得用两个金额 pass 覆盖比例 fail。上述两种 99.89% 均须检出、回执 fail 并引用同一问题；100%/99.90% 仍通过。

证据：`rules_audit.json → current_percentage_receipt_probe`；精简结果 `business_probe_summary.json → percentage`。

### R5-02 · P1：V33-103/104/214 的问题作者仍缺事实/证据/关系 ID

**实际结果：** 使用合法 FactContext 执行生产 legacy payload，三个规则的问题均为 `check_relation_id=null`、`fact_ids=[]`、`evidence_ids=[]`；失败回执的问题引用分别为 0/2、0/1、0/1。BUD-107、CMM-001 的相应身份转发与链接已通过。

**影响：** 决算收入、支出和总计问题无法可靠从 finding 追溯到操作数，校正依赖、精确证据定位和后续 AI 关系比对仍缺链。仅修序列化适配器不能替作者生成缺失的真实身份。

**位置：** [收入规则作者](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:2397>)、[支出规则作者](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:2570>)、[总计规则作者](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:5261>)。

**整改及验收：** 作者在每个比较关系生成问题时携带真实关系、所有操作数事实及证据 ID；身份贯穿 legacy、engine、API、ReviewReport、下载。每条失败关系引用对应 finding，校正该事实后重算可追溯；不能把合成哈希补齐当作原始证据认证。

证据：`rules_audit.json → current_source_extended_probes.result.production_legacy_payload_identity`；`business_probe_summary.json → identity`。

### R5-03 · P1：三公混合取数的关系仍伪称完整事实计算

**复现：** 仅公务接待费提供 verified Fact=10，合计、出国、公车三项走旧表。保持 Fact 不变，把旧表合计 30 改为 31。

**实际结果：** `san_gong:table_total_balance` 随旧表变化由 pass 变 fail，两次却均为 `fiscal_fact/complete`，required/operand 只含一个 `reception` ID。公式需要四项金额，三项实际来源未进入操作数依赖。整项聚合已能降级，但该关系本身仍虚假完整。

**位置：** [三公总额勾稽](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:1100>)、[事实回执构造](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:1236>)。

**整改及验收：** 逐操作数记录来源；公式四项全部物化并绑定事实，或明确标记混合/legacy 未完成迁移。只改旧表操作数时，其依赖和回执必须同步改变；缺任一必需事实不得声明事实迁移 complete。叙述金额也需逐步成为带主体、章节、年度、阶段和证据的事实。

证据：`rules_audit.json → current_source_extended_probes.result.mixed_source`。

### R5-04 · P1：新解析语义继续命中旧 v4 缓存

**复现：** 同标题、同主体年度的合法收入表续页：首页合计100、事业收入40，续页财政收入60。按第四轮保存的源码还原旧解析，得到两个实例；当前解析得到一个合并实例。两版指纹均为 `pdfplumber-cells-v4-identity-boundaries`。

**实际结果：** 当前 `run_structured_ingest` 命中旧 run 88，加载 old-run 的两个实例，不持久化新结构，返回 done。新续页修复及新增物理格字段可能在已有缓存的本地任务上完全不生效。

**位置：** [解析指纹](</Volumes/workSSD/software development/GovBudgetChecker/src/services/pdf_parser.py:92>)、[缓存查找与读取](</Volumes/workSSD/software development/GovBudgetChecker/src/services/structured_ingest_runner.py:250>)。

**整改及验收：** 升级解析语义/产物版本，将本轮续页合并与 raw_rows/raw_cells 契约变化纳入缓存键；既有 v4 缓存需失效重建或明确迁移。先装入真实旧版本产物，再运行新版，应产生新 parse_run 和一个正确实例；新缓存第二次复用仍保真。

证据：`parse_audit.json → reproductions.round4_v4_parser_semantics_cache_replay`。此为真实 ingest 分支配 SQL 协议替身，非真实 PG 实跑。

### R5-05 · P1：常见两字机构简称仍造成本单位误报

**复现：** 同一说明先出现“教育局2026年……213012万元”，后出现本单位收入/支出328661.81万元，表格与本单位一致。

**实际结果：** `教育局` 未加入异主体排除，选择213012，实际 BUD-107 路径产生两条误报。乙局、乙单位、上海市普陀区财政局均已正确排除，说明当前测试只覆盖了较窄命名形态。

**位置：** [机构匹配](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1310>)、[短名匹配](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1318>)、[叙述金额选择](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1499>)。

**整改及验收：** 主体识别使用文档元数据、机构全称/简称和句子归属；多金额不能默认取首个。主体无法确定时转待复核。扩充教育局、财政局、下属单位简称及本单位合法简称的配对用例，既拒绝他单位金额，又保留真实本单位差异。

证据：`parse_audit.json → reproductions.other_org_unprefixed_two_character_name_boundary`，含直接提取与实际规则路径。

### R5-06 · P1：AI 契约拒绝项未持续隔离，仍可进入 agreement

**已修复：** 生产 `_build_ai_issue_item` 确实调用候选契约；critical/fatal 封顶 high，模型金额标记未核实；纯 AI 结果保持候选。单关系异常已隔离，强规则原件保留。

**剩余反例：** 一条候选同时声明 known-fact 和 invented-fact，或 `fact_candidates.value` 为非法数字类型，契约明确 rejected；构建器却保留合法 ID 子集和关系。merger 忽略拒绝状态，与同关系强规则产生 agreement，候选和待复核清单均为空。另 numeric relation 的 `evidence_ids=[]` 也可接受并进入 agreement。

**影响：** 当前反例没有使规则原件被删除，也不能据此声称 AI 产生了新的正式金额错误；但“拒绝项始终留在候选区、缺证据不构成可信交叉验证”的边界未成立。

**位置：** [生产拒绝分支](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ai_findings.py:356>)、[关系归组](</Volumes/workSSD/software development/GovBudgetChecker/src/services/merge_findings.py:23>)、[agreement 生成](</Volumes/workSSD/software development/GovBudgetChecker/src/services/merge_findings.py:196>)。

**整改及验收：** 契约接受状态显式进入 merger 的资格判断；rejected、引用不全或证据不足项保留候选/拒绝原因，不得作为 agreement 证据。数值关系验证 fact-evidence 对应关系，不能只验证 ID 在白名单。混合非法引用、非法类型、空证据均不得 agreement；合法完整引用保持正常配对，确定性规则不受影响。

证据：`evidence_audit.json → production_ai_candidate_contract`；云端模型未实跑。

### R5-07 · P1：实际 PDF 导出与一致性门禁契约不兼容

**复现：** 实际主链生成后，从下载入口保存五组 JSON/CSV/DOCX/PDF：校正前、问题消失后、后续新决定前后两份历史报告、最新报告。原样送入 `_evaluate_export_agreement`，五组均 `not_computable`，PDF 提示缺10或11项可见字段。专门构造的两问题正例可过门禁，因此该正例不足以代表实际产品导出。

**具体原因：** 十项为待复核记录的 `record_type=unfinished_check` 没有打印，PDF 用 status 覆盖了类型；另一个长原因跨页，其文本序列被重复附录页眉打断，门禁只去空白，整句匹配失败。**不是 PDF 丢失了11条审校结论。** 此外实际 PDF 将标题/严重度写在 ISSUE_ID 之前，门禁却从标记之后截取该行语义，可能借下一条标题/严重度匹配或误拒绝完整行。

**位置：** [问题排版顺序](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1674>)、[待复核类型输出](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1711>)、[可见字段及行核对](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:1367>)。

**整改及验收：** 统一实际生成器与门禁的每记录字段和边界契约；状态、类型分别输出；处理明确的分页页眉；绑定每条 ID 的标题、严重度、金额、定位和处理决定，不能只做全局关键词存在性。使用真实下载字节作为正例，再逐项互换/删除作为反例。历史决定保存已通过，不能因本项重新称其失败。

证据：`production_export_gate.json`；`evidence_audit.json → actual_pdf_export_field_diagnosis`；原样下载位于 `export_probe/`。已做图像检查：本机 Poppler 缺 Adobe-GB1 字体映射，中文实际页采用 PyMuPDF 补充渲染读取；合成英文门禁 PDF 经 Poppler 及图像检查。

### R5-08 · P1：人工定位校正接受并发布新版，但未改变事实定位

**复现：** 通过合法校正入口仅提交 `manual_location`。实际 main 重算发布新 revision；目标 bbox 请求从 `[200,195,320,235]` 改为 `[205,155,315,195]`。

**实际结果：** FactContext 仍保留旧 bbox；overlay 汇总为 `status=applied, applied=0`，unsupported 内注明 manual_location 未支持、`blocking=false`，而 `correction_overlay_incomplete=[]`。底层存在未支持记录，但主结论缺明确“定位校正未生效”状态。raw/unit/列映射等其他校正已验证，不能泛化为全部校正无效。

**位置：** [未消费字段定义](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:1054>)、[主链覆盖层](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:594>)、[未生效处理](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:1394>)。

**整改及验收：** 人工页码/bbox 覆盖应更新版本化事实和证据引用，并重新核验原 PDF；若暂不支持，应明确返回未应用和待复核状态，避免整体 applied 掩盖 applied=0。合法修正定位在新报告和四格式下载一致可见，旧报告保持原位置；越界/不含引文的定位仍不得认证 verified。

证据：`evidence_audit.json → legal_manual_location_main_boundary`。

## 五、未完成验收项

### R5-09 · P2：可信 OCR 自动晋升仍未交付可用闭环

当前安全行为正确：有歧义、单位低分或身份不明时拒绝/降级，摘要为 review_required、usable_for_rules=false。已有可物化候选事实的置信度仍固定为0.75，recognition_status=low_confidence，操作数为0。不能把“候选成功生成”当作扫描预算/决算可以自动勾稽。

位置：[OCR 晋升](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:290>)。证据：`parse_audit.json → reproductions.ocr_promotion_score_semantic_ambiguity`。

需要真实离线 CPU 模型、文件哈希清单、原格 bbox、数字与单位置信度、表格主体/阶段身份验证，再分层证明可信晋升与不可信隔离；不能通过提高固定分数强行开放操作数。这项尚未真实实跑，不报告扫描材料准确率。

### R5-10 · P1：冻结盲测与真实 API/Worker/PG/OCR 验收仍缺证据

ZC07 本轮返回 exit2 / `business_acceptance=not_passed`。登记29份材料，但冻结人工复核真值文件1份、缺陷组3组，原始 PDF 哈希认证数0；未提供冻结开发集/盲测清单与真实 E2E 证据。Docker CLI 存在，daemon 不可达；未找到模型文件清单。隔离测试进程未设置测试 PG URL，这只能说明本轮未进行 PG 验收，不推断用户生产数据库状况。

真实准确率、召回率、定位率、证据真实性、完成率、四格式一致性、重复运行指标均 **未计算，JSON 为 null/门禁 not_computable**。合成单例1.0和 pytest 通过数均不能填入这些业务指标。

沿用既定验收任务：冻结开发32 + 独立盲测32、每套8类分层各不少于4份、盲测不少于100个人工复核缺陷组；真实隔离部署完成原生预算、原生决算已知问题、扫描预算、混合决算校正四条 API→Worker→PG→OCR→下载路径，并记录 Linux/CPU/内存及模型/版本指纹。冻结盲测保留独立性，旧开发材料不能充当新盲测。

门槛沿用此前任务包：precision≥98%、recall≥95%、重大问题无漏报、精确定位≥98%、正式证据真实性100%、越界定位0、确定性适用检查完成≥95%、全部应检查状态/原因覆盖100%、四格式与重复运行100%一致。当前无法宣称达标。

## 六、工程检查及测试可信度

| 检查 | 当前源码结果 | 限制 |
|---|---|---|
| 全量 pytest | 2371 passed / 137 skipped，exit0 | 9条警告；跳过项不是通过 |
| tests/remediation | 155 passed / 2 skipped，exit0 | PG 实库和 cv2 项跳过 |
| Ruff | exit0 | 静态检查不能替代业务正确性 |
| 前端 test:unit / build | 均 exit0 | 未做真实浏览器端到端验收 |
| 环境配置一致性 / 日志检查 | 均 exit0 | 未作安全扫描结论 |
| Compose 基础 / AI overlay | 静态配置均成功 | 未启动服务；用了无秘密的占位环境 |
| ZC07 preflight | exit2，not_passed | 数据、服务及业务指标条件不足 |

检查了本轮测试改动：预算运行费测试捕获 RuleDeferred.partial_issues 是合法适配，仍保留2.68万元反例；证据测试增加哈希/引文等必需字段，bbox-only 保持不完整；新增门禁反例覆盖缺分母/冲突回执/缺真值区域/容差与引文变化。回放 JSON 新增身份字段明确标为合成 CI，不能作为真实性证据。金额政策和数学实现未改变，未发现用调宽阈值使测试通过。

但新增混合回执测试只验证手写回执聚合，未验证真实混合取数；机构测试缺教育局简称；缓存测试缺本轮同指纹语义变化；导出正例缺实际完整下载记录。R5 反例正是补这些边界，后续应固化为保留合法阴性和真实错误阳性的生产路径测试。

注册目录仍为82条，5条声明迁移完成、77条 pending，实际访问 FactContext 的也是5条。这不表示其余规则无法执行；表示不能称全规则已迁移为可追溯事实计算。应检查具体关系和实际回执，而不是目录总数。

## 七、下一轮交付顺序

1. **先修业务漏报与回执：** R5-01/02/03/05。每个数值关系绑定全部操作数、真实问题与证据，检查 pass/fail/未完成状态一致。不能只改测试断言、过滤掉应检错误或把空结果标通过。
2. **再修版本与交付闭环：** R5-04/07/08。已有旧缓存升级验证，人工定位重新绑定证据，真实下载四格式通过完整语义正例及互换反例，历史决定与历史报告保持隔离。
3. **再收 AI 与 OCR 边界：** R5-06/09。拒绝项持续留痕并隔离，可信 OCR 用真实模型与证据晋升，低质量材料继续待人工复核。
4. **最后独立业务验收：** R5-10。提交冻结清单、原始哈希、人审真值、真实四条服务链证据及资源记录，再计算业务指标。代码反例修复后才能进行最终发布判断。

每项交付应附：实现位置、旧反例翻转、合法正例不退化、版本指纹、命令与退出码、原始产物。下一轮复核无需重新打开已关闭的单项反例，但应检查其回归。

## 八、证据交付

证据目录：[本轮复核产物](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5>)。

- [结构化问题 JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/findings.json>)、[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/findings.csv>)：10项，区分代码缺陷与未完成验收。
- [业务反例精简结果](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/business_probe_summary.json>)、[规则完整复现](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/rules_audit.json>)。
- [解析完整复现](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/parse_audit.json>)、[校正/证据/AI 完整复现](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/evidence_audit.json>)。
- [门禁反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/root_gate_probes.json>)、[实际导出门禁结果](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/production_export_gate.json>)、[原样下载产物](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/export_probe>)。
- [全量测试日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/full_pytest.log>)、`*_run.json` 命令回执、[ZC07](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round5/zc07_preflight.json>)。
- `source_snapshot_before.json`、`source_snapshot_after.json`、`round4_comparison.json`、`working_tree.patch`、`artifact_manifest.json`：版本与证据可追溯记录。本轮仅新增复核报告及隔离产物。
