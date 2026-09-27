# GovBudgetChecker 系统审计报告

**审计日期**：2026-09-27
**审计基线**：`fix/obligation-performance-phase-amount @ a5d52a9`（工作树干净，仅有未跟踪的 `docs/WP4-I_BENCHMARK_PLAN.md`）
**审计方式**：代码通读 + 本机实跑（pytest / ruff / mypy / Next.js build / 后端起服务 / 3 份真实政府 PDF 端到端 / 扫描件模拟）
**审计约束**：全程未修改任何代码；下文每条结论均给出文件路径与行号或可复现的运行输出。

---

## 0. 结论先行

GovBudgetChecker 已经是一个**工程质量相当高、但业务能力覆盖严重不均衡**的系统。

它最强的地方不是规则数量，而是**诚实性基础设施**：质量门禁（`api/main.py:460-760`，15 类 review 原因）、检查义务台账（`src/engine/check_obligations.py`，55 项义务 `obligations-v9`）、证据完整性校验（`src/services/evidence_guard.py`）、AI 执行状态机（`src/services/ai_execution.py`）。本次审计中，系统在两份真实样张上都没有谎报"检查通过"——AI 全部失败、13/7 条规则取数不足，两次都正确落到 `review_required`。这是很多同类产品做不到的。

它最弱的地方是**三处结构性空洞**：

1. **没有 OCR**。`requirements.txt` 里没有任何 OCR 依赖（`pypdf` / `PyMuPDF` / `pdfplumber` 三件套）。实测把真实决算 PDF 转成纯图像 PDF 后，抽出字符数为 **0**，系统仍输出 **12 条 finding，其中 10 条是 `error` 级的"缺失表 ×9"和"页数过少"**。中国区县级财政公开材料中扫描件占比不低，这条路径目前是"能进门但给不出结论还会倒扣分"。
2. **预算文种的规则面只有决算的 38%**。`src/engine/pipeline.py:65-71` 按文种二选一：预算跑 `ALL_BUDGET_RULES`(17) + `ALL_COMMON_RULES`(8) = **25 条**；决算跑 `ALL_RULES`(57) + 8 = **65 条**。`rules_v33.py` 里那 57 条表内勾稽 / 表间勾稽 / 表↔说明一致性规则，**预算材料一条都跑不到**。
3. **"应收材料基线"没有任何数据来源**。`material_slots.due_at`（`src/db/migrations.py:968`）在全部代码中**只有读、没有写**。前端 `app/app/components/materials/UnitTimelinePage.tsx:284` 自己写着"应收材料基线尚未导入"。这意味着"哪些单位材料缺失"这个财政部门最核心的问题，系统结构上能表达、实际上答不了。

还有一个被文档掩盖的事实：**Golden Corpus 只有 1 份样本**（`corpus/DOC-20260905-001/`，普陀区生态环境局 2025 决算）。`docs/CI_BUSINESS_GATE.md` 与 CI 注释都诚实写了"无 Golden Corpus，本门禁只覆盖结构性指标，不度量召回率与精确率"。所以目前**没有任何自动化手段能证明规则准不准**，全部精度结论来自人工抽检。

---

# 第一阶段：系统架构地图（以代码为准）

## 1.1 仓库真实构成

`git ls-files` 统计（已跟踪文件）：

| 目录 | 文件数 | 角色 |
|---|---|---|
| `src/` | 103 | 引擎 + 服务 + 数据访问（真正的主体） |
| `app/` | 283 | Next.js 前端（含 23 个前端单测文件） |
| `tests/` | 145 | pytest |
| `e2e/` | 33 | Playwright |
| `docs/` | 162 | 设计/整改/验收文档 |
| `scripts/` | 38 | 运维与评测脚本 |
| `api/` | 27 | FastAPI 路由与运行时 |
| `rules/` | 3 | 规则 YAML + 扩展加载器 |
| `corpus/` | 2 | Golden Corpus（**仅 1 份样本**） |

**陷阱提示**：仓库根目录的 `engine/`、`services/`、`providers/`、`schemas/` **未被 git 跟踪，且磁盘上只剩 `__pycache__`**——是 `SRC_LAYOUT_MIGRATION` 之后的历史残留。任何工具按目录名推断架构都会读错，真实代码全在 `src/` 下同名子目录里。

## 1.2 技术栈（实测自依赖清单与代码）

- **后端**：FastAPI 0.136.3 + uvicorn 0.35.0 + asyncpg 0.29 + PostgreSQL 16
- **PDF**：`pdfplumber` / `PyMuPDF` / `pypdf`（`requirements.txt`）——**无 OCR、无版面模型**
- **前端**：Next.js（App Router）+ Tailwind，`app/app/(workspace)/` 下 14 个工作台页面
- **AI**：`openai>=1.0.0` SDK + 自研 provider 层（`src/providers/`，zhipu / doubao / openai_compat）+ 多 provider 灾备与熔断（`src/services/ai_client.py`）
- **质量门禁**：ruff（E/F/B/I）、mypy、pytest、Playwright，全部进 CI（`.github/workflows/ci.yml`，13 个 step）

## 1.3 真实任务流程（代码级，非文档级）

