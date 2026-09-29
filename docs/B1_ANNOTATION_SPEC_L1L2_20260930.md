# B1 基准语料标注规范（L1/L2）——2026-09-30

> 适用范围：`corpus/` 全部登记材料的人工标注（WP4-I 方案 §3 的执行细则）。
> 上游依据：`docs/WP4-I_BENCHMARK_PLAN.md` §3（golden schema、标注方式、
> 判定定义）；`docs/B1_CORPUS_CHECKLIST_20260928.md` §3.1/§3.2（产物格式、
> 盲标纪律）。与它们冲突时以 WP4-I 方案为准，本规范只做细化。
> 现行唯一金标范例：`corpus/DOC-20260905-001/golden.json`。

## 一、两种标注深度（与 manifest `depth` 列、eval 的 by_depth 分层对应）

### L1（结构类事实，每份必做，0.5~1 h/份）

只标**材料事实**，不做判断性复算。字段内容：

- 文种（final / budget / unknown）、财政年度、发布单位全称；
- 页数、核心表清单（表名 + 起始页）、必备说明章节清单（章节名 + 页码）；
- 条件性表格三态：有数据 / 空表（含空表说明页码） / 整表缺失且无说明；
- 三公经费「有支出 / 全零」、绩效「有独立表 / 并入说明」。

底稿：`docs/B1_L1_DRAFT_20260928.csv`（字段带出处与置信；置信=中 的字段
必须回原文核对后才能定稿）。表清单是文本层命中产物，**版式吃掉的表会漏**，
定稿时重点人工清点。

### L2（判断性标注，问题富集材料做，2~3 h/份）

在 L1 之上逐条标注问题与「干净面」：勾稽复算、表文一致、文内一致、
同比与口径、三公、绩效。**正负例都标**（见 §四）。

盲标纪律（红线，摘自勾选单 §2.2.1）：

1. L2 材料在 `truth_frozen_at` 写入前，**禁止**对其运行 `run_benchmark`
   （跑了当前引擎即丧失盲标资格，只能降级为 L1 锚定观察面）；
2. L2 候选 findings 只允许来自**旧版**系统输出（uploads 历史任务结果），
   禁止使用当前代码输出；
3. 标注人不得在标注期间查看 `outputs/benchmark*/` 下涉及该材料的任何产物。

## 二、golden.json 产物格式（入库边界见 .gitignore：JSON/MD 入库，PDF 不入库）

```text
corpus/DOC-B1-###/
├─ sample.pdf        # 不入库（gitignore）
├─ golden.json       # 入库
├─ ANNOTATIONS.md    # 入库：修订记录、分歧仲裁
└─ manifest 行        # S1 工具已写
```

golden.json 顶层字段（沿用 DOC-20260905-001 现行 schema）：

| 字段 | 说明 |
|---|---|
| `doc_id` / `sha256` | 与 manifest 行一致；`sha256` 必须等于源 PDF 实算值 |
| `annotation_version` | 从 `"1"` 起；冻结后任何修订递增并在 ANNOTATIONS.md 记原因 |
| `annotated_at` / `truth_frozen_at` | 标注时间 / 冻结时间；**`truth_frozen_at` 为空 = 未冻结 = 不许跑基准** |
| `report_kind` / `fiscal_year` / `organization` | 与 L1 事实一致 |
| `reviewed_by` | 第二人复核人（每 10 份抽 1 份） |
| `labels[]` | 逐条标注，字段见 §三 |

