# GovBudgetChecker 整改复核报告

复核日期：2026年10月3日。分支：`codex/zcode-remediation`。被复核提交：`edb4e6d7ecaef83252fb1844d0a6c45e4e6b3f07`。对照上一轮提交：`a06f42c64bcc8560d178facbae45f391dbce3e27`。

**复核结论：工程回归通过，业务验收仍未通过。** 本轮确实修复了部分旧问题，但尚不能证明系统能够可靠完成“解析→事实审校→精确证据→人工校正重算→同版本导出”的闭环。主要阻断项是规则迁移不实、操作数选择和检查完成状态不可靠、低置信度OCR晋升、校正目标在重解析后失效、证据认证旁路，以及正式验收评测本身的错误。

这不是对本轮修改全部否定：统一事实已经接入主流程，人工校正已经能够启动重算和生成新版本。上一报告中“事实完全未接入”“校正只留痕不重算”的描述，不再适用于当前提交。当前的问题是接入后仍有规则绕过事实，重算后校正未生效。

本轮未修改业务代码。初始工作区已有未跟踪目录 `.mimosa/`，予以保留；新报告和复核产物单独保存。结论对应上述Git提交，未核实用户正在运行的API、Worker是否加载同一提交。

## 一 依据、范围与证据边界

沿用 [上一轮验收报告](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_整改验收报告_20261002.md>) 的AC-01至AC-16，以 [AGENTS.md](</Volumes/workSSD/software development/GovBudgetChecker/AGENTS.md>) 的 `v3_3_portable` 和 [Zcode分阶段任务包](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_Zcode_分阶段执行任务包_V1.0.md>) 为验收依据。本次同时核对了 [实施方AC整改记录](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_AC整改记录_20261002.md>)；“已完成”须由实际生产入口及反例验证，不能只由记录或注册标记证明。

首版范围不变：上海市及区级部门、单位的单份预算或决算PDF，包含原生、扫描、混合材料；本地CPU OCR；规则优先，AI默认关闭并预留后续接口。跨文件材料包、政府全口径总预决算不是本轮新增要求。

三条检查线并行复核解析与事实、规则与口径、证据与交付，主检查线运行工程回归和门禁反例。证据分为：

1. 实际测试及当前源码。
2. 真实函数的隔离探针：外部数据库、模型或传输由替身隔离，用于证明逻辑输入输出。
3. **合成中文原生PDF的真实主流程**：运行PDF解析、事实物化、生产规则、校正接口、已注册的主流程重算、新旧版本及JSON下载；认证和存储作测试隔离，不连接业务库、不调用AI或真实OCR模型。

第3类能证明校正闭环的生产代码缺陷，但仍不能替代真实API/Worker/PostgreSQL/OCR服务端到端验收。探针金额、页码和bbox均来自合成夹具，不能视作政府材料问题或业务准确率样本。本轮没有使用保留盲测原件调规则，也没有编造真实材料页码、缺陷或准确率。

## 二 独立执行结果

专项与各检查线测试包含在全量套件内，不能相加为独立测试总数。

| 检查 | 本轮结果 | 与上一轮比较及限制 |
|---|---|---|
| 后端全量 `python -m pytest -q` | **2337 passed、137 skipped，0 failed、0 xfailed**，退出码0 | 上轮2322通过、1失败、136跳过、3预期失败；本轮工程回归通过，跳过项仍未验证 |
| 整改专项 `tests/remediation -q -rs` | **120 passed、2 skipped，0 xfailed** | 3个旧xfail已转正式测试；两项仍因测试数据库未配置、cv2缺失而跳过 |
| 前端单元 `npm --prefix app run test:unit` | 通过，退出码0 | 适配器等单元通过，不能替代浏览器业务链路 |
| 前端构建 `npm --prefix app run build` | 通过，退出码0 | 构建成功 |
| Ruff、环境变量对账、日志安全 | 均通过，退出码0 | 静态工程检查通过 |
| 正式ZC07预检 | **not_passed，退出码2** | 冻结数据集与真实E2E证据缺失，业务指标不可计算 |
| 类型检查、浏览器E2E | 未完成 | mypy未安装，Playwright期望的Chromium不存在，本轮未运行浏览器E2E |

