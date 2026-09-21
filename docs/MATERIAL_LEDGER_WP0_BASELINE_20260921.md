# WP0：基线冻结与真值锁定（材料台账整改）

- 冻结日期：2026-09-21
- 基线提交：`4a1cabf2be3fa6e05e9820a9546ee192634871e4`（`origin/main`，PR #41 合并后）
- 工作分支：`feat/material-ledger-foundation-v1`
- 上游文档：`docs/GovBudgetChecker_系统整改PLAN_V1.0_20260921.md`、
  `docs/IMPLEMENTATION_WORKPACKAGES.csv`

## 这条基线是干什么用的

WP0 的全部意义是让**后续任何整改都能对比"改前/改后"**。没有它，
"这次改完是不是真的更好了"只能靠叙述；有了它，每一个数字都能复算。

配套的一条纪律写在 PLAN §18 与本次任务书第十节：**禁止修改真值来让测试变绿**。
因此本文件记录的是"当时的客观事实"，不是"当时期望的值"。改动其中任何一项，
都必须同时说明为什么真值本身变了。

---

## 1. 源码与仓库状态

| 项目 | 值 |
| --- | --- |
| 基线提交（main） | `4a1cabf2be3fa6e05e9820a9546ee192634871e4` |
| 该提交标题 | `feat(engine): 报告画像唯一化 + 检查义务台账 + 独立验收三项 P1 整改（v6） (#41)` |
| 采集时工作分支 | `feat/material-ledger-foundation-v1`（自基线创建） |
| 采集时工作树 | 干净（仅有本轮新上传的 3 个未跟踪文档：PLAN、CSV、UI 效果图压缩包） |
| 本轮基线 SDK | Python 3.12.14（项目 `.venv`） |

> 注意：`main` 分支名在本地指向 `41fab28`（较旧），真正的最新是 `origin/main @ 4a1cabf`。
> 本轮所有对比一律以 `4a1cabf` 为准。

## 2. 检查义务清单（obligation catalog）

| 项目 | 值 |
| --- | --- |
| 清单版本 | `obligations-v2` |
| 清单指纹 | `5461a0267d3d6ac4` |
| 画像解析器版本 | `document-profile-v1` |
| 清单自检问题数 | 0 |
| **尚未实现的检查要求（pending_checkers）** | **8 项** |
| 清单文件 SHA-256 | `868b29cb0bc60be487404b22b30a2243a91c3eeecca3841e8faae8b3cade2e29`（`src/engine/check_obligations.py`，60200 字节） |

### 8 项未实现义务明细（与 PLAN §1.3 一致，未增未减）

| # | obligation_id | 分组 | pending checker | 适用文种 |
| --- | --- | --- | --- | --- |
| 1 | `OBL-CROSS-SAN-GONG-ECON` | 表间关系 | `V33-CROSS-SAN-GONG-ECON` | final |
| 2 | `OBL-TXT-FUND-DETAIL` | 表文关系 | `V33-TXT-FUND-DETAIL` | final |
| 3 | `OBL-NARRATIVE-INDICATOR-REPEAT` | 文内关系 | `V33-NARRATIVE-INDICATOR-REPEAT` | final |
| 4 | `OBL-TREND-ZERO-BASE` | 比例与变动 | `CMM-007` | budget, final |
| 5 | `OBL-TREND-COMPLETION-RATE` | 比例与变动 | `V33-TREND-COMPLETION-RATE` | final |
| 6 | `OBL-DISCLOSURE-PERCENT-UNIT` | 披露与表达 | `V33-DISCLOSURE-PERCENT-UNIT` | budget, final |
| 7 | `OBL-SG-COMPLETION` | 三公经费 | `V33-SG-COMPLETION` | final |
| 8 | `OBL-PERF-PHASE-AMOUNT` | 绩效披露 | `BUD-PERF-PHASE-AMOUNT` | budget |

### 制度性覆盖上限（静态可算，不含任何运行）

