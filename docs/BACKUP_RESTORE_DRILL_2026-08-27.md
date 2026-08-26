# 备份与恢复演练记录（B-12）

- 演练日期：2026-08-27
- 关联缺口：`B-12`「无备份恢复演练」、`P2-05`/`B-09`（同批 Task 13）
- 契约依据：`docs/DATA_RETENTION.md`「备份必须同时覆盖 `UPLOAD_DIR`、PostgreSQL 和审计日志」
- 使用工具：`scripts/backup_all.py`（本轮新增；原 `scripts/db_backup.py` 只覆盖数据库）
- 状态：**已完成一次真实备份 + 真实恢复，三件套全部逐字节校验一致**

## 一、为什么新增 `scripts/backup_all.py`

`scripts/db_backup.py` 只做 `pg_dump`/`psql`，缺 `UPLOAD_DIR` 与审计日志两件，
与 `DATA_RETENTION` 契约不符；且它有两个在本机直接跑不通的实现问题：

1. 压缩走外部 `gzip` 可执行文件——Windows 上没有；
2. 直接按名字调用 `pg_dump`——本机 PostgreSQL 15 的 `bin` 不在 PATH 上，
   会直接 `FileNotFoundError`。

新脚本改为：Python `gzip` 压缩、按 `PG_BIN_DIR` → PATH → 常见安装目录顺序解析
`pg_dump`/`psql`，并对每个构件写入 sha256 到 `manifest.json`——没有校验的备份等于没有备份。

## 二、安全约束（演练不许碰真实数据）

| 约束 | 实现方式 |
|---|---|
| 不改动源库 | 只执行 `pg_dump` 与 `SELECT count(*)`；演练前后各取一次全表行数比对 |
| 不改动真实 `uploads/` | 只 `tarfile.add` 读取 |
| 恢复目标不得是线上目标 | `restore` 默认拒绝 `--uploads-dir` / `--database-url` / `--audit-log` 指向当前 `UPLOAD_DIR` / `DATABASE_URL` / `AUDIT_LOG_PATH`，需显式 `--force` |
| 归档防路径穿越 | 解包前逐个成员校验解析后路径必须位于目标目录内，否则中止 |
| 口令不落盘 | `manifest.json` 与所有输出里的连接串一律经 `redact_database_url` 脱敏 |

### 恢复目标为什么用一次性容器而不是本机临时库

本机 `DATABASE_URL` 使用的 `fiscal_user` 实测 `rolcreatedb=false`、`rolsuper=false`：

```
psql -d postgres -c 'CREATE DATABASE govbudget_probe_drill'
错误:  创建数据库权限不够
```

给它提权会改动共享数据库服务的权限模型——演练不该为了跑通去动生产配置。
因此恢复目标改为一次性 `postgres:15` 容器（端口 55432），与本机 PostgreSQL 完全隔离，
演练结束 `docker rm -f` 删除。

## 三、演练环境

| 项 | 值 |
|---|---|
| 源数据库 | `postgres://fiscal_user:***@localhost:5432/fiscal_db`（PostgreSQL 15.17） |
| 源 `UPLOAD_DIR` | `uploads`（2413 个文件，28,550,427 字节） |
| 源审计日志 | `data/audit/admin-actions.jsonl`（1823 行，485,784 字节） |
| 恢复目标数据库 | 一次性容器 `postgres:15`，库 `govbudget_restore_drill`，`127.0.0.1:55432` |
| 恢复目标目录 | `%TEMP%/govbudget-drill/restored-uploads`、`.../restored-audit/` |
| 归档位置 | `%TEMP%/govbudget-drill/archive`（仓库外，未提交） |

## 四、实测结果

### 1) 备份（耗时 2.55s）

| 构件 | 内容量 | 归档大小 | sha256 |
|---|---|---|---|
| `uploads.tar.gz` | 2413 个文件 / 28,550,427 B | 20,641,329 B | `aaa301700451ded1…` |
| `database.sql.gz` | `fiscal_db` 全量 | 1,750,706 B | `0be90fd26f1b0404…` |
| `audit-log/admin-actions.jsonl` | 1823 行 | 485,784 B | `4a544c447dbe37c1…` |

`verify` 子命令对三个构件逐个复算 sha256，结果全部 `ok`。

### 2) 源库基线（30 张表）

关键表行数：

