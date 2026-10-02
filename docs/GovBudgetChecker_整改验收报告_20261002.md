# GovBudgetChecker 整改验收报告

验收日期：2026年10月2日。验收分支：`codex/zcode-remediation`。被验收提交：`a06f42c64bcc8560d178facbae45f391dbce3e27`，对照整改前提交 `21a7671`。开始验收时工作区干净；本次没有修改业务代码。

**验收结论：未通过。当前完成了一批基础改造和已知反例修复，但尚未实现“输入预算或决算 PDF，基于可靠事实完成规则检查，准确定位问题，人工校正后重新计算”的完整业务链。**

最主要的原因是：统一解析对象和财政事实还没有成为主规则的输入；部分旧误报和漏查仍然可以复现；OCR只保存候选；证据核验仍有漏洞；人工校正只留痕、不重算。另有真实端到端和独立盲测尚未完成，不能计算或宣称业务精确率、召回率及精确定位率达标。

本轮改动可以作为继续整改的基础。当前版本不具备作为完整自动审核结果交付的验收条件；用于技术验证时，应保留未完成范围和人工复核状态。

## 一 验收依据与实际范围

业务规则以用户提供的 [AGENTS.md](</Volumes/workSSD/software development/GovBudgetChecker/AGENTS.md>) 中 `v3_3_portable` 为准，实施要求以 [Zcode 分阶段执行任务包](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_Zcode_分阶段执行任务包_V1.0.md>) 为准，缺陷基线来自 [原评估报告](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_可用性评估报告_20261001.md>)。条件性表判断是本项目的规则契约验收，本报告不作地区、年度法规合法性裁决。

沿用任务包的首版范围：上海市及区级部门、单位的单份预算或决算 PDF，包含原生、扫描和混合材料；本地 CPU OCR；规则优先，AI默认关闭并预留后续接口。材料包、跨文件关联及政府全口径总预算、总决算不作为本轮必须交付项。

检查并行覆盖文档与解析、表身份及完整性、数字和口径、证据及导出、人工校正和合并；主代理另行运行全量工程检查、复核规则反例及验收门禁。没有连接业务数据库、请求云端AI、改写原始PDF或对保留盲测材料运行审校。主要证据是当前源码、真实入口的隔离数据流探针、固定反例和实际测试输出。

数据流探针运行真实函数，但替换了解析传输、持久化或外部服务边界。因此，它们能证明输入输出和逻辑缺陷，不能替代真实 API、Worker、PostgreSQL、OCR 服务端到端验收。没有核实本机正在运行的后端及Worker的加载版本，本报告结论对应上述Git提交。

## 二 本次独立运行结果

以下是本次实际执行的结果，不是照抄实施方的阶段记录。专项和各检查线的测试与全量测试重叠，不能相加成独立测试总数。

| 检查 | 本次结果 | 验收解释 |
|---|---|---|
| 后端全量 `python -m pytest -q` | **2322 passed、1 failed、136 skipped、3 xfailed**，退出码1 | 工程回归并非全绿；跳过项和预期失败不能算完成 |
| 整改专项 `tests/remediation` | **105 passed、2 skipped、3 xfailed**，退出码0 | 已实施的局部契约通过；3项已知缺陷仍被允许失败 |
| 六个关键旧反例取消xfail | **3 passed、3 failed** | 跨年取数、三公局部检查假通过、条件性表判断仍失败 |
| 补充规则正反例 | 22组中13组符合预期、9组不符；主代理重放与检查线输出一致 | 是固定反例结果，不是系统误报率或漏报率 |
| 82条注册规则的事实状态访问探针 | 固定输入中0条读取附加事实，改变事实值和状态后0条输出改变 | 与源码的 `legacy_document/pending` 登记一致；不代表所有输入路径的穷尽证明 |
| 前端 `npm --prefix app run test:unit` | 通过，退出码0 | 适配器等单元检查通过 |
| 前端 `npm --prefix app run build` | 通过，退出码0 | 构建通过，不能替代真实业务端到端 |
| Ruff、环境变量对账、日志安全检查 | 均通过，退出码0 | 工程静态检查通过 |
| ZC07正式验收预检 | **not_passed，退出码2** | 冻结数据集、真实链路证据缺失，业务指标不可计算 |
| 类型检查及浏览器端到端 | 未完成 | mypy未安装；Playwright期望的Chromium不存在，本次未运行浏览器E2E |