```
上传 POST /api/documents/upload
  └─ pdf_selection / preflight（页数、年度、文种、组织预识别）
      └─ 落 uploads/<job_uuid>/ + fiscal_documents / fiscal_document_versions + material_slots
  ↓
POST /api/analyze/{job_id} → 写 status.json → 入队
  ↓
worker / 内联执行 api/main.py:1024 `_run_pipeline_body`
  ├─ ① 解析     pdfplumber 逐页取可见文本 + extract_tables      [api/main.py:1136]
  │             （生产默认走 pdf_parse_in_process 子进程隔离 + 超时/内存上限）
  ├─ ② 覆盖率   _assess_page_extraction：page_coverage / 扫描页检测 [api/main.py:363]
  ├─ ③ 画像     resolve_document_profile（唯一文种判定源）       [src/services/document_profile_resolver.py]
  ├─ ④ 建模     build_document → Document(page_texts, page_tables) [src/engine/rules_v33.py:170]
  ├─ ⑤ 检查
  │     ├─ legacy（默认）：build_issues_payload
  │     │     → 按文种选规则集 → 逐条 apply → order_and_number_issues
  │     │     [src/engine/pipeline.py:74-178]
  │     └─ dual：DualModeAnalyzer.analyze
  │           ├─ engine 侧同上（engine_rule_runner）
  │           └─ AI 侧 AIFindingsService：分窗（12000 字符 × 最多 12 窗）
  │              全文 + build_structured_context 注入 → provider 调用
  │              [src/services/ai_findings.py:38-126]
  ├─ ⑥ 结构化入库 run_structured_ingest（另一条抽取路径，TableRecognizer → fact_fiscal_line_items）
  │                                                    [src/services/structured_ingest_runner.py]
  ├─ ⑦ 证据门   apply_evidence_completeness（缺证据的 AI finding 降级为待复核）[src/services/evidence_guard.py]
  ├─ ⑧ 质量门   _evaluate_quality_gate（15 类 review 原因，fail-closed）  [api/main.py:460]
  └─ ⑨ 落库     analysis_jobs / analysis_results / analysis_result_store
  ↓
GET /api/jobs/{id}/status → review_required | done
  ↓
导出 /api/reports/download?format=json|csv|pdf|docx
  ↓
人工复核 /api/reviews/{slot_id}/start|complete|reopen + obligations/{id}
```

**流程中被文档掩盖的两点**：

1. **第 ⑤ 步和第 ⑥ 步用的是两条互不相通的抽取路径。** 规则引擎消费的是 `page_texts`（纯文本行回退解析），结构化入库消费的是 `TableRecognizer`。实测同一份 31 页决算：legacy 路径 `pdfplumber.extract_tables()` 只在 14 页上各找到 1 张表、其余 17 页为 0；结构化路径则识别出 17 张表中的 9 张。**规则拿不到结构化事实**，这直接导致大量规则落到 `insufficient_data`。
2. **默认模式是 `legacy`（不跑 AI）**（`api/main.py:1037`）。`use_ai_assist` 只有 `mode=="dual"` 且 `config/app.yaml:dual_mode.enabled` 时才为真。

## 1.4 数据库核心实体（`src/db/migrations.py`，22 个迁移）

| 表 | 角色 |
|---|---|
| `material_slots` | **产品核心对象**。身份 = (主体组织, 主体层级, 材料口径, 文种, 财政年度, mapping_key)，sha256 归一；有 10 态状态机（not_due/missing/uploaded/processing/review_required/reviewing/completed/not_applicable/mapping_required/failed）与独立的 `status_reason` |
| `material_sources` | 来源记录；**发布日期与财政年度严格分离**（设计正确，避免 2024 年度决算 2025 年发布被误判） |
| `fiscal_documents` / `fiscal_document_versions` | 文档与版本，版本指针唯一放在 slot 上 |
| `org_department` / `org_unit` | 部门/单位，18 部门 + 31 单位（实测本机库） |
| `fact_fiscal_line_items` | 结构化财政事实（实测本机 5143 行） |
| `review_sessions` / `review_obligation_decisions` | 复核会话与逐义务人工决定 |
| `workflow_issue_records` / `workflow_remediation_packages` | 问题工作流与整改包 |
| `qc_runs_v2` / `qc_findings_v2` | **实测 0 行——见 §2.3 的死代码说明** |

## 1.5 前端页面（`app/app/(workspace)/`，全部已构建通过）

工作台总览 / 上传中心 / 处理队列 / 审核工作台 / 任务历史 / 导出归档 / 质量管理 / 规则与版本 / 系统设置 / 材料台账（4 个子页）/ 材料详情（5 Tab）/ 全局检索 / 登录 / 改密 / 管理员（组织、用户、系统配置、分析结果）。

---

# 第二阶段：功能完成度审计

## 2.A 材料管理

| 能力 | 状态 | 依据 |
|---|---|---|
| 区县管理 | **部分完成** | `material_slots.jurisdiction_org_id` + `/api/materials/districts/{id}/departments`；但本机 `organizations` 表仅 1 行，真实多区数据未灌 |
| 部门管理 | **完成** | `org_department` + `/api/materials/departments/{id}/matrix` |
| 年度管理 | **完成** | `fiscal_year` 可空 + `COALESCE(fiscal_year,-1)` 表达式唯一索引（`migrations.py` 0017/0019），"年份未知"不被迫写 2000 |
| 预算/决算区分 | **完成** | `report_kind` CHECK 约束（budget/final/unknown），唯一解析器 `document_profile_resolver` |
| 本部/直属单位 | **完成** | `material_scope` + `caliber`（summary/self/unknown）+ `caliber_conflict_candidate` 显式记录口径矛盾 |
| 上传管理 | **完成** | 批量上传、preflight、流式分块、校验和、PDF 解析子进程隔离 |
| 版本管理 | **完成** | `fiscal_document_versions` + slot 单一当前版本指针 + "非最新版本跳过结构化入库"（`api/main.py:1601`） |
| **材料缺失提醒** | **结构有、数据没有** | `due_at` 列存在、状态机有 `missing`（逾期未上传）、查询层有 `due_at_unknown` 桶——**但全代码库无任何写入 `due_at` 的语句** |
| 材料状态 | **完成** | 10 态 + 原因码，UI 有独立呈现（`app/lib/materialStatusPresentation.ts`） |

**A 段小结**：材料台账的**数据模型质量高于同类产品**（口径矛盾显式化、未知不折叠成已知、截止时间未知单独可见）。但"应收清单"这一输入端完全没有——台账只能回答"收到了什么"，回答不了"该收什么"。

## 2.B PDF 解析能力

