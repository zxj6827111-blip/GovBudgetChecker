# PDF 入口整改第一批 v1.3 补充记录（2026-10-25）

本记录补充 `docs/PDF入口整改记录_批次1_20261024.md` 的验收结论。原记录保留为 v1.2 历史证据，不覆盖、不改写；本记录的结论以 2026-10-25 在 Mac 隔离工作区的实跑为准。

## 1. 状态与范围

- 第一批 IR-01～IR-10、IR-12、IR-13 已完成代码与验收包 v1.3 收口。
- 正式 compare：**57 PASS / 0 FAIL / 13 REVIEW / 3 LATER**，5 个原 FAIL 全部转 PASS。
- E1：**passed（4/4）**，`source_unchanged=true`、`postgres_removed=true`、浏览器真实 API、`page_errors=[]`。
- 本记录不自行签收；独立验收状态见第 10 节。明确通过前不启动第二批。
- 第二批 IR-11/IR-14、第三批 IR-15～18 尚未开始。

## 2. 起点、版本与验收包

- 权威原工作区：`/Volumes/workSSD/software development/GovBudgetChecker`，分支 `codex/zcode-remediation`，历史比较基线 `6d62a7c0`。
- 隔离实施工作区：`/Volumes/workSSD/software development/GovBudgetChecker/artifacts/pdf-intake-r12-20261024/implementation`，分支 `codex/pdf-intake-r12-20261024`。
- 被测代码 HEAD：`542d8123e690f7716911a4b1788077427a9a4309`；验收起点 `311bdb7c`；后续本记录单独提交，不改变产品源码。
- 验收包 v1.3（用户明确授权按验收方口径修订）：
- `verify_intake_remediation.py`: `c8267d3bf31f791ded3507c7021a07e2509b59950fecf77f29a09009c3b4d034`
- `intake_remediation_cases.json`: `d184f0f118dae3d4deadb9487295fce9ea08aa571d83d40a2a37c016957e31a0`
- 旧 v1.2 对应哈希：tool `5915b24d7d5f79c5addcb61ff89780855bc87fa76e1941c76e8ed74d83c7b035`；cases `2772a574a4beed841cc74d279bd017dc27249f8b5a1ba40f290749a1f3f62aa9`
- 完整 12 项哈希见 `outputs/intake_remediation_20261024/acceptance_kit/SHA256SUMS`；v1.2 旧版归档在 `acceptance_kit/R1.2/`
- 验收资产仍不随代码提交：`scripts/verify_intake_remediation.py`、`scripts/intake_remediation_cases.json`、`scripts/intake_remediation_t0.json`、`scripts/intake_remediation_e1.json`；其版本以验收包 SHA256SUMS 为准。
- 代码提交：
  - `22cf12b7` IR-01 以完整封面名绑定并把目录漏字作为非实质冲突记录
  - `542d8123` IR-07 归一父行名换行并按双边占位与空表回执避免未知金额误判
  - 本记录为 docs-only 提交，另列于交付清单。

## 3. 验收包 v1.3 变更（用户授权）

只改以下三项，其他样本、阈值、真值和比较逻辑均未改动：

1. **A2 三例改为可待确认（`reason_in=evidence_conflict`）**：上海市生物医药产业促进中心 2022、上海市城市数字化转型应用促进中心 2023、上海市青浦监狱 2024。封面明确写“部门决算”但组织库只有 unit，按用户已确认的层级冲突保持待确认，不强绑。
2. **A4 四例临港裸名材料从 must_bind 移到 `name_variants_expected_pending`**：2022/2023/2024/2025 的“年度决算.pdf”（不含“部门决算.pdf”版本）。封面只有“年度决算”，无部门/本级口径；要求未绑定且原因含 `scope_undetermined`。A4 必须绑定例由 19 降为 15，另增 A4b 检查这 4 例。
3. **B4 综合执法大队 2023 (2) 更正为非预决算材料**：原件第 1 页明确为“2023年度国有资产管理情况”，共 4 页；加入 `profile.non_standard` 并在 `profile.non_standard_notes` 记录原件依据。

旧版 v1.2 已归档到 `acceptance_kit/R1.2/`（tool、cases、旧 SHA256SUMS 与 README），可回溯。

## 4. 代码修复

### 4.1 A2b：完整封面名与目录漏字

