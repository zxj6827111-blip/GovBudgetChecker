# GovBudgetChecker 第十五轮整改与自检报告

日期：2026年10月7日。针对第十四轮 R14-01—R14-05 的五项可代码化问题，修复后由主线程完成全量回归、两次隔离真实服务和交付物核对。工作区分支 `codex/zcode-remediation`，HEAD `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`；被测身份以包含未提交修改的 719 项源码 SHA 清单为准，不能用 HEAD 代替。本轮没有提交、部署或覆盖旧验收证据。

**五项问题在本轮开发技术范围内自检通过。完整原始扫描主链两次跑完，缓存校正重审两次入库成功。整体独立业务验收仍未完成：原始扫描没有取得金额计算资格，独立冻结包没有真实人员、材料与人审真值。**

## 一、五项修复及核销边界

| 项目 | 修改与自检结果 | 本轮结论 |
|---|---|---|
| R14-01 扫描表误报缺失 | 结构存在性与金额操作数资格分开。原生有效文本支持完整性判断；扫描 OCR 仅提供存在性线索，未知页不允许证明缺失。两轮原件均无“缺失预算表:”断言；结构规则及核心表义务为 insufficient_data，manual_review 不计正式问题。原生材料真实缺表仍能检出，单位/部门收支预算总表别名及条件性表邻例保留。 | 技术核销；未知结构仍需原页复核 |
| R14-02 物理页绑定 | 候选必须绑定同一原件、物理页、300dpi 渲染、坐标变换、原始响应、模型/管线和逐页请求。页号、变换或响应混用被拒绝，事实按已绑定页创建。恢复 checkpoint 时重新渲染并比较几何，从绑定原始响应重建单元格，拒绝篡改。 | 技术核销；不把绑定等同于独立人审认证 |
| R14-03 栏次成为金额 | 解析器固定栏次/行次的 header 角色，金额物化再次过滤结构行；新 parser fingerprint 使旧污染缓存失效。两轮 35 页原件均保留真实 746.62，未产生栏次/行次事实。零值、合法整数及稀疏金额列回归通过。校正选择真实合计金额。 | 技术核销；未采用排除所有整数的方法 |
| R14-04 原扫描超时 | 默认 Worker 总预算按 600 秒原生阶段储备 + 每物理页 120 秒计算，上限 7200 秒；21 页为 3120 秒。每页渲染、识别及区域请求有界，保存分段进度和身份绑定 checkpoint；新增取消接口。两轮全 21 页实际 OCR 成功并形成当前分析版本。 | 技术核销；仍保留总资源上限 |
| R14-05 缓存读回与验收漏判 | JSONB 统一兼容 str/dict/null，非法对象及 bbox 拒绝；缓存按实际 parser fingerprint 读取。验收必须核对本次 job、结果 revision、run/version、原件 SHA 和当前事实/证据。任一版 ingest error 或未真实 cache hit/readback 都会失败；不借历史全库 counts 充当成功。 | 技术核销；原始解析行和校正覆盖层分别核对 |

主要实现为 `api/main.py`、`src/services/structure_view.py`、`src/engine/budget_rules.py`、`src/engine/pipeline.py`、`src/services/evidence_guard.py`、`src/services/ocr_pipeline.py`、`src/services/ocr_qualification.py`、`src/services/ocr_fact_promotion.py`、`src/services/pdf_parser.py`、`src/services/fiscal_row_roles.py`、`src/services/fiscal_fact_materializer.py`、`src/services/parse_artifact_store.py`、`src/services/structured_ingest_runner.py` 和真实服务验证脚本。

取消接口为 `POST /api/jobs/{job_id}/cancel`，沿用任务访问授权，Worker 轮询取消标记并停止客户端请求；OCR 服务可能完成当前有界推理，回执已明确这一范围。显式 `PIPELINE_TIMEOUT_SEC` 仍受尊重；已有部署若主动设置 600 秒，需按材料规模调整该显式配置。客户端等待与 Worker 资源预算分别记录。

## 二、自检证据

