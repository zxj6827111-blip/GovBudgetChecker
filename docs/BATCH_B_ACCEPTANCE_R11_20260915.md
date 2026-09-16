# 批次 B 独立验收 R11

2026-09-15。**结论：GO（批次B本地代码与本轮约定回归范围）。R10列唯一性P1关闭，当前已知B验收阻塞项关闭。可以进入计划中的C阶段终验；本次未启动C，也不代表生产验收通过。**

## 核验对象

已读取任务 `01a0a588-9e4c-7bb2-ac27-e02f8240ebbf`，标题“修复列定位歧义”，核对其实现总结与当前实际源码/测试。没有仅依据实现方的通过声明放行。

`src/engine/budget_rules.py`当前SHA-256：

`c96b5fefe6e5ee19d7c585df7ce36bf6d9ead90274b7c0a569fa01e8da0ee60c`

与实现任务报告一致。Golden真值SHA仍为 `611aeaebe31ba6e785db5f8d263b1c90434de9c0ee16a38479cd65309b24bcc9`。

## R10关闭依据

`_extract_t1_t4_strict_totals` 的列身份判断先于单元格取值：

- 多个current候选直接返回未知，不再取第一列。
- 无current候选时，依据表头金额列及结构判断唯一性，不再计数已填数值。
- 多个未识别期间列即使只有一个有值仍返回未知。
- 确认列身份后，只读目标单元格；空白/无效不向右回退。明确prior列不能用于本年。

实际BUD-101独立探针验证：纯数字双期间列双非空、右空，以及重复current列均deferred，无虚假finding。

新增16项参数化测试已检查并独立随全量测试执行：纯数字期间列/重复current两类表头 × 双非空/左空/右空/全空 × BUD-101/BUD-105的T4消费者。均验证未完成原因及无伪finding，不以返回空列表代替状态断言。

累计探针与R10机器结果逐键比较（排除SHA）：**仅两项预期变化，另外43项完全一致**。

| 变化项 | R10 | 当前 |
|---|---|---|
| 纯数字期间双列、第二列空白 | 返回[] | deferred |
| 重复current列 | 返回[] | deferred |

其余探针覆盖此前已修复的金额/年份隔离、带/不带“年”字表头、正常/缺输入、单位换算、动态精度、整数金额、部分finding保留及日志扫描反例，未观察到结果退化。此处43是既有探针数，不是全系统业务覆盖率。

## 独立执行结果

| 检查 | 本轮结果 |
|---|---|
| 完整pytest，工作区TEMP/TMP，禁用cacheprovider | **1240 passed、1 skipped**，91.11秒 |
| Ruff：src api scripts tests | 通过 |
| mypy：src api | 通过，104 source files |
| 新鲜legacy Golden，显式no-resume | TP=3、FP=0、FN=0，GATE-PASS |
| Golden其他指标 | hint3/3，证据面4/4，severity/page/locatable均1.0 |
| 累计探针差异断言 | 通过：仅两项预期变化，43项不变 |

## 放行边界

本报告关闭当前已知B阶段阻塞，允许按批准实施包推进C01～C03终验。历史NO-GO文档保留，其阻塞状态由本报告更新。

GO仅适用于本地批次B约定范围；不代表所有真实预算/决算材料已完成真值裁决，也不是NAS/生产运行、前端端到端或发布批准。仍需C阶段完整终验包及独立审核，遵守暂停点三；不能以本报告授权push、部署或修改Golden真值。

本轮只执行独立审查并生成验收文件，没有启动C、修改业务源码/既有测试、提交、推送或部署。

## 本轮证据

独立目录：`outputs/batch_b_independent_review_r11_20260915/`，未覆盖此前目录。

- `probe.py`、`probe_results.json`、`probe.log`：累计真实规则探针，无mock，含六核心文件SHA。
- `check_probe_delta.py`、`probe_delta.json`：与R10逐键差异断言。
- `pytest.log`、`ruff.log`、`mypy.log`：独立门禁日志。
- `golden_replay.log`、`golden_eval.log`：新鲜回放与评估输出。
- 回放：`outputs/golden_replay/DOC-20260905-001-legacy-20260915-145743-707-076a0f8396d74267a9d42f77a97b2e33.json`。
- 评估：`outputs/golden_eval/DOC-20260905-001-eval-legacy-20260915-225815-41c6f7703707496880ecd54325327330.json`。
