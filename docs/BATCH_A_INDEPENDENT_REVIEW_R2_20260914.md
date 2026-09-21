# 批次 A 第二轮独立复验（2026-09-14）

**结论：仍为 NO-GO，暂不放行 B/C。** 本轮实质修复了完整备份、样本集合和状态优先级问题；剩余阻塞收敛为三项：A01 分类漏项、A04 修前证据身份、A06 共存场景的实际边界测试。不是六项全部失败，也不需要重做已验收的修复。

## 已通过的整改与独立验证

1. **A02 可恢复内容已补齐。** `repo_head.bundle` 经 `git bundle verify` 通过，包含完整历史及 HEAD `63735c04a2e489dae57ab02cda9c469e6608eec7`。150 个备份条目 SHA 全部匹配；排除 bundle/tar/patch 后，147 个还原条目 SHA 全部匹配。源码归档有 641 个文件，与 HEAD 的 641 个文件对应：30 个原始字节一致，611 个仅 CRLF/LF 不同，没有其他内容差异。还原树确实包含源码，不再只是七个材料副本。本次未重新执行完整恢复命令，而是独立核验已恢复内容和归档、bundle 的一致性。
2. **A03 输入集合整改通过。** 8 份决算、33 份预算均 SHA 匹配且各组唯一；原 corpus、文旅、石泉路、宜川路、长风已恢复，另有三份补充样张。逐一读取 33 份预算封面，均有预算单位/预算主管部门等预算文种证据。空白 PDF 已从真实预算清单中剔除。这个结论是材料识别与冻结通过，不等于逐页真值或全部规则覆盖验收通过。
3. **A05 上轮已指出的状态和同批转换问题通过。** 三入口使用 `resolve_rule_status`，fail 高于 not_applicable，转换 parse_error 高于 insufficient_data；新增测试包含三入口“部分失败+部分不适用”，以及同批有效/无效 Issue 保留有效 finding 与两种原因。相关测试已随全量套件独立复跑。
4. **当前工作树全量验证通过：** `python -m pytest -q -p no:cacheprovider`：1159 passed、1 skipped、22 warnings，80.00 秒，退出码 0。Ruff 退出码 0；mypy 退出码 0，189 source files。TEMP/TMP 指向仓库内 tmp。
5. **当前 legacy Golden 独立无缓存重放通过：** TP=3、FP=0、FN=0、GATE-PASS；hint 3/3，severity/page/evidence 均为 1.0。该结果不能替代 A06 共存验收。

## 剩余三项阻塞

### R2-1 [P1] A06 真实进程存在，但仍未测试“已确认 finding 与未完成共存”穿过边界

位置：`tests/test_quality_gate_coexistence.py:259`、`:311`。

新增测试确实启动了子进程，PID 断言有效。但其 `_make_dummy_doc()` 配合真实预算规则，并没有注入或触发一条带 partial_issues 的规则。独立用同一输入实际 spawn 后得到：

```text
parent_pid=74012, worker_pid=77796, exitcode=0
findings=13（来自 BUD-001 完整性检查）
total_rules=22, insufficient_data=0, unresolved_total=0
partial_findings_total=0
```

因此该路径没有覆盖本轮新增载荷。第 289 行 `assert len(payload["issues"]["all"]) >= 0` 恒成立；第 357～358 行允许 `done`/`complete`，即使共存状态未被传输也会绿。磁盘测试调用 `Path.write_text` 和 `json.loads` 手动落盘，没有调用项目结果存取接口；它不能支持“真实结果存储链已验证”的报告口径。

**最小补齐要求：** 使用可在 spawn 子进程加载的确定性测试规则/夹具，实际产生至少一条已确认 finding 和一个未完成子检查，经实际进程或项目实际结果边界传输后，精确断言该 rule_id、证据、partial_findings_total、未完成原因保留，且 `status=review_required`、`analysis_conclusion=incomplete`。禁止以存在字段或允许 done 代替这些断言。若声称验证存储，就调用真实结果存取层的隔离实现；否则准确标明仅验证 IPC，避免扩大交付口径。无需连接生产库或启动完整前端。

