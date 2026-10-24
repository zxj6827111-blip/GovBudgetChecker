# GovBudgetChecker 第十六轮系统自检与整改报告

日期：2026-10-07。检查依据：`GovBudgetChecker_系统整改PLAN_V2.0_20260930.md` 的 WBS，沿用 V1.0 Gate A–F，并对照《GovBudgetChecker 完整整改计划》和当前 `AGENTS.md`。

## 1. 结论与范围

**存在额外问题。本轮新确认并修复 11 项工程缺陷，已完成本轮修复范围的自检；整个 PLAN 及独立业务验收仍未通过。** 当前有限反例集不再出现修复前行为，不能据此推定系统不存在其他问题或全部精度/召回目标达标。

自检结果：全量后端 **2729 通过、137 跳过、0 失败**；另外启用真实一次性 PostgreSQL 的 **135 项数据库用例全部通过、0 跳过**。两个集合本轮共实际执行 2864 项通过用例；普通全量中的数据库跳过已由后一个集合补验，其余两项跳过分别为 macOS 内核不强制 RLIMIT_AS、宿主后端虚拟环境缺少 cv2，详见 `remaining_skip_reasons.log`；真实 OCR 容器另经服务链验证，Linux 目标环境内存限制仍须专项复验。前端 unit、lint、类型检查和生产构建通过；**70 项生产环境适用的真实浏览器回归通过、0 重试**。7 个 API + Worker + PostgreSQL + OCR 场景通过，40 个导出文件（10 个初始/校正视图）已重新读取并校验四格式一致性及保存的 SHA。

工作仍在 `codex/zcode-remediation`，Git HEAD `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`，包括原有未提交整改。本轮没有提交、部署或操作生产数据库。开始时 719 文件与第十五轮源码身份相同；最终冻结 723 文件，修改 25 个现有文件、新增 4 个回归测试文件。第十五轮保存证据与报告的 730 个构件 SHA 保持不变。

A–E 检查线覆盖材料/版本/状态、表结构与识别风险、金额/主体/单位、口径和失败处理、验收门禁与归档交付。反例区分工程夹具、复用开发材料、真实服务与独立人审真值；全部原件复跑属于开发技术验证。

## 2. 新发现与整改

| 编号 | 问题 | 修复前复现 | 修复后行为 | 回归证据 |
|---|---|---|---|---|
| R16-NUM-01 | 无金额单位或归一金额仍可入操作数，并由 V33-103 回退旧表值判错 | 无单位表 900 与说明 100 万元被判 fail；normalized_value=None 仍 ok | 要求有限万元归一金额；候选不可用返回 blocked，规则 insufficient_data，保留原因 | tests/remediation/test_round16_operand_admission.py |
| R16-NUM-02 | 已知异主体表金额进入当前主体勾稽，上级名称可被下属表题借用 | 测试甲局材料引用测试乙局金额：差额判错、同值判 pass；长名称被截短 | 核对完整原始表题主体边界；排除明确异主体；保留本主体和合法继承主体事实 | tests/remediation/test_round16_operand_admission.py |
| R16-RUN-01 | 外层超时或取消后，解析/规则子进程仍运行 | asyncio.to_thread 取消只结束 Future，后台进程不一定停止 | 传递取消事件，在线程所有者完成 terminate/kill/reap 后发布终态；4 个真实进程超时/取消反例均无存活子进程 | tests/remediation/test_round16_runtime_closure.py |
| R16-RUN-02 | 统一解析产物或事实入库失败被吞掉，旧同 run 计数可能掩盖当前失败 | 持久化失败仍 structured_ingest done；验收只看旧数据库行 | 返回 error 和具体 ingest_stage/unified_storage 状态；质量门阻止 done；验收要求本次产物/事实/证据写入完成及当前版本一致 | tests/remediation/test_round16_runtime_closure.py |
| R16-GATE-01 | 手填校正投影可通过技术交接，没有原数据库行、真实覆盖层及 FactContext 绑定 | 空覆盖层与旧事实上下文的工程交接包仍 ready | 校验原 DB 行/证据、当前版本和来源、原 JSONL 校正账本、生产 FactContext token/覆盖层投影，不能改写 parse_facts 原件 | tests/remediation/test_round16_acceptance_gates.py |
| R16-GATE-02 | 一个文档空适用分母被整体忽略，仍算出 100% 完成率 | D1 一条通过、D2 空分母仍 completion_rate=1.0 | 每个文档都要求非空有效冻结 applicable_sub_checks；无效时 not_computable | tests/remediation/test_round16_acceptance_gates.py |
| R16-GATE-03 | 同一回放文件或同一次运行被当作两次独立运行 | 同文件比较 repeatable=true | 拒绝同路径、链接同文件和同 run_id；要求独立运行标识及带时区时间；合法独立同结果仍通过 | tests/remediation/test_round16_acceptance_gates.py |
| R16-GATE-04 | 重复性签名漏掉事实 token、认可状态及覆盖层摘要 | 事实值 token/overlay 变化仍 repeatable=true | 比较稳定事实身份和状态/覆盖层摘要；排除随机 parse_run_uid；差异为 repeatable=false | tests/remediation/test_round16_acceptance_gates.py |
| R16-ARCH-01 | 后端可把未确认问题直接打包并改成 in_package | pending 问题 create_package 被接受，跳过人工确认 | 必须是当前分析、当前问题指纹且已 confirmed；复核锁继续生效 | tests/remediation/test_round16_archive.py |
| R16-ARCH-02 | 归档包重审后下载最新报告；旧决定可污染同 ID 新问题显示 | 包只存 job_ids；重审后读当前报告；旧 in_package 显示给新问题 | 保存不可变报告快照与 SHA；按 package_id 下载冻结版本；缺快照/篡改拒绝；旧决定在新问题显示 pending、审计保留 | tests/remediation/test_round16_archive.py |
| R16-UI-01 | 生产构建把站内跳转的 IP 主机改为 localhost，登录 Cookie 丢失 | 127.0.0.1 旧归档深链跳到 localhost/login，真实浏览器失败 | 使用 Next 支持的 skipMiddlewareUrlNormalize 与校验后的请求来源生成绝对跳转；归档/登录保留协议主机端口；生产浏览器通过 | e2e/tests/archive-page.spec.ts；e2e/tests/security-headers.spec.ts |