- `src/services/org_name_identity.py` 新增 `has_name()`：判断一个值是否精确匹配目录条目（含已登记别名）。
- `src/services/org_binding_decision.py`：当封面（或封面标签）与候选精确等价、目录/部分标题只差 1～2 个字且不匹配任何其他目录组织时，漏字**不作为实质冲突**；绑定证据仍只取精确匹配，漏字写入 `original_text_inconsistencies`，不补字、不参与名称等价。
- 真实结果：发展研究中心（本级）2022 绑定到唯一单位；另外两例同模式的“上海市监狱管理局机关 2022/2023”也转为绑定（目录标题写作“上海市管理局机关”，漏“监狱”），逐条记录在 `binding_decision.original_text_inconsistencies`。
- 本轮新绑定 3 例，未出现新解绑或改绑；A5b 86→83、A6 164→161、A7 2181→2184，T0-B 待确认 1→0。

### 4.2 D 鲁迅 2023：不启用 U-5 的通用修复

旧记录里的 `luxun_diagnostic_5804` 是历史诊断；本轮用当前代码重跑隔离全管线，初始仍有 10 条未完成：

- V33-119：第 22/24 行（灾害防治、抗疫特别国债）的 6 个操作数缺失。根因不是续页没合并（第 11 页已并入 FIN_04），而是父行名含换行，`select()` 只归一了事实名、没有归一传入的 `name`。
- V33-122：双边总表收入侧的结构空白被 `strict_unknown_values` 当成未知金额。收入和支出两套标签列位于不同列，支出侧有标签和金额时，收入侧的空白是版式占位。
- 另有 7 条按设计依赖材料汇总口径（`org_scope` 待确认），继续未完成；V33-222 的旧科目名映射继续未完成。二者都不为通过而填口径。

修复：

1. `src/engine/native_table_arithmetic.py::select`：对传入的 `name` 做与事实名相同的空白归一。
2. `src/engine/fact_table_projection.py`：新增 `_bilateral_blank_padding()`，仅在两侧使用不同标签列时，把“本侧无标签、另一侧有标签且有金额”的空白识别为结构占位；本侧有标签的空白仍按未知处理。空白检查先看占位，再判未知。
3. `check_fund_zero_disclosures()`：对应基金表存在、无可确认金额事实，且总表资金列明确为零时，按空表通过并出回执；表不存在或无事实但有未确认读取缺口时仍取数不足。

隔离实测：**10 → 9（修 V33-119）→ 8（修空表回执）**，8 < 基线 9，D 项通过。**未启用 U-5，未修改 D 阈值，未改验收真值。**

## 5. 最终验收

- compare 汇总：**57 PASS / 0 FAIL / 13 REVIEW / 3 LATER**（完整输出见附录 A，原始日志 `outputs/intake_remediation_20261024/compare_batch1_v13.log`）。
- 关键数字：
  - 进入规则金额格 1,056,986（基线 1,054,413）
  - 待复核金额格 9,281（基线 11,760）
  - 解析耗时中位数 ×1.10（上限 ×1.25）
  - T0 全部 PASS；A2/A2b/A4/A4b/B4/D 全部 PASS
- 工程门禁：
  - `.venv/bin/python -m pytest -q` → `4136 passed, 189 skipped, 20 warnings`，0 失败；共 4,325 条（新增 6 条测试，无删除、无新增 skip/xfail）
  - `.venv/bin/python -m ruff check .` → 0，All checks passed
  - `.venv/bin/python -m mypy api src tests` → 0，485 files no issues
- E1：
```json
{
  "status": "passed",
  "source_head": "542d8123e690f7716911a4b1788077427a9a4309",
  "source_unchanged": true,
  "postgres_removed": true,
  "browser_passed": true,
  "browser_real_api": true,
  "browser_page_errors": [],
  "samples": [
    {
      "id": "E1-S",
      "job_id": "e5679e2cf944f476f39808d3cdfad821",
      "analysis_revision": "rev-20261025T045457-ca090f40"
    },
    {
      "id": "E1-C",
      "job_id": "a4c3e7c0914678e9fb87f99aa0485f9c",
      "analysis_revision": "rev-20261025T045459-7bc11a76"
    },
    {
      "id": "E1-R1",
      "job_id": "1d1b1b173482c68e3d2195d08fbd2097",
      "analysis_revision": "rev-20261025T045501-c4441084"
    },
    {
      "id": "E1-R2",
      "job_id": "b56ae711b0580e695a71ba758a4d5360",
      "analysis_revision": "rev-20261025T045511-0cd55756"
    }
  ],
  "limits": [
    "仅验证冻结 E1 样本，不代表全语料准确率",
    "不启用生产 U-5 空白计零约定"
  ]
}
```
  证据目录 `outputs/intake_remediation_20261024/e1_batch1_v13/`；四张页面截图与 receipt、browser-receipt、code-fingerprints 均保留。

