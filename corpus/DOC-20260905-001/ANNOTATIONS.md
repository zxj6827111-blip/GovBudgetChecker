# DOC-20260905-001 语料标注

- 样本：上海市普陀区生态环境局 2025 年度部门决算（31 页，纯文本 PDF，官方公开样张）
- 本地来源：`C:\Users\zxj68\Desktop\3f44e78b9a5649ba8f90ed0a8fa24bbc.pdf`
- SHA-256：`113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7`
- 系统 job_id（2026-09-05 基线）：`303e3d9528da6b4777ae6b82e677e47a`（status=done，52 条 rule findings，AI 未执行）
- 基线产物：`sys_findings_baseline.json`（系统原始 52 条结果）、`sample_pdf_text.txt`（全文逐页文本）
- 标注日期：2026-09-05；标注人：人工审校（见 docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §2，v2 经 GPT-5.6 复核）

## 基线结果摘要

52/52 全部误报（0 真阳性，6 error 全假阳性）：
CMM-004×33、CMM-002×4、V33-120×4、V33-115×2、V33-117×2、V33-220×2、V33-244×2、V33-001×1、V33-106×1、V33-110×1。
硬问题召回 0/3；舍入提示 0/3；AI 未执行却被呈现为已运行；假完成（done/complete）。

## 人工标签（Golden Corpus 首个标注样本）

标签类别：`defect`（确定级缺陷）/ `rounding_hint`（舍入提示）/ `manual_review` / `acceptable`（不应产生 finding 的负例）。

| annotation_id | 类别 | rule_id | 页 | location_key | expected_severity | 证据 |
|---|---|---|---|---|---|---|
| A-001 | defect | V33-001 | 2 | toc:第三部分…202 年度部门决算情况说明 | high | 目录行年度缺位（应为 2025）「202 年度」 |
| A-002 | defect | V33-245 | 26 | sec:三公说明(一)公务接待费 | medium | 「支出决算减少为0.00 万元，与2024年持平」逻辑矛盾（2024 为 0 应称持平） |
| A-003 | defect | V33-246 | 27 | sec:三公说明(二)3 | medium | 国内公务接待批次/人次未披露（仅写外宾 0 批次 0 人次） |
| A-004 | rounding_hint | V33-202 | 10 | tbl:支出决算表合计行·项目支出 | info | 1,367.76 ≠ 分项之和 1,367.75（10.77+259.52+623.06+474.40） |
| A-005 | rounding_hint | V33-203 | 10-14 | xtbl:P14同口径表合计 | info | P14 同口径表合计 1,367.75，两表差 0.01 |
| A-006 | rounding_hint | V33-117 | 15 | tbl:310资本性支出类行 | info | 310 行 14.44 ≠ 明细和 14.43（13.94+0.49）；401.93+14.43=416.36 与公用经费合计自洽（表内自相矛盾，确定性提示） |
| A-007 | acceptable | —（T7 负例） | 28 | tbl:绩效目标表 | none | 绩效项目金额 1,354.13 vs 项目支出决算 1,367.76：绩效口径与全部项目支出允许不等，不得产生 finding |

## 主要勾稽关系（全部成立，供反证/负例用）

D-001 收支总表平衡；收入/支出决算表类款层级；三公合计=分项和；全部占比与同比；
20 个项级科目预算/决算对照；车辆 9 辆口径；政府采购 372.34=8.57+0+363.77。

## 验收口径（修复后重跑本样张）

- 旧 52 条误报清零；
- V33-001/P2、V33-245/P26、V33-246/P27 命中（页码+证据+严重级别准确）；
- P10、P10↔P14、P15 三处差异分别输出 info/manual_review/info；
- P28（A-007）不产生任何 finding；
- 结构化识别 FIN_05，ps_sync.report_type=FINAL；
- 可定位正式问题证据完整率 100%；
- legacy 任务 AI 状态如实（not_run/not_requested），不得呈现已运行。

## 2026-09-07 修订（GPT5.6 R4 P1-3 配套：锚点对齐短语）

