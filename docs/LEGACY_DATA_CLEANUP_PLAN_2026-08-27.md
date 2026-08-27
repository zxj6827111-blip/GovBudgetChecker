# 历史遗留清理方案（待确认后执行）

- 日期：2026-08-27
- 分支：`feat/prod-readiness-m1`
- 数据库：本机真实 PostgreSQL 15.17，`DATABASE_URL` 指向开发库 `fiscal_db`
- **状态：全部为方案，未执行任何删改。** 涉及真实数据的操作一律等确认。

---

## 0. 先报告一次由我造成的意外写库（必须先处理）

### 0.1 事实

Task 15.1 的"整改前后对比"脚本（`tmp/before_after_demo.py`）本意是完全隔离运行，
代码里写了 `os.environ.pop("DATABASE_URL", None)`。但 `api/main.py` 导入链上有
`load_dotenv()`，**它把 `.env` 里的 `DATABASE_URL` 又装回了环境**，于是结构化入库
没有跳过，向开发库真实写入了数据。

实测写入范围（`created_at >= 2026-08-27T00:00:00Z`，全部集中在 01:34–01:36Z）：

| 表 | 新增行数 | 说明 |
|---|---|---|
| `org_dept_annual_report` | 3 | 见下表 |
| `org_dept_table_data` | 16 | 外键 `report_id`，`ON DELETE CASCADE` |
| `org_dept_line_items` | 374 | 外键 `report_id`，`ON DELETE CASCADE` |
| `org_unit` | 1 | 组织匹配新建的单位 |

三条报告行：

| id | file_name | year | scope_key | created_at (UTC) |
|---|---|---|---|---|
| `d3d94c37-0b05-4c04-a4ae-c4606a2249ee` | 上海市普陀区人民政府石泉路街道办事处26单位.pdf | NULL | `2a6d822a…`（checksum） | 01:34:25 |
| `7ea949a8-b4b4-4c0c-a9a3-d78a8e47dec5` | split_mode.pdf | NULL | `64584633…`（checksum） | 01:34:28 |
| `72752aba-dfb4-4b61-afb9-d2ffa5e4b2ea` | 上海市普陀区人民政府石泉路街道办事处26单位.pdf | 2026 | `''` | 01:36:35 |

### 0.2 顺带得到的正面证据

这三行恰好是 M1 修复在**真实库**上的直接证据：

- `year=NULL` 而不是 `2000` → 缺口 B-02 / P0-03 的兜底已消除（历史行里还留着 `year=2000`）；
- 年份识别失败时 `scope_key` 落 checksum → 缺口 P0-09 的身份退化策略生效，
  两份不同文档不会被并进同一个 `report_id`；
- 年份可信时 `scope_key=''` → 保持原有 `(dept, unit, year, type)` 归并语义，未破坏兼容。

### 0.3 建议处置（等确认）

回滚这 4 张表的今日新增行，恢复到我操作之前的状态：