专项两项跳过原因分别是未配置 `GOVBUDGET_TEST_DATABASE_URL` 和未安装 `cv2`。全量唯一失败为 `tests/test_pdf_parse_isolation_and_backup.py::test_memory_limit_is_enforced`：要求分配512MB时触发内存限制，实际没有抛出预期异常。该失败与实施方记录相同；本次没有在原提交重新跑全量套件，不能据此认定它是此次整改新增的回归。

实际日志：[后端全量](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/full_pytest.log>)、[专项及跳过原因](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/remediation_skip_details.log>)、[前端单元](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/frontend_unit.log>)、[前端构建](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/frontend_build.log>)、[验收门禁JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/zc07_preflight.json>)。

## 三 已确认有效的整改

这些成果应保留，不能因为整体验收失败就全部重写：

1. 正常路径复用同一次 `pdfplumber.open` 的句柄构建解析对象，结构化入库可接收该对象。
2. 新财政事实中的 `10000元、1万元、0.0001亿元` 都归一到1万元；契约、表实例ID和分析版本快照已建立。
3. 已知“同页小目标表被最大表覆盖”“下一页异表直接拼接”“三公列序调整”反例通过。已知三页预算夹具恢复为38、38、44行。
4. 连写“财政局部门决算”的主体识别反例通过；普陀、虹口、浦东三个合成标题均识别为部门。该结果不能外推所有文档画像。
5. 未知页码保持null，生成的问题说明不再自动作为原文；单条不存在的引文和第999页越界反例被拦截。表题检索不再制造目标金额的精确bbox。
6. 一对一、身份齐备的AI与规则合并保留规则代表项；金额冲突不再同时统计为一致，严重度冲突不再引发旧枚举异常。
7. ReviewReport和多类导出开始保留待复核、未完成检查、回执和范围说明；人工校正有追加式留痕及版本令牌校验。
8. 验收脚本在缺少真值时返回失败，不再把空样本输出为100%。

上述结论限定在对应测试和反例。新增反例表明，表身份、证据和合并仍有未覆盖的失败路径。

## 四 未通过问题清单

这里的P0/P1、critical/high用于描述系统缺陷影响，不是对政府公开材料定级。P0先修，会直接影响核心结论或证据；P1是本轮仍需完成的能力和验收项。系统问题没有政府材料页码，`page/bbox`均不适用，不能编造。

| 编号 | 优先级 | 系统问题 | 关联阶段 |
|---|---|---|---|
| AC-01 | P0 | 统一财政事实未进入主规则链 | ZC01、ZC03 |
| AC-02 | P0 | 目标表签名错位，表实例及续页身份仍不可靠 | ZC01、ZC02 |
| AC-03 | P0 | 本年检查仍取到上年说明金额 | ZC03 |
| AC-04 | P0 | 缺分项仍整项通过，回执未到事实关系级 | ZC03 |
| AC-05 | P1 | 条件性表仍无条件报缺失 | ZC03 |
| AC-06 | P0 | 声明容差与生产规则实际容差不一致 | ZC03 |
| AC-07 | P1 | OCR只留候选，扫描件规则审校未完成 | ZC02 |
| AC-08 | P0 | 同额不同列证据错绑，持久化丢失定位实体 | ZC01、ZC04 |
| AC-09 | P0 | 多证据真实性及精确bbox认证可被错误数据绕过 | ZC00、ZC04、ZC05 |
| AC-10 | P0 | 人工校正未改变审校事实、未重算、未产生新分析版本 | ZC05 |
| AC-11 | P0 | 重复AI可移走强规则正式项，生产身份与失败隔离未闭环 | ZC06 |
| AC-12 | P0 | 导出仍混用结论来源与定位摘要，历史版本下载缺入口 | ZC04 |
| AC-13 | P0 | 缺少正式数据集和真实服务验收，真实库测试自身还有断言错误 | ZC01、ZC02、ZC07 |
| AC-14 | P1 | 验收门禁部分指标未实现，E2E子校验仍依赖自报字段 | ZC07 |
| AC-15 | P1 | 代码默认关AI，配置模板和推荐部署仍开AI | ZC00 |
| AC-16 | P1 | 本机内存限制回归失败，目标部署保护未实证 | ZC00、ZC02、ZC07 |

### AC-01 统一事实尚未成为主审校输入