| 文种 | 规则注册表条数 | 注册表指纹 | 应检查 | 阻塞 | 完成 | 结构上限 |
| --- | --- | --- | --- | --- | --- | --- |
| budget | 22 | `e6737ade16b7f558` | 23 | 22 | 0 | 0.8696 |
| final | 58 | `9ccb168fbc4e3680` | 44 | 43 | 0 | 0.8409 |

> "完成 0 / 未完成 23、44" 是**理论台账**（不跑任何材料）下的数字：
> 未执行 19/36、尚未实现 3/7、语义模型未执行 1/1。
> 它说明的制度性事实是：**存在一批检查无论用什么材料都不可能通过**，
> 因为它们还没有实现。这正是 PLAN §1.3 所说"把缺口记账已完成、
> 把检查真正做出来尚未完成"。

机器可读版本：`docs/baselines/wp0_coverage_baseline.json`
（复算命令：`python scripts/check_coverage_baseline.py --json`）

## 3. Golden Corpus

| 文件 | SHA-256 | 大小 |
| --- | --- | --- |
| `corpus/DOC-20260905-001/golden.json` | `611aeaebe31ba6e785db5f8d263b1c90434de9c0ee16a38479cd65309b24bcc9` | 4659 B |
| `corpus/DOC-20260905-001/ANNOTATIONS.md` | `13b27cbfd2dce0eca9f93edae5d632bb5b7c46d427bf5fe646881534a6046714` | 12118 B |
| `samples/manifest.yaml` | `6cb251e5e55b9f8748903a80d2d6201ee08a948a4384f55d4bc7dabd95878ac2` | 2248 B |
| `rules/v3_3.yaml` | `ad15859793f17018c92cb6ad8867cff49c17ef3da48a96e5ffc45e003b8d077b` | 34494 B |

Golden 语料当前只有 **1 份**（`DOC-20260905-001`）。
PLAN §FN-02 要求扩到 30 份，**本项属于 WP7，本轮不涉及**；
这里冻结现状，是为了让 WP7 扩容后有可比对的起点。

## 4. 真实样张（三份核心 + 其余本机样张）

样张位于 `uploads/putuo_final_samples/`（该目录被 `.gitignore` 排除，不入库；
SHA 记录在此以便任何机器上取得同名文件后校验同一份）。

| 文件 | SHA-256 |
| --- | --- |
| `上海市普陀区文化和旅游局 2025 年度部门决算.pdf` | `74e6afd06669098f57391a13dad63a6f2a15cac620ac9f32018e79649fe6a367` |
| `上海市普陀区人民政府石泉路街道办事处 2025 年度决算.pdf` | `e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4` |
| `上海市普陀区人民政府宜川路街道办事处 2025 年度决算.pdf` | `f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03` |
| `上海市普陀区规划和自然资源局 2024 年度部门决算.pdf` | `631ac4422779919ad4ab717648d5728a4ca334619eb1f7c7e1f8b1479e240ef5` |
| `上海市普陀区财政局 2024 年度部门决算.pdf` | `d45e923176f9fe4b6533829d3a2fe67f8cbcd55d07099dabf73aca62701f8eb5` |
| `上海市普陀区人民政府办公室 2025 年度单位决算.pdf` | `4b23e89f2874b46048a774a5d40a385dfd8038bfd34fafed356014243e8d093f` |
| `上海市普陀区人民政府长风街道办事处 2025 年度决算.pdf` | `6807f563289f30678c942fb17389c5c8486feb9fd8cc8ffe63712336bf69bd72` |
| `DOC-20260905-001_sample.pdf` | `113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7` |

**未取得的部分**：PLAN §14.1 提到的"文旅局、石泉路、宜川路"三份核心样张的
**人工确认问题真值集**（19 组）在本仓库中只以文字验收报告形式存在，
没有机器可读的 `truth_id`。本轮未取得，原因：该真值集尚未建立，
属于 PLAN §FN-03 的工作范围（WP7），不在 WP0 可冻结的既有产物中。