| 能力 | 状态 | 依据 |
|---|---|---|
| PDF 文本提取 | **完成** | pdfplumber + PyMuPDF 可见字符过滤（`api/main.py:325` `_is_visible_char`） |
| **扫描 PDF OCR** | **不存在** | `requirements.txt` 无任何 OCR 依赖；`api/main.py:369` 注释"本轮不做自动 OCR：这里只负责检测 + 算覆盖率"；`src/services/pipeline_stages.py:22-24` 明确"OCR 阶段本轮不存在，枚举中不得出现" |
| 表格识别 | **部分完成** | 两条路径质量差异大（见 §1.3）；legacy 路径实测 31 页只覆盖 14 页 |
| 页码定位 | **完成** | `utils/issue_bbox.py`、`issue_location.py`；实测 evidence 完整率 100%（3/3、7/7） |
| 原文引用 | **完成** | 每条 finding 带 `evidence[].text_snippet` |
| 图片处理 | **不适用** | 无 |
| 扫描件质量检测 | **完成** | `SCANNED_PAGE_MIN_CHARS=50` + `page_coverage` + 门禁 `low_page_coverage` / `scanned_pages_detected` |

### 实测：系统面对真实政府公开 PDF 能否稳定处理？

| 输入类型 | 实测结果 | 判定 |
|---|---|---|
| 纯文本决算（31 页，官方样张） | 覆盖率 96.8%，52/65 规则执行，7 条 finding，证据完整率 100% | **可用但覆盖不足** |
| 纯文本预算（30 页，真实街道办） | 覆盖率 96.7%，18/25 规则执行，3 条 finding | **可用但规则面过窄** |
| **纯图像扫描件（6 页，由真决算转制）** | **抽出 0 字符、覆盖率 0.0、48/65 规则 insufficient_data、12 条 finding（10 条 error 级：缺失表×9 + 页数过少）** | **失败** |

失败样件虽被质量门拦到 `review_required`（不会谎报通过），但**12 条高危误报会原样呈现在复核工作台的 issue 列表里**（前端对 `review_required` 的处理是打"完成（需人工复核）"角标，见 `app/lib/materialDetailPresentation.ts:269`，不隐藏 finding）。对财政人员而言这是最伤信任的失败模式。

## 2.C 财政业务检查能力

### 规则规模（实测枚举 `ALL_RULES` / `ALL_BUDGET_RULES` / `ALL_COMMON_RULES`）

| 注册表 | 条数 | 适用文种 |
|---|---|---|
| `rules_v33.ALL_RULES` | 57 | **仅决算** |
| `budget_rules.ALL_BUDGET_RULES` | 17 | 仅预算 |
| `common_rules.ALL_COMMON_RULES` | 8 | 通用 |
| **合计注册** | **82** | — |
| **单份决算实际执行集** | **65** | — |
| **单份预算实际执行集** | **25** | — |

### 按用户五类检查逐项对照

| 检查类别 | 已实现 | 缺口 |
|---|---|---|
| **1. 公开及时性** | **0 条** | 无截止时间数据源（`due_at` 无写入）；无发布日 vs 财政年度比对；`rules/v3_3.yaml:timeliness` 是**从未被执行的声明式配置** |
| **2. 公开完整性** | 决算：九表齐全性 `V33-002`、空表说明 `V33-109/114/122`、三公四项细化 `V33-SG-COMPLETION`、绩效阶段金额 `V33-PERF-PHASE-AMOUNT`（预算）。**预算：仅 25 条，无同等细度的完整性检查族** | 政府采购预算完整性、绩效目标表完整性、转移支付披露完整性、国有资产披露——均无独立规则 |
| **3. 格式规范** | 占位符 `V33-112`/`BUD-002`、标点 `V33-113`、百分比精度 `V33-232`、文种口径用语 `V33-236`/`BUD-113`、目录条目数 `CMM-003` | 统一社会信用代码、机构名称规范、文号规范、附件命名规范 |
| **4. 数据逻辑（勾稽）** | 决算侧较完整：`V33-101~105`（总表恒等式 + 表↔说明）、`V33-200~204`（表间）、`V33-240~246`（表内加强）、`V33-233`（明细行公式）、`V33-CROSS-SAN-GONG-ECON`（三公×经济分类跨表） | **预算侧只有 `BUD-101~105` 五个粗粒度规则**；转移支付上下级加总、政府采购分项、经济分类科目编码校验（`R002`）无实现 |
| **5. 文字规范** | 同比逻辑 `CMM-005`、零基数复算 `CMM-007`、重复指标 `V33-NARRATIVE-INDICATOR-REPEAT`、叙述百分比 `V33-234`、叙述金额 `V33-235`、增减原因缺失（`V33-245` 方向矛盾） | 模板缺项（对照最新版《预决算公开操作规程》的必填章节清单）无规则；单位名称错误无规则；年度错用（正文 vs 封面）仅 `BUD-003` 覆盖预算 |

### 关键发现：`rules/v3_3.yaml` 的 C-001~C-005 / D-001~D-009 从未执行

`rules/v3_3.yaml` 定义了 C-001~C-005（预算必查勾稽）与 D-001~D-009（决算必查勾稽），包含用户 AGENTS.md 里点名的"三公经费合计 = 三项之和""收支总表平衡"等。**但这个 YAML 在生产路径上只被三处消费**：

1. `api/routes/health.py:135` — `/ready` 检查文件存在
2. `api/routes/rules.py` — `/api/rules/entries` 展示
3. `analyze_dual._load_rules` → `rules/loader_ext.py` — 仅用于把规则**分流**成 `ai_rules` / `engine_rules`

而该 YAML 中 `executor: ai` **只出现 1 次**（`rules/v3_3.yaml:849`）。所以：legacy 模式下这些 YAML 规则不产生任何 finding；dual 模式下它们也不产生 finding——只被用来决定"要不要跑 AI"。

**实际执行勾稽的是 `src/engine/rules_v33.py`（9817 行 Python 硬编码）。** 这意味着 YAML 规则集目前是**展示用的目录，不是可执行规则库**——规则要上线必须改 Python。这是规则库建设的根本性架构约束（见第四阶段）。

## 2.D AI 智能审查能力

**当前定性：这是一个"规则系统 + 一条已接好但未验证的 AI 辅助链路"，不是智能审查系统。**