[真实主入口](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:1298>)仍用独立抽取的 `page_texts/page_tables` 构造旧Document；artifact随后用于结构化入库。[规则目录](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rule_registry.py:101>)的82条规则全部登记 `fact_inputs=[]、fact_migration=pending`。

隔离运行真实 `_run_pipeline` 时，故意让旧二维表含100、新artifact含999、OCR候选含777：规则接收100，入库接收999及777，规则Document没有artifact或FiscalFact。这里的金额是合成探针数据，不是某份真实材料的金额。这证明“同一次打开”与“共享同一事实”是两个不同的要求，前者已经改善，后者未完成。

**再次验收条件：** 在规则执行前生成统一事实；有事实依赖的规则、AI上下文、证据、入库均消费同一版本。无数据库时也应完成事实审校。对同一输入改变被校正事实后，相关规则输出必须变化；unverified、ambiguous、low_confidence不能被当作确定的操作数。不得继续把改善入库旁路视为修复主规则。

### AC-02 表身份修复不完整，并有新增错绑

[预算签名字典](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:415>)的T6—T9与正式表规格错位，T9写成“国有资本经营”。同页放三公表和国资表时，T9选择了国资表999的合计。旧“小表与大表”测试通过，不能覆盖这个反例。

[原生逻辑表构建](</Volumes/workSSD/software development/GovBudgetChecker/src/services/pdf_parser.py:247>)先合并表，再将合并结果当成片段；[续页判断](</Volumes/workSSD/software development/GovBudgetChecker/src/services/pdf_parser.py:746>)在同表种时直接允许合并。相邻“甲局2024收入表”“乙局2025收入表”被合成一个实例，单元格来自第1、2页，逻辑实例却仍 `page_end=1`，主体、年度也未进入实例身份。

[预算续页路径](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:474>)还存在三个可执行反例：同表头但新独立标题仍被拼入；短表的“合计”列头导致提前停止；歧义续页在543—544行仍回退取最大表。

**再次验收条件：** 从同一表规格生成签名及适用配置；保留原始片段ID、物理页和单元格；以主体、年度、资金、单位、标题及表头路径确认逻辑实例和续表边界。标题冲突或多表歧义必须转未完成，不得回退猜最大表。增加相同表头不同主体/年度、表头含合计、同页多种表的正反例。

### AC-03 上年叙述仍污染本年金额比对

既有建管委预算夹具原本通过。只在前部追加明确标注2025年的历史收入、支出预算213012万元，本年正文及表格328661.81万元不动，BUD-107产生两条不一致。该旧反例取消xfail后仍失败。[源码](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:2495>)仍从全文提取首次匹配金额。

**再次验收条件：** 绑定主体、年度、资金口径、预算阶段及具体章节事实；有多个候选时说明歧义。历史段位置变化不得改变本年结果；本年真实数字差异仍须检出。移除 `test_xfail_previous_year_narrative_misbinding` 的xfail。

### AC-04 检查回执仍会掩盖漏查

三公表分项完整，说明仅写合计30万元，没有出国、公车小计、购置、运行和接待五个分项说明时，CMM-001仍返回pass。[回执构建](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/check_obligations.py:1407>)把规则总状态复制到义务与规则配对，`required_fact_ids/operand_fact_ids`为空、`formula=null`，不能证明每个适用关系完成。

**再次验收条件：** 为各分项、合计关系和表文关系生成实际子检查回执，保存操作数事实ID、公式、适用性、结果和未完成原因，再聚合总状态。已确认问题应保留；缺字段、解析失败应计未完成。移除 `test_xfail_partial_comparison_reported_pass` 的xfail。

### AC-05 条件性表政策仍与portable不一致

预算基金、国资表在适用情况未知，或者材料明确“本单位未安排”时，仍报缺表。[缺失表判断](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1680>)未完成“有正面适用证据才要求出现”的项目政策。

**再次验收条件：** 分开未知、不涉及、确有预算三种状态；未知不能直接判违规缺失，无此项说明必须参与判断，确有预算但缺表的正例仍须检出。决算的空表/等效说明检查另行记录。移除条件性表反例的xfail。

### AC-06 统一容差只部分接入生产

统一helper声明0.5万元，但[总表规则](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:5049>)仍按0.01比较：100与100.10、100.50都报错。另一方面，[旧相对容差](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:255>)使V33-103的表1000、文字1000.60通过，超过portable的0.5万元也未报。

