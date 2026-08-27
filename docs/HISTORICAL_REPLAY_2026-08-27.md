# 历史任务全量回放与整改前后对比（Task 15.1 / P2-03）

- 日期：2026-08-27
- 分支：`feat/prod-readiness-m1`（HEAD 含 M1–M3 + Task C + Task 14）
- 回放脚本：`scripts/replay_analysis.py`（**只读**离线指标回放）
- 原始输出：`outputs/replay_2026-08-27.json`（含 766 条逐任务明细；`outputs/` 已入 `.gitignore`，不进版本库）
- 引擎版本：`0.1.0`；生成时间：`2026-08-27T01:31:17Z`

## 0. 一句话结论

历史产物**全部是 M1 之前生成的**，因此回放只能证明"历史数据长什么样"，
不能证明"新代码好在哪"。为此本轮额外做了**同一份真实 PDF 的前后对比**（第 3 节）：
新代码把历史上一份 0 文本页的 `done` 任务纠正为 `review_required`，
把一份 34 页真实材料的结论从"裸 done"升级为"done + 覆盖率 0.9706 + 证据完整率 1.0 + 版本留痕"，
且检出的 5 条问题一条不少。

## 1. 只读自证

度量动作本身不得污染被度量的数据。做法是全量 `sha256 + size + mtime_ns` 快照 → 回放 → 再快照 → 逐项比对：

| 项 | 值 |
|---|---|
| 快照文件数（回放前） | 2563 |
| 快照文件数（回放后） | 2563 |
| 新增 / 删除 / 内容或时间戳变化 | 0 / 0 / 0 |
| 结论 | `read_only_verified: true` |

脚本另有两道结构性保护：`--output` 必须显式指定，且**拒绝写入被扫描目录**
（`_resolve_output_path`），所以报告不可能落进 `uploads/`。

## 2. 全量指标与上一轮基线对比

| 指标 | 上一轮基线 | 本轮（2026-08-27） | 说明 |
|---|---|---|---|
| 任务总数 | 661 | **766** | 期间 M2–M4 的手工验证与 e2e 又产生了 105 个任务目录 |
| 跳过目录 | — | 1（`uploads/reports`，非任务目录） | 无 `status.json` 视为非任务目录 |
| `done` | 242 | **242** | 完全一致，说明既有产物未被改动 |
| `error` | 8 | **8** | 一致 |
| `processing` | 18 | **39** | 新增的都是被中断的运行残留（见 5.1） |
| `queued` | 18 | **39** | 同上 |
| `unnormalized:uploaded` | 375 | **438** | 上传后从未发起分析 |
| `report_kind=unknown` | 366（55.37%） | **429（56.01%）** | 比例基本持平 |
| 年份未识别 | 366（55.37%） | **429（56.01%）** | 与 unknown 同源 |
| 证据完整率 | 0.4861 | **0.4861** | 数值完全一致 |
| findings 总数 / 完整数 | — | 2732 / 1328 | 缺证据的 1404 条全部落在 `rule_warning`（规则告警类，不计入正式问题） |
| `report_id` 冲突组数 | 2 | **2** | 3 job 共用 `1a94158a…`、2 job 共用 `3d54ad55…` |
| 有 `report_id` 的任务 / 去重后 | — | 128 / 42 | 冲突集中在少量报告身份上 |
| 有 `page_coverage` 的任务 | — | **0** | **关键**：页面覆盖率是 M1 Task 2 才引入的字段，历史产物一条都没有 |
| 有 `quality_status` 的任务 | — | 71 | 来自 M1 之前的 `degraded` 机制 |
| 完成态里 0 正式问题 | — | 24 / 242（9.92%） | 整改前无法区分"真没问题"和"没查完" |

### 2.1 怎么读这张表

- `done/error/证据完整率` 三项与上一轮**逐位一致**，这是回放只读的第二重旁证。
- `processing/queued/uploaded` 增长来自开发期的手工与 e2e 运行，不是质量退化。
- `page_coverage` 全缺、`analysis_conclusion` 全缺，说明**历史产物无法用新指标体系评价**。
  这不是脚本缺陷，而是数据事实：想让历史任务具备新字段，必须重新分析（属"历史数据重跑"范畴，
  见第 5 节的处置建议）。

## 3. 同输入的整改前后对比（本轮新增）

方法：把历史任务的 PDF **复制**到 `tmp/` 下的临时目录（`UPLOAD_DIR` 指向该临时目录），
用当前代码重跑 `_run_pipeline_inner`，再与历史 `status.json` 逐字段比。
隔离措施：`AI_ASSIST_ENABLED=false`（不发外部请求）、
`ORG_DATA_DIR`/`USER_DATA_DIR` 指向 tmp（不动 `data/`）。
`uploads/` 只读、未写入（已由第 1 节的快照比对覆盖）。

> **隔离失效事故（如实披露）**：脚本里写的是 `os.environ.pop("DATABASE_URL")`，
> 但 `api/main.py` 导入链上的 `load_dotenv()` 又把 `.env` 里的 `DATABASE_URL` 装回去了，
> 于是结构化入库**真实写入了开发库** `fiscal_db`：
> `org_dept_annual_report` 3 行、`org_dept_table_data` 16 行、
> `org_dept_line_items` 374 行、`org_unit` 1 行（全部在 01:34–01:36Z）。
> 回滚 SQL、根因与防复发措施见
> `docs/LEGACY_DATA_CLEANUP_PLAN_2026-08-27.md` 第 0 节，**待确认后执行**。
> 附带的正面证据：这 3 行的 `year` 落 `NULL` 而不是 `2000`，年份不可信时 `scope_key`
> 落 checksum —— 这是 M1 的 B-02 与 P0-09 修复在真实库上的直接验证。
> `uploads/` 未受影响（第 1 节快照比对为 0 变化）。

