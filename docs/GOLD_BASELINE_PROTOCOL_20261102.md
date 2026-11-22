# 金标准基线协议（2026-11-02）

> 本文件在**计算任何指标之前**写定，之后的匹配与指标按它执行。执行中如需修订，在文末“修订记录”
> 追加（写明时间、原因、影响了哪些行），不改动正文。真值定义见 `docs/GOLD_STANDARD_SPEC_20261101.md`
> （§七指标、R1–R29）与 `gold/DISPUTES.md`（D1、D2 均裁决为 A）。

## 0. 被测系统与运行条件

| 项 | 取值 |
|---|---|
| 被测提交 | `c6cd033d79f907a4541af7036f3d8dc49a140cd2`（开始与结束时 `git rev-parse HEAD` 一致） |
| 代码快照 | `git archive` 导出到 `outputs/gold_baseline_20261102/code/`；只对快照运行，不碰在线工作树 |
| 开始时工作树状态 | `outputs/gold_baseline_20261102/git_status_at_start.txt`（`verify_pending_review.py` 等有未提交改动，与被测提交无关；快照不含它们） |
| 运行画像 | `outputs/pending_review_20261101/acceptance_kit/v1-regression-r4/runtime_profile.json`：AI 关、OCR 关、`DATABASE_URL=` 空（无库）、`PYTHON_DOTENV_DISABLED=1`、`PYTHONHASHSEED=0`、`TZ=Asia/Shanghai`、模板范围关（`GBC_TEMPLATE_SCOPE=0`） |
| 子进程环境 | 从零构造；`HOME/TMPDIR/ORG_DATA_DIR/UPLOAD_DIR/AUDIT_LOG_PATH/AI_MODEL_SETTINGS_PATH/DISCLOSURE_CALENDAR_PATH` 指向每份样本的独立运行目录；`PATH` 固定为 `/usr/bin:/bin`（**偏离**：任务写“指向运行目录”，实际取固定常量而非继承，因为 Python 标准库与 `sw_vers` 等需要系统目录；环境不继承调用者这一目的不变） |
| 输入 | 33 份冻结金标 PDF 的拷贝（sha256 与样本清单逐份核对，另核对 33 份 `annotation.json` 与 `gold/FROZEN.csv`，全部一致）。上传链路输入（预检→元数据→自动归属组织）推导一次后冻结在 `inputs/inputs.json`，两遍共用；组织台账用验收包 r4 的 1,028 条快照（只读复制） |
| 采集脚本 | `scripts/gold_baseline_run.py`（`prepare / collect / selfcompare`）；借用 `verify_pending_review.py` 的子进程 worker 做法，不调用它（它拒绝金标 sha256） |
| 运行方式 | 同一快照连跑两遍（`run1`、`run2`），逐份保存完整 `status.json`；`todo/` 保存管线内同一组函数算出的列表计数与阻塞义务 |
| 重要说明 | **AI 关**为主条件，故“靠 AI 才能发现的错别字/语义类”（R25）在本基线中**未被代表**；其命中数只代表确定性词表（CMM-009 等）的能力 |

**doc_type 说明**：上传链路对 33 份都推导出 `doc_type = dept_final`（含封面为“年度决算”的单位决算），
并按 `upload_selection` 的归属组织层级（均为部门级台账条目）确定“部门/单位”口径。这是线上上传的真实行为，
**不是**本脚本的选择；它会直接影响 CMM-018/019 等口径类规则（见 §3、报告 §误报原因）。

## 1. 真值单位

**金标检查单位（CU）**：24 项检查里 `items[]` 中 `verdict = defect` 的每一条（一项检查可含多条）。共 73 条。

**金标自由发现单位（FU）**：`free_findings[]` 的每一条（`occurrences` 保留，用于出现点级复核）。共 103 条，按
`expected` 文本与类别归入下列**真值类**（脚本里用同一组正则，见 `scripts/gold_baseline_match.py: classify_free`）：

