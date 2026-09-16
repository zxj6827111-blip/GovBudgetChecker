# 查全能力与"不把漏查当通过"整改：批次 A + 批次 C 核心交付

> 分支：`feat/v6-coverage-ledger-20260916`
> 基点：`7cdc32f`（`fix/v5-remediation-final-20260916` 的 HEAD）
> 对应方案：用户提供的《系统性整改方向：提高查全能力，并防止"漏查被当成通过"》
> 本批范围：方案 §1（报告画像收敛）+ §3（检查义务清单与覆盖台账）+ §6 的证据/导出部分；
> 批次 B / D / E / F 尚未开工，残留见第 6 节。

---

## 1. 先修正方案里与代码不符的三处前提

动手前做了代码核查，方案里三条表述与仓库现状不一致。它们不影响方案方向，
但会影响验收时"拿什么对账"，所以先记录：

| 方案表述 | 代码实测 |
|---|---|
| `engine/` 目录是规则引擎所在 | `engine/` 下**没有任何 Python 源码**，只有陈旧 `__pycache__/*.pyc`。真正的引擎在 `src/engine/`。 |
| "174 次规则执行中，38 次未完成" | 代码中**不存在**任何计算 38 的运行时统计。`docs/BATCH_A_INDEPENDENT_REVIEW_20260914.md:45` 的 38 是"漏登记的已注册规则数"，`docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md:104` 的 38 是"批量误报条数"，两者都不是"未完成"，且都无对应运行时计数。 |
| 规则编号形如 `C-001` | 不存在 `C-` 前缀。通用规则是 `CMM-001`…`CMM-006`；`C-001` 只出现在 `AGENTS.md` 的规则集描述里。方案 §2 的 `C-001`/`C-002` 实际对应 `CMM-001` 与"基本支出合计"（后者由 `V33-243`/`V33-117` 覆盖）。 |

另外核实了方案对现状的判断**确实成立**，且比方案描述更严重：

- 文种识别有 **8 处**独立实现，兜底值互相矛盾（`src/engine/common_rules.py` 默认 `final`，
  `src/engine/pipeline.py` 默认 `unknown`）。
- 除文种外只稳定识别了年度与机构名；**地区、本级/汇总口径、资金范围、发布主体层级未识别**。
- **不存在任何"检查义务/应检查事项"台账**。最接近的三种信号（页面文本覆盖率、
  7 张核心表识别、规则执行摘要）都不是"业务要求完成度"。
- `src/schemas/issues.py:454` 的 `RuleCoverage` 模型、以及 `AnalysisMetrics.coverage_rate`
  字段，**在引用它们的维度上没有任何读写**（`coverage_rate` 这个名字在
  `api/main.py` 里出现两处，都是本次新增的义务台账键，与本字段无关）。
  `src/db/migrations.py:104-121` 的 `issues` 表同理：定义了 `auto_status`/`human_status`
  但全仓无读写代码。

---

## 2. 本批交付

### 2.1 报告画像唯一化（方案 §1）

新增：

- `src/schemas/document_profile.py` —— `DocumentProfile`，每个维度携带
  值 + 来源 + 置信度 + **落选候选**（`rejected`）。
- `src/services/document_profile_resolver.py` —— 唯一解析器。

收敛的入口（保留原函数签名，内部改为调用解析器）：

| 原实现 | 现状 |
|---|---|
| `api/runtime.normalize_report_kind` | 转调解析器 |
| `api/runtime.extract_cover_metadata` | 转调解析器；返回值新增 `profile` / `profile_status` |
| `api/runtime._detect_cover_report_kind` / `_detect_cover_scope_hint` | 薄封装转发 |
| `src/engine/pipeline._resolve_report_kind` | 转调解析器 |
| `src/services/engine_rule_runner._resolve_report_kind` | 转调解析器 |
| `src/engine/common_rules._infer_report_kind` | 转调解析器 |
| `src/services/analysis_result_store._normalize_report_kind` | 转调解析器 |

流水线在 `api/main.py` 解析出**一个** `document_profile`，同时写入
`result.meta.document_profile` 与 `status.json` 顶层；后续规则执行、入库、
导出全部消费它。

三个刻意的行为修正（都是"减少静默猜错"方向，均有测试锁定）：

1. 候选按**来源优先级**取值（显式 > 封面标签 > 封面标题 > 上传文种 > 文件名 > 正文首页）。
   旧 `normalize_report_kind` 把文种与文件名混在一个"预算优先"的判断里，
   "用户选了决算、文件名带预算"会被静默判成预算；现在按优先级取值并记录冲突。
