# 环境变量三方对账（Task 14.1 / 缺口 B-10）

- 日期：2026-08-27
- 分支：`feat/prod-readiness-m1`
- 对账三方：**代码实际读取** / **`.env.example` 声明** / **compose 设置或引用**
- 机器可复现：`python scripts/check_env_consistency.py`（已接入 `make lint` 与 CI）
- 对账后结果：`violations: 0`，代码 124 个 / `.env.example` 120 个 / compose 55 个

## 一、为什么要机器对账

人工核对这三方一定会漏。整改前实测到的典型症状：

`docker-compose.ai.yml` 给 backend 传 `RULES_FILE_PATH=/app/engine/rules_v33.py`，
但代码读的是 `RULES_FILE`（`api/config.py:9`、`api/routes/health.py:135`），而且期望值是
YAML 规则集而不是 `.py` 模块。也就是说：**运维以为配了规则集路径，实际完全没生效**，
`/ready` 的 `rules_file_exists` 检查一直在看默认路径。这类问题不会报错，只会安静地跑错。

## 二、代码侧的读取方式（对账难点）

代码读环境变量有四种形态，只扫 `os.getenv` 会漏掉后三种：

| 形态 | 例子 | 说明 |
|---|---|---|
| 直接 `os.getenv` | `os.getenv("UPLOAD_DIR", "uploads")` | AST 直接可见 |
| 包装函数 | `env_flag("JOB_QUEUE_INLINE_FALLBACK", ...)`、`_read_str_env("AI_EXTRACTOR_URL", ...)` | 需要登记函数名 |
| 前缀动态拼装 | `os.getenv(f"{env_prefix}_BASE_URL")`，`env_prefix ∈ {AI_MAIN, AI_BACKUP, AI_LOCATOR}` | AST 取不到常量名，脚本里显式登记前缀×后缀 |
| 配置文件间接指定 | `config/providers.yaml` 的 `api_key_env: "ARK_API_KEY"` | 由 `ai_client.py:279` 按值 `os.getenv` |

`scripts/check_env_consistency.py` 四种都覆盖。

## 三、发现的不一致与处置

### 3.1 compose 设了但代码从不读（dead_compose_var）

| 变量 | 处置 | 理由 |
|---|---|---|
| `RULES_FILE_PATH` | **改为 `RULES_FILE=/app/rules/v3_3.yaml`** | 代码读的是 `RULES_FILE`，且值应为 YAML 规则集 |
| `GEMINI_BASE_URL`、`GEMINI_MAIN_MODEL`、`GEMINI_LOCATOR_MODEL` | 从 backend/worker 环境**移除** | `config/providers.yaml` 里 `base` / `model` 是写死的，只有 `api_key_env`（即 `GEMINI_API_KEY`）会被读取 |
| `CODEX_BASE_URL`、`CODEX_MODEL` | 从 backend/worker 环境**移除** | 同上，仅 `CODEX_API_KEY` 有效 |
| `OPENAI_BASE_URL`、`OPENAI_MODEL` | 从 backend/worker 环境**移除** | 同上，仅 `OPENAI_API_KEY` 有效 |
| `ARK_BASE_URL`、`ARK_MODEL` | **保留在 `ai-extractor` 服务段**，并在检查脚本白名单登记 | 它们是给独立 extractor 服务的透传配置；仓库自带的最小实现（`ai_extractor_service.py`）不读，但换成真实抽取器实现时需要。后端环境里已移除 |

想改 provider 的 base/model，正确做法是用逻辑 slot 变量
（`AI_MAIN_BASE_URL` / `AI_MAIN_MODEL` 等，见 `.env.example` 的 AI 段），
或直接改 `config/providers.yaml`。

### 3.2 `.env.example` 声明了但代码从不读（dead_example_var）

