# GovBudgetChecker 第十一轮整改交接记录

日期：2026-10-06。依据：[第十轮复核报告](GovBudgetChecker_第十轮整改复核报告_20261005.md)。本记录是开发整改与工程验证交接，供另一会话独立复核，不是独立业务验收报告。

**R10-01 至 R10-04 已实现整改并通过开发验证。OCR 已从任务文档推进到真实模型、逐格资格、扫描认证接口和生产校正链；R10-05、R10-06 仍不能整体核销。** 真实独立人工扫描认证、原始扫描阳性、冻结开发/盲测集及正式四场景尚未齐备。缺数据业务指标保持 null / not_computable。

## 逐项交接

| 编号 | 本轮落实 | 开发验证状态 | 独立验收还需确认 |
|---|---|---|---|
| R10-01 年度与未知主体误绑 | 年度不再作为主体已确认的依据；未知/他单位声明不进入本单位金额或比例集合；保留年度/主体锚点 | 已完成 | 年度前后置、无年度、未知名称；他单位同句不能压掉本单位真实错误 |
| R10-02 跨分句往年口径 | 金额/比例共同继承主体、年度、阶段，显式本年重置；历史声明只匹配同年同主体同阶段事实，另建历史关系ID | 已完成 | 无往年事实转未完成；有同年事实的阳性/阴性；本年重置、收入/基本/项目支出与金额容差 |
| R10-03 真值组契约 | 配额和评测共享 canonical_truth_group；显式 truth_group_id 优先，原样 truth_id 次之，仅 T数字+单字母兼容旧多证据面 | 已完成 | DOC-001-income / DOC-001-expense 保持两组；同组多证据不增加缺陷配额；旧冻结标签未改 |
| R10-04 精确定位分母 | 文档明确仅 defect 真值区域进入正式定位分母，多证据区域与缺陷组召回分母分开 | 已完成 | 1 defect + 其他标签的正式定位分母仍为 1；未改定位门槛 |
| R10-05 可信 OCR | 真实模型离线绑定、逐格协议、原格框、真实分数/复识别、资格门、来源入库、外部人工认证接口、校正重审 | 工程实现与有限实跑；未核销 | 真实原始高质量扫描阳性、独立人工认证与阈值校准；完整扫描风险覆盖及业务门禁 |
| R10-06 冻结材料/四场景 | 提供可复跑的隔离服务脚本、真实原生材料与明确标记的派生/合成开发材料、失败留痕和交接模板 | 未完成正式验收 | 开发32 + 独立盲测32、8层各至少4份、盲测至少100真实人审缺陷组、冻结分母/近重复审查及正式四场景 |

规则实现见 `src/engine/rules_v33.py`，契约见 `src/services/truth_grouping.py`。新增测试为 `tests/remediation/test_round11_contracts.py` 和 `test_round11_ocr.py`。金额千位分隔符不会被误当成分句边界；金额0.5万元、比例0.1个百分点门槛保持。

## 验证结果及范围

- 全量 pytest：**2543 通过、137 跳过、0 失败**，9条警告。跳过项目不算通过。
- 第十轮原脚本独立复放：原九例、金额边界及37条邻近检查符合预期；37条 expected/actual 无差异。
- 针对混合链缺口、年度、原子快照、OCR及旧存储契约的检查：140通过、1跳过；本轮改动文件 Ruff 通过。
- ZC07 正式预检：**not_passed，exit2**。开发/盲测清单和正式 E2E 证据缺失，业务准确率、召回率、精确定位、证据真实性和适用检查完成率不可计算。

本轮实际服务运行结果见下方回执，不以 pytest、模型分数或导出一致率代替业务准确率。真实输入已用于开发验证，不能再冒充未见独立盲测材料。

## OCR 实现与证据