```sql
BEGIN;
-- 先留档：把要删的行导出，便于事后核对。
-- 父行与两张子表都要留档——`ON DELETE CASCADE` 会静默带走 16 + 374 行子数据，
-- 只存父行等于把绝大部分被删内容丢掉了（独立复核指出的对称性缺失）。
CREATE TABLE IF NOT EXISTS _rollback_20260827_reports AS
SELECT * FROM org_dept_annual_report
WHERE id IN (
  'd3d94c37-0b05-4c04-a4ae-c4606a2249ee',
  '7ea949a8-b4b4-4c0c-a9a3-d78a8e47dec5',
  '72752aba-dfb4-4b61-afb9-d2ffa5e4b2ea'
);

CREATE TABLE IF NOT EXISTS _rollback_20260827_table_data AS
SELECT * FROM org_dept_table_data
WHERE report_id IN (SELECT id FROM _rollback_20260827_reports);

CREATE TABLE IF NOT EXISTS _rollback_20260827_line_items AS
SELECT * FROM org_dept_line_items
WHERE report_id IN (SELECT id FROM _rollback_20260827_reports);

-- 留档行数自检：删之前先确认留档数量与实测一致（3 / 16 / 374）
SELECT
  (SELECT count(*) FROM _rollback_20260827_reports)    AS reports,     -- 期望 3
  (SELECT count(*) FROM _rollback_20260827_table_data) AS table_data,  -- 期望 16
  (SELECT count(*) FROM _rollback_20260827_line_items) AS line_items;  -- 期望 374

-- 子表由 ON DELETE CASCADE 自动清理（table_data 16 行、line_items 374 行）
DELETE FROM org_dept_annual_report
WHERE id IN (
  'd3d94c37-0b05-4c04-a4ae-c4606a2249ee',
  '7ea949a8-b4b4-4c0c-a9a3-d78a8e47dec5',
  '72752aba-dfb4-4b61-afb9-d2ffa5e4b2ea'
);

-- 校验：应返回 0
SELECT count(*) FROM org_dept_annual_report WHERE created_at >= '2026-08-27 00:00:00+00';
COMMIT;  -- 数字不对就 ROLLBACK;
```

留档表用完（确认无需还原后）再单独删除：
`DROP TABLE _rollback_20260827_line_items, _rollback_20260827_table_data, _rollback_20260827_reports;`

新增的那 1 个 `org_unit` 需要单独确认：它可能与既有组织重复，也可能是有用的组织记录。
建议先看一眼再决定（下面是只读查询）：

```sql
SELECT id, department_id, name, created_at FROM org_unit
WHERE created_at >= '2026-08-27 00:00:00+00';
```

### 0.4 防复发

- 教训：**`tests/conftest.py` 的隔离只保护 pytest**。一次性脚本走的是自己的进程，
  `load_dotenv()` 会把 `.env` 装回来，`os.environ.pop` 挡不住。
- 正确做法（两种任选）：
  1. 显式设置 `DATABASE_URL=""`（空串）而不是 pop —— `load_dotenv()` 默认不覆盖已存在的键；
  2. 用 `GOVBUDGET_TEST_DATABASE_URL` + `tests/` 里的 fixture 走测试路径。
- 本轮已把这条写进本文件，作为后续同类脚本的前置检查项。

---

## 1. `split_mode.pdf` 测试残留

### 1.1 现状（只读实测）

`org_dept_annual_report` 现有 **2** 行 `file_name='split_mode.pdf'`：

| id | year | scope_key | created_at | 来源 |
|---|---|---|---|---|
| `b98e1095-4942-4c7c-9472-c3c7a45d57cd` | **2000** | `''` | 2026-06-08 08:14:27Z | `tests/test_queue_split_mode.py` 在隔离修复前写入 |
| `7ea949a8-b4b4-4c0c-a9a3-d78a8e47dec5` | NULL | `64584633…` | 2026-08-27 01:34:28Z | **本次由我误写（见第 0 节）** |

`split_mode.pdf` 是 267 字节的合成样本，不是任何真实材料；`year=2000` 正是缺口 B-02 的症状。

### 1.2 影响

- 污染 `year` 分布统计（当前 27 行里 `year=2000` 仅此 1 行）；
- 会被组织/部门报表当成一份真实材料展示；
- 无业务下游依赖（下面的只读查询可确认子表行数）。

### 1.3 清理 SQL（等确认）

```sql
-- 第一步：只读确认影响面
SELECT r.id, r.year, r.scope_key,
       (SELECT count(*) FROM org_dept_table_data d WHERE d.report_id = r.id) AS table_rows,
       (SELECT count(*) FROM org_dept_line_items l WHERE l.report_id = r.id) AS line_rows
FROM org_dept_annual_report r
WHERE r.file_name = 'split_mode.pdf';

-- 第二步：留档 + 删除（子表 CASCADE 自动清理）
BEGIN;
CREATE TABLE IF NOT EXISTS _rollback_20260827_split_mode AS
SELECT * FROM org_dept_annual_report WHERE file_name = 'split_mode.pdf';
DELETE FROM org_dept_annual_report WHERE file_name = 'split_mode.pdf';
SELECT count(*) FROM org_dept_annual_report WHERE file_name = 'split_mode.pdf';  -- 期望 0
COMMIT;
```