内存限制原失败项现在先检测平台是否实际执行 `RLIMIT_AS`，本机检测后跳过。这是对平台差异的如实处理，不构成Linux目标部署的内存保护通过证据。

独立补充探针也运行了真实错误正例，避免仅验证“少报问题”：BUD-107消费已验证的改变金额后会报差异；完整三公表文一致时通过；差额0.51万元的决算三公比对会报错；确有基金/国资预算但缺表的单独场景能够报出。与此同时，多个新增边界反例仍失败，见下文。

实际日志与证据：[后端全量](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/backend_full.log>)、[整改专项](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/remediation_tests.log>)、[前端单元](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/frontend_unit.log>)、[前端构建](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/frontend_build.log>)、[ZC07预检](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/zc07_preflight.json>)。

## 三 AC逐项复核

“局部通过”仅指明确的旧反例或功能已通过；整项是否完成还取决于剩余条件。本轮不使用不具业务含义的“整改完成百分比”。

| 原编号 | 已确认改善 | 当前判断与剩余条件 |
|---|---|---|
| AC-01 统一事实主链 | 无数据库也可构建FactContext，主入口和Worker接入；BUD-107部分实际消费 | **部分修复、未通过**：82条规则固定访问探针仅BUD-107读取；5条声明迁移完成中另4条仍读旧表；事实筛选和拒绝后的回退存在错误 |
| AC-02 表身份与续页 | 已知错签名、短表合计表头、歧义取最大表、异年度合并、续页跨度反例已修 | **部分修复、未通过**：同年异单位仍合并，原始片段/单元格身份未完整保留，单位冲突边界不可靠 |
| AC-03 年度及口径 | 同句明确上年的旧反例已修，旧xfail已移除 | **部分修复、未通过**：历史章节标题跨行、异主体、调整后预算仍污染本年比较 |
| AC-04 子检查与回执 | 新增关系级回执，纯“只有合计”的旧三公反例已转未完成 | **部分修复、未通过**：加一个分项后其余缺失仍整体pass；回执空操作数、错操作数及不适用状态掩盖漏查 |
| AC-05 条件性表 | unknown、不涉及、单项确有预算等旧场景通过 | **部分修复、未通过**：基金金额污染国资适用性，零金额和历史预算也可报本年缺表 |
| AC-06 实际容差 | 部分生产比较改用0.5万元；旧总表边界等反例改善 | **部分修复、未通过**：决算CMM-001仍按0.01，回执却声明0.5万元 |
| AC-07 OCR事实 | 候选晋升入口已接入，能够生成事实，扫描质量门仍保留待复核 | **部分修复、未通过**：晋升不使用真实数字置信度；通用表头错认表种；扫描事实尚不能驱动完整生产规则 |
| AC-08 证据绑定存储 | 同额不同列分别绑定来源格；SQL写入完整EvidenceRef字段 | **旧反例局部通过、完整验收待补**：真实库往返未跑，生产问题ID贯通及缓存版本错绑仍未解决 |
| AC-09 证据认证 | 多引文逐条按自称页核验；空白主bbox和错误hash读取旧反例改善 | **部分修复、未通过**：table_refs旁路可进入实际PDF标注；hash、physical_page及缺字段认证仍有漏洞 |
| AC-10 校正与复核 | 能排队重算、保存新分析版本；旧版本可保留 | **部分修复、未通过**：重解析导致事实ID改变，校正被拒仍按旧值重算；部分校正字段静默丢弃，复核投影不即时、不按版本绑定 |
| AC-11 合并及AI | 重复弱AI不再移走强规则；适配器保留已提供ID；异常不清空全批 | **部分修复、未通过**：生产规则未提供关系身份；候选契约未接管AI入口；缺逐项失败回执 |
| AC-12 报告与导出 | canonical结论优先；定位摘要不借用异页bbox；支持指定历史版本 | **部分修复、未通过**：真实主流程报告revision为空；PDF共享输出文件，历史/当前下载可串版 |
| AC-13 真实数据和服务 | 真实库测试的int+list断言已修 | **未通过**：冻结开发/盲测、真实服务及真实库证据尚缺 |
| AC-14 验收脚本 | 新增指标函数；开始核对实际原件/导出文件与API/Worker版本 | **部分修复、未通过**：定位未比真值、接口不一致、分母漏项、导出评测异常、重复运行漏比结论，另有永久不可通过门项 |
| AC-15 默认部署 | `.env.example`和README默认AI改为false | **部分修复、未通过**：推荐完整部署仍加载强制AI覆盖配置，缺首版可实跑的默认规则/OCR全链交付 |
| AC-16 内存保护 | 可执行平台探针，macOS差异诚实记录 | **目标部署待验**：本机跳过不能替代Linux容器受控超限实跑 |