2. `common_rules._infer_report_kind` 不再对**整条路径**做关键词匹配。
   仓库目录名 `GovBudgetChecker` 自带 "Budget"，整串匹配会把任何材料判成预算。
   现在只看文件名基名，并按两种路径分隔符切分（Windows 路径在 Linux 上取基名不再出错）。
3. `common_rules` 的兜底从 `final` 改为 `unknown`。调用点按 `budget` / 其它 二分支
   选锚点集合，因此锚点选择结果不变，但"不知道"不再被伪装成"知道了"。

**未识别即留空**：年度、机构名、地区、口径、资金范围、提取质量识别不到就是
`None`/`unknown`，不做任何经验兜底（与 `src/utils/report_year.py` 同一条纪律）。
`profile_status` 为 `unresolved` 时质量门会保留 `unknown_report_kind`。

### 2.2 检查义务清单与覆盖台账（方案 §3）

新增 `src/engine/check_obligations.py`：

- **版本化清单**：54 条义务、10 个业务分组，`OBLIGATION_CATALOG_VERSION = "obligations-v1"`，
  另有内容指纹 `catalog_fingerprint()`（改注释不漂移，改 checker 必漂移）。
- **checker 按文种声明**（`checkers_by_kind`）。同一个要求（如"封面要素齐全"）
  在预算与决算下由不同规则实现（`BUD-003` / `V33-001`）；若声明成并集，
  决算材料会把预算规则不在注册表里误报成"尚未实现"——假缺口会淹没真缺口。
- **逐实例状态**：`completed` / `not_applicable` / `not_implemented` /
  `not_executed` / `insufficient_data` / `parse_ambiguity` / `execution_error` /
  `profile_unresolved` / `ai_not_run` / `ai_failed`。
- **三公经费拆成六个实例**（合计/分项/预算完成率/同比/表文一致性/披露要素），
  落实方案 §3"不能只记一条完成"。

配套改动 `src/engine/rule_outcome.py`：`summarize_rule_outcomes` 新增
**逐规则回执** `rule_statuses: {rule_id: status}`。此前摘要只有"未决规则列表"
和"命中规则列表"，通过的规则只剩一个计数——"这条检查到底跑没跑"无法回答，
台账只能靠总数推断（总数对不上就什么都证明不了）。同规则编号重复出现时保留更严重的状态。

质量门新增第 14 条原因码 `check_obligations_incomplete`：**存在阻塞性未完成义务时，
终态保留 `review_required` + `incomplete`**，并把未完成义务编号、原因分布、
清单版本一并写入 `review_reasons`。

### 2.3 证据与严重度的边界（方案 §4 尾段 / §6）

- 问题新增 `obligation_ids`（`src/schemas/issues.py` + `attach_obligation_ids`），
  让"这条问题属于哪一项应检查事项"可被页面、JSON、CSV 直接消费。
- 导出补齐检查范围：
  - JSON 新增 `conclusion_scope` / `check_coverage` / `unfinished_checks` /
    `document_profile` / `coverage_note`；
  - CSV 新增列 `关联检查项` / `obligation_ids`；
  - 旧任务（整改前的结果）明确写 **"旧版未记录"**，不补一个空的"已完成"。
- `conclusion_scope` 落地"未发现问题必须限定为本次已完成的检查范围"：
  `no_findings_within_covered_checks` / `findings_within_covered_checks` / `incomplete_scope`。

**刻意没做的一件事**：方案 §6 要求"有结论却缺证据的规则告警保留为待复核候选，
不计入已确认问题"。`src/services/evidence_guard.py` 当前对规则告警只打标记、
不改严重度、仍计入正式问题数（该设计在模块 docstring 里有明确理由）。
翻转它会改变"正式问题数"这一既有契约，进而影响 golden 回放基线——
而方案批次 A 的前提正是"先冻结基线再改口径"。因此本批不改它，
改由覆盖台账承担：**没有执行回执或证据的检查不得记为"已完成"**。
是否翻转 `is_formal_finding` 对规则告警的判定，留作独立决策。

---

## 3. 为什么完成率不能被绿化（方案 §3 的核心纪律）

台账分母只由**义务清单**决定，与规则注册数、调度条数无关。四条反向用例锁定：

| 手法 | 为什么无效 |
|---|---|
| 删规则 | 分母不变；被删规则的义务变成 `not_implemented`，完成率反而下降。测试 `test_removing_rules_from_the_registry_does_not_improve_coverage`。 |
| 把缺失当零 / 跳过输入 | `input_gaps` 只做诊断，状态判定只信规则回执；回执缺失即 `not_executed`，不默认通过。 |
| 批量标不适用 | 只有两条路径能移出分母：清单声明的文种不符、规则自身返回 `not_applicable`。两条都必须留下可读依据（`basis` + `detail`）。 |
| 分母归零 | 文种未识别时登记显式 `OBL-PROFILE-UNRESOLVED`，分母为 1、完成率 0.0。分母真为 0 时 `coverage_rate` 是 `None` 而不是 100%。 |

