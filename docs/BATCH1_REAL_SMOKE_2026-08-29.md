# 批次一真实系统冒烟与补修记录（2026-08-29）

- 目的：批次一（T1–T7）自动化验证全绿之后，按「系统能够正常运行、各项功能都正常」
  的验收口径，用**真实后端 + 真实前端 + 真实浏览器**做端到端走查，并修复走查发现的问题。
- 环境：本地裸跑（非容器）。后端 `.venv` uvicorn `api.main:app:8000`（根 `.env`：
  AUTH_ENABLED=true，API_KEY=.env 值，Postgres 可达，AI provider 未配置）；
  前端 `next-dev.cjs:3000`（代理转发 8000）。登录账号 admin（data/users.json 既有）。

## 1. 走查结论（全部通过，含一处补修）

| # | 测试点 | 方式 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 未登录访问 `/` → 落 `/login`；admin 登录 → 落 `/workbench` | 真实点击 | ✅ | 截图 gui-test（登录页、workbench 快照：KPI 87/1/8/6、覆盖率 7%、admin 管理员徽章、服务正常） |
| 2 | 工作台队列表 → JOB5（review_required 预算件）「复核」入口进审核工作台 | 真实点击 | ✅ | 三栏布局：23 页缩略图、PDF 预览、6 条真实命中（CMM-002 连续句号、BUD-109 科目不一致、BUD-111 同比矛盾-高风险） |
| 3 | 审核工作台「确认问题」工作流写入 | 真实点击 | ✅ | 计数条 0/0/6 → 1/0/5，卡片出现「已确认」徽章，`uploads/.issue_workflow.json` 落盘 4 条记录 |
| 4 | 质量管理页指标渲染 | 真实浏览 | ⚠️→✅ | 发现既有缺口（见 §2），补修后指标全部显示：处理成功率 99.0%、证据完整率 31.5%（797 条）、门禁表带样本依据 |
| 5 | 系统设置 → 运维操作 → 结构化清理预览弹窗（只预览未执行） | 真实点击 | ✅ | A5 令牌化视觉验收通过：`bg-dialog-header-wash` 头部柔光、`shadow-dialog` 重投影、emerald/info/slate/amber 四 tone 卡、primary 实色确认按钮；预览真实数据（保留 189/待清理 7/扫描 794） |

## 2. 走查发现并补修的功能缺口：`/api/metrics` 代理路由缺失

- **现象**：质量管理页所有指标显示"—"并提示「指标端点暂不可用」。
- **根因**：`QualityPage.tsx:79` 以相对路径 `fetch("/api/metrics")`，请求落在 Next 源，
  而 `app/app/api/metrics/route.ts` 代理**从未存在**（git 历史为空）→ 浏览器端 404。
  e2e（`quality-management.spec.ts:111`、`workbench-overview.spec.ts:94`）用
  `page.route` mock 了该路径，因此自动化全绿却测不出真实环境断链。
- **修复**：新建 `app/app/api/metrics/route.ts`，照 workflow 代理既有三段式
  （`requireBackendAuthHeaders` 注入 X-API-Key + X-Session-Token；后端
  `authorize_metrics_request` 支持 scrape_token/admin_session）；透传查询串支持
  `format=prom`；超时显式放宽 30s（首次全量采集 797 任务需扫描全部 status.json）。
- **验证**：浏览器内指标全部显示、banner 消失；`?format=prom` 正常透传且同时暴露
  `govbudget_evidence_completeness_rate 0.3149` 与 B1 新增
  `govbudget_evidence_locatable_completeness_rate 1.0`；lint/build/18 个前端单测全过，
  build 输出确认 `ƒ /api/metrics` 已注册。

## 3. B1 的真实数据验证（API 级）

- 4 份真实 PDF 走完整分析链路（上传→规则分析→终态）：3 份决算 + 1 份预算转
  review_required（质量门禁真实拦截）。新任务 `status.json` 的
  `result.meta.evidence_completeness` 均含 B1 新字段
  （`locatable_total/locatable_complete/locatable_completeness_rate/document_level_total`），
  且 `locatable + document_level == total` 恒成立。
- 793 个真实历史任务全量回放门禁（`check_replay_thresholds.py --uploads uploads`）：
  输出「完整率 0.9194 低于 0.99（**可定位类 645 条（文档级单列 2884 条）**）」——
  旧口径会被 2884 条 BUD-001 拖到约 0.18，新口径如实呈现真实证据水平；其余 FAIL 项
  （2 组 report_id 冲突、242 个缺覆盖率）与历史记录吻合，属已知历史状态非回归。
- 文档级（BUD-001）触发样本：现行规则版本对 4 份真实样本均不再产生 BUD-001
  finding（历史 12 条来自旧规则版本），live 触发未复现；该分支的证据链 = 同一函数
  完整路径已 4 次实测 + `tests/test_evidence_completeness.py` 3 个新单测 + 管线集成
  测试 + 历史 1209 条 BUD-001 曾由同一函数处理，判定为已覆盖。