| 维度 | 状态 | 依据 |
|---|---|---|
| LLM 接入 | **代码完整、生产未跑通** | `src/providers/` 三家 provider + `ai_client.py` 熔断/灾备/令牌账本齐备；**实测两次均失败**：`Cannot connect to host gmn.chuangzuoli.com:443`（.env 配的 host 不可达）与 `no_providers_available (all circuit breakers open)` |
| Embedding | **不存在** | 全库无 embedding/向量库依赖（`grep embedding\|faiss\|chroma\|milvus\|qdrant\|pinecone` 仅命中一处中文注释） |
| 知识库 | **不存在** | 无文档库、无范例库、无法规库结构 |
| RAG | **不存在** | AI 输入只有分窗原文 + `build_structured_context` 的表格摘要，无检索环节 |
| Prompt 管理 | **弱** | prompt 硬编码在 `ai_findings.py` 的 f-string 里；`prompt_version` 实测为 `null`；无 prompt 仓库、无 A/B、无版本回滚 |
| 审查结果引用依据 | **有** | AI finding 走同一套 `evidence_guard`，实测完整率 100% |
| 幻觉控制 | **有机制、未验证** | 窗口上限显式告警（`audit_window_limit_reached`）、证据门禁、无证据降级为 `manual_review`、AI 未执行禁止呈现为已执行（`ai_execution.py`）。**但这些机制全是在 AI 一次都没成功跑过的前提下验证的** |
| 人工确认机制 | **完成** | 复核生命周期 start/complete/reopen + 55 项义务逐条决定（`obligations` PUT）+ 完成门禁（`CompletionGate`）+ CAS 乐观锁 + 会话绑定 |

**AI 链路的隐藏风险**：`AIFindingsService` 窗口为 12000 字符 × 最多 12 窗 = **14.4 万字符上限**。当前两份样张（1.7 万字）远未触及，但一份 150 页以上的完整部门决算会静默截断后半部分——代码有告警，但**没有把截断升级为阻断**。

**另有两处会污染 AI 判断的遗留**（均不在生产路径，但会误导后续开发）：
- `src/services/ai_rule_runner.py` 是**纯 mock**：注释"模拟AI检测结果"，为任何含"预算/决算"字样的页面前三页伪造 `AI-BUDGET-00X` 问题。**全库无调用者**。
- `src/services/ai_findings.py:406 _build_prompt` 依赖 `context.ocr_text`（生产恒为 None，会渲染成"无OCR文本"）。**无调用者**，是死代码。

## 2.E 问题生命周期

| 阶段 | 状态 | 依据 |
|---|---|---|
| 发现 | **完成** | 规则/AI 双通道，统一 `IssueItem` |
| 问题编号 | **完成** | `order_and_number_issues` + `id = {rule}-{idx}` |
| 人工复核 | **完成** | `/api/reviews/{slot_id}/start` + 逐义务决定（`review_obligation_decisions`，真库 CAS 测试在 CI 硬门禁内） |
| 修改意见 | **完成** | `IssueNoteDialog` + `issue_workflow_store.note` |
| 整改状态 | **部分完成** | `workflow_issue_records.status ∈ {pending, confirmed, no_issue, needs_review, in_package}`——**没有"整改中/已整改/待验证/已关闭"**；`workflow_remediation_packages` 有独立 status 但语义未与问题状态联动 |
| 关闭 | **部分完成** | 复核层面有 `complete`/`reopen`；**整改层面没有真正的闭环回执**——材料重新上传后，系统不会验证上一轮问题是否已消除 |

**WP3 结论**：WP3-A（复核生命周期）与 WP3-B（人工补核）**代码与测试都扎实**（真库并发/CAS/回滚链测试在 CI 里跑，0 skipped 硬门禁）。缺的是 WP3-C：**整改闭环**。

## 2.F 用户实际工作流检查

> 场景：上海某区财政部门工作人员上传 2025 年度部门预算公开文件，希望 1 分钟内知道：哪些单位材料缺失 / 哪些文件有问题 / 问题在哪里 / 依据是什么 / 如何整改。

**实测结果（后端起真服务，真实 30 页街道办预算 PDF）：**

| 用户想要 | 系统实际 | 判定 |
|---|---|---|
| 哪些单位材料缺失 | `/api/materials/coverage` 返回 `expected_total: null`（应收基线为空） | **做不到** |
| 哪些文件存在问题 | 3 条 finding + `review_required` 状态 | **部分做到**（且 3 条只覆盖 25 条规则里的 18 条） |
| 问题在哪里 | 页码 + 章节 + 原文片段，实测证据完整率 100% | **做到** |
| 依据是什么 | `evidence[].text_snippet` + `rule_id` + `suggestion` | **做到** |
| 如何整改 | 每条带 `suggestion`，可导出 CSV/Word 整改通知 | **形式上做到，实质是通用话术**（`utils/rule_text.py` 的 `default_rule_suggestion`，非针对本材料的整改建议） |
| 1 分钟内 | 单份 30 页实测 **15~25 秒**（规则引擎本身仅 328ms，其余是解析+结构化入库+AI 超时等待） | **时间达标** |

**核心矛盾**：这份预算材料触发了 7 条 `insufficient_data`。系统诚实地把它标成 `review_required` 并在 `check_coverage` 里列出未覆盖的检查项——这是对的。但**当前产品里没有"未覆盖检查项"这一级业务对象**，财政人员看到的是"完成（需人工复核）"和一个 68% 的覆盖率数字，而不是"这份材料的哪几项检查根本没做成、为什么没做成、该找谁补"。

---

# 第三阶段：真实能力压力测试方案设计

现有语料的致命缺陷：`corpus/` **只有 1 份样本**；`samples/manifest.yaml` 声称有 5 类样本，但其中 2 个文件不存在（`上海市XX局_2023决算公开_支出勾稽错误.pdf`、`政府性基金_口径不一致_示例.pdf`），引用的 3 个规则 ID（`GJ-001`/`TB-003`/`ZB-002`）**在代码中根本不存在**。而 `tests/test_samples.py`（4 passed）**只校验 manifest 的字段形状，不校验文件存在、也不校验规则是否真的触发**——这是一个"看起来有回归语料、实际是空壳"的假门禁。