背景：评估器升级为「零锚点命中即拒配」终版语义（跨位置候选不再晋升 TP）。
复用 R2 时的实测数据：A-003/005/006/008 四条标注的 location_key 锚短语与
规则产出文案零重叠（标注者行级措辞 vs 规则文案），按新语义会全部拒配、
样张召回跌至 3/7。经用户确认采用「标注侧补对齐短语」方案而非接受召回下降。

修订内容（原 location_key 保留不动，新增 anchor_phrases_aligned 字段）：
- A-003（V33-246 国内接待披露缺失）：补「公务接待」「国内公务接待」——
  对齐规则文案「公务接待说明未披露国内公务接待批次、人次」。
- A-005（V33-120 跨表同口径差）：补「支出决算表」「同口径」——
  对齐规则文案「支出决算表与一般公共预算财政拨款支出决算表同口径列相差」。
- A-006（V33-117 明细舍入差）：补「经济分类科目」「明细之和」——
  对齐规则文案「经济分类科目 310 与其明细之和相差」。
- A-008（V33-117 公用经费显示和差）：补「公用经费」「显式合计」——
  对齐规则文案「公用经费显式合计与类级之和相差」。

对齐短语均取自规则实际产出文案，只用于评估器的定位域约束消费
（scripts/evaluate_golden_corpus.py 的 _annotation_anchor_phrases）；
标注本体（label/rule_id/page/evidence/expected_severity）未做任何修改。

## 2026-09-08 修订（GPT5.6 R5 P1-C/P1-D：evidence 驻留对齐）

背景：评估器锚点命中搜索切换为 **evidence-only**（规则 message 是文案不是
定位证据——V33-245 的 message 模板固定含「三公说明」，把它算进命中等于
规则文案自证章节）。对齐短语必须驻留在 finding 的 evidence_text（原文引文）
中才有效，此前记录的 R4 对齐短语（取自 message 文案）全部失效。

修订内容（原 location_key 保留不动，仅改 anchor_phrases_aligned /
新增 section_phrases_aligned；标注本体 label/rule_id/page/evidence 未动）：
- A-003：aligned 改「国内公务接待」（evidence 驻留）；section_phrases_
  aligned 声明「接待外宾」「国内公务接待」（硬约束：跨章节候选拒配）
- A-004：aligned 改「合计值」（evidence「合计值：1367.76」——与 A-005
  的跨表 finding 区分）
- A-005：aligned 改「一般公共预算财政拨款支出决算表」（只在跨表 finding
  的 evidence 中）；R4 记录的「同口径列」曾抢占 A-004 的 finding，
  该历史已修正
- A-006：aligned 改「310」「明细之和」（evidence 驻留）
- A-008：aligned 改「显式合计」（evidence 驻留）
- A-002：**无** section_phrases_aligned——V33-245 的 evidence 模板是矛盾
  分句拼接，天然不含章节词；该规则的章节域由规则层 scope 限定保证
  （V33-245/246 已改为只扫「三公」主章节范围），评估器章节约束退位排序

规则层配套（同轮）：V33-245/246 章节限定——find_section_scope 提取
「三公经费…决算情况说明」主章节完整范围（含（一）（二）子章节），
其他章节的公务接待表述不再产出 finding。

## 2026-09-08 真值冻结（GPT5.6 R6 P1-4，annotation_version=2）

背景：机器 golden 曾按系统输出漂移——A-004/A-005 被改写成 V33-120（原始
手工标注是 V33-202/V33-203 跨表差）、A-005 被改 manual_review、并新增 A-008
「接住」第二条 V33-117 输出（annotation_version 仍标 1）。存在「按现有输出
塑造真值」的假绿风险。

冻结内容（golden.json v2）：
- **truth_id 独立冻结**：T1-T7 与本文件「人工标签」表一一对应，不随规则
  实现变化。T4a=支出决算表合计差（原 A-004）、T4b=跨表同口径差（原 A-005）、
  T5a=310 行明细差（原 A-006）、T5b=公用经费显示和差（原 A-008，与 T5a
  为同一 0.01 差的第二个证据面——聚类验收，命中任一即 T5 命中）。
- **allowed_rule_ids**：真值到规则映射解耦——T4a 允许 V33-202（原始
  标注口径）/V33-120（当前实现近似路径），均视为命中；后续实现演进
  不再要求改真值，只调 allowed 列表。
