# GovBudgetChecker 第十二轮整改复核报告

日期：2026-10-06。复核对象：第十一轮整改交接记录及当前本地工作区。依据：第十轮复核要求、实际源码与用户提供的 `v3_3_portable` 规则。本报告评价系统实现和验收证据，不对某一份公开材料给出最终业务审校结论。

**结论：R10-01至R10-04可以核销；OCR已取得真实实现与运行进展，但新发现两个OCR可信性缺陷，另有服务验证脚本和验收材料对接问题。整体业务验收仍未通过。** 全量回归通过。本轮五个开发服务场景的功能检查均成立，原服务脚本末尾清理报错，整次独立服务复跑必须保留为失败。

## 一、验收结果

| 检查 | 本轮独立结果 | 结论边界 |
|---|---|---|
| 全量pytest | **2543通过、137跳过、0失败**，9条警告 | 工程回归；不能据此推出业务准确率 |
| tests/remediation | **326通过、2跳过、0失败** | 测试PG URL与宿主cv2项跳过；真实容器OCR和隔离PG另行实跑 |
| Ruff、环境对账、日志检查 | 均exit0 | 无本轮检查失败 |
| 原规则反例 | 原9例、37条邻近例、金额边界全部符合预期 | 原主体/年度误绑可以核销 |
| 真值组与定位分母 | 英文ID保持两组；配额与指标一致；定位分母仅defect区域 | 原R10-03/04可核销 |
| OCR断网冷启动 | network=none、只读容器中完成预热及真实推理；pip check通过 | 验证既有本地组合镜像，不是当前Dockerfile从零构建证明 |
| 实际开发服务链 | 5个场景子检查均通过；2个校正检查通过 | 原脚本exit1，末尾临时目录清理失败；整次运行不记passed |
| 四格式 | 新实跑与第十一轮保存产物共14套原版/校正版均一致 | 两次各7个版本视图；不是14份独立业务样本 |
| 正式ZC07 | **not_passed，exit2** | 独立冻结分母、原始扫描认证与完整正式场景仍缺 |