### 1.4 回滚方式

```sql
BEGIN;
INSERT INTO org_dept_annual_report SELECT * FROM _rollback_20260827_split_mode;
COMMIT;
```

注意：子表数据被 CASCADE 删掉后**无法**由这条回滚恢复。如果第一步查出子表行数不为 0，
应先把子表也一并留档：

```sql
CREATE TABLE IF NOT EXISTS _rollback_20260827_split_mode_tabledata AS
SELECT * FROM org_dept_table_data WHERE report_id IN
  (SELECT id FROM org_dept_annual_report WHERE file_name='split_mode.pdf');
CREATE TABLE IF NOT EXISTS _rollback_20260827_split_mode_lineitems AS
SELECT * FROM org_dept_line_items WHERE report_id IN
  (SELECT id FROM org_dept_annual_report WHERE file_name='split_mode.pdf');
```

### 1.5 风险与建议

风险低（合成数据、无真实业务含义）。建议执行，并在执行前跑一次
`python scripts/backup_all.py`（三件套备份已在 `docs/BACKUP_RESTORE_DRILL_2026-08-27.md` 演练过）。

---

## 2. 两组历史 `report_id` 冲突

### 2.1 现状（只读实测）

回放检出 2 组冲突（`docs/HISTORICAL_REPLAY_2026-08-27.md` 第 4 节）：

| report_id | 共用任务数 | 不同 checksum 数 | 数据库当前行 |
|---|---|---|---|
| `1a94158a-f92d-47e4-8dc6-2708f9982c9f` | 3 | 2 | dept `9283167d…` / unit `ee95f8c8…` / year 2026 / BUDGET / `上海市普陀区城市管理行政执法局2026年度单位预算公开.pdf` / `scope_key=''` |
| `3d54ad55-9b49-4fe6-a5d5-177e63b9c4fd` | 2 | 2 | dept `9562ebdb…` / unit `f76a493c…` / year 2024 / BUDGET / `上海市普陀区规划和自然资源局 2024 年度部门决算.pdf` / `scope_key=''` |

即：**多份 checksum 不同的文档被 `ON CONFLICT` 并进了同一行**，
`file_name`/`file_hash` 只保留了最后一次写入的那份。第二组还有一个附带问题：
文件名写着"部门决算"，`report_type` 却是 `BUDGET`，说明当时的类型判定也不可靠。

根因已在 M1 Task 5 修掉（`_resolve_report_scope_key`：`fallback_name` 匹配或年份未识别时
按 checksum 建身份），**只防新增，不追溯**。

### 2.2 修复方案（按 `scope_key` 重新分配身份）

原则：**不动现有行的 `id`**（有外键与历史引用），只为"被挤掉"的文档补建新行。

```sql
-- 第 0 步：建映射表，保留旧 -> 新 的身份映射（PLAN 第 18.2 节要求保留映射）
CREATE TABLE IF NOT EXISTS report_id_remap (
    old_report_id  uuid        NOT NULL,
    new_report_id  uuid        NOT NULL,
    job_id         text        NOT NULL,
    file_hash      text        NOT NULL,
    file_name      text        NOT NULL,
    reason         text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (old_report_id, file_hash)
);
COMMENT ON TABLE report_id_remap IS
  'M1 Task 5 之前 report_id 碰撞的身份重分配映射；查历史结果时用它把旧 ID 翻译到新 ID';
```

对每一组冲突：

1. 保留当前那一行作为"其中一份文档"的身份（`file_hash` 就是它对应的那份）；
2. 对其余 checksum，各插入一行新报告，`scope_key = <该文档 checksum>`
   —— 与整改后新代码的口径一致，且 `uq_dept_report_scope_type_key`
   （`department_id, unit_id, COALESCE(year,-1), report_type, scope_key`）保证不会再撞；