- 原始 severity 恢复：A-005（T4b）回到 rounding_hint/info（曾被改
  manual_review）。
- 评估器配套：defect/hint 验收按 truth_id 聚类（同一真值多证据面只要求
  命中任一面），matched 明细输出 truth_id/allowed_rule_ids/matched_rule。

anchor_phrases_aligned / section_phrases_aligned 保留为**评估基建**
（非真值本体）：短语全部为 finding evidence 的原文驻留片段，用于
evidence-only 锚点匹配；V33-245/246 的 evidence 现自带「【章节:…】」
结构化前缀（R6 P1-3），章节验证不再依赖标注猜测。

## 2026-09-09 修订（GPT5.6 R7 P0-1：真值编号纠偏，annotation_version=3）

背景：R6 冻结曾按标注顺序错位编号——T2=A-002、T3=A-003、
T4=A-004/A-005、T5=A-006/A-008。这与 HANDOFF §2 的权威编号
（确定级缺陷 T1/T5/T6、舍入提示 T2/T3/T4）不一致，导致舍入真值组
从 3 缩成 2（T4/T5 两组），评估器输出 hint 命中 2/2 的假绿——验收
标准（HANDOFF §7）明确要求舍入提示 3/3。

纠偏内容（golden.json v3，仅 truth_id，标注本体 label/rule_id/page/
evidence/expected_severity 未动）：
- A-001 = T1（P2 目录年度缺位，defect）
- A-002 = T5（P26 三公逻辑矛盾，defect）
- A-003 = T6（P27 国内接待披露缺失，defect）
- A-004 = T2（P10 支出决算表合计行差 0.01，rounding_hint）
- A-005 = T3（P10↔P14 跨表同口径差 0.01，rounding_hint）
- A-006 = T4a / A-008 = T4b（P15 310 行差与公用经费显示和差，
  同一真值的两个证据面，命中任一即 T4 命中，rounding_hint）
- A-007 = T7（acceptable 负例，不变）

配套（同轮）：
- check_gates 新增舍入硬门禁：hint_groups_total == hint_groups_hit == 3
  （两侧都必须等于 3，真值集不得缩减），并锁定硬问题 tp==3；
- 评估器新增 sec 锚标注的独立 section_id 校验（R7 P1-3）——finding
  携带结构化 section_id 时跨章节候选（如「其他重要事项说明 + 公务
  接待费」）不得晋升 TP；
- 样张复验：hint 真值命中 3/3（证据面 4/4）、TP=3 FP=0 FN=0、
  GATE-PASS。

## 2026-09-09 修订（GPT5.6 R8 P1：sec 章节锚收紧，golden.json v3 增补）

背景：评估器 sec 锚校验此前 fail-open——finding 缺失 section_id 时
退回锚点语义即可晋升 TP；章节匹配允许 2 字前缀（「九、公务管理情况
说明」仅共享「公务」两字即可命中三公真值，实测）。R8 收紧为：
sec 真值强制非空结构化 section_id + **全短语**章节锚匹配（不做前缀
宽松）。

增补内容（仅新增字段，标注本体 label/rule_id/page/evidence/
expected_severity 未动）：
- 新增 `section_title_phrases_aligned` 字段（章节标题短语，与
  section_phrases_aligned 的 evidence 驻留词职责分离）：
  - A-002：`["三公经费支出决算情况说明"]`（V33-245 finding 的
    section_id 归一后整短语包含）；
  - A-003：`["三公经费支出决算情况说明"]`（保留原 section_
    phrases_aligned 的 evidence 词不变）。
- R5 记录「A-002 无 section_phrases_aligned」依旧成立（该字段仍未
  声明）；新约束走独立字段，不再依赖 evidence 拼章节词。

配套（同轮）：
- 评估器 `--mode` 显式化（R8 P0）：shadow replay 双结果 auto 拒绝
  猜测，必须 `--mode legacy|structured`——structured 路径失败不再
  被 legacy 掩盖（样张 structured 4 findings/TP=0/FN=3 实测 GATE-FAIL）；
- 评估器校验 replay doc_id + sha256 与 golden 一致（R8 P1）——篡改
  DOC-WRONG / sha256=deadbeef 均 EVAL-REJECTED（exit 2）。