原始 before/after 证据保存在 `outputs/reacceptance_20261007_round16/rules_audit/`、`outputs/reacceptance_20261007_round16/runtime_audit/`、`outputs/reacceptance_20261007_round16/acceptance_audit/`、`outputs/reacceptance_20261007_round16/archive_before.log`、`outputs/reacceptance_20261007_round16/archive_after.log` 和浏览器运行记录。新增 4 个后端测试文件合计 78 个用例；浏览器额外覆盖旧深链主机/端口、匿名登录 next 参数和安全响应头。

真实 PG 首跑曾 **16 失败、119 通过**，第二次 **1 失败、134 通过**，最终 **135 通过**。失败根因是旧测试夹具未提供现行 `build_parse_artifact`、分析版本及当前问题身份，或把 migration 0021 假设为最新版本。补齐真实入口所需的有效夹具，保留非法/过期版本被拒绝的断言，没有放宽生产复核门禁。三次日志与清理回执分别保存。

浏览器首次缺失 Playwright 浏览器，全部用例在启动前失败；改用本机已有 Chrome 的独立临时配置后，新发现了 `127.0.0.1` → `localhost` 的真实跳转缺陷。一次相对 Location 修复被 Next 生产运行时拒绝，随后使用受支持的 `skipMiddlewareUrlNormalize` 和校验后的请求来源生成绝对跳转，最后完整适用集合 70/70 通过。所有失败日志都保留；生产默认关闭的 `/e2e/batch-upload` 演示 CSP 用例不适用于这次生产构建，未放开生产测试页面。

归档包新增确认状态和版本约束，冻结报告体及 SHA；`package_id` 下载会读取冻结快照。已有无快照旧包明确返回 409，须从当前确认问题重建，不自动套用新报告。实际生成 PDF 的回归证明重审后仍得到原归档版本、原始 PDF 字节不变；另有 ZIP 契约和篡改反例。持久报告快照位于现有工作流文件权威存储，本轮没有声称数据库镜像单独恢复也能还原新快照。

## 3. 真实服务和交付复验

| 场景 | 终态 | 场景总耗时（秒，含导出/部分校正） | 本次结构化入库 | 技术服务检查 |
|---|---|---:|---|---|
| native_budget | review_required | 22.01 | 通过 | 通过 |
| native_final | review_required | 20.84 | 通过 | 通过 |
| synthetic_scanned_control | review_required | 52.48 | 通过 | 通过 |
| derived_scanned_budget | review_required | 73.19 | 通过 | 通过 |
| derived_mixed_final | review_required | 65.19 | 通过 | 通过 |
| original_scanned_budget | review_required | 676.14 | 通过 | 通过 |
| original_mixed_final | review_required | 23.06 | 通过 | 通过 |