| 真值类 | 判定 | 穷尽性（计入漏报率分母？） |
|---|---|---|
| R9-千分位 | `expected` 含“半角逗号分千分位” | 是（R9 程序全文扫描） |
| R9-小数位 | 含“小数位不是两位” | 是 |
| R9-双小数点 | 含“双小数点” | 是 |
| R9-标点连用 | 含“标点符号连用” | 是 |
| R9-半角冒号 | 含“半角冒号” | 是 |
| R22-半角标点 | 含“半角逗号、半角分号或句末半角句点” | 是 |
| R28-文号 | 含“六角括号” | 是 |
| R21-口径 | 含“口径表述” | 是 |
| R24-序号 | 含“序号”，或 `quote` 为“数字+、/.”开头的列表序号混用（GS-2025-05 F01 经人工归入） | 是 |
| 尽力-错别字/多字漏字/语句不通/重复词 | 类别为这四类 | **否**（R25，单列命中数） |
| 尽力-模板残留 / 编码名称不符 / 其他（语义） | 其余 | **否**：spec 未把它们列入“要求穷尽”清单（R25 只列了程序全文扫描的类），故按保守口径不进分母；另在报告里给“若计入”的敏感性数字 |

`cannot_determine`：全集 0 条（已核对），不进任何分母，若出现单列。`not_applicable`（GS-N07、GS-T07 各 1 份）视同 pass。

**D2、R8、R19、R29 的执行**：金标已按它们改判为 pass；系统按清单报出同类问题（2022 三公表名带“一般公共预算”、
2023+ 部门决算正文无“第二部分”、半角括号、被列宽截断的科目名）一律按金标 pass 记误报，并在报告里单列“按 D2/R29/R19 计入的误报”。

## 2. 什么算“系统问题”（四层，彼此分开计数）

数据全部取自每份 `status.json`：

| 层 | 代号 | 提取方式（与 `api/runtime.py` 列表摘要、`review_queue_counts` 同口径） | 本基线用途 |
|---|---|---|---|
| 正式问题 | **F** | `review_report.issues[]` 中 `evidence_status ≠ degraded_missing_evidence` 且 `severity ∈ {critical,fatal,error,high,warn,warning,medium,low}`。共 155 条（60 error + 95 warn） | 误报率分母；漏报率的“命中” |
| 未能自动核对（检查补核） | **O** | `todo.blocking_ids`：`check_coverage.instances` 里 `blocks_gate` 且状态未完成、且未被同源正式问题覆盖的义务（`blocking_coverage_obligations`）。共 90 条。**界面上并入“必须处理”**（2026-11-01），但它声明的是“没查成”而非“有错” | 工作量；待核实命中率（政策口径）；静默漏报的“非静默”判定 |
| 待核实 | **P** | `review_report.manual_review[]` 中 `record_type = finding_review_candidate`（同时核对 `issues/tips` 里 `review_kind = manual_review` 或 `severity = manual_review`：0 条）。共 73 条 | 待核实命中率；工作量；静默漏报的“非静默”判定 |
| 参考 | **R** | `review_report.tips[]`（166 条，全是 `info`）、`manual_review` 中 `review_reason`（61 条，整体性提示）、`degraded`（0 条）；`unfinished_check`（123 条）是 O 层的底层记录，不重复计 | 不进误报率与工作量；仅用于“漏报但在参考里出现过”的单列 |

自检：对 33 份逐份复算 F / O / P / R 的条数，必须与 `todo.queue_counts`（`queue_required_total`、`queue_obligation_total`、
`queue_candidate_total`、`queue_tip_total`、`queue_notice_total`）一致，不一致则停止。

**界面口径**：列表“必须处理” = F + O；“参考”含 P；“待核实”页签 = P。本基线的**人工工作量** = F + O + P。

## 3. 规则 → 金标映射

系统问题先按“规则号 + 报文特征”归入**主张类型**（CT），再由 CT 决定可对应的金标检查/真值类。
规则语义取自 `rules/checklists/final_checklist_v1.yaml`、`src/engine/check_obligations.py`（义务→规则）与各规则类的文档。

### 3.1 F / P / R 层：规则 → CT → 金标目标