回执写“配置容差0.5”不代表本次计算实际使用了它。**再次验收条件：** 所有生产金额、百分比及同比关系调用实际政策；记录本次使用的单位、绝对容差和独立舍入包络。对0.5边界内外和0.1个百分点边界内外同时验证，不能通过放宽容差掩盖真实错误。

### AC-07 OCR候选尚未转为可检查事实

[OCR流水线](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_pipeline.py:219>)记录 `candidate_only`；主链探针中的OCR文本和表格确实进入artifact，但 `usable_for_rules=false`，没有进入主规则，最终诚实返回 `review_required`。

这项保护有效，但扫描件自动审校未实现。**再次验收条件：** 完成本地模型锁定、离线启动、扫描和混合页实跑，保存OCR到原PDF的坐标变换；经置信度、列身份和关键数字复验后生成可消费事实。不可靠候选继续待复核，不能为通过而直接把所有OCR数据设成verified。

### AC-08 财政事实的证据错绑和定位实体丢失

[事实物化](</Volumes/workSSD/software development/GovBudgetChecker/src/services/fiscal_fact_materializer.py:494>)按同行金额相等反找首个单元格，而不是按来源列映射。总额和基本支出同为100时，两事实绑定同一证据ID。构造的EvidenceRef只保留ID，[持久化](</Volumes/workSSD/software development/GovBudgetChecker/src/services/parse_artifact_store.py:240>)仅写quote、unverified、unresolved，没有物理页、bbox和来源单元格。

**再次验收条件：** 根据实际列映射保留来源单元格；事实及完整EvidenceRef一起传递、存储和读取。同额不同列仍应有不同来源身份。数据库往返后的页、bbox、文件哈希、单元格ID和单位应完全一致，并能从报告跳回原件。

### AC-09 证据核验尚未逐条绑定原件

[引用核验](</Volumes/workSSD/software development/GovBudgetChecker/src/services/evidence_guard.py:285>)使用“任一引文在任一所称页命中”。因此，一条真实的“收入100”能掩盖另一条不存在的“支出999”；互换两条证据的页码也仍verified。主代理另行复现：不存在的金额999作为text_snippet，附一条真实100的证据，整体仍 `complete=true、verified`。

[bbox定位](</Volumes/workSSD/software development/GovBudgetChecker/src/utils/issue_bbox.py:80>)接受自报exact/verified及页内边界。合成PDF中的空白区域、错误文件hash、虚构证据ID仍被接受为精确定位。[证据读取接口](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/corrections.py:298>)只验证页和边界，也可原样返回错误quote/hash的verified标签。纯bbox无引文的规则项另可被统计为正式问题，完整性与真实性未彻底分离。

**再次验收条件：** 每个操作数证据分别验证文件哈希、对应物理页、引用及来源cell/span；精确bbox由解析器保存的可信引用导出，不能由模型或输入自报verified。某一必要证据失败时，相应关系必须待复核，不能靠另一条真实引文整体通过。区域导航与精确定位分别计数。

### AC-10 人工校正只留痕，不修正审校结果

[校正接口响应](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/corrections.py:486>)明确返回 `new_analysis_revision=null`、`reanalysis_status=not_executed_fact_overlay_not_consumed_by_production_rules`。即使已有真实子检查依赖映射，100校正为90后，原事实仍100，发布版本不变，只新增JSONL记录；未知target也能作为校正记录接受。

**再次验收条件：** 校正对象须存在于指定事实版本；形成不可变事实覆盖层和新分析版本；重跑受影响检查，无法安全缩小范围时完整重跑。旧结果和复核决定保留并标明失效范围；新报告及各导出反映校正后的值。界面不能把“提交留痕”描述为“校正已生效”。

人工复核也未完整进入统一报告：隔离执行真实workflow更新为confirmed后，ReviewReport仍 `human_decisions=[]`，问题缺少对应复核状态。[复核事件存储](</Volumes/workSSD/software development/GovBudgetChecker/src/services/issue_workflow_store.py:854>)与报告投影尚未接通。复核事件应按问题、事实、检查及分析版本关联，并同步到界面和导出；人工确认问题与人工修正数据是两种动作，都需要明确生效范围。

### AC-11 合并和AI接口仍有业务缺口