21 页原始扫描预算 SHA `5978b569d1f4bf58d07281db4150e72b9cf867fa45a051da4976aabdc6a5eec7`，**21/21 页 OCR 成功**，无失败页、截页或跳过 OCR。Worker 总预算 3120 秒，实测 pipeline **675.02 秒**，完成时未超时。`progress_snapshots.jsonl` 保存跨过原 600 秒失败点的过程证据：621.8 秒时 19 页完成、正在处理第20页。预算为 Worker 的实际分页预算，客户端等待 3600 秒不能替代它。

该扫描原件仍是 `review_required`；事实上下文仅有 **22 项事实**，BUD-001 为 `insufficient_data`，结构页未可靠认定时维持待复核。正式问题数量 0 表示证据门控制，并不表示预算材料没有问题。JSON、PDF、DOCX 没有重新出现确定性“缺失预算表”；PDF/DOCX 明示“结构识别尚未完成”。这是运行可靠性和诚实回执通过，扫描金额可计算能力仍有限。

35 页混合决算原件 SHA `f22ac36e2536a15d9a90c5e2ace74b84c3fa79f8e15d010d8a7663ff883e97f7`。实际金额从 **746.62 → 756.62** 的开发校正已重新分析，原始 `parse_facts` 仍为 746.62，原证据页/来源不变，当前 FactContext 投影为 756.62；原始校正 JSONL 在临时工作区清理前原样复制并校验 SHA。当前结果版本、DB 查询计数、parse_run/version、覆盖层 basis 与当前 FactContext token 均一致，缓存读回和本次入库成功，四格式历史版本下载相同。栏次/行次未再次生成金额事实。

合成扫描控制样本验证合法 OCR 金额操作数及校正后的关联规则状态变化：`rule_status_changed=True`；它明确不算原件或盲测样本。对35页原件，校正产生新投影并不自动证明所有目标勾稽都已消费该值；真实识别或关联缺口继续保留未完成。

`outputs/reacceptance_20261007_round16/technical_preflight_development/technical_preflight_check.json` 使用生产技术前置校验器重新读取本轮实际服务附件，验证当前查询回执、不可变原事实、真实账本和生产上下文，结果通过。它没有生成或验收人员签字、冻结真值或盲测指标。

服务启动时的后端冻结身份保持不变。运行期间另修复4个前端/浏览器测试文件；最终另存全量冻结，服务受测源指纹与最终版本逐项一致，未把两个不同版本的全链复跑当成重复性证明。本轮只有一次当前后端的完整7场景复跑；第十五轮两次运行仅保留为历史证据。

前端服务器、临时 API/Worker、服务验证 PostgreSQL、PG 用例容器及本轮 OCR 容器均已清理；服务临时工作区已删除。依赖检查（后端63包、OCR）、环境变量三方对账（136/131/65）、Ruff、保留日志键和 `git diff --check` 通过。macOS 等环境能力限制不通过降低阈值或删除用例掩盖。

## 4. PLAN 对账与剩余工作