“叙述章节”指按金标 `basic.sections` 的页码把问题所在页归入对应章节：一→N01、二→N02、三→N03、四→N04、五→N05、六→N06、七→N07、八/九→N08。

| 规则（报文特征） | CT | 金标目标（检查 / 真值类） |
|---|---|---|
| CMM-013（含“标题缺少‘第…部分’字样”） | S02-部分 | GS-S02 |
| CMM-013（其余：目录与正文标题不一致、正文未找到目录标题） | S03-目录 | GS-S03 |
| CMM-014 | 编号 | GS-S03（标题重复项）、R24-序号 |
| CMM-017 | S04-名称 | GS-S04 |
| V33-001、CMM-012 | S04-年度 | GS-S04（R27；与 S03 并列时同看 S03） |
| CMM-018、CMM-019、CMM-027、V33-236 | R21-口径 | R21-口径（金标 R2：封面为准） |
| V33-002、V33-003、CMM-021 | S01-表 | GS-S01 |
| V33-109、V33-114、V33-122、CMM-026 | S01-空表 | GS-S01、GS-N08 |
| V33-112 | 占位符 | GS-S01（空表说明含“XX 局”）、尽力-模板残留 |
| CMM-008 | 模板残留 | 尽力-模板残留 |
| V33-234（含“仍保留该表格式”） | 模板残留-空表 | GS-S01、尽力-模板残留 |
| V33-234（含“未说明原因”“仅重述结果”） | 原因 | GS-N09、叙述章节检查 |
| V33-234（含“同比比例与同条金额不一致”） | 同比 | 叙述章节检查（N01/N04/N05/N07） |
| V33-234（含“条目序号由…跳”） | 编号 | R24-序号 |
| CMM-010 | 格式 | R9-千分位 / R9-双小数点（小数位类系统无对应规则，见 §4.4） |
| CMM-002（“连续句号”） | 标点连用 | R9-标点连用 |
| V33-113（“重复标点”） | 标点连用 | R9-标点连用 |
| CMM-002、V33-113（“重复词/重复用词”） | 重复词 | 尽力-重复词 |
| CMM-009 | 错别字 | 尽力-错别字/多字漏字/语句不通 |
| CMM-011、V33-222（“金额单位口径错误”）、V33-225 | 单位 | GS-W01、叙述章节检查 |
| V33-222（“表文金额不一致”） | 表文金额 | GS-N05 |
| V33-227 | 科目名称 | GS-N05 |
| V33-251 | 逐项完整 | GS-N05 |
| V33-249、V33-106 | 占比 | GS-N05 / N03（所在章节） |
| V33-102、V33-101 | 总表-说明 | GS-N01 |
| V33-103、V33-226 | 收入-说明 | GS-N02 |
| V33-104、V33-220 | 支出-说明 | GS-N03 |
| V33-105、V33-221、V33-242 | 拨款-说明 | GS-N04 |
| V33-107、V33-223 | 基本支出-说明 | GS-N06 |
| V33-108、V33-224、V33-245、V33-246、V33-SG-COMPLETION、CMM-001 | 三公-说明 | GS-N07 |
| V33-TXT-FUND-DETAIL、V33-248 | 基金-说明 | GS-N08 |
| CMM-007、V33-TREND-COMPLETION-RATE | 同比/完成率 | 叙述章节检查（N01/N04/N05/N07；R16、R26） |
| V33-NARRATIVE-INDICATOR-REPEAT、CMM-020、V33-250 | 文内一致 | 叙述章节检查；V33-250 另看尽力-其他 |
| V33-110、CMM-005 | 方向 | GS-N09（R15） |
| V33-240 / V33-241 / V33-005 / V33-120 / V33-233 | 表内合计 | GS-T02 / GS-T03 / GS-T05（按报文表名） |
| V33-243、V33-117、V33-203 | 基本支出 | GS-T06、GS-T08（经济分类） |
| V33-244 | 三公表 | GS-T07 |
| V33-214、V33-115 | 总表平衡 | GS-T01 |
| V33-119 | 拨款总表 | GS-T04 |
| V33-200、V33-201 | 总表-明细 | GS-X01 |
| V33-252 | 基金列 | GS-X02 |
| V33-202、V33-203 | 拨款表-功能表 | **无金标对应**（金标 X02 只管基金/国资列，T05 只管 T5 内部） |
| CMM-004（“金额差异较大”） | D-011 | **无金标对应**（口径决议⑤：差异可能源于结转结余，不判错） |
| CMM-015、CMM-016 | 版面 | **无金标对应** |
| 其余未列规则 | — | 脚本遇到未列规则即报错，须先补本表（修订记录里登记） |

