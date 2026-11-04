# 金标补样 B1 交付说明（2026-11-03，复核后退回整改版）

- 计划提交：`d90ba45956613c13900d1d972a90557a77a1b6cf`
- 分支：`codex/gold-b1-20261103`
- 固定快照：`48ee4e28441bd6ddbf34beee85bc6883d60db773`（已导出，未用联合整改 HEAD）
- 运行目录（证据）：`/Volumes/workSSD/software development/GovBudgetChecker/outputs/gold_b1_20261103/run_20261103T150928/`
- 复核退回依据：`outputs/.../reviewer_codex_20261103/independent_review.md`（R01–R08）
- 独立复核：Codex 未再次返回；5 份初标全部 `PENDING_REVIEW`，未冻结。

## 本轮整改（R01–R08）

| 项 | 状态 | 证据 |
|---|---|---|
| R01 5份初标不完整 | 已整份重做：120项=100 pass+20 defect+0 NA+0 CD | `annotations/`；旧版 `revisions/round1_annotations/` |
| R02 盲标/模型身份 | 5份独立子上下文初标；prompt/metadata/原始输出 | `blind_annotation_context/`；U02 污染 prompt 已 clean 重做 |
| R03 T02 | B1-2023-D01 p11 2140101 差1,451.96；T02 PENDING_REVIEW | `annotations/B1-2023-D01/`；`budget_ledger.csv` |
| R04 S04/空表 | “22025”“2023 度”已判 defect；9个空表改 empty_with_note | 各 annotation basic/checks |
| R05 预算账 | reported/verified 分列；超限历史保留；对照改 INCOMPLETE | `budget_ledger.csv`、`target_evidence.csv`、`family_budget.csv` |
| R06 机器核验 | full checker + Decimal 16条 + 自由发现/扫描 | `machine_checks_full.json`、`decimal_checks.csv` |
| R07 清单/命令 | manifest 先progress后排除自身；147文件无失配；checksum审计 | `source_manifest.json`、`checksum_audit.csv`、`commands.log` |
| R08 预算 | 27项逐项适用范围/四态/证据/容差/年度依据 | `budget/GOLD_STANDARD_SPEC_BUDGET_DRAFT_20261103.md` |

完整整改说明：`outputs/.../run_20261103T150928/review_response.md`。

## 当前状态

- 13目标：5 PENDING_REVIEW、2 NOT_FOUND、6 INCOMPLETE
  - PENDING_REVIEW：T02、T05、T07、N02、N06（5个）
  - NOT_FOUND：T03、T08
  - INCOMPLETE：T01、T04、T06、X01、X02、N08
- 对照4类：全部 INCOMPLETE（无正式 annotation）。
- 预算：INCOMPLETE（扫描范围内0份预算）。
- 系统比较：NOT_RUN（无冻结样本）。
- 未写 `reviewed_by`/`truth_frozen_at`；未生成 `FROZEN_NEXT.csv`；不得宣称 B1 全部通过。

## 脚本

新增/修订（本任务目录）：

- `extract_candidate_leads.py`
- `run_candidate_pipeline.py`（strip+64位校验）
- `machine_check_annotations.py`
- `machine_check_full.py`
- `build_decimal_checks.py`
- `build_b1_ledgers_round2.py`
- `build_manifest_progress.py`（先progress后manifest、排除自身）
- `validate_b1.py`
- 旧版：`revisions/round1_scripts/`

## 继续方式

1. Codex 按 `review_package/PENDING_REVIEW.md` 复核 5 份全项初标。
2. 通过后由复核者写 `reviewed_by`/`truth_frozen_at`/`FROZEN_NEXT.csv`；实施者不代签。
3. 冻结后按 `system_compare/NOT_RUN.md` 跑两遍固定快照+selfcompare+逐条比较。
4. 预算规范获批后补材料试标；不强制份数。