## 3.1 测试材料清单（20 份，全部需真实获取并入库）

### A 组：预算公开（10 份）

| 编号 | 类型 | 构造方式 | 期望系统行为 |
|---|---|---|---|
| B-01 | 标准文本·部门预算 | 真实公开 PDF（街道/委办局） | 25 条规则全执行，coverage_rate ≥ 0.9 |
| B-02 | 标准文本·单位预算（本部） | 真实 PDF | 正确判定 `caliber=self`，不与部门汇总串线 |
| B-03 | 纯扫描件·部门预算 | 由 B-01 转 150dpi 图像 | **必须** `review_required` + **0 条 `error` 级 finding**；当前实测会产出 10 条 → 预期失败 |
| B-04 | 表格复杂·横向双栏 | 真实经济分类宽表 | 不发生列错位；`insufficient_data` 而非误判 |
| B-05 | 缺表·缺"三公"表 | 删页 | 命中空表说明规则；不得报"页数过少"以外噪声 |
| B-06 | 缺表·缺绩效目标表 | 删页 | 命中完整性规则 |
| B-07 | 年份错误·封面 2024/正文 2025 | 改封面 | `BUD-003` 命中且页码正确 |
| B-08 | 数字错误·合计≠分项和 | 改一个数字 | 命中公式规则，差额与页码精确 |
| B-09 | 数字错误·三公≠三项和 | 改合计 | 命中三公勾稽（当前**预算侧无此规则**，预期失败） |
| B-10 | 同比错误·零基数写"完成 X%" | 改叙述 | `CMM-007` 命中 |

### B 组：决算公开（10 份）

| 编号 | 类型 | 期望 |
|---|---|---|
| F-01 | 标准文本·部门决算（=现有 golden） | 65 条规则，coverage ≥ 0.9 |
| F-02 | 纯扫描件·部门决算 | `review_required` + 0 条 error 级误报（当前实测失败） |
| F-03 | 表格复杂·双栏经济分类 | `V33-CROSS-SAN-GONG-ECON` 正常执行 |
| F-04 | 缺页·缺收入决算表 | 命中缺表规则 + 人工确认 |
| F-05 | 缺页·缺政府性基金表 | 条件性表：判 `not_applicable` 而非缺失 |
| F-06 | 年份错误·目录"202 年度" | `V33-001` 命中（golden 已标注 T1） |
| F-07 | 数字错误·收支总表不平衡 | `V33-214` 命中（当前该规则在此样张 `insufficient_data`，预期失败） |
| F-08 | 数字错误·明细行≠小计 | `V33-233` 命中 |
| F-09 | 文字错误·三公"减少为0与上年持平" | `V33-245` 命中（golden T5） |
| F-10 | 跨表口径差 0.01 | 输出 `info` 而非 `error`（golden T2/T3） |

## 3.2 评价标准（每份材料五个维度，全部机器可判）

| 维度 | 指标 | 通过线 | 当前可测性 |
|---|---|---|---|
| **① 解析完整性** | `page_coverage`、`scanned_page_count`、`recognized_tables` | ≥ 0.95 / 0 / ≥ 80% | ✅ `structured_ingest` 已输出 |
| **② 规则覆盖率** | `check_coverage.coverage_rate` | ≥ 0.90 | ✅ 已有 |
| **③ 精确率** | 人工标注 finding 中判定为误报的比例 | ≤ 5% | ⚠️ 需人工标注，无自动手段 |
| **④ 召回率** | 人工标注真值中被命中的比例 | ≥ 80% | ⚠️ 仅 1 份样张有 7 条标注 |
| **⑤ 诚实性** | 终态与实际执行情况是否一致 | 100% 一致 | ✅ 质量门已覆盖 |
| **⑥ 扫描件安全** | 纯图像件上的 `error` 级 finding 数 | = 0 | ❌ **当前实测 10 条，此项是当前最痛的失败模式** |

## 3.3 执行方式

新增 `scripts/benchmark_suite.py`，复用现有 `scripts/replay_golden_corpus.py` + `scripts/evaluate_golden_corpus.py`（**不改语义**，只扩语料与指标），输出 `outputs/benchmark_<date>.json`，并在 CI 加一条**不阻塞**的报告任务。理由：先建立可观测的事实基线，再谈改规则——当前 82 条规则的精度**没有任何自动化证据**。

---

# 第四阶段：距离商业化缺失清单