### 3.2 O 层：义务 → 金标检查 / 真值类（文档级）

| 义务 | 金标 |
|---|---|
| OBL-STRUCT-NINE-TABLES / -EMPTY-STATEMENT / -TABLE-NAMES | GS-S01 |
| OBL-STRUCT-SECTIONS | GS-S02 |
| OBL-STRUCT-TOC-ALIGN | GS-S03 |
| OBL-STRUCT-ORG-NAME、OBL-STRUCT-COVER、OBL-TEXT-YEAR | GS-S04 |
| OBL-INTERNAL-T1-BALANCE、OBL-CROSS-T1-IDENTITY | GS-T01 |
| OBL-INTERNAL-T2-TOTAL | GS-T02 |
| OBL-INTERNAL-T3-TOTAL、OBL-INTERNAL-DETAIL-ROW | GS-T03 |
| OBL-CROSS-FISCAL-TOTAL | GS-T04 |
| OBL-INTERNAL-TABLE-TOTAL | GS-T05 |
| OBL-INTERNAL-BASIC-EXPENSE | GS-T06、GS-T08 |
| OBL-SG-TOTAL、OBL-SG-ITEMS | GS-T07 |
| OBL-CROSS-T1-T2、OBL-CROSS-T1-T3 | GS-X01 |
| OBL-CROSS-T4-FUNDS | GS-X02 |
| OBL-TXT-T1 / T2 / T3 / T4 / T5 / T6 | GS-N01 / N02 / N03 / N04 / N05 / N06 |
| OBL-TXT-FINAL-DETAIL | GS-N05 |
| OBL-TXT-T7、OBL-SG-TABLE-TEXT、OBL-SG-COUNTS、OBL-SG-DISCLOSURE、OBL-SG-COMPLETION、OBL-SG-YOY | GS-N07 |
| OBL-TXT-FUND-DETAIL | GS-N08 |
| OBL-TREND-DIRECTION、OBL-TREND-COMPARATIVE-LOGIC | GS-N09 |
| OBL-TREND-ZERO-BASE、OBL-TREND-COMPLETION-RATE、OBL-NARRATIVE-AMOUNT、OBL-NARRATIVE-INDICATOR-REPEAT、OBL-NARRATIVE-SHARE-SUM | 叙述类（N01–N07 任一，见 §4.3） |
| OBL-TEXT-AMOUNT-UNIT | GS-W01 |
| OBL-TEXT-AMOUNT-FORMAT | R9-千分位 / 小数位 / 双小数点 |
| OBL-STRUCT-PUNCTUATION | R9-标点连用、R9-半角冒号、R22、尽力-重复词 |
| OBL-STRUCT-TERMINOLOGY | R21-口径 |
| OBL-STRUCT-NUMBERING | R24-序号 |
| OBL-STRUCT-PLACEHOLDER | 尽力-模板残留 |
| OBL-TEXT-TYPO | 尽力-错别字 / 多字漏字 |
| OBL-NARRATIVE-CODE-NAME、OBL-TEXT-SUBJECT-CODE、OBL-PERF-ZERO-COUNT、OBL-STRUCT-PAGES、OBL-STRUCT-VISUAL、OBL-STRUCT-NUMBER-VALIDITY、OBL-AI-SEMANTIC-REVIEW | 无金标对应 |

## 4. 匹配规则（对所有文档同一套）