## 4. 备忘（非缺陷）

1. 弹窗内历史任务中文文件名显示乱码——本批冒烟用 curl `-F` 上传所致的文件名
   编码残留（curl 以 Latin-1 发送文件名），浏览器页面上传路径正常，非系统问题。
2. IAB 内嵌浏览器里 Playwright 定位点击（button/link）会卡 actionability 命中检测，
   坐标点击正常；真实 Chromium（e2e 137 项）与真人操作无此现象，属测试工具适配项。
3. `data/users.json` 的 admin 密码实为 `admin123`（本次走查实测），建议生产部署前
   修改；本记录留存提示。
4. 走查产生 4 个测试任务（job 前缀 74400ab6/0fd62f8c/22e39e28/71d5f41e/399a7159）
   与 1 条 workflow 确认记录，留在 uploads/ 属真实测试数据，未清理。

## 5. 补验（同日）：「出报告」环节端到端实测

首日冒烟止步于审核工作台，未点导出。本节补齐 导入→解析→分析→**出报告** 全链路：

- 经真实前端代理链路（cookie 会话 → Next `/api/reports/download` 代理 → 后端）
  下载 job `74400ab6`（26 页决算）报告：HTTP 200，`application/pdf`，338,909 B，
  PDF 1.7 共 **26 页**——内容为**标注版 PDF**（原文 + 问题标注），第 25 页标注索引
  「CMM-002 问题索引 #12｜中｜V33-113 #35」与该任务 status.json 的 finding
  （CMM-002@25 等）一一对应。
- UI 接线核实：`ReviewWorkbenchPage.tsx:294` `window.open("/api/reports/download?...")`，
  与实测 URL 一致；e2e `full-flow-review-export.spec.ts`（真实 Chromium）覆盖同一流程。
  IAB 内 `window.open` 弹新标签被测试工具环境拦截（同 Playwright 点击问题，工具适配项）。
- 整改包 ZIP 导出（`/archive`，Task 9）本日未在真实环境点下载，e2e
  `archive-page.spec.ts` 有覆盖——如实标注为「自动化已覆盖、真人实测未做」。

## 6. 补验（同日）：真实 AI 链路实测——失败路径全验证，成功路径被上游通道阻塞

用户提供了 muyuan.do 中转（glm-5.2）。实测结论：**四条通道全部不可用，各有根因**；
系统侧的「AI 全链失败 → 安全降级到纯规则」已被真实验证。

### 6.1 通道连通矩阵

| 通道 | 鉴权/models | chat 调用 | 根因 | 解法 |
|---|---|---|---|---|
| muyuan.do + glm-5.2（用户新给） | ✅ 200，模型列表含 glm-5.2 | ❌ 稳定 500 `Failed to retrieve proxy group`（provider 层报 Invalid token） | **中转站服务端**：token 的代理分组/上游通道配置损坏，非我方问题 | 需中转站后台修复通道或换 token 分组 |
| Google 网关 gemini-2.5-pro（.env 既有） | ✅ | ❌ 429 免费层配额 `limit: 0` | 免费 tier 不含 2.5-pro | 开通计费或换付费中转 |
| Google 网关 gemini-2.5-flash | ✅ | ❌ 400 `User location is not supported` | 地域限制（本机 IP 直连被拒） | 需受支持地区的出口 |
| gmn.chuangzuoli.com + gpt-5.4（CODEX 既有） | — | ❌ 403 authentication | key 失效/过期 | 续费或换 key |

### 6.2 系统侧降级安全网：实测通过 ✅

配置 `AI_MAIN_*=muyuan/glm-5.2` + `use_ai_assist=true` 跑真实任务（极简 1 页件，job
`1f7810a7`）：AI 阶段耗时 20.2s，按回退链 `gemini_main → main → codex_backup →
gemini_locator` 对**两个 AI 子步骤**（全报告审计 + 语义审计）各试一轮全部失败后，
如实落盘 `ai_findings=0`，任务仍 `done`、11 条规则发现完整保留、质量门禁 `done`、
无崩溃——「AI 全灭不伤害规则结论」的安全网设计在真实故障下工作正常。

### 6.3 实测发现的配置坑（重要）

切换 AI 主槽时**只改 `AI_MAIN_*` 不够**：审计子步骤显式使用 `AI_AUDIT_MODEL`（当前
gemini-2.5-pro），定位子步骤用 `AI_LOCATOR_MODEL`——本次 muyuan 收到的是
「No available channel for model **gemini-2.5-pro**」（它只有 glm-5.2 通道）。
完整切换需要同步改 `AI_MAIN_* / AI_AUDIT_MODEL / AI_LOCATOR_MODEL` 三组；
当前 .env 已写入 muyuan 主槽（注释标明原值，回滚即删该段恢复 gemini_main）。