## 四 剩余问题及再次验收条件

以下RC编号用于本次系统整改。P0/P1和严重度描述软件缺陷影响，不是政府公开材料定级；`page/bbox`不适用于系统问题。可导出的 [JSON问题清单](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/findings.json>)、[CSV问题清单](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/findings.csv>) 与本节对应。

### RC-01 / P0：事实接入后，规则迁移和取数仍未完成（AC-01）

[规则登记](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rule_registry.py:24>)标记BUD-107、CMM-001、V33-103、V33-104、V33-214为迁移完成，但82条固定访问探针仅BUD-107读取真正的 `doc.fact_context`。对另4条保持旧表不变，将相关事实翻倍或改为ambiguous/low_confidence，其输出均不变。V33-214旧表100/100、事实100/999不报；反向输入则报。这里只能证明探针和对应实现路径，不能将82条固定输入结果当成所有规则路径的穷尽证明。

BUD-107真实读事实，但[实现](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:2863>)仍先要求旧二维表和原生单位。没有旧表、只有事实时不能完成比对；歧义或低置信事实被拒后仍使用旧表值，返回pass。另已执行两个选择器反例：明细50位于合计100之前时把50当总计；同时存在verified100和unverified999时，[排序](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:146>)反而选择999。

**修复与验收：** 所有首版必查关系消费统一版本的财政事实；旧表仅作诊断参考。总计须由指标、行角色、层级及口径唯一确定，不能取首个同指标事实。歧义/低置信事实须转未完成，不能静默回退为确定旧值。注册迁移状态由实际输入依赖证明；同事实改值、改状态、调行序的正反例均须通过，并验证无数据库、无旧二维表时仍能执行。

### RC-02 / P0：表与来源身份仍跨主体混合（AC-02）

原生解析探针中，“甲局2024年度收入决算表”和“乙局2024年度收入决算表”合为一个逻辑实例，`org_name=null`，两页事实均回退封面单位。同标题但万元/元冲突也被合并。不同年度分开、明确续表跨页范围正确，这些修复有效。

[构建与合并](</Volumes/workSSD/software development/GovBudgetChecker/src/services/pdf_parser.py:251>)仍把合并表替换为物理片段，虽然保留A/B片段ID列表，却没有保留对应原始对象和原始单元格ID；后页单元格使用新合并ID和重排行号。旧预算二维路径的同表头异独立标题边界也仍可误拼。

**修复与验收：** 保留不可变物理片段及来源格，逻辑表只引用片段。主体、年度、资金、阶段、单位和表头须进入实例边界；冲突不能自动合并。真实续页仍要成功。验证同年异主体、单位冲突、独立新标题和多页续表，并检查来源格能够回到原页。

### RC-03 / P0：叙述取数未绑定完整上下文（AC-03）

旧“同句出现上年年份”的案例修复有效。但生产BUD-107对三个新输入仍误报：历史年份只写在上一行章节标题；相同年份但另一单位的说明；本年调整后预算说明位于年初预算说明之前。当前提取主要按匹配句局部年份过滤，[表文比对入口](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:2895>)尚未建立章节和事实上下文。

**修复与验收：** 文字金额也应成为带章节、主体、年度、资金和预算阶段的事实。与表侧身份完整匹配后比较；上下文无法确定时待复核。历史段前移、标题换行、主体不同和预算阶段不同不得改变本年结果，本年真实金额错误仍须检出。

### RC-04 / P0：缺分项仍假通过，回执不能证明实际操作数（AC-04）

仅有合计的旧三公反例现在返回insufficient_data。但说明“合计+出国0”，其余应检分项缺失时，预算、决算CMM-001均pass。缺少数值被[记为not_applicable](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:540>)，一个已完成分项就使整体完成；缺披露不能自动等同无此项。