### R2-2 [P1] A01 只枚举 return，仍漏记条件不成立或 continue 导致的未检查

底账已覆盖注册的 74 条规则，但真实构成为 **16 budget、52 final、6 common**，不是报告的 13/48/13。总数对了，不代表逐分支分类准确。

反例位置：

- `src/engine/budget_rules.py:1418`、`:1521`：BUD-105 只有依赖表存在才执行相应子检查，缺表时不进入分支；底账第 247 行附近却只有末尾 `return issues`，分类 normal、migration_card=none。
- `src/engine/budget_rules.py:1910`：BUD-110 缺表或页号时直接 continue；底账第 405 行附近也只有正常结束，没有待迁移记录。
- BUD-107 的底账第 285 行附近同样仅一条 normal；独立缺表输入执行 BUD-105/107/110 全部返回空列表。

这些业务规则留到 B 修改本身正确；问题是 A 的迁移底账没有把它们列为 B 的工作，反而退回“正常、不迁移”。此外，V33-102 的异常捕获分支产出“规则执行异常”info，也被通用模板标为 valid return，应结合 execution_error 契约重新裁决。

**最小补齐要求：** 对 BUD-101～113 和已约定决算范围，记录独立子检查的进入条件、缺数据时的隐式跳过、continue、异常吞掉与返回点，而非仅 AST Return 节点。逐项给目标状态、理由和 B 卡号；至少 BUD-105/107/110 缺输入不能标 normal/none。只修底账，不提前实施 B 规则。不要将扫描器的统一模板当作业务裁决。

### R2-3 [P1] A04 新日志是修后 1159 测试，仍不能充当修前基线复验

`outputs/remediation_v5/batch_a/A04_baseline_test_run.json` 绑定了完整日志及具体 Golden 文件，解决了“没有日志”问题。但日志列出本次新增的 14 个测试，结果为 1159 passed；新样本清单的四个代码指纹也绑定当前 A 修后机制，摘要已有 partial_findings_total。这些是当前工作树/进入 B 前的有效证据，不能证明 A 修改前的基线已经复验。

**最小补齐要求：** 利用已验证的 bundle，在隔离目录恢复未叠加 A patch 的 HEAD，补跑该基线原有 pytest/ruff/mypy 与 legacy Golden，保存原始日志、完整 HEAD、命令 cwd、解释器和配置，并标记“复验期间补建的 pre-A 基线”。现有 1159 日志保留并正确标为 post-A/pre-B。两者不能覆盖或互相冒充。相关材料输出也应区分其实际源代码身份；无需伪造历史运行时间。

## 本轮裁决

| 卡号 | 第二轮结论 |
|---|---|
| A01 | 未通过：缺输入子检查仍漏记，见 R2-2 |
| A02 | 本轮备份/恢复内容复核通过 |
| A03 | 输入集合整改通过；修前与修后证据身份需随 R2-3 明确 |
| A04 | 当前全量验证通过；pre-A 基线日志仍未补齐 |
| A05 | 本轮状态聚合和同批转换修复通过 |
| A06 | 真实进程启动通过；共存载荷实际边界验收未通过 |

下一轮仅处理 R2-1～R2-3。每项按具体断言和证据交付，再暂停独立复验；不进入 B/C，不通过放宽测试、改变 Golden 或改写“全部通过”文案结束返工。

## 本轮独立证据

- `outputs/batch_a_independent_review_r2_20260914/audit_r2.py` 与 `evidence.json`：备份/还原 SHA、HEAD 归档核验、输入 SHA、缺表反例、真实 spawn 的载荷结果。
- 同目录 `pytest.log`、`ruff.log`、`mypy.log`、`golden_eval.log`：本轮独立命令结果。
- `outputs/golden_replay/DOC-20260905-001-legacy-20260914-135724-763-7a8be66b20004a448e8d76106e7410c0.json`。
- `outputs/golden_eval/DOC-20260905-001-eval-legacy-20260914-215743-d134e11561224913a271bffc16763727.json`。

本次只新增第二轮审查文档和独立输出，未改业务实现、未提交、未 push。保留第一轮报告；本文件表示对返工后的当前状态复验，不覆盖历史结论。