| 编号 | 缺失能力 | 影响 | 严重程度 | 建议方案 |
|---|---|---|---|---|
| 1 | **OCR 完全缺失**，扫描件抽出 0 字符且倒扣 10 条 error 误报 | 区县级扫描件材料完全不可用；误报直接摧毁用户信任 | **致命** | 引入 PaddleOCR（离线可私有化，符合政务内网要求）；扫描页走独立分支：只做"页存在性"检查，禁止产出 error 级 finding |
| 2 | **应收材料基线无数据源**（`due_at` 无写入方） | "哪些单位没交"是财政部门第一诉求，系统结构上答不了 | **致命** | 建"应公开清单"导入（Excel/官网栏目抓取），落 `due_at` + `expected_source_url`；补公开时限规则（预算法第 22 条：批复后 20 日/决算后 10 日） |
| 3 | **预算文种规则面只有决算的 38%** | 预算材料是区级财政一半的工作量，规则覆盖不足一半 | **高** | 把 `rules_v33` 的表内/表间/表↔说明族按预算表结构移植，或抽象出表语义层让两文种共用 |
| 4 | **YAML 规则集不可执行**，`rules/v3_3.yaml` 只是展示目录 | 规则库建设无法靠配置完成；非开发人员无法加规则 | **高** | 规则 DSL 化：YAML 定义 + Python 执行器注册表；`C-00x`/`D-00x` 变成真规则而非文档 |
| 5 | **Golden Corpus 仅 1 份**，无召回率/精确率自动化 | 任何规则改动都无法证明是变好还是变坏 | **高** | 按第三阶段扩到 20 份；把精确率/召回率进 CI（先报告后门禁） |
| 6 | **AI 链路从未成功执行过**（两次实测均失败，provider host 不可达） | "AI 审查"目前是产品宣传而非能力 | **高** | 修 provider 配置并做一次真实成功运行；在此之前**产品内不得出现"AI 辅助"字样** |
| 7 | **无 OCR/无 RAG/无知识库，prompt 硬编码且 `prompt_version=null`** | 无法应对新文种、新口径；无法审计 AI 判据 | **中** | prompt 入库版本化；引入法规/规程知识库做 RAG 引用 |
| 8 | **无整改闭环**：`in_package` 之后无"已整改/待验证/已关闭" | 问题只被发现不闭环，年复一年累积 | **中** | 补整改状态机 + 重传后自动回归验证上一轮问题是否消除 |
| 9 | **"未覆盖检查项"不是一级业务对象** | 财政人员看到"68% 覆盖率"不知该做什么 | **中** | 把 `check_coverage` 升格为复核工作台的一等 Tab，按义务组给出"未覆盖原因 + 补齐动作" |
| 10 | **扫描件误报无抑制** | 即使接了 OCR，混合件（部分扫描页）仍会误报 | **高** | 规则层加"输入可用性"前置判断：页面不可读时该表相关规则一律降级为 `manual_review`，禁止 error |
| 11 | **性能基线只覆盖列表接口**（`docs/PERF_BASELINE.md`），未覆盖分析吞吐 | 无法回答"500 份材料多久跑完" | **中** | 加分析吞吐基线：并发 N worker 下 30 页材料 P50/P95 |
| 12 | **README 的 API 文档已失效**（文档写 `POST /upload`，实际是 `POST /api/documents/upload` + `X-Session-Token`） | 集成方按文档开发必失败 | **中** | README API 段改为从 `openapi.json` 生成 |
| 13 | **死代码与假语料**：`ai_rule_runner.py`(mock)、`job_orchestrator.py`+`qc/runner_v3.py`(无调用者)、`ai_findings._build_prompt`(死方法)、`config/app.yaml: ocr.enabled=true`(无读取方)、`samples/manifest.yaml`(2 文件缺失 + 3 规则号不存在) | 误导后续开发者；`ocr.enabled: true` 是**对外宣称了不存在的能力** | **中** | 删除或明确标注 deprecated；`test_samples.py` 补文件存在性断言 |
| 14 | **多区/多租户无真实数据验证**（`organizations` 1 行） | 跨区数据隔离、权限边界未在真实规模验证 | **中** | 灌入上海 16 区真实组织树后跑一遍 RBAC 与台账聚合 |
| 15 | **缺合规资质视角**：信创适配、等保三级、密码合规、审计留痕期限 | 政务内网销售的准入门槛 | **中**（非技术） | 走等保测评 + 信创适配清单 |

---

# 第五阶段：WP5 之后的开发路线

原则：**不重新设计**。当前 `material_slot` 底座、质量门、检查义务台账、复核生命周期都是可复用的正确资产，后续阶段都在其上加固。

### WP5：解析与规则面补齐（对应缺失 1、3、10）
- **目标**：扫描件可用 + 预算规则面对齐决算
- **内容**：PaddleOCR 接入（含混合件页级路由）；扫描页误报抑制（输入可用性前置判断）；把 `rules_v33` 表内/表间族移植到预算；`V33-214`/`V33-101` 等核心勾稽从 `insufficient_data` 修到可执行
- **验收**：20 份语料中 4 份扫描件全部 `review_required` 且 **0 条 error 级误报**；B-01 覆盖率 ≥ 0.9；F-07（收支总表不平衡）命中

### WP6：应收基线与公开及时性（对应缺失 2）
- **目标**：回答"谁没交、谁超期"
- **内容**：应公开清单导入（Excel/官网栏目）；`due_at` 写入路径；公开时限规则（预算法 20 日 / 决算法 10 日）；台账首页改为"应收-实收-逾期"三视图
- **验收**：任选一个区的完整清单导入后，系统能列出全部逾期未上传单位且与人工核对零差异

### WP7：规则库工程化（对应缺失 4、5）
- **目标**：规则可配置、可回归、可度量
- **内容**：规则 DSL 化（YAML 定义 + 执行器注册表）；Golden Corpus 扩到 20 份；精确率/召回率进 CI；规则版本与回填绑定
- **验收**：新增一条规则**不改 Python 引擎代码**即可上线；CI 报告精确率与召回率，跌破基线阻断合并

### WP8：AI 审查实装（对应缺失 6、7）
- **目标**：让 AI 从"宣传"变成"能力"
- **内容**：修通 provider 并完成首次真实成功运行；prompt 入库版本化（`prompt_version` 不再为 null）；引入预决算公开规程知识库做 RAG；AI 窗口截断升级为阻断
- **验收**：连续 20 份材料 AI 执行成功率 ≥ 95%；AI finding 与规则 finding 的人工裁定一致率 ≥ 85%；prompt 可按版本回放

### WP9：整改闭环与报告交付（对应缺失 8、9）
- **目标**：从"发现问题"到"问题消失"
- **内容**：整改状态机补全；材料重传后自动回归验证上一轮问题；Word/Excel 整改通知书按单位自动成文；未覆盖检查项升格为一等 Tab
- **验收**：一次完整"发现→通知→整改→重传→验证消除"闭环在真库跑通

### WP10：商用交付（对应缺失 11、12、13、14、15）
- **目标**：可卖
- **内容**：性能基线扩到分析吞吐；上海 16 区真实数据压测；README/API 文档重新生成；死代码清理与 `ocr.enabled` 配置修正；等保三级 + 信创适配
- **验收**：500 份材料批量分析的 P95 在可接受范围；等保测评通过；集成方按文档 30 分钟内跑通首次分析

---

# 第六阶段：最终报告

## 6.1 完成度（三口径必须分开说）