### 4.1 页面与证据
系统问题的页集 `pages(I)` = `evidence[*].page` ∪ `page_number` ∪ `location.page` ∪ `location.body_page` ∪ `location.table_refs[*].page`；
文字取 `title + message + evidence[*].quote/text_snippet + location.actual/expected`。
金标检查单位的页集 = 其 `values[*].page`；自由发现 = `page` 与全部 `occurrences[*].page`。
金标检查 c 在文档 d 里的**已检页集** = c 所有 items（pass 与 defect）的 `values[*].page` 的并集，叙述类检查再并上对应章节页范围。

### 4.2 匹配步骤（对每条 F / P 问题 I）
1. 取 I 的 CT 与金标目标集 T（§3.1）。T 为“无金标对应”→ 状态 `NOGOLD`，进 §5 复核。
2. 在同一文档的 T 内找候选金标单位：检查单位 / 真值类 FU，要求 `pages(I) ∩ pages(单位) ≠ ∅`。
3. **锚点重合**：金标单位 `values[*].quote`、`label`、`formula`、`note` 里的数字（去千分位，≥3 位）或 ≥4 字的中文片段，至少一个出现在 I 的文字里；
   对 FU，改为“I 引用的原文串与某个 `occurrences[*].quote` 互相包含”。
4. 判定：
   - 存在 defect 候选单位且页重合 + 锚点重合 → **TP**，记连边（I ↔ 单位）。
   - 仅页重合、无锚点重合的 defect 候选 → `AMBIGUOUS`，人工判（§5）。
   - 无 defect 候选，但 T 内某检查（或穷尽性 FU 类）的已检页集覆盖 `pages(I)` 且其 items 全为 pass → **FP-金标**。
   - 其余（金标没检过该处，或尽力类 FU 无对应）→ `UNMATCHED`，进 §5。
5. **方向相反**：I 与金标 defect 单位同主体同位置，但 I 声称的“应为”与金标真值相反（如 CMM-019 要求加“部门”而金标 R2 以封面为准），
   记 FP（`wrong_direction`），且该金标单位**不**因此算命中。
6. 一条 I 可连多个单位，一个单位可被多条 I 命中；误报率按 I 计，漏报率按单位计。
7. O 层：按 §3.2 取其金标检查/真值类；若同文档该检查有 defect 单位，则该 O 条目对这些单位“覆盖”（文档级，不看页）；O 条目命中判定为 `O-HIT`（该义务所属检查在金标里有 defect），否则 `O-NOISE`。
8. P 层：同 F 层规则得 TP/FP-金标/NOGOLD/UNMATCHED；`CMM-004` 的“金额差异较大”恒为 NOGOLD，按口径决议⑤不判错，**不记命中**（单列“金标范围外”）。
9. R 层（tips/notices）：只用于“金标 defect 是否至少以参考形式出现过”，按同一页+锚点规则连边。

### 4.3 叙述章节检查
叙述类 CT 的目标检查 = 问题所在页对应的章节检查（§3.1 说明）∪ CT 附带检查（单位→W01，原因/方向→N09）。

### 4.4 系统无对应规则的真值类
“R9-小数位”（金额/百分比小数位不是两位）：清单 C-09/C-15 虽写“小数位统一两位”，但系统没有对应检测规则时，该类的金标单位全部自然成为漏报；
报告里按“规则缺失”归因（查 `CMM-010` 实现确认后写）。

## 5. 无对应金标的问题（复核）

对 `NOGOLD`、`UNMATCHED`、`AMBIGUOUS` 的 F 与 P 条目，以及**每一条 FP-金标**，逐条回 PDF 复核：
渲染该页成图，并用 pdfplumber 再读一遍文本（PyMuPDF 已有丢字先例，R14）；判为
`adjudicated_true`（问题真实存在）/`adjudicated_false`（系统误报）/`indeterminate`（原文无法裁定）。每条记证据（页码 + 逐字原文 + 一句理由）。
- 复核得 true 的 FP-金标：**金标可能漏标**，列入“金标修订候选”（页码+原文），**不改金标**；指标仍按金标记误报，另给“若采纳修订候选”的敏感性数字。
- 复核得 true 的 UNMATCHED：计为真阳性，但不计入“金标漏报”（金标没有这条）；单列为“系统比金标多发现”。
- `indeterminate` 不进误报率分母，单列。

