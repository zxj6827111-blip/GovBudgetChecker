# GovBudgetChecker 第十三轮整改交接记录

日期：2026-10-06。对象：第十二轮复核报告中的四项问题、原始扫描材料验证及独立盲测准备。

**四项问题已实施修复并通过工程验证，供另一会话独立核销。全量测试 2582 通过、137 跳过、0 失败。整体业务验收仍未通过：新原始扫描预算已经完整实跑，但未形成可用确定性操作数；独立人审认证、冻结真值及正式业务四场景仍缺。**

分支 `codex/zcode-remediation`，HEAD `9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd`。本轮在既有未提交工作区继续整改，保留原变化，没有提交、重置、覆盖旧真值或向其他会话发送消息。实际被测工作区身份由 `outputs/remediation_20261006_round13/source_snapshot.json` 和源码副本固定，不能仅用 HEAD 代表本轮实现。

## 四项整改与证据

| 项目 | 实现 | 验证与边界 |
|---|---|---|
| R12-01 扫描认证跨格借用 | evidence_guard 对每条引用独立认证；先验证原件、物理页、source ID、quote、派生 evidence_id，再核对认证的 bbox、模型、原始预测与复核依据。未认证 OCR 引用清除旧 verified 声明，同页同金额不共享结果；有文本层也不能替代 OCR 格认证。 | 同页同金额不同格、改框、不同页、改原件/模型/预测/证据 ID、有无文本层、同格重复、原生与扫描混合均覆盖。旧复现的 mixed 从 verified 变为 unverified；逐条正式门禁仍是 true/false。结构 complete 与真实 verified 分开记账，不冒充业务认证。 |
| R12-02 数值与位置脱离预测 | 从原始预测重建唯一格对象，逐格比较行列/跨度/文本/分数/像素框；rows 必须与重建结果一致。PDF 点框由已验证变换重算并比较。资格和物化使用同一候选快照及重建格；不一致者拒绝资格，重建成功时物化也使用原始数值/位置。 | 单改100→900、单改点框[1,1,2,2]、行列/跨度错位、复识别来源变化均拒绝，900及错框不进入事实。逐页保留真实区域请求日志，将复识别输入 SHA、预测 SHA与具体格和像素区域绑定；同时改候选两处输入 SHA不能改写请求日志。真实合成控制仍形成2个操作数，低分原始扫描保留未完成。 |
| R12-03 服务清理顺序 | service_lifetime 位于 TemporaryDirectory 内层，先停止/wait API及Worker并关闭日志，再删除临时目录；正常和失败路径一致。清理异常使整次失败；PG删除、目录消失、进程/日志状态及运行期间源码一致性分别留痕。 | 真实后台持续创建uploads文件的正常/异常退出测试通过。最终7场景实跑passed，errors为空，四项清理状态及source_unchanged_during_run全部true；另一次真实金额格注入运行也完成清理。没有吞掉OSError。 |
| R12-04 正式扫描认证装配 | golden空模板补scan_attestations_file/SHA字段；文档明确corpus/doc_id相对清单、相对复核附件、SHA与真实人员签认。部署环境和正式golden分别装配；扫描只有独立认证才可verified。加载器拒绝缺字段、非法SHA、绝对清单和目录越界。 | 缺字段、SHA不符、绝对/越界路径和完整模拟装配测试通过。模板仍为空；模拟复核记录只验证接口，不是政府材料人审认证。 |

实现位置：`src/services/evidence_guard.py`、`ocr_qualification.py`、`ocr_fact_promotion.py`、`ocr_pipeline.py`、`scripts/run_isolated_service_validation.py`、`run_zc07_acceptance.py`。新的操作数绑定版本为 `raw-cells-pdf-v1`，加入OCR配置指纹，避免沿用旧的资格缓存。扫描协议仍为 `gbc.ocr.cells.v2`。