| 口径 | 估计 | 依据 |
|---|---|---|
| **代码完成度** | **约 85%** | 70,630 行 Python/TS 业务代码；ruff 全绿、mypy 254 文件 0 issue、pytest 2086 passed、Next.js build exit 0、34 个 E2E spec（217 用例）全部进 CI；CI 含真库 PostgreSQL、迁移幂等、业务门禁 |
| **功能完成度** | **约 55%** | 材料台账/复核闭环/队列/导出/权限完整可用；但 OCR 缺失、预算规则面 38%、应收基线无源、YAML 规则不可执行、AI 未跑通——这些是功能而非代码的空洞 |
| **商用完成度** | **约 30%** | 缺等保与信创、缺 20 份真实语料与精度基线、缺多区真实数据验证、缺整改闭环、缺性能吞吐证据、产品内仍存在对外宣称了不存在能力的配置（`ocr.enabled: true`） |

**请注意这个梯度**：代码完成度 85% 而商用完成度 30%，差距**不在写代码上，而在真实世界语料、合规资质与业务闭环上**。继续投代码不会缩小这个差距。

## 6.2 当前已经能解决的真实问题

1. **上传一份纯文本的部门决算/预算 PDF，30 秒内拿到带页码、原文引用、整改建议的问题清单**——实测证据完整率 100%。
2. **不谎报**：AI 失败、规则取数不足、扫描页、覆盖率低、表缺失歧义，15 类原因全部 fail-closed 到 `review_required`（本次两次实跑均正确触发）。
3. **九张表齐全性、空表说明、三公口径、绩效阶段金额、政府性基金/国资表 × 情况说明逐项一致性**——决算侧有实质覆盖。
4. **材料台账**：多年度、多主体、本部/直属口径、版本、来源、状态与原因码，模型设计质量高。
5. **复核闭环**：逐检查义务的人工决定 + 完成门禁 + 并发 CAS，真库测试在 CI 硬门禁内。
6. **工程纪律**：双层解析隔离、防超日/防超内存、三方环境变量对账、日志泄漏扫描、迁移回滚链验证。

## 6.3 当前无法解决的真实问题

1. 扫描件（预决算公开里占比不低）——**零字符 + 10 条高危误报**。
2. "哪些单位该交没交"——**没有应收清单，无数据源**。
3. 预算材料的深度勾稽——**57 条强规则一条都跑不到**。
4. 规则准确率——**无任何自动化证据**（Golden Corpus 1 份）。
5. 整改闭环——**只到"打包通知"，无"验证消除"**。
6. AI 语义审查——**代码齐备但从未成功执行过一次**。
7. 大批量吞吐——**无分析性能基线**。

## 6.4 距离市场销售版本还需做的工作

按依赖顺序（**必须做 → 应该做 → 以后做**）：

### 必须做（不做不能卖）
1. **接入 OCR 并消除扫描件误报**（缺失 1、10）——这是产品可信度的生死线。
2. **建立应收材料基线**（缺失 2）——不做这个，系统只是"单份文档查错工具"，不是"部门级审校系统"，价值主张完全不同。
3. **补齐预算文种规则面**（缺失 3）——用户一半的工作量目前覆盖不足一半。
4. **语料扩到 20 份 + 精度基线进 CI**（缺失 5）——没有这个，任何后续改动都是盲改。
5. **等保三级 + 信创适配 + 审计留痕合规**（缺失 15）。

### 应该做（决定客户体验与扩展性）
6. **规则库 DSL 化**（缺失 4）——否则"按客户属地定制规则"永远依赖研发。
7. **修通并实装 AI**（缺失 6、7）——在成功跑通前，产品内不应出现"AI 审查"字样。
8. **整改闭环**（缺失 8）。
9. **"未覆盖检查项"升格为一等对象**（缺失 9）——直接决定财政人员是否信任 68% 这个数字。
10. **上海 16 区真实数据压测 + 多区隔离验证**（缺失 14）。
11. **性能吞吐基线**（缺失 11）。

### 以后做（规模化后才需要）
12. 法规知识库 RAG 深化。
13. 跨省属地化扩展（规则按省可配置）。
14. 与预算一体化系统、财政大数据平台的系统对接。
15. 死代码清理与文档重生成（缺失 12、13）——技术债，可在任意迭代顺带清理。

---

## 深度根因分析：为什么"查不准"（2026-09-27 追加）

> 针对用户追问"差距最大在哪里、为什么不能正常找到问题"。以下两条失败链均为实测还原，
> 不是推断。总判断：**最大差距不在规则数量，而在"规则的眼睛"（表格解析层）——
> 同一个底层缺陷同时制造误报和漏报。**

### 根因链 A（误报侧）：文旅局决算「1700.57」级联误报

实测 `tests/fixtures/wenlv_narrative_truth_page_data.json`（2025 决算 33 页冻结页数据）：

1. **真实值**：一般公共预算财政拨款支出决算表（T5）合计 = 24,535.67（写在第 14 页
   财政拨款总表总计行 `总计26,538.47 … 24,535.67 …`）。
2. **pdfplumber 线框提取损坏**：第 16 页表格里 221 / 22102 / 2210201 / 2210203 行的数值
   （17,127.62 / 14,758.06 / 457.86 / 3,070.63）**与第 15 页 207 系列行完全相同**——
   无框线政府表格的单元格被复制错位；行也有丢失（2080503/2080504 缺失）。
3. **规则取数启发式**：`_get_table_rows`（rules_v33.py:2599）= 锚点页 15 + 找该页"最大表"
   + **最多只并 1 页**（注释原话：放开合并会"新暴露 V33-202/V33-222/V33-119 的口径与
   列选择缺陷"，故只有 V33-120 被允许用 full_extent）。
4. **合计行造假**：第 16 页末尾一行 `合计 1,700.57`（实为 2070105 文化展示及纪念机构的
   1,700.57，被错位粘贴）被规则当作 T5 合计；`/|max(vals)|` 的取值假设在此失效。
5. **级联误报**：V33-202（T4 24535.67 vs T5 1700.57）、V33-222（说明 24535.67 vs 1700.57）、
   V33-005（合计 1700.57 vs 分项和 25411.28）三条 error 因同一个错位数字同时炸。

### 根因链 B（漏报侧）：核心勾稽在 4/4 份材料上全部未执行