```
analysis_jobs 629    analysis_results 249    fact_fiscal_line_items 3241
fiscal_table_cells 31080    fiscal_column_mappings 307    org_dept_line_items 1362
fiscal_documents 26    fiscal_document_versions 25    org_dept_annual_report 24
org_dept_table_data 159    fiscal_table_instances 171   schema_migrations 18
qc_rule_versions 16    qc_rule_definitions_v2 15    org_units 26    org_unit 22
org_department 11    workflow_issue_records 3    workflow_state_mirror 1
workflow_remediation_packages 1    organizations 1    qc_rule_definitions 1
（其余 7 张表为 0 行）
```

### 3) 恢复

| 步骤 | 结果 |
|---|---|
| 数据库恢复（psql + ON_ERROR_STOP=1） | 成功，1.404s |
| `uploads` 恢复 | 2413 个文件 / 28,550,427 B |
| 审计日志恢复 | 485,784 B，sha256 与源一致 |

### 4) 一致性比对（这是演练的实质）

| 比对项 | 方法 | 结果 |
|---|---|---|
| 数据库 | 逐表 `count(*)`，源库 30 表 vs 恢复库 30 表 | **零差异**（`mismatched={}`，`identical=true`） |
| `uploads` | 逐文件 sha256 集合比对 | 2413 vs 2413，缺失 0、多余 0、校验和不一致 0 |
| 审计日志 | 整文件 sha256 | 一致 |
| 源库未被改动 | 演练前后各取一次全表行数 | 完全相同 |

### 5) 清理

一次性容器 `govbudget-drill-pg` 已 `docker rm -f` 删除。
归档与恢复产物留在系统临时目录（仓库外），未提交。

## 五、恢复操作手册（照抄可用）

```bash
# 1) 备份（只读源数据）。Windows 上需先指定 pg 客户端目录：
#    set PG_BIN_DIR=C:\Program Files\PostgreSQL\15\bin
python scripts/backup_all.py create --output /backups/2026-08-27

# 2) 落地前先校验归档，校验不过一律不许恢复
python scripts/backup_all.py verify --archive /backups/2026-08-27

# 3) 恢复到临时目标（演练 / 灾备验证）
python scripts/backup_all.py restore \
    --archive /backups/2026-08-27 \
    --uploads-dir /tmp/restore-uploads \
    --database-url postgres://user:pw@localhost:5432/drill_db \
    --audit-log /tmp/restore-audit.jsonl

# 4) 真正的灾难恢复：目标就是线上目标，必须显式 --force
python scripts/backup_all.py restore --archive /backups/2026-08-27 \
    --uploads-dir "$UPLOAD_DIR" --database-url "$DATABASE_URL" \
    --audit-log "$AUDIT_LOG_PATH" --force
```

恢复顺序建议：**先数据库、再 `UPLOAD_DIR`、最后审计日志**。
数据库里保存的是 `storage_key`（相对路径）与文件哈希，先有数据库才能核对
`UPLOAD_DIR` 是否恢复齐全；反过来则无从校验。

恢复后必做的核对：

1. `GET /ready?details=true`（管理员会话）确认上传目录可写、数据库可达、审计目录可写；
2. `GET /metrics` 看 `govbudget_report_id_collision_count` 是否为 0；
3. 抽查若干任务的 `status.json` 与库内 `analysis_jobs` 行数是否对齐。

## 六、本轮局限（如实说明）

- **恢复目标是容器内的 PostgreSQL 15，不是本机 PostgreSQL 15.17 实例**。
  两者主版本一致、`pg_dump` 纯文本格式兼容，但严格意义上"恢复到本机实例"
  这一步未做（原因见上文权限说明）。若要覆盖该场景，需要一个具备 `CREATEDB`
  权限的账号，或由 DBA 预先建好空库再执行 `restore`。
- 本次演练规模为 2413 个文件 / 28.5 MB / 31 万行量级数据；
  更大规模下 `tar.gz` 的耗时与内存表现未测。
- 未做定时备份编排（cron / K8s CronJob）与异地副本，脚本只提供单次操作能力；
  备份保留策略沿用 `scripts/db_backup.py` 的 `cleanup_old_backups`，
  尚未接入新脚本，属于后续项。
- 未演练"仅数据库可用、`UPLOAD_DIR` 全丢"的降级场景
  （`docs/DATA_RETENTION.md` 提到该场景下任务列表仍可用、页面预览需要卷恢复）。