| 检查 | 结果 |
|---|---|
| 全量 pytest | **2651 通过、137 跳过、0 失败**；14 条警告，78.72 秒 |
| 新增四组针对性回归 | 69 项通过，覆盖结构未知/真缺表、跨页及 header、有限预算/取消/恢复/JSONB/验收负例、独立交接门禁 |
| Ruff、依赖、环境变量一致性、日志安全、diff 空白检查 | 全部通过；63 个依赖兼容 |
| 两次隔离 API → 独立 Worker → PostgreSQL 16 → 实际 OCR | 两轮各 7/7 场景通过；源码运行期间未变；进程、PG、目录和日志清理完整 |
| 最终交付核对 | 719 项冻结源码 SHA 未变；20 个初审/校正导出视图重新读取，JSON/CSV 语义与 PDF/DOCX 报告版本摘要一致，产物 SHA 与服务回执一致；原扫描 PDF/DOCX 可提取正文均没有缺表断言且保留识别未完成提示 |
| 重复与历史版本 | 初审四格式重复下载字节一致，校正后旧版本四格式保持原字节；两轮两份原件的状态、规则状态、问题明细及正式计数规范化后相同 |

运行全量回归时发现新增取消路由尚未登记到冻结路由测试，已准确补登记并重新运行全量测试。最终依据为 `pytest_pass.log`；开发中间失败日志保留，不作为通过证据。跳过项不转换为业务验收通过。

两轮场景耗时如下，包含上传、等待、导出及适用的校正流程；完整原扫描的 Worker 耗时另列，不能与客户端耗时混用。

| 场景 | 第一次 | 第二次 | 技术服务结果 |
|---|---|---|---|
| native_budget | 19.72 秒 | 17.74 秒 | 两轮通过 |
| native_final | 17.68 秒 | 17.72 秒 | 两轮通过 |
| synthetic_scanned_control | 45.96 秒 | 45.07 秒 | 两轮通过 |
| derived_scanned_budget | 67.83 秒 | 63.95 秒 | 两轮通过 |
| derived_mixed_final | 52.86 秒 | 51.02 秒 | 两轮通过 |
| original_scanned_budget | 614.70 秒 | 605.85 秒 | 两轮通过 |
| original_mixed_final | 21.90 秒 | 21.97 秒 | 两轮通过 |

原始扫描 Worker 实测 **613.44 秒 / 604.88 秒**，均在 3120 秒预算内完成。第一轮实际超过旧 600 秒限制，证明修复进入 Worker 主链，而非仅扩大 UI/客户端等待。两轮均为新的隔离 API/Worker/PG，原始扫描逐页重新实际识别，没有以跨运行缓存代替完整复跑。

OCR 使用固定本地组合镜像 `govbudgetchecker-ocr:round11-offline-current`、CPU threads=2；镜像身份、模型及管线 SHA、健康状态和容器代码对应关系见 `ocr_runtime.json`。未重新声称 Dockerfile 从零构建已验证。任务结束后已保存日志，并只删除本任务创建的 OCR 容器。

## 三、原件主链和业务能力边界

两份完整官方原件均保留原始字节，并与第十四轮材料核对：

- 金华市婺城区新狮街道社区卫生服务中心 2024 年单位预算，完整 **21 页扫描**，SHA `5978b569d1f4bf58d07281db4150e72b9cf867fa45a051da4976aabdc6a5eec7`。
- 金华市福利彩票发行中心 2024 年决算，完整 **35 页混合件**，SHA `f22ac36e2536a15d9a90c5e2ace74b84c3fa79f8e15d010d8a7663ff883e97f7`。

21 页预算两轮均 OCR pages 1—21 全部 ok、failed_pages 为空，入库 done，形成当前 analysis_revision。P12 的实际 OCR 标题“2024年单位收支预算总表”保留为结构存在性线索；未编造其余页的确定命中或 bbox。两轮正式问题计数均为 0，BUD-001 和核心表结构义务均未计完成；导出显示识别尚未完成及人工复核原因。