关系回执确已新增，但CMM-001操作数ID为空。BUD-107三公四个字段的回执全部引用最后的“机关运行”事实；真实主流程发现的收入差异，其回执 `issue_refs=[]`，问题项 `fact_ids/check_relation_id/evidence_ids=null`。新增回执的存在，不能证明关系已正确计算和定位。

**修复与验收：** 先枚举本材料适用关系和必需操作数，再执行和聚合。缺字段为未完成，明确不适用须有证据。每关系分别保留真实操作数ID、公式、实际政策、结果及问题ID；有确定差异又有漏查时同时保留差异和未完成状态。关系级回执与汇总回执须区分，不能重复充当完成率分母。

### RC-05 / P1：条件性表正面证据跨表种、跨年度污染（AC-05）

unknown、明确未安排、单独基金/国资正面金额均已改善。但“基金有预算、国资无预算”的探针同时要求两表；金额0和历史基金预算也可能同时要求两表。[适用性函数](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1992>)对两表共用全文金额正则，未限定当前表种、年份及正值。

**修复与验收：** 适用性绑定各自资金事实及当前年度；明确零金额、无此项和历史金额不能作为本年正面适用证据。保留有真实正面预算却缺对应表的检出能力，并单独验证决算空表或等效说明。

### RC-06 / P0：决算三公实际容差与回执不同（AC-06）

CMM-001决算表10.00，说明10.10或10.50万元均fail，10.00通过，10.51报错；[生产比较](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:549>)仍使用0.01。同期回执声称配置0.5万元且来源fact_policy。表总数部分新版修复不能覆盖该路径。

**修复与验收：** 所有金额及比例关系调用同一实际政策，保存本次真正使用的容差、单位和舍入包络。0.5万元及0.1个百分点边界内外都要验证，不能只修改回执字段。大额真实差异和单位数量级错误仍须检出。

### RC-07 / P0：OCR表头分数被当成数字可信度（AC-07）

真实OCR编排配合模拟传输时，score=0.1的结果被统计为低分，随后仍晋升出可作操作数的财政事实。只有“名称/合计”的通用表被认定为 `FIN_08_gov_fund`，晋升confidence=1.0。该分数由[表头命中率](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:106>)生成，关键数字只检查是否可解析，并未逐格复验OCR准确度。真实OCR模型本轮没有运行，不能评价其准确率。

扫描覆盖门仍能把任务保持review_required，是应保留的保护。但它不能使内部错误表种、低可信操作数成为正确事实；更不能证明扫描材料已完成规则检查。

**修复与验收：** 表种须结合标题、完整表头和上下文消歧，通用合计不能唯一认证基金表。每个关键金额保存真实识别置信度、列身份及复验结果；未经认证的关键数据不进入确定性勾稽。真实本地模型完成扫描、混合页实跑，规则须直接消费可信OCR事实，不能依赖旧原生表才能执行。

### RC-08 / P0：缓存命中把新事实挂到旧解析版本（AC-01、AC-08、AC-12）

隔离执行真实[结构化入库缓存分支](</Volumes/workSSD/software development/GovBudgetChecker/src/services/structured_ingest_runner.py:236>)：命中旧run88后，不加载旧artifact、不保存新artifact，却把新instance和sourcecell事实持久化到run88。查询使用固定parser fingerprint、空config、OCR=None，忽略传入artifact的新配置和模型指纹。

这是以内存替身验证分支的实际结果，尚未连接真实PostgreSQL，不能声称已有业务库事故。风险是解析版本、事实与证据来源失配，妨碍校正和历史回放。

**修复与验收：** 命中时完整恢复被命中的artifact及其事实，或按当前全部指纹另建run；不能旧run配新身份。统一事实快照含人工覆盖层后也要与入库一致。验证配置、模型、规则或覆盖层变化的失效策略，并执行真实隔离库往返。

### RC-09 / P0：证据认证旁路可实际画到错误区域（AC-09）

逐条引文自称页核验和主bbox内容锚定已改善，但[table_refs验证](</Volumes/workSSD/software development/GovBudgetChecker/src/utils/issue_bbox.py:117>)仍只核几何及自报exact/verified标签。合成PDF中的伪引文999、错误hash、空白bbox保持verified；经真实标注导出，页面确实画出该空白区域矩形。顶层issue bbox被清空没有阻断这条引用旁路。

