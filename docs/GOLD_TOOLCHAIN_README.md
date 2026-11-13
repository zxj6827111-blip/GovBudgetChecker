# 金标工具链使用说明（GOLD TOOLCHAIN README）

本文说明 GovBudgetChecker 金标（gold standard）工具链的 10 份正式工具的用途、数据位置、典型流程与读写边界。所有命令均以仓库根目录为工作目录，且已在 2026-11-13 实测验证。

## 1. 工具清单与职责

### 采集与核验（单人可跑）

| 工具 | 职责 | 主要命令 |
|---|---|---|
| `scripts/gold_extract.py` | 从源 PDF 抽取页面文本/表格快照，供标注取数 | `pages <sid>`、`table <sid> <页>`、`map <sid>`、`show <sid> <页>`、`glyph <sid> <页> <文字>`、`compare <sid>` |
| `scripts/gold_checks.py` | 把九张核心表数字读出来逐项复算，只给算式与结果，不下结论 | `report <sid>`、`audit <sid>` |
| `scripts/gold_diff.py` | 比较两份金标 annotation 的差异，产出 MD/JSON 报告 | `--old A --new B --out xx.md --json xx.json [--allow xx.json]` |

### 标注生成（写 `gold/<sid>/annotation.json`）

| 工具 | 职责 | 主要命令 |
|---|---|---|
| `scripts/gold_skeleton.py` | 生成标注骨架：basic 自动定位，GS-T/X 按表格取数，S/N/W 类先写 cannot_determine 占位 | `python scripts/gold_skeleton.py <sid>` |
| `scripts/gold_autofill.py` | 把骨架中的 GS-T*/GS-X* 与 GS-S01 按表格逐格填满 | `python scripts/gold_autofill.py <sid>` |
| `scripts/gold_judge.py` | 把人工判定（S/N/W + 非格式自由发现）注入骨架产物 | `python scripts/gold_judge.py <sid>` |
| `scripts/gold_build.py` | 返工辅助：R10 算式取值填充、R9 格式扫描、R21-R24 口径/标点/序号扫描 | `fill <sid>`、`format <sid>`、`audit-be <sid>`、`scan <sid> [r21\|r22\|r23\|r24]` |

### 校验与冻结

| 工具 | 职责 | 主要命令 |
|---|---|---|
| `scripts/gold_verify.py` | 机器核验标注：sha256 对账、检查项完整性、quote 定位、格式算式复算；`--all` 为冻结层校验 | `--all`、`--summary`、`gold/<sid>/annotation.json` |

### 基线重放（分析级，重）

| 工具 | 职责 | 主要命令 |
|---|---|---|
| `scripts/gold_baseline_run.py` | 把 33 份冻结金标 PDF 交给固定提交的系统（git archive 导出）采集两遍并自比，只采集不计算指标 | `prepare --out ... --code-root ...`、`collect --out ... --inputs ... --code-root ...`、`selfcompare --a ... --b ...` |
| `scripts/gold_baseline_tables.py` | 由 `analysis/*.json` 生成基线报告表格（避免手抄数字） | `GOLD_BASELINE_REV=r2 python scripts/gold_baseline_tables.py` |

### 第一阶段已入库的依赖模块（`de781ed4`）

- `scripts/gold_baseline_match.py`：金标匹配/真值/指标模块（955 行），`gold_baseline_tables.py` 依赖它。
- `scripts/gold_baseline_decisions.py` / `_r2.py`：v1 人工裁决底本与 R9 轮改判。
- `scripts/gold_baseline_eval_fp_batch1.py`：第二批误报评估入口。
- `docs/GOLD_STANDARD_SAMPLE_20261101.csv`：33 份样本注册表（sample_id、相对 path、sha256）。

## 2. 金标数据存放位置

- 数据根目录：`gold/`（**本地保留，不入 Git**）。
- 单样本：`gold/<sample_id>/annotation.json`（人工标注）、`judge.json`（人工判定）、`manifest.json`（采集快照，含源 PDF sha256）、`pages/`（可再生的页面文本/表格 JSON）。
- 台账：`gold/FROZEN.csv`（33 份冻结登记：sample_id、冻结时间、标注 sha256）、`gold/SUMMARY.csv`、`gold/DISPUTES.md`、`gold/REVIEW_R1..R9.md`（轮审记录）、`gold/DIFF_R7/R9.*`、`gold/ALLOW_R7/R9.json`。
- 历史：`gold/_backup_r3/_r6/_r7/_r9/`（返工前备份）、`gold/templates/`。
- 源 PDF：本机 `~/Desktop/预决算信息收集/决算测试上海市/<年份>/<单位>/<文件>.pdf`，经样本清单 `path` 列相对定位（见 §8）。

## 3. 如何新增一份金标样本

