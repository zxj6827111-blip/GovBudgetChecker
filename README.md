# GovBudgetChecker

政府预算/决算公开材料自动审校系统。

## 当前状态（2026-03-02）
- 生产就绪改造（Phase 1）已完成：鉴权默认开启、持久化上传目录、可恢复任务队列、就绪检查、CI/E2E 门禁、发布与回滚手册。
- 最近一次本地验证：`ruff`、`mypy`、`pytest`、前端 `build`、`Playwright E2E` 均通过。

## 核心能力
- 上传 PDF 并创建分析任务。
- 基于规则与 AI 辅助生成问题列表。
- 提供任务状态流转：`queued -> processing -> done/error`。
- 导出报告：`JSON` / `CSV` / `PDF`。
- 组织/部门管理与任务关联。

## 技术栈
- 前端：Next.js + Tailwind
- 后端：FastAPI
- 引擎：Python（规则与解析流水线）
- 测试：pytest + Playwright
- 质量：ruff + mypy

## 快速开始（本地开发）

### 本地目录建议（与云端保持一致）
推荐把运行数据放到仓库外部目录，例如：

- 代码目录：`D:/软件开发/TRAE/GovBudgetChecker`
- 数据目录：`D:/软件开发/TRAE/GovBudgetChecker-data`

对应 `.env` 建议配置：

```env
HOST_DATA_DIR=D:/软件开发/TRAE/GovBudgetChecker-data/data
HOST_LOGS_DIR=D:/软件开发/TRAE/GovBudgetChecker-data/logs
HOST_SAMPLES_DIR=D:/软件开发/TRAE/GovBudgetChecker-data/samples
HOST_UPLOADS_DIR=D:/软件开发/TRAE/GovBudgetChecker-data/uploads
```

这样本地更新代码、切换分支或重建容器时，不会覆盖运行数据，行为与云端一致。

### 1. 安装依赖
```bash
pip install -r api/requirements.txt
npm --prefix app install
npx --yes playwright install --with-deps chromium
```

### 2. 启动服务
```bash
# 终端 1：后端（默认鉴权开启，自动注入本地开发 key）
make backend

# 终端 2：前端
make frontend
```

或一键并行：
```bash
make dev
```

### 3. 健康检查
```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

## 认证说明
- 默认开启 API Key 认证。
- 请求头支持：
  - `X-API-Key: <your_key>`
  - `Authorization: Bearer <your_key>`
- 免认证端点：`/health`、`/api/health`、`/ready`、`/api/ready`、`/docs`、`/openapi.json`。

关键变量：
- `GOVBUDGET_AUTH_ENABLED`（默认 `true`）
- `GOVBUDGET_API_KEY`（生产环境必须配置）

## API 使用流程（最小闭环）

### 1) 上传文件
```bash
curl -X POST "http://127.0.0.1:8000/upload" \
  -H "X-API-Key: dev-local-key" \
  -F "file=@/path/to/sample.pdf;type=application/pdf"
```
返回中获取 `job_id`。

### 2) 启动分析
```bash
curl -X POST "http://127.0.0.1:8000/api/analyze/<job_id>" \
  -H "X-API-Key: dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"mode":"dual","use_local_rules":true,"use_ai_assist":true}'
```

### 3) 轮询状态
```bash
curl "http://127.0.0.1:8000/api/jobs/<job_id>/status" \
  -H "X-API-Key: dev-local-key"
```

### 4) 下载报告
```bash
# JSON
curl "http://127.0.0.1:8000/api/reports/download?job_id=<job_id>&format=json" \
  -H "X-API-Key: dev-local-key"

# CSV
curl "http://127.0.0.1:8000/api/reports/download?job_id=<job_id>&format=csv" \
  -H "X-API-Key: dev-local-key"

# PDF
curl -o report.pdf "http://127.0.0.1:8000/api/reports/download?job_id=<job_id>&format=pdf" \
  -H "X-API-Key: dev-local-key"
```

## 质量门禁与测试

```bash
# 代码质量
make lint
make typecheck

# 后端测试
make unit

# 前端构建与 E2E
make frontend-build
make e2e

# 全量门禁
make test
```

## 关键环境变量
- `UPLOAD_DIR`：上传和任务产物目录（生产必须挂载持久化卷）
- `MAX_UPLOAD_MB`：上传大小限制（默认 30）
- `MAX_UPLOAD_PAGES`：PDF 页数限制（默认 800）
- `UPLOAD_CHUNK_BYTES`：流式写入分块大小
- `AUDIT_LOG_PATH`：管理员敏感操作审计日志输出位置
- `AI_ASSIST_ENABLED`：是否启用 AI 辅助（默认 `true`）
- `AI_EXTRACTOR_URL`：AI 抽取服务地址
- `DATABASE_URL`：可选，配置后 `ready` 会检查可达性
- `JOB_QUEUE_ENABLED`：任务队列开关（默认开启）

## 生产强化建议
- 上传产物与任务目录建议放在持久化卷，容器重启后仍可恢复；若上云要做多实例，可进一步接入对象存储。
- `ready` 端点会额外检查上传目录、数据库、AI 服务、队列状态以及审计日志目录是否可写。
- 管理员操作（组织创建/修改/删除、导入、批量重分析、结构化清理）会写入 `AUDIT_LOG_PATH`。
- 建议把反向代理上传大小限制与 `MAX_UPLOAD_MB`、`MAX_UPLOAD_PAGES` 保持一致。
- 建议定期备份 `UPLOAD_DIR`、数据库和 `AUDIT_LOG_PATH`，并至少做一次恢复演练。

## 目录结构（简化）
```text
GovBudgetChecker/
  api/                  FastAPI 入口与路由
  app/                  Next.js 前端
  src/                  引擎与服务实现
  rules/                规则文件
  tests/                pytest
  e2e/                  Playwright
  docs/                 设计与发布文档
  uploads/              本地任务产物目录（可持久化）
```

## 发布相关文档
- `docs/PROD_READINESS_PLAN.md`
- `docs/RELEASE_RUNBOOK.md`
- `CLOUD_DEPLOY_EXECUTION_SHEET.md`

## 备注
- 本项目当前采用 FastAPI `on_event(startup/shutdown)` 管理生命周期，后续可迁移到 lifespan。
- 若发现文档和代码不一致，以代码与 `make test` 结果为准。

## Split Deployment (API + Worker)

To isolate request latency from heavy PDF analysis, run API and worker as separate processes.

### API process (enqueue only)

```bash
JOB_QUEUE_ROLE=api JOB_QUEUE_INLINE_FALLBACK=false make backend
```

### Worker process (consume queue)

```bash
JOB_QUEUE_ROLE=worker make worker
```

### Notes

- `JOB_QUEUE_ROLE=api` means the API only writes queued status and never executes analysis inline.
- `JOB_QUEUE_ROLE=worker` means no HTTP traffic is needed; the process only consumes queued jobs.
- Keep `UPLOAD_DIR` shared between API and worker processes.