另已复现：内容bbox有效但hash错误时，locator及guard仍verified；guard不读取EvidenceRef.physical_page而回退顶层page；仅bbox无引文可被统计为正式规则问题；证据读取在缺quote或缺hash时仍可能保持verified。附件夹具最终已固定合法的source.pdf选择，不以多PDF选择失败作为内容认证证据。

**修复与验收：** issue、evidence、table_refs、读取接口及导出使用同一认证函数；统一物理页字段，校验原件hash、来源cell/span、引文和区域。缺必要字段为unverified，失败证据不得进入精确标注或确定性关系；区域导航单独展示。必须验证伪引文/空白区域、错误及缺失hash、互换physical_page、混合证据、OCR证据，并检查实际标注PDF。

### RC-10 / P0：校正重算发布新版本，但没有用校正后的值（AC-10）

这是本轮最直接的闭环阻断。合成中文原生PDF原表收入100万元，文字90万元，实际主入口产生BUD-107差异；将其实际事实ID校正为90，校正接口queued并启动1个后台任务，新分析目录生成。但新结果 `fact_overlay_applied=-1、overlay_count=0`，收入问题仍是表100、文字90。

原因是同一PDF重复解析的fragment/fact ID不同：[片段UUID默认随机](</Volumes/workSSD/software development/GovBudgetChecker/src/schemas/parse_artifact.py:235>)，[事实ID由片段ID组成](</Volumes/workSSD/software development/GovBudgetChecker/src/services/fiscal_fact_materializer.py:566>)。重跑[覆盖层应用](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:1307>)找不到旧目标，捕获ValueError后仍用未校正事实继续。

API还接受未知target；[覆盖层解析](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:510>)仅消费原始值/归一金额，unit、year、org、scope、indicator、column_mapping、manual_location等已接受字段被跳过。人工复核决定也仅在[下一轮流水线末尾](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:1952>)按issue_id/status投影，当前报告不会随决定即时更新，且未绑定分析版本。

**修复与验收：** 校正基于指定不可变事实快照执行，或提供可靠的跨解析来源映射；不要求不同解析版本必然共享ID，但必须让旧目标安全映射到当前来源。写入前验证目标及字段支持情况。重算失败须有明确失败/未生效状态，不能只以新版本存在描述校正成功。所有支持字段实际改变后重算，未支持字段明确拒绝。新旧版本报告、原件不变、校正生效、复核决定失效及重新绑定要端到端验证；100→90后相关差异须消除，90→错误值时须产生差异。

### RC-11 / P1：AI契约和逐项失败隔离尚未贯通（AC-11）

强规则在重复弱AI候选中保留、适配器保留已提供身份、全局异常后不清空全AI批次，均已修复。AI默认关闭时不应把本项描述成必然发生的规则模式线上事故。

但是生产规则问题仍未提供事实/关系/证据ID。离线[候选契约](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ai_candidate_contract.py:41>)没有接管生产AI构建；[构建器](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ai_findings.py:274>)接受未知fact/evidence ID和模型提供的critical、expected100/actual999。异常回退保留好坏候选整批，没有逐项验证和失败关系回执。

**修复与验收：** 在实际AI入口验证允许ID和候选契约；模型只提出语义/取数候选，确定性金额、公式、容差由系统计算。逐项异常隔离并留失败回执；保留已经完成的强规则和良好候选。默认规则模式验收与离线AI接入验收分别报告。

### RC-12 / P0：分析版本未贯通，PDF共享路径可串版（AC-12）

历史下载入口已新增，合成新旧JSON正确选择各自内容。实际主流程却在构建ReviewReport之后才生成版本快照，[报告构建及写入顺序](</Volumes/workSSD/software development/GovBudgetChecker/api/main.py:1969>)使新旧canonical报告的 `analysis_revision=null`，削弱统一报告版本绑定。

两版PDF均生成到[同一annotated.pdf路径](</Volumes/workSSD/software development/GovBudgetChecker/api/routes/reports.py:1614>)。已执行真实下载handler与PDF生成反例：历史响应和当前响应引用同一路径；第二次导出后，第一响应尚待发送的文件内容已改变。原PDFhash保持不变，但历史/当前或并发下载可串版。