## 5. 数据库迁移

| 项目 | 值 |
| --- | --- |
| 基线迁移条数 | **18** |
| 最后一条迁移 | `2026-08-26_0018_report_scope_key` |
| 迁移文件 | `src/db/migrations.py` |
| 本机可用 PostgreSQL | 15.17（`localhost:5432`） |
| 开发库当前状态 | `fiscal_db.public` 已应用 18 条（0019 未应用） |

> 本机 `fiscal_db` 是**开发库**。本轮所有真库验证一律在随机命名的临时 schema 中
> 完成并随测随删，`public` 始终保持 18 条迁移、无 `material_slots` 表。
> 验证见 §7。

## 6. 静态检查与测试基线（改前）

| 检查 | 基线结果 | 复算命令 |
| --- | --- | --- |
| `pytest`（全量） | **1307 passed, 1 skipped, 0 failed**（101.64s） | `python -m pytest -q` |
| `ruff check src tests scripts api` | **All checks passed** | 同左 |
| `mypy api src tests` | **Success: no issues found in 199 source files** | 同左 |

基线 `pytest` 原始输出：`outputs/wp0_pytest_baseline.txt`（该目录被 `.gitignore` 排除）。

`ruff` / `mypy` 的基线数字取自**基线提交的干净检出**（`git worktree` 指向 `4a1cabf`），
不是取自带有本轮改动的当前工作树——后者会把新文件算进去，无法作为对比起点。

## 7. 真库验证能力（本轮新建立）

本仓库默认不连数据库（`tests/conftest.py` 无条件摘掉 `DATABASE_URL`），
连库必须显式 opt-in `GOVBUDGET_TEST_DATABASE_URL`。
本机存在可用的 PostgreSQL 15.17，因此本轮**建立了真库验证路径**并实际跑通：

- 隔离方式：`PG_SCHEMA` 指向随机 schema（`matslot_test_<12位十六进制>`），
  测试结束 `DROP SCHEMA ... CASCADE`；
- 实测结果：`10 passed`（含全新库、既有库升级、语句重放、唯一约束、CHECK 约束、
  级联行为、服务层端到端）；
- 实测后核对：`fiscal_db` 中 `public` schema 未变（仍 18 条迁移、无 `material_slots`、
  无残留测试 schema）。

这条路径对 WP4（8 项义务实现）以后同样适用，属于本轮的可复用产出。

## 8. 本轮基线中记录的既有问题（不是本轮引入）

以下三点在采集基线时被客观观察到，一并记录，供后续批次判断优先级：

1. **本机 `uploads/` 中存在大量测试残留**：348 个任务目录中，`sample_budget_2025.pdf`
   63 份、`split_mode.pdf` 63 份，均无组织绑定。这与 `tests/conftest.py` 注释中
   记载的历史污染问题一致（该注释称已通过 autouse fixture 隔离，本轮未观察到新的
   增长，但旧残留仍在）。
2. **组织目录 `data/organizations.json` 含测试数据**：存在名为 `split` 的
   department 与 unit 两条记录（`73445f1c1c02` / `8665d44c1be7`）。
   该文件被 `.gitignore` 排除，属于本机数据卫生问题。
3. **同名部门/单位在真实目录中普遍存在**：413 条组织中，有 **42 组**
   同名且同时存在 department 与 unit 记录（如"上海市普陀区规划和自然资源局"
   的部门 `8f773936806d` 与本级单位 `53f98bebfc3a`）。
   这正是本轮必须解决的隔离问题，也是其真实规模。

## 9. 复现方式

```bash
git checkout 4a1cabf2be3fa6e05e9820a9546ee192634871e4

# 覆盖基线（静态、不连库、不读 PDF）
python scripts/check_coverage_baseline.py --json
python scripts/check_coverage_baseline.py --assert-gaps 8

# 全量测试
python -m pytest -q

# 静态检查
python -m ruff check src tests scripts api
python -m mypy api src tests
```
