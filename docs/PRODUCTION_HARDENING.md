# 生产部署强化清单

本文补充当前仓库内已落地的生产强化项，以及仍建议在云上部署时完成的配置。

## 已在代码中落地

- 管理员敏感操作保护
  - 组织创建、修改、删除
  - 组织结构导入
  - 按部门批量重分析
  - 旧版结构化入库清理
- 删除前影响预览
  - 删除部门/单位前可看到影响的组织数、单位数、任务关联数
- 上传硬限制
  - `MAX_UPLOAD_MB`
  - `MAX_UPLOAD_PAGES`
  - 重复上传检测（同组织下同 checksum，且年份/类型不冲突时拦截）
- 审计日志
  - 管理员操作会写入 `AUDIT_LOG_PATH`
- 健康检查增强
  - `ready` 会返回上传限制、队列状态、数据库可达性、AI 服务可达性、审计日志目录可写性

## 建议在云环境继续完成

### 1. 持久化与备份

- `UPLOAD_DIR` 挂载持久化卷
- `DATABASE_URL` 指向独立数据库
- 审计日志目录单独持久化
- 建议每日备份：
  - 数据库
  - `UPLOAD_DIR`
  - `AUDIT_LOG_PATH`

### 2. 多实例部署

当前代码已适合：

- API 进程：`JOB_QUEUE_ROLE=api`
- Worker 进程：`JOB_QUEUE_ROLE=worker`

部署多实例时建议：

- API 与 Worker 共享 `UPLOAD_DIR`
- 关闭 API 进程内联回退：`JOB_QUEUE_INLINE_FALLBACK=false`
- 由外部反向代理统一做上传大小、超时和 TLS

### 3. 反向代理建议

- 请求体限制要不小于 `MAX_UPLOAD_MB`
- 请求超时要覆盖大文件上传和分析启动时间
- 开启 gzip / brotli
- 对静态资源与 Next.js 资源做缓存

### 4. 监控告警建议

建议监控以下指标：

- 上传失败率
- 批量任务失败率
- 平均分析耗时
- 队列积压数量
- 磁盘剩余空间
- 数据库连接失败次数
- AI 服务不可达次数

### 5. 人员与权限

- 普通用户：查看、上传、浏览任务
- 管理员：组织维护、导入、批量重跑、结构化清理、删除
- 建议定期审查管理员账号和 `AUDIT_LOG_PATH`

## 推荐环境变量

```env
UPLOAD_DIR=/app/uploads
MAX_UPLOAD_MB=30
MAX_UPLOAD_PAGES=800
AUDIT_LOG_PATH=/app/logs/admin-actions.jsonl
DATABASE_URL=postgres://...
JOB_QUEUE_ROLE=api
JOB_QUEUE_INLINE_FALLBACK=false
GOVBUDGET_AUTH_ENABLED=true
GOVBUDGET_API_KEY=<strong-secret>
```