契约证据：`ocr_contract_tests_final_confirmed.log`（82通过，含旧OCR契约和新增39项）、`scan_guard_probe_fixed.json`、`ocr_qualification_probe_fixed.json`。模拟扫描认证复现只读沿用第十二轮明确标识的合成PDF/模拟认证夹具；没有生成真人签认。

## 回归与真实服务

| 检查 | 本轮结果 |
|---|---|
| 最终全量pytest | 2582通过、137跳过、0失败，9条警告，69.16秒 |
| OCR资格/认证/清理契约 | 82通过；包括新增39项 |
| 原规则复现 | 原9例、37条邻近例及金额边界重放符合预期；邻近expected/actual偏差0 |
| Ruff | 本轮业务文件及准备工具通过 |
| 最终真实API + 独立Worker + PG + OCR | 7场景均通过，910.67秒，生产源码和验证脚本在运行期间未变化 |
| 四格式 | 最终7个原版与3个校正版视图均一致；7个原版重复下载字节一致，3个旧版本四格式保持字节一致 |
| OCR输入与原始响应复核 | 控制原版/校正版各2条引用、原始扫描22条引用，重建请求图像与已存原始JSON的SHA均匹配，错误0 |
| 正式空包门禁 | not_passed、exit2，业务指标保持不可计算；没有把64个席位当64份材料 |

最终回执位于 `services_final/service_receipt.json`；保存源文件、job/revision、Worker事件、PG记录、实际下载与日志。7场景为原生预算、原生决算、合成扫描控制、派生扫描预算、派生混合决算、新官方完整扫描预算、新官方完整混合决算。全部为开发验证，不是独立冻结四场景。

真实控制仍是合成材料：2个操作数资格通过、证据unverified；1000→1010工程覆盖使金额及占比关系pass→fail，约99.01%。其作用是确认修复保留真实模型阳性，不计原始政府扫描配额或业务指标。

本轮沿用既有本地组合镜像 `sha256:a1b7fb8d1c8c4e2a474520c1434818bbeed695e426f0f0c4e53d0ddd59b99ed1`；模型清单SHA `9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea`，CPU=2的pipeline SHA `5bc11dae3b172ed5c3dd11e3a335b7394731fa989e7f0fb8cd9ad16f11b78a5f`。本轮用只读、localhost映射容器；实际app/protocol源码SHA和pip check留在ocr_runtime。没有重下载模型，也没有宣称当前Dockerfile从零构建已经验证。第十二轮同镜像断网冷启动证据仍是历史独立证据。

## 原始材料验收结果

直接下载官方浙江政府云域名的原始附件，保存URL、下载字节SHA、逐页文本/图像盘点及实际渲染，未从原生材料制造扫描件。完整身份见 `original_material_review_manifest.json`，来源原始盘点见 `original_materials/zhejiang_inventory.json`。

| 原件 | 完整链路与观察 | 当前结论 |
|---|---|---|
| 金华市婺城区新狮街道社区卫生服务中心2024年单位预算，21页全扫描 | OCR请求21页、成功21页、失败0；10个表候选，3个形成低置信度候选片段，22个候选事实。单位/主体绑定和关键分数未过资格，自动操作数0；22条引用的请求图及原始JSON SHA复核错误0。 | 技术链路完成；原始扫描高质量阳性和独立认证未通过，不能核销R12-05。 |
| 金华市福利彩票发行中心2024年单位决算，35页 | 官方原件P1为扫描封面，P2-P35为原生正文；OCR仅处理P1且无表，正文形成原生事实。API/Worker/PG、校正重审、版本及导出流程成立。 | 完整混合原件已实跑；其OCR只覆盖封面，不能证明原始扫描金额格阳性。 |