反向边界也有守护：把清单里的实现缺口补完后，完成率**必须**到 1.0
（`test_completing_every_obligation_reaches_full_coverage`）——永远红的指标
和永远绿的指标一样没有判别力。

---

## 4. 当前真实覆盖情况（基线口径）

`python scripts/check_coverage_baseline.py`（新增脚本，只读静态信息，不读 PDF、
不连库、不调 AI）：

```
画像解析器: document-profile-v1 | 义务清单: obligations-v1 (08004b7eb7447640)
尚未实现的检查要求: 7 项

-- final --   规则注册表 58 条
  应检查 43 项 / 不适用 0 项 / 未完成 43 项（其中阻塞 42 项）
  制度性完成率上限 0.8605（6 项尚无实现，任何材料都无法让它们完成）
    未执行: 36   尚未实现: 6   语义模型未执行: 1

-- budget --  规则注册表 22 条
  应检查 22 项 / 不适用 0 项 / 未完成 22 项（其中阻塞 21 项）
  制度性完成率上限 0.9091（2 项尚无实现，任何材料都无法让它们完成）
    未执行: 19   尚未实现: 2   语义模型未执行: 1
```

7 项实现缺口（`pending_checkers`，逐条带 `gap_note`）：

| 义务 | 文种 | 缺什么 |
|---|---|---|
| `OBL-CROSS-SAN-GONG-ECON` | final | 三公经费表与财政拨款经济分类表之间的跨表资金来源一致性（本轮样张"三公表与经济分类表冲突"漏报） |
| `OBL-TXT-FUND-DETAIL` | final | 基金表/国资表与对应说明的逐项金额比对（本轮样张"基金 105 与 135"漏报） |
| `OBL-NARRATIVE-INDICATOR-REPEAT` | final | 文内（同段/跨段）同一指标重复披露的一致性（本轮样张"职业年金 257.14 与 245.53"漏报） |
| `OBL-TREND-COMPLETION-RATE` | final | 预算完成率分母口径校验与复算（`AGENTS.md` R004 类缺陷） |
| `OBL-DISCLOSURE-PERCENT-UNIT` | budget/final | "占 XX.XX"漏百分号等百分比写法完整性（本轮样张"占76.23"漏报） |
| `OBL-SG-COMPLETION` | final | 三公经费预算数/决算数对比与说明一致性 |
| `OBL-PERF-PHASE-AMOUNT` | budget | 绩效阶段金额与项目集合/预算版本口径的说明性校验 |

**注意**：`未执行 36 / 19` 是"无材料基线"下的数字（没有跑任何规则，自然没有回执），
不代表真实场景。真实场景的完成率见第 6 节第 1 条——需要在真实材料上重新测。

---

## 5. 验证

| 检查 | 结果 |
|---|---|
| `pytest`（全量） | **1297 passed, 1 skipped**（本批新增 56 条） |
| `ruff check src/ api/ tests/` | All checks passed |
| `mypy api src tests` | Success: no issues found in 199 source files |
| `scripts/check_log_message_safety.py` | 通过 |
| `scripts/check_env_consistency.py` | 通过 |
| `scripts/check_coverage_baseline.py --assert-gaps 7` | 退出码 0；`--assert-gaps 3` → 退出码 1 |

新增测试文件：

- `tests/test_document_profile.py`（22 条）：四个入口文种一致、仓库目录名不参与
  路由、冲突保留、不猜测纪律、政府级不从机构名推断、口径/资金范围/质量维度。
- `tests/test_check_obligations.py`（23 条）：清单自检、声明 checker 必须真实存在、
  声明缺口必须真的没有实现、三公六实例、六态映射、四种防绿化手法、分组对账。
- `tests/test_coverage_baseline.py`（8 条）：基线形状与自洽、指纹跟随内容、
  CLI 缺口门禁、JSON 可机读。
- `tests/test_report_exports.py` 新增 3 条：旧记录显式标注、未完成清单导出、
  CSV 关联列。

改动测试里需要说明的 5 处（都是契约变更后的桩/断言更新，不是放水）：

1. `tests/support_rule_receipt.py`（新增）：把内联的 `rule_execution_summary` 桩
   集中到一处，规则编号取自**真实注册表**并带逐规则回执。原来 4 个文件各自内联
   "总数桩"，与生产契约脱节。