**修复与验收：** 先分配revision，再构建统一报告与不可变快照。每revision和格式使用独立不可变产物，或响应绑定生成时的完整字节。并行下载历史/当前版本，逐项比较问题、结论、事实、证据、未完成范围、人工决定及版本ID；JSON、CSV、Word、标注PDF都须一致。

### RC-13 / P0：正式评测有接口、算法及运行错误（AC-14）

新增评测并非“全部仍未实现”，但当前不能据此验收业务。主检查线保存了 [可重跑脚本](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/run_gate_counterexamples.py>) 和 [实际输出](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/root_gate_counterexamples_replay.json>)，复现以下问题：

| 子评测 | 实际反例或源码结果 | 应修要求 |
|---|---|---|
| 精确定位 | 真值P1小区域，声称P9远离区域仍算1.0；[实现](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:658>)只看exact/verified/bbox，不比真值 | 真实页与区域匹配，冻结匹配标准，区域或页错误计失败 |
| 真值/匹配接口 | 现有golden使用labels，函数读annotations；标准matched对象无finding，正确定位构造也算0.0 | 统一正式golden、匹配记录、finding身份接口并实跑完整评测 |
| 回放接口 | 正式旧回放读取findings，新辅助迭代只读issues，合法回放可能被当空 | 明确版本化输入契约，拒绝不匹配结构，禁止空集合冒充一致 |
| 证据真实率 | 100条问题仅1条有核验记录、99条缺失，[评测](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:596>)分母只有1，算1.0 | 所有应认证证据进入分母；缺记录、缺身份、未认证不算真实 |
| 完成率/回执覆盖 | 应检100关系只给1个pass回执，[评测](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:630>)仍两项1.0 | 用冻结适用关系全集作分母，校验操作数存在，去重汇总/子回执，缺回执计未完成 |
| 导出一致性 | 两个实际JSON导出触发AttributeError：list没有add；[代码](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:796>)在JSON、CSV分支均调用错误方法 | 修运行错误，比较全部规定格式及完整统一报告语义，非仅规则/严重度/页码 |
| 重复运行 | 一个incomplete、一个no_findings，问题列表为空，[评测](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:737>)仍repeatable=true | 比结论、完成范围、事实、证据、决定及绑定身份；缺重复产物不能从分母消失 |
| 统计集合/阈值 | 真实性/完成/定位混合开发与盲测；真实性门槛0.98、回执0.95不符任务包100%要求 | 对独立盲测单独报告；恢复约定阈值，完成率95%与回执覆盖100%分开 |
| 最终通过条件 | 两个候选/重复报警指标永久not_computable，但all_pass要求全部pass | 实现必需指标；明确诊断项与正式门项，不可永久阻断，也不可将缺指标直接改pass |

这些是子函数反例，**不表示整个业务门禁现在可以被伪造通过**；当前整体门禁诚实返回not_passed。旧E2E子校验现已核API/Worker版本及真实原件/导出文件hash，应保留这部分改善；自动采集和完整关联仍需真实场景证明。

**再次验收：** 从合法、非空、含真实错误和真值区域的数据走完整ZC07入口；通过正确结果，拒绝错页/错框、少回执、少核验、结论冲突、串版导出和不匹配schema。修好算法之后再跑冻结盲测，不通过降低门槛解决。

### RC-14 / P1：真实数据、服务和目标内存保护仍未验（AC-13、AC-16）

本次预检盘点：登记材料29份；golden、冻结真值、人工复核冻结真值各1份；人工冻结缺陷组3个；原件hash核验通过0份。这不表示29份原件全部不存在，只表示没有满足正式验收身份条件的开发/盲测集。

未提供冻结开发32、盲测32，每集8层每层至少4份，以及盲测至少100个人工复核的真实缺陷组。测试数据库未配置，Docker CLI存在但daemon不可达，OCR模型清单缺失，四条真实服务场景证据缺失。真实库断言已经修正，仍因环境跳过；Linux容器超限受控终止也没有本轮证据。

