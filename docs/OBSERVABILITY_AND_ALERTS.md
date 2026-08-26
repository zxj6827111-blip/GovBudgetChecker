# 可观测性与告警阈值

- 编制日期：2026-08-26
- 关联缺口：`P2-01` / `B-05`（结构化日志）、`B-06`（关键指标与告警钩子）
- 关联实现：`src/utils/logging_config.py`、`src/services/metrics.py`、`api/routes/metrics.py`
- 与 `docs/PRODUCTION_HARDENING.md` 第 4 节「监控告警建议」配套：那节给方向，本文给可直接配置的阈值与处置动作。

## 一、结构化日志

### 开关与格式

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LOG_JSON` | 自动 | 未配置时按 stdout 是否为 TTY 判断：容器/管道 → JSON，本地终端 → 人类可读 |
| `LOG_FILE` | 空 | 可选，额外写入的文件路径（父目录自动创建） |

装配入口只有两处，不要在别处再调 `setup_logging`：

- API：`api/main.py` 的 `_app_lifespan` → `configure_logging_from_env("api")`
- Worker：`api/worker.py` 的 `main()` → `configure_logging_from_env("worker")`

`TESTING=true` 时默认跳过装配（`setup_logging` 会清空 root handler，会打断 pytest 采集）。

### job_id 关联

所有任务相关日志都能按 `job_id` 检索：

- `api/main.py:_run_pipeline` / `_run_pipeline_inner` 用 `log_context(job_id=...)` 绑定，
  绑定基于 `contextvars`，队列并发多任务时不会互相串字段；
- `api/main.py:_safe_write` 是状态流转的唯一出口，在这里统一 `log_job_stage`，
  覆盖 `开始解析文档 / 解析PDF内容 / 构建文档对象 / 双模式分析 / 执行规则检查 / 结构化入库 / 完成（…）` 等全部阶段；
- `api/job_queue.py:_worker_loop` 在执行前绑定 `job_id` 与 `worker_index`。

常用检索字段：`job_id`、`stage`、`job_status`、`progress`、`page_coverage`、
`scanned_page_count`、`quality_status`、`analysis_conclusion`、`review_reason_codes`。

### 敏感内容红线

`redact_log_fields` 在写入侧强制替换，`StructuredFormatter` 再做一次兜底：
命中敏感键时丢弃原值，只保留 `<key>_len` 与 `<key>_sha256`（前 12 位）。

覆盖的键包括凭据类（`api_key` / `authorization` / `password` / `token` / `cookie` / `secret`…）
与材料原文类（`evidence_text` / `text_snippet` / `page_text(s)` / `pdf_text` / `raw_text` / `prompt` / `content` / `snippet`），
以及以 `_password` / `_secret` / `_token` / `_api_key` / `_text` / `_prompt` 结尾的任意键。

> 注意：在 `log_context` 内使用 `logger.xxx(extra=...)` 时必须用 `safe_log_extra(...)` 构造，
> 否则与上下文同名的键会让 `logging.makeRecord` 抛 `KeyError`。

## 二、指标端点

### 为什么用端点而不是只做日志聚合

结构化日志解决"事件可检索"，但"当前积压多少、`review_required` 占比多少"这类**即时态势**
需要外部日志管道先落地并写查询，本轮无法在仓库内自证。端点方案可被测试直接断言、
可被 Prometheus 抓取，与日志是叠加关系：**明细看日志，态势看端点**。

### 访问方式与鉴权

`GET /metrics`、`GET /api/metrics`，支持 `?format=prometheus` 输出文本暴露格式。

鉴权是三道叠加，缺一不可：

1. `SecurityMiddleware` 的 API Key（该路径**不在**免认证白名单里）；
2. 管理员会话（`X-Session-Token` 对应管理员），沿用 `/ready?details=true` 的先例；
3. 或独立抓取令牌 `METRICS_API_TOKEN`，采集器用 `X-Metrics-Token` 提交
   （给采集器单独发令牌，避免把管理员口令写进 Prometheus 配置）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `METRICS_ENABLED` | `true` | 设为 `false` 时端点返回 404（只走日志聚合的部署） |
| `METRICS_API_TOKEN` | 空 | 采集器专用令牌；不配置则只能用管理员会话 |
| `METRICS_CACHE_TTL_SECONDS` | `15` | 采集结果缓存秒数，防止高频抓取反复全量扫盘 |
| `METRICS_MAX_JOBS` | `5000` | 单次扫描的任务目录上限 |

### 数据来源

只读 `UPLOAD_DIR/<job_id>/status.json` 与 `UPLOAD_DIR/.worker-heartbeats/*.json`，
不新增存储、不写任何文件。任务产物本来就是本系统的事实源，从它算指标不会出现
"指标说 A、产物说 B"的两套事实。

## 三、告警阈值与处置动作

阈值依据一栏说明"为什么是这个数"，避免拍脑袋数字。所有比例类指标的分母都是
**被扫描到的任务产物总数**，因此上线初期样本少时波动大，建议同时要求
`govbudget_jobs_total >= 20` 才触发比例类告警。

| 指标（Prometheus 名） | 级别 | 阈值 | 阈值依据 | 触发后处置 |
|---|---|---|---|---|
| `govbudget_queue_live_workers` | P1 | `== 0` 持续 2 分钟 | 心跳窗口默认 15s（`JOB_QUEUE_HEARTBEAT_MAX_AGE_SECONDS`），2 分钟已是 8 个窗口，排除单次抖动 | 检查 worker 进程存活与 `UPLOAD_DIR` 挂载；`/ready` 的 `job_queue_started` 会同步转红 |
| `govbudget_queue_stale_heartbeats` | P2 | `> 0` 持续 10 分钟 | 心跳文件不会自清理，残留说明有 worker 异常退出 | 确认是缩容残留还是崩溃；崩溃需查 `pipeline_error` 日志 |
| `govbudget_queue_backlog` | P2 | `> 2 × JOB_QUEUE_WORKERS` 持续 10 分钟 | 积压超过并发度两倍说明消费跟不上生产 | 扩 worker 或降 `AI_SEQUENTIAL_MODE` 并发压力 |
| `govbudget_queue_oldest_queued_age_seconds` | P1 | `> PIPELINE_TIMEOUT_SEC`（默认 600） | 排队时间已超过单任务超时上限，说明任务实际没被消费 | 同 `live_workers == 0` 处置；必要时重启 worker 触发 resume |
| `govbudget_stage_duration_p95_ms_total` | P2 | `> 0.8 × PIPELINE_TIMEOUT_SEC × 1000` | p95 逼近超时上限意味着下一步就会大面积超时 | 查 PDF 体量分布与 AI 服务延迟；必要时上调超时或拆分材料 |
| `govbudget_stage_duration_p95_ms_ai` | P3 | `> 60000` | AI 阶段超过 1 分钟已显著拖慢整体交付 | 查 `AI_EXTRACTOR_URL` 延迟与限流 |
| `govbudget_ai_failure_rate` | P2 | `> 0.1` 持续 15 分钟 | 10% 以上失败意味着 AI 覆盖面已不完整，双模式退化为单规则 | 查 AI 服务可达性；若 AI 属必需能力应配 `AI_ASSIST_REQUIRED=true`，让门禁把任务转复核而不是静默降级 |
| `govbudget_unknown_report_kind_ratio` | P2 | `> 0.05` | unknown 只跑通用规则、专项规则未覆盖，是漏检来源；5% 是可人工兜住的量级 | 补充材料类型识别关键词；这些任务门禁已转 `review_required`，需人工复核 |
| `govbudget_review_required_ratio` | P2 | `> 0.3` | 超过三成需人工复核时，自动化已基本失去价值 | 按 `review_reason_codes` 分组定位主因（扫描页 / 覆盖率 / 年度 / 证据不足） |
| `govbudget_unresolved_report_year_ratio` | P3 | `> 0.05` | 年度识别失败会让同比与口径判断失去基准 | 检查文件命名与封面年度；**不得**用兜底年份填补（M1 已明确禁止 2000 兜底） |
| `govbudget_error_job_ratio` | P1 | `> 0.05` | 5% 以上硬失败属于系统性问题 | 按 `stage=pipeline_error` 的 `error_type` 聚类定位 |
| `govbudget_report_id_collision_count` | P1 | `> 0` | 冲突意味着不同原件被并进同一份报告身份（缺口 P0-09 的回归信号），会直接污染结构化数据 | 用 `collisions[].job_ids` 定位；核对组织匹配模式与年度识别结果后重跑结构化入库 |
| `govbudget_evidence_degraded_findings` | P3 | 单日增量 `> 20` | 大量 AI 问题拿不出证据，说明提示词或抽取链路退化 | 查 `stage=evidence_checked` 日志与 `prompt_version` 留痕 |

### 与 `/ready` 的分工

- `/ready`：**能不能接流量**（依赖可达性、目录可写、队列有活 worker）。
- `/metrics`：**跑得好不好**（积压、耗时、失败率、结论质量）。

两者都要接监控：`/ready` 做存活探针与流量摘除，`/metrics` 做趋势与质量告警。

## 四、本轮局限（如实说明）

- 比例类指标的分母是"扫描到的历史任务总数"，**不是时间窗口内的任务数**。
  因此它反映的是累计态势，不是瞬时速率；需要瞬时速率时应由采集端做 `rate()` 差分，
  或后续把指标口径改为按时间窗过滤（本轮未做）。
- 无 Golden Corpus，所有质量指标都是**结构性指标**，不含召回率/精确率。
  这些阈值能证明"没有静默失败、没有虚假成功"，不能证明业务召回率达标。
- 告警规则本身未在真实 Prometheus/Alertmanager 环境验证，本轮只验证了
  指标采集函数与端点行为（`tests/test_metrics.py`）。