实测 4 份决算共 **53 条 unresolved**，其中 V33-101/102/103/104/105/107/108（对应
AGENTS.md `D-001`~`D-007` 决算必查勾稽）**每份都缺失**，合计 28 条。以 V33-101 为例：

1. 文旅局"收入支出决算总表"提取 **成功**：30 行，含
   `['本年收入合计','26,538.47','本年支出合计','26,538.47']`、
   `['使用非财政拨款结余','','结余分配','']`、
   `['年初结转和结余','52.61','年末结转和结余','52.61']`。
2. 但 `_row_value`（rules_v33.py:1170）**只在 row[0] 里找行标签**；这份表是左右双栏排版，
   "本年支出合计/结余分配/年末结转和结余"全部在 **row[2]**。
3. 三个行标签全部取不到 → 规则走 RuleDeferred → 记 `insufficient_data` →
   质量门转 `review_required`。**恒等式从头到尾都没算过。**

> 关键点：规则"诚实拒答"是对的（没有造假通过），但它让核心勾稽在真实材料上
> 等于不存在。`_split_two_sided_row`（同文件已有）能拆双栏，V33-101~108 没用它。

### 账总览

| 面 | 数据 | 性质 |
|---|---|---|
| 漏报 | 决算 4 份共 53 条 unresolved；`D-001`~`D-007` 每份 7 条全缺（28 条） | **核心勾稽全灭** |
| 误报（假错误） | T5 级联等表错位 6–10 条 error | 双向级联，一个错表连带多规则 |
| 误报（真噪声） | 91 条 findings 中 55 条是 `V33-120` 逐科目 0.01 舍入（宜川单份 23 条） | 正确但淹没信号 |
| 需求①失效面 | 纯扫描件 0 字符 + 10 条 error 误报 | 扫描件直接不可用 |
| 验证面 | 7 份真实材料中仅 1 份有 6 条人工标注（且该份 6/6、0 误报） | 其余 85 条 finding 无判定依据 |

### 为什么"再补一批规则"是错的方向

- 82 条注册规则里，**真正消费结构化表格的不到 10 条**（`STRUCTURED_MIGRATED_RULES`
  只登记了 V33-115/117/120/202/203 的部分化；structured_rules.py:41-62 注释明说
  WP5 才迁移）。而结构化解析器 `build_parsed_tables`（坐标+语义列）已经能把同一份
  材料拆对——文旅局能认出 10 张表、347 行事实入库，规则却还在用"锚点页+最大表+并一页"。
- 同一个底层缺陷在两侧起作用：修不懂双栏和错列，加 40 条预算规则只会得到
  40 条新的 `insufficient_data`。
- AI 补不上：AI 从未成功调用过一次（实测两连失败），且 AI 若拿到同样错位的
  表格数据，会去"合理化"错误数字，产生更隐蔽的误报。

### 修复顺序（每一步都以前一步的证据为依据）

1. **先建 7–20 份语料与精度基线**（否则后面全是盲改）。
2. **把规则输入切换为结构化解析**（修双栏/续页/错列这一处），一次同时消灭
   A（误报）与 B（漏报）的最大公约数。工程量主要是迁移 + 回归验证，不是重写。
3. **V33-120 按"表+差额"聚类**（3–5 人天），把舍得里把石泉/文旅/宜川的输出量
   直接从 84 压到 ~36。
4. **总量级：需求①+③在"决算+纯文本"子集 25–40 人天；含预算 55–85 人天；
   含扫描件 75–115 人天。**（详见第六阶段对照表。）

## 附录 A：本次审计的复现命令

```bash
# 静态质量
.venv/Scripts/python.exe -m ruff check .                       # All checks passed
.venv/Scripts/python.exe -m mypy api src tests                 # Success: no issues found in 254 source files
.venv/Scripts/python.exe -m pytest -q                          # 2086 passed, 135 skipped (3:08)

# 前端
npm --prefix app run build                                     # exit 0

# 规则引擎实跑（离线，不需要 DB）
GOVBUDGET_AUTH_ENABLED=false PYTHONPATH=. .venv/Scripts/python.exe -c "
import pdfplumber
from src.engine.pipeline import build_document, build_issues_payload
from api.main import _extract_visible_text_from_page, _extract_tables_from_page, _assess_page_extraction
pdf='corpus/DOC-20260905-001/sample.pdf'
t=[];b=[]
with pdfplumber.open(pdf) as p:
    for pg in p.pages: t.append(_extract_visible_text_from_page(pg)); b.append(_extract_tables_from_page(pg))
print(_assess_page_extraction(t,b))
d=build_document(path=pdf,page_texts=t,page_tables=b,filesize=1)
pl=build_issues_payload(d,use_ai_assist=False,report_kind='final')
print(len(pl['issues']['all']), pl['rule_execution_summary'])"

# 端到端（需先起服务）
GOVBUDGET_AUTH_ENABLED=false .venv/Scripts/python.exe -m uvicorn api.main:app --port 8099
curl -X POST http://127.0.0.1:8099/api/auth/login -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"admin123"}'          # 取 access_token
# 注意：业务接口鉴权头是 X-Session-Token，不是 Authorization: Bearer
```

## 附录 B：三份真实材料的实跑结果

| 样张 | 页数 | 文种 | 覆盖率 | 注册/执行规则 | insufficient | finding | 义务覆盖率 | AI | 终态 | 规则耗时 |
|---|---|---|---|---|---|---|---|---|---|---|
| 普陀区生态环境局 2025 部门决算（golden） | 31 | final | 96.8% | 65 / 52 | 13 | 7 | 72.09% | 失败（网络） | review_required | 359 ms |
| 普陀区曹杨新村街道 2026 部门预算 | 30 | budget | 96.7% | 25 / 18 | 7 | 3 | 68.18% | 失败（熔断） | review_required | 328 ms |
| 纯图像扫描件（由 golden 转制） | 6 | final | **0.0%** | 65 / 17 | **48** | **12（10 条 error）** | — | 未执行 | — | — |

> 声明：本报告未修改任何代码。实跑过程中产生的临时文件（`scratch/` 下）已清理，工作树保持干净。