1. 在 `docs/GOLD_STANDARD_SAMPLE_20261101.csv` 追加一行（sample_id、year、kind、department、file、path、pages、text_layer、sha256）。
2. `.venv/bin/python scripts/gold_extract.py pages <sid>` 抽取页面快照（写 `gold/<sid>/pages/`）。
3. `.venv/bin/python scripts/gold_skeleton.py <sid>` 生成骨架（写 `annotation.json`）。
4. `.venv/bin/python scripts/gold_autofill.py <sid>` 表格自动填充。
5. 人读原文，`.venv/bin/python scripts/gold_judge.py <sid>` 注入判定；配合 `gold_checks.py report <sid>` 与 `gold_extract.py table/show/glyph` 逐项核数。
6. `.venv/bin/python scripts/gold_verify.py gold/<sid>/annotation.json` 机器核验至 0 错误。
7. 冻结：把 `annotation.json` 的 sha256 与冻结时间登记进 `gold/FROZEN.csv`。

## 4. 如何验证现有 33 份冻结金标

```bash
.venv/bin/python scripts/gold_verify.py --all          # 冻结层：33 行 FROZEN OK，退出码 0
```

或跑等价的回归测试：`.venv/bin/python -m pytest tests/test_gold_toolchain.py -k frozen`（无 `gold/` 数据的环境自动 skip）。

## 5. 如何比较金标新旧版本

```bash
.venv/bin/python scripts/gold_diff.py --old 旧annotation.json \
    --new 新annotation.json --out diff.md --json diff.json [--allow allow.json]
```

`--allow` 用于登记已知可接受差异（历史轮次如 `gold/ALLOW_R9.json`）。输出 `差异 N 条；未允许 M 条`。

## 6. 如何生成基线统计报告

```bash
.venv/bin/python scripts/gold_baseline_run.py prepare --out outputs/gold_baseline_<日期> --code-root <仓库> [--kit <验收包>] [--jobs N]
.venv/bin/python scripts/gold_baseline_run.py collect --out outputs/gold_baseline_<日期>/run1 --inputs <prepare产物> --code-root <仓库> [--jobs N] [--timeout 1800]
.venv/bin/python scripts/gold_baseline_run.py collect --out .../run2 --inputs <同上> --code-root <仓库>
.venv/bin/python scripts/gold_baseline_run.py selfcompare --a .../run1 --b .../run2
GOLD_BASELINE_REV=r2 .venv/bin/python scripts/gold_baseline_tables.py   # 由 analysis/*.json 生成报告表格
```

运行前提见 `docs/GOLD_BASELINE_PROTOCOL_20261102.md`。注意：`--kit` 默认指向 `outputs/pending_review_20261101/acceptance_kit/v1-regression-r4`（本机验收包，已不在仓库/outputs 默认位置时需显式传 `--kit`）。

## 7. 只读命令与写入命令

**只读（不改动 `gold/` 与业务数据）**：`gold_extract.py` 的 `table/show/map/compare`（`compare` 不加 `--render` 时）；`gold_checks.py report/audit`；`gold_verify.py` 全部子命令；`gold_build.py format/scan/audit-be`；`gold_diff.py`（写 `--out/--json` 指定的目标文件，不动金标）。

**写入（动 `gold/<sid>/` 或 outputs）**：`gold_extract.py pages`、`glyph`、`compare --render`；`gold_skeleton.py`、`gold_autofill.py`、`gold_judge.py`（覆盖 `annotation.json`）；`gold_build.py fill`（写回 annotation）；`gold_baseline_run.py`（写 outputs 重放产物）；`gold_baseline_tables.py`（写 `report_tables.md`）。

**跑任何写入命令前先备份**：`cp -R gold/<sid> outputs/<备份目录>/`。历史返工的备份范式见 `gold/_backup_r3.._r9/`。

## 8. 在另一台电脑 / 干净检出环境运行

```bash
git clone <仓库> && cd GovBudgetChecker
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r api/requirements.txt
```

- PDF 库依赖（`PyMuPDF>=1.24.0`、`pdfplumber>=0.11.0`）均已在 `requirements.txt` 声明，随上例一并安装。
- 代码层依赖已验证：干净 `git archive HEAD` 导出 + 10 份工具 + 项目 venv 下 14/14 模块加载、CLI 冒烟全部通过（2026-11-13 实测）。
- 源 PDF 根目录按 `Path.home()/Desktop/预决算信息收集/决算测试上海市` 解析（`gold_extract.py`、`gold_verify.py`、`gold_skeleton.py`、`gold_build.py`、`gold_baseline_run.py` 各自的 `SOURCE_ROOT/SRC/CORPUS_ROOT` 常量）。换机器需把 PDF 树放到同一位置，或修改常量/等价映射（可配置化方案见交付报告 §P5）。
- `gold/` 金标数据与 `outputs/` 重放产物不在 Git 内，需自行从备份迁移。
- `gold_baseline_tables.py` 必须以 `GOLD_BASELINE_REV=r2` 运行。