函数级一对一旧缺陷已经修复。但新增“同关系两条low AI候选＋一条critical规则”反例使 `merged_ids=[]`，强规则被放入manual_review，ReviewReport正式问题列表为空。原始规则没有从存储中删除，但它从正式结论中被移走，不满足保留强规则的要求。[歧义关系处理](</Volumes/workSSD/software development/GovBudgetChecker/src/services/merge_findings.py:111>)及[规则候选过滤](</Volumes/workSSD/software development/GovBudgetChecker/src/services/merge_findings.py:199>)是直接路径。该反例在身份齐备的合并函数中复现；当前生产适配器缺少这些身份，是下面的另一项缺口，不能混写成默认关闭AI时已经发生的线上漏报。

[AI构建器](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ai_findings.py:225>)和[规则适配器](</Volumes/workSSD/software development/GovBudgetChecker/src/services/engine_rule_runner.py:110>)即使收到fact/check/evidence IDs，输出也丢弃；离线AI候选契约未接管生产入口。另对单个关系注入异常，整个AI批次仍被清空，[双模式全局回退](</Volumes/workSSD/software development/GovBudgetChecker/src/services/analyze_dual.py:392>)没有完成逐条失败隔离。故障注入证明隔离缺失，不表示正常输入必然抛异常。

**再次验收条件：** 强规则确认的问题不能因AI重复或更弱候选而退出正式集合；保留全部来源并对候选去重。生产端贯通关系和事实ID，AI仅提交受契约约束的候选。单条异常保留其他已完成结果及出错回执。AI默认关闭不影响规则审校，后续接入前仍需离线候选测试通过。

### AC-12 多格式交付尚未完全同源

正常合成样例的JSON、CSV、Word、PDF附录保留未完成范围，这部分通过。但是：[导出定位摘要](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:399>)可取得第2页证据bbox，同时把主page写成第1页；[导出结论](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:608>)仍读取顶层analysis_conclusion，在canonical报告为incomplete、顶层为no_findings时判完整。当前[下载路由](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1604>)只有request、job_id、format，没有指定analysis_revision的入口。

这些是摘要和单一来源边界反例：摘要的顶层定位仍unverified，前端已有同页/等级保护，PDF标注按各ref自身页处理，本次没有据此认定实际PDF已经误画；结论冲突也不表示正常产线必然产生两个相反结论。但既然保留多个来源，就必须保证冲突时以选定版本的报告为准。

**再次验收条件：** 页码、bbox、定位等级和证据ID作为一个对象输出；结论、问题和未完成范围只从选定版本的ReviewReport读取。禁止并行保留互相冲突的结论来源。界面、JSON、CSV、Word及标注PDF逐项比对，并允许下载历史分析版本。

### AC-13 真实业务和服务验收材料不足

本次门禁盘点：登记29份材料，golden文件1份，人工冻结缺陷组3个；通过冻结真值原件哈希核验的数量为0。这不表示29份登记原件全部不存在，而是目前没有满足正式验收身份要求的数据集。

尚未提供64份分层真实PDF的开发/盲测清单、至少100个盲测真实缺陷组和四条真实服务场景证据。本机Docker daemon不可达，测试数据库地址、OCR模型清单缺失，Chromium也未安装。真实PG、OCR和浏览器链路因此保持未验收，不能用模拟测试替代。

另外，跳过的[真实数据库测试](</Volumes/workSSD/software development/GovBudgetChecker/tests/remediation/test_zc01.py:650>)含 `assert written == len(facts_a) + facts_b`，其中facts_b是列表；到达此处会产生int与list相加的TypeError。这是源码可确定的测试错误，本次没有连接数据库执行该路径。应改正断言后实际跑库往返测试，不能只解除skip就宣称覆盖完成。

**再次验收条件：** 用隔离测试库和真实容器完成上传→Worker消费→原件定位→复核→校正重算→导出的四场景实跑，记录运行、代码、配置、原件、模型和结果哈希。冻结开发32、盲测32及人工真值，保留8个分层，每层每集至少4份，盲测真实缺陷组至少100个。已经用于修复的样本不得再作为独立盲测。

### AC-14 验收脚本仍有未实现指标和弱证据校验

[正式门禁](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:651>)目前把精确定位、证据真实性、检查完成率、回执覆盖、导出一致性和重复运行等指标固定为not_computable。即使补齐数据，也还需要实现这些评测，而不只是填写数据文件。