`GEMINI_BASE_URL`、`GEMINI_MAIN_MODEL`、`GEMINI_LOCATOR_MODEL`、`CODEX_BASE_URL`、`CODEX_MODEL`
—— 原来标注为 "Legacy named-provider variables (optional compatibility)"，实测代码不读。
已从 `.env.example` 移除，只保留真正被 `api_key_env` 使用的
`GEMINI_API_KEY` / `CODEX_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `ZHIPU_API_KEY`，
并在注释里写清"只有 `*_API_KEY` 会被读取"。

### 3.3 代码读取但 `.env.example` 完全没提（undeclared_code_var）

共 28 个，已全部补进 `.env.example`（可选项以注释形式给出默认值，复制模板不会改变行为）：

| 分组 | 变量 |
|---|---|
| 任务产物与上传 | `UPLOAD_DIR`、`UPLOAD_CHUNK_BYTES`、`MAX_ORG_IMPORT_MB`、`RULES_FILE` |
| 日志与指标 | `LOG_LEVEL`、`LOG_JSON`、`LOG_FILE`、`METRICS_ENABLED`、`METRICS_API_TOKEN`、`METRICS_CACHE_TTL_SECONDS`、`METRICS_MAX_JOBS` |
| 质量门禁阈值 | `SCANNED_PAGE_MIN_CHARS`、`PAGE_COVERAGE_MIN_RATIO`、`AI_ASSIST_REQUIRED` |
| 解析/规则超时与隔离 | `PIPELINE_TIMEOUT_SEC`、`RULES_TIMEOUT_SEC`、`PDF_PARSE_ISOLATION_ENABLED`、`PDF_PARSE_TIMEOUT_SEC`、`PDF_PARSE_MAX_PAGES`、`PDF_PARSE_MAX_TEXT_CHARS`、`PDF_PARSE_MAX_TABLE_CELLS`、`PDF_PARSE_MEMORY_MB` |
| 安全头与首登改密 | `SECURITY_HEADERS_ENABLED`、`SECURITY_HSTS`、`SECURITY_HSTS_ALWAYS`、`SECURITY_CSP`、`SECURITY_CSP_DOCS`、`SECURITY_FRAME_OPTIONS`、`SECURITY_REFERRER_POLICY`、`REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE` |
| 账号 / 会话 / 登录保护 | `DEFAULT_ADMIN_USERNAME`、`DEFAULT_LEGACY_PASSWORD`、`USER_SESSION_TTL_SECONDS`、`USER_PASSWORD_MIN_LENGTH`、`USER_PASSWORD_ITERATIONS`、`LOGIN_LOCKOUT_THRESHOLD`、`LOGIN_LOCKOUT_DURATION`、`GOVBUDGET_ADMIN_API_KEYS`、`ALLOW_ORIGINS` |
| 队列 | `JOB_QUEUE_ENABLED`、`JOB_QUEUE_RESUME_ON_START`、`JOB_QUEUE_POLL_INTERVAL_SECONDS`、`JOB_QUEUE_CLAIM_TTL_SECONDS`、`JOB_QUEUE_HEARTBEAT_MAX_AGE_SECONDS` |
| 就绪检查与运维 | `READY_REQUIRE_DATABASE`、`READY_REQUIRE_AI_EXTRACTOR`、`READY_EXPOSE_DETAILS`、`PG_BIN_DIR` |
| 本地状态目录 | `ORG_DATA_DIR`、`USER_DATA_DIR`、`USER_FILE` |
| 缓存与数据库超时 | `DEPARTMENT_STATS_CACHE_TTL_SECONDS`、`DEPARTMENT_STATS_CACHE_MAX_SIZE`、`WORKFLOW_DB_TIMEOUT_SECONDS` |
| AI 抽取与语义审计 | `AI_EXTRACTOR_TIMEOUT`、`AI_EXTRACTOR_MAX_RETRIES`、`AI_EXTRACTOR_RETRY_DELAY`、`AI_EXTRACTOR_MODEL`、`AI_AUDIT_WINDOW_CHARS`、`AI_AUDIT_WINDOW_OVERLAP`、`AI_AUDIT_MAX_WINDOWS`、`AI_AUDIT_MAX_CONCURRENCY`、`AI_MIN_ISSUE_CONFIDENCE`、`AI_{MAIN,BACKUP,LOCATOR}_{ENABLED,TIMEOUT_S,RETRIES}` |

### 3.4 刻意不进 `.env.example` 的变量（已在脚本里登记理由）

| 变量 | 理由 |
|---|---|
| `TESTING`、`GOVBUDGET_TEST_DATABASE_URL` | 测试环境标记 / 连库测试专用 |
| `PDF_PARSE_TEST_ALLOCATE_MB`、`PDF_PARSE_TEST_DELAY_SECONDS`、`RULES_PROCESS_TEST_DELAY_SECONDS` | 进程隔离测试钩子 |
| `PDF_PARSE_PROCESS_START_METHOD`、`RULES_PROCESS_START_METHOD` | 跨平台测试用的启动方式覆盖 |
| `DIAG_BACKEND_URL`、`DIAG_FRONTEND_URL`、`BACKEND_API_KEY` | 本地诊断脚本用 |
| `CODESPACE_NAME`、`GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN` | Codespaces 平台注入 |
| `ALLOW_INSECURE_DEFAULT_ADMIN` | 仅测试放行弱默认口令，生产不应出现 |
| `POSTGRES_*` | postgres 官方镜像读取 |
| `HOST_*`、`COMPOSE_FILE` | compose 自身用 |
| `BACKEND_URL` | Next.js 前端（`app/` 下 TS 代码）读取，不在 Python 侧 |

## 四、值层面的差异（不改，但要知道）

| 变量 | `.env.example` | compose 缺省 | 说明 |
|---|---|---|---|
| `AI_MAIN_PROVIDER` | `main` | `gemini_main` | `.env.example` 推荐用逻辑 slot 名 `main`；compose 的缺省值保留历史命名，避免既有部署在未设置该变量时行为突变。实际部署都会在 `.env` 里显式设置，缺省值极少生效 |
| `AI_LOCATOR_PROVIDER` | `locator` | `gemini_locator` | 同上 |
| `AUDIT_LOG_PATH` | `/opt/GovBudgetChecker-data/logs/admin-actions.jsonl` | `/app/logs/admin-actions.jsonl` | 容器内路径必须是 `/app/logs`，宿主侧由 `HOST_LOGS_DIR` 决定，二者不冲突 |

## 五、防回归

- `scripts/check_env_consistency.py` 违规即非零退出；
- `make lint` 与 CI 的 `Env var consistency` 步骤都会跑；
- `tests/test_compose_deployment.py::test_env_vars_are_consistent_across_code_example_and_compose`
  把它固化成单元测试，另有解析器自身的正反对照用例。