## 6. 指标公式

记 `TP_g`、`FP_g` = 与金标 defect / pass 对应的 F 条数；`U_t`、`U_f`、`U_i` = UNMATCHED/NOGOLD/AMBIGUOUS 经复核得 true / false / indeterminate 的 F 条数。

| 指标 | 公式 |
|---|---|
| **误报率（头条）** | `(FP_g + U_f) / (TP_g + FP_g + U_t + U_f)`，仅 F 层；`U_i` 单列 |
| 误报率（严格金标口径） | `FP_g / (TP_g + FP_g)`，只算有金标对应的 F 条 |
| 误报率（含 O 的界面口径） | 把 O 层当作“必须处理”条目：`O-NOISE` 视为误报，`O-HIT` 视为非误报；分母加 O 总数。仅作对照，不作头条（O 不声称有错） |
| 按规则 / 按检查 / 按年份 / 按口径 | 同一公式，分组计 |
| **漏报率** | 分母 = 金标 CU（defect 条目）+ 穷尽性 FU 类的 defect 单位；分子 = 其中**没有任何 F 命中**的单位。`cannot_determine` 不进分母 |
| 静默漏报率 | 分子 = 漏报单位中**既无 F 命中、也无 P 命中、也无覆盖它的 O 条目**的单位；其中再拆 `R-only`（只在参考里出现，字面上也算静默，但单列）与 `完全静默` |
| 自由发现-尽力类 | 不算漏报率；报 `命中数/总数`（分类汇总），以及“若计入” 的敏感性漏报率（模板残留、编码名称不符、其他语义） |
| 出现点级召回 | FU 的 `occurrences` 逐点（页+原文）被 F 层问题的原文串命中的比例，补充用 |
| **待核实命中率** | `P` 层：命中 = 连边到金标 defect 单位的 P 条数 / P 总数。分母不含 NOGOLD（单列“金标范围外”）。`P+O` 合并版：`(P 命中 + O-HIT) / (P + O)`（spec §七写“待核实/未能自动核对”，故两版都报） |
| **人工工作量** | 每份 `F + O + P` 条数（O = `queue_obligation_total`，F = `queue_required_total`，P = `queue_candidate_total`）；报均值、中位数、最大、合计，以及其中“复核为真”的占比（F 层） |
| **自动化覆盖** | 对每份文档的 24 项检查，系统给出 **defect**（有连到该检查的 F 层 TP/FP/…问题，即 F 问题的目标集含该检查）或 **pass**（该检查所映射的全部义务状态为 completed，且无指向该检查的 F/P 问题）则计“有结论”；映射义务中有任一未完成、或该检查无映射义务（`OBL-AI-SEMANTIC-REVIEW` 不算）计“无结论”。覆盖 = 有结论的（文档,检查）对数 / 792 |

“系统给出 pass”以义务账本为准：`check_coverage.instances[*].status = completed` 且未阻塞。此为“系统自认查完且没报”，
它与金标 defect 并列时即是“系统判为通过的漏报”（较静默漏报更强的一类），在 §漏报分析里单列。

## 7. 已知局限（预先声明）
- 33 份是同一上海市语料的分层样本，不代表其它地区；2025 年用 2024 模板（金标 R8）。
- AI 关；OCR 关（33 份均有文本层，`text_layer` 全为“有”，不受影响）。
- 同批 F 条目可能因去重/合并与金标单位不是一一对应；已用连边（多对多）表达。
- 组织归属取自验收包快照；GS-2024-04 未能自动归属（见报告）。

## 修订记录
以下修订都发生在**看到第一版自动连边之后、算指标之前或之间**，目的是让“命中/误报”的判定名副其实；每条都写明受影响的行。
按时间顺序（2026-11-02）：