预算官方原件：[附件](https://zjjcmspublic.oss-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn/jcms_files/jcms1/web3613/site/attach/0/b2f2571951e34e18a02294f692daf8fe.pdf)。决算官方原件：[附件](https://zjjcmspublic.oss-cn-hangzhou-zwynet-d01-a.internet.cloud.zj.gov.cn/jcms_files/jcms1/web3563/site/attach/0/9175fdd8afa244799e31231a9b724c8d.pdf)。附件创建时间未当作发布日期。

原始扫描报告的业务验收还有明确阻断：原件P12/P13/P14等存在收支、收入、支出表，但系统因OCR候选未进入规则视图，仍输出BUD-001的缺表错误提示。不能将“识别未完成”当作“原件缺表”，这些提示与AI查看的实际页图不一致，须继续核对并整改缺失判定的识别前提。工程服务passed并不核销这项业务问题；对应开发观察见 `original_business_limitations.json`，正式误报率仍不可计算。

按AGENTS规则并行检查结构/核心表、数字/说明与证据，保存 `original_audit/structure.json`、`numbers.json`、MD说明及页图。这些是AI开发观察：预算当前PDF未见其引用的绩效表11，须核对发布页独立附件；决算有零预算完成率、自评资金覆盖比例和宣传促销执行率的内部冲突片段。没有将这些观察注册为独立人工真值、缺陷配额或扫描认证。

最终7场景的通用校正选择器在新混合决算中先选中了行次1，1→11只能证明流程，不能当金额校正阳性。为避免借此声称金额验收通过，另保存可复跑专用开发驱动 `validate_original_amount_correction.py`：对原件P7实际金额框[238,256.38,314,275.38]及文本746.62核对后，明确选择对应事实，在隔离环境注入746.62→756.62。专用驱动及SHA、3场景回执见 `original_amount_correction/` 和 `original_amount_validation_summary.json`。

该金额格覆盖已持久化、新版生成、旧版四格式保持一致、清理完整；受影响关系状态没有变化，仍不能作为确定性金额关系重算阳性。它是工程注入实验，不是原件金额应改为756.62的人审结论；原始PDF保持不变。行次事实及关系适用性需在正式原生材料验收中核对。

## 独立盲测准备与剩余条件

已完成可复跑工具 `scripts/prepare_independent_acceptance.py` 和 [交接操作](独立盲测交接操作_20261006.md)。最终准备包 `independent_intake/` 含64个空席位、已见开发PDF的SHA排除清单、713项源码/配置/依赖文件指纹、空白标注及扫描认证模板、四场景回执模板、待实际人员填写的冻结记录。code_manifest逐项与当前文件对账，差异0。

开发和盲测各32份、八类各至少4份、盲测100个真实人审缺陷组尚未提供。包内actual_frozen_documents=0、actual_human_defect_groups=0；人员/时间/签认留空，dataset.documents为空。没有读取新的盲测标签，也没有把本轮官方原件或AI观察改标成盲测。初版准备包和中断/阶段性服务尝试保留为历史过程，不作为最终通过依据。

还需实际独立材料保管人、标注人和复核人提供原件与真值，完成近重复审查、完整适用分母、区域标注、分歧处理、访问隔离与冻结/解封签认；扫描认证由真实人员按golden路径/SHA装配。原始扫描高质量操作数阳性、独立阈值校准、正式完整四场景及同源同配置同版本的两次业务运行尚未核销。扫描续表自动合并和人工位置覆盖仍是已有能力限制。

沿用门槛：precision≥98%、recall≥95%、重大问题零漏报、精确定位≥98%、正式证据真实性100%、越界0、适用确定性检查完成≥95%、状态/原因覆盖100%、四格式和重复运行100%一致。工程运行中的一致性不转换成这些冻结业务指标。

## 下一会话复核入口

先验证 `artifact_manifest.json`、`source_snapshot.json`、`source_changes.json`、`this_turn_diff.patch` 与当前工作区；源码副本包含本轮实际未提交实现。然后运行全量/新增契约、重放两项修复复现，核对最终服务回执、源SHA、OCR输入/原始预测、输出版本和清理状态。具体命令见 `outputs/remediation_20261006_round13/reproduce_services.sh`。

优先核销R12-01至R12-04，独立重核新原件；按实际证据继续保留R12-05/06未闭合，不沿用“未实跑原始扫描”的旧理由，也不能将原始扫描实跑成功等同于操作数/认证业务通过。