## 6. 13 个 REVIEW 的逐例说明

| 项 | 本轮实际 | 说明 |
|---|---|---|
| A5b | 83 份 | 与文件名等价的绑定转为待确认；v1.2 为 86，本轮减少的 3 份正是 4.1 新绑定的 3 例。其余逐例原因沿用 v1.2 附件。 |
| A6 | 161 份 | 效率目标 ≤40 **未达标**；v1.2 为 164，减少 3 份。原因仍是组织目录重复/缺失、口径待定、扫描件无文字层等；逐例见证 `batch1_review_closure` 与 v1.2 附件。 |
| A7 | 2184 份 | 仅观察；比 v1.2 多 3，正是新绑定的 3 例与文件名主体一致。 |
| B2 | 1 份 | 水生野生动植物保护研究中心 2025；封面主体恢复为原件完整名，已渲染核实。 |
| C3 | 43 份 | 单份待复核数变差；本轮 parse 的 C1/C2 与 v1.2 完全一致，43 份明细与 v1.2 **逐条相同**（已脚本比对 detail），沿用原逐例说明。 |
| C4 | 1 份 | 规划展示馆 2023 的 4 个旧指纹从 FIN_01 迁到 FIN_04；金额与原页证据保留，不是事实丢失；detail 与 v1.2 相同。 |
| D 对照 1～6、8 | 与基线有差异 | 8 个对照中 7 个为 REVIEW；本轮 detail 与 v1.2 **逐条相同**（已脚本比对），沿用 v1.2 的逐例解释；对照 7 仍 PASS。 |

A6 是真实效率缺口，不是硬性失败；不把 REVIEW 当作 PASS。A5b/A6/A7 的变化全部由 4.1 的 3 例绑定解释，无未解释的新增。

## 7. 未运行与边界

- 未启用生产 U-5（空白/横线按零）；未执行组织库正式清洗 `--apply`、缺失单位补录或层级确认（U-2）；未执行真实任务重匹配、历史任务重分析或生产部署。
- 未实施第二批 IR-11/IR-14、第三批 IR-15～18；未做真实 OCR 全量、完整逐格真值集、PDF/Word 导出格式验证。
- E1 只验证冻结的 4 个样本，不代表全语料准确率；快照/存储一致性通过不等于源准确率。
- 本轮没有修改 `data/` 真实数据、原工作区和原服务；回滚无需数据操作，不采用该隔离分支即可。

## 8. 交付物与哈希

- 验收报告：`outputs/intake_remediation_20261024/batch1_v13/acceptance_report.json`
- compare 全文：`outputs/intake_remediation_20261024/compare_batch1_v13.log`
- 采集数据：`batch1_v13/intake.jsonl.gz`、`parse.jsonl.gz`、`e2e.jsonl.gz`、`t0.jsonl.gz`、`meta.json`
- 证据与源码指纹：`outputs/intake_remediation_20261024/batch1_v13_provenance.json`
- 测试/静态检查：`pytest_batch1_v13.log`、`ruff_batch1_v13.log`、`mypy_batch1_v13.log`
- E1：`e1_batch1_v13/receipt.json`、`browser-receipt.json`、`code-fingerprints.json`、四张 `E1-*-task.png`
- D 隔离诊断：`luxun_diag_current/`（当前 8 条）、`luxun_diag_current_after_v119/`（9 条）、`luxun_diag_current_before_fix/`（10 条），均为诊断，不替代正式 e2e/compare 证据。
- 交接包：`outputs/intake_remediation_20261024/PDF入口整改_批次1_v1.3_交接包_20261025.tar.gz`，外部同名 `.sha256` 为权威校验；包内另有 `SHA256SUMS` 与 `SOURCE_HEAD.json`。

## 9. 提交映射

```text
22cf12b7 IR-01 以完整封面名绑定并把目录漏字作为非实质冲突记录
542d8123 IR-07 归一父行名换行并按双边占位与空表回执避免未知金额误判
（本记录：docs-only 提交）
```