**该扫描原件的可计算操作数仍为 0，最终是 review_required/incomplete。** 主体/关键识别资格及独立认证尚不足，不能宣布其已能自动完成数字勾稽。此次通过的是完整有限资源主链、页身份及误报控制，没有降低资格阈值将低质量候选硬转为可靠事实。

35 页混合决算两轮初审和校正重审均 structured_ingest done，校正均 cache_hit/readback_completed=true，恢复 2947 个原生单元格，当前 run/version 与数据库限定查询对应。P7 合计 **746.62→756.62** 在校正覆盖层及 FactContext 中持久化；原始 parse_facts 与 parse_evidence 保留 746.62 及其原页证据，这是不可变解析基础。表头污染已排除。

这份混合原件的受影响关系状态没有发生变化，回执缺少相关操作数依赖，故只核销真实金额校正传递、缓存读回及当前版本入库，不宣称其业务重算阳性。真实模型合成扫描控制另有 2 个可计算操作数、8 格嵌套 OCR provenance 缓存读回，1000→1010 后金额/比例关系从 pass 变 fail；该阳性单独标为合成工程控制，不计官方原始扫描或独立认证。

## 四、独立盲测交接

已更新 [独立盲测交接操作](独立盲测交接操作_20261006.md)，新增角色、近重复/泄漏审查、实际签认、完整原件技术准入、按当前版本查询、两次输出先冻结后解封及附件 SHA 模板。`scripts/prepare_independent_acceptance.py --check-handoff` 分 intake/execution 两阶段检查，不读取封存真值；原审/校正 ingest error、无真实缓存读回、原扫描未全页完成、身份不符或清理未完成均不能通过技术准入。

本轮新准备包为 `outputs/remediation_20261007_round15/independent_intake_final/`：64 空席位、**0 冻结材料、0 人审真实缺陷组**。空包门禁仍拒绝，业务指标保持 null/not_evaluated，没有注册虚构人员、AI 真值或签认。本轮两份已用于开发的原件继续列入排除清单，不能重新改标为未接触盲测。

实际交接仍需独立保管、标注、复核和执行人员提供开发/盲测各 32 份新原件，预算/决算 × 部门/单位 × 原生/扫描八层各至少 4 份；盲测至少 100 个独立真实缺陷组，补完整适用分母、区域及扫描认证、近重复/访问审查、分歧复核和签认。随后冻结同一源码、配置、模型、原件及覆盖层，完整真实服务运行两次，先冻结四格式系统输出再解封评测。

门槛保留：precision≥98%、recall≥95%、重大问题零漏报、精确定位≥98%、证据真实性100%、越界0、确定性适用检查完成≥95%、状态/原因覆盖100%、四格式及同版本重复运行100%一致。开发自检和 pytest 数量不转换为这些独立业务指标。

## 五、交付及复跑入口

证据根目录为 `outputs/remediation_20261007_round15/`。最终 [自检回执](../outputs/remediation_20261007_round15/self_check.json)、[结构化核销清单](../outputs/remediation_20261007_round15/remediation_closure.json) 及 CSV 保留五项核销范围；`services_run1/service_receipt.json` 和 `services_run2/service_receipt.json` 保存两次真实服务、当前版本数据库观测、导出身份和清理回执；各场景保留完整 source.pdf、status、FactContext 及四格式导出。

`verify_delivery.py` 重新读取实际导出、核对冻结源码及两轮原件规范化结果；`export_self_check.json` 保存 20 个视图的复验。`original_identity.json`、`ocr_runtime.json`、`progress_snapshots.jsonl`、`pytest_pass.log`、`ruff_final.log`、`dependency_check.log`、`env_consistency.log`、`log_message_safety.json` 和 `artifact_manifest.json` 保存对应证据。准备与服务复跑均使用新输出目录，旧轮次文件保持不变。

子任务负责部分实现及交接设计，其中两条委派因工作区额度中断；主线程独立完成后续代码核对、回归、两轮真实服务和最终交付检查，没有将未完成委派算为验收通过。