由于 `report_kind`/`report_year` 是**上传阶段**（`api/routes/upload.py`）确定的、不是分析流水线
确定的，为避免"没走上传流程"这个噪声混进结论，每份材料跑两个变体。

### 3.1 案例 A：真实区级部门预算公开材料（34 页，207 KB）

`uploads/05461c32b6822359f74a47ddccfa3c4d/上海市普陀区人民政府石泉路街道办事处26单位.pdf`

| 字段 | 整改前（历史产物） | 整改后（补回上传期元数据） | 整改后（不补元数据） |
|---|---|---|---|
| `status` | `done` | `done` | `review_required` |
| `analysis_conclusion` | 无此字段 | `findings_detected` | `incomplete` |
| `page_coverage` | 无此字段 | **0.9706**（34 页中 33 页有文本层） | 0.9706 |
| `scanned_page_count` | 无此字段 | 0 | 0 |
| 低文本页 | 无此字段 | 第 1 页（封面，疑似图片） | 第 1 页 |
| 证据完整率 | 无此字段 | **1.0**（5/5） | 1.0 |
| finding 版本留痕 | 无 | `rule_version=v3_3`、`engine_version=0.1.0` | 同 |
| 检出问题数 | 5 | **5** | 5 |

读法：
- 问题数不变 → 新增的门禁**没有削弱检出能力**；
- 从"裸 `done`"变成"`done` + 覆盖率 + 证据完整率 + 版本留痕" → 结论首次可被审计；
- 第三列说明门禁真的会拦：元数据缺失时不再给 `done`，而是转人工复核。

### 3.2 案例 B：0 文本页材料（1 页，无文本层）

`uploads/01aa2e37d3779caebccb711860fe4de8/split_mode.pdf`

| 字段 | 整改前 | 整改后（两个变体一致） |
|---|---|---|
| `status` | **`done`** | **`review_required`** |
| `analysis_conclusion` | 无此字段 | `incomplete` |
| `page_coverage` | 无此字段 | **0.0** |
| `scanned_page_count` | 无此字段 | **1** |
| 检出问题数 | 0 | 0 |

这正是缺口 B-01 / B-03 的原症状：**一页文本都没提取到，却报 `done` + 0 问题**，
使用者会以为"审过了没问题"。整改后同一份输入落 `review_required` + `incomplete`，
并给出 `page_coverage=0.0`、`scanned_page_count=1` 作为转人工的依据。
本轮按既定决策**不做自动 OCR**，只做检测 + 转复核。

## 4. 本次回放暴露的数据问题（进入历史遗留清理）

| 编号 | 现象 | 数量 | 处置 |
|---|---|---|---|
| H-1 | `report_id` 冲突：3 个 job 共用 `1a94158a-f92d-47e4-8dc6-2708f9982c9f`（2 个不同 checksum）、2 个 job 共用 `3d54ad55-9b49-4fe6-a5d5-177e63b9c4fd`（2 个不同 checksum） | 2 组 / 5 job | 属 M1 Task 5 修复前的遗留；修复方案见 `docs/LEGACY_DATA_CLEANUP_PLAN_2026-08-27.md`，**待确认后执行** |
| H-2 | 39 个任务停在 `processing`、39 个停在 `queued` | 78 | 都是开发期被中断的运行残留。队列 `resume_on_start` 会在 worker 启动时重新领取；上线前建议先清理或重跑，避免把开发垃圾带进生产统计 |
| H-3 | 438 个任务只上传未分析 | 438 | 不是缺陷（用户未发起分析），但会拉低"整体完成率"类统计口径，报表需按 `uploaded` 单独分档（回放已单列 `unnormalized:uploaded`） |
| H-4 | 历史产物无 `page_coverage` / `analysis_conclusion` | 766 | 只能靠重新分析补齐；本轮不追溯（见第 5 节） |

## 5. 局限（发布决策必须知道）

1. **这是离线指标回放，不是重新分析。** 它读的是产物里记了什么，不重跑解析、不调 AI。
   因此"整改后指标"只能靠第 3 节的抽样重跑证明，不能靠回放本身证明。
2. **无 Golden Corpus，因此没有召回率/精确率。** 本轮所有指标都是结构性的
   （覆盖率、unknown 比例、证据完整率、`report_id` 唯一性、终态分布）。
   它们能证明"没有静默失败、没有虚假成功"，**不能证明业务漏检率达标**。
3. **抽样重跑只有 2 份材料**（1 份真实 34 页 + 1 份 0 文本页），
   是定性验证而非统计意义上的验证。
4. **历史数据不追溯。** 本轮修复只保证"新任务不再出现虚假成功 / 年份兜底 / 身份碰撞"，
   历史 766 个任务的产物保持原样（只读）。批量重跑属独立变更，需单独评审。

## 6. 复现命令

```bash
# 只读回放（输出不能落在 uploads/ 内）
python scripts/replay_analysis.py --uploads uploads --output outputs/replay_2026-08-27.json --print-summary

# CI 里的结构性门禁（无数据时跳过并说明，见 docs/CI_BUSINESS_GATE.md）
python scripts/check_replay_thresholds.py --report outputs/replay_2026-08-27.json
```