3. 把 (旧 id, 新 id, job_id, file_hash) 写入 `report_id_remap`；
4. `uploads/<job_id>/status.json` 里的 `structured_ingest.ps_sync.report_id`
   **保持不动**（`uploads/` 只读约定），改由查询侧经 `report_id_remap` 翻译。

需要人工先确认的输入（我不能替你决定）：

- 每个 job 的 `file_hash` 与"到底属于哪个单位/哪个年度"的对应关系，
  尤其是 `3d54ad55…` 这组还夹着"决算材料被标成 BUDGET"的问题；
- 是否顺带修正 `report_type`（改类型会影响既有报表口径）。

因此第 2 项建议的执行顺序是：**先出一份 5 个 job 的逐份归属确认表**（我可以生成只读清单），
确认后再执行插入 + 映射写入。

### 2.3 回滚方式

```sql
BEGIN;
DELETE FROM org_dept_annual_report WHERE id IN (SELECT new_report_id FROM report_id_remap);
DELETE FROM report_id_remap;
COMMIT;
```

因为方案只做"新增行 + 新增映射表"，不改任何既有行，所以回滚是纯删除新增物，
不会丢历史数据。

### 2.4 风险

中。新增报告行会让"报告总数"统计上升（27 → 30 左右），
对外报表若按报告数统计需同步说明；另外必须确保查询侧接入 `report_id_remap`，
否则旧 ID 查不到新拆出来的那几份。

---

## 3. `scope_key` 历史回填

### 3.1 现状

- 27 行里 **25 行 `scope_key=''`**（含我误写的 1 行 `72752aba`），2 行是 checksum。
- `scope_key=''` 的语义是"按 `(dept, unit, year, type)` 归并"，
  这是整改前唯一的归并方式。
- 唯一约束：`uq_dept_report_scope_type_key(department_id, unit_id, COALESCE(year,-1), report_type, scope_key)`。

### 3.2 结论：不做全量回填

理由：

1. **不必要**。`scope_key=''` 且 `(dept, unit, year, type)` 组合唯一的行，
   语义上没有任何问题，回填 checksum 只会把归并粒度从"报告"变成"文档"，
   反而破坏"同一份报告的多个版本归并到一行"这一既有语义。
2. **有风险**。回填等于给每行换身份维度，会触发唯一约束重算；
   若中途失败，表处于半新半旧状态，比不动更糟。
3. **真正需要处理的只有第 2 项那 2 组冲突**，它们是"归并错了"的实例，
   按第 2 节单独处理即可。

### 3.3 若坚持回填，建议的最小策略

只回填"确定被 fallback 匹配错误合并"的行，不动其余：

```sql
-- 只读：找出可能被错误合并的候选（同一归并键下出现过多个 file_hash）
SELECT r.department_id, r.unit_id, r.year, r.report_type,
       count(DISTINCT r.file_hash) AS distinct_hashes,
       array_agg(DISTINCT r.file_name) AS names
FROM org_dept_annual_report r
WHERE r.scope_key = ''
GROUP BY 1,2,3,4
HAVING count(DISTINCT r.file_hash) > 1;
```

注意：这条查询**查不出全部**错误合并——被 `ON CONFLICT DO UPDATE` 挤掉的那份文档
在表里已经没有痕迹了，只能靠 `uploads/*/status.json` 里的 `report_id` 反查
（这正是第 2 节用回放结果定位冲突的原因）。

### 3.4 风险评估

| 选项 | 风险 | 收益 |
|---|---|---|
| 不回填（**推荐**） | 低：历史归并语义保持不变 | 无需变更 |
| 只修 2 组冲突（第 2 节） | 中：需人工确认归属 | 消除已知的身份错误 |
| 全量回填 checksum | 高：改变全部历史行的归并语义，唯一约束重算，回滚复杂 | 口径统一，但对现有数据无实际收益 |

