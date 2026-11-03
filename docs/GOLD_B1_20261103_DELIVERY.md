# 金标补样 B1 交付说明（2026-11-03）

- 计划提交：`d90ba45956613c13900d1d972a90557a77a1b6cf`
- 分支：`codex/gold-b1-20261103`
- 固定快照：`48ee4e28441bd6ddbf34beee85bc6883d60db773`（已导出，未用联合整改 HEAD）
- 运行目录（证据）：`/Volumes/workSSD/software development/GovBudgetChecker/outputs/gold_b1_20261103/run_20261103T150928/`
- 独立复核：Codex 未返回；5 份初标全部 `PENDING_REVIEW`，未冻结。

## 交付内容

| 文件 | 说明 |
|---|---|
| `outputs/.../execution_contract.json` | 本轮口径：按 §3.7、FOUND≠DETECTED、D/I 池、无复核则 PENDING_REVIEW |
| `source_manifest.json` | 计划/快照/依赖/池子/33 份金标/文件 SHA256 |
| `progress.json` | 步骤状态：筛选/初标/机器核验完成；复核/预算/系统比较 blocked |
| `candidate_screen_log.csv` | 20 份唯一 PDF 的逐份核对、排除/候选理由、预算单位 |
| `candidates.csv` | 5 个候选（4 目标 + 1 非目标 C-02） |
| `target_status.csv` | 13 目标 + 4 对照逐项状态 |
| `annotations/*/annotation.json` | 5 份初标，24 项齐全；目标检查有证据，其余 CD 待复核 |
| `machine_checks.json` | 5 份 24 项/quote/页码核验，ok=true |
| `review_package/PENDING_REVIEW.md` | Codex 复核清单、冻结要求、I 池说明 |
| `budget/` | 27 项预算规范草案、样本清单、结构试标 INCOMPLETE |
| `system_compare/` | NOT_RUN 说明 + 对照误报结果 + 目标候选对照 |
| `system_issue_leads.csv` | 真实非目标问题与系统误报线索 |
| `B1_report.md` | 完整报告 |
| `selfcheck.json` | 43 项自检通过 |

## 状态

- 13 目标：4 PENDING_REVIEW（T03/T07/N02/N06）、3 NOT_FOUND（T02/T05/T08）、6 INCOMPLETE（T01/T04/T06/X01/X02/N08）。
- 预算：INCOMPLETE（本地 0 份预算 PDF；唯一文件名“预算”实为决算）。
- 系统比较：NOT_RUN（无冻结新样本）。
- 未改业务代码/规则/裁决；未启动 B2/B3；未合入/部署。

## 脚本

本目录 `scripts/` 下新增：

- `extract_candidate_leads.py`：从 160 回归记录提取候选线索
- `run_candidate_pipeline.py`：固定快照单份管线
- `build_initial_annotations.py`：生成 PENDING_REVIEW 初标
- `machine_check_annotations.py`：quote/页码/24 项核验
- `build_b1_ledgers.py`：筛选日志/候选/目标状态/合同
- `build_manifest_progress.py`：清单/进度/文件哈希
- `validate_b1.py`：交付自检

## 继续方式

1. Codex 按 `review_package/PENDING_REVIEW.md` 复核；通过后由复核者写 `reviewed_by`、`truth_frozen_at`、`FROZEN_NEXT.csv`。
2. 冻结后按 `system_compare/NOT_RUN.md` 跑两遍固定快照 + selfcompare。
3. 预算规范获批后补 2 部门 + 2 单位预算试标。