### 6.4 待用户

修复 muyuan.do 中转后台（或提供任一可用通道的 key）后，把 `AI_AUDIT_MODEL/
AI_LOCATOR_MODEL` 一并改为 glm-5.2（或该通道支持的模型），重跑本节即可验证
成功路径；届时重点观察：AI finding 数量与质量、缺证据降级比例、耗时与 token 成本。

## 7. 补验（同日深夜）：真实 AI 成功路径——sensenova/deepseek-v4-flash 跑通，效果与根因全查清

用户随后提供了 `https://token.sensenova.cn/v1`（deepseek-v4-flash）。**该通道完全可用**，
端到端成功跑通，并定位出「AI 报 0 问题」的完整根因链。

### 7.1 配置（已写入 .env，可回滚）

`AI_MAIN_*` 与 `AI_LOCATOR_*` 指向 sensenova（deepseek-v4-flash），`AI_AUDIT_MODEL=
deepseek-v4-flash`（吸取 §6.3 教训三处同步），另加 `AI_FALLBACK_CHAIN=main` 收窄
回退链。原 muyuan 配置注释保留。

### 7.2 端到端实测

| 任务 | 材料 | AI 阶段 | 结果 |
|---|---|---|---|
| `80590762` | 1 页极简件 | 10.1s | 全报告审计真实调用了 deepseek，返回「无问题」（符合预期）；规则 11 条保留，done |
| `63cf9837` | 26 页测试问题版（规则基线 68 条） | 58.3s | 全报告审计 + 语义审计两次真实调用均返回 0 条；规则 68 条完整保留，done |

流水线对 AI 的编排（窗口 12000 字符、并发审计、Phase1 先发布规则结果等 AI）全程正常；
本地语义抽取服务（`AI_EXTRACTOR_URL` 127.0.0.1:9009）未部署 → 502 → 自动回退直连 LLM，
该回退链真实生效。

### 7.3 「AI 报 0」根因链（逐层排除实验确认）

1. **推理模型吃光输出预算（管线硬伤）**：`_direct_semantic_audit` 写死 `max_tokens=3200`。
   deepseek-v4-flash 是推理模型，一次 12000 字符窗口审计 `completion_tokens=3200`
   **全部是 reasoning_tokens，可见输出为空** → 被当作「无问题」。glm-5.2 更重（16k
   预算 15101 reasoning 仍未完成思考）。**任何推理模型配 3200 上限必然静默返回空**。
2. **`thinking:{"type":"disabled"}` 参数有效（GLM 风格）**：加该参数后 deepseek
   reasoning 归零，16.2s 返回 5629 字符实质输出。`enable_thinking:false`（Qwen 风格）
   无效；glm-5.2 带 thinking 参数报 400（该中转仅部分模型支持）。
3. **关思考后模型输出合法 JSON，但在该材料上仍报 `[]`**——两层原因：
   - **输入表征缺口（架构级，最关键）**：实测已知问题引文 `60.41`、`增加17.62`、
     `43269.3` 等**都不在 PyMuPDF 纯文本窗口里**（26 页只抽出 10760 字符，表格数字
     大量丢失）——这份材料的问题主要在表格勾稽，AI 在纯文本输入下天然看不见；
     `。。`（CMM-002 文字类）在文本中，但 flash 档模型未报。
   - **flash 档模型行为**：sensenova-6.8-flash-lite 会输出「核对一致」的 info 级条目
     （expected==actual、difference=0.00 也当条目报），且 JSON 偶有语法问题导致
     `_extract_json_array` 解析失败——提示词需明确「只报问题不报核对一致」，
     解析器需容忍对象包裹。

### 7.4 效果评估结论与修复建议

**当前真实效果**：通道与编排全通；在表格型决算材料上 AI 审计**没有增益**（0 发现），
根因排序为 输入表征 > 输出预算/思考参数 > 模型档位。修复建议按性价比排序：

1. **管线小修（建议尽快，半天）**：`AI_AUDIT_MAX_TOKENS` 环境变量化（默认提到
   8000–16000）+ provider 层透传 `thinking` 参数（env 开关，推理模型关思考）。
2. **输入增强（决定 AI 上限，中期）**：给审计窗口喂结构化表格数据——候选方案
   pdf-inspector 的 markdown 表格提取（本轮已装、53 份真实 PDF 已验证 text_based），
   或把规则引擎已有的结构化事实（fiscal_fact_materializer 产物）一并入提示词。
   纯文本窗口下表格勾稽类问题对 AI 物理不可见，这条不解决，换任何模型都无效。
3. **模型档位**：审计用非推理或轻推理模型 + thinking disabled；deepseek-v4-flash/
   glm-5.2 推理模式在当前输出预算与延迟预算下不可用。

本次实测消耗：约 10 万 token 量级（多数烧在推理实验），后续每次窗口审计（关思考）
约 7k prompt + 2.4k completion。