2. `test_quality_gate.py`：原 `test_pipeline_marks_clean_document_as_done_no_findings`
   断言"干净材料 = done"。在真实实现缺口存在时这条**不再成立**，改为断言
   `review_required` + `check_obligations_incomplete`，并新增反向用例：把缺口义务
   从清单摘掉后必须回到 `done` + `no_findings`。
3. `test_evidence_completeness.py` / `test_pdf_parse_isolation_and_backup.py` /
   `test_pipeline_stage_progress_integration.py`：这三个用例的自变量分别是
   证据链、解析隔离、阶段进度，不是门禁结论。因此摘掉"尚未实现"的义务，
   让终态仍能到达 `done`，避免另一个原因盖住它们要测的东西。每处都写了理由。
4. `test_structured_logging.py`：`"完成" in stages` 改为前缀断言——终态 stage 文本
   本来就有三态（`完成` / `完成（部分能力降级）` / `完成（需人工复核）`），
   等值断言会在质量门因任何原因转人工时误报。
5. `test_runtime_year_parsing.py`：未改动。它挡住了我一度把封面标签表
   改成"更自然"顺序的改动（`预算单位` 被 `单位` 抢先匹配），已按原表逐字恢复。

---

## 6. 残留与下一批建议

按方案批次顺序，本批完成了 A（基线可复现）与 C 的核心（画像 + 义务台账 + 统一执行记录），
以及 D 的证据/导出部分。**以下均未开工**：

1. **在真实样张上重测覆盖**（最该先做）。本批只建立了台账，没有跑真实材料。
   需要在宜川/石泉样张与历史 job 上跑一遍，确认：(a) 规则回执确实覆盖全部
   已实现义务（"未执行"归零）；(b) 真实场景的自动完成率与人工复核量；
   (c) `check_obligations_incomplete` 没有把本来完整的材料误转人工。
   **这是本批最大的未验证点。**
2. **批次 B 的六类通用核验**（方案 §4）：表内关系、表间关系、表文关系、文内关系、
   比例与变动、披露与表达。台账已把 7 个缺口点名并给出 `gap_note`，
   但规则本体一条都没写。
3. **批次 C 的取数层**（方案 §2）：统一事实记录仍缺"主体、年度、资金口径、
   预算/决算属性、显示精度、行号列号"。实测两套互不相通的解析
   （内存 `structured_rules` vs 落库 `pdf_parser`+`fiscal_fact_materializer`），
   后者金额是 `float`（`src/services/fiscal_fact_materializer.py:30`）且**丢弃单位换算**
   （`src/services/pdf_parser.py` 的 `_parse_numeric` 只是 strip 掉 `亿元/万元/元`
   再转 float，不做量纲归一，亿元表与万元表会落进同一列）。
   合并单元格 `row_span/col_span` 只有建列与读写，没有任何计算合并跨度的实现
   （`src/db/migrations.py:619` 建列，`src/services/pdf_parser.py` 只按默认值 1 持久化）。
   `STRUCTURED_PARSING_CONSUMERS` 仍只有 `V33-115` 一条，且 `run_structured_rules`
   在生产路径**没有调用者**。
4. **解析快照**：无版本化，重跑解析是原地 `DELETE` + 重写，同一 `document_version_id`
   下的历史解析结论不可回溯。方案 §2 要求的不可变快照未做。
5. **严重度分级修正**（方案 §4 末段）：1,844.57 万元差额被标为普通提示的问题
   本批未处理。金额差额与证据置信度分开管理的统一策略尚未建立。
6. **前端展示与输出物覆盖**（方案 §6）：后端已把画像、覆盖摘要、四类内容写入
   status.json 载荷与 JSON/CSV 导出；但 `app/` 前端**一行未动**——页面上仍只有
   问题列表，看不到"哪些未完成、为什么"。`make frontend-build` 未运行（本批无
   前端改动）。PDF 标注版与 DOCX 导出**未携带**义务覆盖与画像信息，只在 JSON/
   CSV 与 status.json 可见。
7. **`is_formal_finding` 对规则告警的判定**：见第 2.3 节，留作独立决策，
   需要连同 golden 基线一起重冻结。
8. **发布门槛**（方案 §7）：已知缺陷回归集、结构变化测试、真实盲测
   （精确率 ≥98%、高严重度召回 ≥95%）、每类型 ≥20 份真实材料
   （≥10 份不参与开发）——全部未建立。当前只能声称"台账口径可复现"，
   **不能声称任何报告类型已完整支持**。

本批启动时 `make lint` 在本机已因 `scratch/`（gitignored 的临时脚本）报 9 条 ruff 错误，
与本次改动无关，未处理（属他人临时产物）。

本地已提交到 `feat/v6-coverage-ledger-20260916`，**未推送**。