1. 锁定 PaddlePaddle 3.2.0 / PaddleOCR 3.7.0，传递依赖由 `ocr_service/requirements.lock.txt` 约束；显式预置8个实际启用子模型。服务核验整个清单，真实白图预热成功后才 ready；预测前后再次核验。关闭矫正、方向模型及未绑定的可选模型。
2. 断网、只读容器完成冷启动与真实推理。模型清单 SHA 为 `9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea`；本次 CPU=2 的 pipeline SHA 为 `5bc11dae3b172ed5c3dd11e3a335b7394731fa989e7f0fb8cd9ad16f11b78a5f`。模型包在忽略的 `models/`，须连同清单交接。服务不会在启动时下载模型。
3. `gbc.ocr.cells.v2` 根据 HTML结构、格子几何和文本空间重叠映射行/列及合并格，保留原始片段分数与原始预测JSON；不以数组顺序猜配对、不造字符分数。实际政府页已映射逐格结果；缺数组、分数/几何冲突仍拒绝。
4. 原件300 DPI渲染；为本机8GB Docker环境将推理输入长边限制为1024，记录精确线性缩放并回到原像素/PDF points。真实渲染检验0/90/180/270°及裁剪偏移；逐格框保留原物理页。此前直接大图和1600上限运行出现OOM，失败记录保留，不计通过。
5. 关键原始片段最低0.98、主体/年度/阶段/单位/列/来源明确且不冲突才有操作数资格。高分数字格用新的裁剪发第二次真实HTTP请求；首轮整表已低分或映射失败时标记未复识别，不伪称 completed。区域请求与页面总预算限制失败时继续拒绝。
6. 阳性控制是明确标为合成的图像PDF，实际模型生成2个可用操作数，最弱关键分数约0.9898。V33-103金额/比例通过；校正收入合计1000→1010后，相应金额和比例关系失败，算得财政拨款占比约99.01%。格框已在原图叠加复核。该结果不能替代真实政府扫描阳性或人审真值。
7. 操作数资格通过仍保持原件证据 unverified。扫描认证另走 `scan_evidence_certification.py`：部署固定外部人工清单SHA，逐格核验原件、页、框、引文、格身份、模型/原始响应、整页/裁剪PNG以及真实复核附件SHA。缺独立认证时，不依赖隐藏文本层或OCR自我比对认证。

当前认证阳性只在合成契约测试中验证。模板没有填写真实复核人或伪造冻结记录；原始扫描材料、人工认证和完整独立扫描风险覆盖尚缺。扫描续表自动合并未核销；人工位置覆盖仍未应用，属于原报告明确保留的能力限制，未列入已完成项。数值、单位与列映射校正不能据此宣称支持人工改框或修改事实主体/年度。

实际镜像验证采用锁定依赖修复层、模型层和当前源码层组合，保存最终image ID、运行包版本、pip check、容器配置及加载源码SHA；**未把这次组合镜像验证写成从当前 Dockerfile 全新构建成功**。基础部署仍挂载模型目录；断网验证镜像另将同一模型包烘焙到只读镜像以隔离本机挂载故障。Docker启动故障、OOM、上传413和早期脚本断言失败均有留痕。

## 服务链与材料身份

使用真实 localhost API、独立 Worker、隔离 PostgreSQL16.15、真实 OCR HTTP和实际四格式下载；内联回退与云端AI关闭。所有完成的开发场景终态为 review_required，服务完成不等于材料审校通过。

最终运行 `isolated-eaa2c06e9573` 耗时290.57秒，5/5场景的工程服务检查通过。5个原版与2个校正版共7套四格式语义/版本一致；5个原版的重复下载字节一致，2个校正场景的旧版四格式字节稳定。没有据此宣称完成同版独立重复业务回放。API/Worker、隔离PG及本轮OCR容器均已停止或清理。

| 开发场景 | 输入身份 | 验证范围 | 正式验收限制 |
|---|---|---|---|
| native_budget | 真实2025年普陀区委社会工作部预算 | 上传/排队/Worker/SQL/导出 | 未冻结人工真值 |
| native_final | 真实2024年普陀区委社会工作部决算 | 同上 | 未冻结已知缺陷组及独立分母 |
| synthetic_scanned_control | 明确标记的合成扫描控制 | 真模型阳性、操作数、OCR事实数值校正、新旧版四格式 | 合成材料，不能占真实扫描或盲测配额 |
| derived_scanned_budget | 真实预算第1/8页的图像派生文件 | 真模型、逐格候选、低分降级及完整服务链 | 非原始扫描材料、非完整预算；不满足正式扫描场景 |
| derived_mixed_final | 真实决算第1/6页原生 + 第8页图像派生 | 混合页、OCR候选、数值校正/Worker/历史下载 | 派生且非完整原件；不能代替正式混合决算验收 |