| 工作包 | 本轮状态 | 可复验依据 | 未完成范围 |
|---|---|---|---|
| WP0 | 工程冻结已复核 | 719 文件起点完全匹配 R15；最终源码冻结，旧轮次证据单独核对 | 不是新 Git 提交或发布授权 |
| WP-C1 | 人工交接待完成 | 64 空槽、开发污染排除清单、签字/封存模板、当前技术前置验证 | 真实材料入组、人员身份及签字、近重复裁定未完成 |
| WP-C2 | 业务指标未验收 | 分母、投影、独立运行身份及稳定摘要门禁已补负例 | 0 冻结材料/0 人审缺陷组；precision/recall 不可计算 |
| WP1 | 本轮工程回归通过，工作包部分核验 | 真实 PG 槽位身份、年文种约束、多版本、回填/删除/升级测试 | 历史残留任务处置及真实台账抽验未在本轮签收 |
| WP2 | 实现与浏览器回归通过，人工对齐待验收 | unit/lint/生产 build；材料台账/下钻/导航实际浏览器和接口夹具 | 未按效果图06–13完成逐屏人工视觉签收；API 夹具不等于真实用户 UAT |
| WP3 | 新增缺陷已修，仍有迁移欠项 | 当前版本确认、完成锁/重开、PG 并发/替换失效、归档冻结与旧决定可见状态 | legacy ignored_issues.json 与 workflow 权威存储仍并存；没有完成 A3-S1 统一迁移及全链用户签收 |
| WP4 | 事实准入本轮修复，真实精度待验收 | 无单位/异主体反例；合法万元、元/亿元、零值/整数邻例保留 | 未完成全量 Decimal/根因机制化的工作包验收；不能以反例推定正式精度 |
| WP5 | checker 实现目录核验，基准未验收 | obligations-v9 自检零错误、unimplemented_checks=[]；预算23/决算44义务 | 8 个 checker 的全部真实反例入集及人审基线对比未本轮完成 |
| WP6 | 局部消费修复，全量迁移未验收 | 统一事实准入/V33-103 不再用缺单位事实回退旧表判错；真实缓存事实链 | 其余 legacy 路径/适配器须逐条按真实消费证据对账；登记不等于迁移完成 |
| WP7 | 真实 Golden/裁决待完成 | 交接模板及污染排除门禁 | 人工缺陷组、top30/旧漏报真值和分歧裁决尚未冻结 |
| WP8 | 页面工程回归通过，业务覆盖签收未完成 | quality/rules 适配器与质量页浏览器回归；展示真实 incomplete 状态 | 需真实台账抽验指标来源及未完成面可见性；不把 checker 目录当实际完成率 |
| WP9 | 本轮未验收，需补实现/验收证据 | 审读 material 路由、槽位/台账服务和页面 | 未找到可复验的应收 CSV 导入→Slot 应收台账 E2E；不能沿用计划文字宣布实现 |
| WP10 | 本轮技术回归通过，正式发布门禁未全验收 | 全量/真实PG、取消回收、写失败门禁、原件服务、导出版本与前端70回归 | 正式用户 UAT、部署环境资源验收、备份三件套恢复演练及 Gate C/D 未本轮通过 |
| WP11 | 原件技术扫描复跑，识别与泛化仍有限 | 完整21页原始扫描预算和35页混合决算，未截页、未停OCR，沿用原始SHA | 扫描无认可金额事实时仍 incomplete/manual_review；需要独立跨地域真实OCR样本和核验标注 |
| WP12 | 工程债未宣告完成 | 当前规则/别名及受测路径审读 | 规则单体、历史目录/备份文件/任务处置等债务需单独登记和验收 |

| Gate | 本轮结论 | 未达到整体门禁的原因 |
|---|---|---|
| A 数据/状态 | 自动化与真实PG覆盖现行身份/版本/状态路径 | 真实历史台账、残留处置及年度/发布日期人工抽验没有全量签收 |
| B 检查完整性 | checker 目录无未实现项；失败/缺事实/缺分母不再冒充完成 | 实际材料仍有阻塞义务，7场景终态不能当作7份“无问题”；目录实现率不能替代运行完成率 |
| C 误报 | 新金额/主体准入、证据回执反例通过 | 无独立冻结真值，正式 precision、证据定位率及舍入误报目标未验收 |
| D 漏报 | 现行checker与回执回归通过 | overall/关键勾稽 recall 和8类真实反例全覆盖没有冻结人审基线 |
| E 审核闭环 | 真实PG并发/版本失效/完成锁与归档快照、浏览器交互通过 | 双忽略权威存储统一和完整刷新/重启用户链路签收仍未完成 |
| F 可靠性 | 真实空库/升级/幂等/重试与取消回收、SHA/版本导出通过 | 部署目标环境的完整资源验收和数据库+上传目录+审计日志恢复演练未本轮完成 |

独立交接最新准备包为 `outputs/reacceptance_20261007_round16/independent_intake_final_origin/`：32 开发 + 32 盲测槽，实际冻结材料 **0**、人审缺陷组 **0**，业务指标 `null`，交接门禁为 `pending_not_passed`。开发已知材料和本轮复跑原件不能更名计入盲测。封存真值未读取；SHA 只绑定文件内容，不冒充人员身份认证。

下一阶段先收口 WP3 双忽略存储与 WP6 全量消费对账，补 WP9 应收清单的端到端证据；由独立保管/标注/复核人员完成官方完整样本抽样、近重复排除、分歧裁定、代码/模型/配置/覆盖层冻结后两次运行，再按授权揭盲。正式发布仍须 Gate C/D 指标、UAT 和三件套恢复演练记录。本轮不宣布整个计划或生产可用性通过。

结构化输出：`outputs/reacceptance_20261007_round16/findings.json` / `outputs/reacceptance_20261007_round16/findings.csv`，计划矩阵 `outputs/reacceptance_20261007_round16/plan_matrix.json` / `outputs/reacceptance_20261007_round16/plan_matrix.csv`；最终自检 `outputs/reacceptance_20261007_round16/self_check.json`，源码差异 `outputs/reacceptance_20261007_round16/source_delta_final.json`，证据完整性 `outputs/reacceptance_20261007_round16/artifact_manifest.json`。原始日志、失败记录和运行脚本均保存，便于下一位复核者重新计算。