## 10. 独立验收状态

**待独立验收。** 本记录只记录执行方实跑结果，不自行签收；独立验收子代理按验收包 v1.3 复核代码差异、kit 哈希、compare 57/0/13/3、E1 4/4 与 13 个 REVIEW 的逐例说明。明确通过前不启动第二批。

## 附录 A compare 全文

```text
[PASS  ] A0 预检无异常                        期望 0 份报错  实际 0 份
[PASS  ] A1 自动绑定到市/区级根节点                 期望 0 份  实际 0 份（基线 34）
[PASS  ] A2 已知错绑样本                       期望 107 例全部符合期望  实际 0 例不符
[PASS  ] A3 冲突检查字段契约一致                   期望 0 份不一致  实际 0 份
[PASS  ] A4 写法差异不得误拒                     期望 15 例仍能绑定  实际 0 例未绑定
[PASS  ] A4b 写法差异但口径不足的样本保持待确认           期望 4 例按 A2 口径待确认  实际 0 例不符
[PASS  ] A5a 与文件名等价的绑定发生改绑               期望 观察；有则逐例给出封面依据  实际 0 份
[REVIEW] A5b 与文件名等价的绑定转为待确认              期望 观察（效率）；逐例说明原因  实际 83 份
[REVIEW] A6 未自动绑定数（效率目标）                 期望 目标 ≤ 40（组织库清洗后）；超出逐例说明  实际 161（基线 113）
[PASS  ] A6r 待确认的原因与证据                   期望 每份未绑定材料的 binding_decision 符合冻结结构，原因码合法、证据可追溯（自动核对部分）  实际 0 份不合规
[PASS  ] A2b 部门/本级绑定边界                   期望 120 例符合期望：{'benji_unique_must_bind': 64, 'benji_tie': 49, 'benji_evidence_conflict': 1, 'benji_missing_org': 6}  实际 0 例不符
[REVIEW] A7 绑定结果与文件名主体等价                 期望 仅报告，逐例复核差异  实际 2184（基线 2180）
[PASS  ] B1 首页有字但封面主体为空（效率目标）            期望 目标 ≤ 10；超出逐例说明  实际 10（基线 55）
[REVIEW] B2 基线已取到的封面主体不变                 期望 仅报告，逐例复核  实际 1 份变化
[PASS  ] B3 非预决算材料不按预决算处理                期望 7 份均非 budget/final  实际 0 份仍判为预决算
[PASS  ] B4 标准决算文种不回退                    期望 0 份  实际 0 份
[PASS  ] B5 年度与目录年份冲突                    期望 观察；有则逐例核对原件  实际 0 份
[PASS  ] B6 混合文种标题待确认                    期望 文种待确认、年度不自动取值  实际 {"report_kind": "unknown", "report_year": null, "profile_status": "unresolved", "conflicts": ["report_kind"]}
[PASS  ] C0 解析无异常                        期望 0 份报错  实际 0 份
[PASS  ] C1 进入规则的金额格                     期望 不低于基线；下降须逐例说明  实际 1056986（基线 1054413）
[PASS  ] C2 待复核金额格                       期望 不高于基线；上升须逐例说明  实际 9281（基线 11760）
[REVIEW] C3 单份文件数量变差                     期望 0 份；有则逐例说明  实际 43 份
[REVIEW] C4 基线可用事实丢失                     期望 0 份；有则逐例说明（独立核对集 T0 内的事实丢失直接判失败）  实际 1 份
[PASS  ] C5 整份事实因身份不完整被挡                 期望 0 份；有则逐例给出口径无法确定的原件依据  实际 0 份（基线 3）
[PASS  ] C9 算术门禁降级                       期望 0 份；有则逐例给出独立结构证据  实际 0 份（基线 5）
[PASS  ] C6 解析耗时中位数                      期望 ≤ 基线 × 1.25  实际 × 1.10
[LATER ] C7 [第二批] 交通委2022 重复框线           期望 9 列恢复、续页接上；基线 admitted 331 / pending 516  实际 admitted 331 ≥ 590；pending 516 ≤ 20；UNKNOWN 页 [11, 12]
[LATER ] C7 [第二批] 材料工程学校2022 重复框线        期望 基线 admitted 468 / pending 320  实际 admitted 468 ≥ 565；pending 320 ≤ 10
[PASS  ] C7 交通委2024 口径兜底                 期望 组织库层级兜底部门口径；基线 1,343 个事实全部因身份不完整被挡  实际 admitted 1343 ≥ 1300；identity_incomplete 0 ≤ 0
[PASS  ] C7 鲁迅纪念馆2023 三公表资金口径            期望 表题缺字时资金口径取自认定的表种；基线 12 个三公事实因 fund_scope 为空被挡  实际 identity_incomplete 0 ≤ 0
[PASS  ] C7 华东政法大学2025 原件多处勾稽错误不得降级      期望 原件明细行确有系统性勾稽错误（已渲染原页逐份核实），不得当作列映射错误降级；基线降级 211 格  实际 算术降级 0 ≤ 0
[PASS  ] C7 龙华烈士陵园2025 原件多处勾稽错误不得降级      期望 原件明细行确有系统性勾稽错误（已渲染原页逐份核实），不得当作列映射错误降级；基线降级 147 格  实际 算术降级 0 ≤ 0
[PASS  ] C7 航宇科普中心2023 原件多处勾稽错误不得降级      期望 原件明细行确有系统性勾稽错误（已渲染原页逐份核实），不得当作列映射错误降级；基线降级 112 格  实际 算术降级 0 ≤ 0
[PASS  ] C7 上海应用技术大学本级2022 原件多处勾稽错误不得降级  期望 原件明细行确有系统性勾稽错误（已渲染原页逐份核实），不得当作列映射错误降级；基线降级 60 格  实际 算术降级 0 ≤ 0
[PASS  ] C8 规划展示馆2023 表题缺“财”             期望 第8页 = FIN_04_fiscal_grant_total  实际 FIN_04_fiscal_grant_total
[PASS  ] C8 戒毒康复中心2023 表题缺“政”            期望 第13页 = FIN_08_gov_fund  实际 FIN_08_gov_fund
[PASS  ] C8 鲁迅纪念馆2023 表题缺“财”             期望 第15页 = FIN_07_three_public  实际 FIN_07_three_public,UNKNOWN_P15_T0
[PASS  ] D 驻西宁办事处2025 口径待定时表内检查照常执行      期望 按规则声明身份维度后，表内勾稽不因汇总口径待定而停止；取数不足规则数低于基线  实际 符合
[PASS  ] D 中小企业发展服务中心2025 口径待定时表内检查照常执行  期望 按规则声明身份维度后，表内勾稽不因汇总口径待定而停止；取数不足规则数低于基线  实际 符合
[PASS  ] D 规划展示馆2023 表题缺字不得误报缺表          期望 按结构认定为 FIN_04；保留表名缺字问题且期望表名正确；不报缺表  实际 符合
[PASS  ] D 戒毒康复中心2023 表题缺字不得误报缺表         期望 按结构认定为 FIN_08；表名缺字照报；不报缺表  实际 符合
[PASS  ] D 鲁迅纪念馆2023 三公表题缺字              期望 三公表进入规则；表名缺字照报  实际 符合
[PASS  ] D 交通委2022 缺失分项不按0、三公表不误报缺表      期望 缺失分项不按 0 参与勾稽；2022 版三公表名照报命名问题但不报缺表；原件标题漏“年”字照报  实际 符合
[LATER ] D [第二批] 交通委2022 列恢复后检查覆盖提升      期望 9 列恢复后取数不足的规则数低于基线（27 条）  实际 取数不足规则 34 条，未低于基线 27
[PASS  ] D 交通委2024 口径兜底后九表进入检查           期望 1,343 个金额进入规则；目录/正文“部门”字样不一致照报  实际 符合
[PASS  ] D 绩效自评表不按决算九表检查                 期望 识别为非预决算材料，不报九表缺失、页数过少  实际 符合
[PASS  ] D 华东政法大学2025 收入决算表勾稽错误须报出       期望 收入决算表明细行勾稽不平作为问题报出：明确数值的为 error/warn；涉及空白格且 U-5 未批准的，以 manual_review 报出差额与空白格，不得只记取数不足  实际 符合
[PASS  ] D 龙华烈士陵园2025 收入决算表勾稽错误须报出       期望 收入决算表明细行勾稽不平作为问题报出：明确数值的为 error/warn；涉及空白格且 U-5 未批准的，以 manual_review 报出差额与空白格，不得只记取数不足  实际 符合
[PASS  ] D 航宇科普中心2023 收入决算表勾稽错误须报出       期望 收入决算表明细行勾稽不平作为问题报出：明确数值的为 error/warn；涉及空白格且 U-5 未批准的，以 manual_review 报出差额与空白格，不得只记取数不足  实际 符合
[PASS  ] D 文旅公共服务和人才服务中心2022 表头缺失（反向用例）  期望 原件表格本身是平的，第二列表头漏印“财政拨款收入”：有结构证据，可以降级；不得报出错误级勾稽结论；表头缺失作为原件问题报出  实际 符合
[PASS  ] D 上海应用技术大学本级2022 收入决算表勾稽错误须报出   期望 收入决算表明细行勾稽不平作为问题报出：明确数值的为 error/warn；涉及空白格且 U-5 未批准的，以 manual_review 报出差额与空白格，不得只记取数不足  实际 符合
[PASS  ] D 扫描件不得报缺表 上海市农业生物基因中心2022年     期望 页面未可靠读取时报告检查未完成，不报“缺失表”  实际 符合
[PASS  ] D 扫描件不得报缺表 上海市农产品质量安全中心2022     期望 页面未可靠读取时报告检查未完成，不报“缺失表”  实际 符合
[REVIEW] D 对照1 上海市文化旅游市场质量监测和服务中心2025年度  期望 与基线一致或差异有合理解释  实际 消失 6 新增 4
[REVIEW] D 对照2 上海市水旱灾害防御技术中心2022年度决算.pd  期望 与基线一致或差异有合理解释  实际 消失 1 新增 0
[REVIEW] D 对照3 上海市文化旅游市场质量监测和服务中心2022年度  期望 与基线一致或差异有合理解释  实际 消失 3 新增 1
[REVIEW] D 对照4 上海市商务发展研究中心2023年决算.pdf    期望 与基线一致或差异有合理解释  实际 消失 2 新增 1
[REVIEW] D 对照5 上海市国有企业绩效评价中心2025年度决算.pd  期望 与基线一致或差异有合理解释  实际 消失 4 新增 3
[REVIEW] D 对照6 上海市土地交易事务中心2022年度决算.pdf   期望 与基线一致或差异有合理解释  实际 消失 2 新增 1
[PASS  ] D 对照7 上海市动物疫病预防控制中心2023年度决算.pd  期望 与基线一致或差异有合理解释  实际 消失 0 新增 0
[REVIEW] D 对照8 上海市司法鉴定中心2024年度决算.pdf     期望 与基线一致或差异有合理解释  实际 消失 8 新增 8
[PASS  ] T0-B 封面核实样本绑定正确                 期望 30 份不得绑错（转待确认计入效率）  实际 绑错 0 份，待确认 0 份
[PASS  ] T0 T0-C01 重复框线错列                期望 no_wrong_admission  实际 符合（未进入规则 9 格，已标待复核）
[PASS  ] T0 T0-C02 原件多处勾稽错误              期望 exact  实际 符合（未进入规则 0 格）
[PASS  ] T0 T0-C03 原件多处勾稽错误              期望 exact  实际 符合（未进入规则 0 格）
[PASS  ] T0 T0-C04 原件多处勾稽错误              期望 exact  实际 符合（未进入规则 0 格）
[PASS  ] T0 T0-C05 原件多处勾稽错误 + 空白格        期望 exact  实际 符合（未进入规则 0 格）
[PASS  ] T0 T0-C06 原件表头缺失                期望 unlabeled_not_admitted  实际 符合（未进入规则 0 格）
[PASS  ] T0 T0-X01 整数金额（元）               期望 read_or_flag  实际 符合（未进入规则 3 格，已标待复核）
[PASS  ] T0 T0-X02 横线与空白                 期望 read_or_flag  实际 符合（未进入规则 0 格）
[PASS  ] T0 T0-X03 无框线整数表（页面探针看不到整数）     期望 read_or_flag  实际 符合（未进入规则 11 格，已标待复核）
[PASS  ] T0 T0-S01 整份扫描（无 OCR 结果）        期望 检查未完成、不显示通过、不报缺表  实际 符合
[PASS  ] T0 T0-S02 整份扫描（无 OCR 结果）        期望 检查未完成、不显示通过、不报缺表  实际 符合
合计: {'PASS': 57, 'REVIEW': 13, 'LATER': 3} → outputs/intake_remediation_20261024/batch1_v13/acceptance_report.json
```