**再次验收：** 提供隔离测试环境并实跑上传→Worker→原件证据→复核→校正→多格式导出，覆盖原生、扫描、混合及失败/恢复等任务包场景；核代码、配置、原件、模型和结果hash。冻结分层数据和独立人工真值后计算指标。保留DOC-B1-016/018/021/022/023/024及尚未冻结007/008的盲测身份；旧14份观察样本只能作开发材料。Linux容器执行内存超限终止、失败回执和后续任务不受污染。

### RC-15 / P1：默认关闭AI的部署交付仍不一致（AC-15）

模板与README默认false已修。但是README的[完整服务启动示例](</Volumes/workSSD/software development/GovBudgetChecker/README.md:137>)仍加载 `docker-compose.ai.yml`，该[覆盖文件](</Volumes/workSSD/software development/GovBudgetChecker/docker-compose.ai.yml:18>)强制AI=true并要求AI extractor就绪。基础compose提供数据库及可选OCR，不构成完整规则模式API/Worker/前端默认启动链。

**修复与验收：** 交付清楚可执行的规则优先API/Worker/前端/数据库/本地OCR启动方式，模板和就绪逻辑同源；无模型密钥、无AI服务时任务完成且无云请求。AI覆盖配置保留为显式可选模式。本项应由实际启动与上传验证关闭，而非仅修改默认值文字。

## 五 下一轮执行顺序与退出条件

不建议现在扩充规则数量或先接大模型；现有链路的身份、事实和评测错误会使新能力继续产生不可追溯结果。可直接按以下顺序交给Zcode，每批完成后运行本报告对应反例，不重写已经通过的部件。

| 批次 | 任务 | 退出条件 |
|---|---|---|
| 1 事实与校正闭环 | RC-01、02、08、10、12；先确定来源身份/快照，再迁移必查规则和校正 | 合成PDF及真实开发样本100→90确实改变规则结果；每关系与问题能追到事实/证据；新旧版本和并发下载一致 |
| 2 规则与识别可信度 | RC-03、04、05、06、07、09 | 缺数据不能pass；口径/条件/容差正反例通过；低可信OCR不进入确定性关系；假证据不能标注 |
| 3 正式评测与服务 | RC-13、14、15 | 门禁非空完整入口通过/拒绝均正确；隔离PG/容器/OCR实跑；默认无AI可启动；Linux资源保护有记录 |
| 4 AI接口 | RC-11 | 离线实际入口校验候选与允许ID；逐项失败不损失已完成结果；后续启用AI有独立增益验收 |
| 5 独立业务验收 | 冻结开发/盲测，停止按盲测结果调规则后运行 | 精确率≥98%、召回率≥95%、核心/高严重问题无漏检、精确区域定位≥98%、证据真实且无越界100%、确定性必查完成≥95%、适用关系回执100%、多格式与重复运行一致 |

完成第1批只能证明闭环基础修复，不能提前宣称整体验收。指标达标前应保留manual_review及incomplete范围，不将任务运行结束或无正式问题解释为报告准确。

## 六 复核附件与复现

| 附件 | 内容与使用方式 |
|---|---|
| [解析/事实审查JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/parse_audit.json>) | 11组执行探针、源码、边界说明；各reproduction保留可执行代码及结果 |
| [规则审查JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/rules_audit.json>) | 22组旧例重放、新FactContext的82访问探针、5条声明完成规则的值/状态变化、23组新增边界 |
| [证据/校正/导出审查JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/evidence_audit.json>) | 19组历史场景、真实合成PDF主流程及补充边界，代码与隔离依赖明确记录 |
| [门禁反例脚本](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/run_gate_counterexamples.py>) | 在项目根目录用`.venv/bin/python outputs/reacceptance_20261003/run_gate_counterexamples.py`重跑，不依赖盲测原件 |
| [门禁反例输出](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003/root_gate_counterexamples_replay.json>) | 错区域、标准接口、缺核验/回执、导出异常、重复运行结论冲突等真实输出 |
| [工程运行记录目录](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261003>) | 命令、退出码、日志、环境及预检结果；该目录受仓库忽略规则保护，交接时需另行保留 |

探针既有确认修复，也有未通过结果；不应只提取失败数作为系统误报率。正式精确率、召回率、定位率及完成率本轮均为**未能计算**，待冻结数据和正确评测完成后再给数值。
