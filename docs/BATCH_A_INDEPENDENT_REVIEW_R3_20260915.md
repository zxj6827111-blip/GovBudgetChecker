# 批次 A 第三轮独立复验（2026-09-15）

**结论：仍暂不放行 B/C，但只剩 A01 迁移底账这一项阻塞。** 第二轮提出的 R2-1（真实共存边界）与 R2-3（pre-A 隔离基线）本轮通过。无需再次重做已经通过的底层机制、备份或样本整理。

## 本轮已通过

- **真实进程共存载荷：** 新的可选 `rules` 参数从 `run_rules_in_process` 经 worker 传到 pipeline；未传参仍使用原有规则选择。测试在 Windows 实际 spawn 子进程执行可序列化的共存规则，精确断言指定 finding、证据、partial_findings_total、未完成规则及原因；质量门必须为 review_required/incomplete。实际运行的全量测试包含这两个修改后的边界测试。
- **实际文件存取边界：** 测试经过项目 `api.runtime.write_json_file`/`read_json_file`，不再只是手写 JSON 往返。经过存取后严格检查共存状态与质量门。本结论限该结果传输/文件边界，不是前端或数据库端到端验收。
- **pre-A 基线证据：** 独立检查 `C:\Users\zxj68\AppData\Local\Temp\govbudget_pre_a_baseline_run`，HEAD=`63735c04a2e489dae57ab02cda9c469e6608eec7`，`git status --short` 为空。完整 pytest 日志的 rootdir 指向该隔离目录，1145 passed/1 skipped；Ruff、mypy 日志通过，187 个源文件；指定 Golden replay/eval 文件在该目录实际存在，日志 GATE-PASS。A04 明确说明是复验期间补建的 pre-A，与 post-A 分账。本轮核验这些原始证据，没有再次运行整套 pre-A 测试。
- **当前源码指纹：** manifest 的 8 个源码/测试文件 SHA 与当前文件一致，新增 rule_process.py 变更已纳入指纹。当前实际是 6 个已跟踪修改文件；handoff/A04 中个别“5 个”描述应随下次交付更正，不单独阻塞。
- **独立全量复跑：** `python -m pytest -q -p no:cacheprovider` → **1159 passed、1 skipped、22 warnings，101.75 秒，退出码 0**；Ruff 退出码 0；mypy 退出码 0，189 source files。TEMP/TMP 指向仓库内 tmp。
- **独立新鲜 Golden 回放：** 当前代码重新解析 PDF、执行 legacy，再评估新生成文件，**TP=3、FP=0、FN=0，GATE-PASS**。提示命中 3/3，severity/page/evidence 均为 1.0。实施方 post-A 日志仍引用旧 replay，本次新鲜回放已独立补证当前结论；后续不要继续把旧 replay 当新回放。

## 唯一阻塞：A01 仍不是可直接用于 B 的可靠迁移底账 [P1]

下面三类错误属于同一项底账质量问题，不是扩大任务范围。A01 的要求一直包含真实分支、必要字段缺失和明确的迁移裁决。

### 1. 记录了不存在的业务分支

`scratch/build_a01_comprehensive_ledger.py` 对 BUD-105 手写：

```text
1340: if t1_rows and t3_rows:  T1 vs T3 支出总计勾稽
```

但 `src/engine/budget_rules.py:1340` 实际是读取 BUD_T1 数据，没有这条条件。BUD-105 真实的表对只有 **T1↔T4、T3↔T5、T3↔T8**。底账不能把不存在的检查当既有迁移工作，否则 B 可能据此新增未授权业务规则。应删除虚构条目并根据实际 AST/源码定位，不能靠手填行号构造“AST 条件”。

### 2. 同一缺数据退出路径被登记成相反目标

独立 AST 检查发现 **53 对**直接父 if 与其 return 的矛盾记录：父条件标 `to_migrate/insufficient_data`，同一路径中的 return 又标 `normal/pass`。

具体例子：

| 规则 | 缺数据条件 | 同一路径的 return | 当前底账冲突 |
|---|---|---|---|
| BUD-101 | 1220 行 `not rows` | 1221 行 | 待迁移/未完成 vs 正常/pass |
| BUD-102 | 1250 行必要金额为 None | 1251 行 | 待迁移/未完成 vs 正常/pass |
| BUD-108 | 1753 行 `perf_amount_wy is None` | 1754 行 | 待迁移/未完成 vs 正常/pass |
| V33-101 | 1449 行 `not p` | 1450 行 | 待迁移/未完成 vs 正常/pass |

成因明确：生成器先按 If 的行号登记提前退出，再按 Return 的另一行号去重，未识别它们是同一控制流出口，故把提前退出再次登记成“正常完成”。因此当前 283 分支、176 normal 等统计不能作为已核验口径。

整改应按同一控制流出口去重，或显式关联父子记录并保留一致分类；不能只改统计数字。正常结束也不应无条件宣称 pass，应依 findings 和未完成状态聚合。

### 3. 仍漏掉“表存在但字段未识别”的独立子检查

BUD-105 底账只登记外层缺表条件，缺少内部必要数值条件：

- 1352、1385 行：T1/T4 收入、支出金额未取到。
- 1422、1455、1488 行：T3/T5 合计、基本、项目金额未取到。
- 1524 行：T3/T8 基本支出或合计未取到。

BUD-107 至少缺少 1650、1663、1686 行：表格存在，但表侧金额或说明侧金额为 None 时不进入比较。这些分支同样属于缺输入静默跳过，不能用外层“缺表已登记”代表已覆盖。V33-101 的 1459 行四个值完整性守卫及其转换异常吞掉路径，也需按实际语义裁决。

只登记外层表缺失会使 B 仍可能把“金额提取失败”记为正常，通过错误底账制造下一轮返工。

## 下一轮最小范围与验收标准

**只修 A01 底账及其生成器/交付统计，不提前改 B 的业务规则，不重跑已经验证且未改动的全部工作。**

1. 删除不存在的 BUD-105 T1↔T3 条目；每项条件能对应实际源码，位置与业务对象一致。
2. 对 53 对同一退出路径消除矛盾，修复生成器去重逻辑，避免重新生成后复发。
3. 在原任务范围内补齐必要字段/说明金额缺失与异常吞掉的子检查裁决，不能只处理缺表；每项明确迁移卡号、目标状态及理由。
4. 分支状态无法确定时登记未决，不以模板统一判 normal/pass，也不机械把所有未匹配内容都判 insufficient_data。
5. 重新核对底账与源码后更新统计。保留已经通过的 A02～A06 证据；如果修改这些实现，再跑对应回归。

满足上述条件后再判定是否放行 B。下一轮不需要重复对已关闭的 R2-1/R2-3 提交同样说明。

## 独立证据

- `outputs/batch_a_independent_review_r3_20260915/check_ledger.py`、`ledger_evidence.json`：53 对冲突、BUD-105/107 真实条件与漏记条件。
- 同目录 `pytest.log`、`ruff.log`、`mypy.log`、`golden_eval.log`：当前独立验证日志。
- `outputs/golden_replay/DOC-20260905-001-legacy-20260914-231435-807-1903ddedc975416d8dbe08a09e23fdfc.json`。
- `outputs/golden_eval/DOC-20260905-001-eval-legacy-20260915-071501-b6d0d56f0b4944baa477e96046656ea8.json`。

本次仅新增第三轮审查文档及独立输出；没有修改业务实现、旧证据或迁移底账，也未提交、推送、部署或开始 B/C。