## 三、单条 label 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `annotation_id` | ✓ | 材料内唯一，如 `L001` |
| `truth_id` | ✓ | 问题分类码（§五 taxonomy）；旧码（GJ/TB/ZB）必须先映射为现码（V33/BUD/CMM），映射记录进 ANNOTATIONS.md |
| `label` | ✓ | 四态之一（§四） |
| `page` | ✓ | 1 起页码；跨页断表写在主证据页 |
| `allowed_rule_ids` | ✓ | 允许命中该条的引擎规则码清单（含 common）；不确定时写 `["*"]` 并在 ANNOTATIONS.md 说明 |
| `location_key` | ✓ | `sec:`/`toc:`/`tbl:`/`xtbl:` 前缀 + 锚文本（如 `tbl:三公经费财政拨款情况`）；评测用它做位置匹配 |
| `expected_severity` | ✓ | critical/high/medium/low/manual_review（口径按 AGENTS.md 严重级别定义） |
| `evidence` | ✓ | **原文摘录**（含关键数字），评测的证据重叠匹配依赖它；禁止转述 |
| `confidence` | ✓ | 0~1；<0.7 的条目自动进待复核清单 |
| `obligation_group` | 可空 | 10 组义务分组之一（评测时由规则码反查；采用与否见勾选单 §四 待签项） |

## 四、label 四态（判定定义）

| label | 含义 | 对评测的作用 |
|---|---|---|
| `defect` | 真问题（数字错、勾稽不成立、说明缺失、口径错误…） | 引擎命中=TP，未命中=FN |
| `acceptable` | 材料正确处。**正例必须标**：核心合计、关键勾稽、三公合计、
  同比表述等「引擎可能误报的干净面」 | 引擎命中=FP；不标则 FP 无法测 |
| `rounding_hint` | 舍入/取整差（允许误差，不构成问题） | 引擎报 formal=计入门禁 C-2 的舍入误报 |
| `manual_review` | 标注人无法判定（OCR 风险、口径疑似调整、跨页断裂） | 不计入 P/R 分母，单列待复核 |

与 WP4-I 方案 §3.4 的对应：`defect` = 真问题；`acceptable` 上的引擎命中 =
误报；`manual_review` = 无法判断。**每个 L2 材料至少标注 3 处
`acceptable` 正例**——只标问题的真值集测不出 precision。

## 五、truth_id 分类码（问题分类 taxonomy）

优先使用引擎现行规则码（`V33-*` 决算 / `BUD-*` 预算 / `CMM-*` 通用），
使「真值分类」与「规则命中」可直接对表。无对应规则码的材料侧问题用
`DOC-*` 前缀自定义（如 `DOC-MISSING-TABLE`、`DOC-UNIT-ERROR`），并在
ANNOTATIONS.md 登记，供后续规则立项。**禁止使用旧码**（`GJ-*`/`TB-*`/`ZB-*`），
遇到旧期望（如 `samples/manifest.yaml`）先做映射。

## 六、流程与仲裁

1. L1 全量 → 每 10 份抽 1 份第二人复核 → 修订进 ANNOTATIONS.md；
2. L2（材料清单见 `docs/B1_L2_CANDIDATES_20260930.csv`，候选来自旧版输出）
   → 同上抽检；
3. 分歧仲裁：标注人与复核人意见分歧写入 ANNOTATIONS.md「分歧与仲裁」节，
   由用户裁决；未裁决前该条 label 记 `manual_review`；
4. 全部完成后写 `truth_frozen_at` 冻结 → 才允许 `run_benchmark`；
5. 冻结后修订：递增 `annotation_version` + ANNOTATIONS.md 记原因 +
   重跑全语料 eval（禁止只重跑有利单份）。

## 七、自检清单（每份材料定稿前过一遍）

- [ ] sha256 与 manifest、源 PDF 三方一致；
- [ ] L1 事实（文种/年度/单位/页数）与 PDF 封面/正文一致；
- [ ] 条件性表格三态逐表有结论（有数据/空表/缺失无说明）；
- [ ] `evidence` 全部为原文摘录，数字与原文逐字符一致；
- [ ] `location_key` 锚文本能在该页文本中找到；
- [ ] 旧码已映射；`confidence<0.7` 条目已列待复核；
- [ ] 至少 3 条 `acceptable` 正例（L2 材料）；
- [ ] ANNOTATIONS.md 已记：底稿来源、修订、分歧；
- [ ] 该材料从未出现在任何 `outputs/benchmark*/` 产物中（L2 纪律）。