[E2E子校验](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:369>)核对自报布尔值和trace文件哈希，没有核对真实源PDF/导出/模型内容及API、Worker同版。主代理构造空trace `{}`、不存在的原件与导出、不同的API/Worker版本，子校验仍返回ready。**这不表示整个业务门禁能够通过**：其他门槛仍会失败；它说明E2E证据子验证尚不能独立证明真实链路。

**再次验收条件：** 自动采集可追溯运行证据并校验实际产物、版本和关联ID；实现原件区域匹配、子检查分母、回执、全导出一致性及重复运行评测。不允许把缺失指标直接改成pass，或用自报true替代真实执行记录。

### AC-15 默认关闭AI未覆盖模板和推荐部署

[代码默认](</Volumes/workSSD/software development/GovBudgetChecker/api/config.py:15>)已改为false。但 [.env.example](</Volumes/workSSD/software development/GovBudgetChecker/.env.example:79>)仍为true，README仍写默认true，并推荐加载 [docker-compose.ai.yml](</Volumes/workSSD/software development/GovBudgetChecker/docker-compose.ai.yml:18>)，该配置显式开启AI且要求AI extractor就绪。使用AI专用配置可以合理地启用AI；问题在于尚缺与本轮“规则优先、默认无需AI”一致的默认部署交付。

**再次验收条件：** 给出无模型密钥、无云端请求的默认规则＋本地OCR部署；模板、README、API和Worker一致；AI专用模式由显式配置开启。验证无AI服务时规则任务和就绪检查正常。

### AC-16 内存保护回归仍未通过

本机全量测试的内存限制断言失败。不能把其他2322项通过概括为全绿，也不能仅删除或跳过这个测试解决验收。

**再次验收条件：** 核实macOS与实际Linux容器的限制机制，使用可执行的进程隔离或容器资源上限；在目标部署验证超限任务受控终止、生成失败回执、不污染后续任务。平台差异可以明确记录，目标部署保护必须有实跑证据。

## 五 分阶段与原缺陷对照

| 阶段 | 本次独立判定 | 剩余关键条件 |
|---|---|---|
| ZC00 止血与基线 | 部分通过 | 多证据核验、默认部署一致、工程失败项 |
| ZC01 契约与事实 | 部分通过，整阶段未通过 | 主规则消费、独立表身份、完整证据、真实库往返 |
| ZC02 解析与OCR | 部分通过，整阶段未通过 | 新错表/续页反例、OCR事实消费、真实模型与容器验证 |
| ZC03 规则与回执 | 未通过 | 全事实关系迁移、3项旧反例、实际容差、子检查回执 |
| ZC04 证据与报告 | 部分通过，整阶段未通过 | 来源认证、导出一致、指定历史版本下载 |
| ZC05 校正与复核 | 留痕功能通过，业务闭环未通过 | 校正生效、重算、新版本、旧决定失效 |
| ZC06 合并与AI接口 | 部分通过，整阶段未通过 | 强规则保留、生产身份、候选契约、逐条异常隔离 |
| ZC07 真实验收 | 未通过 | 数据、环境、真实链路和完整评测均待补齐 |

实施方ZC02—ZC07阶段记录本身也明确写了“部分完成/未通过”。本次独立结果与这些限制一致；提交名称包含某阶段，并不等于该阶段业务已经验收。

| 原缺陷 | 现状 | 本报告对应 |
|---|---|---|
| SYS01 主链与旁路 | 一次打开改善，统一消费未完成 | AC01 |
| SYS02 目标表/续页 | 旧反例修复，新增错绑和续页失败 | AC02 |
| SYS03 年度绑定 | 未修复 | AC03 |
| SYS04 三公列序 | 已知反例通过；全身份迁移未完成 | 已修复项、AC01/02 |
| SYS05 子检查假通过 | 未修复 | AC04 |
| SYS06 合并 | 一对一旧反例修复，重复AI及生产链仍失败 | AC11 |
| SYS07 假证据 | 单条保护改善，逐证据真实性未完成 | AC08/09 |
| SYS08 规则/容差统一 | 目录和helper建立，实际容差不统一 | AC01/04/06 |
| SYS09 黄金集 | 正式数据及指标仍缺 | AC13/14 |
| SYS10 OCR | 候选留痕实现，规则审校未完成 | AC07 |
| SYS11 bbox/翻页 | 模糊定位移除、导航改善，来源认证未完成 | AC09/12 |
| SYS12 导出范围 | 常规样例改善，结论同源仍有反例 | AC12 |
| SYS13 条件性政策 | 未修复 | AC05 |
| SYS14 人工反馈 | 追加留痕实现，校正及受控评测未闭环 | AC10/13 |
| SYS15 单位归一 | 新事实等值单位通过，主规则统一政策未完成 | 已修复项、AC01/06 |
| SYS16 表实例身份 | ID契约建立，实际合并仍冲突 | AC02 |
| SYS17 财政局主体误判 | 已知反例通过，完整画像准确率待真值验证 | 已修复项、AC13 |