真实预算原件来自 [普陀区政府公开附件](https://www.shpt.gov.cn/zhengwu/shgzb-zfbm/2025/91/195679/cef2edce224140019949b9fb468f7200.pdf)，SHA为 `06a572d3f22cc7d218ad6273bf885a0f7237ca7b8f4c4a06b2c2764c508c5317`；真实决算原件SHA为 `44874c062a89c26ea6d94e4e6e7dccdaa136642c2c9bbb560ed21b33039b0597`。派生文件及物理页映射另在 `development_materials/derivatives.json`，无损压缩保留原像素，并记录新SHA。

实跑额外修复：年度解析不再把20.82等小数尾数当2082年；API和解析存储年度统一转整数；原子JSON快照将有限Decimal保留为精确字符串，非法值拒绝；终态写入失败不会继续记录完成。校正端点使用正式排队路径，由独立Worker生成新版本。数据库 `analysis_results` 每个job只保存当前行，校正检查使用 `analysis_jobs.analysis_revision` 递增；不以累积 parse_* 表计数冒充本任务独占事实数。

派生混合场景确实覆盖了OCR事实的数值校正、持久化和重审，但因其他操作数/说明不足，其关系状态没有转变，不能声称该场景完成了可信新算式。明确的 pass→fail 算式变化来自合成阳性控制；正式混合场景仍需完整真实材料与人工冻结真值。

## 交接证据与复跑

产物目录：`outputs/remediation_20261006_round11/`，由仓库忽略规则排除在Git提交之外。请保留整个目录及模型包；只复制HEAD不能复现本次未提交工作区。

- [逐项台账JSON](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/remediation_ledger.json>)、[CSV](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/remediation_ledger.csv>)。
- [最终真实服务回执](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/live_verified/service_receipt.json>)：任务/版本、原件SHA、Worker事件、SQL、实际下载SHA、重复下载和校正前后回执；同目录保留原样产物。
- [全量测试](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/full_pytest_final.log>)、[邻近规则复放](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/rules_replay.json>)、[Ruff](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/ruff_final.log>)、[正式预检](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/zc07_handoff_preflight.json>)。
- [源码清单](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/source_snapshot.json>)、开工baseline.json/before.patch、交接working_tree.patch、逐文件源码快照、本轮相对开工差异、[产物SHA清单](</Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261006_round11/artifact_manifest.json>)。
- OCR断网配置/启动日志/健康响应、image ID、包版本、实际原始预测、正反例、格框叠加与导出预览均在产物目录。`scripts/export_ocr_provenance.py` 从原件和事实快照重新生成请求PNG/格裁剪，并与实际请求SHA逐字节对账；这部分明确标记为重建请求证据，不是独立人工认证，覆盖的是快照中的OCR财政证据格。

```bash
.venv/bin/python -m pytest -q
.venv/bin/python outputs/reacceptance_20261005_round10/root_rules_replay.py
.venv/bin/python scripts/run_zc07_acceptance.py --output outputs/independent_review/preflight.json
```

隔离服务复跑命令保存在 `outputs/remediation_20261006_round11/reproduce_services.sh`；每次使用新输出目录。需要先启动同一离线模型/源码镜像，并提供实际localhost OCR地址。脚本清理自己的API、Worker和隔离PG；不使用生产数据库或生产.env。正式验收仍需按 [冻结材料要求](冻结盲测材料要求_20261005.md) 填写完整manifest与E2E证据，再执行正式门禁。

## 下一会话的验收顺序

先核对源码和产物SHA，重放R10-01至04及旧反例，再复核阳性格框、真实请求/局部响应、候选降级、数值校正前后关系和四格式历史隔离。工程验证成立后，仍需真实人员提交独立冻结材料、分母、近重复/分歧处理记录及扫描认证；模板 `freeze_record.template.json` 保持空白。收齐后在同一冻结代码/模型/配置上跑两次正式四场景，先固定输出再解封真值。未完成项保持未完成，不用本轮合成或已见开发材料填补盲测配额。
