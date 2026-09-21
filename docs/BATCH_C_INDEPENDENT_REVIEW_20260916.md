# 批次 C（C01～C03）独立终验报告

审查时间：2026-09-15 至 2026-09-16。证据目录沿用本轮启动日期：`outputs/batch_c_independent_review_20260915/`。

**结论：NO-GO，保持暂停点三。** 修后重跑和 C02 技术门禁通过；C01 逐项差异裁决、C03 拟提交分组及交付一致性未完成。不能按现有材料宣布 C 阶段全部通过。本结论不撤销批次 B 的 R11 放行，也没有认定存在新的业务代码回归。

## 1. 验收依据与独立实测

依据 `docs/GEMINI_IMPLEMENTATION_PACKET_V4_20260914.md` 第 83～85、146～151 行：逐项登记新增/消失/严重度/证据/未完成差异，按业务对象及证据匹配；记录双方位置、原因、裁决状态；未独立核实不标为最终真值；交付依赖完整的拟提交分组，真实材料未裁决差异明确保留未决。

| 核验项 | 本轮独立结果 |
|---|---|
| 固定清单及输入指纹 | 8 份决算、33 份真实预算、1 份负例，共 42 份，PDF 均存在且 SHA 与 A03 冻结底账一致 |
| 当前源码真实重跑 | 42 份重新抽取、执行规则；逐份 findings、summary、页数及 PDF SHA 与 C 交付物完全一致，0 差异 |
| 全量 pytest | 1240 passed、1 skipped、22 warnings，95.78 秒 |
| Ruff：src/api/scripts/tests | All checks passed |
| Mypy：src/api | 104 source files 通过 |
| 新鲜 legacy Golden | TP=3、FP=0、FN=0；hint 3/3，证据面 4/4；GATE-PASS |
| 累计探针 | 独立目录重新执行，45 项与 R11 逐键一致，相关源码/测试指纹也一致 |
| C manifest 指纹 | 所列 18 个文件当前 SHA 全部匹配 |
| Golden 真值 | SHA 为 `611aeaebe31ba6e785db5f8d263b1c90434de9c0ee16a38479cd65309b24bcc9`，与冻结值一致 |
| budget_rules.py | SHA 为 `c96b5fefe6e5ee19d7c585df7ce36bf6d9ead90274b7c0a569fa01e8da0ee60c`，与 R11 一致 |

独立运行环境为项目 `.venv/Scripts/python.exe`，Python 3.12.14；交付 manifest 记录的是 Python 3.14.3，两者需分别保留，不混称同一环境。pytest 使用工作区 TEMP/TMP、禁用 cacheprovider。唯一 skip 已单独重跑确认：`tests/test_pdf_parse_isolation_and_backup.py:254`，Windows 无 RLIMIT_AS，不适用该内存硬上限测试；这不是新增跳过，但交付包应说明平台边界。

重跑相同证明可复现，不等于每份 PDF 业务真值已审定；单份 legacy Golden 的通过也不能外推全材料零漏报。本轮没有对 41 份真实材料逐页进行视觉业务裁决。

## 2. C-P1-1：修前重复计数，差异匹配未满足业务对齐要求

位置：`scratch/run_batch_c_sample_replay.py` 的 `normalize_key` 和修前 findings 转换/映射逻辑；`outputs/remediation_v5/batch_c/diff_review.json`、`manifest.json` 的汇总。

修前 `issues` 同时保存分类列表和 `all`。脚本遍历全部列表，使每条记录被计入两次。按 `issues.all` 只计一次，修前为 **247**，也与冻结底账的 `finding_counts.all` 累加相符；修后为 **253**，净变化 **+6**。交付包的 **494→253、净 -241** 不成立。该数值是输出记录数，仍不等于独立业务问题数。

现有机器比较列出新增 110、消失 104、严重度变化 7、未变化 136；110−104=6，与报告的 -241 不守恒。不能一边把重复记录算进总量，一边在字典中合并后宣称逐项核实完成。

匹配键使用 `rule|page|message[:60]|evidence[:100]`：含展示编号和可变文案，截断原文，并通过字典赋值静默覆盖同键记录。已找到确定的错误拆分：`dept_budget.pdf` 第 9 页 BUD-111，业务金额、规则、页码、完整证据相同，仅中文/英文冒号及空格变化，仍被分为一次消失和一次新增。BUD-106 的展示编号变化也产生类似候选拆分。

修前采用展示层问题，修后采用原始 finding 序列化，还出现 `error→high`、`warn→manual_review` 等不同严重度表示。现有 7 条不能直接当作业务严重度变化结论。报告未单列 evidence_changes，部分修前回填到证据的 message 与修后空证据也缺少明确解释。

**整改验收条件：**

- 修前取唯一权威列表一次；对修前/修后使用一致字段和严重度口径，并保留原始值。
- 以 PDF SHA、规则、表/行/字段/期间等业务对象和来源位置关联；展示编号、标点只用于候选归一化，不把短字符串当唯一业务标识。
- 多候选、键冲突明确输出待裁决，不覆盖；保留完整双方证据及证据变化。
- 分别校验修前、修后记录数守恒；当前输入未变化时记录总量应为 247→253。新增/消失/变化数须在正确匹配后重新生成，不能照抄 110/104。