当前分支为 `codex/zcode-remediation`，HEAD `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。验收的是未提交工作区：累计64个已跟踪文件修改、14个未跟踪源码、测试及相关说明文件。另将AGENTS和交接记录纳入保护，共80个文件。开工差异SHA-256为 `8d360c5a13250c2bc4481fa3a12e59e242841c5667af8c61d93bb98069c4ad6d`。本轮不修改业务源码、测试、既有整改材料或冻结真值。

## 二、原整改核销与OCR进展

### 原R10-01/02：主体与年度绑定反例已修

本轮重放实际规则、画像、台账与回执：学校主体带/不带年度、年度前/后置均不借用本单位事实；本单位99.89%的独立错误不再被他单位声明抑制。2024年口径可以跨数字分句继承，没有同年事实时转未完成；本年显式重置及本年正确表达仍计算。收入、基本/项目支出的原反例全部符合预期。

原九例和37条邻近检查均无expected/actual偏差；金额600.50/600.51相对600万元仍分别通过/失败，比例0.10/0.11个百分点边界保留。新增测试确实调用生产规则及关系回执，包括同年历史事实和独立历史关系ID；未发现以放宽容差、全部过滤或全部NA消除误报。

这些是限定输入的契约验收，不代表所有主体、年度、阶段表达均已覆盖。合成反例的测试页不是可认证原始PDF页，不计入业务定位指标。

### 原R10-03/04：分组与定位文档已修

[共享分组函数](</Volumes/workSSD/software development/GovBudgetChecker/src/services/truth_grouping.py:13>)优先取显式truth_group_id，再取原样truth_id，仅完整 `T<数字><单个小写字母>` 兼容旧多证据面。`DOC-001-income`、`DOC-001-expense`保持两组；实际配额与指标共享同一函数。T4a/T4b归为T4，T4income保留原样。没有改写旧冻结标签。

[定位分母说明](</Volumes/workSSD/software development/GovBudgetChecker/docs/ZC07_真实验收数据格式.md:80>)已明确只取defect标签的区域，多证据区域与缺陷组召回分母分开。纯函数输入1个defect、1个acceptable、1个manual_review，truth_total仍为1；定位门槛未变化。

### 原R10-05：不再是仅有任务文档

本轮独立启动既有本地镜像，验证实际加载的app/protocol源码SHA与工作区一致。模型在无网络、只读容器中完成真实白图预热与合成扫描图推理：`gbc.ocr.cells.v2`返回1张表、14个文本块；输入与原始预测指纹完整。实际包版本为PaddlePaddle3.2.0、PaddleOCR3.7.0、PaddleX3.7.2，pip check无依赖冲突。

模型清单SHA为 `9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea`；CPU=2的pipeline SHA为 `5bc11dae3b172ed5c3dd11e3a335b7394731fa989e7f0fb8cd9ad16f11b78a5f`。实跑使用的组合镜像ID为 `sha256:a1b7fb8d1c8c4e2a474520c1434818bbeed695e426f0f0c4e53d0ddd59b99ed1`。没有在本轮下载或替换模型。

合成扫描阳性经真实HTTP、独立Worker和PG形成2个可用操作数，原件证据仍unverified；收入合计1000→1010数值校正已持久化，收入金额及财政拨款占比回执从pass转fail，算得约99.01%。派生扫描预算及混合决算的原版OCR事实保持low_confidence；人工数值覆盖另有校正记录，没有把识别低分当成自动晋升依据。混合场景的OCR数值校正持久化、生成新版本和历史隔离成立，但受影响关系没有状态变化，不能冒称可信新算式阳性。

这是实质进展。原始政府扫描阳性、独立人工认证及阈值校准仍未完成；0.98是识别分数门槛，不能直接解释成98%的业务准确率。以下新缺陷需在继续业务验收前修复。

## 三、新发现的代码与对接问题

### R12-01 · P1：扫描认证按引文和页码复用，未认证格可借用结果

位置：[evidence_guard.py:327](</Volumes/workSSD/software development/GovBudgetChecker/src/services/evidence_guard.py:327>)、[第353行](</Volumes/workSSD/software development/GovBudgetChecker/src/services/evidence_guard.py:353>)。

守卫将认证成功引用存成 `(归一化引文, physical_page)`，后续引用只按这两个字段判断。政府表中重复金额很常见；同页不同格的相同数字不是同一条证据。

主审制作无文本层的合成PDF和明确标识的模拟外部认证清单，只认证cell1。另一cell2同页、同引文100，框为另一个未认证区域，有独立派生evidence_id。实际结果：

| 输入 | 独立逐格认证 | 生产守卫整体状态 |
|---|---|---|
| 只有已认证cell1 | true | verified |
| 只有未认证cell2 | false | unverified |
| cell1与cell2同时引用 | true / false | **verified**；complete=1、formal_issue_total=1 |

独立认证函数能拒绝第二格，问题在守卫将第一格结果复用到第二格。ZC07逐条认证仍拒绝第二引用，所以本轮没有证明正式验收器被绕过；生产状态却与正式门禁不一致，影响可信状态、问题计数和定位解释。

整改要求：将认证结果绑定到具体引用身份，包含原件、物理页、source_cell_id/block_id、evidence_id、quote、bbox及认证依据；不得跨格按相同数字借用。逐条未认证的OCR引用保持未核验，混合引用整体不能verified。补同页同金额不同格、改变框、同格重复引用、不同页及有/无文本层对照，覆盖完整生产守卫与正式认证函数。

### R12-02 · P1：资格验证与最终物化数值、定位未绑定同一结果

位置：[ocr_qualification.py:82](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_qualification.py:82>)及[promotion第285行](</Volumes/workSSD/software development/GovBudgetChecker/src/services/ocr_fact_promotion.py:285>)。

资格检查重算并比较candidate.cells的原始格子、分数、render_bbox；物化却从另一份candidate.rows读取数值，直接采用cell.page_bbox。rows数值和最终PDF点框没有进入同一绑定校验。

用新增测试的合成完整协议作有限契约复放，保留原始预测、格文本100、真实分数字段与复识别100：

| 改动 | qualification | 实际进入规则的结果 |
|---|---|---|
| 基线，rows与cells均100 | qualified | 总收入/财政拨款各100；金额和比例pass |
| 只把rows的总收入100改成900 | **qualified** | 总收入900成为可用操作数；生成“表900≠文本100”和“表算11.11%≠文本100%”两条错误 |
| 只把一个cell.page_bbox改为[1,1,2,2] | **qualified** | 原render_bbox未变，错误PDF点框进入事实证据 |

这证明候选出错或不一致时仍可通过资格门；不表示真实模型本轮自然识别出了900，也不是对真实公开材料误报率的估计。证据仍unverified不能补救确定性算式已经消费了与原始预测不一致的数值。

整改要求：资格检查、数值物化和证据定位消费同一份经过认证绑定的格对象；从重算结果派生rows，或逐格检查rows/cells值及跨度一致。page_bbox必须由已验证的坐标变换从render_bbox重新计算，或逐格校验两者一致，不能仅验证框非空有效。补只改rows、只改point框、行列/合并格错位、复识别来源变化等反例，同时保留实际阳性操作数和低分降级对照。

### R12-03 · P2：服务验证脚本先删临时目录，后停止仍写目录的进程

位置：[run_isolated_service_validation.py:112](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_isolated_service_validation.py:112>)、[finally第650行](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_isolated_service_validation.py:650>)。

原脚本在TemporaryDirectory作用域内运行API/Worker，退出作用域时开始删除uploads；进程停止和wait却在外层finally之后执行。本轮五个场景及两次数值校正检查均为true，资源快照也已写入；随后清理时报 `OSError: [Errno 66] Directory not empty: 'uploads'`。脚本用时276.18秒，exit1、status=failed，错误被诚实保留。外层OCR复跑回执亦failed，不能把该运行写成完整服务通过。

整改要求：先停止并等待API/Worker、关闭日志，再清理临时目录；可以用正确的上下文栈顺序或把进程清理放进临时目录作用域内finally。失败链路也要保持同一顺序。补仍有后台写入时的清理验证，不用吞掉OSError冒充成功。API/Worker、隔离PG和本轮OCR容器最终均已停止/删除；临时目录删除失败不应被当成已经彻底清理。

该问题属于工程验收脚本稳定性，不是本轮五个业务任务的运行中断。

### R12-04 · P2：扫描认证接口缺少正式验收材料对接说明

[正式加载函数](</Volumes/workSSD/software development/GovBudgetChecker/scripts/run_zc07_acceptance.py:664>)从golden读取 `scan_attestations_file`、`scan_attestations_sha256`，并要求清单位于对应corpus/doc_id内。[golden模板](</Volumes/workSSD/software development/GovBudgetChecker/docs/acceptance_templates/golden.template.json>)没有这两个字段，ZC07文档也未说明连接方法；[第78行](</Volumes/workSSD/software development/GovBudgetChecker/docs/ZC07_真实验收数据格式.md:78>)还写无可核实文本的扫描不能verified，与新增独立人工认证分支不一致。

纯函数检查：没有golden连接字段时即使认证文件已存在也加载不到；填入对应文件及SHA后，模拟认证引用可以通过扫描认证分支。部署侧的OCR_EVIDENCE_ATTESTATIONS环境配置不能自动替代冻结验收材料的连接字段。

整改要求：补golden模板及正式材料说明，明确清单/复核附件相对目录、外部人工冻结与SHA、部署和评测两端的装配步骤；将扫描正文表述改为“须有独立认证，否则未核验”。保持模板空白，不伪造真实复核人或认证阳性；补相对路径越界、缺字段、SHA不符以及完整装配的契约测试。

## 四、业务验收仍未闭合

### R12-05 · 沿用R10-05，P2：可信OCR业务闭环尚未核销

应核销“仅有任务文档、没有模型实跑”的旧判断，保留真实实施进展；但R12-01/02仍影响资格和认证边界，真实原始扫描阳性、独立人工扫描认证及独立阈值校准缺失，不能整体核销OCR。扫描续表自动合并、人工位置覆盖仍是交接明确保留的能力限制，不在已完成项中。

下一步以未用于当前开发定位的新原始政府扫描材料，证明高质量样本形成可信操作数、低质量及歧义继续未完成，逐格来源和认证独立可追溯；不要把从原生PDF渲染的派生图或合成控制纳入原始扫描配额。修复上述两个可信性缺陷后再进行正式认证验收。

### R12-06 · 沿用R10-06，P1：独立冻结材料与正式四场景尚未齐备

第十一轮和本轮实际场景身份核对如下：

| 开发场景 | 材料身份 | 独立实跑观察 | 不得据此核销的内容 |
|---|---|---|---|
| native_budget | 本地保存的真实2025年普陀区委社会工作部预算 | 原件、任务、版本、Worker事件、PG结果和四格式对齐 | 独立冻结人工真值 |
| native_final | 本地真实2024年度部门决算 | 同上 | 冻结已知缺陷与完整适用分母 |
| synthetic_scanned_control | 明确标识的合成图像PDF | 2可用操作数；数值校正使两条关系pass→fail；历史四格式稳定 | 真实政府扫描阳性或盲测配额 |
| derived_scanned_budget | 真实预算第1/8页的图像派生文件 | OCR低分候选降级、完整开发服务链 | 原始完整扫描预算场景 |
| derived_mixed_final | 决算第1/6页原生＋第8页图像派生 | OCR校正入库、Worker重审、新旧版本隔离 | 原始完整混合决算及可信算式变化阳性 |

五个原版重复下载字节一致；两个校正场景的旧版四格式字节稳定。每个job当前analysis_results为1行，校正后analysis_jobs.analysis_revision递增。parse_*表是累计计数，不能充当该job独占事实数。上述功能检查有效，但本轮整体脚本failed需修清理后再证明稳定复跑。

核对了80项受保护源码与交接副本SHA、80项选定最终产物SHA；第十一轮最终服务5个代码指纹、日志、原件和下载均对齐。新旧两次各7个实际下载视图通过当前门禁，14套四格式computed且一致。不得将此1.0写入冻结业务准确率、业务导出率或同版业务重复率。

正式预检仍为not_passed/exit2：登记29份、人工复核冻结真值1份、缺陷组3组、原件PDF认证0；空模板由正式validators返回incomplete。Docker现已可用，OCR模型清单存在，且真实运行已另外验证，不能再沿用“Docker或模型缺失”的旧理由。

仍需开发32份、独立盲测32份，每套8类各至少4份、盲测至少100个真实人审缺陷组；提交原件SHA、近重复审查、区域真值、完整适用关系分母、人工复核与分歧记录。完成原生真实预算、原生决算已知问题、原始扫描预算、完整混合决算校正四个正式场景，在同源同配置同版本上重复运行。当前已见开发材料不能重新标成独立盲测。

沿用门槛：正式问题precision≥98%、真实问题recall≥95%、重大问题无漏报、精确定位≥98%、正式证据真实性100%、越界定位0、确定性适用检查完成≥95%、状态/原因覆盖100%、四格式与重复运行100%一致。独立业务指标继续为null/not_computable，不能从pytest数量、模型分数或合成反例推算。

## 五、下一轮可执行整改

1. 修R12-01：扫描认证逐引用绑定；认证与未认证同金额共存时整体不得verified；同步生产守卫与离线门禁。
2. 修R12-02：原始预测、映射格、rows、数值、PDF点框和复识别来源保持同一条绑定链；数据不一致拒绝资格，保留真实阳性与低分对照。
3. 修R12-03/04：调整进程/目录清理顺序，补扫描认证正式材料字段与装配说明；完整五场景复跑应正常退出并保留失败证据。
4. 完成R12-05/06外部验收输入，以新的原始扫描和独立人工真值执行正式门禁。原R10-01至04保持回归，不重复列为未修。

可以继续受控、带人工复核地测试原生材料；当前尚不满足全类型自动审校正式使用的验收条件，也不能保证任意扫描预算/决算均准确判断并精确指出问题。应先修本轮四项，再进入正式业务验收。

## 六、证据与复核范围

- [问题JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/findings.json>)、[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/findings.csv>)、[核销台账](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/remediation_ledger.json>)。
- [原反例独立复放](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/root_rules_replay.json>)及同名Python；[扫描守卫反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/scan_guard_probe.json>)、[OCR资格反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/ocr_qualification_probe.json>)与各自reproduce脚本；[验收契约反例](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/acceptance_contract_probe.json>)。
- [OCR冷启动/服务复跑回执](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/ocr_service_review.json>)、[新服务原回执](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/live_services/service_receipt.json>)、[服务交叉核对](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/live_service_evidence_integrity.json>)、[交接SHA核对](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/handoff_integrity.json>)、[四格式门禁](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/production_export_gate.json>)。
- [全量日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/full_pytest.log>)、[整改测试日志](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/remediation_pytest.log>)、[正式预检](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/zc07_preflight.json>)、[PDF渲染检查](</Volumes/workSSD/software development/GovBudgetChecker/outputs/reacceptance_20261006_round12/pdf_visual_qa.json>)、首尾源码快照、差异与产物清单。

扫描守卫认证记录是明确标识的模拟复核fixture，不是真实人员对政府材料的认证；资格反例是合成完整协议，调用实际资格、晋升、物化和规则函数，不估计真实OCR误报率。服务复跑使用原脚本与真实HTTP、Worker、PG及本地模型，没有为通过修改业务源码或服务脚本。原版/校正版导出及实际99.01%与≠符号已渲染核对。

按AGENTS尝试并行子审，但三个子审均因工作区额度不足失败；本报告结论来自主审独立检查与重放，未声称取得完成的子审意见。未重复前端浏览器验收、真实云端AI增益或完整安全扫描；本轮不评估全新部署构建，也未创建独立人审真值。
