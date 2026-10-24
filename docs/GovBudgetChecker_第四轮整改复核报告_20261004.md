# GovBudgetChecker 第四轮整改复核报告

本轮结论：**未通过验收，当前后端存在无法导入的语法错误；也不能确认剩余可代码化整改已经全部完成。** 解析身份、缓存版本、证据认证、单位校正、人工决定和 AI 候选隔离都有实质进展，但仍有事实选择、关系回执、局部列校正和验收计算的缺口。本报告用于安排下一批修复和验收，不代表真实材料的精确率或召回率已经达标。

## 一 复核对象与证据边界

复核日期为 2026 年 10 月 4 日。对象是分支 `codex/zcode-remediation` 的**当前未提交工作树**，HEAD 仍为 `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。相对该提交，26 个文件发生修改，增加 2524 行、删除 379 行；不能用同一个提交号代表本轮的新代码。完整 Git 差异的 SHA256 为 `9a4709a3434645aa2a6f289039edf9931a1185ce87987ff9ce390c6a668ba4e0`，26 个修改文件在本轮始末的校验值一致。

本轮以仓库 AGENTS.md 的 `v3_3_portable` 为默认业务口径，保留前几轮的发布标准。复核范围为上海市、区级部门或单位的单份预算/决算 PDF，包含原生、扫描和混合材料；规则优先，AI 默认关闭。没有输入新的真实待审报告，因此本报告中的合成金额和页码仅用于软件反例，不是某政府单位的审校结论。

证据分为三类，不能互相替代：

- **原工作树执行**：未改变业务源码即可运行的解析、FactContext、证据、AI 转换、门禁与 Compose 检查。
- **语法诊断副本执行**：仅在内存或隔离临时副本删除一组完全重复的参数后，运行规则、主分析、校正、人工决定和导出链。变换及结果均留档，**不计为当前生产代码通过**。
- **尚待真实环境验收**：PostgreSQL、API/Worker/OCR 服务栈、扫描模型、Linux 内存保护、冻结盲测。内存数据库协议替身和模型传输替身不能证明这些项目通过。

本轮只新增复核报告和隔离输出，未修改业务代码，未读取 `.env`、`.mimosa/`、保留盲测原件，未调用真实云 AI，也未连接真实数据库。上一轮报告保留为对照。

## 二 本轮验证结果

| 检查 | 实际结果 | 可支持的结论 |
|---|---|---|
| 原工作树全量 pytest | 退出码 2，65 个收集错误，未进入测试执行 | 后端工程验收失败 |
| 原工作树整改 pytest | 退出码 2，8 个收集错误 | 不能沿用上一轮 2358 passed 的结论 |
| Ruff | 退出码 1，4 条错误 | 三个重复关键字，以及门禁脚本未导入 `Optional` |
| 不依赖阻断模块的选定测试 | **60 passed** | ZC07、事实物化、结构化解析和 golden 匹配的小集通过 |
| 前端单测及构建 | 两项退出码均为 0 | 前端工程检查通过，未进行浏览器完整交互验收 |
| 环境变量一致性、日志安全检查 | 两项退出码均为 0 | 对应静态检查通过 |
| 完整语法诊断副本 pytest | **2316 passed / 40 failed / 137 skipped** | 仅用于定位后续问题，不是原工作树回归结果 |
| ZC07 预检 | 退出码 2，`business_acceptance=not_passed` | 如实保留数据和真实环境缺项 |
| Compose 静态展开 | 默认包含 postgres、backend、worker、frontend、ocr；AI 叠加增加 ai-extractor | 上轮默认栈缺服务问题已修，尚未实跑容器 |

诊断运行的 40 项失败包含不同原因：子进程仍导入原始语法错误模块；旧测试仅用 bbox 或弱证据却期待认证；旧测试将 AI 候选、结构化待复核项算为正式问题；以及规则未完成项和接口契约的变化。**不能把它们直接解释成 40 个新业务缺陷，也不能删除测试或降低门槛后宣称通过。** 应修复真实问题，并将合法阳性、阴性、未完成状态的测试契约分别更新。

典型例子是旧 CMM 公车运行费测试：差额 2.68 万元确实大于 0.5 万元，但说明缺少出国分项，直接调用 `.apply` 会转未完成；经真实规则执行器仍保留两条部分发现。补齐明确的出国 0 后也能检出。因此本轮没有将该失败判成容差导致的漏报。

## 三 已有实证的改进

以下是局部行为的有效修复，不能据此把整个 RC 或系统判为通过。

| 改进 | 本轮证据 |
|---|---|
| 续表硬身份冲突 | 带“续表”标记的异主体、跨年度表仍拆成两个实例；合法续表继续合并 |
| 解析缓存版本 | 指纹更新为 v4，旧语义缓存不再覆盖新解析；来源格、物理片段 ID 和成员身份保留 |
| OCR 风险状态 | 缓存往返保持 `ocr/region`；摘要如实报告候选物化、操作数 0、不可供规则使用 |
| 原件认证 | 原工作树独立函数中，完整引用认证成功；缺引文、缺哈希、错误哈希、缺来源对象、错误证据 ID、纯 bbox 都不能认证为精确证据 |
| 单位校正 | 诊断链中合法 unit-only 请求实际应用，原始 100 改按“元”解释后为 0.01 万元；100→90 校正仍使原差异消失 |
| 人工决定投影 | 诊断真实路由即时更新 canonical 报告；四格式含确认状态、说明、复核人、版本和一致指纹。重算消除问题后当前移除旧决定；尚未新增后续决定时，历史保留旧决定。后续决定覆盖历史的反例见下文 |
| AI 候选身份 | 原工作树接受生产字典上下文，未知 fact/evidence/relation ID 均剔除并计数；模型 critical 封顶 high，金额标为未经验证的模型声称 |
| AI 故障回退 | 诊断故障注入后仅两条规则发现进入正式问题，两条 AI 发现保持候选，不再由 fallback 转正式 |
| 门禁核心反例 | 标准 `labels` 与真实认证位置的阳性为 1.0；整页区域、错框为 0；错误哈希或自报 verified 无引用的真实性为 0；100 项仅一回执的完成及覆盖均为 0.01 |

单位校正、主分析、人工决定路由和四格式导出的上述结果均来自语法诊断副本，须在原始源码修复后重跑。导出检查包含可见文本和指纹，未完成所有真实版式的人工视觉验收。AI 检查是离线契约和故障隔离检查，未评价模型业务增益。

## 四 仍需整改的问题

### 1 后端不能导入

**P0 / critical，原工作树确定复现。** [budget_rules.py](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:3334>) 同一 `_issue` 调用连续两次传入 `check_relation_id`、`fact_ids`、`evidence_ids`。第二组从第 3340 行开始，Python 报 `SyntaxError: keyword argument repeated: check_relation_id`，影响规则注册、API 和 Worker 的导入。先删除重复组，完成原始源码导入和全量检查，再讨论业务签收。

[run_zc07_acceptance.py](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:599>) 的 `Optional[Path]` 未导入，Ruff 也失败。该脚本因延迟求值注解仍可执行预检，但不能将静态门禁标绿。

### 2 合计歧义仍可能被明细替代，阶段仍可能串用

**P1 / high，原工作树独立 FactContext 复现。** [resolve_operand](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:145>) 先排除不可用事实，再从可用集合判断总计。合计 100 为 ambiguous、明细 50 为 verified 时，结果是 `ok=50`，而非“合计未确认，待复核”。应先在完整候选集合确定所需角色，合计被阻断时不得改用某一明细。

[阶段过滤](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:97>) 使用子串匹配，“预算”会匹配“调整后预算”。合法独立探针中请求年初预算仍取到调整后预算；预算 CMM 诊断成对案例中，表格和说明年初预算均为 10、调整后事实为 11，仍被拿来比较并报错。阶段应为明确枚举和关系口径，禁止泛化子串匹配；不同阶段保留不同事实。

决算 CMM 另有相反的阶段问题：[按文种统一允许决算阶段](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:541>) 会排除预算列的合法年初预算事实。诊断中事实预算 31、说明及旧表预算 30，合法阶段被排除后回退旧表 pass；把该事实错误标成决算阶段才 fail。此结论限定于语义事实契约：当前物化器仍按整份文种赋阶段，不能据此声称所有真实 PDF 已触发该漏报。应按指标和列分别物化阶段。

### 3 事实与关系身份在默认规则输出中仍丢失

**P1 / high，静态和诊断生产链复现。** BUD107 作者端已附关系和事实身份，EngineRuleRunner 也新增失败回执链接；但默认 legacy [序列化器](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/pipeline.py:387>) 没有转发 `check_relation_id/fact_ids/evidence_ids`。实际主链报告中的 BUD107 问题仍为 null，失败回执 `issue_refs=[]`。V33-103/104/214 作者端也尚未完整附这些身份。

注册的 82 条规则仍是 5 条标为事实迁移 complete、77 条 pending；诊断访问探针仅这 5 条访问 FactContext。结构规则可以保留适当实现，但数值检查和各条适用关系必须真实消费同一版本事实，不能用规则数量或注册标签替代迁移验收。

[校正依赖读取](</Volumes/workSSD/software development/GovBudgetChecker/src/services/correction_overlay_store.py:122>) 仍只识别 `operand_fact_uids` 等旧键，实际回执使用 `operand_fact_ids`，合法校正响应仍为 `affected_checks=unknown`。数值全量重算已生效，这不是校正完全无效；仍需统一依赖字段，才能明确失效了哪些检查和决定。

### 4 逐关系回执与实际计算依据仍不完整

**P1 / high，原工作树回执函数及诊断规则复现。** 空 FactContext 加合法旧表可让预算 CMM 整体 pass，8 条回执全部 `operand_fact_ids=[]`，却标成 `evaluation_basis=fiscal_fact`、`fact_migration_status=complete`。旧表可计算不等于事实上下文已消费，应明确记 legacy 或将该关系转未完成，不能错误归因。

[公车小计与购置加运行的内部勾稽](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/common_rules.py:1006>) 能产生问题，却没有对应关系 ID 和回执。现有回执中的 required facts 及实际数字取整包络也未完整留痕，出现 `rounding_envelope_wan="policy"`，不能复算当时的真实比较阈值。应为每个执行分支生成一次关系回执，记录实际操作数、所需角色、阈值、状态和对应发现。

另有 P2 可读性问题：[V33-214 阻断原因](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/rules_v33.py:5284>) 将字符串展开为逐字符列表。未完成状态已正确，不是风险阻断失效；仍应把每个原因保存为完整字符串，便于人工复核。

### 5 局部列校正会删除其他正确事实

**P1 / high，原工作树真实原生 PDF 解析、物化及校正函数独立复现；诊断实际请求链确认。** 真实表实例原有 4 个收入、支出事实，合法校正只指定收入列 `{income_actual: 1}`，报告 `applied=1/unresolved=[]`，重建后仅剩两个收入事实，支出事实被删。

原因是 [列映射取值](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:484>) 将任何非空人工映射视为整个有效映射，而 [合并校正](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/fact_context.py:922>) 只合并实例显式映射，未合并原先推导出的完整映射。应先取得完整有效映射，再替换指定列或指标。诊断主链中的支出关系随后回退旧表并以空操作数标 pass，也印证上述回执归因问题。

### 6 后续人工决定会改变历史报告

**P1 / high，合法请求链的语法诊断副本复现。** 100 版本问题先确认，校正为 90 后问题消失；再合法校正为 110，BUD107 问题重新出现，经新版本真实工作流路由记 `no_issue`。此时重新下载 100 历史版本，原 `confirmed` 从 JSON、CSV、Word、PDF 消失，历史报告 SHA256 也改变。

[工作流写入](</Volumes/workSSD/software development/GovBudgetChecker/src/services/issue_workflow_store.py:988>) 仍按 `job::issue_id` 覆写单个槽位；[报告投影](</Volumes/workSSD/software development/GovBudgetChecker/src/services/issue_workflow_store.py:608>) 只读取最新决定，再按版本或身份筛选。新增几个版本字段不能恢复已经被覆盖的事件。应按 analysis revision、issue identity、decision revision 追加保存决定，并从对应版本事件或不可变 canonical 快照导出历史报告。此问题也限制 RC12 的历史一致性验收。

### 7 叙述主体和预算旧续页路径仍有风险

**P1 / high，语法诊断副本复现。** 历史跨行标题、调整后段落以及长单位名过滤已有改善，但 [机构识别正则](</Volumes/workSSD/software development/GovBudgetChecker/src/engine/budget_rules.py:1277>) 要求后缀前至少两个中文字。明确异主体“乙单位”“乙局”仍能进入本单位取数，213012 被用于当前主体的收支比较，产生两条误报。不能把普通“行政单位”“事业单位”等词当作可靠主体实体。应把金额物化为带章节、主体、年度和阶段的叙述事实，再按关系绑定。

预算旧二维表续页反例仍把一张独立政府采购页拼入目标表，11 行变 14 行。结构化解析已加硬身份边界，旧取表路径也应同样处理，或统一退出旧续页拼接。

### 8 缓存物理片段还未原样保留，扫描审校能力尚未交付

**P1 / high，原工作树内存存储协议往返复现。** 原生物理片段数量已由 2→0 修为 2→2，成员和来源格也保留；但第二页原网格 `[表头, 合计200]` 恢复为仅 `[合计200]`，`header_row_count` 仍为 1。新增存储只保留片段元数据，重复表头格已在逻辑合并阶段删除。应不可变保存完整物理片段，再单独生成逻辑合并视图。本轮仅确认保真问题，没有推断已丢业务金额，也没有以协议替身宣称真实 PG 往返通过。

OCR 目前仍把所有物化事实放在低置信候选，操作数为 0。这是诚实的风险控制，但尚不能达到扫描 PDF 自动勾稽目标。需要逐格置信度、表/列身份、关键数字复验和明确的可信晋升条件，并通过真实 CPU 模型及冻结扫描材料验证，不能简单提高固定分数。

### 9 验收脚本仍可能高估部分指标

**P1 / high，原工作树合法接口合成反例复现。** 以下只证明评测子函数缺陷，不代表整套 ZC07 在缺数据时会通过；当前整体预检仍如实不通过。

| 反例 | 当前结果 | 必要修正 |
|---|---|---|
| 两份文档仅一份有冻结适用关系分母 | 完成、覆盖均 1.0，只统计有分母的一份 | 每份都校验冻结分母；缺项不能跳过 |
| 同一关系先 pass，后 execution_error | 只保留第一条，仍为 1.0 | 相同回执可去重，冲突回执须判失败或不可计算 |
| 两个缺陷仅一个有位置真值 | 定位分母为 1，定位率 1.0 | 冻结真值不完整时保留缺项，不能缩小分母 |
| 同源同配置同版本，回执容差 0.5→99 | repeatable=true | 比较完整关系语义和实际阈值 |
| 同源同配置同版本，引用原文改变 | repeatable=true | 比较完整证据引用，不能只比较证据 ID |
| 完整四格式非空报告中 Word/PDF 的 F1、F2 问题金额内容互换，指纹不变 | export_agreement_rate=1.0 | 按问题 ID 逐行绑定内容核对，不能只验证全文包含全部词 |

对应代码见 [回执去重和分母](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:732>)、[定位真值筛选](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:861>)、[重复运行签名](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:989>)、[Word 和 PDF 全文词包含判断](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:1312>)。本轮也验证了完整非空四格式阳性为 1.0，避免仅用不合法缺字段输入指控接口。

### 10 真实业务验收仍缺输入和环境

**manual_review / 尚未交付。** 登记材料 29 份，只有 1 份冻结且有人复核的 golden、3 个缺陷组；其原件哈希可验证数为 0。这不代表仓库所有 29 份 PDF 均不存在，但不能计作 32+32 冻结验收集。

当前 Docker daemon 不可用，隔离测试数据库连接未配置，OCR 模型清单不存在；四条真实 API/Worker/PG/OCR 场景未交付。本机为 macOS，Linux 内存保护未实跑，浏览器 Chromium 可执行文件也缺失。AI 离线候选统一契约尚未完整接管生产入口，模型增益尚未独立评估。没有据此提供精确率、召回率、真实精确定位率或真实完成率，相关值保持 null。

AI 还有可代码化余项：[统一候选校验器](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ai_candidate_contract.py:41>) 尚无生产入口调用；字典白名单修复不能替代完整契约。故障注入显示单关系异常仍让整个合并循环退出，关系集合为空，没有“坏关系失败回执、好关系继续裁决”。新增 fallback 已正确保留 AI 候选边界，应在此基础上实现逐项隔离，再评价模型增益。

## 五 原整改项状态

| 整改项 | 本轮状态 |
|---|---|
| RC01 事实主链 | 部分改进；迁移和默认序列化身份仍未完成，原生产链被语法阻断 |
| RC02 表身份 | 结构化续表冲突已修；旧预算续页仍有诊断反例 |
| RC03 叙述事实 | 历史/阶段诊断改善；短异主体仍误取，结构化叙述事实未收口 |
| RC04 关系回执 | 部分完善；空操作数错误归因、遗漏公车关系、数值包络及发现链接仍未齐 |
| RC05 条件适用 | 本轮 8 个诊断状态案例正确；须恢复原生产链重跑，未做全面业务签收 |
| RC06 金额和阶段 | 预算 CMM 的 0.5 容差诊断生效；阶段子串串用仍存在 |
| RC07 OCR | 风险摘要已修；可信扫描自动操作数和真实模型验收未完成 |
| RC08 缓存 | 旧指纹、来源方法、成员 ID 已修；完整物理片段与 PG 实跑待完成 |
| RC09 证据认证 | 认证边界独立探针已修；主链证据关联及全量真实认证验收未完成 |
| RC10 校正和决定 | 单位校正、当前决定投影诊断改善；局部映射删事实、后续决定覆盖历史、身份绑定未完成 |
| RC11 AI | 字典白名单原生有效、故障候选隔离诊断有效；统一契约及业务增益待验收 |
| RC12 导出 | 当前四格式决定及指纹诊断改善；后续决定使历史四格式改变，尚未满足不可变历史要求 |
| RC13 验收门禁 | 核心旧反例已翻转；部分分母、冲突回执、重复语义、导出行绑定仍需修复 |
| RC14 数据和实跑 | 未关闭，冻结盲测和真实运行证据不足 |
| RC15 部署 | 默认完整栈静态问题已修；当前镜像中的后端仍被语法错误阻断，尚未实际部署验收 |

## 六 下一批实施与验收顺序

1. **恢复工程可执行。** 修重复关键字和 Optional 导入；原源码导入 API/Worker/规则，Ruff 与全量测试通过。逐项解释并修正诊断的 40 项失败，不降低误报控制、证据认证和发布阈值。
2. **统一取数和保全事实。** 修角色优先与阶段枚举、局部列映射增量合并、异主体叙述、旧预算续页和完整物理片段。每个修复都保留合法阳性、阴性、歧义转未完成反例。
3. **补完关系和版本保存。** 默认流水线必须转发 fact/evidence/relation ID；每个适用数值关系有真实操作数、实际阈值、状态和 issue_refs。遗留表回退如实记账，禁止标成已完成事实迁移。人工决定按版本追加保存，以“确认→问题消失→新问题再决定→重读旧四格式”验证历史保持不变。
4. **收口 AI 契约和验收计算。** 模型响应入口接统一候选契约，关系异常逐项隔离并留失败回执。按每份文档校验冻结分母，拒绝冲突回执；重复运行比较完整证据与关系；四格式按 ID 逐条核对。将本报告反例固化为测试，并保留完整非空阳性。
5. **完成真实发布验收。** 配隔离 PG、Docker、CPU OCR 离线模型及 Linux 测试机，实跑原生预算、原生决算已知问题、扫描预算、混合决算人工校正四场景。冻结开发 32、盲测 32，每个 8 分层各至少 4，盲测至少 100 个经人工复核的真实缺陷组。保留盲测样本不得进入开发调参。

发布时至少满足：正式问题精确率≥98%，真实问题召回率≥95%，核心及 high/critical 零漏报，真实精确定位≥98%，全部正式引用可认证且无越界，确定性必查关系完成≥95%，适用子检查状态和未完成原因覆盖100%，当前及历史导出和同版本重复运行一致。AI 在规则基础上单独评估增益；不能用模型补造操作数、页码或认证状态。

## 七 复核产物

所有输出位于 [本轮证据目录](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4>)，其中：

- [源文件快照](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/source_snapshot_before.json>)、`source_snapshot_after.json`、`working_tree.patch` 绑定此次未提交代码。
- [解析复核](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/parse_audit.json>)、[规则复核](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/rules_audit.json>)、[证据和校正复核](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/evidence_audit.json>) 包含探针代码、结果、源校验值和执行限制。
- [验收计算反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/root_gate_probes.json>) 及 `run_gate_probes.py` 包含原件哈希、非空阳性、分母/重复/导出反例和合成 PDF。
- `full_pytest.log`、`remediation_pytest.log`、`ruff.log` 为原工作树失败证据；`independent_subset.log` 为 60 项独立测试结果。
- `diagnostic_full_pytest.log`、`diagnostic_transform.json`、`run_diagnostic_pytest.py` 为语法诊断记录，不是生产通过证明。
- [ZC07 预检](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/zc07_preflight.json>)、[部署静态检查](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261004_round4/deployment_probe.json>) 及各项 `_run.json` 保留命令、退出码和耗时。
- `findings.json`、`findings.csv` 为可交给实施方的结构化问题清单；真实业务指标未计算，禁止将合成反例的 1.0 写成系统业务指标。

对照报告：[第三轮复核报告](</Volumes/workSSD/software development/GovBudgetChecker/docs/GovBudgetChecker_第三轮整改复核报告_20261004.md>)。