1. **主张类型与金标条目标签的兼容（`COMPAT`）。** 第一版自动连边只要求“同检查 + 页重合 + 数字/词锚点重合”，结果把
   “GS-2022-06 的 V33-234（未说明原因）连到 N05 名称缺陷”“GS-2022-08 V33-222（元/万元）连到 N05、N09”这类
   偶然锚点（同一页上的同一数字）也连了边，会让不相干的金标缺陷被虚假“命中”。改为：每种主张类型只能连到标签相容的金标条目
   （正则见 `scripts/gold_baseline_match.py: COMPAT`）。受影响：约 12 条自动连边被去掉或改连。
2. **“同比/完成率/文内一致”类允许连到自由发现“其他”（尽力类）**：GS-2024-04 的 F06（机关运行经费 155/697=22.24% 对 18.2%）
   本来就是这类主张，第一版漏连。受影响：GS-2024-04 V33-234-15（由“无金标对应”改为 TP，连 F06）。
3. **O 层叙述类义务只对应 N01–N07**（协议 §3.2 原文如此；第一版代码误展开到 N01–N09，使 GS-2022-03 N09 条目被虚假“已有未能核对条目”）。受影响：1 个金标条目由 PEND 变 SILENT。
4. **CMM-010 的“金额小数位不统一”**（info，进参考）按 R9-小数位处理（第一版只映射千分位/双小数点）。受影响：GS-2024-08 F04 由 SILENT 变 REF。
   同时更正 §4.4：CMM-010 **确有**小数位检查，但只查“多于两位小数”，金标 R9-小数位的绝大多数（少于两位，如“0万元”“12.7%”）它不查。
5. **人工逐条复核了全部 155 条 F 与 73 条 P**（见 `scripts/gold_baseline_decisions.py`，每条带 PDF 页码与逐字原文），其中：
   - 对 TP 的连边逐条检查；自动连边里“同检查但不是同一个错”的偶然锚点再去掉（GS-2024-07 V33-222-11/13/14、GS-2025-06 V33-234-10/-11/-12 与 V33-224-14、GS-2022-06 V33-249-9、GS-2022-08 V33-222-16、GS-2023-05 V33-249-14 / CMM-011-19、GS-2024-04 V33-222-9/11/12）；
   - 发现 GS-2023-01 CMM-007-19 的自动 TP 实为 R16 舍入区间内（改 FP_金标）；
   - 新增原因类别：`CASCADE`（“元”字单位错折算出来的重复/连带条目）、`STD_CONFLICT`、`R004`（零预算完成率）、`BIND_ERR`、`PARSE_ERR`、`PYMUPDF_DROP`、`NOTICE_ONLY`、`GOLD_MISS`/`GOLD_INCONSISTENT`（回 PDF 看系统说对了、金标可能漏标）。这些只是对误报的**归因标签**，不改变 TP/FP 的判定口径（金标为 pass 即记 FP_金标）；
   - **未采纳的做法**：没有把 CASCADE、STD_CONFLICT 等从头条误报率里剔除；剔除后的数字作为敏感性单列。
6. **O 层“命中”的定义维持文档级**（协议 §4.2.7），不按页；因此“仅待核实/未能核对”的漏报只说明系统在该份该检查上挂了“没查成”的条目，不等于定位到了那处错误。
7. **r2（2026-11-03）：金标修订后重算。** 用户批准 `gold/REVIEW_R9.md`：7 份金标改动并重新冻结（GS-2022-04、GS-2024-03、GS-2024-07、GS-2025-02、GS-2025-04、GS-2025-07、GS-2025-09），
   R26 细化（0/0 的“0%”不算缺陷）、新增 R30（泛泛原因也算写明）。**协议正文、匹配规则、指标公式一律不变**，系统运行结果不变；只重做 `extract/match/metrics`，
   结果写到 `outputs/gold_baseline_20261102/rev2/`，v1 文件不动。人工复核结论在 `scripts/gold_baseline_decisions_r2.py`（v1 的 `gold_baseline_decisions.py` 之上改 20 行）。
   v1 与 r2 并排见 `docs/GOLD_BASELINE_20261102.md` “修订版 r2”。