## 六 待复核和业务指标

以下事项没有足够实际证据，本报告不替它们判通过，也不假定线上必然失败：真实OCR准确率及二次识别、arm64主机运行amd64 CPU容器的启动和性能、PG空库迁移及往返幂等、扫描/旋转/混合页面坐标、正在运行的API与Worker版本一致性、真实用户的完整页面操作链。

解析缓存的OCR指纹查询与传入artifact不一致，以及缓存命中仍沿用新随机实例ID，也有源码风险；尚未完成真实数据库复现，列入再次验收的缓存往返项目，不能冒充实证故障。详见解析附件。

| 任务包指标 | 目标 | 当前结论 |
|---|---|---|
| 正式问题精确率 | ≥98% | 不可计算，无正式盲测结果 |
| 真实问题召回率 | ≥95% | 不可计算；待复核候选不能计正式命中 |
| 核心及重大错误漏报 | 固定验收集0漏报 | 未证明，且已有漏查/正式项被移走反例 |
| 精确定位率 | ≥98% | 不可计算；页码命中不是精确区域命中 |
| 证据引用真实且页码不越界 | 100% | 未证明，且有核验绕过反例 |
| 必查子项完成率 | ≥95% | 不可计算，当前回执不能证明关系级完成 |
| 回执覆盖及未完成原因 | 全部适用子项 | 未证明，操作数和公式为空 |
| 各格式一致及重复运行 | 全部一致，可重复 | 未证明，已有摘要和结论不一致反例 |

不输出综合“完成百分比”。不同阶段的基础代码、单测和业务结果不是同一分母，用一个百分数概括会误导。

## 七 返工顺序与再次验收要求

**第一批先让同一事实进入主链。** 完成AC01、AC02、AC08；按真实列和主体年度构建事实与证据，再迁移有事实依赖的生产规则。先打通原生PDF，也要保留OCR和校正的统一入口。

**第二批关闭误报和漏查。** 完成AC03—AC06、AC09、AC11；将三个xfail移除，并把本报告新增反例及能检出真实错误的正例纳入可提交测试。每条关系产出真实回执；保留已确认规则问题。

**第三批完成用户操作闭环。** 完成AC07、AC10、AC12、AC15及目标部署资源保护：OCR候选转事实，人工校正驱动重算和新版本，页面及导出消费选定版本报告，默认规则模式独立运行。

**第四批做真实验收。** 修正真实库测试断言，完成AC13、AC14和真实服务运行记录；再按冻结盲测计算各项指标。人工真值准备可以与前面三批并行，但未冻结的答案不得用于宣布业务通过。

下一次提交必须附每个AC编号的：修复位置、进入真实上传链的说明、正反例结果、已知限制和实际运行产物。不能仅提交“新增接口/新增类型/测试全绿”的说明。工程通过、阶段验收通过、业务指标通过必须分别记录。

## 八 交付与复现附件

- [结构化验收问题清单JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/acceptance_findings.json>)及[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/acceptance_findings.csv>)。
- [解析检查附件](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/parse_audit.json>)：6组可执行探针、实际输出、源码位置及未实证缓存风险。
- [规则检查附件](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/rules_audit.json>)：旧反例、22组正反例、82条状态访问探针和可执行代码。
- [主代理规则重放摘要](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/root_rules_probe_summary.json>)：与规则附件输出一致。
- [证据及交付检查附件](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/evidence_audit.json>)：旧保护通过、新证据/合并/校正/导出反例及可执行代码。
- [主代理证据反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/root_evidence_probe.json>)及[E2E子校验反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/acceptance_20261002/root_gate_probe.json>)。

这些固定反例是开发验收输入，不能计入真实盲测缺陷组或业务误报率。`outputs/`当前被仓库忽略，验收报告已经把关键输入、实际输出和返工标准写入docs；返工时应将新增反例转成仓库可提交测试，避免只依赖本机附件。
