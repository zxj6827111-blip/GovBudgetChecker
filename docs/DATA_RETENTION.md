# 数据留存契约

## 留存位置

- 原始 PDF：`UPLOAD_DIR/<job_id>/<filename>`。生产环境必须把 `UPLOAD_DIR` 挂载到持久化文件卷，并由 API 与 Worker 共享。
- PostgreSQL：保存任务快照、PDF 哈希、文件大小、MIME 类型、稳定 `storage_key`、材料版本、结构化单元格和事实、规则/AI 审校结果。
- `storage_key` 始终相对于存储根目录，例如 `a1b2.../上海市普陀区人民政府办公室2024年度部门决算.pdf`，不保存机器绝对路径。

## 写入时机

1. 上传成功后立即写入本地状态文件，并在配置 `DATABASE_URL` 时写入 PostgreSQL 任务快照。
2. 启动分析、处理中的状态变化和最终结果继续更新同一任务快照。
3. 结构化入库创建或复用材料版本，并把表格、事实和待人工复核项关联到该版本。

## 边界与恢复

- PDF 二进制不写入 PostgreSQL，避免数据库膨胀；备份必须同时覆盖 `UPLOAD_DIR`、PostgreSQL 和审计日志。三件套的备份/校验/恢复统一用 `scripts/backup_all.py`（`scripts/db_backup.py` 只覆盖数据库，已不足以满足本契约）；一次真实恢复演练的记录见 `docs/BACKUP_RESTORE_DRILL_2026-08-27.md`。
- 数据库暂不可用时，任务目录中的 `persistence.json` 会标记同步状态，便于后续排查或重试；生产环境应启用 `READY_REQUIRE_DATABASE=true` 阻止未接通数据库的实例接流量。
- 问题处置状态的同步结果单独写入 `UPLOAD_DIR/.issue_workflow_persistence.json`；失败时保留 `pending_retry`，应用启动会重放最新恢复快照，历史导入工具也可手动重放。
- 迁移 `2026-07-13_0015_document_storage_metadata` 为已有材料版本补充原件存储元数据列，并由启动时迁移流程自动执行。
- 迁移 `2026-07-13_0016_workflow_state_mirror` 为问题处置状态和整改包提供 PostgreSQL 镜像；`UPLOAD_DIR/.issue_workflow.json` 仍是断库恢复源。

## 历史数据导入

- 先执行只读盘点：`python scripts/migrate_historical_uploads.py --uploads-dir uploads`。
- 确认盘点结果后，设置有效的 `DATABASE_URL`，再执行：`python scripts/migrate_historical_uploads.py --uploads-dir uploads --apply`。
- 工具仅读取 `uploads/*/status.json`、同目录 `structured_ingest.json` 和 `.issue_workflow.json`；不会复制、删除或重命名原始 PDF。

## 恢复与完整性

- 上传去重在数据库快照写入前完成；被拒绝的重复文件不会留下 `analysis_jobs` 孤儿记录。
- `persistence.json` 为 `pending_retry` 的任务会在应用启动时自动重放。工作流旧快照没有 `revision` 时会升级为 revision 1 后再镜像。
- 历史导入会把可确认的封面年度、材料类型和自动匹配机构写入迁移审计字段；原先标记为 `manual` 或 `confirmed` 的机构、年度和类型不会被自动覆盖。发生冲突时写入 `historical_metadata_correction` 和 `metadata_reanalysis_required=true`，必须在人工确认后重新分析，旧规则结果不会被伪装成新结果。
- `--apply` 输出 `failed_job_ids`。只要有任务或工作流镜像失败，进程以非零退出；发布或迁移验收应以该字段为空为准。
- 数据库可用但原始上传目录丢失时，持久化任务列表接口仍可用于检索任务与结果；恢复原 PDF 页面预览则仍需要同步恢复 `UPLOAD_DIR` 卷。

## 2026-10-07 恢复范围补齐

组织和用户的文件权威源不在 `UPLOAD_DIR` 内，不能仅用数据库镜像恢复：
`ORG_DATA_DIR/organizations.json`、`ORG_DATA_DIR/job_org_links.json` 和
`USER_FILE`（默认 `USER_DATA_DIR/users.json`）现在随同备份写入
`application-state.tar.gz`。组织目录读写通过跨进程锁、磁盘版本刷新和原子替换协调，
避免 API 新建组织后 Worker 仍使用旧目录，或并发任务关联互相覆盖。

完整恢复须同时指定四项目标，例如：

```bash
.venv/bin/python scripts/backup_all.py restore --archive /absolute/path/archive \
  --uploads-dir /temporary/restore/uploads \
  --database-url "$RECOVERY_DATABASE_URL" \
  --audit-log /temporary/restore/audit.jsonl \
  --state-dir /temporary/restore/state
```

重启时把 `UPLOAD_DIR`、`DATABASE_URL`、`AUDIT_LOG_PATH` 指向恢复目标，
把 `ORG_DATA_DIR`、`USER_DATA_DIR` 指向 `state`；如设置了 `USER_FILE`，
将其指向恢复的 `state/users.json`。部署密钥由原有安全配置单独提供，
不写入备份清单或验收报告。只恢复某一构件属于部分恢复，不能宣布完整恢复。

备份前暂停写入或使用一致性快照；本轮演练先停止一次性 API、Worker 和前端，
再备份、校验并恢复到另一个临时数据库和空目录。恢复前校验构件哈希，
拒绝路径穿越、链接和特殊成员；上传和应用状态目标拒绝覆盖已有内容。
备份目录默认权限为 `0700`，构件及包含密码哈希的应用状态文件为 `0600`。
旧备份缺少应用状态时必须明确补齐，不能把启动时重新创建账号当作恢复成功。

本轮原始演练记录在 `outputs/completion_20261007_round17/extended_preflight_v5/`。
检查覆盖全部数据库表的行数/有序 JSON 摘要、全部上传文件 SHA、审计 SHA、
应用状态 SHA、重启登录、组织加载、阻塞复核状态及四格式导出 SHA。
更大真实材料批次的最终演练记录以第十七轮报告所引用的最终回执为准。
