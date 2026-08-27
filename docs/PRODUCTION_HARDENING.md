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
- 登录会话
  - 会话令牌已支持多 worker / 重启后继续校验
  - 生产环境建议固定配置 `USER_SESSION_SECRET`
- 首次登录强制改密（缺口 B-07）
  - 播种出来的默认管理员带 `must_change_password`，未改密时除
    `/api/auth/login`、`/api/auth/me`、`/api/auth/logout`、`/api/auth/change-password`
    以外的端点一律返回 403（响应头带 `X-Password-Change-Required: 1`）
  - 开关 `REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE`，生产默认开启
  - 已存在的账号（`users.json` 里没有该字段）不受影响
- 安全响应头（缺口 B-08）
  - 后端 `src/security.SecurityHeadersMiddleware`：CSP、`X-Content-Type-Options`、
    `X-Frame-Options`、`Referrer-Policy`、HSTS（HTTPS 或 `X-Forwarded-Proto: https` 时下发）
  - `/docs`、`/redoc`、`/openapi.json` 使用单独一份宽松 CSP，否则 Swagger UI 白屏
  - 前端 `app/middleware.ts`：CSP 等头本就存在，本轮补齐生产环境的 HSTS
- 审计日志
  - 管理员操作会写入 `AUDIT_LOG_PATH`
- 健康检查增强
  - `ready` 会返回上传限制、队列状态、数据库可达性、AI 服务可达性、审计日志目录可写性
- PDF 解析资源隔离（缺口 P2-05 / B-09）
  - 解析在独立、可终止的子进程中执行，超时后 `terminate` → `kill`，不再让杀不掉的线程继续烧 CPU
  - 上限：`PDF_PARSE_TIMEOUT_SEC`（跨平台）、`PDF_PARSE_MAX_PAGES`（默认跟随 `MAX_UPLOAD_PAGES`）、
    `PDF_PARSE_MAX_TEXT_CHARS` / `PDF_PARSE_MAX_TABLE_CELLS`（跨平台）、
    `PDF_PARSE_MEMORY_MB`（`RLIMIT_AS`，**仅 POSIX 生效**；Windows 无等价能力）
  - 超时/超限/解析失败一律落 `status=error` + `analysis_conclusion=analysis_error`，
    错误码为 `pdf_parse_timeout` / `pdf_parse_limit_exceeded` / `pdf_parse_failed`
  - 开关 `PDF_PARSE_ISOLATION_ENABLED`，生产默认开启
- 备份与恢复（缺口 B-12）
  - `scripts/backup_all.py` 覆盖 `UPLOAD_DIR` + PostgreSQL + 审计日志三件套，产出带 sha256 的 `manifest.json`
  - `restore` 默认拒绝写入当前 `UPLOAD_DIR` / `DATABASE_URL` / `AUDIT_LOG_PATH`，需显式 `--force`
  - 一次真实恢复演练记录见 `docs/BACKUP_RESTORE_DRILL_2026-08-27.md`

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

具体指标名、阈值依据与处置动作见 `docs/OBSERVABILITY_AND_ALERTS.md`（指标端点 `/metrics` 与结构化日志字段都在那里说明）。

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
USER_SESSION_SECRET=<strong-session-secret>
DATABASE_URL=postgres://...
JOB_QUEUE_ROLE=api
JOB_QUEUE_INLINE_FALLBACK=false
GOVBUDGET_AUTH_ENABLED=true
GOVBUDGET_API_KEY=<strong-secret>
```