## 3. C-P1-2：缺少逐项裁决和未决清单，结论超出证据

`diff_review.json` 当前提供机器分类与 unresolved 列表，但没有逐项原因、业务判断、裁决状态、充分的双方来源对应及待审清单。`handoff.md` 因而不能声称“所有已确认真实业务缺陷均完整保留”“275 处精准转入”“彻底杜绝 false-pass”。

275 是本批材料的规则未完成记录数：决算原 5 份 66、补充 3 份 41、真实预算 33 份 155、负例 1 份 13，即真实材料 262 加负例 13。它不是 275 个修复代码分支，更不是覆盖率证明。需区分正确暴露的缺输入、适用范围待确认、解析受限以及其他未决原因。

具体待解释例：`上海市普陀区规划和自然资源局 2024 年度部门决算.pdf` 的旧 V33-220 第 20 页记录消失；旧证据为说明项目支出 4381.74，表行候选 `[9759.38, 5377.64]`。现有材料未给出它是旧误报纠正、转为未完成，还是其他状态变化的业务裁决。**此例不构成已确认的新漏报**，不能仅凭旧告警或新输出决定真值。

**整改验收条件：** 对重新匹配后的差异逐项补充业务对象、旧/新值或状态、双方位置/原文、变化原因、裁决状态。明确关联 finding 与对应未完成状态，核实已确认问题在 partial 情况下的保留。无法判定的真实材料允许按 C03 明确保留 `pending_review`，写清原因及影响，不需要为了通过把 unresolved 清零，也不得把未裁决问题标成已修复或最终真值。

## 4. C-P1-3：C03 缺少依赖完整的拟提交分组

handoff 将“拟提交分组和证据包”标 PASS，但提交目录中未提供具体分组、文件清单及依赖顺序；manifest 的 18 个关键指纹不是完整提交清单。

当前工作树同时有源码修改、新增 `src/engine/field_extractor.py`、新增共存与批次回归测试，以及大量 scratch/历史 pytest 目录。仅按 tracked files 提交会漏掉依赖，批量加入全部未跟踪文件也无法保证范围。

**整改验收条件：** 提供明确的拟提交文件清单及依赖顺序（可合为一个依赖完整的实现组，不强求拆分）；列出必要实现/测试/适配/生成器/文档与证据的去向、排除项及理由、每组验证依据。被 Git 忽略的证据应有明确归档引用和指纹。该阶段仅拟定分组，继续等待独立审核，不执行实际提交或 push。

## 5. C-P2-1：交付文档与指纹、基线及环境记录不一致

- manifest 的 18 个指纹全部正确，但 handoff 中 `rule_outcome.py`、`pipeline.py`、`engine_rule_runner.py`、`rule_process.py`、`tests/test_batch_b_remediation.py` 五项 SHA 与当前文件不符。完整 claimed/actual 对照见 `package_audit.json`。这是交付文档陈旧，不能据此指称代码被篡改。
- C manifest 把 `6438029...` 写为 baseline；A manifest 已区分实际 baseline `63735c0` 与 reference `6438029...`，应延续该区分。
- 生成器硬编码 `pytest: 9.0.2 / 9.0.3` 和门禁结果字符串，缺乏单次命令、实际解释器/工具版本与产物对应；应依据实际运行更新，不用候选版本字符串代替实测版本。
- 用户提及的 walkthrough.md 未在提交的 batch_c 目录中找到；若作为验收依据，补充可访问路径。解释唯一 skip，并同步所有 PASS 状态及结论，不能保留过度承诺。

## 6. 返回实施方的最小工作范围

1. 修正 C01 比较生成器与统计口径，基于已验证的 42 份结果重建差异报告；若输入/代码未变，无须为纯报表修正反复运行耗时 PDF 重放。
2. 完成逐项来源、原因、裁决/未决状态，收回“全部消除/全部保留”的无依据表述。
3. 补齐 C03 拟提交分组、统一指纹与环境记录、skip 说明和证据引用。
4. 交回 C01/C03 复核；若修改业务代码或测试基线，则重新运行对应门禁。

本轮仅新增独立核验脚本、证据及本报告，没有修改业务源码、Golden 真值或覆盖既有 C 交付物，未 commit、push、访问 NAS 或部署。继续暂停点三，直到上述终验缺口闭环。

## 7. 独立证据索引

目录：`outputs/batch_c_independent_review_20260915/`。

- `verification_summary.json`：42 份一致性、45 项探针对照、材料分组和实际 Python。
- `package_audit.json`：输入指纹、权威计数、差异结构、交付 SHA 不一致明细。
- `delta_examples.json`：文案/编号造成的机器差异和 V33-220 未裁决例。
- `replay_comparison.json`、`after/`、`replay.log`：独立真实重跑与逐份对照。
- `pytest.log`、`skip_reason.log`、`ruff.log`、`mypy.log`：独立门禁。
- `golden_replay.log`、`golden_eval.log`：本轮新鲜 Golden 产物路径及评估结果。
- `probe.py`、`probe_results.json`、`probe.log`：复制到独立目录运行的 R11 累计探针。
- `git_status.txt`、`audit_package.py`、`inspect_deltas.py`、`replay_verify.py`、`finalize_audit.py`：工作树快照和可复核工具。