---

## 4. 已被 git 跟踪的一次性脚本（需你决定删留）

`git ls-files` 确认这 5 个文件**已被跟踪**，因此 Task 14.2 没有擅自删除。
它们都是 2026-03-11 提交 `feat: refine UI components, engine logic and update tests` 带进来的
一次性代码改写脚本（`fs.readFileSync` + 字符串替换 + 写回），针对的是当时的 `app/` 组件：

| 文件 | 大小 | 目标文件 | 用途 |
|---|---|---|---|
| `refactor_page_accordion.js` | 3.9 KB | `app/app/page.tsx` | 把页面改成折叠面板结构 |
| `refactor_org_accordion.js` | 4.1 KB | `app/app/components/OrganizationDetailView.tsx` | 组织详情改折叠面板 |
| `apply_ui_patch.js` | 10.0 KB | `app/app/components/OrganizationDetailView.tsx` | 一次性 UI 补丁 |
| `add_deduped_ui.js` | 2.6 KB | `app/app/components/OrganizationDetailView.tsx` | 加去重后的 UI 片段 |
| `fix_tsx.js` | 0.6 KB | `PipelineStatus.tsx` 等 | 批量修 TSX 语法 |

判断与建议：

- 它们**已经执行过**，产物就是当前的 `app/` 代码，脚本本身不再被任何构建或测试引用；
- 再次执行只会二次改写已经变了的文件，属于危险动作；
- **建议归档到 `scripts/legacy/` 而不是直接删**（2026-08-27 独立复核意见，已采纳）：
  理由是保留历史可追溯性——真要回看"当年这段 UI 是怎么批量改出来的"，
  从 git 历史里翻已删文件比看一个现存目录费事得多；
  归档时给每个文件头加"已执行、勿再运行"注释，并在 `scripts/legacy/README.md`
  写明目标文件与执行日期。
- 若倾向彻底清理，直接 `git rm` 也可接受（历史仍在 git 里可查），风险同样低。

无论归档或删除，都需要你点头，我不擅自动已跟踪文件。

---

## 5. 其它遗留（非数据库）

| 项 | 现状 | 建议 |
|---|---|---|
| 6 个临时目录删不掉 | `.codex_pip_tmp`、`.pytest-acceptance-final-1786323806521`、`pip_tmp_20260427143950`、`piptmpdl_20260427144944`、`pytest_basetemp_full_20260427145110`、`pytest_basetemp_probe2_20260427145247`；`shutil.rmtree`（含清只读属性）与 `cmd rmdir /s /q` 均报 NTFS ACL 拒绝访问（WinError 5） | 需要提权：`takeown /f <dir> /r /d y && icacls <dir> /grant %USERNAME%:F /t` 后再删。都在 `.gitignore` 覆盖内，不影响版本库与 CI，可以放着 |
| `api/database.db` | 未被跟踪的本地 SQLite 文件 | 它是数据库不是日志，删除有丢本地数据风险，Task 14.2 未动。建议你确认无用后再删 |
| `migcheck_tmp` schema | 我在验证"空库迁移"路径时在开发库里新建的临时 schema，含 18 个迁移建出的空表 | 验证已完成，建议清理：`DROP SCHEMA migcheck_tmp CASCADE;`（该 schema 由我创建、无业务数据）。未经确认我不执行 |

---

## 6. 执行顺序建议

1. 先跑 `python scripts/backup_all.py`（三件套备份）；
2. 第 0 节：回滚我误写的 3 + 16 + 374 + 1 行；
3. 第 1 节：清理 `split_mode.pdf` 残留；
4. 第 5 节：`DROP SCHEMA migcheck_tmp CASCADE`；
5. 第 2 节：先出 5 个 job 的归属确认表，确认后再重分配身份并写 `report_id_remap`；
6. 第 3 节：按建议**不做**全量回填；
7. 第 4 节：决定一次性脚本删或归档。
